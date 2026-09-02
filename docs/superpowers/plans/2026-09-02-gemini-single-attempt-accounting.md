# Gemini Single-Attempt Accounting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure each logical Gemini call makes at most one provider HTTP attempt and that HTTP/network failures are counted once in Gemini usage accounting.

**Architecture:** Keep the generic `HttpClient` retry defaults for the rest of the bot, but add an explicit per-request switch to disable exception retries. `GeminiClient` will opt out of both status-code retries and exception retries, and will record request exceptions as failed Gemini attempts before re-raising them.

**Tech Stack:** Python 3.12, requests, pytest

**Spec:** `docs/superpowers/specs/2026-09-02-gemini-single-attempt-accounting-design.md`

## Global Constraints

- Gemini free-tier production limits are configured externally; this fix must not hard-code provider quota values.
- Existing non-Gemini HTTP retry behavior must remain unchanged.
- Existing Gemini 429 circuit-breaker behavior must remain unchanged.
- Use TDD: failing regression tests first, then the minimal implementation.

---

### Task 1: Disable hidden Gemini retries and account for request failures

**Files:**
- Modify: `src/job_hunter/http.py`
- Modify: `src/job_hunter/gemini.py`
- Test: `tests/test_gemini.py`
- Test: `tests/test_http.py`

**Interfaces:**
- `HttpClient.post(..., retry_exceptions: bool = True, retry_status_codes: set[int] | None = None)` preserves current behavior by default.
- `GeminiClient.generate_text(...)` calls `HttpClient.post` with `retry_status_codes=set()` and `retry_exceptions=False`.
- `GeminiUsageTracker.record_error(...)` remains the usage-ledger entry point for non-429 failed attempts.

- [ ] **Step 1: Write failing Gemini regression tests**

Add tests proving a Gemini request opts out of status retries and exception retries, and that a request exception is recorded once before being re-raised.

- [ ] **Step 2: Run targeted Gemini tests and verify RED**

Run: `pytest tests/test_gemini.py -q`

Expected: FAIL because `retry_exceptions` is not supported/passed yet and request exceptions are not recorded by `GeminiClient`.

- [ ] **Step 3: Add an HttpClient regression test**

Add a test proving the new `retry_exceptions=False` option performs one request attempt while the default path still retries request exceptions.

- [ ] **Step 4: Run targeted HTTP tests and verify RED**

Run: `pytest tests/test_http.py -q`

Expected: FAIL because `HttpClient` does not yet expose the `retry_exceptions` option.

- [ ] **Step 5: Implement the minimal retry opt-out**

Update `HttpClient.get/post/_request` to accept `retry_exceptions: bool = True`; when false, immediately re-raise a `requests.RequestException` instead of entering another attempt. In `GeminiClient`, pass an empty retry-status set and `retry_exceptions=False`; wrap the post call so request exceptions are recorded through `record_error` once and then re-raised.

- [ ] **Step 6: Run targeted tests and verify GREEN**

Run: `pytest tests/test_gemini.py tests/test_http.py -q`

Expected: PASS.

- [ ] **Step 7: Run full suite**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 8: Commit**

Commit the regression tests and minimal implementation on `fix/gemini-single-attempt-accounting`.
