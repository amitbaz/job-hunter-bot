# Market-Driven Search Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-size-fits-all Europe/remote search policy with ordered, market-specific discovery and evaluation rules that broaden source coverage while keeping Gemini usage bounded.

**Architecture:** Add first-class `MarketPolicy` configuration under `SearchPolicy`, generate market-tagged targeted-search queries within the existing global query cap, attribute each logical job to one primary market after enrichment/canonicalization, and feed that market policy into deterministic eligibility, ranking, Gemini evaluation, Telegram notes, and per-market logging. Existing R2 canonicalization, provenance, company watch, Gmail, shortlist limits, cover-letter generation, and Telegram navigation remain intact.

**Tech Stack:** Python 3.12+, dataclasses, PyYAML, SQLite, requests/BeautifulSoup, pytest, existing Gemini/Telegram pipeline.

**Spec:** `docs/superpowers/specs/2026-09-02-market-driven-search-strategy-design.md`

## Global Constraints

- Market list order in `config/search.yml` is the single source of truth for geographic priority.
- Initial market order is `germany_eu`, `israel_remote`, `london`, `singapore`, `us_nyc_sf`, `secondary_eu_relocation`.
- Initial query shares are `0.35`, `0.25`, `0.17`, `0.10`, `0.10`, `0.03` in that order.
- `max_search_queries_per_run` remains the global public-search cap; market expansion must not bypass it.
- Every enabled market receives at least one query when the budget is at least the number of enabled markets.
- Salary thresholds are gross base salary floors, not FX conversions: Germany/EU EUR 90,000; Israel ILS 420,000/year (ILS 35,000/month); London GBP 90,000; Singapore SGD 120,000; NYC USD 180,000; San Francisco/Bay Area USD 200,000; Amsterdam EUR 90,000; Paris EUR 80,000; Barcelona EUR 70,000.
- Allowed required languages are English only for Germany/EU, London, Singapore, US, and secondary EU relocation; Israel allows Hebrew or English.
- Unknown salary, sponsorship, international-remote eligibility, relocation detail, and time-zone overlap are not blockers merely because they are unknown.
- Israel is remote-only; explicit Israel onsite/hybrid or required physical residence in Israel is a blocker.
- London, Singapore, NYC, and SF allow relocation; sponsorship is required, explicit no-sponsorship is a blocker, and omitted sponsorship remains unknown.
- Permanent full-time employment is required; explicit freelance, contractor, internship, part-time, temporary, or fixed-term-only roles are out of scope.
- Full-Stack discovery includes frontend-heavy and learnable-backend roles, including selected mid-level transition roles; pure backend and backend-dominant roles must not flood the shortlist.
- Engineering Manager and people-management-heavy roles remain blocked; hands-on Architect roles remain in scope.
- Company size and industry remain unfiltered.
- New market-specific source coverage uses targeted public search in this iteration; do not add authenticated scraping or a catalog of new direct scrapers.
- Existing `max_jobs_per_run` remains the fresh Gemini shortlist cap.
- Do not change Gemini client/quota plumbing or model selection as part of this feature. Production continues to run the currently configured Gemini 3.5 Flash-Lite model via `GEMINI_MODEL`.
- Supabase/Postgres migration is out of scope; this implementation remains SQLite-first.
- Preserve backward-compatible legacy search config only when `markets` is absent. When `markets` exists, market-driven query generation is authoritative and legacy global query fields must not also generate a second contradictory search pool.

---

## File Structure

### New files

- `src/job_hunter/market_policy.py` — market lookup, market attribution, city-specific salary-floor resolution, and market ordering helpers.
- `src/job_hunter/market_eligibility.py` — conservative deterministic employment/language/salary/sponsorship/work-mode checks and unknown-status extraction.
- `tests/test_market_policy.py` — attribution, salary-floor, and market-order tests.
- `tests/test_market_eligibility.py` — deterministic blocker/unknown behavior tests.

### Existing files to modify

- `config/search.yml` — authoritative ordered market configuration, market source domains/query templates, broadened role families.
- `src/job_hunter/models.py` — `SalaryPolicy`, `MarketPolicy`, `SearchQuery`, market IDs/hints on job/evaluation/delivery models, `SearchPolicy.markets`.
- `src/job_hunter/config.py` — strict market-config parsing/validation and legacy fallback.
- `src/job_hunter/discovery_queries.py` — market query allocation, deterministic rotation, legacy fallback.
- `src/job_hunter/sources/duckduckgo.py` — consume market-tagged queries and retain query-market hints/statistics.
- `src/job_hunter/sources/__init__.py` — build DuckDuckGo source from market-tagged queries and accept a deterministic query date.
- `src/job_hunter/store.py` — migrate/persist `market_id` on jobs/evaluations and restore it on reads.
- `src/job_hunter/discovery.py` — attribute market after enrichment, apply market-aware prefiltering, preserve attribution after canonical resolution, collect per-market discovery counters.
- `src/job_hunter/prefilter.py` — remove global remote-only blocker and delegate explicit market incompatibilities to market eligibility.
- `src/job_hunter/ranking.py` — market-aware location fit, modest market-priority bonus, targeted-source quality, Full-Stack backend-heavy penalty.
- `src/job_hunter/evaluation.py` — structured market-aware Gemini prompt with market-specific blockers and backend-transition context.
- `src/job_hunter/pipeline.py` — deterministic local query date, per-market shortlist/outcome/delivery metrics, market notes on digest/navigation items.
- `src/job_hunter/telegram_navigation.py` — show evaluation/market notes such as sponsorship unknown or time-zone overlap.
- `tests/test_config.py`
- `tests/test_discovery_queries.py`
- `tests/test_prefilter.py`
- `tests/test_ranking.py`
- `tests/test_evaluation.py`
- `tests/test_store.py`
- `tests/test_discovery.py`
- `tests/test_pipeline.py`
- `tests/test_sources.py` or the existing DuckDuckGo-specific test file if one already exists.
- `tests/test_telegram_navigation.py`
- `README.md` — document market config and tuning workflow.

---

### Task 1: Add first-class market policy models and strict config parsing

**Files:**
- Modify: `src/job_hunter/models.py`
- Modify: `src/job_hunter/config.py`
- Modify: `config/search.yml`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `SalaryPolicy`, `MarketPolicy`, `SearchQuery`, `SearchPolicy.markets`.
- Produces: `MarketPolicy.id`, `.query_share`, `.locations`, `.allowed_languages`, `.salary`, `.remote_policy`, `.relocation_policy`, `.sponsorship_policy`, `.source_domains`, `.query_templates`, `.role_families`, `.enabled`.
- Preserves: existing `SearchPolicy.salary_floor_eur`, legacy global query fields, and legacy test fixtures when `markets` is absent.

- [ ] **Step 1: Write failing config/model tests for ordered market parsing and validation**

Add tests to `tests/test_config.py` that build a minimal YAML with two markets and assert order, nested salary values, and validation failures:

```python
def test_load_settings_parses_markets_in_declared_order(monkeypatch, tmp_path):
    _set_required_bot_env(monkeypatch)
    cfg = tmp_path / "search.yml"
    cfg.write_text(
        "thresholds:\n  package: 75\n  possible: 65\n"
        "target_titles: []\npositive_keywords: []\nblocked_title_keywords: []\n"
        "markets:\n"
        "  - id: germany_eu\n"
        "    query_share: 0.35\n"
        "    locations: [Berlin, Germany, Europe]\n"
        "    allowed_languages: [English]\n"
        "    salary:\n"
        "      currency: EUR\n"
        "      gross_base_floor: 90000\n"
        "    remote_policy: preferred\n"
        "    relocation_policy: selective\n"
        "    sponsorship_policy: not_required\n"
        "    source_domains: [wellfound.com]\n"
        "    query_templates: ['\"{role}\" remote Europe']\n"
        "  - id: israel_remote\n"
        "    query_share: 0.25\n"
        "    locations: [Israel, Tel Aviv]\n"
        "    allowed_languages: [English, Hebrew]\n"
        "    salary:\n"
        "      currency: ILS\n"
        "      gross_base_floor: 420000\n"
        "    remote_policy: required\n"
        "    relocation_policy: none\n"
        "    sponsorship_policy: not_required\n"
    )

    settings = load_settings(cfg)

    assert [market.id for market in settings.policy.markets] == [
        "germany_eu",
        "israel_remote",
    ]
    assert settings.policy.markets[1].allowed_languages == ["English", "Hebrew"]
    assert settings.policy.markets[1].salary.currency == "ILS"
    assert settings.policy.markets[1].salary.gross_base_floor == 420000


def test_load_settings_rejects_duplicate_market_ids(monkeypatch, tmp_path):
    _set_required_bot_env(monkeypatch)
    cfg = tmp_path / "search.yml"
    cfg.write_text(
        "thresholds: {package: 75, possible: 65}\n"
        "target_titles: []\npositive_keywords: []\nblocked_title_keywords: []\n"
        "markets:\n"
        "  - id: london\n"
        "    query_share: 0.5\n"
        "    locations: [London]\n"
        "    allowed_languages: [English]\n"
        "    salary: {currency: GBP, gross_base_floor: 90000}\n"
        "    remote_policy: allowed\n"
        "    relocation_policy: allowed\n"
        "    sponsorship_policy: required\n"
        "  - id: london\n"
        "    query_share: 0.5\n"
        "    locations: [London]\n"
        "    allowed_languages: [English]\n"
        "    salary: {currency: GBP, gross_base_floor: 90000}\n"
        "    remote_policy: allowed\n"
        "    relocation_policy: allowed\n"
        "    sponsorship_policy: required\n"
    )

    with pytest.raises(ValueError, match="duplicate market id: london"):
        load_settings(cfg)
```

Also parameterize invalid `query_share < 0`, empty currency, non-positive floor, invalid remote/relocation/sponsorship enum, and duplicate IDs.

- [ ] **Step 2: Run the config tests and verify they fail**

Run:

```bash
python -m pytest tests/test_config.py -q
```

Expected: the new market tests fail because `SearchPolicy` has no `markets` field and `load_settings()` does not parse `markets`.

- [ ] **Step 3: Add the market dataclasses and parser**

Add to `src/job_hunter/models.py` before `SearchPolicy`:

```python
@dataclass(slots=True)
class SalaryPolicy:
    currency: str
    gross_base_floor: int
    location_floors: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class MarketPolicy:
    id: str
    query_share: float
    locations: list[str]
    allowed_languages: list[str]
    salary: SalaryPolicy
    remote_policy: str
    relocation_policy: str
    sponsorship_policy: str
    source_domains: list[str] = field(default_factory=list)
    query_templates: list[str] = field(default_factory=list)
    role_families: list[str] = field(default_factory=list)
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class SearchQuery:
    text: str
    market_id: str | None = None
```

Add to `SearchPolicy`:

```python
    markets: list[MarketPolicy] = field(default_factory=list)
```

In `src/job_hunter/config.py`, import `MarketPolicy` and `SalaryPolicy`, add `_parse_markets(entries)`, and call it from `load_settings()`:

```python
markets=_parse_markets(data.get("markets", [])),
```

Validation must enforce these exact enum sets:

```python
_REMOTE_POLICIES = {"preferred", "required", "allowed"}
_RELOCATION_POLICIES = {"none", "selective", "allowed"}
_SPONSORSHIP_POLICIES = {"not_required", "required"}
```

Reject duplicate IDs, negative shares, empty required strings/lists, non-positive base/location floors, non-mapping salary blocks, and unknown fields inside a market/salary block. Preserve the existing behavior when `markets` is absent.

- [ ] **Step 4: Replace the production config with the approved ordered market strategy**

Update `config/search.yml` so the current global role/search fields no longer drive production discovery when `markets` exists. Keep legacy fields only if tests/local fallback still read them; set the market block as the authoritative production configuration.

Use this role order globally:

```yaml
role_families:
  - senior frontend engineer
  - staff frontend engineer
  - frontend technical lead
  - frontend lead
  - senior product engineer
  - product engineer
  - frontend architect
  - software architect
  - senior full-stack engineer
  - full-stack product engineer
  - full-stack engineer
  - ai product engineer
```

Keep `engineering manager` blocked and add pure backend title phrases to `blocked_profession_title_phrases`:

```yaml
  - backend engineer
  - back-end engineer
  - backend developer
  - back-end developer
```

Define these six markets in this order with these values:

```yaml
markets:
  - id: germany_eu
    query_share: 0.35
    locations: [Berlin, Germany, Europe, European Union, EU, EMEA]
    allowed_languages: [English]
    salary:
      currency: EUR
      gross_base_floor: 90000
    remote_policy: preferred
    relocation_policy: selective
    sponsorship_policy: not_required
    source_domains:
      - wellfound.com
      - welcometothejungle.com
      - app.welcometothejungle.com
      - jobs.ashbyhq.com
      - jobs.lever.co
      - boards.greenhouse.io
      - ycombinator.com
    query_templates:
      - '"{role}" remote Germany React TypeScript'
      - '"{role}" remote Europe React TypeScript'
      - '"{role}" Berlin React TypeScript'

  - id: israel_remote
    query_share: 0.25
    locations: [Israel, Tel Aviv, Tel Aviv-Yafo]
    allowed_languages: [English, Hebrew]
    salary:
      currency: ILS
      gross_base_floor: 420000
    remote_policy: required
    relocation_policy: none
    sponsorship_policy: not_required
    source_domains:
      - jobs.techaviv.com
      - devjobs.co.il
      - gotfriends.co.il
      - startup.jobs
      - jobs.ashbyhq.com
      - jobs.lever.co
      - boards.greenhouse.io
      - ycombinator.com
    query_templates:
      - '"{role}" Israel remote React TypeScript'
      - '"{role}" Tel Aviv remote React TypeScript'
      - '"{role}" Israel remote'

  - id: london
    query_share: 0.17
    locations: [London, United Kingdom, UK]
    allowed_languages: [English]
    salary:
      currency: GBP
      gross_base_floor: 90000
    remote_policy: allowed
    relocation_policy: allowed
    sponsorship_policy: required
    source_domains:
      - workvisajobs.co.uk
      - welcometothejungle.com
      - builtin.com
      - wellfound.com
      - ycombinator.com
      - jobs.ashbyhq.com
      - jobs.lever.co
      - boards.greenhouse.io
    query_templates:
      - '"{role}" London React TypeScript'
      - '"{role}" London visa sponsorship'
      - '"{role}" UK visa sponsorship React TypeScript'

  - id: singapore
    query_share: 0.10
    locations: [Singapore]
    allowed_languages: [English]
    salary:
      currency: SGD
      gross_base_floor: 120000
    remote_policy: allowed
    relocation_policy: allowed
    sponsorship_policy: required
    source_domains:
      - nodeflair.com
      - sg.jobstreet.com
      - glints.com
      - mycareersfuture.gov.sg
      - builtin.com
      - jobs.ashbyhq.com
      - jobs.lever.co
      - boards.greenhouse.io
    query_templates:
      - '"{role}" Singapore React TypeScript'
      - '"{role}" Singapore visa sponsorship'

  - id: us_nyc_sf
    query_share: 0.10
    locations: [New York, New York City, NYC, San Francisco, Bay Area]
    allowed_languages: [English]
    salary:
      currency: USD
      gross_base_floor: 180000
      location_floors:
        San Francisco: 200000
        Bay Area: 200000
        New York: 180000
        New York City: 180000
        NYC: 180000
    remote_policy: allowed
    relocation_policy: allowed
    sponsorship_policy: required
    source_domains:
      - builtin.com
      - wellfound.com
      - ycombinator.com
      - startup.jobs
      - jobs.ashbyhq.com
      - jobs.lever.co
      - boards.greenhouse.io
    query_templates:
      - '"{role}" New York React TypeScript'
      - '"{role}" New York visa sponsorship'
      - '"{role}" San Francisco React TypeScript'
      - '"{role}" San Francisco visa sponsorship'

  - id: secondary_eu_relocation
    query_share: 0.03
    locations: [Amsterdam, Paris, Barcelona]
    allowed_languages: [English]
    salary:
      currency: EUR
      gross_base_floor: 70000
      location_floors:
        Amsterdam: 90000
        Paris: 80000
        Barcelona: 70000
    remote_policy: allowed
    relocation_policy: selective
    sponsorship_policy: not_required
    source_domains:
      - wellfound.com
      - welcometothejungle.com
      - jobs.ashbyhq.com
      - jobs.lever.co
      - boards.greenhouse.io
    query_templates:
      - '"{role}" Amsterdam English React TypeScript'
      - '"{role}" Paris English React TypeScript'
      - '"{role}" Barcelona English React TypeScript'
```

Do not add new direct source adapters in this task; these domains are targeted-search inputs.

- [ ] **Step 5: Run config tests and commit**

Run:

```bash
python -m pytest tests/test_config.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/models.py src/job_hunter/config.py config/search.yml tests/test_config.py
git commit -m "feat: add market search policy config"
```

---

### Task 2: Generate bounded, rotating, market-tagged targeted-search queries

**Files:**
- Modify: `src/job_hunter/discovery_queries.py`
- Modify: `src/job_hunter/sources/duckduckgo.py`
- Modify: `src/job_hunter/sources/__init__.py`
- Modify: `tests/test_discovery_queries.py`
- Modify/Create: DuckDuckGo source tests in the existing source-test file.

**Interfaces:**
- Consumes: `SearchPolicy.markets`, global `SearchPolicy.role_families`, `MarketPolicy.role_families`, `MarketPolicy.query_templates`, `MarketPolicy.source_domains`.
- Produces: `allocate_market_query_slots(markets: list[MarketPolicy], budget: int) -> dict[str, int]`.
- Produces: `generate_search_queries(policy: SearchPolicy, run_date: date | None = None) -> list[SearchQuery]`.
- Produces: `DuckDuckGoSource.stats.planned_by_market`, `.attempted_by_market`, `.succeeded_by_market`.
- Produces: `Job.market_hint` from the query that discovered the result.

- [ ] **Step 1: Rewrite discovery-query tests around `SearchQuery` and add allocation/rotation tests**

Add these tests to `tests/test_discovery_queries.py`:

```python
from datetime import date

from job_hunter.discovery_queries import allocate_market_query_slots, generate_search_queries
from job_hunter.models import MarketPolicy, SalaryPolicy, SearchPolicy


def market(market_id, share):
    return MarketPolicy(
        id=market_id,
        query_share=share,
        locations=[market_id],
        allowed_languages=["English"],
        salary=SalaryPolicy(currency="EUR", gross_base_floor=90000),
        remote_policy="allowed",
        relocation_policy="allowed",
        sponsorship_policy="not_required",
        source_domains=["wellfound.com", "jobs.ashbyhq.com"],
        query_templates=['"{role}" React TypeScript'],
    )


def test_allocate_market_query_slots_matches_initial_30_query_shape():
    markets = [
        market("germany_eu", 0.35),
        market("israel_remote", 0.25),
        market("london", 0.17),
        market("singapore", 0.10),
        market("us_nyc_sf", 0.10),
        market("secondary_eu_relocation", 0.03),
    ]

    allocation = allocate_market_query_slots(markets, 30)

    assert allocation == {
        "germany_eu": 10,
        "israel_remote": 8,
        "london": 5,
        "singapore": 3,
        "us_nyc_sf": 3,
        "secondary_eu_relocation": 1,
    }
    assert sum(allocation.values()) == 30


def test_allocation_guarantees_priority_order_when_budget_is_smaller_than_market_count():
    markets = [market("one", 0.4), market("two", 0.3), market("three", 0.3)]
    assert allocate_market_query_slots(markets, 2) == {"one": 1, "two": 1, "three": 0}


def test_market_query_rotation_is_stable_per_day_and_changes_across_days():
    policy = make_market_policy(limit=8)
    first = generate_search_queries(policy, date(2026, 9, 2))
    repeated = generate_search_queries(policy, date(2026, 9, 2))
    next_day = generate_search_queries(policy, date(2026, 9, 3))

    assert first == repeated
    assert first != next_day
    assert len({query.text for query in first}) == len(first)
    assert all(query.market_id for query in first)
```

Keep legacy tests, but update their expected values to `query.text` and `query.market_id is None` when `policy.markets` is empty.

- [ ] **Step 2: Run query tests and verify failure**

Run:

```bash
python -m pytest tests/test_discovery_queries.py -q
```

Expected: FAIL because market allocation/tagging does not exist and current query generation returns strings.

- [ ] **Step 3: Implement deterministic allocation and rotation**

In `src/job_hunter/discovery_queries.py`, preserve the existing code as `_generate_legacy_search_queries(policy) -> list[SearchQuery]`, then make markets authoritative when present.

Implement allocation with `round(share * budget)` as the first pass so the approved 30-query split resolves to `10/8/5/3/3/1`, then deterministically correct any total mismatch without dropping an enabled market below one when the budget allows it:

```python
def allocate_market_query_slots(markets: list[MarketPolicy], budget: int) -> dict[str, int]:
    enabled = [market for market in markets if market.enabled]
    allocation = {market.id: 0 for market in markets}
    if budget <= 0 or not enabled:
        return allocation
    if budget < len(enabled):
        for market in enabled[:budget]:
            allocation[market.id] = 1
        return allocation

    expected = {market.id: market.query_share * budget for market in enabled}
    for market in enabled:
        allocation[market.id] = max(1, round(expected[market.id]))

    while sum(allocation.values()) > budget:
        candidates = [market for market in enabled if allocation[market.id] > 1]
        market = max(
            candidates,
            key=lambda item: (
                allocation[item.id] - expected[item.id],
                -enabled.index(item),
            ),
        )
        allocation[market.id] -= 1

    while sum(allocation.values()) < budget:
        market = max(
            enabled,
            key=lambda item: (
                expected[item.id] - allocation[item.id],
                -enabled.index(item),
            ),
        )
        allocation[market.id] += 1

    return allocation
```

For each market, build unique direct and `site:` candidates from ordered roles/templates/domains. Rank candidate combinations first by ordered role/template priority and then by a stable SHA-256 day seed so equal-priority combinations rotate:

```python
def _rotation_key(run_date: date, market_id: str, text: str) -> str:
    payload = f"{run_date.isoformat()}:{market_id}:{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

Use `market.role_families or policy.role_families`. Return exactly the allocated unique count when enough candidates exist, never exceed `policy.max_search_queries_per_run`, and preserve market list order in the final concatenated query list.

- [ ] **Step 4: Make DuckDuckGo preserve market hints and query execution counts**

Add `market_hint` and `market_id` to `Job` in `models.py` if not already added in Task 1:

```python
    market_hint: str | None = None
    market_id: str | None = None
```

Add a compact stats dataclass in `sources/duckduckgo.py`:

```python
@dataclass(slots=True)
class DuckDuckGoStats:
    planned_by_market: dict[str, int] = field(default_factory=dict)
    attempted_by_market: dict[str, int] = field(default_factory=dict)
    succeeded_by_market: dict[str, int] = field(default_factory=dict)
```

Change `DuckDuckGoSource.__init__` to accept `list[SearchQuery]`. For backward-compatible directly constructed test sources, normalize raw strings to `SearchQuery(text=raw)` inside `__init__`.

On each query:

```python
market_key = query.market_id or "legacy"
self.stats.attempted_by_market[market_key] = self.stats.attempted_by_market.get(market_key, 0) + 1
response = self._http.get(_URL, params={"q": query.text})
...
self.stats.succeeded_by_market[market_key] = self.stats.succeeded_by_market.get(market_key, 0) + 1
...
Job(
    source="duckduckgo",
    title=title,
    url=canonicalize_url(href),
    market_hint=query.market_id,
)
```

Never log job-description content in these stats.

- [ ] **Step 5: Pass a deterministic local date into source construction**

Change `build_sources` to accept:

```python
def build_sources(
    settings: Settings,
    http,
    search_breaker: CircuitBreaker | None = None,
    query_date: date | None = None,
) -> list[JobSource]:
```

Use `generate_search_queries(settings.policy, query_date)` for DuckDuckGo. Task 8 will make `run_pipeline()` pass the Europe/Berlin-local configured date.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
python -m pytest tests/test_discovery_queries.py tests/test_sources.py -q
```

If the repository has no `tests/test_sources.py`, run the existing DuckDuckGo-specific test file instead.

Expected: PASS.

Commit:

```bash
git add src/job_hunter/models.py src/job_hunter/discovery_queries.py src/job_hunter/sources/duckduckgo.py src/job_hunter/sources/__init__.py tests/test_discovery_queries.py tests
git commit -m "feat: allocate market search queries"
```

---

### Task 3: Attribute one primary market and persist it with the logical job/evaluation

**Files:**
- Create: `src/job_hunter/market_policy.py`
- Modify: `src/job_hunter/models.py`
- Modify: `src/job_hunter/store.py`
- Create: `tests/test_market_policy.py`
- Modify: `tests/test_store.py`

**Interfaces:**
- Produces: `market_by_id(policy: SearchPolicy, market_id: str | None) -> MarketPolicy | None`.
- Produces: `salary_floor_for_job(job: Job, market: MarketPolicy) -> int`.
- Produces: `attribute_market(job: Job, markets: list[MarketPolicy]) -> str | None`.
- Produces: `JobStore.set_job_market(job_id: int, market_id: str | None) -> None`.
- Persists: `jobs.market_id` and `evaluations.market_id`.

- [ ] **Step 1: Write attribution/salary tests**

Create `tests/test_market_policy.py` with explicit spec examples:

```python
def test_london_hybrid_maps_to_london(policy):
    job = Job(
        source="wellfound",
        title="Senior Frontend Engineer",
        location="London, UK - Hybrid",
        remote=False,
    )
    assert attribute_market(job, policy.markets) == "london"


def test_remote_germany_beats_uk_company_hint(policy):
    job = Job(
        source="duckduckgo",
        title="Senior Frontend Engineer",
        company="London Company",
        location="Remote - Germany",
        remote=True,
        market_hint="london",
    )
    assert attribute_market(job, policy.markets) == "germany_eu"


def test_israel_remote_with_unknown_international_scope_maps_to_israel(policy):
    job = Job(
        source="duckduckgo",
        title="Senior Frontend Engineer",
        location="Israel - Remote",
        remote=True,
        market_hint="israel_remote",
    )
    assert attribute_market(job, policy.markets) == "israel_remote"


def test_city_specific_salary_floors(policy):
    us = market_by_id(policy, "us_nyc_sf")
    secondary = market_by_id(policy, "secondary_eu_relocation")
    assert salary_floor_for_job(Job(source="x", title="x", location="New York City"), us) == 180000
    assert salary_floor_for_job(Job(source="x", title="x", location="San Francisco Bay Area"), us) == 200000
    assert salary_floor_for_job(Job(source="x", title="x", location="Amsterdam"), secondary) == 90000
    assert salary_floor_for_job(Job(source="x", title="x", location="Paris"), secondary) == 80000
    assert salary_floor_for_job(Job(source="x", title="x", location="Barcelona"), secondary) == 70000
```

Also test Singapore onsite -> `singapore`, Paris -> secondary EU, ambiguous `Remote Europe` -> `germany_eu`, and actual job location beats `market_hint`.

- [ ] **Step 2: Write store migration/round-trip tests**

Add to `tests/test_store.py`:

```python
def test_market_schema_upgrades_existing_jobs_and_evaluations(tmp_path):
    db = tmp_path / "state.sqlite3"
    _create_r1_jobs_only_db(db)
    store = JobStore(db)

    job_columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(jobs)")}
    evaluation_columns = {
        row["name"] for row in store._conn.execute("PRAGMA table_info(evaluations)")
    }
    assert "market_id" in job_columns
    assert "market_id" in evaluation_columns


def test_job_market_round_trip(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job_id, _, _ = store.upsert_logical_job(
        Job(source="x", title="Senior Frontend Engineer", location="London")
    )
    store.set_job_market(job_id, "london")

    loaded = store.get_job(job_id)

    assert loaded.market_id == "london"
```

Add `market_id="london"` to a saved `Evaluation` and assert `get_evaluation()` returns it.

- [ ] **Step 3: Run attribution/store tests and verify failure**

Run:

```bash
python -m pytest tests/test_market_policy.py tests/test_store.py -q
```

Expected: FAIL because attribution helpers and persistence columns do not exist.

- [ ] **Step 4: Implement market attribution with explicit-evidence precedence**

Create `src/job_hunter/market_policy.py`.

Use normalized substring evidence in this order: explicit `job.location`, explicit remote-country/region language in `location + description`, sponsorship/relocation market terms, `job.market_hint`, then first compatible market in configured order.

A compact implementation shape:

```python
def market_by_id(policy: SearchPolicy, market_id: str | None) -> MarketPolicy | None:
    if not market_id:
        return None
    return next((market for market in policy.markets if market.id == market_id), None)


def salary_floor_for_job(job: Job, market: MarketPolicy) -> int:
    location = normalize_text(job.location or "")
    best = market.salary.gross_base_floor
    for term, floor in market.salary.location_floors.items():
        if normalize_text(term) in location:
            best = max(best, floor)
    return best


def attribute_market(job: Job, markets: list[MarketPolicy]) -> str | None:
    location = normalize_text(job.location or "")
    haystack = normalize_text(" ".join([job.location or "", job.description or ""]))
    scored: list[tuple[int, int, str]] = []

    for index, market in enumerate(markets):
        if not market.enabled:
            continue
        terms = [normalize_text(term) for term in market.locations]
        score = 0
        if any(term and term in location for term in terms):
            score = max(score, 400)
        elif any(term and term in haystack for term in terms):
            score = max(score, 300)
        if job.market_hint == market.id:
            score = max(score, 100)
        if score:
            scored.append((score, -index, market.id))

    if scored:
        return max(scored)[2]

    if job.remote is True:
        return next((market.id for market in markets if market.enabled), None)
    return None
```

Extend this minimal shape with explicit remote-scope/sponsorship terms so the test examples in the spec pass. Do not allow query origin to override stronger location evidence.

- [ ] **Step 5: Add SQLite migrations and model round-trip fields**

Add `market_id: str | None = None` to `Job` and `market_id: str = ""` to `Evaluation`.

Extend the existing job migration dictionary:

```python
_R2_JOB_COLUMNS = {
    ...,
    "market_id": "TEXT NOT NULL DEFAULT ''",
}
```

Add an evaluation migration dictionary and call it during `_init_db()` after `_CREATE_EVALUATIONS`:

```python
_MARKET_EVALUATION_COLUMNS = {
    "market_id": "TEXT NOT NULL DEFAULT ''",
}
```

Update every `Job` row mapper and evaluation save/load SQL to include `market_id`. Add:

```python
def set_job_market(self, job_id: int, market_id: str | None) -> None:
    with self._conn:
        self._conn.execute(
            "UPDATE jobs SET market_id = ? WHERE id = ?",
            (market_id or "", job_id),
        )
```

During job merges, preserve an existing non-empty market ID temporarily; Task 8 re-attributes the richer survivor before evaluation, so merge identity must not use `market_id` as part of dedupe.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
python -m pytest tests/test_market_policy.py tests/test_store.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/market_policy.py src/job_hunter/models.py src/job_hunter/store.py tests/test_market_policy.py tests/test_store.py
git commit -m "feat: attribute and persist job markets"
```

---

### Task 4: Add conservative market-specific pre-Gemini eligibility

**Files:**
- Create: `src/job_hunter/market_eligibility.py`
- Modify: `src/job_hunter/prefilter.py`
- Create: `tests/test_market_eligibility.py`
- Modify: `tests/test_prefilter.py`

**Interfaces:**
- Consumes: `Job`, attributed `MarketPolicy`, `salary_floor_for_job()`.
- Produces: `MarketEligibilityResult` with `allowed`, `reason_code`, `reason`, `sponsorship_status`, `international_remote_status`, `warnings`, `disclosed_salary_max`.
- Changes: `prefilter_job(job: Job, policy: SearchPolicy, market: MarketPolicy | None = None) -> PrefilterResult`.
- Legacy behavior: when `market is None`, preserve the old remote-only prefilter semantics for old configs/tests.

- [ ] **Step 1: Write the explicit blocker/unknown test matrix**

Create `tests/test_market_eligibility.py` covering the spec cases:

```python
def test_berlin_german_required_is_blocked(germany):
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Berlin",
            description="Fluent German is required. English is used daily.",
        ),
        germany,
    )
    assert result.allowed is False
    assert result.reason_code == "required_language"


def test_berlin_german_nice_to_have_survives(germany):
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Berlin",
            description="English required. German is a nice to have.",
        ),
        germany,
    )
    assert result.allowed is True


def test_israeli_hebrew_role_survives(israel):
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Israel - Remote",
            remote=True,
            description="Hebrew required for product collaboration.",
        ),
        israel,
    )
    assert result.allowed is True


def test_israel_remote_without_international_detail_is_unknown_not_blocked(israel):
    result = evaluate_market_eligibility(
        Job(source="x", title="Senior Frontend Engineer", location="Israel - Remote", remote=True),
        israel,
    )
    assert result.allowed is True
    assert result.international_remote_status == "unknown"
    assert "International remote eligibility: unknown" in result.warnings


def test_london_explicit_no_sponsorship_is_blocked(london):
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="London",
            description="We are unable to offer visa sponsorship for this role.",
        ),
        london,
    )
    assert result.allowed is False
    assert result.reason_code == "no_sponsorship"


def test_london_sponsorship_omitted_is_unknown(london):
    result = evaluate_market_eligibility(
        Job(source="x", title="Senior Frontend Engineer", location="London"),
        london,
    )
    assert result.allowed is True
    assert result.sponsorship_status == "unknown"


def test_timezone_overlap_is_a_warning_not_a_blocker(london):
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="London",
            description="You must overlap four hours with US Eastern Time.",
        ),
        london,
    )
    assert result.allowed is True
    assert any("overlap" in warning.lower() for warning in result.warnings)
```

Also add tests for:

```python
# salary unknown -> allowed
# GBP base range £70k-£80k in London -> blocked below £90k
# SGD 9,000/month base in Singapore -> blocked below SGD 10,000/month
# ILS 34,000/month base in Israel -> blocked below ILS 35,000/month
# USD $190k NYC -> allowed; same SF -> blocked below $200k
# explicit contractor/freelance/part-time/fixed-term/internship -> blocked
# Israel onsite/hybrid or "must be based in Israel" -> blocked
# sponsorship-positive wording -> sponsorship_status == "available"
```

- [ ] **Step 2: Run market-eligibility tests and verify failure**

Run:

```bash
python -m pytest tests/test_market_eligibility.py tests/test_prefilter.py -q
```

Expected: FAIL because the market eligibility module does not exist and the prefilter still globally blocks every non-remote role.

- [ ] **Step 3: Implement conservative signal extraction**

Create `MarketEligibilityResult` and the main function in `src/job_hunter/market_eligibility.py`:

```python
@dataclass(frozen=True, slots=True)
class MarketEligibilityResult:
    allowed: bool
    reason_code: str = "passed"
    reason: str = "passed market eligibility"
    sponsorship_status: str = "not_applicable"
    international_remote_status: str = "not_applicable"
    warnings: tuple[str, ...] = ()
    disclosed_salary_max: int | None = None


def evaluate_market_eligibility(job: Job, market: MarketPolicy) -> MarketEligibilityResult:
    ...
```

Use conservative sentence-level matching. For language, maintain a finite known-language vocabulary including at least German, French, Dutch, Spanish, Italian, Portuguese, Polish, Swedish, Danish, Norwegian, Finnish, Mandarin/Chinese, Japanese, Korean, Arabic, Russian, and Turkish. Only treat a language as required when the same sentence contains a strong requirement marker such as `required`, `must speak`, `fluent`, `professional proficiency`, or `C1/C2`; skip sentences containing optional markers such as `nice to have`, `preferred`, `plus`, `bonus`, or `advantage`.

For sponsorship, use explicit positive and negative phrases. Negative patterns include:

```python
_NO_SPONSORSHIP = (
    "no visa sponsorship",
    "cannot sponsor",
    "unable to sponsor",
    "without sponsorship",
    "must already be authorized to work",
    "must have the right to work",
)
```

Positive patterns include:

```python
_SPONSORSHIP_AVAILABLE = (
    "visa sponsorship available",
    "visa sponsorship provided",
    "we sponsor",
    "skilled worker sponsorship",
    "employment pass sponsorship",
)
```

For time-zone overlap, never block; add the sentence containing `overlap`, `core hours`, `EST`, `EDT`, `PST`, `PDT`, `CET`, `CEST`, `GMT`, or `SGT` as a compact warning, clipped to a reasonable length.

For permanent employment, block only explicit unambiguous phrases: `freelance`, `contractor`, `part-time`, `internship`, `fixed-term`, `fixed term`, `temporary role`. Do not block generic uses of the word `contract` such as `employment contract`.

- [ ] **Step 4: Implement conservative gross-base salary extraction**

Inside `market_eligibility.py`, only treat salary as deterministic when the sentence clearly describes base salary/base pay/salary range and the market currency can be identified. Missing or ambiguous total-compensation/equity wording returns `None`.

Normalize currency markers:

```python
_CURRENCY_MARKERS = {
    "EUR": ("€", "eur"),
    "GBP": ("£", "gbp"),
    "ILS": ("₪", "ils", "nis"),
    "SGD": ("s$", "sgd"),
    "USD": ("$", "usd", "us$")
}
```

Parse all amounts in the qualifying salary sentence, expand `k` to thousands, multiply explicitly monthly amounts by 12, and use the maximum disclosed base amount. Compare it with `salary_floor_for_job(job, market)`. If the parser cannot prove the currency/period/base nature, return unknown and do not block.

- [ ] **Step 5: Make `prefilter_job` market-aware without moving nuanced Full-Stack decisions into regexes**

Change the signature:

```python
def prefilter_job(
    job: Job,
    policy: SearchPolicy,
    market: MarketPolicy | None = None,
) -> PrefilterResult:
```

Keep blocked title/profession and relevance checks. If `market is None`, retain the existing global remote-only checks for legacy config. If `market` is present, remove the global `job.remote is False` hard blocker and call `evaluate_market_eligibility(job, market)` instead:

```python
market_result = evaluate_market_eligibility(job, market)
if not market_result.allowed:
    return PrefilterResult(
        should_evaluate=False,
        hard_blocker=True,
        reason=market_result.reason,
        reason_code=market_result.reason_code,
    )
```

Do not hard-reject ambiguous 60/40 or 70/30 Full-Stack descriptions here. Pure backend titles are handled by configured blocked profession title phrases; ambiguous Full-Stack responsibility balance belongs to ranking/Gemini.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
python -m pytest tests/test_market_eligibility.py tests/test_prefilter.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/market_eligibility.py src/job_hunter/prefilter.py tests/test_market_eligibility.py tests/test_prefilter.py
git commit -m "feat: add market eligibility rules"
```

---

### Task 5: Make pre-Gemini ranking market-aware and broaden frontend-heavy Full-Stack discovery

**Files:**
- Modify: `src/job_hunter/ranking.py`
- Modify: `tests/test_ranking.py`

**Interfaces:**
- Consumes: `Job.market_id`, `SearchPolicy.markets`, configured expanded role families.
- Produces: `market_priority_bonus(job, policy) -> int`.
- Produces: `_backend_transition_penalty(job) -> int`.
- Preserves: existing source diversity selection and stable tie-break behavior.

- [ ] **Step 1: Write ranking tests for market priority, relocation-valid onsite roles, and Full-Stack balance**

Add tests to `tests/test_ranking.py`:

```python
def test_market_priority_is_modest_not_absolute():
    policy = make_market_policy()
    germany = Job(
        source="duckduckgo",
        title="Senior Frontend Engineer",
        company="A",
        location="Berlin",
        market_id="germany_eu",
        description="React TypeScript",
    )
    london = Job(
        source="duckduckgo",
        title="Staff Frontend Engineer",
        company="B",
        location="London",
        market_id="london",
        description="React TypeScript design system architecture mentorship",
    )

    ranked = rank_jobs([(1, germany), (2, london)], policy, make_preferences())

    assert ranked[0][0] == 2  # stronger job can still beat higher-priority market


def test_london_hybrid_can_receive_location_fit_when_market_allows_relocation():
    policy = make_market_policy()
    preferences = make_preferences()
    job = Job(
        source="duckduckgo",
        title="Senior Frontend Engineer",
        location="London - Hybrid",
        remote=False,
        market_id="london",
        description="React TypeScript",
    )
    assert profile_priority_score(job, preferences, policy) > 0


def test_frontend_heavy_full_stack_beats_backend_heavy_full_stack():
    policy = make_market_policy()
    preferences = make_preferences()
    frontend_heavy = Job(
        source="wellfound",
        title="Full-Stack Engineer",
        market_id="germany_eu",
        description="React Next.js TypeScript frontend, Node.js REST APIs and PostgreSQL",
    )
    backend_heavy = Job(
        source="wellfound",
        title="Full-Stack Engineer",
        market_id="germany_eu",
        description="Go Java Kubernetes distributed systems event-driven backend architecture",
    )

    assert profile_priority_score(frontend_heavy, preferences, policy) > profile_priority_score(
        backend_heavy, preferences, policy
    )
```

Add a test that `select_diverse_candidates()` still respects source caps after market scoring changes.

- [ ] **Step 2: Run ranking tests and verify failure**

Run:

```bash
python -m pytest tests/test_ranking.py -q
```

Expected: new tests fail because location scoring is remote-only and no market/backend-transition scoring exists.

- [ ] **Step 3: Add modest market priority and market-valid location scoring**

Add:

```python
def market_priority_bonus(job: Job, policy: SearchPolicy) -> int:
    if not job.market_id or not policy.markets:
        return 0
    for index, market in enumerate(policy.markets):
        if market.id == job.market_id:
            return max(0, len(policy.markets) - index)
    return 0
```

This gives the first market a small 6-point bonus with six markets and the last a 1-point bonus; fit quality can still overcome market priority.

Replace remote-only location assumptions when an attributed market exists. For `germany_eu`, remote receives the strongest location score and feasible Berlin/Germany local work remains positive but lower. For `israel_remote`, only remote survives eligibility. For relocation markets, explicit target-city hybrid/onsite remains a valid positive location signal.

- [ ] **Step 4: Add a bounded backend-transition penalty rather than a brittle Full-Stack blocker**

Only apply this penalty to titles containing `full stack` or `full-stack`.

Use frontend signals:

```python
_FRONTEND_TRANSITION_SIGNALS = (
    "react",
    "next.js",
    "nextjs",
    "frontend",
    "front-end",
    "typescript",
    "design system",
)
```

Use backend-heavy signals:

```python
_BACKEND_HEAVY_SIGNALS = (
    "distributed systems",
    "kubernetes",
    "golang",
    "java",
    "event-driven architecture",
    "backend architecture",
    "high-throughput",
    "message queues",
)
```

Implement:

```python
def _backend_transition_penalty(job: Job) -> int:
    title = normalize_text(job.title or "")
    if "full stack" not in title and "full-stack" not in (job.title or "").lower():
        return 0
    description = normalize_text(job.description or "")
    frontend = sum(signal in description for signal in _FRONTEND_TRANSITION_SIGNALS)
    backend = sum(signal in description for signal in _BACKEND_HEAVY_SIGNALS)
    if frontend == 0 and backend >= 2:
        return 15
    if frontend >= 1 and backend >= 2:
        return 6
    return 0
```

Subtract this from `profile_priority_score`/`priority_score`, and add `market_priority_bonus`. Keep the result clamped to `0..100`.

Extend `source_quality()` so high-value targeted-search result URLs from approved market boards such as `wellfound.com`, `jobs.techaviv.com`, `devjobs.co.il`, `workvisajobs.co.uk`, `nodeflair.com`, `sg.jobstreet.com`, `mycareersfuture.gov.sg`, `builtin.com`, `startup.jobs`, and `ycombinator.com` score like useful specialist sources but still below direct supported ATS postings.

- [ ] **Step 5: Run ranking tests and commit**

Run:

```bash
python -m pytest tests/test_ranking.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/ranking.py tests/test_ranking.py
git commit -m "feat: rank jobs by market and transition fit"
```

---

### Task 6: Make Gemini evaluation consume the attributed market policy

**Files:**
- Modify: `src/job_hunter/evaluation.py`
- Modify: `src/job_hunter/models.py`
- Modify: `tests/test_evaluation.py`

**Interfaces:**
- Consumes: `Job.market_id`, `SearchPolicy.markets`, `market_by_id()`, `salary_floor_for_job()`, `evaluate_market_eligibility()`.
- Preserves: `evaluate_job(job, context, policy, gemini) -> Evaluation` public signature.
- Produces: `Evaluation.market_id` and a market-aware `location_note` contract.

- [ ] **Step 1: Write market-aware evaluator prompt tests**

Add tests to `tests/test_evaluation.py` using production-shaped market fixtures:

```python
def test_evaluation_prompt_uses_london_market_rules(fake_gemini, context):
    policy = make_market_policy()
    job = Job(
        source="x",
        title="Senior Frontend Engineer",
        location="London - Hybrid",
        remote=False,
        market_id="london",
        description="React TypeScript. Hybrid in London.",
    )
    fake_gemini.text = json.dumps(_valid_payload())

    evaluation = evaluate_job(job, context, policy, fake_gemini)
    prompt = fake_gemini.prompts[0][0]

    assert "GBP 90000" in prompt
    assert "Hybrid" in prompt
    assert "sponsorship" in prompt.lower()
    assert "omitted sponsorship" in prompt.lower()
    assert "relocation" in prompt.lower()
    assert evaluation.market_id == "london"


def test_evaluation_prompt_uses_sf_floor_not_nyc_floor(fake_gemini, context):
    policy = make_market_policy()
    job = Job(
        source="x",
        title="Senior Frontend Engineer",
        location="San Francisco Bay Area",
        market_id="us_nyc_sf",
        description="React TypeScript",
    )
    fake_gemini.text = json.dumps(_valid_payload())
    evaluate_job(job, context, policy, fake_gemini)
    assert "USD 200000" in fake_gemini.prompts[0][0]


def test_evaluation_prompt_explains_backend_transition_for_full_stack(fake_gemini, context):
    policy = make_market_policy()
    job = Job(
        source="x",
        title="Full-Stack Engineer",
        location="Remote Germany",
        market_id="germany_eu",
        description="React TypeScript Node PostgreSQL",
    )
    fake_gemini.text = json.dumps(_valid_payload())
    evaluate_job(job, context, policy, fake_gemini)
    prompt = fake_gemini.prompts[0][0].lower()
    assert "senior frontend" in prompt
    assert "do not invent senior backend" in prompt
```

Keep a legacy test that a policy without markets still mentions `salary_floor_eur` and uses the old remote-only fallback.

- [ ] **Step 2: Run evaluator tests and verify failure**

Run:

```bash
python -m pytest tests/test_evaluation.py -q
```

Expected: new market tests fail because the prompt still hardcodes EUR/global remote-only rules.

- [ ] **Step 3: Build a structured market section in the prompt**

In `evaluation.py`, resolve:

```python
market = market_by_id(policy, job.market_id)
```

When a market exists, compute `market_eligibility = evaluate_market_eligibility(job, market)` and `floor = salary_floor_for_job(job, market)` and build a structured section similar to:

```text
Market policy:
- Market ID: london
- Gross base salary floor: GBP 90000
- Allowed required languages: English
- Remote policy: allowed
- Relocation policy: allowed
- Sponsorship policy: required
- Sponsorship status from deterministic precheck: unknown
- International remote status from deterministic precheck: not_applicable
- Deterministic warnings: Visa sponsorship: unknown
```

Replace the opening sentence `remote-only job search` with `market-aware job search`.

Use exact rules in the prompt:

```text
- Missing salary is unknown, not a blocker.
- A disclosed gross base salary maximum below the stated market floor is a hard blocker.
- Do not treat hybrid, onsite, or relocation as a blocker when the market policy allows it.
- If sponsorship is required, explicit no-sponsorship is a blocker; omitted sponsorship is unknown, not a blocker.
- A disallowed language is a blocker only when the posting explicitly requires it; nice-to-have languages are not blockers.
- Time-zone overlap is informational. Preserve any concrete overlap/core-hours requirement in location_note.
- Preserve sponsorship/international-remote uncertainty in location_note so the user can decide.
```

For Full-Stack titles add:

```text
Backend transition context:
The candidate is a senior frontend engineer but is earlier than junior-level in backend depth today. Treat React/Next.js/TypeScript ownership as senior evidence. Node.js/TypeScript APIs, REST/GraphQL, PostgreSQL/Supabase and similar product-backend work may be realistic ramp-up areas. Do not invent senior backend experience. Backend-dominant ownership is a gap and may make the role unsuitable.
```

If no market is attributed, keep the existing global legacy prompt path.

- [ ] **Step 4: Persist the evaluated market ID**

When returning `Evaluation`, set:

```python
market_id=job.market_id or "",
```

Do not alter Gemini `purpose`, `thinking_level`, output-token cap, JSON mode, decision thresholds, or quota tracking.

- [ ] **Step 5: Run evaluator tests and commit**

Run:

```bash
python -m pytest tests/test_evaluation.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/evaluation.py src/job_hunter/models.py tests/test_evaluation.py
git commit -m "feat: evaluate jobs with market policy"
```

---

### Task 7: Surface market uncertainty and time-zone notes in Telegram navigation

**Files:**
- Modify: `src/job_hunter/models.py`
- Modify: `src/job_hunter/pipeline.py`
- Modify: `src/job_hunter/telegram_navigation.py`
- Modify: `tests/test_telegram_navigation.py`
- Modify: pipeline tests covering digest construction.

**Interfaces:**
- Adds: `DigestItem.market_id: str = ""`, `DigestItem.market_note: str = ""`.
- Adds: `NavigationCard.market_id: str = ""`, `NavigationCard.market_note: str = ""`.
- Preserves: old serialized navigation sessions because both new fields have defaults.

- [ ] **Step 1: Write Telegram card tests for visible notes and backward-compatible stored cards**

Add to `tests/test_telegram_navigation.py`:

```python
def test_navigation_card_surfaces_market_note():
    card = NavigationCard(
        job_id=1,
        title="Senior Frontend Engineer",
        company="Acme",
        location="London",
        score=91,
        url="https://example.test/job",
        market_id="london",
        market_note="Visa sponsorship: unknown. Requires 4 hours overlap with EST.",
    )

    text, _keyboard = build_navigation_card(card, "session", 0, 1)

    assert "Visa sponsorship: unknown" in text
    assert "4 hours overlap with EST" in text
```

Add a navigation-store test loading legacy `cards_json` without the two new keys and assert default empty values are accepted by `NavigationCard(**card)`.

- [ ] **Step 2: Run navigation tests and verify failure**

Run:

```bash
python -m pytest tests/test_telegram_navigation.py tests/test_navigation_store.py -q
```

If navigation-store tests live in a differently named file, run that existing file instead.

Expected: FAIL because the new fields are not present/rendered.

- [ ] **Step 3: Add note fields and wire evaluation notes into digest/navigation creation**

Add default fields to `DigestItem` and `NavigationCard` in `models.py`.

In every `DigestItem(...)` construction in `pipeline.py`, use:

```python
market_id=evaluation.market_id or job.market_id or "",
market_note=evaluation.location_note or "",
```

This includes normal evaluation, pending-delivery requeue, and pending-cover-letter paths.

When building `NavigationCard` in `_build_navigation_session`, copy the fields:

```python
market_id=item.market_id,
market_note=item.market_note,
```

- [ ] **Step 4: Render the note compactly**

In `build_navigation_card()`:

```python
note_line = f"\nNote: {card.market_note}" if card.market_note else ""
text = (
    f"{card.title}\n\n"
    f"Company: {card.company or 'Not specified'}\n"
    f"Location: {card.location or 'Not specified'}\n"
    f"Match: {card.score}%"
    f"{note_line}"
)
```

Do not add market-specific business logic to Telegram; it only renders the evaluation note.

- [ ] **Step 5: Run navigation/pipeline-focused tests and commit**

Run:

```bash
python -m pytest tests/test_telegram_navigation.py tests/test_pipeline.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/models.py src/job_hunter/pipeline.py src/job_hunter/telegram_navigation.py tests/test_telegram_navigation.py tests/test_pipeline.py
git commit -m "feat: surface market notes in job cards"
```

---

### Task 8: Integrate attribution/eligibility into discovery and add per-market observability

**Files:**
- Modify: `src/job_hunter/discovery.py`
- Modify: `src/job_hunter/pipeline.py`
- Modify: `src/job_hunter/sources/__init__.py`
- Modify: `tests/test_discovery.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `attribute_market()`, `market_by_id()`, market-aware `prefilter_job()`, market-tagged DuckDuckGo stats.
- Produces: discovery counters `raw_by_market`, `unique_by_market`, `rejected_by_market`, `eligible_by_market`.
- Changes: `_evaluate_and_deliver_job(...) -> tuple[bool, bool, str | None]`, where the third element is the evaluation decision created on this call or `None` when no new evaluation was produced.
- Produces: one compact log line per configured market with query/discovery/selection/outcome/delivery counts.

- [ ] **Step 1: Write discovery integration tests for market attribution before Gemini**

Add to `tests/test_discovery.py`:

```python
def test_collect_candidates_attributes_market_and_uses_market_prefilter(tmp_path, policy, http):
    source = FakeSource([
        Job(
            source="fake",
            title="Senior Frontend Engineer",
            location="London - Hybrid",
            remote=False,
            description="React TypeScript. Visa sponsorship available.",
        )
    ])
    store = JobStore(tmp_path / "state.sqlite3")

    result = collect_candidates([source], store, http, policy)

    assert len(result.eligible) == 1
    job_id, job = result.eligible[0]
    assert job.market_id == "london"
    assert store.get_job(job_id).market_id == "london"
    assert result.stats.eligible_by_market == {"london": 1}
```

Add tests that an Israel onsite role is rejected before Gemini, an unknown-market remote job survives conservative fallback, and a canonicalized duplicate remains one logical job with one market evaluation.

- [ ] **Step 2: Write pipeline logging/selection tests**

Add pipeline tests using injected fake sources/Gemini so no web/Gemini network calls occur. Capture logs with `caplog` and assert one line includes fields such as:

```text
market=london queries_planned=5 queries_attempted=5 raw=... eligible=... selected=... package_match=... delivered=...
```

Also assert selected fresh jobs still never exceed `settings.policy.max_jobs_per_run` after adding more raw candidates/markets.

- [ ] **Step 3: Run discovery/pipeline tests and verify failure**

Run:

```bash
python -m pytest tests/test_discovery.py tests/test_pipeline.py -q
```

Expected: new tests fail because market attribution/metrics are not integrated into discovery/pipeline.

- [ ] **Step 4: Attribute before market-aware prefilter and re-check after canonical resolution**

Extend `DiscoveryStats`:

```python
    raw_by_market: dict[str, int] = field(default_factory=dict)
    unique_by_market: dict[str, int] = field(default_factory=dict)
    rejected_by_market: dict[str, int] = field(default_factory=dict)
    eligible_by_market: dict[str, int] = field(default_factory=dict)
```

During raw-source collection, count `job.market_hint` or a cheap attribution from available source metadata when possible.

After enrichment and before prefilter:

```python
job.market_id = attribute_market(job, policy.markets) if policy.markets else None
if job.market_id:
    store.set_job_market(job_id, job.market_id)
market = market_by_id(policy, job.market_id)
prefilter_result = prefilter_job(job, policy, market)
```

If canonical resolution changes/enriches the logical job, run attribution again before final eligibility append and update `store.set_job_market()` so a stronger actual location can beat an earlier query hint.

A truly unresolved market must not cause a drop by itself; call legacy/general prefilter behavior conservatively only when another explicit blocker exists.

- [ ] **Step 5: Pass the configured local date into market query rotation**

In `run_pipeline()` before `build_sources()`:

```python
query_date = datetime.now(ZoneInfo(settings.timezone)).date()
base_sources = (
    sources
    if sources is not None
    else build_sources(
        settings,
        http,
        search_breaker=search_breaker,
        query_date=query_date,
    )
)
```

This makes daily rotation stable in the user's configured timezone.

- [ ] **Step 6: Track selection/evaluation/delivery counts by market**

Add helpers in `pipeline.py`:

```python
def _market_counts(items) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for _job_id, job, *_rest in items:
        counts[job.market_id or "unattributed"] += 1
    return dict(counts)
```

Change `_evaluate_and_deliver_job` to return the new evaluation decision as the third tuple member:

```python
return promoted, False, evaluation.decision
```

Return `None` for stale/requeued/quota-blocked paths that do not create a fresh evaluation in that call. Update both pending and selected loops to accumulate a nested `Counter` by `(market_id, decision)`.

After `deliverable_items` is known, count deliveries by `DigestItem.market_id`.

Find the `DuckDuckGoSource` instance(s) in `base_sources` and aggregate `stats.planned_by_market`, `attempted_by_market`, and `succeeded_by_market`.

For each configured market, log one bounded line:

```python
logger.info(
    "market=%s queries_planned=%s queries_attempted=%s queries_succeeded=%s "
    "raw=%s unique=%s rejected=%s eligible=%s selected=%s "
    "high_priority=%s package_match=%s possible_match=%s skip=%s blocked=%s delivered=%s",
    ...,
)
```

Keep existing source-level logs unchanged.

- [ ] **Step 7: Run integration tests and commit**

Run:

```bash
python -m pytest tests/test_discovery.py tests/test_pipeline.py -q
```

Expected: PASS.

Commit:

```bash
git add src/job_hunter/discovery.py src/job_hunter/pipeline.py src/job_hunter/sources/__init__.py tests/test_discovery.py tests/test_pipeline.py
git commit -m "feat: integrate market discovery pipeline"
```

---

### Task 9: Document tuning, run regression tests, and perform the controlled dry-run acceptance check

**Files:**
- Modify: `README.md`
- Test: full `tests/` suite

**Interfaces:**
- Documents: market order, query shares, salary floors, unknown-vs-blocker behavior, source strategy, and tuning workflow.
- Verifies: no regression to R2 canonicalization/provenance/watchlist, Gmail, Gemini guardrails, Telegram navigation, or cover-letter flow.

- [ ] **Step 1: Add README documentation for the market-driven config**

Add a concise section explaining:

```markdown
### Market-driven search

When `markets:` exists in `config/search.yml`, it is the authoritative search strategy.
Market list order is priority order. `query_share` divides the bounded
`max_search_queries_per_run` budget; every enabled market receives at least
one slot when the budget permits it.

Market profiles own:
- locations and remote/relocation behavior
- allowed required languages
- gross base salary floor/currency
- sponsorship policy
- targeted-search domains/templates

Unknown salary or sponsorship is not a rejection. Explicit incompatibility is.
```

Include the six-market priority table and approved gross base salary floors. Explain that changing `query_share` is the intended tuning mechanism after observing per-market logs; no code change should be needed for normal tuning.

State that source expansion currently uses targeted public search first and direct adapters are follow-up work only when metrics justify them.

- [ ] **Step 2: Run all focused market/search tests together**

Run:

```bash
python -m pytest \
  tests/test_config.py \
  tests/test_discovery_queries.py \
  tests/test_market_policy.py \
  tests/test_market_eligibility.py \
  tests/test_prefilter.py \
  tests/test_ranking.py \
  tests/test_evaluation.py \
  tests/test_store.py \
  tests/test_discovery.py \
  tests/test_pipeline.py \
  tests/test_telegram_navigation.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run the complete regression suite**

Run:

```bash
python -m pytest -q
```

Expected: PASS with zero failures.

Pay particular attention to existing tests for:

```text
canonical resolution
cross-source dedupe/provenance
company watch promotion/health
Gmail sync/classification/application events
Gemini usage/quota guardrails
cover-letter generation
Telegram navigation persistence
```

No existing subsystem should require a behavior change unrelated to markets.

- [ ] **Step 4: Inspect generated queries without spending Gemini calls**

With the normal project environment available, run a query preview in Python without starting the pipeline:

```bash
python - <<'PY'
from datetime import date
from pathlib import Path
from job_hunter.config import load_settings
from job_hunter.discovery_queries import allocate_market_query_slots, generate_search_queries

settings = load_settings(Path("config/search.yml"))
allocation = allocate_market_query_slots(
    settings.policy.markets,
    settings.policy.max_search_queries_per_run,
)
print(allocation)
for query in generate_search_queries(settings.policy, date(2026, 9, 2)):
    print(f"{query.market_id}: {query.text}")
PY
```

Expected allocation for the approved 30-query config:

```text
{'germany_eu': 10, 'israel_remote': 8, 'london': 5, 'singapore': 3, 'us_nyc_sf': 3, 'secondary_eu_relocation': 1}
```

Inspect the output and confirm every market appears and no duplicate query strings are present.

- [ ] **Step 5: Run one controlled bot dry run only when the configured production environment is intentionally allowed to spend Gemini quota**

This command makes real discovery and Gemini calls even though Telegram delivery is disabled, so execute it only as the final acceptance check under the project's normal guarded free-tier environment:

```bash
JOB_HUNTER_DRY_RUN=1 python -m job_hunter run --config config/search.yml
```

Expected:

```text
- no Telegram delivery
- no uncaught exception
- existing Gemini quota guardrails remain active
- per-source logs still appear
- one per-market log line appears for every configured market
- fresh Gemini evaluations remain bounded by max_jobs_per_run and existing pending-work/quota behavior
- sponsorship/international-remote/time-zone uncertainty appears in evaluation location notes rather than being silently rejected
```

Capture the candidate mix for comparison, but do not tune shares inside code. If real results show a market is over/underrepresented, adjust only `query_share`, source-domain order, or query-template order in `config/search.yml` in a separate reviewed tuning change.

- [ ] **Step 6: Commit documentation/verification state**

Run:

```bash
git status --short
git diff --check
```

Expected: no whitespace errors and only intended files modified.

Commit:

```bash
git add README.md
git commit -m "docs: explain market search tuning"
```

---

## Plan Self-Review Checklist

Before implementation is considered complete, verify each design requirement maps to a task:

- Ordered market config and initial shares: Task 1.
- Market-specific salary/language/work-mode/sponsorship rules: Tasks 1, 4, 6.
- Country/startup-specific source expansion: Tasks 1 and 2 via targeted public search.
- Bounded query allocation and deterministic rotation: Task 2.
- Primary market attribution and city-specific salary floor: Task 3.
- Explicit incompatibility prefilter vs unknown retention: Task 4.
- Frontend-heavy Full-Stack and mid-level transition discovery: Tasks 1 and 5.
- Engineering Manager blocked / Architect retained: Tasks 1, 4, 5.
- Gemini market-aware evaluation without model/quota changes: Task 6.
- User-visible sponsorship/time-zone/international-remote notes: Task 7.
- Existing `max_jobs_per_run` protection: Task 8 and regression verification.
- Per-market observability alongside source metrics: Task 8.
- Legacy config fallback with market config authoritative when present: Tasks 1 and 2.
- R2 canonicalization/provenance/watchlist/Gmail/Telegram compatibility: Tasks 3, 8, 9.
- No Supabase migration/direct scraper catalog/authenticated scraping: Global constraints and Tasks 1-2.

## Expected Commit Sequence

Implementation should leave a reviewable history close to:

```text
feat: add market search policy config
feat: allocate market search queries
feat: attribute and persist job markets
feat: add market eligibility rules
feat: rank jobs by market and transition fit
feat: evaluate jobs with market policy
feat: surface market notes in job cards
feat: integrate market discovery pipeline
docs: explain market search tuning
```

Each commit must pass the focused tests listed in its task before moving to the next task.