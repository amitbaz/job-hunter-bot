import pytest

from job_hunter.circuit_breaker import CircuitBreaker
from job_hunter.models import GeminiQuotaSettings, SearchPolicy, Settings
from job_hunter.sources import (
    ArbeitnowSource,
    AshbySource,
    DuckDuckGoSource,
    GreenhouseSource,
    HimalayasSource,
    JobicySource,
    LeverSource,
    RemotiveSource,
    build_sources,
)


_DUCKDUCKGO_HTML = """
<html><body>
    <a class="result__a" href="https://jobs.ashbyhq.com/acme/a1">Senior Product Engineer - Acme</a>
</body></html>
"""


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


def test_jobicy_maps_public_posting_from_single_feed():
    http = FakeHttp()
    http.json_data = {
        "jobs": [
            {
                "id": 152035,
                "url": "https://jobicy.com/jobs/152035-senior-front-end-developer-react-typescript-market-research",
                "jobSlug": "152035-senior-front-end-developer-react-typescript-market-research",
                "jobTitle": "Senior Front-End Developer (React / TypeScript) - Market Research",
                "companyName": "Truelogic",
                "jobGeo": "LATAM",
                "jobDescription": "<p>React <strong>TypeScript</strong></p>",
            },
            {
                "id": 152036,
                "url": "https://jobicy.com/jobs/152036-staff-frontend-engineer?utm_source=jobicy",
                "jobSlug": "152036-staff-frontend-engineer",
                "jobTitle": "Staff Frontend Engineer",
                "companyName": "Example Co",
                "jobGeo": "Europe",
                "jobDescription": "<div>Design systems</div>",
            },
        ]
    }

    jobs = JobicySource(http, max_pages=2).discover()

    assert len(jobs) == 2
    assert http.calls == [("get_json", "https://jobicy.com/api/v2/remote-jobs", {})]
    assert jobs[0].source == "jobicy"
    assert jobs[0].source_job_id == "152035"
    assert jobs[0].title == "Senior Front-End Developer (React / TypeScript) - Market Research"
    assert jobs[0].company == "Truelogic"
    assert jobs[0].location == "LATAM"
    assert jobs[0].url == "https://jobicy.com/jobs/152035-senior-front-end-developer-react-typescript-market-research"
    assert jobs[0].description == "React TypeScript"
    assert jobs[0].remote is True
    assert jobs[1].url == "https://jobicy.com/jobs/152036-staff-frontend-engineer"


def test_jobicy_skips_malformed_records(fake_http):
    fake_http.json_data = {
        "jobs": [
            {
                "id": 152035,
                "url": "https://jobicy.com/jobs/152035-senior-front-end-developer-react-typescript-market-research",
                "jobTitle": "Senior Front-End Developer",
                "companyName": "Truelogic",
                "jobGeo": "LATAM",
                "jobDescription": "<p>React</p>",
            },
            {
                "id": None,
                "url": "",
                "jobTitle": "",
                "companyName": "Broken Co",
                "jobDescription": "<p>Missing core fields</p>",
            },
        ]
    }

    jobs = JobicySource(fake_http).discover()

    assert len(jobs) == 1
    assert jobs[0].company == "Truelogic"


def test_himalayas_maps_public_posting_and_uses_cursor_pagination():
    pages = [
        {
            "jobs": [
                {
                    "guid": "https://himalayas.app/companies/acme/jobs/senior-product-engineer",
                    "title": "Senior Product Engineer",
                    "companyName": "Acme",
                    "locationRestrictions": ["Europe", "UK"],
                    "description": "<p>React <strong>TypeScript</strong></p>",
                    "applicationLink": "https://boards.greenhouse.io/acme/jobs/123?gh_src=tracker#apply",
                }
            ],
            "nextCursor": "cursor-2",
        },
        {
            "jobs": [
                {
                    "guid": "https://himalayas.app/companies/acme/jobs/staff-frontend-engineer",
                    "title": "Staff Frontend Engineer",
                    "companyName": "Acme",
                    "locationRestrictions": [],
                    "description": "<div>Design systems</div>",
                    "applicationLink": "https://jobs.ashbyhq.com/acme/staff?utm_source=himalayas",
                }
            ]
        },
    ]

    class CursorHttp(FakeHttp):
        def __init__(self):
            super().__init__()
            self.index = 0

        def get_json(self, url, **kwargs):
            self.calls.append(("get_json", url, kwargs))
            page = pages[self.index]
            self.index += 1
            return page

    http = CursorHttp()

    jobs = HimalayasSource(http, max_pages=2).discover()

    assert len(jobs) == 2
    assert http.calls == [
        ("get_json", "https://himalayas.app/jobs/api", {}),
        ("get_json", "https://himalayas.app/jobs/api", {"params": {"cursor": "cursor-2"}}),
    ]
    assert jobs[0].source == "himalayas"
    assert jobs[0].source_job_id == "https://himalayas.app/companies/acme/jobs/senior-product-engineer"
    assert jobs[0].company == "Acme"
    assert jobs[0].title == "Senior Product Engineer"
    assert jobs[0].location == "Europe, UK"
    assert jobs[0].url == "https://boards.greenhouse.io/acme/jobs/123"
    assert jobs[0].description == "React TypeScript"
    assert jobs[0].remote is True
    assert jobs[1].url == "https://jobs.ashbyhq.com/acme/staff"


def test_himalayas_skips_malformed_records(fake_http):
    fake_http.json_data = {
        "jobs": [
            {
                "guid": "https://himalayas.app/companies/acme/jobs/senior-product-engineer",
                "title": "Senior Product Engineer",
                "companyName": "Acme",
                "description": "<p>React</p>",
                "applicationLink": "https://boards.greenhouse.io/acme/jobs/123",
            },
            {
                "guid": "",
                "title": "Broken Job",
                "companyName": "",
                "description": "<p>Missing fields</p>",
                "applicationLink": "",
            },
        ]
    }

    jobs = HimalayasSource(fake_http).discover()

    assert len(jobs) == 1
    assert jobs[0].source_job_id == "https://himalayas.app/companies/acme/jobs/senior-product-engineer"


def test_build_sources_includes_always_on_and_configured_ats(fake_http, policy):
    settings = Settings(
        gemini_api_key="g",
        candidate_profile="profile",
        cover_letter_template="template",
        timezone="Europe/Berlin",
        scheduled_hour=9,
        policy=policy,
        gemini_quota=GeminiQuotaSettings(rpm=10, tpm=250000, rpd=500),
    )
    sources = build_sources(settings, fake_http)
    kinds = [type(s).__name__ for s in sources]
    assert "RemotiveSource" in kinds
    assert "ArbeitnowSource" in kinds
    assert "JobicySource" in kinds
    assert "HimalayasSource" in kinds
    assert "DuckDuckGoSource" in kinds
    assert "AshbySource" in kinds
    assert "LeverSource" in kinds
    assert "GreenhouseSource" in kinds


def test_duckduckgo_opens_circuit_after_consecutive_failures():
    class FailingHttp(FakeHttp):
        def get(self, url, **kwargs):
            self.calls.append(("get", url, kwargs))
            raise RuntimeError("network down")

    http = FailingHttp()
    breaker = CircuitBreaker(failure_threshold=2)

    jobs = DuckDuckGoSource(http, ["q1", "q2", "q3", "q4"], breaker=breaker).discover()

    assert jobs == []
    assert len(http.calls) == 2
    assert breaker.is_open


def test_duckduckgo_circuit_stays_closed_when_a_query_succeeds(fake_http):
    fake_http.text = _DUCKDUCKGO_HTML
    breaker = CircuitBreaker(failure_threshold=2)

    jobs = DuckDuckGoSource(fake_http, ["q1", "q2", "q3"], breaker=breaker).discover()

    assert len(jobs) == 3
    assert not breaker.is_open


def test_duckduckgo_shared_breaker_blocks_a_later_source_instance():
    class FailingHttp(FakeHttp):
        def get(self, url, **kwargs):
            self.calls.append(("get", url, kwargs))
            raise RuntimeError("network down")

    http = FailingHttp()
    breaker = CircuitBreaker(failure_threshold=1)

    DuckDuckGoSource(http, ["q1"], breaker=breaker).discover()
    DuckDuckGoSource(http, ["q2"], breaker=breaker).discover()

    assert len(http.calls) == 1
