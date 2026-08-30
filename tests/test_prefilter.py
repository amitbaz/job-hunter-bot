import pytest
from job_hunter.models import Job, SearchPolicy
from job_hunter.prefilter import prefilter_job


@pytest.fixture
def policy():
    return SearchPolicy(
        target_titles=["senior product engineer", "senior frontend engineer"],
        positive_keywords=["react", "typescript"],
        blocked_title_keywords=["junior", "qa", "devops"],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
    )


def test_prefilter_blocks_explicit_non_remote(policy):
    result = prefilter_job(
        Job(source="x", title="Senior Frontend Engineer", location="Berlin - onsite", remote=False),
        policy,
    )
    assert result.hard_blocker is True
    assert "remote" in result.reason.lower()


def test_prefilter_blocks_blocked_title(policy):
    result = prefilter_job(
        Job(source="x", title="Junior Frontend Engineer", description="React TypeScript"),
        policy,
    )
    assert result.hard_blocker is True


def test_prefilter_keeps_relevant_remote_role(policy):
    result = prefilter_job(
        Job(source="x", title="Senior Product Engineer", description="React TypeScript remote product ownership"),
        policy,
    )
    assert result.should_evaluate is True
    assert result.hard_blocker is False


def test_prefilter_passes_ambiguous_location(policy):
    result = prefilter_job(
        Job(source="x", title="Senior Frontend Engineer", location=""),
        policy,
    )
    assert result.hard_blocker is False


def test_prefilter_blocks_unrelated_title_and_no_keywords(policy):
    result = prefilter_job(
        Job(source="x", title="Data Scientist ML Research", description="Python pandas sklearn"),
        policy,
    )
    assert result.should_evaluate is False
