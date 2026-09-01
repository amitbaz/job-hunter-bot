import logging

import pytest

from job_hunter.discovery import collect_candidates
from job_hunter.models import (
    AtsReference,
    CanonicalResolution,
    Evaluation,
    Job,
    SearchPolicy,
)
from job_hunter.store import JobStore


class FakeSource:
    def __init__(self, jobs):
        self._jobs = jobs

    def discover(self):
        return self._jobs


class FakeResolver:
    def __init__(self, resolution):
        self._resolution = resolution

    def resolve(self, job):
        return self._resolution


class FailFirstResolver:
    def __init__(self):
        self.calls = 0

    def resolve(self, job):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("resolver unavailable")
        return None


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


def test_collect_candidates_logs_canonical_dedupe_metrics_without_job_content(
    store, policy, caplog
):
    sources = [
        FakeSource(
            [
                Job(
                    source=source,
                    title="Senior Product Engineer",
                    company="Acme",
                    location="Berlin",
                    url=url,
                    description="PRIVATE_GMAIL_BODY React TypeScript",
                    remote=True,
                )
            ]
        )
        for source, url in (
            ("gmail:linkedin", "https://linkedin.test/1"),
            ("yc", "https://yc.test/2"),
            ("specialist", "https://specialist.test/3"),
        )
    ]
    resolver = FakeResolver(
        CanonicalResolution(
            url="https://jobs.lever.co/acme/abc",
            ats=AtsReference(provider="lever", board="acme", job_id="abc"),
            confidence=1.0,
            method="test",
        )
    )

    with caplog.at_level(logging.INFO):
        result = collect_candidates(
            sources,
            store,
            NoOpHttp(),
            policy,
            resolver=resolver,
        )

    assert result.stats.raw == 3
    assert result.stats.unique == 1
    assert result.stats.canonical_resolved == 3
    assert result.stats.canonical_unresolved == 0
    assert result.stats.cross_source_duplicates == 2
    assert store.count_jobs() == 1
    job_id = result.eligible[0][0]
    provenance = store.list_job_sources(job_id)
    assert {row["source"] for row in provenance} == {
        "gmail:linkedin",
        "yc",
        "specialist",
    }
    assert {row["source_url"] for row in provenance} == {
        "https://linkedin.test/1",
        "https://yc.test/2",
        "https://specialist.test/3",
    }
    assert "gmail:linkedin=1 specialist=1 yc=1" in caplog.text
    assert "canonical_resolved=3" in caplog.text
    assert "canonical_unresolved=0" in caplog.text
    assert "cross_source_duplicates=2" in caplog.text
    assert "PRIVATE_GMAIL_BODY" not in caplog.text


def test_collect_candidates_late_canonicalization_keeps_history_job_id(store, policy):
    legacy_url = "https://aggregator.test/jobs/acme-frontend"
    canonical_url = "https://jobs.lever.co/acme/abc"
    legacy = Job(
        source="aggregator",
        source_job_id="legacy-1",
        title="Senior Product Engineer",
        company="Acme GmbH",
        location="Berlin",
        url=legacy_url,
        description="React TypeScript",
        remote=True,
    )
    legacy_id, _, _ = store.upsert_job(legacy)
    store.save_evaluation(
        legacy_id,
        Evaluation(
            job_id=legacy_id,
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
    canonical_id, _, _ = store.upsert_job(
        Job(
            source="lever",
            source_job_id="abc",
            title="senior product engineer",
            company="ACME",
            location="Berlin",
            url=canonical_url,
            canonical_url=canonical_url,
            ats_provider="lever",
            ats_board="acme",
            ats_job_id="abc",
        )
    )
    resolution = CanonicalResolution(
        url=canonical_url,
        ats=AtsReference(provider="lever", board="acme", job_id="abc"),
        confidence=1.0,
        method="test",
    )

    result = collect_candidates(
        [FakeSource([legacy])],
        store,
        NoOpHttp(),
        policy,
        resolver=FakeResolver(resolution),
    )

    assert canonical_id != legacy_id
    assert store.count_jobs() == 1
    assert result.eligible == []
    assert result.rediscovered_job_ids == [legacy_id]
    assert store.get_evaluation(legacy_id) is not None
    assert store.get_job(legacy_id).url == canonical_url

    rerun = collect_candidates(
        [FakeSource([legacy])],
        store,
        NoOpHttp(),
        policy,
        resolver=FakeResolver(resolution),
    )

    assert store.count_jobs() == 1
    assert rerun.rediscovered_job_ids == [legacy_id]


def test_collect_candidates_counts_unresolved_canonical_urls(store, policy):
    job = Job(
        source="yc",
        title="Senior Product Engineer",
        company="Acme",
        url="https://yc.test/unresolved",
        description="React TypeScript",
        remote=True,
    )

    result = collect_candidates(
        [FakeSource([job])],
        store,
        NoOpHttp(),
        policy,
        resolver=FakeResolver(None),
    )

    assert result.stats.canonical_resolved == 0
    assert result.stats.canonical_unresolved == 1
    assert result.eligible[0][1].url == "https://yc.test/unresolved"


def test_resolver_exception_preserves_candidate_and_continues_collection(store, policy):
    first = Job(
        source="first",
        title="Senior Product Engineer",
        company="Acme",
        url="https://first.test/jobs/1",
        description="React TypeScript",
        remote=True,
    )
    second = Job(
        source="second",
        title="Senior Product Engineer",
        company="Beta",
        url="https://second.test/jobs/2",
        description="React TypeScript",
        remote=True,
    )

    result = collect_candidates(
        [FakeSource([first]), FakeSource([second])],
        store,
        NoOpHttp(),
        policy,
        resolver=FailFirstResolver(),
    )

    assert result.stats.raw == 2
    assert result.stats.unique == 2
    assert result.stats.canonical_unresolved == 2
    assert first.url == "https://first.test/jobs/1"
    assert {job.source for _job_id, job in result.eligible} == {"first", "second"}
    assert store.count_jobs() == 2
    stored_urls = {
        row["source"]: row["url"]
        for row in store._conn.execute("SELECT source, url FROM jobs")
    }
    assert stored_urls == {
        "first": "https://first.test/jobs/1",
        "second": "https://second.test/jobs/2",
    }
