from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from job_hunter.models import Evaluation, Job, Material
from job_hunter.normalize import description_hash, job_fingerprint

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """SQLite-backed persistence layer for the job hunter bot."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    # ------------------------------------------------------------------
    # Schema setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute(_CREATE_JOBS)
            self._conn.execute(_CREATE_EVALUATIONS)
            self._conn.execute(_CREATE_MATERIALS)
            self._conn.execute(_CREATE_DELIVERIES)

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

        with self._conn:
            # Enable FK each transaction (SQLite resets per-connection)
            self._conn.execute("PRAGMA foreign_keys = ON")

            # Try to insert; ignore if fingerprint already exists
            self._conn.execute(
                """
                INSERT OR IGNORE INTO jobs
                    (fingerprint, source, source_job_id, url, company, title,
                     location, remote, description, description_hash,
                     first_seen_at, last_seen_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
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
                        now,
                        job_id,
                    ),
                )

        return job_id, is_new, description_changed

    def count_jobs(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        return row[0]

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
