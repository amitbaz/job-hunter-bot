# Source Coverage Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn market-specific source configuration into real job ingestion by adding persistent ATS learning/scanning plus direct DevJobs and Wellfound adapters, while using Brave only as scarce source discovery and making source quality measurable.

**Architecture:** Keep the existing discovery/dedupe/market/prefilter/Gemini pipeline intact. Add a persistent ATS registry learned from every job URL, a composite learned-ATS source that reuses the existing Ashby/Lever/Greenhouse adapters, and two direct HTML adapters for the highest-confidence Phase 1 sources. Replace ambiguous `source_domains` semantics with explicit `direct_sources` and `discovery_domains`; market web search runs only through the metered Brave allocation, never DuckDuckGo fallback.

**Tech Stack:** Python 3.12+, dataclasses, PyYAML, SQLite, requests via existing `HttpClient`, BeautifulSoup, pytest.

**Spec:** `docs/superpowers/specs/2026-09-03-source-coverage-expansion-design.md`

## Global Constraints

- This plan implements **Phase 1 only**: ATS registry/harvesting/scanning, DevJobs, Wellfound, Brave-as-source-discovery, and source-quality telemetry.
- Phase 2 sources (JobStreet, Built In, GotFriends, Glints, TechAviv direct adapter, NodeFlair, MyCareersFuture, WorkVisaJobs) are explicitly out of scope for this plan.
- Preserve all current market ordering, query shares, salary floors, language rules, sponsorship rules, relocation rules, role targeting, `max_jobs_per_run`, Gemini model/quota settings, Telegram behavior, Gmail behavior, and SQLite state restore workflow.
- Existing broad feeds remain enabled: Remotive, Arbeitnow, Jobicy, Himalayas, RemoteOK, WeWorkRemotely, Hacker News, and YC.
- Existing static `ats:` config remains supported and additive to learned ATS boards.
- Learned ATS scan cap defaults to `75` boards/run.
- Learned ATS board failure handling mirrors company-watch philosophy: isolate each board, record failure, pause that board for 24 hours, never fail the run.
- The existing Brave persisted monthly budget remains `250` by default and is not increased.
- DuckDuckGo is removed only from **market-specific targeted discovery**. Canonical-resolution behavior is not redesigned in this plan.
- Direct source adapters must use normal `HttpClient` HTTP and server-rendered HTML; no browser automation, Playwright, or authenticated scraping.
- A configured `direct_sources` entry must correspond to an actual adapter instantiated by code. A `discovery_domains` entry is search/seed-only and must never be counted as direct source coverage.
- DevJobs and Wellfound production parsers must have deterministic synthetic/trimmed HTML fixtures in tests and fail open on markup/network errors.
- Do not loosen/tighten pre-Gemini relevance filtering or candidate-context extraction in this feature.

---

## File Map

### Create

- `src/job_hunter/ats_registry.py` — ATS reference harvesting and deterministic board selection.
- `src/job_hunter/sources/learned_ats.py` — scan due learned ATS boards through existing Ashby/Lever/Greenhouse adapters and persist board health.
- `src/job_hunter/sources/devjobs.py` — direct Israel DevJobs adapter.
- `src/job_hunter/sources/wellfound.py` — direct Wellfound role/location adapter.
- `tests/test_ats_registry.py`
- `tests/test_learned_ats_source.py`
- `tests/test_devjobs_source.py`
- `tests/test_wellfound_source.py`

### Modify

- `config/search.yml`
- `src/job_hunter/models.py`
- `src/job_hunter/config.py`
- `src/job_hunter/discovery_queries.py`
- `src/job_hunter/discovery.py`
- `src/job_hunter/store.py`
- `src/job_hunter/sources/__init__.py`
- `src/job_hunter/pipeline.py`
- `tests/market_fixtures.py`
- `tests/test_config.py`
- `tests/test_discovery_queries.py`
- `tests/test_discovery.py`
- `tests/test_sources.py`
- `tests/test_store.py`
- `tests/test_pipeline.py`

---

### Task 1: Make source semantics explicit in config

**Files:**
- Modify: `src/job_hunter/models.py`
- Modify: `src/job_hunter/config.py`
- Modify: `src/job_hunter/discovery_queries.py`
- Modify: `config/search.yml`
- Modify: `tests/market_fixtures.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_discovery_queries.py`

**Interfaces:**
- `MarketPolicy.direct_sources: list[str]`
- `MarketPolicy.discovery_domains: list[str]`
- `SearchPolicy.max_learned_ats_boards_per_run: int = 75`
- Legacy YAML `source_domains` is accepted only as an alias for `discovery_domains`; if both keys are supplied for one market, config loading raises `ValueError`.
- `generate_search_queries()` uses only `market.discovery_domains` for site-qualified search variants.

- [ ] **Step 1: Write failing config tests**

Add tests equivalent to:

```python
def test_market_source_config_distinguishes_direct_and_discovery(monkeypatch, tmp_path):
    settings = _load_market_yaml(
        monkeypatch,
        tmp_path,
        """
markets:
  - id: israel_remote
    query_share: 1.0
    locations: [Israel]
    allowed_languages: [English, Hebrew]
    salary: {currency: ILS, gross_base_floor: 420000}
    remote_policy: required
    relocation_policy: none
    sponsorship_policy: not_required
    direct_sources: [devjobs]
    discovery_domains: [jobs.techaviv.com, jobs.ashbyhq.com]
""",
    )
    market = settings.policy.markets[0]
    assert market.direct_sources == ["devjobs"]
    assert market.discovery_domains == ["jobs.techaviv.com", "jobs.ashbyhq.com"]


def test_legacy_source_domains_are_discovery_only(monkeypatch, tmp_path):
    settings = _load_market_yaml(..., "source_domains: [wellfound.com]")
    assert settings.policy.markets[0].direct_sources == []
    assert settings.policy.markets[0].discovery_domains == ["wellfound.com"]


def test_market_rejects_source_domains_and_discovery_domains_together(...):
    with pytest.raises(ValueError, match="cannot define both source_domains and discovery_domains"):
        load_settings(...)
```

Add a default assertion:

```python
assert settings.policy.max_learned_ats_boards_per_run == 75
```

- [ ] **Step 2: Write failing query-generation test**

In `tests/test_discovery_queries.py`, create a market with:

```python
direct_sources=["wellfound"],
discovery_domains=["jobs.ashbyhq.com"],
```

Assert generated site queries contain `site:jobs.ashbyhq.com` and never `site:wellfound` merely because `wellfound` is direct.

- [ ] **Step 3: Run the focused tests**

```bash
python -m pytest tests/test_config.py tests/test_discovery_queries.py -q
```

Expected: FAIL because the new fields do not exist.

- [ ] **Step 4: Implement the model/parser migration**

Replace `MarketPolicy.source_domains` with:

```python
direct_sources: list[str] = field(default_factory=list)
discovery_domains: list[str] = field(default_factory=list)
```

Add to `SearchPolicy`:

```python
max_learned_ats_boards_per_run: int = 75
```

In `_parse_markets()`:

```python
if "source_domains" in entry and "discovery_domains" in entry:
    raise ValueError(
        f"markets[{index}] cannot define both source_domains and discovery_domains"
    )
discovery_domains = entry.get(
    "discovery_domains",
    entry.get("source_domains", []),
)
```

Allow `direct_sources` and `discovery_domains` in strict field validation. Parse `max_learned_ats_boards_per_run` from top-level YAML and reject non-positive values.

Change `generate_search_queries()` from:

```python
variants = [base] + [f"site:{domain} {base}" for domain in market.source_domains]
```

to:

```python
variants = [base] + [
    f"site:{domain} {base}" for domain in market.discovery_domains
]
```

- [ ] **Step 5: Migrate production `config/search.yml`**

Use the following Phase-1 direct-source mapping:

```yaml
# germany_eu
direct_sources: [wellfound]

# israel_remote
direct_sources: [devjobs]

# london
direct_sources: [wellfound]

# singapore
direct_sources: []

# us_nyc_sf
direct_sources: [wellfound]

# secondary_eu_relocation
direct_sources: []
```

Move every existing `source_domains` value into `discovery_domains`, except that direct adapters remain free to also appear as discovery domains only when they have a distinct source-discovery purpose. Keep ATS domains (`jobs.ashbyhq.com`, `jobs.lever.co`, `boards.greenhouse.io`) in discovery domains because Brave hits on them seed the ATS registry.

Add:

```yaml
max_learned_ats_boards_per_run: 75
```

- [ ] **Step 6: Update shared fixtures, run, commit**

Update `tests/market_fixtures.py::make_market()` to accept `direct_sources` and `discovery_domains` and stop constructing `source_domains` directly.

```bash
python -m pytest tests/test_config.py tests/test_discovery_queries.py -q
git add config/search.yml src/job_hunter/models.py src/job_hunter/config.py src/job_hunter/discovery_queries.py tests/market_fixtures.py tests/test_config.py tests/test_discovery_queries.py
git commit -m "feat: distinguish direct and discovery sources"
```

---

### Task 2: Add persistent ATS registry storage and scheduling data

**Files:**
- Modify: `src/job_hunter/store.py`
- Modify: `src/job_hunter/models.py`
- Modify: `tests/test_store.py`

**Interfaces:**
- `AtsRegistryEntry` dataclass.
- `JobStore.upsert_ats_board(...) -> bool` returns `True` only when the `(provider, board_identifier)` row is newly inserted.
- `JobStore.list_due_ats_boards(now: datetime) -> list[AtsRegistryEntry]`
- `JobStore.record_ats_scan_success(provider, board_identifier, now, job_count) -> None`
- `JobStore.record_ats_scan_failure(provider, board_identifier, now) -> None`
- `JobStore.record_ats_eligible_job(provider, board_identifier, now) -> None`
- `JobStore.count_ats_boards() -> int`

- [ ] **Step 1: Write failing schema/upsert tests**

Add to `tests/test_store.py`:

```python
def test_ats_registry_upsert_is_provider_board_unique():
    store = JobStore(":memory:")
    created = store.upsert_ats_board(
        provider="ashby",
        board_identifier="omnea",
        company_name="Omnea",
        market_hint="london",
    )
    repeated = store.upsert_ats_board(
        provider="ashby",
        board_identifier="omnea",
        company_name="Omnea Ltd",
        market_hint="london",
    )
    assert created is True
    assert repeated is False
    assert store.count_ats_boards() == 1


def test_ats_registry_rejects_unsupported_provider():
    with pytest.raises(ValueError, match="unsupported ATS provider"):
        JobStore(":memory:").upsert_ats_board(
            provider="workday", board_identifier="x"
        )
```

- [ ] **Step 2: Write failing health/rotation tests**

Use fixed aware UTC datetimes. Verify:

```python
store.record_ats_scan_success("lever", "acme", now, 12)
entry = store.list_due_ats_boards(now + timedelta(hours=1))[0]
assert entry.last_job_count == 12
assert entry.consecutive_failures == 0

store.record_ats_scan_failure("lever", "acme", now)
assert store.list_due_ats_boards(now + timedelta(hours=1)) == []
assert store.list_due_ats_boards(now + timedelta(hours=25))
```

A rediscovery/upsert of a paused board must reactivate it by clearing `paused_until` and setting `active=1`.

- [ ] **Step 3: Run red**

```bash
python -m pytest tests/test_store.py -q
```

- [ ] **Step 4: Add `AtsRegistryEntry`**

In `models.py`:

```python
@dataclass(frozen=True, slots=True)
class AtsRegistryEntry:
    provider: str
    board_identifier: str
    company_name: str
    market_hint: str
    first_seen_at: str
    last_seen_at: str
    last_checked_at: str | None
    last_success_at: str | None
    last_eligible_at: str | None
    last_job_count: int
    eligible_jobs_seen: int
    consecutive_failures: int
    active: bool
    paused_until: str | None
```

- [ ] **Step 5: Add SQLite table**

Create during `_init_db()`:

```sql
CREATE TABLE IF NOT EXISTS ats_registry (
    provider TEXT NOT NULL,
    board_identifier TEXT NOT NULL,
    company_name TEXT NOT NULL DEFAULT '',
    market_hint TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_success_at TEXT,
    last_eligible_at TEXT,
    last_job_count INTEGER NOT NULL DEFAULT 0,
    eligible_jobs_seen INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    paused_until TEXT,
    PRIMARY KEY(provider, board_identifier)
)
```

Supported providers remain exactly `ashby`, `lever`, `greenhouse`.

`record_ats_scan_failure()` sets `last_checked_at=now`, increments failures, and sets `paused_until=now+24h`. `record_ats_scan_success()` sets `last_checked_at`, `last_success_at`, `last_job_count`, clears pause, and resets failures. `list_due_ats_boards()` returns active rows whose pause is null/expired; final cap/order is handled by Task 3's pure selector.

- [ ] **Step 6: Run and commit**

```bash
python -m pytest tests/test_store.py -q
git add src/job_hunter/models.py src/job_hunter/store.py tests/test_store.py
git commit -m "feat: persist learned ATS boards"
```

---

### Task 3: Harvest ATS boards from every job before relevance filtering

**Files:**
- Create: `src/job_hunter/ats_registry.py`
- Modify: `src/job_hunter/discovery.py`
- Create: `tests/test_ats_registry.py`
- Modify: `tests/test_discovery.py`

**Interfaces:**
- `extract_ats_reference(job: Job) -> AtsReference | None`
- `harvest_ats_board(store: JobStore, job: Job, market_hint: str | None = None) -> bool`
- `select_ats_boards(entries, market_order: list[str], limit: int) -> list[AtsRegistryEntry]`

- [ ] **Step 1: Write pure extraction tests**

Cover explicit ATS fields first, then each URL field:

```python
@pytest.mark.parametrize(
    ("url", "provider", "board"),
    [
        ("https://jobs.ashbyhq.com/omnea/123", "ashby", "omnea"),
        ("https://jobs.lever.co/acme/abc", "lever", "acme"),
        ("https://boards.greenhouse.io/brex/jobs/999", "greenhouse", "brex"),
    ],
)
def test_extract_ats_reference_from_supported_url(url, provider, board):
    ref = extract_ats_reference(Job(source="feed", title="x", url=url))
    assert (ref.provider, ref.board) == (provider, board)
```

Verify precedence: populated `job.ats_provider/job.ats_board` wins; otherwise inspect `canonical_url`, then `url`, then `original_url`.

- [ ] **Step 2: Write board selection test**

Construct entries with `eligible_jobs_seen`, `market_hint`, and old/new `last_checked_at`. Assert `select_ats_boards(..., market_order=["germany_eu", "israel_remote", "london", ...], limit=3)` prioritizes:

1. entries with historical eligible jobs;
2. earlier market priority;
3. never/oldest checked first.

Use deterministic lexical `(provider, board_identifier)` as the final tie-breaker.

- [ ] **Step 3: Write discovery integration test proving harvest happens before prefilter**

Create a backend-only job that the current prefilter rejects but whose URL is `https://jobs.ashbyhq.com/example/backend-1`. Run `collect_candidates()`. Assert:

```python
assert result.eligible == []
assert store.count_ats_boards() == 1
assert store.list_due_ats_boards(now)[0].board_identifier == "example"
```

Add a second test where canonical resolution supplies an ATS reference and verify it is also harvested.

- [ ] **Step 4: Run red**

```bash
python -m pytest tests/test_ats_registry.py tests/test_discovery.py -q
```

- [ ] **Step 5: Implement harvesting**

`extract_ats_reference()` reuses `canonical.parse_supported_ats_url()`; do not duplicate URL parsing rules.

`harvest_ats_board()` writes:

```python
store.upsert_ats_board(
    provider=ref.provider,
    board_identifier=ref.board,
    company_name=job.company,
    market_hint=market_hint or job.market_hint or job.market_id or "",
)
```

Integrate it in `collect_candidates()`:

- for every raw job immediately after raw market attribution and before any prefilter/`needs_evaluation` exit;
- again immediately after canonical resolution mutates ATS/canonical fields;
- after a job survives prefilter, call `record_ats_eligible_job()` for its known board.

ATS harvesting failures must be caught/logged without dropping the job.

- [ ] **Step 6: Run and commit**

```bash
python -m pytest tests/test_ats_registry.py tests/test_discovery.py -q
git add src/job_hunter/ats_registry.py src/job_hunter/discovery.py tests/test_ats_registry.py tests/test_discovery.py
git commit -m "feat: learn ATS boards from discovered jobs"
```

---

### Task 4: Scan learned ATS boards through existing adapters

**Files:**
- Create: `src/job_hunter/sources/learned_ats.py`
- Modify: `src/job_hunter/sources/__init__.py`
- Create: `tests/test_learned_ats_source.py`
- Modify: `tests/test_sources.py`

**Interfaces:**
- `LearnedAtsStats(boards_scanned, boards_successful, boards_failed, jobs_raw)`
- `LearnedAtsSource(store, http, *, limit: int, market_order: list[str], now=utc_now)`
- `LearnedAtsSource.discover() -> list[Job]`

- [ ] **Step 1: Write failing learned-source success/failure tests**

Use a fake store with one Ashby, one Lever, and one Greenhouse registry entry. Use an HTTP fake that returns each adapter's existing JSON shape. Assert jobs preserve their native source labels (`ashby`, `lever`, `greenhouse`) and:

```python
assert source.stats.boards_scanned == 3
assert source.stats.boards_successful == 3
assert source.stats.jobs_raw == 3
```

Add a failing-board test asserting one failed HTTP call records only that board's failure and the next board still runs.

- [ ] **Step 2: Run red**

```bash
python -m pytest tests/test_learned_ats_source.py -q
```

- [ ] **Step 3: Implement the composite source**

Follow the existing `CompanyWatchSource` health-tracking pattern rather than changing Ashby/Lever/Greenhouse fail-open behavior. Define a private `_HealthTrackingHttp` wrapper in `learned_ats.py` whose `get_json()` stores the exception then re-raises; after the underlying adapter returns `[]`, inspect `tracked_http.error` to distinguish a legitimate empty board from transport failure.

Use:

```python
_ATS_SOURCE_TYPES = {
    "ashby": AshbySource,
    "lever": LeverSource,
    "greenhouse": GreenhouseSource,
}
```

At the start of `discover()`:

```python
entries = select_ats_boards(
    self._store.list_due_ats_boards(checked_at),
    self._market_order,
    self._limit,
)
```

For each board, instantiate the existing adapter with `board_identifier`. On success call `record_ats_scan_success(..., job_count=len(jobs))`; on tracked error call `record_ats_scan_failure(...)` and continue.

- [ ] **Step 4: Add to `build_sources()`**

When `max_learned_ats_boards_per_run > 0`, append exactly one `LearnedAtsSource` using configured market order:

```python
market_order = [m.id for m in settings.policy.markets if m.enabled]
```

Static `ats:` adapters remain separately instantiated as they are today; duplicates are harmless because existing dedupe/provenance collapses the jobs.

- [ ] **Step 5: Run and commit**

```bash
python -m pytest tests/test_learned_ats_source.py tests/test_sources.py -q
git add src/job_hunter/sources/learned_ats.py src/job_hunter/sources/__init__.py tests/test_learned_ats_source.py tests/test_sources.py
git commit -m "feat: scan learned ATS boards"
```

---

### Task 5: Add DevJobs as a real Israel source

**Files:**
- Create: `src/job_hunter/sources/devjobs.py`
- Create: `tests/test_devjobs_source.py`
- Modify: `src/job_hunter/sources/__init__.py`
- Modify: `tests/test_sources.py`

**Interfaces:**
- `DevJobsSource(http, *, max_jobs_per_category: int = 30)`
- Categories are exactly `Frontend` and `Full Stack`.
- Each emitted job uses `source="devjobs"` and `market_hint="israel_remote"`.

**Production URLs verified in September 2026:**

```text
https://www.devjobs.co.il/jobs-grid?developerTypes=Frontend
https://www.devjobs.co.il/jobs-grid?developerTypes=Full+Stack
```

Listing job links use `/job-details/<numeric-id>`. Detail pages are server-rendered and expose title/company/location/work mode/skills/description in ordinary HTML.

- [ ] **Step 1: Write a synthetic listing/detail fixture test**

Use minimal HTML shaped around stable semantics rather than copied full pages:

```html
<a href="/job-details/4458634930">Frontend Engineer</a>
```

and detail HTML with:

```html
<title>Frontend Engineer - Loora - Tel Aviv-Yafo | DevJobs</title>
<h3>Frontend Engineer</h3>
<div>Job Type Remote</div>
<div>Location Tel Aviv-Yafo</div>
<div>Skills</div><span>TypeScript</span><span>React</span>
<p>Build our Web products...</p>
```

Assert:

```python
job.source == "devjobs"
job.source_job_id == "4458634930"
job.title == "Frontend Engineer"
job.company == "Loora"
job.location == "Tel Aviv-Yafo"
job.remote is True
job.market_hint == "israel_remote"
assert "React" in job.description
```

Parameterized work-mode expectations: `Remote -> True`, `On-site -> False`, `Hybrid -> False` (Israel is remote-only, so explicit hybrid should be rejected cheaply downstream).

- [ ] **Step 2: Write network-failure and cap tests**

- listing failure returns `[]` and logs warning;
- one detail failure skips only that posting;
- `max_jobs_per_category=2` fetches at most two detail pages per category even if the listing has more links;
- duplicate detail links are deduped before fetching.

- [ ] **Step 3: Run red**

```bash
python -m pytest tests/test_devjobs_source.py -q
```

- [ ] **Step 4: Implement `DevJobsSource`**

Listing parser:

```python
for anchor in soup.select('a[href^="/job-details/"]'):
    match = re.fullmatch(r"/job-details/(\d+)", urlparse(anchor["href"]).path)
```

Deduplicate by numeric ID, preserve listing order (newest), cap before detail fetches.

Detail parser uses the document `<title>` to obtain title/company/location:

```python
raw = title_tag.get_text(" ", strip=True).removesuffix(" | DevJobs")
job_title, company, location = [part.strip() for part in raw.rsplit(" - ", 2)]
```

Determine work mode from normalized visible text around `Job Type` using exact values `Remote`, `Hybrid`, `On-site`; if unknown, `remote=None`.

Use the cleaned visible detail-page body as `description` so downstream salary/language/relevance checks see the actual posting text rather than only the listing card.

- [ ] **Step 5: Wire direct-source config**

In `build_sources()`, instantiate `DevJobsSource(http)` iff any enabled market declares `devjobs` in `direct_sources`. Dedupe source IDs so one adapter is created even if configured twice.

- [ ] **Step 6: Run and commit**

```bash
python -m pytest tests/test_devjobs_source.py tests/test_sources.py -q
git add src/job_hunter/sources/devjobs.py src/job_hunter/sources/__init__.py tests/test_devjobs_source.py tests/test_sources.py
git commit -m "feat: ingest Israel jobs from DevJobs"
```

---

### Task 6: Add Wellfound as a real Europe/London/US startup source

**Files:**
- Create: `src/job_hunter/sources/wellfound.py`
- Create: `tests/test_wellfound_source.py`
- Modify: `src/job_hunter/sources/__init__.py`
- Modify: `tests/test_sources.py`

**Interfaces:**
- `WellfoundListing(url: str, market_id: str)` dataclass.
- `WellfoundSource(http, listings: list[WellfoundListing], *, max_jobs_per_listing: int = 12)`.
- Each job uses `source="wellfound"` and the listing's `market_id` as `market_hint`.

Use exactly these Phase-1 listing routes:

```python
_WELLFOUND_LISTINGS = {
    "germany_eu": [
        "https://wellfound.com/role/l/frontend-engineer/europe",
        "https://wellfound.com/role/l/full-stack-engineer/europe",
    ],
    "london": [
        "https://wellfound.com/role/l/frontend-engineer/london",
        "https://wellfound.com/role/l/full-stack-engineer/london",
    ],
    "us_nyc_sf": [
        "https://wellfound.com/role/l/frontend-engineer/new-york",
        "https://wellfound.com/role/l/frontend-engineer/san-francisco",
        "https://wellfound.com/role/l/full-stack-engineer/new-york",
        "https://wellfound.com/role/l/full-stack-engineer/san-francisco",
    ],
}
```

These routes are server-rendered in current production; pagination uses `?page=N`. Phase 1 reads page 1 only and caps detail fetches per listing; page-2 support belongs to a later tuning task after runtime/source-yield measurement.

- [ ] **Step 1: Write listing/detail parser tests**

Synthetic listing HTML:

```html
<a href="/jobs/4639071-frontend-engineer">Frontend Engineer</a>
<a href="/jobs/2404013-senior-frontend-engineer-remote-europe">Senior Frontend Engineer - Remote Europe</a>
```

Detail fixture uses stable visible sections currently present on Wellfound:

```html
<title>Frontend Engineer at Omnea • London | Wellfound</title>
<h1>Frontend Engineer</h1>
<div>£90k – £160k</div>
<div>Full Time</div>
<div>Job Location</div><div>London</div>
<div>Visa Sponsorship</div><div>Not Available</div>
<div>Relocation Not Allowed</div>
<h2>About the job</h2><p>Own the design system and front-end architecture...</p>
```

Assert company parsing from `<title>`, job ID from URL, location, market hint, and that the full cleaned body becomes `description` so sponsorship/salary/work-mode text remains available to deterministic eligibility.

For remote pages, test `Remote Work Policy` + `Remote only` maps `remote=True`; explicit `In office` maps `False`; ambiguous/hybrid maps `None` and lets downstream market policy decide from text.

- [ ] **Step 2: Write isolation/cap/dedupe tests**

- duplicate `/jobs/<id>-...` links fetched once per source run;
- detail failure skips only one job;
- listing failure skips only that listing;
- `max_jobs_per_listing` bounds detail calls;
- the same Wellfound job found from two market listing routes may be emitted twice with different hints; existing cross-source/run dedupe later collapses by canonical URL, so do not invent a Wellfound-specific global dedupe that discards market evidence before discovery.

- [ ] **Step 3: Run red**

```bash
python -m pytest tests/test_wellfound_source.py -q
```

- [ ] **Step 4: Implement parser**

Listing links:

```python
_JOB_PATH_RE = re.compile(r"^/jobs/(\d+)-")
for anchor in soup.select('a[href^="/jobs/"]'):
    match = _JOB_PATH_RE.match(urlparse(anchor["href"]).path)
```

Detail company parser:

```python
# "Frontend Engineer at Omnea • London | Wellfound"
page_title = title_tag.get_text(" ", strip=True).removesuffix(" | Wellfound")
company = page_title.split(" at ", 1)[1].split(" • ", 1)[0].strip()
```

Prefer `<h1>` for job title. Use cleaned body text for description and simple heading/value extraction for `Job Location`/remote state. Keep salary/sponsorship text in description rather than adding new `Job` fields.

- [ ] **Step 5: Build listing definitions from enabled markets**

When `wellfound` appears in an enabled market's `direct_sources`, add only that market's routes from `_WELLFOUND_LISTINGS`. Instantiate one `WellfoundSource` with the combined listing list.

- [ ] **Step 6: Run and commit**

```bash
python -m pytest tests/test_wellfound_source.py tests/test_sources.py -q
git add src/job_hunter/sources/wellfound.py src/job_hunter/sources/__init__.py tests/test_wellfound_source.py tests/test_sources.py
git commit -m "feat: ingest startup jobs from Wellfound"
```

---

### Task 7: Make Brave source discovery scarce and remove DuckDuckGo market fallback

**Files:**
- Modify: `src/job_hunter/sources/__init__.py`
- Modify: `src/job_hunter/search_backend.py`
- Modify: `tests/test_sources.py`
- Modify: `tests/test_brave_budget.py`

**Interfaces:**
- Market targeted discovery uses `BraveSearchBackend` directly for the daily allocated subset.
- Non-selected market queries are **deferred/not run**, not sent to DuckDuckGo.
- Auxiliary `build_search_backend()` behavior used by canonical resolution remains unchanged unless a focused regression test proves an unavoidable coupling.

- [ ] **Step 1: Write failing source-builder tests**

With a Brave key and daily budget `2` out of `30` generated queries:

```python
sources = build_sources(...)
assert sum(isinstance(s, TargetedSearchSource) for s in sources) == 1
assert not any(isinstance(s, DuckDuckGoSource) for s in sources)
```

With no Brave key:

```python
assert not any(isinstance(s, (TargetedSearchSource, DuckDuckGoSource)) for s in sources)
```

Direct sources and learned ATS must still be present.

- [ ] **Step 2: Run red**

```bash
python -m pytest tests/test_sources.py tests/test_brave_budget.py -q
```

- [ ] **Step 3: Change market-search construction**

Keep `generate_search_queries()` producing the full planned pool. Use `split_queries_for_brave()` only to choose today's metered slice:

```python
brave_queries, deferred_queries = split_queries_for_brave(
    queries,
    limit=brave_limit,
)
```

Create `TargetedSearchSource(BraveSearchBackend(...), brave_queries, ...)` directly. Do **not** create `DuckDuckGoSource` from `deferred_queries`.

Change budget log wording to:

```text
Brave source-discovery budget: monthly_limit=250 available_today=9 selected=9 deferred=21
```

If no key or no daily allowance, log selected/deferred counts and continue with direct/ATS sources.

The Brave source still returns discovered job URLs into normal discovery. Task 3 then converts supported Ashby/Lever/Greenhouse hits into persistent boards, creating recurring value from a single Brave request.

- [ ] **Step 4: Run and commit**

```bash
python -m pytest tests/test_sources.py tests/test_brave_budget.py tests/test_first_run_hardening.py -q
git add src/job_hunter/sources/__init__.py src/job_hunter/search_backend.py tests/test_sources.py tests/test_brave_budget.py
git commit -m "fix: stop relying on DuckDuckGo market discovery"
```

---

### Task 8: Add source-quality and ATS-registry telemetry

**Files:**
- Modify: `src/job_hunter/discovery.py`
- Modify: `src/job_hunter/pipeline.py`
- Modify: `tests/test_discovery.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- `DiscoveryStats.unique_by_source`, `rejected_by_source`, `eligible_by_source`.
- One final `source_quality` line per bounded source label.
- One final `ats_registry` line from persisted/source-run state.

- [ ] **Step 1: Write discovery source-counter tests**

Given three unique jobs from `devjobs`, with one eligible, one profession reject, and one market/prefilter reject, assert:

```python
assert stats.unique_by_source == {"devjobs": 3}
assert stats.eligible_by_source == {"devjobs": 1}
assert stats.rejected_by_source == {"devjobs": 2}
```

Use `metric_source_label()` for bounded labels.

- [ ] **Step 2: Write final-log test**

Run a small pipeline with `devjobs`, `wellfound`, and an ATS job. Capture logs and assert fields exist:

```text
source_quality source=devjobs raw=... unique=... rejected=... eligible=... selected=... high_priority=... package_match=... possible_match=... skip=... blocked=... delivered=...
```

and:

```text
ats_registry total=... discovered=... scanned=... successful=... failed=... jobs_raw=...
```

Do not assert source log ordering except that it is deterministic (alphabetical label is acceptable).

- [ ] **Step 3: Run red**

```bash
python -m pytest tests/test_discovery.py tests/test_pipeline.py -q
```

- [ ] **Step 4: Implement source counters**

During discovery, increment per-source counters at the same stages as market counters. After dedupe, the representative job's bounded source label owns `unique/rejected/eligible`; provenance remains separately preserved in `job_sources` as today.

In pipeline, add helpers analogous to `_record_decision()` but keyed by bounded source label. Count `selected` from the selected shortlist and `delivered` only after actual Telegram persistence, matching the corrected market metric semantics.

For ATS telemetry:

- `total = store.count_ats_boards()` at end of run;
- `discovered` = count of newly inserted ATS boards during this run (add an integer to `DiscoveryStats` and increment from `harvest_ats_board()` return value);
- `scanned/successful/failed/jobs_raw` = `LearnedAtsSource.stats` from the source instance used this run.

- [ ] **Step 5: Run and commit**

```bash
python -m pytest tests/test_discovery.py tests/test_pipeline.py -q
git add src/job_hunter/discovery.py src/job_hunter/pipeline.py tests/test_discovery.py tests/test_pipeline.py
git commit -m "feat: report source discovery quality"
```

---

### Task 9: Full regression and production-readiness verification

**Files:**
- Review only unless a regression requires a scoped fix.

- [ ] **Step 1: Run the complete test suite**

```bash
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run targeted source/config regression groups explicitly**

```bash
python -m pytest \
  tests/test_config.py \
  tests/test_discovery_queries.py \
  tests/test_store.py \
  tests/test_ats_registry.py \
  tests/test_learned_ats_source.py \
  tests/test_devjobs_source.py \
  tests/test_wellfound_source.py \
  tests/test_sources.py \
  tests/test_discovery.py \
  tests/test_pipeline.py -q
```

- [ ] **Step 3: Verify no out-of-scope policy drift**

```bash
git diff main...HEAD -- config/search.yml src/job_hunter/models.py src/job_hunter/config.py
```

Confirm the diff does **not** change:

- market order/query shares;
- salary floors;
- allowed languages;
- sponsorship/relocation/remote policies;
- target/blocked role behavior;
- `max_jobs_per_run`;
- Gemini model/quota environment handling.

- [ ] **Step 4: Verify source construction in a dry-run-style unit test**

The production-shaped source list must contain all legacy sources plus:

```text
DevJobsSource
WellfoundSource
LearnedAtsSource
```

and must not contain `DuckDuckGoSource` solely for market query fallback.

- [ ] **Step 5: Final implementation commit only if verification required fixes**

```bash
git status --short
```

If clean, do not create an empty commit. If a verification fix was necessary, rerun the full suite and commit that scoped fix.

---

## Production Validation After Merge

Unit tests prove behavior but do not prove source yield. The first production run after merge should be judged by these logs:

```text
discovery source contribution: ... devjobs=N wellfound=N ashby=N lever=N greenhouse=N ...
ats_registry total=N discovered=N scanned=N successful=N failed=N jobs_raw=N
source_quality source=devjobs ... eligible=N ... delivered=N
source_quality source=wellfound ... eligible=N ... delivered=N
```

Success signals for the first few runs:

1. `devjobs` and `wellfound` contribute non-zero raw jobs without Brave.
2. `ats_registry total` grows from jobs already present in broad feeds/direct sources.
3. A later run scans learned ATS boards even when Brave daily allowance is zero.
4. At least one learned ATS board eventually produces a job not present in the source that first revealed that board.
5. Market coverage no longer depends on `search_results` being non-zero.

Do not tune market shares or prefilter strictness from the first run alone; first establish that the new source layer is supplying genuinely new jobs.
