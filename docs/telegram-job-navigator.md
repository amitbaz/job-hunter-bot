# Telegram Job Navigator

The bot now delivers matching jobs as one interactive Telegram card instead of one long digest. `Previous` and `Next` edit the same message in place. `View job` opens the source posting. `Apply` is intentionally a placeholder and only shows `Apply functionality coming soon.`

## Architecture

The scheduled GitHub Actions job remains the owner of discovery, evaluation, cover-letter generation, PDF delivery, and SQLite persistence.

Navigation sessions are stored in the existing `var/job_hunter.sqlite3`. The normal `job-hunter-state` Actions artifact therefore also contains Telegram navigation state. No second database is required.

Button presses arrive after the scheduled Action has exited, so they are handled by a small always-available Flask webhook service. The webhook downloads the newest `job-hunter-state` artifact, opens the SQLite copy read-only, loads the navigation session, and calls Telegram `editMessageText`.

## Webhook runtime

Build the provider-neutral container with:

```bash
docker build -f Dockerfile.telegram-webhook -t job-hunter-telegram-webhook .
```

The container exposes the application on `$PORT` (default `8080`) and provides:

- `GET /health`
- `POST /telegram/webhook`

Deploy the container to any service that provides a stable public HTTPS URL and can remain available for Telegram callbacks.

## Required webhook environment variables

```text
TELEGRAM_BOT_TOKEN=<same bot token used by the daily runner>
TELEGRAM_WEBHOOK_SECRET=<random URL-safe secret>
GITHUB_REPOSITORY=amitbaz/job-hunter-bot
GITHUB_STATE_TOKEN=<GitHub token with Actions read access to this private repository>
GITHUB_STATE_ARTIFACT_NAME=job-hunter-state
GITHUB_STATE_CACHE_DIR=/tmp/job-hunter-state
```

The webhook does **not** need `GEMINI_API_KEY`, `CANDIDATE_PROFILE_B64`, `COVER_LETTER_TEMPLATE_B64`, or `TELEGRAM_CHAT_ID`.

For `GITHUB_STATE_TOKEN`, use a fine-grained token restricted to this repository with permission to read Actions artifacts. Do not reuse or expose the token in callback data, URLs, logs, or client-side code.

Generate a webhook secret locally, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Store the same value as `TELEGRAM_WEBHOOK_SECRET` in the webhook deployment environment.

## Register the Telegram webhook

After deployment, verify the health endpoint first:

```bash
curl https://YOUR-HOST/health
```

Expected response:

```json
{"ok":true}
```

Then set the local environment variables used by the registration helper:

```bash
export TELEGRAM_BOT_TOKEN='...'
export TELEGRAM_WEBHOOK_SECRET='...'
```

Register the callback endpoint once:

```bash
python scripts/set_telegram_webhook.py --url https://YOUR-HOST/telegram/webhook
```

The helper registers only `callback_query` updates and configures Telegram to send `X-Telegram-Bot-Api-Secret-Token` on each request. The webhook rejects requests whose secret header does not match.

Re-run the helper whenever the public webhook URL or webhook secret changes.

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

1. match score descending
2. company name ascending
3. title ascending
4. job ID ascending

Navigation does not wrap at the first or last job.

## Artifact synchronization

The daily pipeline writes the navigation session into SQLite before it sends the card. GitHub Actions uploads the updated SQLite file as `job-hunter-state` when the run finishes.

There is therefore a short synchronization window immediately after a new card arrives in Telegram where the newest artifact may not have been uploaded yet. If a navigation button is pressed during that window, the webhook answers `Job list is still syncing. Try again shortly.` instead of failing or navigating to the wrong job. Retrying after the workflow finishes uses the new artifact.

Navigation sessions remain in SQLite for 30 days and are pruned by later pipeline runs.

## Existing behavior preserved

- Deliverability remains score `> 60` plus the existing decision allowlist.
- Strong matches still receive their cover-letter PDF documents.
- Failed Telegram card delivery leaves jobs pending for the next run.
- A successful card marks all jobs represented by that card as `telegram_message` delivered.
- If only a PDF failed previously, the bot retries the PDF without sending a duplicate navigator card.
- The bot still sends nothing when a run has no new/pending deliverable jobs.
- `Apply` does not submit an application, mark a job applied, or trigger any application workflow.
