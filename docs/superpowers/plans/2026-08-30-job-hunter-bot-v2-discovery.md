# Job Hunter Bot v2 Discovery Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Broaden public job discovery and globally rank all eligible candidates before spending the per-run Gemini evaluation budget, so source ordering no longer hides stronger opportunities.

**Architecture:** Add configuration-driven query generation, three additional public discovery adapters, a focused discovery aggregation layer, and a deterministic pre-Gemini ranking layer. Refactor `run_pipeline()` so all sources are collected and deduplicated first, then the best `max_jobs_per_run` candidates are evaluated while existing SQLite, cover-letter, Telegram retry, and scheduling behavior remain compatible.

**Tech Stack:** Python 3.12, requests, BeautifulSoup4, PyYAML, standard-library `xml.etree.ElementTree`, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-08-30-job-hunter-bot-v2-discovery-design.md`

## Global Constraints

- Do not change the default Gemini model as part of this plan.
- Do not add a paid search API, authenticated scraping, browser automation, or employer-form submission.
- Keep all source failures isolated; one source must never abort the run.
- Preserve compatibility with existing SQLite state artifacts.
- Preserve current evaluation caching, cover-letter/PDF generation, Telegram delivery retry, and scheduled-run behavior.
- `max_jobs_per_run` is applied only after global deterministic ranking of all eligible candidates.
- Search-derived records must be enriched from the actual posting before Gemini evaluation when possible.
- No external calls are allowed in tests.
- Run `pytest -q` before completion.

---

### Task 1: Discovery configuration and query generation

**Files:**
- Modify: `src/job_hunter/models.py`
- Modify: `src/job_hunter/config.py`
- Modify: `config/search.yml`
- Create: `src/job_hunter/discovery_queries.py`
- Modify: `tests/test_config.py`
- Create: `tests/test_discovery_queries.py`

**Interfaces:**
- Produces: `SearchPolicy.role_families: list[str]`
- Produces: `SearchPolicy.search_query_templates: list[str]`
- Produces: `SearchPolicy.search_domains: list[str]`
- Produces: `SearchPolicy.max_search_queries_per_run: int`
- Produces: `generate_search_queries(policy: SearchPolicy) -> list[str]`
- Preserves: existing `SearchPolicy.search_queries` as explicit legacy/additional queries.

- [ ] **Step 1: Write failing configuration tests**

Extend `tests/test_config.py` with a YAML fixture containing:

```yaml
max_search_queries_per_run: 4
role_families:
  - staff product engineer
  - senior software engineer frontend
search_query_templates:
  - '"{role}" React TypeScript remote Europe'
search_domains:
  - jobs.ashbyhq.com
search_queries:
  - '"Senior Product Engineer" remote'
```

Assert the resulting `SearchPolicy` exposes all four values and still exposes `search_queries`.

- [ ] **Step 2: Run the config test and verify failure**

Run:

```bash
pytest tests/test_config.py -q
```

Expected: failure because the new `SearchPolicy` fields are not defined/loaded yet.

- [ ] **Step 3: Add the new policy fields and loader support**

Update the dataclass in `models.py`:

```python
@dataclass(slots=True)
class SearchPolicy:
    target_titles: list
    positive_keywords: list
    blocked_title_keywords: list
    salary_floor_eur: int
    thresholds: dict
    max_jobs_per_run: int = 25
    search_queries: list = field(default_factory=list)
    ats: dict = field(default_factory=dict)
    role_families: list[str] = field(default_factory=list)
    search_query_templates: list[str] = field(default_factory=list)
    search_domains: list[str] = field(default_factory=list)
    max_search_queries_per_run: int = 30
```

Load each field explicitly in `config.py` with the same defaults.

- [ ] **Step 4: Write failing query-generation tests**

Create `tests/test_discovery_queries.py` covering:

```python
def test_generate_search_queries_combines_roles_templates_domains_and_legacy_queries():
    ...
    queries = generate_search_queries(policy)
    assert queries == [
        '"staff product engineer" React TypeScript remote Europe',
        'site:jobs.ashbyhq.com "staff product engineer" React TypeScript remote Europe',
        '"senior software engineer frontend" React TypeScript remote Europe',
        'site:jobs.ashbyhq.com "senior software engineer frontend" React TypeScript remote Europe',
    ]
```

Also add a separate test proving:

```python
assert len(generate_search_queries(policy_with_limit_3)) == 3
```

and a test proving legacy explicit queries are used when role/template lists are empty.

- [ ] **Step 5: Run query tests and verify failure**

Run:

```bash
pytest tests/test_discovery_queries.py -q
```

Expected: import/function failure.

- [ ] **Step 6: Implement deterministic capped query generation**

Create `src/job_hunter/discovery_queries.py` with:

```python
from job_hunter.models import SearchPolicy


def generate_search_queries(policy: SearchPolicy) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        normalized = " ".join(query.split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            queries.append(normalized)

    for role in policy.role_families:
        for template in policy.search_query_templates:
            base = template.format(role=role)
            add(base)
            for domain in policy.search_domains:
                add(f"site:{domain} {base}")

    for query in policy.search_queries:
        add(query)

    return queries[: policy.max_search_queries_per_run]
```

Keep ordering deterministic and do not create an unbounded Cartesian product beyond the configured cap.

- [ ] **Step 7: Update `config/search.yml` with the v2 role/query families**

Add the role families from the spec, a finite set of query templates, ATS domains, and `max_search_queries_per_run: 30`. Retain useful explicit `search_queries` only when they add coverage not already generated.

- [ ] **Step 8: Run focused tests**

Run:

```bash
pytest tests/test_config.py tests/test_discovery_queries.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/job_hunter/models.py src/job_hunter/config.py src/job_hunter/discovery_queries.py config/search.yml tests/test_config.py tests/test_discovery_queries.py
git commit -m "feat: add configurable discovery query families"
```

---

### Task 2: Deterministic pre-Gemini ranking

**Files:**
- Create: `src/job_hunter/ranking.py`
- Create: `tests/test_ranking.py`

**Interfaces:**
- Produces: `source_quality(job: Job) -> int`
- Produces: `priority_score(job: Job, policy: SearchPolicy) -> int`
- Produces: `rank_jobs(jobs: list[tuple[int, Job]], policy: SearchPolicy) -> list[tuple[int, Job, int]]`

- [ ] **Step 1: Write failing ranking tests**

Create `tests/test_ranking.py` with focused tests:

```python
def test_product_engineer_outranks_generic_react_role(policy):
    strong = Job(source="ashby", title="Staff Product Engineer", company="A", location="Remote Europe", remote=True, description="React TypeScript product ownership architecture")
    generic = Job(source="remotive", title="React Developer", company="B", location="Worldwide", remote=True, description="React React React React React")
    assert priority_score(strong, policy) > priority_score(generic, policy)
```

```python
def test_explicit_europe_remote_outranks_unknown_location(policy):
    ...
```

```python
def test_ats_url_gets_higher_source_quality_than_general_search_result():
    ats = Job(source="duckduckgo", title="Senior Product Engineer", url="https://jobs.ashbyhq.com/acme/123")
    web = Job(source="duckduckgo", title="Senior Product Engineer", url="https://example.com/jobs/123")
    assert source_quality(ats) > source_quality(web)
```

```python
def test_keyword_repetition_is_capped(policy):
    normal = Job(..., description="React TypeScript product ownership")
    spammy = Job(..., description="React " * 100)
    assert priority_score(spammy, policy) < priority_score(normal, policy) + 20
```

```python
def test_rank_jobs_is_stable_for_equal_scores(policy):
    ...
```

- [ ] **Step 2: Run ranking tests and verify failure**

Run:

```bash
pytest tests/test_ranking.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement bounded signal helpers**

In `ranking.py`, keep the score transparent and additive:

```python
_ATS_HOSTS = ("jobs.ashbyhq.com", "jobs.lever.co", "boards.greenhouse.io")


def source_quality(job: Job) -> int:
    if any(host in (job.url or "") for host in _ATS_HOSTS):
        return 10
    if job.source in {"ashby", "lever", "greenhouse"}:
        return 10
    if job.source in {"remoteok", "remotive", "weworkremotely", "arbeitnow"}:
        return 7
    if job.source == "hackernews":
        return 5
    return 3
```

Implement title-fit, candidate-strength, career-direction, and location helpers using capped matches. `priority_score()` must return a bounded integer from 0 to 100.

- [ ] **Step 4: Implement stable global ordering**

Implement:

```python
def rank_jobs(jobs: list[tuple[int, Job]], policy: SearchPolicy) -> list[tuple[int, Job, int]]:
    scored = [(job_id, job, priority_score(job, policy)) for job_id, job in jobs]
    return sorted(
        scored,
        key=lambda item: (-item[2], (item[1].company or "").lower(), (item[1].title or "").lower(), item[0]),
    )
```

- [ ] **Step 5: Run ranking tests**

Run:

```bash
pytest tests/test_ranking.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/job_hunter/ranking.py tests/test_ranking.py
git commit -m "feat: rank discovery candidates before Gemini"
```

---

### Task 3: Aggregate, enrich, persist, and collapse candidates before evaluation

**Files:**
- Create: `src/job_hunter/discovery.py`
- Create: `tests/test_discovery.py`
- Use: `src/job_hunter/fetching.py`
- Use: `src/job_hunter/normalize.py`
- Use: `src/job_hunter/prefilter.py`
- Use: `src/job_hunter/store.py`

**Interfaces:**
- Produces: `DiscoveryStats`
- Produces: `DiscoveryResult`
- Produces: `collect_candidates(sources, store, http, policy) -> DiscoveryResult`
- `DiscoveryResult.eligible` contains only unique jobs that currently need Gemini evaluation and passed deterministic prefilter.
- `DiscoveryResult.rediscovered_job_ids` contains already-evaluated jobs encountered during discovery so existing delivery retry behavior can still be invoked by the pipeline.

- [ ] **Step 1: Write failing aggregation tests**

Create tests proving:

1. All sources are called even if one raises.
2. A search result with only URL/title is enriched before eligibility.
3. Two records with the same canonical URL collapse to one candidate.
4. When duplicate records differ in richness, the record with company/description/original ATS URL is preferred.
5. Prefilter-rejected jobs are counted but not returned in `eligible`.
6. Already-evaluated unchanged jobs are excluded from `eligible` and included in `rediscovered_job_ids`.

Use fake sources and fake HTTP only.

- [ ] **Step 2: Run discovery tests and verify failure**

```bash
pytest tests/test_discovery.py -q
```

Expected: import failure.

- [ ] **Step 3: Define focused result dataclasses**

In `discovery.py`:

```python
from dataclasses import dataclass, field


@dataclass(slots=True)
class DiscoveryStats:
    raw: int = 0
    unique: int = 0
    prefilter_rejected: int = 0
    eligible: int = 0
    per_source: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class DiscoveryResult:
    eligible: list[tuple[int, Job]]
    rediscovered_job_ids: list[int]
    stats: DiscoveryStats
```

- [ ] **Step 4: Implement in-run duplicate identity**

Use canonical URL first, with normalized company/title/location fallback. Do not modify persisted fingerprint semantics in this task.

```python
def _candidate_key(job: Job) -> str:
    if job.url:
        return f"url:{canonicalize_url(job.url)}"
    return "identity:" + "|".join(normalize_text(v) for v in (job.company, job.title, job.location))
```

Additionally maintain a normalized company/title/location lookup so two records with different aggregator/original URLs can collapse when their identity matches exactly.

- [ ] **Step 5: Implement richer-record preference**

Prefer records with:

1. original ATS URL;
2. non-empty description;
3. non-empty company;
4. explicit location/remote information.

Keep this deterministic.

- [ ] **Step 6: Implement collection flow**

For each source:

```text
try discover
count raw/per-source
for each job:
    enrich only when URL exists and description is missing
    collapse duplicate candidates
```

After all sources finish:

```text
upsert each unique job
if needs_evaluation:
    run prefilter
    eligible -> return for ranking
else:
    record job_id for delivery retry path
```

- [ ] **Step 7: Run discovery tests**

```bash
pytest tests/test_discovery.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/job_hunter/discovery.py tests/test_discovery.py
git commit -m "feat: aggregate and dedupe discovery candidates"
```

---

### Task 4: Add Remote OK public feed adapter

**Files:**
- Create: `src/job_hunter/sources/remoteok.py`
- Modify: `src/job_hunter/sources/__init__.py`
- Modify: `tests/test_sources.py`

**Interfaces:**
- Produces: `RemoteOKSource(http).discover() -> list[Job]`
- Endpoint: `https://remoteok.com/api`
- Source name: `remoteok`

- [ ] **Step 1: Write failing Remote OK normalization test**

Add a fixture where the JSON array begins with the non-job legal metadata object, followed by a job:

```python
[
    {"last_updated": 1, "legal": "terms"},
    {
        "id": "123",
        "slug": "senior-product-engineer-acme",
        "position": "Senior Product Engineer",
        "company": "Acme",
        "location": "Europe",
        "url": "https://remoteok.com/remote-jobs/123",
        "description": "<p>React TypeScript product ownership</p>",
        "tags": ["react", "typescript"]
    }
]
```

Assert metadata row is skipped, HTML is stripped, `remote=True`, and source/source ID are set.

- [ ] **Step 2: Run the test and verify failure**

```bash
pytest tests/test_sources.py -q -k remoteok
```

Expected: import/class failure.

- [ ] **Step 3: Implement adapter**

Use existing `HttpClient.get_json()` and `strip_html()`. Treat records without a position/title as non-job metadata and skip them.

- [ ] **Step 4: Export the adapter**

Add `RemoteOKSource` to `sources/__init__.py` exports, but defer default `build_sources()` wiring to Task 7.

- [ ] **Step 5: Run source tests**

```bash
pytest tests/test_sources.py -q -k remoteok
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/job_hunter/sources/remoteok.py src/job_hunter/sources/__init__.py tests/test_sources.py
git commit -m "feat: add Remote OK discovery source"
```

---

### Task 5: Add We Work Remotely public RSS adapter

**Files:**
- Create: `src/job_hunter/sources/weworkremotely.py`
- Modify: `src/job_hunter/sources/__init__.py`
- Modify: `tests/test_sources.py`

**Interfaces:**
- Produces: `WeWorkRemotelySource(http, feed_urls=None).discover() -> list[Job]`
- Default feeds:
  - `https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss`
  - `https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss`
  - `https://weworkremotely.com/categories/remote-product-jobs.rss`
- Source name: `weworkremotely`

- [ ] **Step 1: Write failing RSS parsing test**

Use an RSS fixture containing an `<item>` with title, link, description, and category fields. Assert:

- company/title are split when the feed title uses a `Company: Role` form;
- URL is preserved;
- description HTML is stripped;
- `remote=True`;
- malformed items are skipped without aborting the feed.

- [ ] **Step 2: Run the test and verify failure**

```bash
pytest tests/test_sources.py -q -k weworkremotely
```

Expected: import/class failure.

- [ ] **Step 3: Implement with standard-library XML parsing**

Use `xml.etree.ElementTree.fromstring()` to avoid adding a dependency. Fetch each configured feed independently and continue if one feed fails.

- [ ] **Step 4: Export adapter and run focused tests**

```bash
pytest tests/test_sources.py -q -k weworkremotely
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/job_hunter/sources/weworkremotely.py src/job_hunter/sources/__init__.py tests/test_sources.py
git commit -m "feat: add We Work Remotely RSS discovery"
```

---

### Task 6: Add Hacker News Who Is Hiring adapter

**Files:**
- Create: `src/job_hunter/sources/hackernews.py`
- Modify: `src/job_hunter/sources/__init__.py`
- Modify: `tests/test_sources.py`

**Interfaces:**
- Produces: `HackerNewsHiringSource(http).discover() -> list[Job]`
- Uses public HN Algolia API.
- Source name: `hackernews`

- [ ] **Step 1: Write failing tests for thread discovery and comments**

Mock two API responses:

1. Search response locating the newest story titled `Ask HN: Who is hiring? (August 2026)`.
2. Item response containing comment children.

Use comments such as:

```text
Acme | Senior Product Engineer | Remote EU | React, TypeScript | https://jobs.ashbyhq.com/acme/123
```

Assert a normalized `Job` is created with:

- title containing `Senior Product Engineer`;
- company `Acme`;
- location `Remote EU`;
- URL extracted from the comment;
- description containing the comment text;
- `remote=True` when the text explicitly says remote;
- comment ID as `source_job_id`.

Add a test proving unrelated/non-job comments are ignored.

- [ ] **Step 2: Run focused tests and verify failure**

```bash
pytest tests/test_sources.py -q -k hackernews
```

Expected: import/class failure.

- [ ] **Step 3: Implement latest-thread lookup**

Search Algolia for `Ask HN: Who is hiring?` restricted to stories, choose the newest exact monthly hiring thread, then fetch the item tree.

- [ ] **Step 4: Implement conservative comment normalization**

Only emit comments that contain enough evidence to be a job posting: role text plus company-like first segment or URL. Extract the first HTTP(S) URL when present. Preserve the full stripped comment text as the description so later enrichment/ranking has context.

- [ ] **Step 5: Export adapter and run tests**

```bash
pytest tests/test_sources.py -q -k hackernews
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/job_hunter/sources/hackernews.py src/job_hunter/sources/__init__.py tests/test_sources.py
git commit -m "feat: add Hacker News hiring discovery"
```

---

### Task 7: Wire v2 source construction and generated web-search queries

**Files:**
- Modify: `src/job_hunter/sources/__init__.py`
- Modify: `src/job_hunter/discovery_queries.py`
- Modify: `config/search.yml`
- Modify: `tests/test_sources.py`

**Interfaces:**
- `build_sources(settings, http)` includes Remotive, Arbeitnow, Remote OK, We Work Remotely, Hacker News, DuckDuckGo generated queries, and configured ATS adapters.

- [ ] **Step 1: Update failing `build_sources` test**

Change the existing test so expected source types include:

```python
assert "RemoteOKSource" in kinds
assert "WeWorkRemotelySource" in kinds
assert "HackerNewsHiringSource" in kinds
```

Also inspect the built `DuckDuckGoSource` through a test seam or factor a helper so the test can assert it receives `generate_search_queries(settings.policy)` rather than only `policy.search_queries`.

- [ ] **Step 2: Run the test and verify failure**

```bash
pytest tests/test_sources.py -q -k build_sources
```

Expected: FAIL because new sources/query generation are not wired.

- [ ] **Step 3: Wire the default sources**

Update `build_sources()` to instantiate:

```text
RemotiveSource
ArbeitnowSource
RemoteOKSource
WeWorkRemotelySource
HackerNewsHiringSource
DuckDuckGoSource(generate_search_queries(policy))
configured Ashby/Lever/Greenhouse sources
```

Keep each adapter independent.

- [ ] **Step 4: Keep ATS seeds config-compatible**

Retain `ats.ashby`, `ats.lever`, and `ats.greenhouse` lists. Do not invent private credentials. Public company board slugs can be added later by editing YAML only.

- [ ] **Step 5: Run source/query tests**

```bash
pytest tests/test_sources.py tests/test_discovery_queries.py tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/job_hunter/sources/__init__.py src/job_hunter/discovery_queries.py config/search.yml tests/test_sources.py
git commit -m "feat: expand default discovery coverage"
```

---

### Task 8: Refactor pipeline to apply a global ranked Gemini budget

**Files:**
- Modify: `src/job_hunter/pipeline.py`
- Modify: `src/job_hunter/store.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_store.py`
- Use: `src/job_hunter/discovery.py`
- Use: `src/job_hunter/ranking.py`

**Interfaces:**
- Consumes: `collect_candidates(...) -> DiscoveryResult`
- Consumes: `rank_jobs(...) -> list[tuple[int, Job, int]]`
- Produces: same public `run_pipeline(...) -> RunSummary` signature.
- Adds store helper: `pending_delivery_job_ids() -> list[int]` so pending Telegram retries remain independent of fresh discovery.

- [ ] **Step 1: Write the critical failing source-order regression test**

In `tests/test_pipeline.py`, set `max_jobs_per_run=1`.

Create:

- `EarlyNoiseSource` returning several mediocre-but-prefilter-eligible React roles.
- `LateStrongSource` returning one `Staff Product Engineer` with React/TypeScript/product ownership/Remote Europe.

Run once with noise first and strong second.

Assert Gemini evaluates exactly one job and it is the strong job.

Repeat with source order reversed and assert the same job is evaluated.

Fake Gemini should record the evaluation prompt or expose the evaluated job title so the assertion is direct.

- [ ] **Step 2: Run the regression test and verify failure**

```bash
pytest tests/test_pipeline.py -q -k global_ranked_budget
```

Expected: FAIL under the current source-by-source evaluation loop.

- [ ] **Step 3: Write store test for pending delivery IDs**

Add a test:

```python
def test_pending_delivery_job_ids_returns_evaluated_jobs_missing_a_delivery(tmp_path):
    ...
```

Cover:

- evaluation/material exists and no delivery -> returned;
- both Telegram message/document exist -> not returned;
- possible match only needs message delivery;
- ready decision with message sent but document missing -> returned.

- [ ] **Step 4: Implement `pending_delivery_job_ids()`**

Use existing evaluations/materials/deliveries tables without schema reset. Base readiness on latest evaluation decision and delivery type presence.

- [ ] **Step 5: Refactor discovery/evaluation phase**

Replace the source-local Gemini loop with:

```python
discovery = collect_candidates(sources, store, http, settings.policy)
ranked = rank_jobs(discovery.eligible, settings.policy)
selected = ranked[: settings.policy.max_jobs_per_run]
```

Then evaluate only `selected`.

Preserve the existing per-job evaluation exception handling and material-generation logic.

- [ ] **Step 6: Preserve/strengthen pending delivery retries**

Before Telegram delivery, requeue pending jobs from:

```python
set(discovery.rediscovered_job_ids) | set(store.pending_delivery_job_ids())
```

Call the existing `_requeue_pending_delivery()` for each ID, excluding current-run jobs already queued for fresh delivery.

This ensures source outages do not suppress previously pending Telegram delivery.

- [ ] **Step 7: Keep `RunSummary` semantics unchanged**

`ready_to_apply`, `possible_matches`, and `skipped` continue to describe current-run processing, not historical delivery retries. Do not count ranking rejection as Gemini evaluation output.

- [ ] **Step 8: Run pipeline/store tests**

```bash
pytest tests/test_pipeline.py tests/test_store.py -q
```

Expected: PASS, including existing Telegram retry tests.

- [ ] **Step 9: Commit**

```bash
git add src/job_hunter/pipeline.py src/job_hunter/store.py tests/test_pipeline.py tests/test_store.py
git commit -m "feat: globally rank jobs before Gemini evaluation"
```

---

### Task 9: Discovery funnel logging and regression documentation

**Files:**
- Modify: `src/job_hunter/pipeline.py`
- Modify: `src/job_hunter/discovery.py`
- Modify: `tests/test_pipeline.py`
- Modify: `README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Logs discovery funnel counts at INFO level.
- Documents the v2 architecture and configuration.

- [ ] **Step 1: Write failing logging test**

Use `caplog` around a fake multi-source pipeline run and assert logs contain a summary matching:

```text
discovery: raw=... unique=... eligible=... selected=...
```

and a selected-source distribution line.

Do not assert exact timestamps or logger prefixes.

- [ ] **Step 2: Run logging test and verify failure**

```bash
pytest tests/test_pipeline.py -q -k discovery_logging
```

Expected: FAIL because the funnel summary is not logged yet.

- [ ] **Step 3: Add concise run-level logging**

Log after ranking/selection:

```python
logger.info(
    "discovery: raw=%s unique=%s prefilter_rejected=%s eligible=%s selected=%s",
    discovery.stats.raw,
    discovery.stats.unique,
    discovery.stats.prefilter_rejected,
    discovery.stats.eligible,
    len(selected),
)
```

Build selected source counts from selected jobs and log them without descriptions or secrets.

- [ ] **Step 4: Update README**

Document:

- v2 source list;
- public-only/no-auth discovery boundary;
- query-family configuration;
- global ranking before Gemini;
- `max_jobs_per_run` semantics;
- how to add public Ashby/Lever/Greenhouse board slugs;
- how to inspect discovery funnel logs.

- [ ] **Step 5: Update AGENTS.md**

Change the architecture overview from source-by-source evaluation to:

```text
all sources -> enrich/dedupe -> deterministic rank -> top-N Gemini -> materials -> Telegram
```

Add `discovery.py`, `ranking.py`, and the new source adapters to the module map.

- [ ] **Step 6: Run the full test suite**

```bash
pytest -q
```

Expected: all tests PASS.

- [ ] **Step 7: Verify no private source material was committed**

Run:

```bash
git grep -n "CANDIDATE_PROFILE_B64=" -- ':!docs/superpowers/*' || true
git grep -n "COVER_LETTER_TEMPLATE_B64=" -- ':!docs/superpowers/*' || true
```

Expected: no secret values; documentation may mention variable names only.

- [ ] **Step 8: Inspect final diff**

```bash
git status --short
git diff --stat HEAD~1..HEAD
```

Confirm scope is limited to v2 discovery quality and its docs/tests.

- [ ] **Step 9: Commit**

```bash
git add src/job_hunter/pipeline.py src/job_hunter/discovery.py tests/test_pipeline.py README.md AGENTS.md
git commit -m "docs: document v2 discovery pipeline"
```

---

## Final verification

After all tasks are complete:

- [ ] Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] Run a local dry run with test credentials/config if available:

```bash
JOB_HUNTER_DRY_RUN=1 python -m job_hunter run
```

Expected behavior: all configured sources are attempted, discovery funnel counts are logged, only globally top-ranked eligible jobs consume Gemini budget, and Telegram is skipped.

- [ ] Push the implementation branch and confirm `.github/workflows/ci.yml` passes before merging.

## Implementation success check

The implementation is successful when a test with `max_jobs_per_run=1` proves a high-signal job from the last source is selected over many earlier mediocre jobs, additional Remote OK/WWR/Hacker News discovery is active, cross-source duplicates are evaluated once per run, and the existing state/delivery test suite remains green.