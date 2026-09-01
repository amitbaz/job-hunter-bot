# Vercel Root Function Config Fix Design

## Problem

The first production Vercel deployment of `job-hunter-bot` fails before Python starts with:

```text
The pattern "main.py" defined in `functions` doesn't match any Serverless Functions inside the `api` directory.
```

The repository currently has a valid root-level Flask entrypoint in `main.py`, but `vercel.json` also declares `functions.main.py`. That legacy-style `functions` mapping is incompatible with Vercel's current validation because `functions` patterns are expected to target Serverless Functions under `/api`.

## Goal

Make the existing root-level Flask entrypoint deploy successfully on Vercel without moving the webhook into `/api` or changing the Telegram webhook architecture.

## Decision

Keep the current root-level `main.py` Flask entrypoint and remove the invalid `functions` block from `vercel.json`.

Preserve:

- `main.py` and `app = create_app()`;
- `[tool.vercel] entrypoint = "main:app"` in `pyproject.toml`;
- `installCommand = "pip install -e '.[webhook]'"` in `vercel.json`;
- all webhook routes, environment variables, storage abstraction, and Telegram behavior.

The resulting `vercel.json` contains only the schema and install command. Vercel then detects/uses the Python Flask entrypoint without the conflicting legacy function-pattern declaration.

## Scope

Change only deployment configuration, regression coverage, and deployment documentation.

Runtime webhook/domain code must not change.

## Files

- Modify `vercel.json` — remove the invalid `functions` mapping.
- Add `tests/test_vercel_config.py` — lock the supported config shape.
- Modify `docs/telegram-job-navigator.md` — record the root-entrypoint deployment rule and the observed failure mode.

## Regression Test

The test loads `vercel.json` and asserts:

1. `installCommand` remains `pip install -e '.[webhook]'`.
2. The config does not declare a root `main.py` under `functions`.
3. If a `functions` object is introduced later, every pattern must begin with `api/`.

This catches the exact deployment configuration that caused the production build failure before another merge.

## Verification

Success requires all of the following:

- focused Vercel config test passes;
- full repository test suite passes;
- Vercel build proceeds past the previous `unmatched-function-pattern` validation error;
- `/health` responds successfully after a production deployment with the required environment variables configured.

## Non-goals

- No Supabase changes.
- No Telegram callback behavior changes.
- No move from Flask to another framework.
- No move from root `main.py` to `/api`.
- No new Vercel project architecture change.
