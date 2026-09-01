from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from job_hunter.gmail_models import ExtractedJob
from job_hunter.job_identity import (
    locations_compatible,
    normalize_company_name,
    normalize_job_title,
)
from job_hunter.models import Evaluation, Job, Material
from job_hunter.normalize import (
    canonicalize_url,
    description_hash,
    job_fingerprint,
    normalize_text,
)

_CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint      TEXT    NOT NULL UNIQUE,
    source           TEXT    NOT NULL DEFAULT '',
    source_job_id    TEXT,
    url              TEXT    NOT NULL DEFAULT '',
    company          TEXT    NOT NULL DEFAULT '',
    title            TEXT    NOT NULL DEFAULT '',
    location         TEXT    NOT NULL DEFAULT '',
    remote           INTEGER,          -- NULL | 0 | 1
    description      TEXT    NOT NULL DEFAULT '',
    description_hash TEXT    NOT NULL DEFAULT '',
    first_seen_at    TEXT    NOT NULL,
    last_seen_at     TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'new'
)
"""

_R2_JOB_COLUMNS = {
    "canonical_url": "TEXT NOT NULL DEFAULT ''",
    "ats_provider": "TEXT",
    "ats_board": "TEXT",
    "ats_job_id": "TEXT",
}

_CREATE_JOB_SOURCES = """
CREATE TABLE IF NOT EXISTS job_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_job_id TEXT,
    source_url TEXT NOT NULL DEFAULT '',
    identity_key TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(job_id, identity_key)
)
"""

_CREATE_COMPANY_WATCH = """
CREATE TABLE IF NOT EXISTS company_watch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    normalized_company_name TEXT NOT NULL UNIQUE,
    careers_url TEXT NOT NULL DEFAULT '',
    ats_provider TEXT,
    ats_identifier TEXT,
    discovered_from_job_id INTEGER REFERENCES jobs(id),
    promotion_source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    paused_until TEXT,
    first_seen_at TEXT NOT NULL,
    last_verified_at TEXT,
    last_successful_check_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_EVALUATIONS = """
CREATE TABLE IF NOT EXISTS evaluations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                 INTEGER NOT NULL REFERENCES jobs(id),
    total_score            INTEGER NOT NULL DEFAULT 0,
    scores_json            TEXT    NOT NULL DEFAULT '{}',
    decision               TEXT    NOT NULL DEFAULT '',
    hard_blockers_json     TEXT    NOT NULL DEFAULT '[]',
    strengths_json         TEXT    NOT NULL DEFAULT '[]',
    gaps_json              TEXT    NOT NULL DEFAULT '[]',
    salary_note            TEXT    NOT NULL DEFAULT '',
    location_note          TEXT    NOT NULL DEFAULT '',
    rationale              TEXT    NOT NULL DEFAULT '',
    model                  TEXT    NOT NULL DEFAULT '',
    status                 TEXT    NOT NULL DEFAULT 'ok',
    description_hash_at_eval TEXT NOT NULL DEFAULT '',
    evaluated_at           TEXT    NOT NULL
)
"""

_CREATE_MATERIALS = """
CREATE TABLE IF NOT EXISTS materials (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id            INTEGER NOT NULL REFERENCES jobs(id),
    cover_letter_text TEXT    NOT NULL DEFAULT '',
    generated_at      TEXT    NOT NULL
)
"""

_CREATE_DELIVERIES = """
CREATE TABLE IF NOT EXISTS deliveries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              INTEGER NOT NULL REFERENCES jobs(id),
    delivery_type       TEXT    NOT NULL DEFAULT '',
    status              TEXT    NOT NULL DEFAULT 'sent',
    delivered_at        TEXT    NOT NULL,
    telegram_message_id TEXT
)
"""

_CREATE_GMAIL_SYNC_STATE = """
CREATE TABLE IF NOT EXISTS gmail_sync_state (
    account_id TEXT PRIMARY KEY,
    history_id TEXT,
    last_successful_sync_at TEXT,
    last_processed_message_at TEXT,
    backfill_completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_GMAIL_MESSAGES = """
CREATE TABLE IF NOT EXISTS gmail_messages (
    message_id TEXT PRIMARY KEY,
    thread_id TEXT,
    sender TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    classification TEXT NOT NULL,
    confidence REAL NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    processed_at TEXT NOT NULL
)
"""

_CREATE_INBOUND_JOB_CANDIDATES = """
CREATE TABLE IF NOT EXISTS inbound_job_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL DEFAULT 'gmail',
    source_message_id TEXT NOT NULL,
    source_candidate_key TEXT NOT NULL,
    source_platform TEXT NOT NULL DEFAULT '',
    source_job_id TEXT,
    url TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    remote INTEGER,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(origin, source_message_id, source_candidate_key)
)
"""

_CREATE_APPLICATION_EVENTS = """
CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES jobs(id),
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'gmail',
    source_message_id TEXT NOT NULL UNIQUE,
    source_thread_id TEXT,
    confidence REAL NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    role_title TEXT NOT NULL DEFAULT '',
    rationale TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""

_CREATE_REVIEW_DELIVERIES = """
CREATE TABLE IF NOT EXISTS review_deliveries (
    event_id INTEGER PRIMARY KEY REFERENCES application_events(id),
    delivered_at TEXT NOT NULL,
    telegram_message_id TEXT
)
"""

_DELIVERABLE_SCORE_FLOOR = 60
_SUPPORTED_ATS_PROVIDERS = frozenset({"ashby", "greenhouse", "lever"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """SQLite-backed persistence layer for the job hunter bot."""

    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        self._path = str(path)
        if read_only:
            database_uri = Path(path).resolve().as_uri() + "?mode=ro"
            self._conn = sqlite3.connect(
                database_uri,
                uri=True,
                check_same_thread=False,
            )
        else:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if read_only:
            self._conn.execute("PRAGMA foreign_keys = ON")
        else:
            self._init_db()

    # ------------------------------------------------------------------
    # Schema setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute(_CREATE_JOBS)
            self._migrate_jobs_to_r2_schema()
            self._conn.execute(_CREATE_JOB_SOURCES)
            self._conn.execute(_CREATE_COMPANY_WATCH)
            self._conn.execute(_CREATE_EVALUATIONS)
            self._conn.execute(_CREATE_MATERIALS)
            self._conn.execute(_CREATE_DELIVERIES)
            self._conn.execute(_CREATE_GMAIL_SYNC_STATE)
            self._conn.execute(_CREATE_GMAIL_MESSAGES)
            self._conn.execute(_CREATE_INBOUND_JOB_CANDIDATES)
            self._conn.execute(_CREATE_APPLICATION_EVENTS)
            self._conn.execute(_CREATE_REVIEW_DELIVERIES)

    def _migrate_jobs_to_r2_schema(self) -> None:
        columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(jobs)")
        }
        for name, definition in _R2_JOB_COLUMNS.items():
            if name not in columns:
                self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")

    # ------------------------------------------------------------------
    # Job operations
    # ------------------------------------------------------------------

    def upsert_job(self, job: Job) -> tuple[int, bool, bool]:
        """
        Insert or update a job record.

        Returns
        -------
        (job_id, is_new, description_changed)
        """
        fingerprint = job_fingerprint(job)
        desc_hash = description_hash(job.description or "")
        now = _now_iso()
        remote_int: int | None = None if job.remote is None else int(job.remote)
        canonical_url = job.canonical_url or canonicalize_url(job.url or "")

        with self._conn:
            # Enable FK each transaction (SQLite resets per-connection)
            self._conn.execute("PRAGMA foreign_keys = ON")

            # Try to insert; ignore if fingerprint already exists
            self._conn.execute(
                """
                INSERT OR IGNORE INTO jobs
                    (fingerprint, source, source_job_id, url, company, title,
                     location, remote, description, description_hash,
                     canonical_url, ats_provider, ats_board, ats_job_id,
                     first_seen_at, last_seen_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
                """,
                (
                    fingerprint,
                    job.source or "",
                    job.source_job_id,
                    job.url or "",
                    job.company or "",
                    job.title or "",
                    job.location or "",
                    remote_int,
                    job.description or "",
                    desc_hash,
                    canonical_url,
                    job.ats_provider,
                    job.ats_board,
                    job.ats_job_id,
                    now,
                    now,
                ),
            )

            # Fetch current record (whether just inserted or pre-existing)
            row = self._conn.execute(
                "SELECT id, description_hash, first_seen_at FROM jobs WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()

            job_id: int = row["id"]
            old_hash: str = row["description_hash"]
            is_new: bool = row["first_seen_at"] == now
            description_changed: bool = False

            if not is_new:
                description_changed = old_hash != desc_hash
                # Update mutable fields
                self._conn.execute(
                    """
                    UPDATE jobs SET
                        url              = ?,
                        company          = ?,
                        title            = ?,
                        location         = ?,
                        remote           = ?,
                        description      = ?,
                        description_hash = ?,
                        canonical_url    = COALESCE(NULLIF(?, ''), canonical_url),
                        ats_provider     = COALESCE(?, ats_provider),
                        ats_board        = COALESCE(?, ats_board),
                        ats_job_id       = COALESCE(?, ats_job_id),
                        last_seen_at     = ?
                    WHERE id = ?
                    """,
                    (
                        job.url or "",
                        job.company or "",
                        job.title or "",
                        job.location or "",
                        remote_int,
                        job.description or "",
                        desc_hash,
                        canonical_url,
                        job.ats_provider,
                        job.ats_board,
                        job.ats_job_id,
                        now,
                        job_id,
                    ),
                )

        return job_id, is_new, description_changed

    def upsert_logical_job(self, job: Job) -> tuple[int, bool, bool]:
        """Persist a source-independent logical job and its discovery provenance.

        Identity is resolved from strongest to weakest exact evidence. Existing
        rows retain richer fields, while a resolved canonical URL becomes the
        usable job URL. The return shape matches :meth:`upsert_job`.
        """
        canonical_url = canonicalize_url(job.canonical_url) if job.canonical_url else ""
        ats_provider = (job.ats_provider or "").lower()
        fingerprint = job_fingerprint(job)

        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            candidate_ids: list[int] = []

            if canonical_url:
                self._append_unique_id(
                    candidate_ids, self.find_job_by_canonical_url(canonical_url)
                )
            if (
                ats_provider in _SUPPORTED_ATS_PROVIDERS
                and job.ats_board
                and job.ats_job_id
            ):
                self._append_unique_id(
                    candidate_ids,
                    self.find_job_by_ats(ats_provider, job.ats_board, job.ats_job_id),
                )
            self._append_unique_id(
                candidate_ids,
                self.find_job_by_identity(job.company, job.title, job.location),
            )
            self._append_unique_id(
                candidate_ids,
                self._find_single_job_id(
                    "SELECT id FROM jobs WHERE fingerprint = ?", (fingerprint,)
                ),
            )

            if candidate_ids:
                job_id = candidate_ids[0]
                for duplicate_id in candidate_ids[1:]:
                    self._merge_jobs(job_id, duplicate_id)
                description_changed = self._update_logical_job(job_id, job)
                is_new = False
            else:
                job_id = self._insert_logical_job(job, fingerprint)
                description_changed = False
                is_new = True

            self._record_job_source(
                job_id,
                source=job.source or "",
                source_job_id=job.source_job_id,
                source_url=job.original_url or job.url or "",
            )

        return job_id, is_new, description_changed

    @staticmethod
    def _append_unique_id(ids: list[int], job_id: int | None) -> None:
        if job_id is not None and job_id not in ids:
            ids.append(job_id)

    def _insert_logical_job(self, job: Job, fingerprint: str) -> int:
        now = _now_iso()
        persisted_url = job.canonical_url or job.url or ""
        canonical_url = canonicalize_url(job.canonical_url or job.url or "")
        remote = None if job.remote is None else int(job.remote)
        cursor = self._conn.execute(
            """
            INSERT INTO jobs
                (fingerprint, source, source_job_id, url, company, title,
                 location, remote, description, description_hash,
                 canonical_url, ats_provider, ats_board, ats_job_id,
                 first_seen_at, last_seen_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
            """,
            (
                fingerprint,
                job.source or "",
                job.source_job_id,
                persisted_url,
                job.company or "",
                job.title or "",
                job.location or "",
                remote,
                job.description or "",
                description_hash(job.description or ""),
                canonical_url,
                job.ats_provider,
                job.ats_board,
                job.ats_job_id,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def _update_logical_job(self, job_id: int, job: Job) -> bool:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"job does not exist: {job_id}")

        description = self._richer_text(row["description"], job.description)
        description_changed = description_hash(description) != row["description_hash"]
        canonical_url = canonicalize_url(job.canonical_url) if job.canonical_url else ""
        persisted_url = job.canonical_url or row["url"] or job.url or ""

        remote = row["remote"]
        if remote is None and job.remote is not None:
            remote = int(job.remote)

        self._conn.execute(
            """
            UPDATE jobs SET
                url = ?,
                company = ?,
                title = ?,
                location = ?,
                remote = ?,
                description = ?,
                description_hash = ?,
                canonical_url = ?,
                ats_provider = ?,
                ats_board = ?,
                ats_job_id = ?,
                last_seen_at = ?
            WHERE id = ?
            """,
            (
                persisted_url,
                row["company"] or job.company or "",
                row["title"] or job.title or "",
                row["location"] or job.location or "",
                remote,
                description,
                description_hash(description),
                canonical_url or row["canonical_url"],
                job.ats_provider or row["ats_provider"],
                job.ats_board or row["ats_board"],
                job.ats_job_id or row["ats_job_id"],
                _now_iso(),
                job_id,
            ),
        )
        return description_changed

    def merge_jobs(self, survivor_id: int, duplicate_id: int) -> int:
        """Transactionally merge a duplicate job and all attached records."""
        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            return self._merge_jobs(survivor_id, duplicate_id)

    def _merge_jobs(self, survivor_id: int, duplicate_id: int) -> int:
        if survivor_id == duplicate_id:
            return survivor_id

        survivor = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (survivor_id,)
        ).fetchone()
        duplicate = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (duplicate_id,)
        ).fetchone()
        if survivor is None or duplicate is None:
            raise ValueError("survivor and duplicate jobs must both exist")

        survivor_has_ats = self._has_complete_ats_identity(survivor)
        duplicate_has_ats = self._has_complete_ats_identity(duplicate)
        prefer_duplicate_identity = duplicate_has_ats and not survivor_has_ats
        canonical_url = (
            duplicate["canonical_url"]
            if prefer_duplicate_identity
            else survivor["canonical_url"] or duplicate["canonical_url"]
        )
        url = (
            duplicate["url"]
            if prefer_duplicate_identity
            else survivor["url"] or duplicate["url"]
        )
        if prefer_duplicate_identity and canonical_url:
            url = canonical_url
        description = self._richer_text(
            survivor["description"], duplicate["description"]
        )

        self._conn.execute(
            """
            UPDATE jobs SET
                source = ?,
                source_job_id = ?,
                url = ?,
                company = ?,
                title = ?,
                location = ?,
                remote = ?,
                description = ?,
                description_hash = ?,
                canonical_url = ?,
                ats_provider = ?,
                ats_board = ?,
                ats_job_id = ?,
                first_seen_at = ?,
                last_seen_at = ?
            WHERE id = ?
            """,
            (
                survivor["source"] or duplicate["source"],
                survivor["source_job_id"] or duplicate["source_job_id"],
                url,
                survivor["company"] or duplicate["company"],
                survivor["title"] or duplicate["title"],
                survivor["location"] or duplicate["location"],
                survivor["remote"]
                if survivor["remote"] is not None
                else duplicate["remote"],
                description,
                description_hash(description),
                canonical_url,
                duplicate["ats_provider"]
                if prefer_duplicate_identity
                else survivor["ats_provider"] or duplicate["ats_provider"],
                duplicate["ats_board"]
                if prefer_duplicate_identity
                else survivor["ats_board"] or duplicate["ats_board"],
                duplicate["ats_job_id"]
                if prefer_duplicate_identity
                else survivor["ats_job_id"] or duplicate["ats_job_id"],
                min(survivor["first_seen_at"], duplicate["first_seen_at"]),
                max(survivor["last_seen_at"], duplicate["last_seen_at"]),
                survivor_id,
            ),
        )

        for source in self._conn.execute(
            "SELECT * FROM job_sources WHERE job_id = ? ORDER BY id", (duplicate_id,)
        ).fetchall():
            self._conn.execute(
                """
                INSERT INTO job_sources
                    (job_id, source, source_job_id, source_url, identity_key,
                     first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, identity_key) DO UPDATE SET
                    first_seen_at = MIN(first_seen_at, excluded.first_seen_at),
                    last_seen_at = MAX(last_seen_at, excluded.last_seen_at)
                """,
                (
                    survivor_id,
                    source["source"],
                    source["source_job_id"],
                    source["source_url"],
                    source["identity_key"],
                    source["first_seen_at"],
                    source["last_seen_at"],
                ),
            )
        self._conn.execute("DELETE FROM job_sources WHERE job_id = ?", (duplicate_id,))

        for table in ("application_events", "materials", "deliveries", "evaluations"):
            self._conn.execute(
                f"UPDATE {table} SET job_id = ? WHERE job_id = ?",
                (survivor_id, duplicate_id),
            )
        self._conn.execute(
            """
            UPDATE company_watch SET discovered_from_job_id = ?
            WHERE discovered_from_job_id = ?
            """,
            (survivor_id, duplicate_id),
        )
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (duplicate_id,))
        return survivor_id

    @staticmethod
    def _has_complete_ats_identity(row: sqlite3.Row) -> bool:
        return bool(row["ats_provider"] and row["ats_board"] and row["ats_job_id"])

    @staticmethod
    def _richer_text(current: str, candidate: str) -> str:
        if candidate and len(candidate.strip()) > len((current or "").strip()):
            return candidate
        return current or candidate or ""

    def record_job_source(
        self,
        job_id: int,
        *,
        source: str,
        source_job_id: str | None,
        source_url: str,
    ) -> None:
        """Record a discovery source once while refreshing its last-seen time."""
        with self._conn:
            self._record_job_source(
                job_id,
                source=source,
                source_job_id=source_job_id,
                source_url=source_url,
            )

    def _record_job_source(
        self,
        job_id: int,
        *,
        source: str,
        source_job_id: str | None,
        source_url: str,
    ) -> None:
        canonical_source_url = canonicalize_url(source_url)
        identity_key = (
            f"id:{source}:{source_job_id}"
            if source_job_id
            else f"url:{canonical_source_url}"
        )
        now = _now_iso()
        self._conn.execute(
            """
            INSERT INTO job_sources
                (job_id, source, source_job_id, source_url, identity_key,
                 first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, identity_key) DO UPDATE SET
                last_seen_at = excluded.last_seen_at
            """,
            (
                job_id,
                source,
                source_job_id,
                source_url,
                identity_key,
                now,
                now,
            ),
        )

    def list_job_sources(self, job_id: int) -> list[sqlite3.Row]:
        """Return source provenance for a job in insertion order."""
        return self._conn.execute(
            "SELECT * FROM job_sources WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()

    def find_job_by_canonical_url(self, url: str) -> int | None:
        """Return a job ID only when a canonical URL identifies one job."""
        return self._find_single_job_id(
            "SELECT id FROM jobs WHERE canonical_url = ?", (canonicalize_url(url),)
        )

    def find_job_by_ats(
        self, provider: str, board: str, job_id: str | None
    ) -> int | None:
        """Return a job ID only when an ATS tuple identifies one job."""
        if not job_id:
            return None
        return self._find_single_job_id(
            """
            SELECT id FROM jobs
            WHERE ats_provider = ? AND ats_board = ? AND ats_job_id = ?
            """,
            (provider, board, job_id),
        )

    def find_job_by_identity(
        self, company: str, title: str, location: str
    ) -> int | None:
        """Return a job ID only for one normalized company/title/location match."""
        company_key = normalize_company_name(company)
        title_key = normalize_job_title(title)
        if not company_key or not title_key:
            return None
        matching_ids = [
            row["id"]
            for row in self._conn.execute("SELECT id, company, title, location FROM jobs")
            if normalize_company_name(row["company"]) == company_key
            and normalize_job_title(row["title"]) == title_key
            and locations_compatible(location, row["location"])
        ]
        return matching_ids[0] if len(matching_ids) == 1 else None

    def _find_single_job_id(
        self, query: str, parameters: tuple[str, ...]
    ) -> int | None:
        rows = self._conn.execute(query, parameters).fetchall()
        return rows[0]["id"] if len(rows) == 1 else None

    def count_jobs(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        return row[0]

    def list_jobs_for_matching(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT id, source_job_id, url, company, title, first_seen_at, last_seen_at
            FROM jobs
            ORDER BY id
            """
        ).fetchall()

    # ------------------------------------------------------------------
    # Gmail sync and staging operations
    # ------------------------------------------------------------------

    def has_processed_gmail_message(self, message_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM gmail_messages WHERE message_id = ? LIMIT 1",
            (message_id,),
        ).fetchone()
        return row is not None

    def record_gmail_message(
        self,
        *,
        message_id: str,
        thread_id: str | None,
        sender: str,
        subject: str,
        occurred_at: str,
        classification: str,
        confidence: float,
        rationale: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO gmail_messages
                    (message_id, thread_id, sender, subject, occurred_at,
                     classification, confidence, rationale, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    thread_id,
                    sender,
                    subject,
                    occurred_at,
                    classification,
                    confidence,
                    rationale,
                    _now_iso(),
                ),
            )

    def get_gmail_sync_state(self, account_id: str) -> sqlite3.Row | None:
        table = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gmail_sync_state'"
        ).fetchone()
        if table is None:
            return None
        return self._conn.execute(
            "SELECT * FROM gmail_sync_state WHERE account_id = ?",
            (account_id,),
        ).fetchone()

    def save_gmail_sync_state(
        self,
        account_id: str,
        history_id: str | None,
        last_successful_sync_at: str | None,
        backfill_completed_at: str | None,
    ) -> None:
        now = _now_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO gmail_sync_state
                    (account_id, history_id, last_successful_sync_at,
                     backfill_completed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    history_id = excluded.history_id,
                    last_successful_sync_at = excluded.last_successful_sync_at,
                    backfill_completed_at = excluded.backfill_completed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    account_id,
                    history_id,
                    last_successful_sync_at,
                    backfill_completed_at,
                    now,
                    now,
                ),
            )

    def stage_inbound_job(
        self,
        source_message_id: str,
        source_candidate_key: str,
        job: ExtractedJob,
    ) -> int:
        now = _now_iso()
        remote = None if job.remote is None else int(job.remote)
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO inbound_job_candidates
                    (origin, source_message_id, source_candidate_key,
                     source_platform, source_job_id, url, company, title,
                     location, remote, description, created_at, last_seen_at)
                VALUES ('gmail', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(origin, source_message_id, source_candidate_key) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    source_message_id,
                    source_candidate_key,
                    job.source_platform,
                    job.source_job_id,
                    job.url,
                    job.company,
                    job.title,
                    job.location,
                    remote,
                    "",
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                """
                SELECT id FROM inbound_job_candidates
                WHERE origin = 'gmail' AND source_message_id = ?
                  AND source_candidate_key = ?
                """,
                (source_message_id, source_candidate_key),
            ).fetchone()
        return row["id"]

    def list_unmaterialized_inbound_jobs(self) -> list[sqlite3.Row]:
        candidates = self._conn.execute(
            "SELECT * FROM inbound_job_candidates ORDER BY id"
        ).fetchall()
        jobs = self._conn.execute(
            """
            SELECT source, source_job_id, url, company, title, location
            FROM jobs
            """
        ).fetchall()

        return [
            candidate
            for candidate in candidates
            if not any(
                self._matches_materialized_job(candidate, job) for job in jobs
            )
        ]

    @staticmethod
    def _matches_materialized_job(
        candidate: sqlite3.Row, job: sqlite3.Row
    ) -> bool:
        if (
            job["source"] == f"gmail:{candidate['source_platform']}"
            and job["source_job_id"] == candidate["source_candidate_key"]
        ):
            return True

        if candidate["url"] and job["url"]:
            if canonicalize_url(candidate["url"]) == canonicalize_url(job["url"]):
                return True

        candidate_identity = "|".join(
            normalize_text(value)
            for value in (candidate["company"], candidate["title"], candidate["location"])
        )
        job_identity = "|".join(
            normalize_text(value)
            for value in (job["company"], job["title"], job["location"])
        )
        return candidate_identity != "||" and candidate_identity == job_identity

    def save_application_event(
        self,
        *,
        job_id: int | None,
        event_type: str,
        occurred_at: str,
        source_message_id: str,
        source_thread_id: str | None,
        confidence: float,
        company: str,
        role_title: str,
        rationale: str,
        source: str = "gmail",
    ) -> int:
        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute(
                """
                INSERT OR IGNORE INTO application_events
                    (job_id, event_type, occurred_at, source, source_message_id,
                     source_thread_id, confidence, company, role_title,
                     rationale, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    event_type,
                    occurred_at,
                    source,
                    source_message_id,
                    source_thread_id,
                    confidence,
                    company,
                    role_title,
                    rationale,
                    _now_iso(),
                ),
            )
            row = self._conn.execute(
                "SELECT id FROM application_events WHERE source_message_id = ?",
                (source_message_id,),
            ).fetchone()
        return row["id"]

    def list_application_events(self, job_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT * FROM application_events
            WHERE job_id = ?
            ORDER BY occurred_at, id
            """,
            (job_id,),
        ).fetchall()

    def current_application_state(self, job_id: int) -> str | None:
        from job_hunter.gmail_matching import derive_application_state

        return derive_application_state(self.list_application_events(job_id))

    def pending_review_events(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            """
            SELECT e.*, m.subject
            FROM application_events e
            JOIN gmail_messages m ON m.message_id = e.source_message_id
            LEFT JOIN review_deliveries d ON d.event_id = e.id
            WHERE e.event_type = 'REVIEW_NEEDED' AND d.event_id IS NULL
            ORDER BY e.occurred_at, e.id
            """
        ).fetchall()

    def mark_review_delivered(
        self, event_ids: list[int], telegram_message_id: str
    ) -> None:
        if not event_ids:
            return
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO review_deliveries
                    (event_id, delivered_at, telegram_message_id)
                VALUES (?, ?, ?)
                """,
                [(event_id, _now_iso(), telegram_message_id) for event_id in event_ids],
            )

    # ------------------------------------------------------------------
    # Evaluation operations
    # ------------------------------------------------------------------

    def needs_evaluation(self, job_id: int) -> bool:
        """
        Return True when the job should be evaluated (or re-evaluated).

        Conditions:
        - No evaluation row exists, OR
        - Most recent evaluation has status == 'failed', OR
        - The job description changed since the most recent evaluation.
        """
        row = self._conn.execute(
            """
            SELECT e.status, e.description_hash_at_eval, j.description_hash
            FROM   evaluations e
            JOIN   jobs j ON j.id = e.job_id
            WHERE  e.job_id = ?
            ORDER  BY e.id DESC
            LIMIT  1
            """,
            (job_id,),
        ).fetchone()

        if row is None:
            return True  # No evaluation yet

        if row["status"] == "failed":
            return True

        # Re-evaluate only if the content actually changed since the last evaluation.
        if row["description_hash_at_eval"] != row["description_hash"]:
            return True

        return False

    def save_evaluation(self, job_id: int, evaluation: Evaluation) -> None:
        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            row = self._conn.execute(
                "SELECT description_hash FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            description_hash = row["description_hash"] if row else ""
            self._conn.execute(
                """
                INSERT INTO evaluations
                    (job_id, total_score, scores_json, decision,
                     hard_blockers_json, strengths_json, gaps_json,
                     salary_note, location_note, rationale, model, status,
                     description_hash_at_eval, evaluated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    evaluation.total_score,
                    json.dumps(evaluation.scores),
                    evaluation.decision,
                    json.dumps(evaluation.hard_blockers),
                    json.dumps(evaluation.strengths),
                    json.dumps(evaluation.gaps),
                    evaluation.salary_note,
                    evaluation.location_note,
                    evaluation.rationale,
                    evaluation.model,
                    evaluation.status,
                    description_hash,
                    _now_iso(),
                ),
            )

    # ------------------------------------------------------------------
    # Material operations
    # ------------------------------------------------------------------

    def save_material(self, job_id: int, material: Material) -> None:
        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute(
                """
                INSERT INTO materials (job_id, cover_letter_text, generated_at)
                VALUES (?, ?, ?)
                """,
                (job_id, material.cover_letter_text, _now_iso()),
            )

    # ------------------------------------------------------------------
    # Delivery operations
    # ------------------------------------------------------------------

    def mark_delivered(
        self,
        job_id: int,
        delivery_type: str,
        telegram_id: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute(
                """
                INSERT INTO deliveries
                    (job_id, delivery_type, status, delivered_at, telegram_message_id)
                VALUES (?, ?, 'sent', ?, ?)
                """,
                (job_id, delivery_type, _now_iso(), telegram_id),
            )

    def has_delivery(self, job_id: int, delivery_type: str | None = None) -> bool:
        if delivery_type is None:
            row = self._conn.execute(
                "SELECT 1 FROM deliveries WHERE job_id = ? LIMIT 1",
                (job_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT 1 FROM deliveries WHERE job_id = ? AND delivery_type = ? LIMIT 1",
                (job_id, delivery_type),
            ).fetchone()
        return row is not None

    def pending_delivery_job_ids(self) -> list[int]:
        rows = self._conn.execute("""
            SELECT j.id, e.decision, e.total_score,
                   EXISTS (SELECT 1 FROM deliveries d WHERE d.job_id=j.id AND d.delivery_type='telegram_message') AS msg,
                   EXISTS (SELECT 1 FROM deliveries d WHERE d.job_id=j.id AND d.delivery_type='telegram_document') AS doc
            FROM jobs j JOIN evaluations e ON e.id=(SELECT MAX(id) FROM evaluations WHERE job_id=j.id)
        """).fetchall()
        return [
            r["id"]
            for r in rows
            if r["total_score"] > _DELIVERABLE_SCORE_FLOOR
            and (
                (r["decision"] == "possible_match" and not r["msg"])
                or (
                    r["decision"] in ("high_priority", "package_match")
                    and (not r["msg"] or not r["doc"])
                )
            )
        ]

    # ------------------------------------------------------------------
    # Retrieval for delivery-retry (avoid re-calling Gemini)
    # ------------------------------------------------------------------

    def get_job(self, job_id: int) -> Job | None:
        row = self._conn.execute(
            """
            SELECT source, title, company, location, url, description,
                   source_job_id, remote
            FROM jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return Job(
            source=row["source"],
            title=row["title"],
            company=row["company"],
            location=row["location"],
            url=row["url"],
            description=row["description"],
            source_job_id=row["source_job_id"],
            remote=None if row["remote"] is None else bool(row["remote"]),
        )

    def get_evaluation(self, job_id: int) -> Evaluation | None:
        row = self._conn.execute(
            """
            SELECT total_score, scores_json, decision, hard_blockers_json,
                   strengths_json, gaps_json, salary_note, location_note,
                   rationale, model, status
            FROM evaluations
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return Evaluation(
            job_id=job_id,
            total_score=row["total_score"],
            scores=json.loads(row["scores_json"]),
            decision=row["decision"],
            hard_blockers=json.loads(row["hard_blockers_json"]),
            strengths=json.loads(row["strengths_json"]),
            gaps=json.loads(row["gaps_json"]),
            salary_note=row["salary_note"],
            location_note=row["location_note"],
            rationale=row["rationale"],
            model=row["model"],
            status=row["status"],
        )

    def get_material(self, job_id: int) -> Material | None:
        row = self._conn.execute(
            """
            SELECT cover_letter_text FROM materials
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return Material(job_id=job_id, cover_letter_text=row["cover_letter_text"])

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *_) -> None:
        self.close()
