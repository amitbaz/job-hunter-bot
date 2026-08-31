# Job Hunter Bot R1 — Gmail Intelligence Design

Date: 2026-08-31
Status: Proposed for review
Repository: `amitbaz/job-hunter-bot`

## 1. Purpose

Release 1 adds Gmail as a first-class intelligence input to the standalone Job Hunter Bot without coupling Gmail logic into the existing job-discovery pipeline.

The release has two goals:

1. Discover job opportunities from Gmail, especially LinkedIn and other job-board alert emails plus recruiter outreach.
2. Build an auditable application lifecycle history from application confirmations, recruiter responses, interview invitations, technical assessments, offers, and rejections.

This release does not automate LinkedIn login, browser scraping, job applications, or email mutation. Gmail access is read-only.

## 2. Scope

### In scope

- Gmail OAuth with read-only scope.
- One-time local OAuth bootstrap that produces credentials suitable for GitHub Actions.
- A dedicated `sync-gmail` command, separate from the existing `run` command.
- A one-time 12-month historical backfill on first successful connection.
- Incremental Gmail sync after the backfill.
- Job-alert discovery from LinkedIn and other job-board emails.
- Recruiter inbound opportunity extraction.
- Application lifecycle event detection.
- Append-only application event history.
- High-confidence automatic status derivation.
- Low-confidence `REVIEW_NEEDED` events surfaced for manual review.
- Idempotent processing keyed by Gmail message ID.
- Shared SQLite persistence with the existing Job Hunter Bot.
- Fail-open workflow behavior so Gmail problems do not stop the normal daily job search.
- Logs and metrics for Gmail processing.
- A dry-run inspection mode that classifies Gmail messages without mutating application state.

### Out of scope

- Logged-in LinkedIn browser automation.
- LinkedIn credential or cookie storage.
- Gmail send, reply, archive, delete, label, or other mutation.
- Company watchlists.
- New ATS discovery adapters.
- Telegram inbound job-URL ingestion.
- Automatic job application submission.
- Learning-to-rank from application outcomes.
- Real-time Gmail Pub/Sub infrastructure.
- Relay integration.

These are candidates for later release specs.

## 3. Architectural decision

Use a separate Gmail synchronization stage that shares the existing SQLite database.

The existing daily job-search pipeline remains responsible for public-source discovery, normalization, ranking, Gemini evaluation, cover-letter generation, and Telegram delivery. Gmail synchronization is isolated in a dedicated command and module boundary.

```text
GitHub Actions
      |
      +--> restore SQLite state
      |
      +--> sync-gmail
      |       |
      |       +--> Gmail OAuth (read-only)
      |       +--> 12-month backfill once
      |       +--> incremental sync thereafter
      |       +--> deterministic classification first
      |       +--> Gemini classification when needed
      |       |
      |       +--> job alert ----------> discovered job candidate
      |       +--> recruiter inbound --> inbound opportunity
      |       +--> application email --> application_event
      |       +--> rejection ---------> application_event
      |       +--> interview ---------> application_event
      |       +--> ambiguous ---------> REVIEW_NEEDED
      |
      +--> existing job_hunter run
      |       |
      |       +--> public sources
      |       +--> Gmail-discovered jobs
      |       +--> dedupe / rank / Gemini
      |       +--> Telegram
      |
      +--> persist SQLite artifact
```

This architecture is preferred because it keeps Gmail failures isolated, preserves the current pipeline boundaries, and allows Gmail ingestion to be tested independently.

## 4. Command model

Add a new CLI command:

```text
python -m job_hunter sync-gmail
```

The command performs only Gmail synchronization and persistence. It does not run public job discovery or Telegram delivery of the normal daily job digest.

The existing command remains:

```text
python -m job_hunter run
```

The scheduled workflow runs `sync-gmail` before `run`.

A Gmail-specific dry-run option must be supported so classification can be inspected without creating application events or changing derived application state.

## 5. Authentication and secrets

### OAuth model

Use Google OAuth for a personal Gmail account with the minimum required read-only Gmail scope.

A one-time local bootstrap flow performs interactive Google authorization and produces the long-lived refresh token needed by GitHub Actions.

The scheduled GitHub Actions runner must never require interactive login.

### Secret storage

OAuth credentials are stored only as GitHub Actions secrets.

Expected secret inputs:

- Gmail OAuth client ID.
- Gmail OAuth client secret.
- Gmail refresh token.

No OAuth credential may be committed to the repository or written to the SQLite state artifact.

### Gmail permission boundary

The bot must have read-only Gmail access. It must not send, reply, archive, delete, label, mark read/unread, or otherwise mutate mailbox state.

## 6. Gmail sync state

Add a small sync-state table so the bot can distinguish the first historical backfill from normal incremental operation.

Conceptual schema:

```text
gmail_sync_state
- account_id                 primary key
- last_successful_sync_at
- last_processed_message_at
- backfill_completed_at
- created_at
- updated_at
```

The exact cursor mechanism may use Gmail history IDs or message timestamps depending on implementation constraints, but the following guarantees are required:

- The 12-month backfill runs once after the first successful authorization.
- Subsequent runs process only messages not successfully processed before.
- The sync cursor advances only after the corresponding processed batch is durably persisted.
- A crash during a batch must not silently skip messages on the next run.
- Reprocessing is safe because Gmail message IDs are idempotency keys.

## 7. Historical backfill

On the first successful Gmail connection, scan the previous 12 months.

The backfill must:

- Fetch in bounded batches.
- Persist successful work incrementally.
- Avoid loading the full 12-month mailbox window into memory.
- Use the same classification and matching logic as incremental sync.
- Tolerate partial failures and resume safely.
- Mark `backfill_completed_at` only after the backfill has been fully processed.

The backfill is intended to reconstruct as much recent application history as possible before ongoing incremental tracking begins.

## 8. Email classification strategy

Use a two-stage classifier.

### Stage 1 — deterministic classification

Apply deterministic rules to known high-signal email patterns before spending Gemini calls.

Examples include:

- LinkedIn job alert senders and known job-alert structures.
- Standard application confirmation language.
- Common rejection templates.
- Obvious interview scheduling messages.
- Obvious coding challenge or technical-assessment invitations.

Deterministic classification should be preferred when confidence is high.

### Stage 2 — semantic classification

Use Gemini only when deterministic rules cannot confidently classify the message.

Gemini receives only the minimum message content required for classification and extraction.

Supported classifications:

```text
JOB_ALERT
RECRUITER_CONTACT
APPLIED
INTERVIEW
TECHNICAL
OFFER
REJECTED
REVIEW_NEEDED
IRRELEVANT
```

### Confidence policy

High-confidence outcomes may create application events automatically.

Low-confidence or conflicting outcomes must become `REVIEW_NEEDED` and must not alter the derived application status automatically.

## 9. Job-alert and recruiter-opportunity ingestion

### Job alerts

A job-alert email may contain one or more job postings.

The Gmail sync stage extracts candidate fields such as:

- company
- role title
- location when present
- job URL
- source platform
- source job ID when recoverable

Extracted jobs enter the same normalized job flow used by public-source discovery. They must not bypass existing deduplication, ranking, or Gemini evaluation rules.

LinkedIn alert emails are therefore a discovery channel, not a separate scoring system.

### Recruiter inbound

Recruiter outreach that describes a concrete role or includes a job URL becomes an inbound opportunity candidate.

If enough information is available, the role enters the normal job pipeline. If not, the message can still produce a `RECRUITER_CONTACT` application event or review item without inventing missing job details.

## 10. Application event model

Do not make the existing `jobs.status` column the primary history store.

Add an append-only `application_events` table.

Conceptual schema:

```text
application_events
- id                     primary key
- job_id                  nullable foreign key to jobs
- event_type
- occurred_at
- source                  default 'gmail'
- source_message_id       unique
- source_thread_id        nullable
- confidence
- company                 extracted snapshot
- role_title              extracted snapshot
- rationale               short classifier rationale
- created_at
```

Supported event types:

```text
APPLIED
RECRUITER_CONTACT
INTERVIEW
TECHNICAL
OFFER
REJECTED
REVIEW_NEEDED
```

The table is append-only from the sync pipeline's perspective. Corrections should be represented explicitly rather than silently rewriting history.

The current application state is derived from event history, not treated as the only durable record.

## 11. Job matching strategy

When an application-related email is classified, match it back to an existing job using the following priority order:

1. Exact canonical or known job URL match.
2. Source job ID match.
3. Company plus normalized role-title match.
4. Company-only plus a recent application window, but only when the result is unambiguous.
5. Otherwise leave `job_id` unresolved and create `REVIEW_NEEDED`.

Matching must prefer false negatives over false positives. An unresolved event is acceptable; attaching an event to the wrong job is not.

No ambiguous email may mutate a derived job/application status automatically.

## 12. Derived application state

High-confidence events can update a derived current state exposed by the store/API layer.

The lifecycle is not strictly linear, but the common progression is:

```text
APPLIED
  -> RECRUITER_CONTACT
  -> INTERVIEW
  -> TECHNICAL
  -> OFFER

or

APPLIED / RECRUITER_CONTACT / INTERVIEW / TECHNICAL
  -> REJECTED
```

`REVIEW_NEEDED` never becomes a derived lifecycle state.

The implementation plan must define deterministic precedence rules for deriving the current state when several events exist.

## 13. Idempotency

Gmail message ID is the primary idempotency key.

Processing the same Gmail message more than once must not create duplicate application events or duplicate Gmail-origin job candidates.

The sync stage must be safe to rerun after crashes, workflow retries, or restored database artifacts.

## 14. Privacy and data minimization

The SQLite artifact should contain only the data needed for job intelligence and auditability.

Persisted Gmail-derived metadata may include:

- Gmail message ID
- Gmail thread ID
- sender
- subject
- relevant timestamps
- extracted company
- extracted role title
- extracted job URLs / IDs
- classification
- confidence
- short classifier rationale

Do not persist the full email body by default.

The message body may be processed in memory for extraction/classification and then discarded.

OAuth credentials must never be stored in SQLite.

## 15. Workflow integration

The scheduled workflow becomes:

```text
restore SQLite artifact
        |
        v
sync-gmail
        |
        v
existing job hunter run
        |
        v
upload SQLite artifact
```

### Fail-open requirement

Gmail synchronization must not become a hard dependency for the existing daily job search.

If Gmail authorization fails, Google is temporarily unavailable, or an individual message cannot be parsed:

- log the error;
- record sync error metrics;
- preserve the last safe sync cursor where appropriate;
- continue the normal public-source job-hunter run;
- avoid repeated noisy notifications for the same persistent Gmail failure.

The daily job search must remain useful even when Gmail is unavailable.

## 16. Observability

Log a compact Gmail sync summary on every run.

Example metrics:

```text
gmail_fetched=42
gmail_processed=39
gmail_job_alerts=12
gmail_application_events=7
gmail_review_needed=2
gmail_errors=1
```

Logs must not include OAuth secrets, full email bodies, or other unnecessary private content.

## 17. Review-needed behavior

Ambiguous email classifications or ambiguous job matches create `REVIEW_NEEDED` records.

These items should be surfaced in Telegram as a compact review section rather than silently ignored.

The first release does not require a full conversational correction workflow. Manual review may be completed through an explicit follow-up command or later release. The important Release 1 requirement is that ambiguous events are visible and do not corrupt application history.

## 18. Testing strategy

### Unit tests

- Deterministic email classifier rules.
- Event-type mapping.
- Confidence threshold behavior.
- Company/title normalization.
- Job matching priority order.
- Derived application-state precedence.
- Gmail message-ID idempotency.

### Gmail client tests

Use mocked Gmail API responses to test:

- pagination/batching;
- message retrieval;
- transient API failures;
- expired/invalid credentials;
- incremental cursor behavior.

### Integration tests

Use a temporary SQLite database and fixture emails to verify:

- 12-month backfill writes expected events;
- rerunning a backfill does not duplicate events;
- incremental sync processes only new messages;
- a crash before cursor advancement is safely recoverable;
- ambiguous matching creates `REVIEW_NEEDED` rather than a false association;
- Gmail failure does not block the normal job-hunter pipeline;
- Gmail-discovered jobs still pass through normal deduplication/ranking/evaluation.

### Privacy tests

Assert that full fixture email bodies are not persisted in SQLite by default.

## 19. Operational safety

Release 1 must preserve the existing Job Hunter safety boundary:

- no application submission;
- no legal or work-authorization attestations;
- no CAPTCHA or 2FA automation;
- no logged-in LinkedIn browser automation;
- no Gmail mutation.

The bot remains an assistant for discovery, analysis, material preparation, and application-state intelligence.

## 20. Success criteria

Release 1 is complete when:

1. Gmail can be authorized once locally with read-only access and used non-interactively from GitHub Actions.
2. A 12-month backfill can complete incrementally and resume safely after interruption.
3. LinkedIn/job-board alerts produce normalized job candidates without duplicate records.
4. Recruiter outreach can produce useful inbound opportunities or recruiter-contact events.
5. Clear application confirmations, rejections, interviews, technical assessments, and offers create idempotent application events.
6. Ambiguous messages never change application status automatically and are surfaced as review-needed items.
7. Existing public-source job hunting continues when Gmail fails.
8. The SQLite artifact does not persist full email bodies or OAuth credentials by default.
9. Tests cover classifier behavior, matching, idempotency, backfill, incremental sync, and fail-open execution.

## 21. Later releases

The following work is intentionally deferred:

- Release 2: broader discovery expansion, direct company/ATS watchlists, canonical URL resolution improvements, and additional specialist sources.
- Release 3: Telegram inbound job-URL ingestion and immediate scoring.
- Release 4: application-outcome analytics and learning feedback for ranking and interview preparation.

Each later release requires its own design spec and implementation plan before code changes begin.
