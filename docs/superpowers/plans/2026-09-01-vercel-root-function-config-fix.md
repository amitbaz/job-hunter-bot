# Vercel Root Function Config Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the invalid Vercel root `main.py` function mapping while preserving the existing root-level Flask entrypoint and webhook install command.

**Architecture:** Keep `main.py` as the Vercel Flask entrypoint and rely on Vercel's Python app detection/entrypoint configuration. Remove only the conflicting `functions.main.py` declaration from `vercel.json`; runtime webhook logic remains unchanged.

**Tech Stack:** Python 3.12, Flask 3.1, pytest, Vercel Python Functions

**Spec:** `docs/superpowers/specs/2026-09-01-vercel-root-function-config-fix-design.md`

## Global Constraints

- Do not change Telegram callback behavior.
- Do not change SQLite/GitHub artifact persistence.
- Do not add Supabase runtime dependencies.
- Keep `main.py` as the root Flask entrypoint.
- Keep `pip install -e '.[webhook]'` as the Vercel install command.

---

### Task 1: Lock the deployment configuration regression

**Files:**
- Create: `tests/test_vercel_config.py`

**Interfaces:**
- Consumes: repository-root `vercel.json`
- Produces: regression coverage for Vercel function-pattern compatibility

- [ ] **Step 1: Write the failing config regression test**

Create:

```python
import json
from pathlib import Path


def test_vercel_config_does_not_map_root_main_as_legacy_function():
    config = json.loads(Path("vercel.json").read_text())

    assert config["installCommand"] == "pip install -e '.[webhook]'"

    functions = config.get("functions", {})
    assert "main.py" not in functions
    assert all(pattern.startswith("api/") for pattern in functions)
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
pytest -q tests/test_vercel_config.py
```

Expected: FAIL because current `vercel.json` contains `functions["main.py"]`.

- [ ] **Step 3: Commit the regression test**

```bash
git add tests/test_vercel_config.py
git commit -m "test: guard Vercel root function config"
```

---

### Task 2: Remove the invalid Vercel function pattern

**Files:**
- Modify: `vercel.json`
- Test: `tests/test_vercel_config.py`

**Interfaces:**
- Preserves: `installCommand = "pip install -e '.[webhook]'"`
- Removes: `functions.main.py`

- [ ] **Step 1: Replace `vercel.json` with the minimal supported config**

Use exactly:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "installCommand": "pip install -e '.[webhook]'"
}
```

- [ ] **Step 2: Run the focused test and confirm GREEN**

Run:

```bash
pytest -q tests/test_vercel_config.py tests/test_vercel_entrypoint.py
```

Expected: PASS.

- [ ] **Step 3: Run the full suite**

Run:

```bash
pytest -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 4: Commit the config fix**

```bash
git add vercel.json
git commit -m "fix: remove invalid Vercel function pattern"
```

---

### Task 3: Document the deployment rule and verify Vercel

**Files:**
- Modify: `docs/telegram-job-navigator.md`

**Interfaces:**
- Documents: root-level `main.py` deployment and `functions` restriction

- [ ] **Step 1: Add a troubleshooting note**

Document that the webhook uses root `main.py` as the Flask entrypoint and that `vercel.json` must not declare `functions.main.py`; Vercel's `functions` patterns are reserved for `/api` functions and trigger `unmatched-function-pattern` when pointed at root `main.py`.

- [ ] **Step 2: Commit documentation**

```bash
git add docs/telegram-job-navigator.md
git commit -m "docs: clarify Vercel Flask entrypoint config"
```

- [ ] **Step 3: Verify branch CI**

Confirm GitHub Actions installs `.[test,webhook]` and `pytest -q` passes on the final branch head.

- [ ] **Step 4: Verify deployment behavior**

After the branch/PR is deployed by Vercel, confirm the build proceeds past the previous `unmatched-function-pattern` error. After merge to production, confirm `GET /health` returns HTTP 200 with `{"ok": true}`.
