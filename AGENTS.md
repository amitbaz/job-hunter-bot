# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, etc.) when working with code in this repository.

## What this is

A daily, mostly hands-off job-hunting assistant that runs on GitHub Actions. It discovers public remote job postings, deduplicates them in SQLite, evaluates each against a candidate profile with Gemini, drafts tailored cover letters + PDFs for strong matches, and delivers a digest via Telegram. **It never submits applications** — see "v1 safety boundary" in README.md.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'

pytest -q                          # run full test suite
pytest tests/test_pipeline.py -q   # single file
pytest tests/test_pipeline.py::test_name -q  # single test

python -m job_hunter run                       # full pipeline run
python -m job_hunter run --scheduled           # only runs at config/search.yml's scheduled_hour
python -m job_hunter run --config path/to.yml  # alternate config
```

Local dry run (skips Telegram, no Telegram creds needed): copy `.env.example` to `.env`, fill in `GEMINI_API_KEY`, `CANDIDATE_PROFILE_B64`, `COVER_LETTER_TEMPLATE_B64`, set `JOB_HUNTER_DRY_RUN=1`, then `set -a; source .env; set +a` before running. `JOB_HUNTER_DRY_RUN` truthy values are `1/true/yes` (case-insensitive); anything else is treated as unset/false.

CI (`.github/workflows/ci.yml`) runs `pytest -q` on Python 3.12 for every push/PR — no lint step configured.

## Architecture

Pipeline, in `pipeline.py::run_pipeline`, one source at a time:

```
sources (Remotive, Arbeitnow, DuckDuckGo search, Ashby/Lever/Greenhouse boards)
  -> normalize + dedupe (SQLite, store.py)
  -> prefilter (deterministic hard blockers: title, remote-only — prefilter.py)
  -> Gemini evaluation (scored fit against candidate profile + policy — evaluation.py/gemini.py)
  -> cover letter generation + PDF rendering, strong matches only (cover_letter.py/pdf.py)
  -> Telegram digest + PDF delivery (telegram.py)
```

Key modules:
- `src/job_hunter/sources/` — one adapter per job source, all implementing a common `discover()` interface (`base.py`). Each source **fails open**: an exception during discovery is caught in `run_pipeline`, logged, and that source is skipped — the rest of the run continues.
- `src/job_hunter/store.py` — SQLite persistence: job dedup (`upsert_job`), re-evaluation gating (`needs_evaluation` — a job is only re-evaluated if it hasn't been evaluated before or its description changed), evaluation caching, delivery tracking (`mark_delivered`). DB path defaults to `var/job_hunter.sqlite3`, overridable via `JOB_HUNTER_DB_PATH`.
- `src/job_hunter/config.py` — loads `config/search.yml` + required env vars into a `Settings`/`SearchPolicy` (see `models.py`). Candidate profile and cover letter template are base64-encoded secrets (`CANDIDATE_PROFILE_B64`, `COVER_LETTER_TEMPLATE_B64`), decoded in memory only — never write decoded plaintext to the repo or logs.
- `src/job_hunter/cli.py` — `python -m job_hunter run` entrypoint. `--scheduled` gates execution on `should_run_scheduled` (pipeline.py), comparing current local hour in `settings.timezone` against `settings.scheduled_hour`.
- Per-job evaluation and cover-letter/PDF generation failures are caught individually inside the loop (not fail-open at the run level) so one bad job doesn't abort the run; each increments `summary.errors`.
- Only jobs with decision `high_priority` or `package_match` (`pipeline.py::_READY_DECISIONS`) get a cover letter + PDF generated and a `possible_match` decision only counts toward the digest, not material generation.

## GitHub Actions state persistence

Actions runners are ephemeral — `var/job_hunter.sqlite3` does not persist between runs on its own. `.github/workflows/daily.yml`:
1. Restores the DB from the most recent `job-hunter-state` artifact via `scripts/restore_state.py` before running (silently starts fresh if none exists/expired).
2. Re-uploads the resulting DB as `job-hunter-state` (90-day retention) after the run, even on failure, as long as the DB file exists.

The daily workflow fires on two cron triggers (`5 7 * * *` and `5 8 * * *` UTC) to cover both sides of the `Europe/Berlin` DST transition; `--scheduled` makes only one of them actually run the pipeline on any given day.

## Required secrets/env

`GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CANDIDATE_PROFILE_B64`, `COVER_LETTER_TEMPLATE_B64` — see README.md for setup. In dry-run mode, Telegram vars are optional.
