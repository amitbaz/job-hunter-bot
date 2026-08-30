import pytest

from job_hunter.models import SearchPolicy, Settings
from job_hunter.sources import (
    ArbeitnowSource,
    AshbySource,
    DuckDuckGoSource,
    GreenhouseSource,
    LeverSource,
    RemotiveSource,
    build_sources,
)


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


class FakeHttp:
    def __init__(self):
        self.json_data = None
        self.text = ""
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(("get_json", url, kwargs))
        if self.json_data is None:
            raise RuntimeError("no fake json_data configured")
        return self.json_data

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return FakeResponse(self.text)


@pytest.fixture
def fake_http():
    return FakeHttp()


@pytest.fixture
def policy():
    return SearchPolicy(
        target_titles=["senior product engineer"],
        positive_keywords=["react"],
        blocked_title_keywords=["junior"],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
        search_queries=['"Senior Product Engineer" remote'],
        ats={"ashby": ["acme"], "lever": ["acme"], "greenhouse": ["acme"]},
    )


def test_remotive_maps_public_posting(fake_http):
    fake_http.json_data = {
        "jobs": [
            {
                "id": 123,
                "title": "Senior Product Engineer",
                "company_name": "Acme",
                "candidate_required_location": "Worldwide",
                "url": "https://remotive.com/jobs/123",
                "description": "<p>React TypeScript</p>",
            }
        ]
    }
    jobs = RemotiveSource(fake_http).discover()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "remotive"
    assert job.source_job_id == "123"
    assert job.company == "Acme"
    assert job.remote is True
    assert job.description == "React TypeScript"


def test_arbeitnow_paginates_up_to_max_pages(fake_http):
    page_one = {
        "data": [
            {
                "slug": "job-1",
                "title": "Senior Product Engineer",
                "company_name": "Acme",
                "location": "Remote",
                "url": "https://arbeitnow.com/jobs/job-1",
                "description": "React",
                "remote": True,
            }
        ],
        "links": {"next": "https://www.arbeitnow.com/api/job-board-api?page=2"},
    }
    page_two = {
        "data": [
            {
                "slug": "job-2",
                "title": "Frontend Engineer",
                "company_name": "Acme",
                "location": "Remote",
                "url": "https://arbeitnow.com/jobs/job-2",
                "description": "TypeScript",
                "remote": True,
            }
        ],
        "links": {"next": "https://www.arbeitnow.com/api/job-board-api?page=3"},
    }

    calls = {"n": 0}
    pages = [page_one, page_two]

    class PaginatingHttp(FakeHttp):
        def get_json(self, url, **kwargs):
            self.calls.append(("get_json", url, kwargs))
            page = pages[calls["n"]]
            calls["n"] += 1
            return page

    jobs = ArbeitnowSource(PaginatingHttp(), max_pages=2).discover()
    assert len(jobs) == 2
    assert calls["n"] == 2  # stopped at max_pages despite a further "next" link
    assert jobs[0].source_job_id == "job-1"
    assert jobs[1].source_job_id == "job-2"


def test_duckduckgo_parses_result_links_and_skips_navigation(fake_http):
    fake_http.text = """
    <html><body>
        <a class="result__a" href="https://jobs.ashbyhq.com/acme/a1">Senior Product Engineer - Acme</a>
        <a class="result__a" href="https://duckduckgo.com/y.js?ad_provider=x">Sponsored</a>
    </body></html>
    """
    jobs = DuckDuckGoSource(fake_http, ['"Senior Product Engineer" remote']).discover()
    assert len(jobs) == 1
    assert jobs[0].source == "duckduckgo"
    assert jobs[0].url == "https://jobs.ashbyhq.com/acme/a1"
    assert jobs[0].title == "Senior Product Engineer - Acme"


def test_duckduckgo_continues_after_query_failure():
    class FailingHttp(FakeHttp):
        def get(self, url, **kwargs):
            raise RuntimeError("network down")

    jobs = DuckDuckGoSource(FailingHttp(), ["query one", "query two"]).discover()
    assert jobs == []


def test_ashby_maps_public_posting(fake_http, policy):
    fake_http.json_data = {
        "jobs": [
            {
                "id": "a1",
                "title": "Senior Product Engineer",
                "location": "Remote Europe",
                "jobUrl": "https://jobs.ashbyhq.com/acme/a1",
                "descriptionPlain": "React TypeScript",
                "isRemote": True,
            }
        ]
    }
    jobs = AshbySource("acme", fake_http).discover()
    assert jobs[0].source_job_id == "a1"
    assert jobs[0].remote is True
    assert jobs[0].company == "acme"


def test_lever_maps_public_posting(fake_http):
    fake_http.json_data = [
        {
            "id": "l1",
            "text": "Senior Product Engineer",
            "categories": {"location": "Remote", "team": "Engineering"},
            "hostedUrl": "https://jobs.lever.co/acme/l1",
            "descriptionPlain": "React TypeScript",
            "workplaceType": "remote",
        }
    ]
    jobs = LeverSource("acme", fake_http).discover()
    assert jobs[0].source_job_id == "l1"
    assert jobs[0].remote is True
    assert jobs[0].location == "Remote"


def test_greenhouse_maps_public_posting(fake_http):
    fake_http.json_data = {
        "jobs": [
            {
                "id": 555,
                "title": "Senior Product Engineer",
                "location": {"name": "Remote - Europe"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/555",
                "content": "<p>React TypeScript</p>",
            }
        ]
    }
    jobs = GreenhouseSource("acme", fake_http).discover()
    assert jobs[0].source_job_id == "555"
    assert jobs[0].remote is True
    assert jobs[0].description == "React TypeScript"


def test_build_sources_includes_always_on_and_configured_ats(fake_http, policy):
    settings = Settings(
        gemini_api_key="g",
        candidate_profile="profile",
        cover_letter_template="template",
        timezone="Europe/Berlin",
        scheduled_hour=9,
        policy=policy,
    )
    sources = build_sources(settings, fake_http)
    kinds = [type(s).__name__ for s in sources]
    assert "RemotiveSource" in kinds
    assert "ArbeitnowSource" in kinds
    assert "DuckDuckGoSource" in kinds
    assert "AshbySource" in kinds
    assert "LeverSource" in kinds
    assert "GreenhouseSource" in kinds
