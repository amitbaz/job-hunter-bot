import pytest

from job_hunter.discovery import collect_candidates
from job_hunter.models import Evaluation, Job, SearchPolicy
from job_hunter.store import JobStore


class FakeSource:
    def __init__(self, jobs):
        self._jobs = jobs

    def discover(self):
        return self._jobs


class BrokenSource:
    def discover(self):
        raise RuntimeError("source is down")


class NoOpHttp:
    def get(self, url, **kwargs):
        raise AssertionError(f"unexpected enrichment fetch for {url!r}")


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeHttp:
    def __init__(self, html):
        self._html = html
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return FakeResponse(self._html)


_JOB_POSTING_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@type": "JobPosting",
  "title": "Senior Product Engineer",
  "hiringOrganization": {"name": "Acme"},
  "jobLocationType": "TELECOMMUTE",
  "description": "<p>Loves React and TypeScript</p>"
}
</script>
</head><body></body></html>
"""


@pytest.fixture
def policy():
    return SearchPolicy(
        target_titles=["senior product engineer"],
        positive_keywords=["react"],
        blocked_title_keywords=["junior"],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
        max_jobs_per_run=25,
    )


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "state.sqlite3")


def test_collect_candidates_continues_after_source_failure(store, policy):
    broken = BrokenSource()
    good = FakeSource(
        [
            Job(
                source="x",
                source_job_id="1",
                title="Senior Product Engineer",
                description="React TypeScript",
                remote=True,
            )
        ]
    )
    result = collect_candidates([broken, good], store, NoOpHttp(), policy)
    assert result.stats.raw == 1
    assert len(result.eligible) == 1


def test_collect_candidates_collapses_same_canonical_url(store, policy):
    jobs = [
        Job(
            source="duckduckgo",
            title="Senior Product Engineer",
            url="https://jobs.ashbyhq.com/acme/1?utm_source=x",
        ),
        Job(
            source="ashby",
            source_job_id="1",
            title="Senior Product Engineer",
            company="Acme",
            url="https://jobs.ashbyhq.com/acme/1",
            description="React TypeScript",
            remote=True,
        ),
    ]
    result = collect_candidates([FakeSource(jobs)], store, NoOpHttp(), policy)
    assert result.stats.unique == 1
    assert len(result.eligible) == 1
    assert result.eligible[0][1].company == "Acme"


def test_collect_candidates_enriches_url_only_job(store, policy):
    job = Job(source="duckduckgo", title="", url="https://example.com/jobs/1")
    http = FakeHttp(_JOB_POSTING_HTML)

    result = collect_candidates([FakeSource([job])], store, http, policy)

    assert http.calls == ["https://example.com/jobs/1"]
    assert len(result.eligible) == 1
    enriched = result.eligible[0][1]
    assert enriched.title == "Senior Product Engineer"
    assert enriched.company == "Acme"
    assert enriched.remote is True
    assert "React" in enriched.description


def test_collect_candidates_does_not_reenrich_job_with_description(store, policy):
    job = Job(
        source="ashby",
        source_job_id="1",
        title="Senior Product Engineer",
        company="Acme",
        url="https://jobs.ashbyhq.com/acme/1",
        description="React TypeScript",
        remote=True,
    )

    result = collect_candidates([FakeSource([job])], store, NoOpHttp(), policy)

    assert len(result.eligible) == 1


def test_collect_candidates_counts_prefilter_rejections(store, policy):
    irrelevant_job = Job(
        source="x",
        source_job_id="2",
        title="Junior QA Tester",
        description="manual testing",
    )

    result = collect_candidates([FakeSource([irrelevant_job])], store, NoOpHttp(), policy)

    assert result.stats.prefilter_rejected == 1
    assert result.eligible == []
    assert result.rediscovered_job_ids == []


def test_collect_candidates_excludes_already_evaluated_unchanged_job(store, policy):
    job = Job(
        source="ashby",
        source_job_id="1",
        title="Senior Product Engineer",
        company="Acme",
        description="React TypeScript remote role",
        remote=True,
    )
    job_id, _is_new, _changed = store.upsert_job(job)
    store.save_evaluation(
        job_id,
        Evaluation(
            job_id=job_id,
            total_score=90,
            scores={},
            decision="high_priority",
            hard_blockers=[],
            strengths=[],
            gaps=[],
            salary_note="",
            location_note="",
            rationale="",
            model="gemini-test",
            status="ok",
        ),
    )

    result = collect_candidates([FakeSource([job])], store, NoOpHttp(), policy)

    assert result.eligible == []
    assert result.rediscovered_job_ids == [job_id]
