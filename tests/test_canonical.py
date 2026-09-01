import job_hunter.canonical as canonical
from job_hunter.canonical import CanonicalResolver, parse_supported_ats_url
from job_hunter.models import AtsReference, Job


class _Response:
    def __init__(self, *, url: str, text: str = "", status_code: int = 200):
        self.url = url
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Http:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return self.response


def test_parse_lever_reference():
    ref = parse_supported_ats_url("https://jobs.lever.co/acme/abc-123")
    assert ref is not None
    assert (ref.provider, ref.board, ref.job_id) == ("lever", "acme", "abc-123")


def test_parse_ashby_reference():
    ref = parse_supported_ats_url("https://jobs.ashbyhq.com/acme/xyz")
    assert ref is not None
    assert (ref.provider, ref.board, ref.job_id) == ("ashby", "acme", "xyz")


def test_parse_greenhouse_reference():
    ref = parse_supported_ats_url("https://boards.greenhouse.io/acme/jobs/456")
    assert ref is not None
    assert (ref.provider, ref.board, ref.job_id) == ("greenhouse", "acme", "456")


def test_direct_ats_url_wins_without_search():
    http = _Http(_Response(url="https://unused.test"))
    resolver = CanonicalResolver(
        http, search_candidates=lambda job: [], watch_target=lambda company: None
    )
    result = resolver.resolve(
        Job(
            source="yc",
            title="Frontend Engineer",
            company="Acme",
            url="https://jobs.lever.co/acme/abc",
        )
    )
    assert result is not None
    assert result.url == "https://jobs.lever.co/acme/abc"
    assert result.confidence == 1.0
    assert http.calls == 0


def test_redirect_to_supported_ats_is_accepted():
    http = _Http(_Response(url="https://jobs.lever.co/acme/abc"))
    resolver = CanonicalResolver(
        http, search_candidates=lambda job: [], watch_target=lambda company: None
    )
    result = resolver.resolve(
        Job(
            source="board",
            title="Frontend Engineer",
            company="Acme",
            url="https://board.test/job",
        )
    )
    assert result is not None
    assert result.method == "redirect"
    assert result.confidence == 0.98


def test_targeted_search_rejects_wrong_company():
    http = _Http(_Response(url="https://board.test/job", text="<html></html>"))
    resolver = CanonicalResolver(
        http,
        search_candidates=lambda job: [
            Job(
                source="duckduckgo",
                title="Frontend Engineer",
                company="Other",
                url="https://jobs.lever.co/other/abc",
            )
        ],
        watch_target=lambda company: None,
    )
    result = resolver.resolve(
        Job(
            source="board",
            title="Frontend Engineer",
            company="Acme",
            url="https://board.test/job",
        )
    )
    assert result is None


def test_watch_target_matches_ats_board_and_exact_normalized_title():
    http = _Http(_Response(url="https://board.test/job", text="<html></html>"))
    resolver = CanonicalResolver(
        http,
        search_candidates=lambda job: [
            Job(
                source="duckduckgo",
                title="Frontend-Engineer",
                company="Acme",
                url="https://jobs.lever.co/acme/abc",
            )
        ],
        watch_target=lambda company: AtsReference("lever", "acme", None),
    )
    result = resolver.resolve(
        Job(
            source="board",
            title="Frontend Engineer",
            company="Acme",
            url="https://board.test/job",
        )
    )
    assert result is not None
    assert result.confidence == 0.92
    assert result.method == "watch_target"


def test_embedded_supported_ats_url_resolves_at_095():
    http = _Http(
        _Response(
            url="https://board.test/job",
            text='<a href="https://jobs.ashbyhq.com/acme/abc">Apply</a>',
        )
    )
    resolver = CanonicalResolver(
        http, search_candidates=lambda job: [], watch_target=lambda company: None
    )

    result = resolver.resolve(
        Job(
            source="board",
            title="Frontend Engineer",
            company="Acme",
            url="https://board.test/job",
        )
    )

    assert result is not None
    assert result.url == "https://jobs.ashbyhq.com/acme/abc"
    assert result.confidence == 0.95
    assert result.method == "embedded"


def test_targeted_search_exact_match_resolves_at_090():
    http = _Http(_Response(url="https://board.test/job", text="<html></html>"))
    resolver = CanonicalResolver(
        http,
        search_candidates=lambda job: [
            Job(
                source="duckduckgo",
                title="Frontend Engineer",
                company="Acme Inc.",
                location="Berlin, Germany",
                url="https://careers.acme.test/jobs/frontend",
            )
        ],
        watch_target=lambda company: None,
    )

    result = resolver.resolve(
        Job(
            source="board",
            title="Frontend Engineer",
            company="Acme",
            location="Berlin",
            url="https://board.test/job",
        )
    )

    assert result is not None
    assert result.url == "https://careers.acme.test/jobs/frontend"
    assert result.confidence == 0.90
    assert result.method == "targeted_search"


def test_targeted_search_prefers_supported_ats_url_over_generic_result():
    http = _Http(_Response(url="https://board.test/job", text="<html></html>"))
    resolver = CanonicalResolver(
        http,
        search_candidates=lambda job: [
            Job(
                source="duckduckgo",
                title="Frontend Engineer",
                company="Acme",
                url="https://careers.acme.test/jobs/frontend",
            ),
            Job(
                source="duckduckgo",
                title="Frontend Engineer",
                company="Acme",
                url="https://jobs.lever.co/acme/abc",
            ),
        ],
        watch_target=lambda company: None,
    )

    result = resolver.resolve(
        Job(
            source="board",
            title="Frontend Engineer",
            company="Acme",
            url="https://board.test/job",
        )
    )

    assert result is not None
    assert result.url == "https://jobs.lever.co/acme/abc"
    assert result.ats == AtsReference("lever", "acme", "abc")


def test_watch_target_failure_does_not_block_targeted_search():
    http = _Http(_Response(url="https://board.test/job", text="<html></html>"))
    resolver = CanonicalResolver(
        http,
        search_candidates=lambda job: [
            Job(
                source="duckduckgo",
                title="Frontend Engineer",
                company="Acme",
                url="https://careers.acme.test/jobs/frontend",
            )
        ],
        watch_target=lambda company: (_ for _ in ()).throw(RuntimeError("watch down")),
    )

    result = resolver.resolve(
        Job(
            source="board",
            title="Frontend Engineer",
            company="Acme",
            url="https://board.test/job",
        )
    )

    assert result is not None
    assert result.confidence == 0.90
    assert result.method == "targeted_search"


def test_extraction_failure_does_not_block_targeted_search(monkeypatch):
    def fail_extraction(html: str, base_url: str) -> list[str]:
        raise RuntimeError("invalid page")

    monkeypatch.setattr(canonical, "extract_job_page_links", fail_extraction)
    http = _Http(_Response(url="https://board.test/job", text="<html></html>"))
    resolver = CanonicalResolver(
        http,
        search_candidates=lambda job: [
            Job(
                source="duckduckgo",
                title="Frontend Engineer",
                company="Acme",
                url="https://jobs.ashbyhq.com/acme/abc",
            )
        ],
        watch_target=lambda company: None,
    )

    result = resolver.resolve(
        Job(
            source="board",
            title="Frontend Engineer",
            company="Acme",
            url="https://board.test/job",
        )
    )

    assert result is not None
    assert result.confidence == 0.90
    assert result.method == "targeted_search"


def test_source_fetch_failure_does_not_block_targeted_search():
    class _FailingHttp:
        def get(self, url, **kwargs):
            raise RuntimeError("aggregator unavailable")

    resolver = CanonicalResolver(
        _FailingHttp(),
        search_candidates=lambda job: [
            Job(
                source="duckduckgo",
                title="Frontend Engineer",
                company="Acme",
                url="https://jobs.ashbyhq.com/acme/abc",
            )
        ],
        watch_target=lambda company: None,
    )

    result = resolver.resolve(
        Job(
            source="board",
            title="Frontend Engineer",
            company="Acme",
            url="https://unavailable.test/job",
        )
    )

    assert result is not None
    assert result.url == "https://jobs.ashbyhq.com/acme/abc"
    assert result.confidence == 0.90
    assert result.method == "targeted_search"


def test_resolution_failure_is_non_blocking():
    class _FailingHttp:
        def get(self, url, **kwargs):
            raise RuntimeError("network down")

    resolver = CanonicalResolver(
        _FailingHttp(), search_candidates=lambda job: [], watch_target=lambda company: None
    )
    result = resolver.resolve(
        Job(
            source="board",
            title="Frontend Engineer",
            company="Acme",
            url="https://board.test/job",
        )
    )
    assert result is None
