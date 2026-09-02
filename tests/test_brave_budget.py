from datetime import datetime, timezone

from job_hunter.circuit_breaker import CircuitBreaker
from job_hunter.models import Job, SearchQuery
from job_hunter.pipeline import _targeted_canonical_candidates
from job_hunter.search_budget import brave_queries_available_today, split_queries_for_brave
from job_hunter.store import JobStore


UTC = timezone.utc


def test_brave_budget_spreads_250_monthly_queries_and_blocks_same_day_reruns():
    store = JobStore(":memory:")
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

    # 250 remaining across Sep 2-30 => ceil(250 / 29) = 9 for today.
    assert brave_queries_available_today(store, monthly_limit=250, now=now) == 9

    for minute in range(9):
        store.record_search_api_usage(
            provider="brave",
            occurred_at=now.replace(minute=minute).isoformat(),
        )

    # A manual rerun on the same day must not spend another 9 calls.
    assert brave_queries_available_today(store, monthly_limit=250, now=now) == 0

    # The next day gets a fresh share of the remaining monthly allowance.
    tomorrow = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    assert brave_queries_available_today(store, monthly_limit=250, now=tomorrow) == 9


def test_brave_budget_never_exceeds_monthly_limit():
    store = JobStore(":memory:")
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

    for index in range(250):
        store.record_search_api_usage(
            provider="brave",
            occurred_at=datetime(2026, 9, 1, index % 24, index % 60, tzinfo=UTC).isoformat(),
        )

    assert brave_queries_available_today(store, monthly_limit=250, now=now) == 0


def test_brave_query_selection_round_robins_across_markets():
    queries = [
        SearchQuery("germany-1", "germany_eu"),
        SearchQuery("germany-2", "germany_eu"),
        SearchQuery("germany-3", "germany_eu"),
        SearchQuery("israel-1", "israel_remote"),
        SearchQuery("israel-2", "israel_remote"),
        SearchQuery("london-1", "london"),
        SearchQuery("singapore-1", "singapore"),
        SearchQuery("us-1", "us_nyc_sf"),
        SearchQuery("secondary-1", "secondary_eu_relocation"),
    ]

    brave, fallback = split_queries_for_brave(queries, limit=8)

    assert [query.market_id for query in brave] == [
        "germany_eu",
        "israel_remote",
        "london",
        "singapore",
        "us_nyc_sf",
        "secondary_eu_relocation",
        "germany_eu",
        "israel_remote",
    ]
    assert [query.text for query in fallback] == ["germany-3"]


class _Response:
    status_code = 200

    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class _Http:
    def __init__(self):
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return _Response('<a class="result__a" href="https://jobs.ashbyhq.com/hera/123">Founding Software Engineer</a>')


def test_canonical_lookup_never_spends_brave_search(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "configured-but-budgeted")
    http = _Http()
    job = Job(
        source="test",
        company="Hera",
        title="Founding Software Engineer",
        url="https://example.com/job",
    )

    _targeted_canonical_candidates(http, job, CircuitBreaker(5))

    assert http.urls
    assert all("api.search.brave.com" not in url for url in http.urls)
    assert any("duckduckgo.com" in url for url in http.urls)
