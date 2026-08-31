# Job Hunter Bot R2 — Automated Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the standalone Job Hunter Bot's automated discovery coverage with a self-expanding company watchlist, canonical employer/ATS resolution, cross-source provenance/deduplication, and a small curated specialist-source layer while preserving the existing SQLite-first, fail-open pipeline.

**Architecture:** Keep all discovered jobs on one pipeline. Existing sources, Gmail-staged jobs, a new YC source, targeted specialist-domain search, and watched-company checks produce normal `Job` candidates; a shared identity/resolution layer attempts to resolve them to employer/ATS postings, persists source provenance, and merges duplicates before the existing profession gate, ranking, Gemini evaluation, material generation, and Telegram delivery. Strong final evaluations (`high_priority` or `package_match`) may promote their companies into a persistent watchlist, whose endpoints are checked on future runs.

**Tech Stack:** Python 3.12, SQLite, `requests`, `beautifulsoup4`, existing DuckDuckGo search adapter, existing Ashby/Lever/Greenhouse adapters, Gemini REST client, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-31-job-hunter-bot-r2-automated-discovery-design.md`

## Global Constraints

- R2 remains SQLite-first; do not introduce Supabase/Postgres or Relay dependencies.
- User-driven/Telegram job-URL ingestion remains deferred.
- No logged-in LinkedIn, Wellfound, Welcome to the Jungle, or other authenticated scraping.
- No application submission, CAPTCHA/2FA automation, work-authorization attestations, salary commitments, or demographic answers.
- Canonical resolution is aggressive but best-effort: resolution failure must keep the original usable job candidate.
- Fuzzy similarity alone must never automatically merge jobs.
- All source/watch/resolution failures fail open independently.
- Existing R1 Gmail lifecycle tracking and review behavior must continue unchanged.
- Automatic watch promotion occurs only after a final evaluation decision of `high_priority` or `package_match`; `possible_match`, `skip`, and `blocked` never auto-promote.
- Manual watch entries are persistent and must never be automatically deleted or permanently disabled.
- Watch health uses a deterministic backoff: after 3 consecutive failed checks, pause an automatic or manual entry for 24 hours; any successful verification/check resets failures and clears the pause. Pausing never deletes an entry.
- Prefer supported structured ATS targets (Ashby, Lever, Greenhouse) over generic careers URLs; never downgrade a known-good structured endpoint to a weaker guess.
- Preserve every trustworthy source URL/source ID as provenance even after a canonical employer URL is found.
- Full evaluation/material/application history must remain attached to the surviving logical job when duplicates collapse.
- Specialist-source mechanics for R2 are fixed as: YC public pages via a dedicated source adapter; Wellfound, Welcome to the Jungle, VC portfolio boards, and smaller specialist boards via targeted public search. Do not add brittle dedicated scrapers for them in R2.

---

## File Structure

### New production files

- `src/job_hunter/job_identity.py` — source-independent company/title/location normalization and strong identity keys used by canonical resolution, persistence, and dedupe.
- `src/job_hunter/canonical.py` — canonical employer/ATS resolver, confidence model, supported ATS URL parsing, redirect/embedded-link handling, and targeted resolution search.
- `src/job_hunter/watchlist.py` — company promotion rules, watch target upgrades, health/backoff policy, and conversion of watch entries into discovery sources.
- `src/job_hunter/sources/company_watch.py` — one fail-open `JobSource` that checks active watch entries through supported ATS adapters or public careers-page extraction.
- `src/job_hunter/sources/yc.py` — public YC jobs adapter.

### Modified production files

- `src/job_hunter/models.py` — extend `Job` with optional provenance/canonical fields needed during discovery and add small watch/canonical result dataclasses.
- `src/job_hunter/config.py` — load manual company-watch seeds and specialist search domains.
- `config/search.yml` — add R2 specialist-domain search configuration and optional manual watch seeds.
- `src/job_hunter/discovery_queries.py` — generate targeted Wellfound/Welcome to the Jungle/VC specialist queries without consuming the existing role-search semantics incorrectly.
- `src/job_hunter/sources/__init__.py` — register YC and allow watched-company source construction after the store exists.
- `src/job_hunter/discovery.py` — resolve canonical identity before final in-run dedupe/upsert; persist provenance; collect resolution/dedupe statistics.
- `src/job_hunter/store.py` — backward-compatible schema migration for `canonical_url`, `job_sources`, and `company_watch`; provenance, merge, watch, and health operations.
- `src/job_hunter/pipeline.py` — add watched-company discovery; promote strong evaluated companies; log R2 metrics.
- `src/job_hunter/fetching.py` — expose reusable public-page metadata/link extraction helpers if needed by canonical/company-watch code without duplicating fetch logic.
- `README.md` — document R2 automated discovery, manual watch seeds, source mechanics, and health behavior.

### New tests

- `tests/test_job_identity.py`
- `tests/test_canonical.py`
- `tests/test_watchlist.py`
- `tests/test_company_watch_source.py`
- `tests/test_yc_source.py`

### Existing tests to extend

- `tests/test_store.py`
- `tests/test_config.py`
- `tests/test_discovery_queries.py`
- `tests/test_discovery.py`
- `tests/test_pipeline.py`
- `tests/test_sources.py` or the repository's existing per-source test files, following current convention.

---

### Task 1: Define Source-Independent Job and Company Identity

**Files:**
- Create: `src/job_hunter/job_identity.py`
- Modify: `src/job_hunter/models.py`
- Test: `tests/test_job_identity.py`

**Interfaces:**
- Produces: `normalize_company_name(value: str) -> str`
- Produces: `normalize_job_title(value: str) -> str`
- Produces: `normalize_location(value: str) -> str`
- Produces: `company_identity_key(company: str) -> str`
- Produces: `job_fallback_identity(company: str, title: str, location: str) -> str | None`
- Produces: `locations_compatible(left: str, right: str) -> bool`
- Extends `Job` with `original_url: str = ""`, `canonical_url: str = ""`, `ats_provider: str | None = None`, and `ats_job_id: str | None = None`.

- [ ] **Step 1: Write failing normalization tests**

```python
from job_hunter.job_identity import (
    company_identity_key,
    job_fallback_identity,
    locations_compatible,
    normalize_company_name,
)


def test_company_identity_ignores_safe_legal_suffix_and_punctuation():
    assert normalize_company_name("Acme GmbH") == "acme"
    assert normalize_company_name("ACME, GmbH") == "acme"
    assert company_identity_key("Acme GmbH") == company_identity_key("ACME")


def test_company_identity_does_not_remove_meaningful_words():
    assert company_identity_key("Meta Platforms") != company_identity_key("Meta")


def test_fallback_identity_requires_company_and_title():
    assert job_fallback_identity("Acme", "Senior Frontend Engineer", "Berlin")
    assert job_fallback_identity("", "Senior Frontend Engineer", "Berlin") is None


def test_locations_are_compatible_when_one_side_is_unspecified():
    assert locations_compatible("", "Berlin") is True
    assert locations_compatible("Berlin, Germany", "Berlin") is True
    assert locations_compatible("Berlin", "New York") is False
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
pytest tests/test_job_identity.py -v
```

Expected: FAIL because `job_hunter.job_identity` does not exist.

- [ ] **Step 3: Implement minimal normalization helpers**

Use conservative transformations only: lowercase, trim/collapse whitespace, strip punctuation, and remove a small explicit trailing legal-suffix set (`gmbh`, `ag`, `ltd`, `limited`, `inc`, `incorporated`, `llc`, `corp`, `corporation`) only when it appears as a suffix token. Do not use fuzzy matching here.

```python
_SAFE_LEGAL_SUFFIXES = {
    "gmbh", "ag", "ltd", "limited", "inc", "incorporated",
    "llc", "corp", "corporation",
}


def normalize_company_name(value: str) -> str:
    tokens = _tokenize(value)
    while tokens and tokens[-1] in _SAFE_LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)
```

Implement `job_fallback_identity()` as the normalized `company|title|location` triple only when company and title are non-empty.

- [ ] **Step 4: Extend `Job` without breaking existing callers**

Append optional/defaulted fields to the dataclass so existing positional/keyword construction remains valid:

```python
@dataclass(slots=True)
class Job:
    source: str
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    source_job_id: str | None = None
    remote: bool | None = None
    original_url: str = ""
    canonical_url: str = ""
    ats_provider: str | None = None
    ats_job_id: str | None = None
```

- [ ] **Step 5: Run focused and model-dependent tests**

```bash
pytest tests/test_job_identity.py tests/test_normalize.py tests/test_discovery.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/job_hunter/job_identity.py src/job_hunter/models.py tests/test_job_identity.py
git commit -m "feat: add source-independent job identity"
```

---

### Task 2: Add Backward-Compatible Provenance and Watch Persistence

**Files:**
- Modify: `src/job_hunter/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `JobStore.record_job_source(job_id: int, *, source: str, source_job_id: str | None, source_url: str) -> None`
- Produces: `JobStore.list_job_sources(job_id: int) -> list[sqlite3.Row]`
- Produces: `JobStore.find_job_by_canonical_url(url: str) -> int | None`
- Produces: `JobStore.find_job_by_ats(provider: str, ats_job_id: str) -> int | None`
- Produces: `JobStore.find_job_by_identity(company: str, title: str, location: str) -> int | None`
- Produces: `JobStore.merge_jobs(survivor_id: int, duplicate_id: int) -> int`
- Produces watch CRUD/query methods used in Tasks 6–8.

- [ ] **Step 1: Write failing schema-upgrade tests against a legacy database**

Create a SQLite database containing the current R1 `jobs` schema only, then instantiate `JobStore` and assert the R2 structures appear without data loss:

```python
def test_r2_schema_upgrades_legacy_jobs_table(tmp_path):
    db = tmp_path / "state.sqlite3"
    create_r1_schema_and_one_job(db)

    store = JobStore(db)

    columns = store._conn.execute("PRAGMA table_info(jobs)").fetchall()
    assert "canonical_url" in {row["name"] for row in columns}
    assert store.count_jobs() == 1
    assert store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='job_sources'"
    ).fetchone()
    assert store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='company_watch'"
    ).fetchone()
```

- [ ] **Step 2: Run the legacy schema test**

```bash
pytest tests/test_store.py::test_r2_schema_upgrades_legacy_jobs_table -v
```

Expected: FAIL because the R2 tables/column do not exist.

- [ ] **Step 3: Add idempotent schema migration**

Add:

```sql
CREATE TABLE IF NOT EXISTS job_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_job_id TEXT,
    source_url TEXT NOT NULL DEFAULT '',
    identity_key TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(job_id, identity_key)
);

CREATE TABLE IF NOT EXISTS company_watch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    normalized_company_name TEXT NOT NULL UNIQUE,
    careers_url TEXT NOT NULL DEFAULT '',
    ats_provider TEXT,
    ats_identifier TEXT,
    discovered_from_job_id INTEGER REFERENCES jobs(id),
    promotion_source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    paused_until TEXT,
    first_seen_at TEXT NOT NULL,
    last_verified_at TEXT,
    last_successful_check_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Add `canonical_url TEXT NOT NULL DEFAULT ''` to legacy `jobs` tables only when absent, using `PRAGMA table_info(jobs)` before `ALTER TABLE`.

- [ ] **Step 4: Add provenance idempotency tests**

```python
def test_record_job_source_is_idempotent_and_refreshes_last_seen(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job_id, *_ = store.upsert_job(Job(source="yc", title="Frontend", company="Acme", url="https://yc.example/job"))

    store.record_job_source(job_id, source="yc", source_job_id="123", source_url="https://yc.example/job")
    store.record_job_source(job_id, source="yc", source_job_id="123", source_url="https://yc.example/job")

    rows = store.list_job_sources(job_id)
    assert len(rows) == 1
    assert rows[0]["source"] == "yc"
```

- [ ] **Step 5: Add merge-preservation tests before merge implementation**

Create two duplicate jobs and attach an evaluation/material/delivery/application event to the duplicate plus different `job_sources` to both. Assert after `merge_jobs(primary, duplicate)`:

```python
assert store.get_job(duplicate_id) is None
assert len(store.list_job_sources(primary_id)) == 2
assert store.get_evaluation(primary_id) is not None
assert store.get_material(primary_id) is not None
assert store.current_application_state(primary_id) == "INTERVIEW"
```

- [ ] **Step 6: Implement lookup/provenance/merge operations transactionally**

`merge_jobs()` must run in one transaction and re-parent, in this order, rows from:

```text
job_sources
application_events
materials
deliveries
evaluations
company_watch.discovered_from_job_id
```

Before re-parenting `job_sources`, insert/upsert into the survivor to satisfy uniqueness; then delete duplicate provenance rows. Preserve the survivor row, enrich its blank/weaker fields from the duplicate, and delete the duplicate job last.

Do not merge if `survivor_id == duplicate_id`.

- [ ] **Step 7: Run store tests**

```bash
pytest tests/test_store.py -v
```

Expected: PASS, including existing R1 Gmail persistence tests.

- [ ] **Step 8: Commit**

```bash
git add src/job_hunter/store.py tests/test_store.py
git commit -m "feat: persist job provenance and company watches"
```

---

### Task 3: Implement Supported ATS Parsing and Canonical Resolution

**Files:**
- Create: `src/job_hunter/canonical.py`
- Modify: `src/job_hunter/fetching.py`
- Test: `tests/test_canonical.py`

**Interfaces:**
- Produces: `CanonicalResolution(url: str, provider: str | None, job_id: str | None, confidence: float, method: str)`
- Produces: `parse_supported_ats_url(url: str) -> tuple[str, str | None] | None`
- Produces: `CanonicalResolver(http, search_source=None, watch_lookup=None)`
- Produces: `CanonicalResolver.resolve(job: Job) -> CanonicalResolution | None`

- [ ] **Step 1: Write failing ATS parsing tests**

```python
from job_hunter.canonical import parse_supported_ats_url


def test_parse_supported_ats_urls():
    assert parse_supported_ats_url("https://jobs.lever.co/acme/abc-123") == ("lever", "abc-123")
    assert parse_supported_ats_url("https://jobs.ashbyhq.com/acme/xyz") == ("ashby", "xyz")
    assert parse_supported_ats_url("https://boards.greenhouse.io/acme/jobs/456") == ("greenhouse", "456")
```

- [ ] **Step 2: Write failing resolution-order tests**

Use a fake HTTP client and fake targeted-search callback. Cover:

```python
def test_direct_ats_url_wins_without_search(): ...
def test_redirect_to_ats_is_accepted(): ...
def test_embedded_employer_link_is_used_when_company_and_title_match(): ...
def test_targeted_search_rejects_wrong_company(): ...
def test_targeted_search_rejects_incompatible_title(): ...
def test_resolution_exception_returns_none(): ...
```

The low-confidence/failure expectation is always `None`, leaving the caller's original URL untouched.

- [ ] **Step 3: Run canonical tests**

```bash
pytest tests/test_canonical.py -v
```

Expected: FAIL because the resolver does not exist.

- [ ] **Step 4: Add reusable public-page metadata extraction**

In `fetching.py`, expose a helper that can inspect fetched HTML for:

- `<link rel="canonical" href="...">`
- JSON-LD `JobPosting` URL fields
- outbound links to supported ATS domains

Keep the current enrichment behavior unchanged.

- [ ] **Step 5: Implement deterministic confidence scoring**

Use explicit confidence bands rather than an LLM call:

```text
1.00 direct supported ATS URL already on job
0.98 HTTP redirect ending on supported ATS URL
0.95 structured canonical/JSON-LD URL on supported ATS/employer domain with compatible company/title
0.92 known watch ATS target + exact/near-exact normalized title match
0.90 targeted search result with company identity match + normalized title equality + compatible location
<0.90 unresolved; do not replace URL
```

The resolver returns only candidates at `>= 0.90`.

- [ ] **Step 6: Implement targeted search as an injected callback**

Do not couple `canonical.py` to DuckDuckGo internals. The resolver accepts a callable like:

```python
SearchCanonical = Callable[[Job], list[Job]]
```

and evaluates returned public search candidates using the same company/title/location rules.

- [ ] **Step 7: Run canonical and fetching tests**

```bash
pytest tests/test_canonical.py tests/test_fetching.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/job_hunter/canonical.py src/job_hunter/fetching.py tests/test_canonical.py tests/test_fetching.py
git commit -m "feat: resolve canonical employer job postings"
```

---

### Task 4: Replace Fingerprint-Only Upsert With Cross-Source Logical Job Upsert

**Files:**
- Modify: `src/job_hunter/store.py`
- Modify: `src/job_hunter/discovery.py`
- Test: `tests/test_store.py`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: `CanonicalResolver.resolve(job)`
- Produces: `JobStore.upsert_logical_job(job: Job) -> tuple[int, bool, bool, bool]` where the fourth value is `merged_existing_duplicate`.
- Produces additional `DiscoveryStats.canonical_resolved`, `canonical_unresolved`, and `cross_source_duplicates` counters.

- [ ] **Step 1: Write failing cross-source upsert tests**

```python
def test_same_canonical_url_from_different_sources_is_one_job(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    a = Job(source="gmail:linkedin", title="Senior Frontend Engineer", company="Acme", url="https://linkedin.example/1", canonical_url="https://jobs.lever.co/acme/abc", ats_provider="lever", ats_job_id="abc")
    b = Job(source="yc", title="Senior Frontend Engineer", company="Acme GmbH", url="https://yc.example/2", canonical_url="https://jobs.lever.co/acme/abc", ats_provider="lever", ats_job_id="abc")

    first_id, *_ = store.upsert_logical_job(a)
    second_id, *_ = store.upsert_logical_job(b)

    assert first_id == second_id
    assert len(store.list_job_sources(first_id)) == 2
```

Also test ATS ID match with differing canonical URL tracking params and exact company/title/location fallback.

- [ ] **Step 2: Add explicit false-merge tests**

```python
def test_similar_titles_at_same_company_do_not_merge_without_strong_identity(tmp_path):
    # "Senior Frontend Engineer" and "Staff Frontend Engineer" stay separate.
    ...


def test_same_title_at_different_companies_never_merges(tmp_path): ...
```

- [ ] **Step 3: Run focused tests and verify failure**

```bash
pytest tests/test_store.py -k "logical_job or canonical or merge" -v
```

Expected: FAIL because `upsert_logical_job` is missing.

- [ ] **Step 4: Implement identity lookup precedence**

Inside `upsert_logical_job()`:

```text
1. canonical_url
2. ats_provider + ats_job_id
3. strong company/title/location fallback
4. existing fingerprint fallback for backward compatibility
5. insert new row
```

Set `jobs.url` to canonical URL when present, otherwise original URL. Set `jobs.canonical_url` only when resolved. Always call `record_job_source()` with the original source URL.

Do not use fuzzy title similarity to merge.

- [ ] **Step 5: Integrate canonical resolution before final in-run dedupe**

In `collect_candidates()`, resolve each raw candidate before `_dedupe()`. On success:

```python
job.original_url = job.original_url or job.url
job.canonical_url = resolution.url
job.url = resolution.url
job.ats_provider = resolution.provider
job.ats_job_id = resolution.job_id
```

On failure, preserve `job.url` and increment `canonical_unresolved` only for candidates that had a usable original URL.

- [ ] **Step 6: Make in-run dedupe use strong cross-source keys**

Update `_dedupe()` to union candidates when any of these exact keys match:

```text
canonical URL
ATS provider + job ID
fallback employer/title/location identity
```

Keep the existing richness merge behavior, but preserve original/provenance metadata from each raw candidate by passing each candidate to store provenance after the logical upsert.

- [ ] **Step 7: Add end-to-end discovery collapse test**

Provide three fake sources representing Gmail/YC/ATS copies of the same job and a fake resolver returning the same Lever URL. Assert:

```python
assert result.stats.raw == 3
assert result.stats.unique == 1
assert result.stats.cross_source_duplicates == 2
assert store.count_jobs() == 1
assert {r["source"] for r in store.list_job_sources(result.eligible[0][0])} == {
    "gmail:linkedin", "yc", "lever"
}
```

- [ ] **Step 8: Run discovery/store regression suite**

```bash
pytest tests/test_store.py tests/test_discovery.py tests/test_gmail_staged_source.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/job_hunter/store.py src/job_hunter/discovery.py tests/test_store.py tests/test_discovery.py
git commit -m "feat: dedupe jobs across discovery sources"
```

---

### Task 5: Add Curated Specialist Search Domains and YC Public Source

**Files:**
- Create: `src/job_hunter/sources/yc.py`
- Modify: `src/job_hunter/sources/__init__.py`
- Modify: `src/job_hunter/models.py`
- Modify: `src/job_hunter/config.py`
- Modify: `src/job_hunter/discovery_queries.py`
- Modify: `config/search.yml`
- Test: `tests/test_yc_source.py`
- Test: `tests/test_config.py`
- Test: `tests/test_discovery_queries.py`

**Interfaces:**
- Produces: `YCSource(http, urls: list[str])`
- Extends `SearchPolicy` with `specialist_search_domains: list[str]`, `specialist_query_templates: list[str]`, and `yc_job_pages: list[str]`.

- [ ] **Step 1: Write failing config/query tests**

```python
def test_specialist_queries_are_generated_separately_from_ats_queries():
    policy = make_policy(
        role_families=["senior frontend engineer"],
        specialist_search_domains=["wellfound.com", "app.welcometothejungle.com"],
        specialist_query_templates=['"{role}" remote Europe'],
    )
    queries = generate_search_queries(policy)
    assert 'site:wellfound.com "senior frontend engineer" remote Europe' in queries
    assert 'site:app.welcometothejungle.com "senior frontend engineer" remote Europe' in queries
```

Ensure the global `max_search_queries_per_run` cap still applies after dedupe.

- [ ] **Step 2: Add R2 config values**

Use:

```yaml
specialist_search_domains:
  - wellfound.com
  - app.welcometothejungle.com
specialist_query_templates:
  - '"{role}" remote Europe'
  - '"{role}" Berlin'
yc_job_pages:
  - https://www.ycombinator.com/jobs/role
  - https://www.ycombinator.com/jobs/location/berlin
manual_company_watch: []
```

VC portfolio domains are user-extensible by adding them to `specialist_search_domains`; do not hard-code a speculative VC catalog.

- [ ] **Step 3: Write YC fixture tests**

Build a minimal saved HTML fixture matching public YC page structure and assert extraction of company, title, location, URL, and stable source ID where the page exposes one.

```python
def test_yc_source_extracts_public_jobs(http_stub):
    jobs = YCSource(http_stub, ["https://www.ycombinator.com/jobs/role"]).discover()
    assert jobs[0].source == "yc"
    assert jobs[0].company == "Acme"
    assert jobs[0].title == "Senior Product Engineer"
```

Also test malformed HTML and HTTP failure return `[]`/partial successes rather than raising.

- [ ] **Step 4: Implement YC adapter using public HTML only**

Use BeautifulSoup, no login/session impersonation, and no internal/private API assumptions. Parse job links and nearby company/title/location text defensively. Each configured YC page fails independently.

- [ ] **Step 5: Register YC in `build_sources()`**

Append `YCSource(http, settings.policy.yc_job_pages)` when pages are configured. Wellfound and Welcome to the Jungle remain DuckDuckGo/domain-search inputs rather than dedicated source classes.

- [ ] **Step 6: Run source/config/query tests**

```bash
pytest tests/test_yc_source.py tests/test_config.py tests/test_discovery_queries.py tests/test_sources.py -v
```

If `tests/test_sources.py` does not exist, run the repository's per-source tests instead; do not create an unrelated aggregate test file solely for this command.

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/job_hunter/sources/yc.py src/job_hunter/sources/__init__.py src/job_hunter/models.py src/job_hunter/config.py src/job_hunter/discovery_queries.py config/search.yml tests/test_yc_source.py tests/test_config.py tests/test_discovery_queries.py
git commit -m "feat: expand specialist job discovery"
```

---

### Task 6: Implement Company Watch CRUD, Promotion, and Endpoint Upgrade Rules

**Files:**
- Create: `src/job_hunter/watchlist.py`
- Modify: `src/job_hunter/store.py`
- Modify: `src/job_hunter/models.py`
- Modify: `src/job_hunter/config.py`
- Test: `tests/test_watchlist.py`
- Test: `tests/test_store.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `CompanyWatchSeed(company_name, careers_url="", ats_provider=None, ats_identifier=None)`
- Produces: `CompanyWatchCandidate(company_name, careers_url, ats_provider, ats_identifier, confidence)`
- Produces: `should_auto_promote(evaluation: Evaluation) -> bool`
- Produces: `promote_company(store: JobStore, *, job_id: int, job: Job, evaluation: Evaluation, candidate: CompanyWatchCandidate | None) -> int | None`
- Produces: `sync_manual_watch_seeds(store: JobStore, seeds: list[CompanyWatchSeed]) -> None`
- Produces store methods `upsert_company_watch`, `get_company_watch`, `list_due_company_watches`.

- [ ] **Step 1: Write failing promotion-policy tests**

```python
@pytest.mark.parametrize("decision,expected", [
    ("high_priority", True),
    ("package_match", True),
    ("possible_match", False),
    ("skip", False),
    ("blocked", False),
])
def test_auto_promotion_uses_final_decision(decision, expected):
    assert should_auto_promote(make_evaluation(decision=decision)) is expected
```

- [ ] **Step 2: Write failing manual-seed idempotency/protection tests**

```python
def test_manual_seed_is_idempotent_and_marked_manual(tmp_path): ...
def test_automatic_update_cannot_downgrade_manual_structured_ats_target(tmp_path): ...
```

- [ ] **Step 3: Write failing endpoint-upgrade tests**

Cover:

```text
generic careers -> high-confidence Greenhouse: upgrade
Greenhouse -> generic careers guess: do not downgrade
known Lever target -> newer high-confidence Lever identifier: update only when confidence is higher/verified
```

- [ ] **Step 4: Implement watch candidate extraction from evaluated job**

Prefer:

```text
job.ats_provider + parsed board/company identifier
canonical employer careers/jobs URL
company homepage-derived careers URL only when explicitly present in fetched metadata
```

Do not invent an ATS slug from company name.

- [ ] **Step 5: Implement promotion and manual seed sync**

`promote_company()` returns `None` unless final decision is `high_priority` or `package_match` and company identity is non-empty. Automatic entries use `promotion_source='automatic'`; manual config always writes `promotion_source='manual'` and is allowed to activate/repair its existing entry.

- [ ] **Step 6: Run watch/config/store tests**

```bash
pytest tests/test_watchlist.py tests/test_store.py tests/test_config.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/job_hunter/watchlist.py src/job_hunter/store.py src/job_hunter/models.py src/job_hunter/config.py tests/test_watchlist.py tests/test_store.py tests/test_config.py
git commit -m "feat: learn relevant company watch targets"
```

---

### Task 7: Add Watched-Company Discovery With Health Backoff

**Files:**
- Create: `src/job_hunter/sources/company_watch.py`
- Modify: `src/job_hunter/store.py`
- Modify: `src/job_hunter/watchlist.py`
- Test: `tests/test_company_watch_source.py`
- Test: `tests/test_watchlist.py`

**Interfaces:**
- Produces: `CompanyWatchSource(store: JobStore, http)` implementing `discover() -> list[Job]`
- Produces store methods `record_watch_success(watch_id: int, verified_at: str | None = None)`, `record_watch_failure(watch_id: int)`, and `list_due_company_watches(now: datetime) -> list[sqlite3.Row]`.

- [ ] **Step 1: Write failing structured-watch tests**

Create watch rows for Greenhouse, Lever, and Ashby and fake their public responses. Assert `CompanyWatchSource.discover()` returns standard jobs with sources such as:

```text
watch:greenhouse
watch:lever
watch:ashby
```

and preserves source job IDs/URLs.

- [ ] **Step 2: Write failing health/backoff tests**

```python
def test_three_failures_pause_watch_for_24_hours(tmp_path, frozen_time):
    ...
    assert watch["consecutive_failures"] == 3
    assert watch["paused_until"] == "2026-09-01T12:00:00+00:00"


def test_success_resets_failure_state(tmp_path): ...
def test_paused_watch_is_not_due(tmp_path): ...
def test_manual_watch_is_paused_not_deleted(tmp_path): ...
```

- [ ] **Step 3: Implement ATS delegation**

For supported ATS entries, instantiate the existing provider adapter with the watch identifier and use its public `discover()` output. Rewrite only the source label to `watch:<provider>`; retain provider source IDs and URLs.

- [ ] **Step 4: Implement generic public careers-page fallback**

For `careers_url` without a supported ATS target, fetch the page and extract only explicit job links supported by structured `JobPosting` JSON-LD or clearly job-like links. Do not crawl arbitrary site depth in R2.

Each watch entry is isolated in `try/except`; one failure calls `record_watch_failure()` and continues.

- [ ] **Step 5: Implement deterministic pause behavior**

On failure count 3, set `paused_until = now + 24 hours`. Counts above 3 should not extend the pause repeatedly while the entry is already paused because paused entries are not due. On success clear `paused_until` and reset count.

- [ ] **Step 6: Run source/watch tests**

```bash
pytest tests/test_company_watch_source.py tests/test_watchlist.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/job_hunter/sources/company_watch.py src/job_hunter/store.py src/job_hunter/watchlist.py tests/test_company_watch_source.py tests/test_watchlist.py
git commit -m "feat: discover jobs from watched companies"
```

---

### Task 8: Wire Watch Discovery and Post-Evaluation Promotion Into the Pipeline

**Files:**
- Modify: `src/job_hunter/pipeline.py`
- Modify: `src/job_hunter/sources/__init__.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `CompanyWatchSource`, `sync_manual_watch_seeds`, `promote_company`.
- Produces no new public interface; integrates R2 into `run_pipeline()`.

- [ ] **Step 1: Write failing pipeline test for manual watch discovery**

Arrange settings with one manual seed and fake watch source output. Assert the watch candidate enters normal discovery/evaluation rather than a separate path.

- [ ] **Step 2: Write failing post-evaluation promotion test**

```python
def test_package_match_promotes_company_after_evaluation(...):
    summary = run_pipeline(...)
    watch = store.get_company_watch("Acme")
    assert watch is not None
    assert watch["promotion_source"] == "automatic"
```

Also assert `possible_match` does not promote.

- [ ] **Step 3: Write failure-isolation test**

Make `CompanyWatchSource.discover()` fail while an existing public source returns a valid job. Assert that job is still evaluated/delivered and pipeline returns normally.

- [ ] **Step 4: Wire manual seed sync before source construction**

At pipeline startup:

```python
sync_manual_watch_seeds(store, settings.policy.manual_company_watch)
```

Then append `CompanyWatchSource(store, http)` alongside `GmailStagedSource(store)` after the store exists.

- [ ] **Step 5: Promote only after evaluation is persisted**

Immediately after `store.save_evaluation(job_id, evaluation)`, call `promote_company(...)` inside its own fail-open `try/except`. Promotion failure increments/logs an R2 metric but must not interrupt cover-letter generation or delivery.

- [ ] **Step 6: Run pipeline regression suite**

```bash
pytest tests/test_pipeline.py tests/test_discovery.py tests/test_gmail_sync.py -v
```

Expected: PASS and R1 behavior unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/job_hunter/pipeline.py src/job_hunter/sources/__init__.py tests/test_pipeline.py
git commit -m "feat: integrate self-expanding company watches"
```

---

### Task 9: Add Source, Canonical, Dedupe, and Watch Observability

**Files:**
- Modify: `src/job_hunter/discovery.py`
- Modify: `src/job_hunter/pipeline.py`
- Modify: `src/job_hunter/watchlist.py`
- Test: `tests/test_discovery.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Extends `DiscoveryStats` with `canonical_resolved`, `canonical_unresolved`, `cross_source_duplicates`, and per-source error/contribution counters as practical without changing source failure semantics.
- Extends `RunSummary` only if needed for user-visible/testable counters; logs remain the primary observability surface.

- [ ] **Step 1: Write failing metric tests**

Assert a run with three source copies resolving to one canonical job logs/records:

```text
raw=3
unique=1
canonical_resolved>=1
cross_source_duplicates=2
```

and a watch promotion records `companies_promoted=1`.

- [ ] **Step 2: Implement deterministic counters at the ownership layer**

`discovery.py` owns candidate/resolution/dedupe counters. `pipeline.py` owns promotion counters. `CompanyWatchSource`/watch store owns check success/failure/pause logs. Avoid duplicate counting across layers.

- [ ] **Step 3: Add compact logs**

Emit lines equivalent to:

```text
source=yc discovered=18 eligible=6 errors=0
source=company_watch discovered=21 eligible=8 errors=2
canonical_resolved=31 canonical_unresolved=9 cross_source_duplicates=14
companies_promoted=2 watch_checks=17 watch_paused=1
```

Never log Gmail bodies, secrets, CV text, or raw Gemini prompts.

- [ ] **Step 4: Run metric tests**

```bash
pytest tests/test_discovery.py tests/test_pipeline.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/job_hunter/discovery.py src/job_hunter/pipeline.py src/job_hunter/watchlist.py tests/test_discovery.py tests/test_pipeline.py
git commit -m "feat: report automated discovery health"
```

---

### Task 10: Verify Late Canonical Discovery Preserves Existing Job History

**Files:**
- Modify: `src/job_hunter/store.py`
- Modify: `src/job_hunter/discovery.py`
- Test: `tests/test_store.py`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes `merge_jobs()` and `upsert_logical_job()` from Tasks 2 and 4.
- No new public interface; hardens migration of already-existing R1 job rows.

- [ ] **Step 1: Write failing legacy late-resolution integration test**

Scenario:

```text
Run 1: Gmail stores aggregator URL and job is evaluated/delivered.
Run 2: Lever source finds canonical employer posting for the same company/title/location.
```

Assert after Run 2:

```python
assert store.count_jobs() == 1
assert store.get_evaluation(original_job_id) is not None
assert store.has_delivery(original_job_id, "telegram_message")
assert len(store.list_job_sources(original_job_id)) >= 2
assert store.get_job(original_job_id).url == "https://jobs.lever.co/acme/abc"
```

- [ ] **Step 2: Write application-history preservation test**

Attach an R1 `INTERVIEW` application event to the pre-canonical job. After canonical merge/resolution:

```python
assert store.current_application_state(original_job_id) == "INTERVIEW"
```

- [ ] **Step 3: Implement survivor-selection rule**

When a newly discovered canonical job matches an existing legacy job, prefer the **existing job ID as survivor** if it already has evaluations, deliveries, materials, or application events. Update that row's canonical URL/ATS identity rather than switching IDs unnecessarily.

If two existing job rows must be merged, choose survivor deterministically:

```text
1. row with application events
2. row with evaluations/deliveries/materials
3. older first_seen_at
4. lower job id
```

- [ ] **Step 4: Run preservation tests**

```bash
pytest tests/test_store.py tests/test_discovery.py tests/test_gmail_matching.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/job_hunter/store.py src/job_hunter/discovery.py tests/test_store.py tests/test_discovery.py
git commit -m "fix: preserve job history during canonical merges"
```

---

### Task 11: Document R2 Configuration and Operational Behavior

**Files:**
- Modify: `README.md`
- Modify: `.env.example` only if R2 introduces an environment variable (expected: no new secrets).
- Test: `tests/test_config.py`

**Interfaces:** Documentation only; no new runtime behavior.

- [ ] **Step 1: Update README architecture**

Document the new flow explicitly:

```text
Gmail + existing sources + YC + specialist-domain search + company watch
  -> canonical resolution + provenance/dedupe
  -> existing filter/rank/evaluate/deliver pipeline
  -> strong match may promote company into watchlist
```

- [ ] **Step 2: Document manual watch seeds**

Include a concrete example matching the implemented config schema:

```yaml
manual_company_watch:
  - company_name: Example GmbH
    ats_provider: greenhouse
    ats_identifier: example
  - company_name: Another Company
    careers_url: https://example.com/careers
```

Explain that manual entries are never automatically deleted and that three failed checks pause them for 24 hours rather than removing them.

- [ ] **Step 3: Document specialist source mechanics**

State that YC is read from public pages, while Wellfound/Welcome to the Jungle/VC boards are discovered through public targeted search and then canonicalized. Explicitly state that R2 does not authenticate to these services.

- [ ] **Step 4: Document canonical/provenance behavior**

Explain that the bot may display/use an employer ATS URL even when originally discovered through another source, while preserving the original source in SQLite provenance.

- [ ] **Step 5: Run config tests and grep for accidental secrets/placeholders**

```bash
pytest tests/test_config.py -v
grep -R "TBD\|TODO\|GMAIL_REFRESH_TOKEN=.*[^.]" README.md config/search.yml .env.example || true
```

Review grep matches manually; legitimate documentation mentioning secret names is allowed, but no values may be committed.

- [ ] **Step 6: Commit**

```bash
git add README.md config/search.yml src/job_hunter/config.py src/job_hunter/models.py tests/test_config.py .env.example
git commit -m "docs: document R2 automated discovery"
```

---

### Task 12: Full Verification and Release Readiness

**Files:**
- No planned production-file changes. Fix only defects exposed by verification and commit each focused fix separately.

**Interfaces:** None.

- [ ] **Step 1: Run the complete automated test suite**

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run focused R1 regression tests**

```bash
pytest tests/test_gmail_auth.py tests/test_gmail_client.py tests/test_gmail_classifier.py tests/test_gmail_matching.py tests/test_gmail_sync.py tests/test_gmail_staged_source.py -q
```

Expected: all pass; R2 must not regress Gmail intelligence.

- [ ] **Step 3: Run focused R2 tests verbosely**

```bash
pytest tests/test_job_identity.py tests/test_canonical.py tests/test_watchlist.py tests/test_company_watch_source.py tests/test_yc_source.py tests/test_discovery.py tests/test_store.py tests/test_pipeline.py -v
```

Expected: all pass.

- [ ] **Step 4: Run a local dry-run smoke test with production-shaped config**

With normal non-secret test/local environment values loaded:

```bash
JOB_HUNTER_DRY_RUN=1 python -m job_hunter run
```

Expected:

```text
- existing public discovery still runs
- YC failure/success is isolated
- specialist search queries are visible only in normal safe logs
- company-watch failures do not abort the run
- canonical resolution failures fall back to original URLs
- no Telegram message is sent
```

- [ ] **Step 5: Inspect SQLite state after smoke test**

Use Python or `sqlite3` locally and verify:

```sql
SELECT COUNT(*) FROM job_sources;
SELECT company_name, promotion_source, active, paused_until, consecutive_failures FROM company_watch;
SELECT id, company, title, url, canonical_url FROM jobs ORDER BY id DESC LIMIT 20;
```

Confirm no duplicate logical job is obvious among a multi-source fixture/test setup and no Gmail full body data was introduced by R2.

- [ ] **Step 6: Review git diff against the approved spec**

```bash
git diff main...HEAD --stat
git diff main...HEAD -- docs/superpowers/specs/2026-08-31-job-hunter-bot-r2-automated-discovery-design.md
```

The approved design doc should remain intact except for explicit user-approved corrections.

- [ ] **Step 7: Run formatting/static checks already used by repository CI**

Inspect `.github/workflows/ci.yml` and run the same commands locally. Do not introduce a new formatter/linter solely for R2.

- [ ] **Step 8: Commit any verification-only fixes individually**

Examples:

```bash
git add <focused-files>
git commit -m "fix: preserve provenance during watch discovery"
```

Do not create a meaningless empty “verification” commit when no changes were needed.

---

## Implementation Order and Review Gates

Implement Tasks 1–12 in order. The dependency chain is intentional:

```text
identity
  -> persistence
  -> canonical resolution
  -> logical dedupe
  -> specialist discovery
  -> watch promotion/storage
  -> watched-company source/health
  -> pipeline integration
  -> observability
  -> legacy-history preservation
  -> docs
  -> full verification
```

Recommended reviewer checkpoints:

1. After Task 4: verify identity/canonical/provenance semantics before adding more sources.
2. After Task 8: verify the self-expanding watchlist is truly downstream of final strong evaluation.
3. After Task 10: verify existing R1 evaluation/delivery/application history survives late canonicalization.
4. After Task 12: final spec-conformance review before PR merge.

## Explicit Non-Goals During Execution

Do not opportunistically add any of the following while implementing this plan:

- Supabase tables or repositories.
- Relay integration.
- Telegram inbound URL commands.
- LinkedIn browser login/session automation.
- Wellfound or Welcome to the Jungle authenticated scraping.
- A generic crawler framework.
- Automatic application submission.
- Application-outcome learning/ranking feedback.
- Additional specialist boards beyond the configured targeted-domain mechanism unless the spec is revised first.
