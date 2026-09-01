# Telegram Vercel Webhook Architecture Design

**Date:** 2026-09-01

## Status

Approved for implementation.

## Context

The job hunter currently runs as a scheduled GitHub Actions workflow. It discovers and evaluates jobs, persists state in SQLite, sends Telegram messages/PDFs, uploads `job-hunter-state`, and then exits.

The Telegram job navigator introduces interactive `Previous`, `Next`, and placeholder `Apply` buttons. Telegram callback queries can arrive long after the GitHub Actions run has finished, so they require a stable public HTTPS endpoint.

The project also has a longer-term direction: gradually move Job Hunter Bot state from SQLite to the same Supabase ecosystem used by the Interviewer App. That migration must not block current feature development.

## Decision

Deploy the Telegram callback receiver as a **Vercel-hosted Python/Flask Function in the Job Hunter Bot repository**.

Keep the Telegram interaction layer independent of its persistence mechanism through a small `NavigationSessionRepository` interface.

Current repository implementation:

```text
GitHubArtifactNavigationRepository
    -> download latest job-hunter-state artifact
    -> open SQLite snapshot read-only
    -> load Telegram navigation session
```

Future repository implementation:

```text
SupabaseNavigationRepository
    -> query Supabase
    -> load Telegram navigation session
```

The Telegram callback handler must not know which storage implementation is active.

## Goals

1. Give Telegram a stable public HTTPS callback endpoint without running an always-on custom server.
2. Keep the existing GitHub Actions cron and SQLite persistence unchanged.
3. Deploy the webhook as a separate Vercel project from the Interviewer App, even if both use the same Vercel account/team.
4. Preserve the existing Telegram navigator behavior and security model.
5. Introduce a storage boundary that makes the later SQLite -> Supabase migration small and explicit.
6. Document the eventual Supabase migration path so later implementation does not require rediscovering these architectural decisions.

## Non-goals

- Do not migrate the bot to Supabase in this change.
- Do not add Supabase SDK dependencies.
- Do not move the scheduled job-search pipeline from GitHub Actions.
- Do not move the webhook into the Interviewer App repository.
- Do not implement the `Apply` workflow.
- Do not introduce queues, Redis, or another database.
- Do not deploy a Supabase Edge Function yet.

## Architecture

### Current production architecture

```text
GitHub Actions cron
        |
        | discovery / evaluation / PDFs
        v
SQLite: var/job_hunter.sqlite3
        |
        | uploaded after run
        v
GitHub Actions artifact: job-hunter-state

Telegram job card
        |
        | callback_query
        v
Vercel Python Function
        |
        v
NavigationSessionRepository
        |
        v
GitHubArtifactNavigationRepository
        |
        v
latest SQLite artifact snapshot
        |
        v
Telegram editMessageText
```

### Future architecture after Supabase migration

```text
Job Hunter pipeline -------------------+
                                        |
                                        v
                                    Supabase
                                        ^
                                        |
Telegram -> Vercel webhook -> SupabaseNavigationRepository
                                        ^
                                        |
                               Interviewer App
```

Whether the webhook later remains on Vercel or moves to a Supabase Edge Function is intentionally deferred. Once Supabase becomes the source of truth, moving the HTTP adapter is optional because the Telegram handler and persistence contract will already be separated.

## Component boundaries

### 1. Telegram callback domain logic

Existing `telegram_navigation.py` remains responsible for:

- callback payload encoding/parsing;
- navigation index validation;
- rendering job-card text and keyboard;
- placeholder `Apply` acknowledgement;
- calling `editMessageText` through the Telegram client.

It must remain unaware of GitHub, SQLite, Vercel, or Supabase.

### 2. Navigation session repository

Create a narrow protocol:

```python
class NavigationSessionRepository(Protocol):
    def get_session(self, session_id: str) -> NavigationSession | None: ...
```

The initial concrete implementation, `GitHubArtifactNavigationRepository`, owns all current storage mechanics:

1. Load the newest `job-hunter-state` artifact through `GitHubArtifactStateLoader`.
2. Return a distinct unavailable/syncing outcome when no usable current snapshot/session exists.
3. Open the downloaded SQLite snapshot read-only.
4. Read the navigation session through `navigation_store.get_navigation_session`.
5. Close the SQLite connection before returning.

The webhook route calls only the repository interface.

### 3. Flask webhook application

`telegram_webhook.py` remains a Flask application factory because it is testable and supported by Vercel's Python runtime.

The app is responsible for:

- `GET /health`;
- `POST /telegram/webhook`;
- verifying `X-Telegram-Bot-Api-Secret-Token` with constant-time comparison;
- validating Telegram JSON structure;
- short-circuiting `Apply` and no-op callback actions without touching persistence;
- loading navigation sessions via `NavigationSessionRepository`;
- converting repository failures into safe Telegram callback acknowledgements;
- never leaking GitHub tokens, webhook secrets, artifact paths, or exception text to Telegram.

### 4. Vercel entrypoint

Add a small root-level Python entrypoint that exports the Flask `app` expected by Vercel.

Because the repository uses a `src/` Python package layout, the entrypoint must make `src` importable before importing `job_hunter.telegram_webhook`.

Configure Vercel's Python runtime through `[tool.vercel]` in `pyproject.toml` and a minimal `vercel.json` for function duration/bundle exclusions.

The Vercel project is dedicated to Job Hunter Bot webhook infrastructure; it is not part of the Interviewer App deployment.

## Environment variables

### Required now

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_WEBHOOK_SECRET
GITHUB_REPOSITORY=amitbaz/job-hunter-bot
GITHUB_STATE_TOKEN
GITHUB_STATE_ARTIFACT_NAME=job-hunter-state
GITHUB_STATE_CACHE_DIR=/tmp/job-hunter-state
```

`GITHUB_STATE_TOKEN` must be a repository-scoped token with only the permissions necessary to read Actions artifacts.

The Vercel function does not need:

```text
GEMINI_API_KEY
CANDIDATE_PROFILE_B64
COVER_LETTER_TEMPLATE_B64
TELEGRAM_CHAT_ID
GMAIL_CLIENT_ID
GMAIL_CLIENT_SECRET
GMAIL_REFRESH_TOKEN
```

### Future Supabase variables

Do not add these now. When the repository switches to Supabase, the deployment will instead receive the minimum server-side Supabase credentials required by the chosen access model. A Supabase service-role/secret key must never be exposed to a browser or callback payload.

## Vercel deployment model

Vercel is used as request-driven compute, not as an always-running process.

Telegram sends an HTTPS POST, Vercel invokes the Flask function, the function processes the callback, returns HTTP 200, and the runtime may shut down. No process-local state is trusted across invocations.

The webhook must therefore continue to load navigation state from external persistence on every navigation callback. Temporary artifact files may be cached under `/tmp`, but correctness cannot depend on that cache surviving.

## Data flow

### Navigation callback

1. Telegram POSTs a `callback_query` to `/telegram/webhook`.
2. Flask verifies the Telegram secret header.
3. The callback payload is parsed.
4. For `Apply`/no-op actions, respond immediately without storage access.
5. For `Previous`/`Next`, call `NavigationSessionRepository.get_session(session_id)`.
6. The GitHub-artifact repository loads the newest artifact and reads the session from SQLite.
7. `handle_callback_query` renders the target card.
8. `TelegramClient.edit_job_card` edits the original Telegram message.
9. The callback query is acknowledged.

### New-card synchronization window

The GitHub Action writes the SQLite session before sending the Telegram card, but the Actions artifact is uploaded only when the workflow reaches the upload step. A user may press a navigation button during that window.

The existing user-facing behavior remains:

```text
Job list is still syncing. Try again shortly.
```

No stale session may be silently substituted for the requested session ID.

## Error handling

- Invalid webhook secret -> HTTP 403, no artifact access.
- Invalid JSON -> HTTP 400.
- Non-callback Telegram update -> HTTP 200 and ignore.
- Invalid callback payload -> acknowledge as unavailable.
- Artifact API/network failure -> acknowledge `Could not load this job list right now.` and return HTTP 200 to Telegram.
- Missing session in latest snapshot -> acknowledge `Job list is still syncing. Try again shortly.`.
- Expired session -> existing `This job list has expired.` behavior.
- Telegram edit failure -> existing `Could not update this job right now.` behavior.

Telegram should not repeatedly retry application-level failures, so valid Telegram webhook requests return HTTP 200 after safe acknowledgement even when the downstream artifact or Telegram edit operation fails.

## Security

1. Verify Telegram's secret header before parsing/processing callback data.
2. Use `hmac.compare_digest` for secret comparison.
3. Keep Telegram callback payloads opaque and free of credentials.
4. Keep the GitHub Actions token server-side in Vercel environment variables.
5. Scope `GITHUB_STATE_TOKEN` to the one private repository and Actions-read permission where possible.
6. Do not log secret values or full environment dumps.
7. Keep SQLite artifact access read-only in the webhook.
8. Do not rely on obscurity of the Vercel URL as authentication.

## Supabase migration path

### Phase A - Current change

- GitHub Actions remains scheduler/worker.
- SQLite remains source of truth.
- Vercel receives Telegram callbacks.
- `GitHubArtifactNavigationRepository` supplies sessions.

### Phase B - Bot begins Supabase migration

When jobs/navigation state start moving to Supabase, add a `SupabaseNavigationRepository` implementing the same `get_session(session_id)` contract.

Prefer a clean cutover for navigation reads after the corresponding navigation-session data is reliably written to Supabase. Avoid a permanent dual-read fallback because it hides migration errors and complicates consistency semantics.

If a temporary migration period requires dual-write, GitHub Actions may write both SQLite and Supabase, but the webhook should have one explicitly configured read source at a time.

### Phase C - Supabase becomes source of truth

- Remove GitHub artifact access from the webhook.
- Remove `GITHUB_STATE_TOKEN` and artifact-specific environment variables from Vercel.
- Remove `GitHubArtifactNavigationRepository` only after SQLite compatibility is no longer needed.
- Keep Telegram domain logic unchanged.

### Phase D - Optional Edge Function move

Once the webhook has no Python/SQLite dependency, evaluate moving the thin HTTP adapter to a Supabase Edge Function.

This is optional. Keeping it on Vercel is valid if operationally simpler. The migration decision should be based on ownership, observability, latency, deployment workflow, and cost rather than an assumption that all Supabase-backed code must run inside Supabase.

## Testing strategy

### Unit tests

- Repository returns a session from an artifact snapshot.
- Repository returns `None` for a missing session.
- Repository propagates/classifies artifact-loader failures correctly.
- Repository opens SQLite read-only.
- Webhook delegates session loading to the repository instead of directly using GitHub/SQLite classes.
- `Apply` and no-op callbacks do not call the repository.

### Vercel entrypoint test

Import the Vercel entrypoint with environment/config dependencies stubbed and assert that it exports a Flask application. This catches `src/` path/import regressions before deployment.

### Regression tests

Run the complete suite with webhook dependencies installed:

```bash
pip install -e '.[test,webhook]'
pytest -q
```

Existing Gmail, discovery, PDF, Telegram delivery, artifact-loader, and navigator behavior must remain green.

## Documentation requirements

Update `docs/telegram-job-navigator.md` to make Vercel the primary deployment path and include:

- architecture diagram;
- Vercel project separation from Interviewer App;
- required environment variables;
- deployment and health-check steps;
- Telegram webhook registration;
- troubleshooting;
- the staged SQLite -> Supabase migration path;
- explicit statement that Supabase is not introduced by this change.

Keep this design spec and its implementation plan as the durable architectural record for future migration work.

## Acceptance criteria

1. The webhook runs as a Vercel-compatible Flask/Python Function.
2. No Docker host is required for the supported deployment path.
3. The handler depends on `NavigationSessionRepository`, not directly on GitHub artifact/SQLite classes.
4. Current behavior still reads sessions from the existing GitHub Actions SQLite artifact.
5. No Supabase runtime dependency or schema change is introduced.
6. GitHub Actions remains the scheduled bot runtime.
7. The Interviewer App deployment is not modified.
8. All webhook and full regression tests pass.
9. Documentation clearly records both current deployment and future Supabase migration phases.
