# Job Hunter Bot

A daily, mostly hands-off job-hunting assistant that runs on GitHub Actions. It discovers public remote job postings, deduplicates them in SQLite, evaluates each one against your candidate profile with Gemini, drafts tailored cover letters and renders them as PDFs for strong matches, and delivers a concise digest through Telegram.

The bot **never submits applications**. It prepares material for you to review and send yourself — see [v1 safety boundary](#v1-safety-boundary) below.

## Architecture

```
all public sources (Remotive, Arbeitnow, Remote OK, We Work Remotely, Hacker News, DuckDuckGo, ATS boards)
  -> enrich + dedupe -> profession gate + prefilter -> deterministic global ranking -> top-N Gemini evaluation
  -> cover letter generation + PDF rendering (strong matches only)
  -> Telegram digest + PDF delivery
```

- `src/job_hunter/sources/` — public job discovery adapters. Each source fails open: if one adapter errors, the run continues with the rest.
- `config/search.yml` supports role families, query templates, ATS domains, and `max_search_queries_per_run`; all eligible candidates are ranked globally before `max_jobs_per_run` Gemini calls.
- Only software/product-engineering professions reach Gemini. The default safety ceiling is 75 valid jobs per run; blocked profession phrases take precedence over generic `engineer`/`developer` markers.
- `skip` evaluations are persisted but never sent to Telegram. Telegram sections are ordered by final Gemini score descending, and unknown decisions are omitted.
- `src/job_hunter/prefilter.py` — cheap deterministic filtering before spending Gemini calls.
- `src/job_hunter/evaluation.py` / `gemini.py` — Gemini-based scoring and rationale.
- `src/job_hunter/cover_letter.py` / `pdf.py` — cover letter drafting and PDF rendering for jobs that clear the bar.
- `src/job_hunter/store.py` — SQLite persistence (dedup, evaluation cache, delivery tracking) at `var/job_hunter.sqlite3` by default.
- `src/job_hunter/telegram.py` — outbound-only Telegram Bot API delivery (digest message + PDF documents).
- `src/job_hunter/pipeline.py` / `cli.py` — orchestration and the `python -m job_hunter run` entrypoint.
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

This runs discovery, evaluation, and cover letter/PDF generation against the local SQLite database at `var/job_hunter.sqlite3` (override with `JOB_HUNTER_DB_PATH`) without sending anything to Telegram.

## Manual GitHub Actions dispatch

Go to **Actions -> Daily Job Hunter -> Run workflow** to trigger an on-demand run using the current `main` branch and configured secrets. A manual dispatch always runs the full pipeline (`python -m job_hunter run`, no scheduled-hour gate).

## Schedule and state persistence

The daily workflow runs on two cron triggers, `5 7 * * *` and `5 8 * * *` (UTC), to cover both sides of the `Europe/Berlin` DST transition. Each scheduled run calls `python -m job_hunter run --scheduled`, which only proceeds if the current local hour (in the timezone/hour configured by `timezone`/`scheduled_hour` in `config/search.yml`, default `Europe/Berlin` at `9`) matches; the other cron trigger that day exits immediately without side effects. This means you may see two scheduled workflow runs per day in the Actions log, but only one of them actually executes the pipeline.

Because GitHub Actions runners are ephemeral, the workflow:
1. Restores `var/job_hunter.sqlite3` from the most recent non-expired `job-hunter-state` artifact (via `scripts/restore_state.py`, using the run's `GITHUB_TOKEN`) before running — silently starting with a fresh database if none exists.
2. Uploads the resulting `var/job_hunter.sqlite3` as a `job-hunter-state` artifact (90-day retention) after the run, even if the bot run step failed partway through (as long as the database file was created).

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

The pipeline does not implement a Gemini-quota circuit breaker. If you hit Gemini API quota or rate limits partway through a run, each remaining job's evaluation is still attempted individually and fails independently (the failure is caught per job, so the run completes and other jobs aren't blocked), rather than the run stopping immediately. In the worst case this wastes up to `max_jobs_per_run` (default 25) API calls on a single run, but it is harmless — those jobs simply aren't evaluated and will be retried on the next run. If you see repeated Gemini failures in the Actions log, check your API key's quota/rate limit in Google AI Studio.

### Telegram delivery errors

A failed Telegram send (bad token, bot not started, wrong chat id, message too large) is logged and does not crash the run or discard evaluation results — the job stays evaluated and marked undelivered in SQLite, and delivery is not automatically retried. Verify `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are correct and that you've sent at least one message to the bot (see [Telegram bot setup](#telegram-bot-setup)). Note that such a job is not resurfaced in a later digest either: since it was already successfully evaluated, it won't be re-evaluated on subsequent runs, so a job that fails delivery this way stays undelivered until you intervene manually. This is a known v1 limitation.

### Flaky web sources

Public source APIs and job boards occasionally time out or return errors. Each source adapter fails open — an exception during discovery for one source is logged and skipped, and the run continues with the remaining sources — so a single flaky source does not abort the whole run. Check the Actions log for `discovery failed` warnings to see which source had trouble on a given run.
