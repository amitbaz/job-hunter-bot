# Telegram Job Navigator — Design Spec

Date: 2026-08-31
Status: Approved design

## Summary

Replace the current long Telegram digest with a single interactive job card that lets the user browse deliverable jobs using inline buttons. The same Telegram message is edited in place when the user moves to the previous or next job.

The first version introduces navigation only. A visible `Apply` button is included as a placeholder for a future application workflow, but it must not submit an application or perform any application-side effects.

## Goals

- Present one job at a time instead of a long digest.
- Browse deliverable jobs using `Previous` and `Next` inline buttons.
- Keep deterministic score-first ordering.
- `View job` opens the original posting URL.
- Show an `Apply` placeholder now without application side effects.
- Preserve current score/decision eligibility and PDF delivery behavior.
- Make navigation work after the scheduled GitHub Actions process exits.

## Non-goals

- Automated application submission or form filling.
- CAPTCHA/2FA handling.
- Changes to discovery, ranking, Gemini evaluation, or cover-letter generation.
- A general-purpose Telegram command framework.
- Wraparound navigation.

## Current State

The pipeline currently builds one grouped text digest and sends it through an outbound-only `TelegramClient`. Strong matches receive cover-letter PDFs as separate Telegram documents.

`DigestItem` currently contains `job_id`, `company`, `title`, `score`, `decision`, `url`, and `hard_blockers`. Job location already exists in `Job` and SQLite, but is not propagated to `DigestItem`.

GitHub Actions runners are ephemeral, so interactive navigation cannot depend on process memory or only on the runner-local SQLite copy once the workflow has finished.

## User Experience

The bot sends one card:

```text
Senior Frontend Developer

Company: Example GmbH
Location: Berlin
Match: 87%
```

Inline keyboard:

```text
[ View job ]  [ Apply ]
[ ◀ Previous ]  [ 3 / 12 ]  [ Next ▶ ]
```

### View job

A Telegram URL button that opens the original posting. If the URL is missing or unusable, omit the button.

### Apply

A callback button reserved for the future application flow. In this feature it only answers the callback with a small notification such as `Apply functionality coming soon.` It must not generate material, submit anything, or mark a job as applied.

### Previous / Next

Callback buttons that use Telegram `editMessageText` so the existing job-card message changes in place. First/last positions do not wrap.

### Position

Render `current / total` as a no-op callback button because Telegram inline keyboards do not provide a truly disabled button. Edge navigation buttons may also use no-op callbacks when navigation in that direction is unavailable.

## Ordering

Use one deterministic order for each navigation session:

1. score descending
2. company ascending, case-insensitive
3. title ascending, case-insensitive
4. job ID ascending

## Navigation Session

A navigation session represents the ordered batch attached to one Telegram message.

Persist at minimum:

- opaque session ID
- ordered job/card entries or job IDs
- Telegram message ID after send succeeds
- created timestamp
- optional expiry/status

Callback data must stay compact and must not contain the whole session. Logical forms:

- `nav:<session>:<index>`
- `apply:<session>:<index>`
- `noop:<session>:<index>`

The final encoding may be shortened to remain comfortably inside Telegram's callback-data limit.

## Production Interaction Architecture

Interactive Telegram callbacks require an inbound HTTP endpoint. A scheduled GitHub Actions job cannot receive arbitrary button presses after it exits.

Separate the feature into two runtimes:

1. **Scheduled delivery runtime** — evaluates jobs, creates a navigation session, sends the first card, and continues current PDF delivery.
2. **Telegram webhook runtime** — receives callback queries, loads durable session/card state, edits the existing Telegram message, and acknowledges callbacks.

The two runtimes therefore need a small shared durable interaction store. The current artifact-restored SQLite database is not sufficient by itself because the webhook runtime cannot rely on the runner-local copy.

Recommended implementation boundary: define a navigation-session repository interface, keep a simple SQLite implementation for local/unit tests, and use a shared production persistence adapter for the webhook/delivery runtimes. The implementation plan should choose the concrete production adapter based on the project's deployment environment without coupling Telegram formatting to that provider.

## Components

### `DigestItem` enrichment

Add `location` and populate it in both normal evaluation and retry paths.

### Job-card formatter

A deterministic formatter responsible for card text and inline-keyboard JSON, including location fallback, one-based position display, URL omission, edge-button behavior, and callback payload construction.

### Telegram client extensions

Add narrowly scoped methods for:

- sending a card with `reply_markup`
- editing a card
- answering callback queries

Keep persistence out of the HTTP client.

### Navigation-session repository

Responsibilities:

- create a session from an ordered list
- attach the Telegram message ID
- retrieve a session
- validate bounds
- expire/prune sessions

### Callback handler

Responsibilities:

- parse callback payloads
- load/validate the session and target index
- rebuild or load the target card
- edit the Telegram message
- acknowledge callbacks
- return harmless responses for `apply` and `noop`
- handle stale or malformed callbacks without crashing

The callback handler must not invoke discovery, Gemini evaluation, or cover-letter generation.

### Pipeline integration

When deliverable jobs exist:

1. select and deterministically sort them
2. create durable navigation state
3. send the first card
4. attach the Telegram message ID to the session
5. only after successful send, mark the represented jobs message-delivered
6. deliver ready-to-apply PDFs exactly as today

If no deliverable jobs exist, keep `No matching jobs today.` and do not create a session.

## Failure Handling

- Initial card send failure: mark none of the session jobs as message-delivered; keep retry eligibility.
- Card edit failure: log safely and answer the callback with a short failure notification where possible.
- Unknown/expired session: answer with `This job list has expired.` and do not crash.
- Invalid index/malformed callback: reject safely; do not edit the message.
- Missing job/card data: treat as stale rather than silently navigating elsewhere.

## Security

- Never put bot tokens or other secrets into callback data or logs.
- Treat callback payloads as untrusted input.
- Validate session IDs and indices.
- Optionally validate Telegram webhook secret/header if supported by the chosen runtime.
- Keep `Apply` strictly side-effect free in this feature.

## Testing

Unit coverage must include:

- card text formatting and location fallback
- missing URL behavior
- first/middle/last keyboards
- callback serialization/parsing
- malformed callback rejection
- session repository behavior and bounds
- Telegram send/edit/answer payloads
- placeholder Apply/no-op behavior

Pipeline coverage must verify:

- one navigator card replaces the long digest
- deterministic ordering
- location propagation
- delivery marking only after successful send
- retry behavior after failed send
- unchanged PDF delivery

Interaction tests must verify Next, Previous, edges, no-op, Apply placeholder, expired session, missing data, and invalid indices.

## Acceptance Criteria

1. Deliverable jobs are represented by one Telegram job card instead of one long multi-job digest.
2. Card shows title, company, location, and match percentage.
3. `View job` opens the posting URL.
4. `Apply` is visible and side-effect free.
5. `Previous` and `Next` edit the same Telegram message.
6. Position shows `<current> / <total>`.
7. Navigation does not wrap.
8. Navigation still works after the scheduled pipeline process exits.
9. Interaction state is durable/shared rather than process-memory-only.
10. Existing >60 deliverability/decision filtering remains unchanged.
11. Cover-letter PDF delivery remains functional.
12. Stale/invalid callbacks fail safely.
13. Tests cover formatter, callbacks, persistence, Telegram HTTP calls, pipeline integration, and interaction handling.

## Expected Implementation Surface

Likely existing files:

- `src/job_hunter/models.py`
- `src/job_hunter/telegram.py`
- `src/job_hunter/pipeline.py`
- `src/job_hunter/store.py` or a new persistence module
- `tests/test_telegram.py`
- `tests/test_pipeline.py`

Likely new files:

- `src/job_hunter/telegram_navigation.py`
- `src/job_hunter/telegram_webhook.py` or equivalent runtime entry point
- navigation/session persistence tests
- callback-handler tests

The future Apply workflow should be able to replace only the placeholder callback behavior without redesigning navigation.