# Telegram Vercel Webhook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the Telegram job navigator callback receiver as a Vercel-compatible Flask/Python Function while isolating navigation persistence behind a repository interface that can later switch from GitHub-artifact SQLite to Supabase.

**Architecture:** Keep GitHub Actions and SQLite unchanged. Introduce `NavigationSessionRepository` and a current `GitHubArtifactNavigationRepository`; make the Flask webhook depend only on that interface. Add a small Vercel Flask entrypoint and deployment config. Supabase remains documentation-only in this change.

**Tech Stack:** Python 3.12, Flask, Vercel Python Functions, GitHub Actions artifacts, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-telegram-vercel-webhook-design.md`

## Global Constraints

- Do not migrate Job Hunter Bot state to Supabase in this change.
- Do not add a Supabase client dependency or schema migration.
- Keep GitHub Actions as the scheduled job-search runtime.
- Do not modify the Interviewer App deployment.
- Keep Telegram callback payloads free of credentials.
- Keep artifact SQLite access read-only inside the webhook.
- Preserve existing Telegram navigator copy and behavior.

---

### Task 1: Isolate navigation persistence behind a repository

**Files:**
- Create: `src/job_hunter/navigation_repository.py`
- Create: `tests/test_navigation_repository.py`

**Interfaces:**
- Produces: `NavigationSessionRepository.get_session(session_id: str) -> NavigationSession | None`
- Produces: `GitHubArtifactNavigationRepository.get_session(session_id: str) -> NavigationSession | None`
- Consumes: `GitHubArtifactStateLoader.load_latest()`, `JobStore(..., read_only=True)`, `navigation_store.get_navigation_session()`

- [ ] **Step 1: Write failing repository tests**

Cover these cases:

```python
def test_repository_reads_session_from_latest_artifact(tmp_path): ...
def test_repository_returns_none_when_snapshot_is_missing(): ...
def test_repository_returns_none_when_session_is_missing(tmp_path): ...
def test_repository_opens_snapshot_read_only(tmp_path): ...
def test_repository_propagates_artifact_loader_failure(): ...
```

Use a fake state loader returning `ArtifactStateSnapshot`. Create SQLite fixtures with `JobStore` plus `create_navigation_session`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
pytest tests/test_navigation_repository.py -q
```

Expected: import/definition failures because `navigation_repository.py` does not exist.

- [ ] **Step 3: Implement the repository protocol and GitHub-artifact adapter**

The module should contain:

```python
class NavigationSessionRepository(Protocol):
    def get_session(self, session_id: str) -> NavigationSession | None: ...

class GitHubArtifactNavigationRepository:
    def __init__(self, state_loader):
        self._state_loader = state_loader

    def get_session(self, session_id: str) -> NavigationSession | None:
        snapshot = self._state_loader.load_latest()
        if snapshot is None:
            return None
        with JobStore(snapshot.path, read_only=True) as store:
            return get_navigation_session(store, session_id)
```

Do not catch network/API exceptions here; the HTTP adapter owns user-facing error translation.

- [ ] **Step 4: Run focused tests and verify GREEN**

```bash
pytest tests/test_navigation_repository.py -q
```

Expected: all repository tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/job_hunter/navigation_repository.py tests/test_navigation_repository.py
git commit -m "refactor: isolate Telegram navigation persistence"
```

---

### Task 2: Make the webhook depend on the repository interface

**Files:**
- Modify: `src/job_hunter/telegram_webhook.py`
- Modify: `tests/test_telegram_webhook.py`

**Interfaces:**
- Consumes: `NavigationSessionRepository.get_session(session_id)`
- Produces: `create_app(settings=None, navigation_repository=None, telegram=None) -> Flask`

- [ ] **Step 1: Update webhook tests to inject a fake repository**

Replace fake artifact-loader coupling with:

```python
class FakeNavigationRepository:
    def __init__(self, session=None, error=None): ...
    def get_session(self, session_id): ...
```

Assert:

- navigation callback calls `get_session` once;
- `Apply` does not call the repository;
- no-op callback does not call the repository;
- repository exception produces `Could not load this job list right now.`;
- missing session produces `Job list is still syncing. Try again shortly.`;
- wrong Telegram secret rejects before repository access.

- [ ] **Step 2: Run webhook tests and verify RED**

```bash
pytest tests/test_telegram_webhook.py -q
```

Expected: failures because `create_app` still expects `state_loader` and directly opens SQLite.

- [ ] **Step 3: Refactor `create_app`**

Default construction should be:

```python
state_loader = GitHubArtifactStateLoader(...)
navigation_repository = GitHubArtifactNavigationRepository(state_loader)
```

The route itself should call only:

```python
session = navigation_repository.get_session(session_id)
```

Remove direct `JobStore` and `get_navigation_session` imports from `telegram_webhook.py`.

- [ ] **Step 4: Run focused webhook tests**

```bash
pytest tests/test_telegram_webhook.py tests/test_navigation_repository.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/job_hunter/telegram_webhook.py tests/test_telegram_webhook.py
git commit -m "refactor: decouple Telegram webhook from SQLite artifacts"
```

---

### Task 3: Add the Vercel Python/Flask deployment adapter

**Files:**
- Create: `main.py`
- Create: `vercel.json`
- Create: `tests/test_vercel_entrypoint.py`
- Modify: `pyproject.toml`
- Delete: `Dockerfile.telegram-webhook`

**Interfaces:**
- Produces: root-level `main.app`, a Flask app discoverable by Vercel.
- Consumes: `job_hunter.telegram_webhook.create_app()`.

- [ ] **Step 1: Write a failing import test**

`tests/test_vercel_entrypoint.py` should set all webhook environment variables, import `main`, and assert:

```python
from flask import Flask
assert isinstance(main.app, Flask)
```

Use `monkeypatch` to set:

```text
TELEGRAM_BOT_TOKEN=test-token
TELEGRAM_WEBHOOK_SECRET=test-secret
GITHUB_REPOSITORY=amitbaz/job-hunter-bot
GITHUB_STATE_TOKEN=test-github-token
```

- [ ] **Step 2: Run entrypoint test and verify RED**

```bash
pytest tests/test_vercel_entrypoint.py -q
```

Expected: `ModuleNotFoundError` for `main`.

- [ ] **Step 3: Add `main.py`**

The entrypoint must support the repository's `src/` layout:

```python
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from job_hunter.telegram_webhook import create_app

app = create_app()
```

- [ ] **Step 4: Configure Vercel**

Add to `pyproject.toml`:

```toml
[tool.vercel]
entrypoint = "main:app"
```

Keep Flask in the `webhook` optional dependency. Remove `gunicorn` because Vercel provides the function runtime and the supported deployment path no longer runs a custom WSGI server.

Create `vercel.json`:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "functions": {
    "main.py": {
      "maxDuration": 30,
      "excludeFiles": "{tests/**,.superpowers/**,docs/**,var/**}"
    }
  }
}
```

Delete `Dockerfile.telegram-webhook` to avoid maintaining a second official runtime.

- [ ] **Step 5: Run entrypoint and webhook tests**

```bash
pytest tests/test_vercel_entrypoint.py tests/test_telegram_webhook.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add main.py vercel.json pyproject.toml tests/test_vercel_entrypoint.py
git rm Dockerfile.telegram-webhook
git commit -m "feat: deploy Telegram webhook on Vercel"
```

---

### Task 4: Make Vercel the documented deployment path and record Supabase migration

**Files:**
- Modify: `docs/telegram-job-navigator.md`
- Modify: `.env.example`

**Interfaces:**
- Documents current operations and future migration; no runtime API changes.

- [ ] **Step 1: Update `.env.example` comments/variables**

Retain the existing webhook variables and make clear they are Vercel server-side variables. Do not add Supabase variables.

- [ ] **Step 2: Rewrite the deployment section**

Replace Docker instructions with:

```text
1. Create a separate Vercel project from `amitbaz/job-hunter-bot`.
2. Keep it separate from Interviewer App.
3. Configure the six webhook environment variables.
4. Deploy `main.py` as the Flask entrypoint.
5. Verify GET /health.
6. Register https://<project-domain>/telegram/webhook with scripts/set_telegram_webhook.py.
```

- [ ] **Step 3: Add a durable migration section**

Document four phases:

```text
A. Vercel + GitHub artifact + SQLite (now)
B. Bot begins Supabase writes
C. Webhook swaps to SupabaseNavigationRepository
D. Optional move from Vercel Flask to Supabase Edge Function
```

State explicitly that C does not require changing Telegram callback payloads or card rendering.

- [ ] **Step 4: Commit docs**

```bash
git add .env.example docs/telegram-job-navigator.md
git commit -m "docs: record Vercel webhook and Supabase migration path"
```

---

### Task 5: Full verification

**Files:** none unless tests reveal a defect.

- [ ] **Step 1: Install the supported webhook test environment**

```bash
pip install -e '.[test,webhook]'
```

- [ ] **Step 2: Run the complete test suite**

```bash
pytest -q
```

Expected: all tests pass, including Gmail, pipeline, artifact, navigation, and Vercel entrypoint tests.

- [ ] **Step 3: Verify branch diff**

Confirm:

- no Supabase dependency/schema appears;
- Interviewer App is untouched;
- GitHub Actions daily workflow remains the scheduler;
- Dockerfile is removed;
- Vercel files are present;
- repository abstraction is the only storage dependency used by the Flask route.

- [ ] **Step 4: Verify PR readiness against the existing navigator branch**

Compare `feature/telegram-vercel-webhook` with `feature/telegram-job-navigator-impl` and ensure the diff is limited to this architecture adjustment and its documentation.
