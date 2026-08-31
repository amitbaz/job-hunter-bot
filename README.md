# Job Hunter Bot

A daily, mostly hands-off job-hunting assistant that runs on GitHub Actions. It reads Gmail job signals, discovers public remote job postings, deduplicates them in SQLite, evaluates each one against your candidate profile with Gemini, drafts tailored cover letters and renders them as PDFs for strong matches, and delivers a concise digest through Telegram.

The bot **never submits applications**. It prepares material for you to review and send yourself — see [v1 safety boundary](#v1-safety-boundary) below.

## Project direction

Job Hunter Bot currently runs as a standalone Python application using SQLite for persistence.

The longer-term direction is to share the same Supabase/Postgres backend used by the [Interviewer App](https://github.com/amitbaz/interviewer-app). The migration will happen incrementally and must not block ongoing feature development in either project.

Until an individual persistence domain is explicitly migrated, SQLite remains the production source of truth for Job Hunter Bot. New features should avoid unnecessary coupling to SQLite so that persistence can later move behind shared repository/service boundaries without requiring unrelated feature rewrites.

Target ecosystem:

```text
Job Hunter Bot  ->  Supabase/Postgres  <-  Interviewer App
```

This is an evolutionary migration, not a rewrite. Documentation describing the target architecture should not be interpreted as meaning that Supabase is already available or in use by this repository.

## Roadmap

Current product direction includes:

- Improve Telegram job browsing and navigation.
- Add an explicit application workflow and application status tracking.
- Gradually migrate persistent job/application data from SQLite to Supabase/Postgres.
- Share candidate, job, application, and related data with the Interviewer App where appropriate.
- Move toward a unified job-search -> application -> interview-preparation workflow across both projects.

The exact shared schema and migration phases are intentionally not defined here. Each migration phase should have its own design and implementation plan before code changes begin.

## Architecture

```
all public sources (Remotive, Arbeitnow, Jobicy, Himalayas, Remote OK, We Work Remotely, Hacker News, DuckDuckGo, ATS boards)
  -> enrich + dedupe -> profession gate + prefilter -> deterministic ranking or profile-aware ranking
  -> diversity-constrained top-N shortlist (stable-ranking fallback on error) -> Gemini evaluation
  -> cover letter generation + PDF rendering (strong matches only)
  -> Telegram digest + PDF delivery
```

- `src/job_hunter/sources/` — public job discovery adapters: Remotive, Arbeitnow, Jobicy, Himalayas, Remote OK, We Work Remotely, Hacker News, DuckDuckGo query expansion, plus optional Ashby/Lever/Greenhouse ATS boards. Each source fails open: if one adapter errors, the run continues with the rest.
- `config/search.yml` supports role families, query templates, ATS domains, and `max_search_queries_per_run`; DuckDuckGo queries expand each role/template pair across the configured ATS domains before deduping.
- Only software/product-engineering professions reach Gemini. The default evaluation budget is 35 jobs per run, with source-diverse selection (`source_minimum_per_run: 2`, `source_max_share: 0.5`) when profile extraction succeeds.
- `src/job_hunter/preferences.py` extracts a compact preference profile from `CANDIDATE_PROFILE_B64`; `src/job_hunter/ranking.py` then uses preferred roles, seniority, must-have signals, location fit, avoid signals, and source quality to rank eligible jobs before Gemini. If profile extraction or diversity selection fails, the pipeline falls back to the stable deterministic global ranking and logs the fallback without exposing private profile text.
- `skip` evaluations are persisted but never sent to Telegram. Telegram sections are ordered by final Gemini score descending, unknown decisions are omitted, and only scores strictly greater than 60 are eligible for digest or retry delivery.
- `src/job_hunter/prefilter.py` — cheap deterministic filtering before spending Gemini calls.
- `src/job_hunter/evaluation.py` / `gemini.py` — Gemini-based scoring and rationale.
- `src/job_hunter/cover_letter.py` / `pdf.py` — cover letter drafting and PDF rendering for jobs that clear the bar.
- `src/job_hunter/store.py` — SQLite persistence (dedup, evaluation cache, delivery tracking) at `var/job_hunter.sqlite3` by default.
- `src/job_hunter/telegram.py` — outbound-only Telegram Bot API delivery (digest message + PDF documents).
- `src/job_hunter/gmail_sync.py` — read-only Gmail intake that classifies job signals and stages discovered jobs or review-needed events in the shared SQLite state.
- `src/job_hunter/pipeline.py` / `cli.py` — orchestration and the `python -m job_hunter run` and `python -m job_hunter sync-gmail` entrypoints.
- `scripts/restore_state.py` — restores the SQLite database from the most recent `job-hunter-state` GitHub Actions artifact before a run, since Actions runners are ephemeral.

SQLite state does not persist on the runner between workflow runs, so the daily workflow restores the previous run's database from an uploaded artifact at the start of each run and re-uploads it at the end (see [Schedule and state persistence](#schedule-and-state-persistence)).

## Required GitHub secrets

Set these under **Settings -> Secrets and variables -> Actions** on your fork/repo:

| Secret | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Gemini API key used for evaluation and cover letter drafting |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Telegram chat id to deliver the digest/PDFs to |
| `CANDIDATE_PROFILE_B64` | Base64-encoded plain-text candidate profile/CV |
| `COVER_LETTER_TEMPLATE_B64` | Base64-encoded plain-text cover letter template |
| `GMAIL_CLIENT_ID` | OAuth client ID used only by the Gmail intelligence sync |
| `GMAIL_CLIENT_SECRET` | OAuth client secret used only by the Gmail intelligence sync |
| `GMAIL_REFRESH_TOKEN` | Refresh token printed by the local Gmail OAuth bootstrap |

Never commit your CV or cover letter template text in plain form. Encode them locally and paste only the base64 output into the GitHub secret:

```bash
base64 -i candidate_profile.txt | tr -d '\n'
base64 -i cover_letter_template.txt | tr -d '\n'
```

(On Linux, `base64 -w0 candidate_profile.txt` produces the same single-line output.)

Paste the resulting string as the secret value. The bot decodes it in memory at runtime; it is never written back to the repo.

## Telegram bot setup

1. In Telegram, message **@BotFather** and send `/newbot`. Follow the prompts to name your bot; BotFather returns a bot token — this is `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot (or add it to a group/channel you want the digest posted to).
3. Find your chat id without exposing the token in git: call `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser or with `curl` locally (substitute your real token only in that local command, never in a committed file), and read the `chat.id` field from the JSON response for your message.
4. Store the bot token and chat id as the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` GitHub secrets above. Do not put either value in `config/search.yml`, `.env`, or any committed file.

## Gemini API key setup

1. Create a free-tier API key at [Google AI Studio](https://aistudio.google.com/).
2. Store it as the `GEMINI_API_KEY` secret.
3. The model used is controlled by the `GEMINI_MODEL` environment variable, defaulting to `gemini-3.6-flash` if unset. Override it (as a repo secret or variable, or in your local `.env`) if you want to point at a different Gemini model.

## Local dry run

Copy `.env.example` to `.env`, fill in `GEMINI_API_KEY`, `CANDIDATE_PROFILE_B64`, and `COVER_LETTER_TEMPLATE_B64`, and set `JOB_HUNTER_DRY_RUN=1` to skip Telegram delivery (Telegram credentials are not required in dry-run mode):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# edit .env with your values, then:
set -a; source .env; set +a
python -m job_hunter run
```

This runs discovery, profile extraction, source-diverse shortlisting, evaluation, and cover letter/PDF generation against the local SQLite database at `var/job_hunter.sqlite3` (override with `JOB_HUNTER_DB_PATH`) without sending anything to Telegram.

## Gmail intelligence setup

Gmail intelligence reads job-related messages into the shared SQLite state before normal job discovery. It uses the Gmail read-only OAuth scope: Gmail is never modified, and full email bodies are not stored. The sync stores only the privacy-minimized message metadata and extracted job/application signals needed by the bot.

Create an OAuth client for the Gmail API, then run the local bootstrap with the client credentials available only in your shell:

```bash
export GMAIL_CLIENT_ID='...'
export GMAIL_CLIENT_SECRET='...'
python scripts/gmail_oauth_bootstrap.py
```

The bootstrap opens the Google consent flow and prints a refresh token. Store that printed value as the GitHub Actions secret `GMAIL_REFRESH_TOKEN`; also add `GMAIL_CLIENT_ID` and `GMAIL_CLIENT_SECRET` as GitHub secrets. Never commit any of these values.

Use the following local commands after loading those Gmail variables and `GEMINI_API_KEY`:

```bash
python -m job_hunter sync-gmail --dry-run
python -m job_hunter sync-gmail --force-backfill
```

`--dry-run` classifies and extracts without advancing the Gmail cursor or persisting Gmail-derived state. `--force-backfill` repeats the 12-month backfill idempotently and is non-destructive. A completed sync with individual message errors keeps its cursor so those messages retry on the next sync; setup, authorization, profile, or listing failures return a nonzero status.

## Manual GitHub Actions dispatch

Go to **Actions -> Daily Job Hunter -> Run workflow** to trigger an on-demand run using the current `main` branch and configured secrets. A manual dispatch always runs the full pipeline (`python -m job_hunter run`, no scheduled-hour gate).

## Schedule and state persistence

The repository workflow exposes `workflow_dispatch` only. Daily execution is expected to come from the configured external scheduler, which dispatches **Daily Job Hunter** at the desired local time; GitHub Actions itself has no cron trigger. Every external or manual dispatch runs the full pipeline directly, including the fail-open Gmail sync followed by `python -m job_hunter run`.

Because GitHub Actions runners are ephemeral, the workflow:
1. Restores `var/job_hunter.sqlite3` from the most recent non-expired `job-hunter-state` artifact (via `scripts/restore_state.py`, using the run's `GITHUB_TOKEN`) before running — silently starting with a fresh database if none exists.
2. Runs the read-only Gmail intelligence sync before the normal job pipeline. This step is fail-open, so Gmail setup or service failures do not prevent the public-source job run.
3. Uploads the resulting `var/job_hunter.sqlite3` as a `job-hunter-state` artifact (90-day retention) after the run, even if a prior step failed partway through (as long as the database file was created).

## Adding ATS board slugs

Edit `config/search.yml`'s `ats` section to add direct board adapters, keyed by ATS provider, with a list of board identifiers:

```yaml
ats:
  ashby: ["acme-inc"]
  lever: ["acme"]
  greenhouse: ["acmeinc"]
```

- `ashby`: the board slug from `https://jobs.ashbyhq.com/<slug>`.
- `lever`: the company slug from `https://jobs.lever.co/<slug>`.
- `greenhouse`: the board token from `https://boards.greenhouse.io/<token>`.

Each configured slug adds one additional source adapter queried on every run, alongside the built-in Remotive, Arbeitnow, and DuckDuckGo-search sources.

## v1 safety boundary

The bot prepares application-ready material — it does not submit anything on your behalf. Out of scope for v1, by design:

- Automated submission to employer application forms.
- Answering legal attestations, visa/work-authorization questions, salary commitments, notice period, or demographic questions.
- CAPTCHA/2FA handling or browser automation.

You remain responsible for reviewing and submitting every application yourself.

## Troubleshooting

### No prior state artifact found

On the very first run (or if the `job-hunter-state` artifact has expired past its 90-day retention, or was deleted), `scripts/restore_state.py` logs that no matching artifact was found and exits normally — the bot proceeds with a fresh, empty database. This is expected on first setup and simply means every discovered job is treated as new.

### Gemini quota / rate limits

The pipeline does not implement a Gemini-quota circuit breaker. Each run uses one compact profile-extraction call, then up to `max_jobs_per_run` (default 35) independent evaluation calls; strong matches can also use a cover-letter call. If quota or rate limits interrupt the run, each affected job fails independently and can be retried on the next run without blocking the rest. If you see repeated Gemini failures in the Actions log, check your API key's quota/rate limit in Google AI Studio.

### Telegram delivery errors

A failed Telegram send (bad token, bot not started, wrong chat id, message too large) is logged and does not crash the run or discard evaluation results. The job stays evaluated and marked undelivered in SQLite, and later runs retry only the missing Telegram deliveries without re-calling Gemini. Retry eligibility follows the same score floor as the digest: only jobs with final score `>60` are retried, and ready-to-apply jobs retry both the digest message and PDF until both succeed. Verify `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are correct and that you've sent at least one message to the bot (see [Telegram bot setup](#telegram-bot-setup)).

### Flaky web sources

Public source APIs and job boards occasionally time out or return errors. Each source adapter fails open — an exception during discovery for one source is logged and skipped, and the run continues with the remaining sources — so a single flaky source does not abort the whole run. Check the Actions log for `discovery failed` warnings to see which source had trouble on a given run.
