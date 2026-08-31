# Job Hunter Bot R1 — Gmail Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only Gmail intelligence to the standalone Job Hunter Bot so it can ingest LinkedIn/job-board alerts and recruiter opportunities, reconstruct application lifecycle events, and surface ambiguous events without disrupting the existing daily search.

**Architecture:** Implement Gmail as a separate `sync-gmail` stage sharing the existing SQLite database. Gmail messages are fetched with read-only OAuth, classified deterministically first and with Gemini only when needed, then persisted as application events or staged job candidates. The normal `run` command adds a DB-backed Gmail source so staged jobs pass through the existing enrichment, deduplication, profession gate, ranking, evaluation, cover-letter, and Telegram delivery path.

**Tech Stack:** Python 3.12, SQLite, `requests`, `beautifulsoup4`, Google OAuth (`google-auth`, `google-auth-oauthlib`), Gmail REST API v1, existing Gemini REST client, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-31-job-hunter-bot-r1-gmail-intelligence-design.md`

## Global Constraints

- Gmail access is read-only: no send, reply, archive, delete, label, mark read/unread, or other mailbox mutation.
- Do not automate logged-in LinkedIn browsing, store LinkedIn credentials/cookies, or submit applications.
- The first successful Gmail connection performs a 12-month backfill; normal runs are incremental afterward.
- Gmail message ID is the message-level idempotency key.
- Low-confidence or ambiguous lifecycle signals become `REVIEW_NEEDED`; they never mutate derived application state.
- Only high-confidence lifecycle events with a resolved `job_id` participate in derived application state.
- Derived state is timestamp-first; same-timestamp precedence is `OFFER > REJECTED > TECHNICAL > INTERVIEW > RECRUITER_CONTACT > APPLIED`.
- Full email bodies are processed in memory and are not persisted by default.
- OAuth credentials are GitHub Secrets only and must never enter SQLite or repository files.
- `sync-gmail --dry-run` may read/classify/extract but must not persist jobs, staged candidates, application events, delivery state, processed-message state, or Gmail cursors.
- Gmail failures are fail-open for the daily workflow; the existing public-source job search still runs.
- Release 1 does not add company watchlists, new ATS adapters, Telegram inbound URLs, learning-to-rank, Pub/Sub, Relay integration, or application submission.

---

## File Structure

### New production files

- `src/job_hunter/gmail_models.py` — Gmail-only domain types and constants: decoded message, extracted job, classification, sync state/summary.
- `src/job_hunter/gmail_auth.py` — read-only OAuth token provider used by the runtime Gmail client.
- `src/job_hunter/gmail_client.py` — Gmail REST API calls, pagination models, history-expiry handling, MIME/body decoding.
- `src/job_hunter/gmail_classifier.py` — deterministic job-signal rules, LinkedIn/job-link extraction, Gemini fallback, confidence gating.
- `src/job_hunter/gmail_matching.py` — ordered job matching and derived application-state calculation.
- `src/job_hunter/gmail_sync.py` — backfill/incremental orchestration, idempotent message processing, staging/events, sync metrics.
- `src/job_hunter/sources/gmail_staged.py` — DB-backed `JobSource` exposing only staged candidates not yet materialized as Gmail-source jobs.
- `scripts/gmail_oauth_bootstrap.py` — one-time local interactive OAuth helper that prints a refresh token.

### Modified production files

- `pyproject.toml` — add Google OAuth dependencies.
- `src/job_hunter/models.py` — add `ReviewItem` only; keep Gmail-specific types out of the general model module.
- `src/job_hunter/config.py` — add a Gmail-specific settings loader without making normal `run` depend on Gmail secrets.
- `src/job_hunter/store.py` — add Gmail sync/message/staging/application-event/review-delivery tables and query methods.
- `src/job_hunter/pipeline.py` — append `GmailStagedSource` after the store exists; deliver pending review items after normal job delivery.
- `src/job_hunter/telegram.py` — format Gmail `REVIEW_NEEDED` items.
- `src/job_hunter/cli.py` — add `sync-gmail`, `--dry-run`, and `--force-backfill`.
- `.github/workflows/daily.yml` — run Gmail sync before normal job search with `continue-on-error: true`.
- `.env.example` — document Gmail environment variable names without values.
- `README.md` — document OAuth bootstrap, secrets, Gmail behavior, dry-run/backfill, and failure semantics.

### New tests

- `tests/test_gmail_auth.py`
- `tests/test_gmail_client.py`
- `tests/test_gmail_classifier.py`
- `tests/test_gmail_matching.py`
- `tests/test_gmail_sync.py`
- `tests/test_gmail_staged_source.py`

### Existing tests to extend

- `tests/test_store.py`
- `tests/test_config.py`
- `tests/test_cli.py`
- `tests/test_pipeline.py`
- `tests/test_telegram.py`

---

### Task 1: Gmail Domain Types, Runtime Settings, and OAuth Bootstrap

**Files:**
- Create: `src/job_hunter/gmail_models.py`
- Create: `src/job_hunter/gmail_auth.py`
- Create: `scripts/gmail_oauth_bootstrap.py`
- Modify: `src/job_hunter/config.py`
- Modify: `pyproject.toml`
- Test: `tests/test_gmail_auth.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `GMAIL_READONLY_SCOPE: str`
- Produces: `GmailSettings(client_id, client_secret, refresh_token, gemini_api_key, gemini_model, db_path)`
- Produces: `GmailMessage(message_id, thread_id, sender, subject, sent_at, snippet, body)`
- Produces: `ExtractedJob(source_platform, source_job_id, url, company, title, location, remote, description, index)`
- Produces: `GmailClassification(kind, confidence, company, role_title, source_job_id, job_urls, jobs, rationale)`
- Produces: `GmailSyncSummary(fetched, processed, job_alerts, application_events, review_needed, irrelevant, errors)`
- Produces: `AccessTokenProvider.get_access_token() -> str`
- Produces: `GoogleOAuthTokenProvider(settings: GmailSettings).get_access_token() -> str`
- Produces: `load_gmail_settings() -> GmailSettings`

- [ ] **Step 1: Write failing domain/config tests**

Add tests proving Gmail settings are independent of CV/Telegram settings and missing Gmail secrets fail explicitly:

```python
from job_hunter.config import load_gmail_settings


def test_load_gmail_settings_does_not_require_candidate_profile(monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini")
    monkeypatch.delenv("CANDIDATE_PROFILE_B64", raising=False)
    settings = load_gmail_settings()
    assert settings.client_id == "client"
    assert settings.db_path == "var/job_hunter.sqlite3"


def test_load_gmail_settings_requires_refresh_token(monkeypatch):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "client")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini")
    monkeypatch.delenv("GMAIL_REFRESH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="GMAIL_REFRESH_TOKEN"):
        load_gmail_settings()
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
pytest tests/test_config.py -q
```

Expected: FAIL because `load_gmail_settings` and `GmailSettings` do not exist.

- [ ] **Step 3: Add Gmail types and settings loader**

Create `gmail_models.py` with these concrete values/types:

```python
from dataclasses import dataclass, field
from datetime import datetime

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SUPPORTED_KINDS = frozenset({
    "JOB_ALERT", "RECRUITER_CONTACT", "APPLIED", "INTERVIEW",
    "TECHNICAL", "OFFER", "REJECTED", "REVIEW_NEEDED", "IRRELEVANT",
})
AUTO_CONFIDENCE_THRESHOLD = 0.90
MATCH_RECENCY_DAYS = 120


@dataclass(frozen=True, slots=True)
class GmailSettings:
    client_id: str
    client_secret: str
    refresh_token: str
    gemini_api_key: str
    gemini_model: str = "gemini-3.6-flash"
    db_path: str = "var/job_hunter.sqlite3"


@dataclass(frozen=True, slots=True)
class GmailMessage:
    message_id: str
    thread_id: str | None
    sender: str
    subject: str
    sent_at: datetime
    snippet: str
    body: str


@dataclass(frozen=True, slots=True)
class ExtractedJob:
    source_platform: str
    source_job_id: str | None = None
    url: str = ""
    company: str = ""
    title: str = ""
    location: str = ""
    remote: bool | None = None
    description: str = ""
    index: int = 0


@dataclass(frozen=True, slots=True)
class GmailClassification:
    kind: str
    confidence: float
    company: str = ""
    role_title: str = ""
    source_job_id: str | None = None
    job_urls: list[str] = field(default_factory=list)
    jobs: list[ExtractedJob] = field(default_factory=list)
    rationale: str = ""


@dataclass(slots=True)
class GmailSyncSummary:
    fetched: int = 0
    processed: int = 0
    job_alerts: int = 0
    application_events: int = 0
    review_needed: int = 0
    irrelevant: int = 0
    errors: int = 0
```

Add `load_gmail_settings()` in `config.py` using `_require_env()` for `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, and `GEMINI_API_KEY`, while reading `GEMINI_MODEL` and `JOB_HUNTER_DB_PATH` with the same defaults as normal settings.

- [ ] **Step 4: Add OAuth dependencies and token provider test**

Update `pyproject.toml` dependencies with:

```toml
"google-auth>=2.40",
"google-auth-oauthlib>=1.2",
```

Create `tests/test_gmail_auth.py` using a fake credentials object and assert the provider refreshes when needed and returns the token. Structure `GoogleOAuthTokenProvider` so credentials construction is injectable in tests rather than making network calls.

- [ ] **Step 5: Implement read-only OAuth provider and bootstrap script**

`gmail_auth.py` must construct Google credentials with exactly one scope, `GMAIL_READONLY_SCOPE`, and refresh through `google.auth.transport.requests.Request`.

The bootstrap script must use this client configuration and print only the refresh token on success:

```python
client_config = {
    "installed": {
        "client_id": os.environ["GMAIL_CLIENT_ID"],
        "client_secret": os.environ["GMAIL_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}
flow = InstalledAppFlow.from_client_config(client_config, scopes=[GMAIL_READONLY_SCOPE])
credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
if not credentials.refresh_token:
    raise RuntimeError("Google did not return a refresh token; revoke prior consent and retry")
print(credentials.refresh_token)
```

- [ ] **Step 6: Run tests and commit**

Run:

```bash
pytest tests/test_config.py tests/test_gmail_auth.py -q
```

Expected: PASS.

Commit:

```bash
git add pyproject.toml src/job_hunter/config.py src/job_hunter/gmail_models.py src/job_hunter/gmail_auth.py scripts/gmail_oauth_bootstrap.py tests/test_config.py tests/test_gmail_auth.py
git commit -m "feat: add Gmail OAuth configuration"
```

---

### Task 2: Gmail REST Client and Safe Message Decoding

**Files:**
- Create: `src/job_hunter/gmail_client.py`
- Test: `tests/test_gmail_client.py`

**Interfaces:**
- Consumes: `AccessTokenProvider.get_access_token() -> str`
- Consumes: `GmailMessage`
- Produces: `GmailPage(message_ids: list[str], next_page_token: str | None)`
- Produces: `GmailHistoryPage(message_ids: list[str], history_id: str, next_page_token: str | None)`
- Produces: `GmailHistoryExpired`
- Produces: `GmailClient.get_profile() -> tuple[str, str]` returning `(email_address, history_id)`
- Produces: `GmailClient.list_message_ids(query, page_token=None) -> GmailPage`
- Produces: `GmailClient.list_history(start_history_id, page_token=None) -> GmailHistoryPage`
- Produces: `GmailClient.get_message(message_id) -> GmailMessage`

- [ ] **Step 1: Write failing API/pagination tests**

Use the repository's existing fake HTTP-response style. Cover profile, search pagination, history pagination, and expired history:

```python
def test_list_message_ids_returns_ids_and_page_token():
    http = FakeHttp({"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "p2"})
    client = GmailClient(http=http, token_provider=FakeTokenProvider("token"))
    page = client.list_message_ids("after:2025/08/31")
    assert page.message_ids == ["m1", "m2"]
    assert page.next_page_token == "p2"
    assert http.last_headers["Authorization"] == "Bearer token"


def test_history_404_raises_history_expired():
    http = FakeHttpResponse(status_code=404, payload={"error": {"message": "historyId too old"}})
    client = GmailClient(http=http, token_provider=FakeTokenProvider("token"))
    with pytest.raises(GmailHistoryExpired):
        client.list_history("123")
```

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
pytest tests/test_gmail_client.py -q
```

Expected: FAIL because `gmail_client.py` does not exist.

- [ ] **Step 3: Implement Gmail REST calls**

Use these endpoints only:

```python
_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
# GET {_BASE}/profile
# GET {_BASE}/messages?q=...&maxResults=100&pageToken=...
# GET {_BASE}/messages/{message_id}?format=full
# GET {_BASE}/history?startHistoryId=...&historyTypes=messageAdded&maxResults=100&pageToken=...
```

Every request must send `Authorization: Bearer <token>`. `list_history` must deduplicate message IDs appearing multiple times in one response. A 404 from history becomes `GmailHistoryExpired`; other HTTP errors use `raise_for_status()`.

- [ ] **Step 4: Write failing MIME-decoding tests**

Cover:

```python
def test_decode_prefers_plain_text_over_html(): ...
def test_decode_falls_back_to_stripped_html(): ...
def test_decode_recurses_nested_multipart_payload(): ...
def test_decode_uses_snippet_when_body_is_empty(): ...
def test_decode_does_not_return_attachment_content(): ...
```

Use base64url fixture helper:

```python
def b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
```

- [ ] **Step 5: Implement safe decoding**

`get_message()` must:

1. Normalize headers case-insensitively.
2. Parse `From`, `Subject`, and Gmail `internalDate` into UTC `datetime`.
3. Recursively collect only inline `text/plain` and `text/html` payload bodies where `body.data` is present.
4. Prefer joined plain-text parts; otherwise strip HTML with BeautifulSoup.
5. Fall back to `snippet` if there is no inline textual body.
6. Never fetch attachment IDs in Release 1.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
pytest tests/test_gmail_client.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/gmail_client.py tests/test_gmail_client.py
git commit -m "feat: add Gmail read-only client"
```

---

### Task 3: Gmail Persistence, Staging, Events, and Idempotency

**Files:**
- Modify: `src/job_hunter/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `JobStore.has_processed_gmail_message(message_id: str) -> bool`
- Produces: `JobStore.record_gmail_message(...) -> None`
- Produces: `JobStore.get_gmail_sync_state(account_id: str) -> sqlite3.Row | None`
- Produces: `JobStore.save_gmail_sync_state(account_id, history_id, last_successful_sync_at, backfill_completed_at) -> None`
- Produces: `JobStore.stage_inbound_job(source_message_id, source_candidate_key, job: ExtractedJob) -> int`
- Produces: `JobStore.list_unmaterialized_inbound_jobs() -> list[sqlite3.Row]`
- Produces: `JobStore.save_application_event(...) -> int | None`
- Produces: `JobStore.list_application_events(job_id: int) -> list[sqlite3.Row]`
- Produces: `JobStore.pending_review_events() -> list[sqlite3.Row]`
- Produces: `JobStore.mark_review_delivered(event_ids: list[int], telegram_message_id: str) -> None`

- [ ] **Step 1: Write schema/idempotency tests**

Add tests that initialize a fresh DB and assert all new tables exist. Add duplicate-message/event/candidate tests:

```python
def test_application_event_source_message_is_idempotent(tmp_path):
    store = JobStore(tmp_path / "db.sqlite3")
    first = store.save_application_event(
        job_id=None, event_type="REVIEW_NEEDED", occurred_at="2026-08-31T10:00:00+00:00",
        source_message_id="m1", source_thread_id="t1", confidence=0.4,
        company="Acme", role_title="Frontend Engineer", rationale="ambiguous",
    )
    second = store.save_application_event(
        job_id=None, event_type="REVIEW_NEEDED", occurred_at="2026-08-31T10:00:00+00:00",
        source_message_id="m1", source_thread_id="t1", confidence=0.4,
        company="Acme", role_title="Frontend Engineer", rationale="ambiguous",
    )
    assert first is not None
    assert second == first
```

Also assert duplicate `(origin, source_message_id, source_candidate_key)` returns the existing inbound candidate row instead of inserting another.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
pytest tests/test_store.py -q
```

Expected: FAIL because Gmail persistence methods/tables do not exist.

- [ ] **Step 3: Add Gmail tables without altering existing tables**

Add `CREATE TABLE IF NOT EXISTS` statements for:

```sql
CREATE TABLE IF NOT EXISTS gmail_sync_state (
    account_id TEXT PRIMARY KEY,
    history_id TEXT,
    last_successful_sync_at TEXT,
    last_processed_message_at TEXT,
    backfill_completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

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
);

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
);

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
);

CREATE TABLE IF NOT EXISTS review_deliveries (
    event_id INTEGER PRIMARY KEY REFERENCES application_events(id),
    delivered_at TEXT NOT NULL,
    telegram_message_id TEXT
);
```

Do not add full email body columns.

- [ ] **Step 4: Implement store methods transactionally**

Rules:

- `record_gmail_message` uses `INSERT OR IGNORE` and never stores body text.
- `stage_inbound_job` updates `last_seen_at` when the same candidate key is seen again.
- `save_application_event` returns the existing event ID when the message ID already exists.
- `pending_review_events` returns only `REVIEW_NEEDED` events absent from `review_deliveries`, oldest first.
- `save_gmail_sync_state` updates timestamps atomically and never advances state from dry-run code paths.

- [ ] **Step 5: Add an unmaterialized-candidate query**

`list_unmaterialized_inbound_jobs()` must exclude candidates already represented by a Gmail-source job created from the same stable candidate key. Use the convention implemented in Task 7:

```text
Job.source = "gmail:<source_platform>"
Job.source_job_id = source_candidate_key
```

The query can compare `jobs.source` and `jobs.source_job_id` directly, avoiding a new staging-consumed column.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
pytest tests/test_store.py -q
```

Expected: PASS, including existing persistence tests.

Commit:

```bash
git add src/job_hunter/store.py tests/test_store.py
git commit -m "feat: persist Gmail intelligence state"
```

---

### Task 4: Deterministic Classification, Job Extraction, and Gemini Fallback

**Files:**
- Create: `src/job_hunter/gmail_classifier.py`
- Test: `tests/test_gmail_classifier.py`

**Interfaces:**
- Consumes: `GmailMessage`, `GmailClassification`, `ExtractedJob`, `GeminiClient`
- Produces: `is_probably_job_related(message: GmailMessage) -> bool`
- Produces: `classify_deterministically(message: GmailMessage) -> GmailClassification | None`
- Produces: `classify_email(message: GmailMessage, gemini: GeminiClient) -> GmailClassification`
- Produces: `source_candidate_key(job: ExtractedJob) -> str`

- [ ] **Step 1: Write failing deterministic-rule tests**

Add fixture-like messages for these exact outcomes:

```python
@pytest.mark.parametrize((subject, body, expected), [
    ("Thanks for applying", "We received your application for Frontend Engineer.", "APPLIED"),
    ("Update on your application", "We will not be moving forward with your application.", "REJECTED"),
    ("Interview invitation", "Choose a time for your interview with our team.", "INTERVIEW"),
    ("Technical assessment", "Please complete this coding challenge by Friday.", "TECHNICAL"),
    ("Offer", "We are pleased to offer you the position.", "OFFER"),
])
def test_high_signal_templates_are_deterministic(subject, body, expected): ...
```

Add LinkedIn alert test using sender `jobalerts-noreply@linkedin.com` and HTML-decoded body containing `https://www.linkedin.com/jobs/view/1234567890/`. Assert `JOB_ALERT`, confidence `1.0`, and one extracted job URL.

- [ ] **Step 2: Run classifier tests and verify failure**

Run:

```bash
pytest tests/test_gmail_classifier.py -q
```

Expected: FAIL because classifier functions do not exist.

- [ ] **Step 3: Implement deterministic precedence and cheap irrelevance gate**

Use this precedence when multiple deterministic phrases appear:

```text
OFFER > REJECTED > TECHNICAL > INTERVIEW > APPLIED > JOB_ALERT > RECRUITER_CONTACT
```

`is_probably_job_related()` must return true when sender/subject/body contains strong job terms such as `application`, `interview`, `recruiter`, `hiring`, `job alert`, `position`, `technical assessment`, `coding challenge`, `offer`, or known job-platform senders. If none are present, return `IRRELEVANT` with confidence `1.0` without calling Gemini.

- [ ] **Step 4: Implement URL/job extraction and stable candidate keys**

Extract HTTP(S) links with `urllib.parse`, strip tracking parameters with existing `canonicalize_url`, and retain links matching known job URL patterns (`linkedin.com/jobs/view`, `greenhouse`, `lever`, `ashbyhq`, `workable`, and generic URLs when Gemini explicitly identifies them as a job URL).

Candidate key order must be:

```python
if job.url:
    return "url:" + canonicalize_url(job.url)
if job.source_job_id:
    return f"id:{job.source_platform.lower()}:{job.source_job_id}"
return "fallback:" + "|".join([
    normalize_text(job.company), normalize_text(job.title), str(job.index)
])
```

- [ ] **Step 5: Write failing semantic-classification tests**

Use a fake Gemini client returning JSON. Cover high-confidence recruiter outreach, lower-confidence lifecycle output, malformed JSON, and unsupported kind:

```python
def test_low_confidence_semantic_lifecycle_becomes_review_needed():
    gemini = FakeGemini('{"kind":"INTERVIEW","confidence":0.72,"company":"Acme","role_title":"Frontend Engineer","jobs":[],"rationale":"possibly scheduling"}')
    result = classify_email(message("Next steps", "Can we find time to chat?"), gemini)
    assert result.kind == "REVIEW_NEEDED"
```

- [ ] **Step 6: Implement Gemini fallback with strict JSON validation**

Use `gemini.generate_text(prompt, json_mode=True)` with this schema instruction embedded verbatim in the prompt:

```text
Return one JSON object only with keys:
kind, confidence, company, role_title, source_job_id, job_urls, jobs, rationale.
kind must be one of JOB_ALERT, RECRUITER_CONTACT, APPLIED, INTERVIEW, TECHNICAL, OFFER, REJECTED, REVIEW_NEEDED, IRRELEVANT.
confidence must be a number from 0 to 1.
jobs must be an array of objects with source_platform, source_job_id, url, company, title, location, remote, description.
Do not infer facts not present in the email. Keep rationale under 160 characters.
```

Only send `sender`, `subject`, `snippet`, and a body truncated to 20,000 characters. If JSON is malformed, kind unsupported, required values conflict, or a lifecycle/recruiter result is below `AUTO_CONFIDENCE_THRESHOLD = 0.90`, return `REVIEW_NEEDED`. A confident `IRRELEVANT` result may remain irrelevant.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
pytest tests/test_gmail_classifier.py tests/test_gemini.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/gmail_classifier.py tests/test_gmail_classifier.py
git commit -m "feat: classify Gmail job signals"
```

---

### Task 5: Ordered Job Matching and Derived Application State

**Files:**
- Create: `src/job_hunter/gmail_matching.py`
- Modify: `src/job_hunter/store.py`
- Test: `tests/test_gmail_matching.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `GmailClassification`, `GmailMessage`, `JobStore`
- Produces: `JobMatch(job_id: int | None, reason: str, ambiguous: bool)`
- Produces: `match_job(store, classification, message) -> JobMatch`
- Produces: `derive_application_state(events) -> str | None`
- Produces: `JobStore.list_jobs_for_matching() -> list[sqlite3.Row]`
- Produces: `JobStore.current_application_state(job_id: int) -> str | None`

- [ ] **Step 1: Write failing priority-order matching tests**

Create store fixtures with multiple jobs and prove the matcher uses the approved priority exactly:

```python
def test_exact_canonical_url_beats_company_title(tmp_path): ...
def test_source_job_id_is_second_priority(tmp_path): ...
def test_company_and_normalized_title_matches_when_unique(tmp_path): ...
def test_company_only_recent_match_requires_single_candidate(tmp_path): ...
def test_ambiguous_company_match_returns_no_job(tmp_path): ...
```

The recent company-only window is exactly `MATCH_RECENCY_DAYS = 120`, measured from email `sent_at` against job `first_seen_at`/`last_seen_at`.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
pytest tests/test_gmail_matching.py -q
```

Expected: FAIL because matching module/methods do not exist.

- [ ] **Step 3: Implement matching without database mutation**

`match_job()` must canonicalize URLs with existing `canonicalize_url()` and normalize company/title with existing `normalize_text()`. It returns no job when a priority level has multiple equally valid matches. It does not create placeholder jobs for unresolved historical applications.

Source job ID matching compares the classification source job ID against existing `jobs.source_job_id`. URL matching compares canonicalized URLs in Python so tracking parameters do not prevent a match.

- [ ] **Step 4: Write failing derived-state tests**

Cover timestamp-first and tie precedence:

```python
def test_latest_event_wins_even_if_earlier_event_has_higher_stage(): ...
def test_same_timestamp_uses_offer_before_rejected(): ...
def test_review_needed_never_becomes_current_state(): ...
def test_unresolved_event_never_becomes_current_state(): ...
```

- [ ] **Step 5: Implement derived-state rule exactly**

Filter to events where `job_id` is non-null, `event_type != "REVIEW_NEEDED"`, and `confidence >= AUTO_CONFIDENCE_THRESHOLD`. Sort by:

```python
_STATE_TIE_PRECEDENCE = {
    "APPLIED": 1,
    "RECRUITER_CONTACT": 2,
    "INTERVIEW": 3,
    "TECHNICAL": 4,
    "REJECTED": 5,
    "OFFER": 6,
}
key = (event["occurred_at"], _STATE_TIE_PRECEDENCE[event["event_type"]], event["id"])
```

Return the event type from the maximum key.

- [ ] **Step 6: Integrate `current_application_state` into `JobStore` and commit**

Run:

```bash
pytest tests/test_gmail_matching.py tests/test_store.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/gmail_matching.py src/job_hunter/store.py tests/test_gmail_matching.py tests/test_store.py
git commit -m "feat: match Gmail events to jobs"
```

---

### Task 6: Backfill and Incremental Gmail Sync Orchestration

**Files:**
- Create: `src/job_hunter/gmail_sync.py`
- Test: `tests/test_gmail_sync.py`

**Interfaces:**
- Consumes: `GmailClient`, `JobStore`, `GeminiClient`, classifier/matcher/store interfaces from Tasks 2–5
- Produces: `GmailSyncService.sync(now: datetime, dry_run: bool = False, force_backfill: bool = False) -> GmailSyncSummary`
- Produces: `build_backfill_query(now: datetime) -> str`
- Produces: `process_message(message: GmailMessage, dry_run: bool) -> GmailClassification`

- [ ] **Step 1: Write failing 12-month backfill tests**

Use fake Gmail pages/messages and a temporary SQLite database. Assert:

```python
def test_first_sync_scans_12_months_and_marks_backfill_complete(tmp_path): ...
def test_backfill_rerun_skips_already_processed_message_ids(tmp_path): ...
def test_backfill_completion_is_not_written_when_a_page_fetch_fails(tmp_path): ...
```

`build_backfill_query()` must calculate the same calendar date one year earlier (clamp Feb 29 to Feb 28) and return a Gmail query that searches job-related mail content:

```text
after:YYYY/MM/DD {application interview recruiter hiring "job alert" position "technical assessment" "coding challenge" offer}
```

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
pytest tests/test_gmail_sync.py -q
```

Expected: FAIL because sync service does not exist.

- [ ] **Step 3: Implement per-message processing and crash-safe persistence**

For each message ID:

1. Increment `fetched`.
2. If non-dry-run and `has_processed_gmail_message(message_id)` is true, skip fetching/classifying it.
3. Fetch/decode message.
4. Classify it.
5. In dry-run: log the classification/extraction and return without any store write.
6. For `JOB_ALERT`, stage each extracted job using `source_candidate_key()`; increment `job_alerts` once per message.
7. For concrete recruiter outreach with extracted jobs, stage those candidates; lifecycle handling continues below.
8. For lifecycle kinds, run `match_job()`.
9. If matching is ambiguous/unresolved, persist `REVIEW_NEEDED` rather than the original lifecycle kind.
10. If matching is resolved and classification confidence is at least `0.90`, persist the lifecycle event.
11. Persist minimal `gmail_messages` metadata only after related staging/event writes succeed.
12. Increment summary counters.

Do not write message body into any store call.

- [ ] **Step 4: Add stale-opportunity protection for historical alerts**

The 12-month backfill exists primarily to reconstruct lifecycle history. To avoid flooding the current search with expired alert jobs, stage `JOB_ALERT` candidates only when the email `sent_at` is within 14 days of the sync's `now`. Older job-alert messages are still recorded as processed/classified but do not create current inbound candidates. Recruiter outreach is staged regardless of age only when it represents an explicit concrete role; lifecycle events still follow the full 12-month backfill.

Add tests proving a 6-month-old LinkedIn alert does not stage jobs while a 3-day-old alert does.

- [ ] **Step 5: Write failing incremental/history tests**

Cover:

```python
def test_second_sync_uses_saved_history_id_instead_of_backfill_search(tmp_path): ...
def test_history_message_ids_are_idempotent(tmp_path): ...
def test_expired_history_falls_back_to_overlap_search(tmp_path): ...
def test_incremental_cursor_advances_only_after_page_is_processed(tmp_path): ...
def test_dry_run_does_not_write_cursor_or_message_state(tmp_path): ...
def test_force_backfill_runs_search_without_erasing_existing_events(tmp_path): ...
```

- [ ] **Step 6: Implement incremental cursor semantics**

At successful end of the initial backfill:

```python
email_address, history_id = gmail.get_profile()
store.save_gmail_sync_state(
    account_id=email_address,
    history_id=history_id,
    last_successful_sync_at=now.isoformat(),
    backfill_completed_at=now.isoformat(),
)
```

Normal sync uses `users.history.list(startHistoryId=saved_history_id, historyTypes=messageAdded)`. After every history page's message IDs are safely processed, continue pagination; update the stored history ID only after all pages complete.

When `GmailHistoryExpired` occurs, do a bounded overlap search beginning one day before `last_successful_sync_at`, process idempotently, fetch the current profile history ID, and replace the stale cursor only after success.

`force_backfill=True` repeats the 12-month search idempotently but does not delete events/candidates or clear existing state first.

- [ ] **Step 7: Add compact metrics logging and commit**

Log exactly this shape at INFO:

```text
gmail_fetched=<n> gmail_processed=<n> gmail_job_alerts=<n> gmail_application_events=<n> gmail_review_needed=<n> gmail_irrelevant=<n> gmail_errors=<n>
```

Run:

```bash
pytest tests/test_gmail_sync.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/gmail_sync.py tests/test_gmail_sync.py
git commit -m "feat: sync Gmail job intelligence"
```

---

### Task 7: Feed Staged Gmail Jobs Through the Existing Discovery Pipeline

**Files:**
- Create: `src/job_hunter/sources/gmail_staged.py`
- Modify: `src/job_hunter/sources/__init__.py`
- Modify: `src/job_hunter/pipeline.py`
- Test: `tests/test_gmail_staged_source.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `JobStore.list_unmaterialized_inbound_jobs()`
- Produces: `GmailStagedSource(store: JobStore).discover() -> list[Job]`
- Existing `collect_candidates()` remains the only path that enriches/upserts/prefilters these jobs.

- [ ] **Step 1: Write failing staged-source tests**

Assert exact conversion convention:

```python
def test_staged_source_returns_normal_job_with_stable_gmail_identity(tmp_path):
    # staged row source_platform="linkedin", source_candidate_key="url:https://..."
    jobs = GmailStagedSource(store).discover()
    assert jobs[0].source == "gmail:linkedin"
    assert jobs[0].source_job_id == "url:https://www.linkedin.com/jobs/view/123"
```

Also prove rows already materialized as `jobs(source="gmail:linkedin", source_job_id=<candidate_key>)` are not emitted again.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
pytest tests/test_gmail_staged_source.py -q
```

Expected: FAIL because source does not exist.

- [ ] **Step 3: Implement `GmailStagedSource`**

Map every staging row to `Job` without extra network access:

```python
Job(
    source=f"gmail:{row['source_platform'] or 'unknown'}",
    source_job_id=row["source_candidate_key"],
    title=row["title"],
    company=row["company"],
    location=row["location"],
    url=row["url"],
    description=row["description"],
    remote=None if row["remote"] is None else bool(row["remote"]),
)
```

- [ ] **Step 4: Write failing pipeline integration test**

Stage one Gmail job, inject no public sources, and assert normal pipeline behavior still evaluates it through existing ranking/evaluation logic. A second run must not re-evaluate the same materialized candidate unless its normal job description-change rules require it.

- [ ] **Step 5: Append the source only after store initialization**

In `run_pipeline()`:

```python
store = store or JobStore(settings.db_path)
base_sources = sources if sources is not None else build_sources(settings, http)
sources = [*base_sources, GmailStagedSource(store)]
```

Do not put this source in `build_sources(settings, http)` because it requires the initialized `JobStore`.

- [ ] **Step 6: Run pipeline/discovery tests and commit**

Run:

```bash
pytest tests/test_gmail_staged_source.py tests/test_discovery.py tests/test_pipeline.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/sources/gmail_staged.py src/job_hunter/sources/__init__.py src/job_hunter/pipeline.py tests/test_gmail_staged_source.py tests/test_pipeline.py
git commit -m "feat: route Gmail jobs through discovery"
```

---

### Task 8: Telegram Review Delivery for Ambiguous Gmail Signals

**Files:**
- Modify: `src/job_hunter/models.py`
- Modify: `src/job_hunter/telegram.py`
- Modify: `src/job_hunter/pipeline.py`
- Test: `tests/test_telegram.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `ReviewItem(event_id, company, role_title, occurred_at, subject, rationale)`
- Produces: `build_gmail_review_digest(items: Sequence[ReviewItem]) -> str`
- Consumes: `JobStore.pending_review_events()` and `mark_review_delivered()`

- [ ] **Step 1: Write failing review-format tests**

Expected text shape:

```text
Gmail review needed
- Acme — Frontend Engineer | ambiguous scheduling language
- Unknown company — Senior Engineer | could not match message to a job
```

Assert deterministic order by `occurred_at`, then `event_id`, and that no email body is rendered.

- [ ] **Step 2: Implement `ReviewItem` and formatter**

Add to `models.py`:

```python
@dataclass(slots=True)
class ReviewItem:
    event_id: int
    company: str
    role_title: str
    occurred_at: str
    subject: str
    rationale: str
```

`build_gmail_review_digest()` must use `company or "Unknown company"` and `role_title or "Unknown role"`, with rationale truncated to 200 characters.

- [ ] **Step 3: Write failing delivery/retry test**

In pipeline tests, create two pending review events. Assert:

- successful `send_message` marks both in `review_deliveries`;
- failed send leaves both pending for the next run;
- Gmail review delivery is independent from the normal scored-job digest.

- [ ] **Step 4: Add review delivery after normal job/document delivery**

At the end of non-dry-run `run_pipeline()`, query pending review events, build `ReviewItem`s, call `telegram.send_message()`, and only call `mark_review_delivered()` if a message ID is returned.

Do not include `REVIEW_NEEDED` application events in the scored job digest.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
pytest tests/test_telegram.py tests/test_pipeline.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/models.py src/job_hunter/telegram.py src/job_hunter/pipeline.py tests/test_telegram.py tests/test_pipeline.py
git commit -m "feat: surface Gmail review events"
```

---

### Task 9: CLI, Fail-Open GitHub Actions Integration, and Operator Documentation

**Files:**
- Modify: `src/job_hunter/cli.py`
- Modify: `.github/workflows/daily.yml`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `load_gmail_settings`, `GoogleOAuthTokenProvider`, `GmailClient`, `GmailSyncService`, existing `HttpClient`, `GeminiClient`, `JobStore`
- Produces CLI: `python -m job_hunter sync-gmail [--dry-run] [--force-backfill]`

- [ ] **Step 1: Write failing CLI parser/dispatch tests**

Cover:

```python
def test_parser_accepts_sync_gmail_dry_run(): ...
def test_parser_accepts_sync_gmail_force_backfill(): ...
def test_sync_gmail_does_not_load_candidate_profile_settings(mocker): ...
def test_sync_gmail_returns_nonzero_on_fatal_auth_error(mocker): ...
```

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
pytest tests/test_cli.py -q
```

Expected: FAIL because `sync-gmail` command is absent.

- [ ] **Step 3: Implement CLI construction and dispatch**

Add parser:

```python
sync_parser = subparsers.add_parser("sync-gmail", help="Read Gmail job signals into shared state")
sync_parser.add_argument("--dry-run", action="store_true", help="Classify/extract without persisting Gmail-derived state")
sync_parser.add_argument("--force-backfill", action="store_true", help="Repeat the 12-month backfill idempotently")
```

Build Gmail dependencies only for this command. Use `datetime.now(timezone.utc)` and log the summary. Fatal authorization/API setup errors may return exit code 1; per-message classification errors stay inside `GmailSyncSummary.errors` and do not abort the entire sync.

- [ ] **Step 4: Modify daily workflow with an explicitly fail-open sync step**

Insert after `Restore prior state` and before `Run job hunter`:

```yaml
      - name: Sync Gmail intelligence
        continue-on-error: true
        env:
          GMAIL_CLIENT_ID: ${{ secrets.GMAIL_CLIENT_ID }}
          GMAIL_CLIENT_SECRET: ${{ secrets.GMAIL_CLIENT_SECRET }}
          GMAIL_REFRESH_TOKEN: ${{ secrets.GMAIL_REFRESH_TOKEN }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: python -m job_hunter sync-gmail
```

Do not add Gmail secrets to the normal `Run job hunter` step. Keep the existing SQLite restore/upload behavior so Gmail state is persisted in the same artifact.

- [ ] **Step 5: Update `.env.example` and README with concrete setup commands**

Document these names only, never example secret values:

```text
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REFRESH_TOKEN=
```

Document bootstrap:

```bash
export GMAIL_CLIENT_ID='...'
export GMAIL_CLIENT_SECRET='...'
python scripts/gmail_oauth_bootstrap.py
```

Then instruct the operator to copy the printed refresh token into GitHub Secret `GMAIL_REFRESH_TOKEN` and unset/remove local shell history as appropriate.

Document inspection commands:

```bash
python -m job_hunter sync-gmail --dry-run
python -m job_hunter sync-gmail --force-backfill
```

State explicitly that dry-run does not advance Gmail cursors or persist Gmail-derived state, and that `--force-backfill` is idempotent rather than destructive.

- [ ] **Step 6: Run focused tests and full suite**

Run:

```bash
pytest tests/test_cli.py -q
pytest -q
```

Expected: all tests PASS.

- [ ] **Step 7: Perform repository privacy scan**

Run:

```bash
grep -RInE 'GMAIL_REFRESH_TOKEN=.+|client_secret[^A-Za-z].*[A-Za-z0-9_-]{20,}' . \
  --exclude-dir=.git --exclude='*.md'
```

Expected: no committed Gmail credential values. Environment-variable names and documentation placeholders are allowed.

- [ ] **Step 8: Commit operator integration**

```bash
git add src/job_hunter/cli.py .github/workflows/daily.yml .env.example README.md tests/test_cli.py
git commit -m "feat: run Gmail intelligence before daily search"
```

---

### Task 10: Final End-to-End Verification Against the Approved Spec

**Files:**
- Modify only if a verification failure requires a fix; do not add new scope.

**Interfaces:**
- Verifies every Release 1 interface and global constraint above.

- [ ] **Step 1: Run the complete automated suite**

```bash
pytest -q
```

Expected: PASS with zero failures.

- [ ] **Step 2: Verify CLI help surfaces both commands and Gmail safety flags**

```bash
python -m job_hunter --help
python -m job_hunter sync-gmail --help
```

Expected: `run` and `sync-gmail` are listed; `--dry-run` and `--force-backfill` appear only on `sync-gmail`.

- [ ] **Step 3: Verify schema contains no full-body storage**

```bash
python - <<'PY'
from job_hunter.store import JobStore
from pathlib import Path
import sqlite3, tempfile
with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "db.sqlite3"
    store = JobStore(path)
    store.close()
    conn = sqlite3.connect(path)
    for table in ("gmail_messages", "application_events", "inbound_job_candidates"):
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        assert "body" not in columns and "email_body" not in columns, (table, columns)
print("privacy schema check passed")
PY
```

Expected: `privacy schema check passed`.

- [ ] **Step 4: Verify workflow fail-open configuration**

```bash
python - <<'PY'
from pathlib import Path
text = Path('.github/workflows/daily.yml').read_text()
assert 'Sync Gmail intelligence' in text
assert 'continue-on-error: true' in text
assert text.index('Sync Gmail intelligence') < text.index('Run job hunter')
print('workflow ordering check passed')
PY
```

Expected: `workflow ordering check passed`.

- [ ] **Step 5: Dry-run against a local mocked/fake Gmail fixture path, not a real mailbox**

Run the integration test that exercises the CLI/service with fake Gmail responses:

```bash
pytest tests/test_gmail_sync.py tests/test_cli.py -q
```

Expected: PASS and no SQLite mutations in dry-run tests.

- [ ] **Step 6: Review git diff for scope discipline**

```bash
git diff main...HEAD --stat
git diff main...HEAD -- . ':!docs/superpowers/specs/*' ':!docs/superpowers/plans/*'
```

Expected: changes are limited to Release 1 Gmail intelligence, its tests, workflow integration, dependencies, and operator documentation. There are no company-watchlist, Telegram-inbound, Relay, LinkedIn-browser, or application-submission changes.

- [ ] **Step 7: Final implementation commit only if verification required fixes**

If verification required code/test/doc fixes, commit only those fixes:

```bash
git add -A
git commit -m "fix: complete Gmail intelligence verification"
```

If no files changed, do not create an empty commit.
