# Telegram Job Navigator

The bot delivers matching jobs as one interactive Telegram card instead of one long digest. `Previous` and `Next` edit the same message in place. `View job` opens the source posting. `Apply` is intentionally a placeholder and only shows `Apply functionality coming soon.`

## Current architecture

The scheduled job-search pipeline still runs in GitHub Actions and SQLite remains the source of truth.

```text
GitHub Actions cron
        |
        | discover / evaluate / generate PDFs
        v
SQLite: var/job_hunter.sqlite3
        |
        | uploaded after each run
        v
GitHub Actions artifact: job-hunter-state

Telegram card
        |
        | callback_query
        v
Vercel Python/Flask Function
        |
        v
NavigationSessionRepository
        |
        v
GitHubArtifactNavigationRepository
        |
        v
latest SQLite artifact (read-only)
        |
        v
Telegram editMessageText
```

The webhook is request-driven serverless compute. There is no always-on custom server and no Docker host to operate.

The Vercel project should be a **separate project for `job-hunter-bot`**, not an API route inside the Interviewer App. Both projects may live in the same Vercel account/team, but they remain independently deployable.

## Storage boundary

The HTTP webhook does not know how navigation state is stored. It depends on:

```python
class NavigationSessionRepository(Protocol):
    def get_session(self, session_id: str) -> NavigationSession | None: ...
```

Today the concrete repository is `GitHubArtifactNavigationRepository`, which downloads the latest `job-hunter-state` artifact and reads the SQLite snapshot read-only.

This boundary is intentional: when the bot moves navigation state to Supabase, the webhook can switch to `SupabaseNavigationRepository` without changing Telegram callback payloads, job-card rendering, or Previous/Next behavior.

## Vercel deployment

Vercel's Python runtime discovers the Flask application through the root `main.py` entrypoint. `pyproject.toml` declares:

```toml
[tool.vercel]
entrypoint = "main:app"
```

`vercel.json` explicitly installs the webhook extra:

```json
{
  "installCommand": "pip install -e '.[webhook]'"
}
```

This keeps Flask out of the normal scheduled bot dependencies while guaranteeing it is installed for the Vercel deployment.

### 1. Create the Vercel project

Create a new Vercel project connected to:

```text
amitbaz/job-hunter-bot
```

Recommended project name:

```text
job-hunter-telegram-webhook
```

Use the repository root as the project root. Do not point this deployment at the Interviewer App.

### 2. Configure production environment variables

Set these as **server-side Vercel environment variables**:

```text
TELEGRAM_BOT_TOKEN=<same bot token used by the daily runner>
TELEGRAM_WEBHOOK_SECRET=<random URL-safe secret>
GITHUB_REPOSITORY=amitbaz/job-hunter-bot
GITHUB_STATE_TOKEN=<repository-scoped token with Actions read access>
GITHUB_STATE_ARTIFACT_NAME=job-hunter-state
GITHUB_STATE_CACHE_DIR=/tmp/job-hunter-state
```

The webhook does **not** need:

```text
GEMINI_API_KEY
CANDIDATE_PROFILE_B64
COVER_LETTER_TEMPLATE_B64
TELEGRAM_CHAT_ID
GMAIL_CLIENT_ID
GMAIL_CLIENT_SECRET
GMAIL_REFRESH_TOKEN
```

`GITHUB_STATE_TOKEN` must remain server-side. Do not put it in Telegram callback data, URLs, logs, or browser-exposed environment variables.

Generate a webhook secret locally, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Use the same value for the Vercel `TELEGRAM_WEBHOOK_SECRET` variable and webhook registration.

### 3. Deploy

Deploy the Vercel project from `main` after the relevant PRs are merged. Preview deployments are useful for verifying build/runtime behavior, but Telegram should ultimately be registered against the stable production domain.

### 4. Verify health

```bash
curl https://YOUR-VERCEL-DOMAIN/health
```

Expected:

```json
{"ok":true}
```

### 5. Register Telegram

Set the local values used by the registration helper:

```bash
export TELEGRAM_BOT_TOKEN='...'
export TELEGRAM_WEBHOOK_SECRET='...'
```

Register the stable production URL:

```bash
python scripts/set_telegram_webhook.py \
  --url https://YOUR-VERCEL-DOMAIN/telegram/webhook
```

The helper registers only `callback_query` updates and configures Telegram to send `X-Telegram-Bot-Api-Secret-Token`. The Flask route rejects requests whose secret header does not match.

Re-register whenever the production webhook URL or webhook secret changes.

## Expected Telegram behavior

A batch with 12 deliverable jobs appears as one message:

```text
Senior Frontend Developer

Company: Example GmbH
Location: Berlin
Match: 87%

[ View job ]  [ Apply ]
[ ◀ Previous ]  [ 3 / 12 ]  [ Next ▶ ]
```

Jobs are ordered by:

1. match score descending;
2. company ascending;
3. title ascending;
4. job ID ascending.

Navigation does not wrap at the first or last job.

## Artifact synchronization window

The daily pipeline stores the navigation session in SQLite before sending the Telegram card, but `job-hunter-state` is uploaded only when the GitHub Actions workflow reaches its artifact-upload step.

A user can therefore press Next immediately after receiving the card while the newest session is not yet in the latest downloadable artifact. In that case the webhook responds:

```text
Job list is still syncing. Try again shortly.
```

Retrying after the workflow finishes reads the new session. No stale session is silently substituted.

Navigation sessions remain in SQLite for 30 days and are pruned by later pipeline runs.

## Failure behavior

- Wrong Telegram secret: HTTP 403 before storage access.
- Invalid JSON: HTTP 400.
- Non-callback Telegram updates: ignored with HTTP 200.
- GitHub/artifact failure: callback says `Could not load this job list right now.`.
- Missing new session: callback says `Job list is still syncing. Try again shortly.`.
- Expired session: callback says `This job list has expired.`.
- Telegram edit failure: callback says `Could not update this job right now.`.

Valid Telegram requests are acknowledged with HTTP 200 after application-level failures so Telegram does not unnecessarily retry them.

## Existing bot behavior preserved

- GitHub Actions remains the scheduler.
- SQLite remains the source of truth.
- Deliverability remains score `> 60` plus the current decision allowlist.
- Strong matches still receive cover-letter PDFs.
- Failed Telegram card delivery leaves jobs pending for the next run.
- A successful card marks all represented jobs as `telegram_message` delivered.
- If only a PDF failed, the bot retries the PDF without sending a duplicate navigator card.
- The bot still sends nothing when a run has no new/pending deliverable jobs.
- `Apply` does not submit or mark an application.
- No Supabase runtime dependency is introduced by this deployment.

# Planned Supabase migration

This section is the durable migration record. Do not skip directly to the final architecture; migrate one boundary at a time so current feature work remains unblocked.

## Phase A — now: Vercel + GitHub artifact + SQLite

```text
GitHub Actions -> SQLite -> job-hunter-state
                           ^
                           |
Telegram -> Vercel -> GitHubArtifactNavigationRepository
```

This is the current supported design.

## Phase B — bot begins writing shared state to Supabase

Migrate bot persistence incrementally. Jobs, evaluations, application events, and navigation sessions do not all need to move in one release.

When Telegram navigation sessions are reliably written to Supabase, add:

```text
SupabaseNavigationRepository.get_session(session_id)
```

It must satisfy the existing `NavigationSessionRepository` contract.

A temporary dual-write period is acceptable during migration if needed, but the webhook should have one explicitly configured read source at a time. Avoid a permanent SQLite-then-Supabase fallback because it can hide failed migrations and stale state.

## Phase C — Supabase becomes navigation source of truth

Switch the Vercel webhook from:

```text
GitHubArtifactNavigationRepository
```

to:

```text
SupabaseNavigationRepository
```

At that point:

- remove GitHub artifact reads from the webhook;
- remove `GITHUB_STATE_TOKEN`, `GITHUB_STATE_ARTIFACT_NAME`, and `GITHUB_STATE_CACHE_DIR` from the Vercel deployment;
- keep Telegram callback data unchanged;
- keep `telegram_navigation.py` unchanged;
- keep card rendering and Previous/Next behavior unchanged.

This is why the repository interface exists now.

## Phase D — optional move to Supabase Edge Functions

Once the callback runtime no longer needs Python/SQLite artifact access, evaluate whether the thin Telegram HTTP adapter should move from Vercel to a Supabase Edge Function.

That move is **optional**, not a requirement of using Supabase. Keeping the webhook on Vercel is valid if it remains simpler operationally.

If the adapter moves later, the conceptual flow becomes:

```text
Telegram
   |
   v
Supabase Edge Function
   |
   v
Supabase Postgres
   ^
   |
Job Hunter Bot + Interviewer App
```

Make that decision based on deployment ownership, observability, latency and cost—not merely because the data is in Supabase.

## Future migration checklist

Before switching the webhook to Supabase:

1. Define the canonical Supabase navigation-session schema.
2. Decide whether session cards are normalized rows or a JSON snapshot.
3. Add migration/dual-write tests from SQLite to Supabase.
4. Implement `SupabaseNavigationRepository` behind the existing protocol.
5. Verify read consistency and session expiry semantics.
6. Cut the Vercel webhook to the Supabase repository explicitly.
7. Remove artifact credentials only after production verification.
8. Separately decide whether Vercel remains the HTTP runtime or an Edge Function is preferable.

The Interviewer App can share the same Supabase ecosystem without sharing deployment/runtime code with the Telegram webhook.

## Troubleshooting

### Vercel build cannot import Flask

Confirm `vercel.json` still contains:

```text
pip install -e '.[webhook]'
```

and `pyproject.toml` still defines Flask in the `webhook` optional dependency.

### `/health` works but navigation fails

Check the Vercel runtime logs and verify `GITHUB_STATE_TOKEN` has access to Actions artifacts in the private repository.

### New card says it is still syncing

Wait until the daily GitHub Actions run has uploaded `job-hunter-state`, then press the button again. This is expected only during the short artifact synchronization window.

### Telegram sends 403

The value registered with Telegram and the Vercel `TELEGRAM_WEBHOOK_SECRET` must match exactly. Re-run `scripts/set_telegram_webhook.py` after changing the secret.
