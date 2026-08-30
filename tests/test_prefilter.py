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
        engineering_title_keywords=["engineer", "developer"],
        engineering_title_phrases=[
            "technical lead",
            "frontend lead",
            "software architect",
        ],
        blocked_profession_title_phrases=[
            "product manager",
            "product designer",
            "sales engineer",
            "data engineer",
            "machine learning engineer",
            "ios engineer",
        ],
    )


def test_prefilter_blocks_explicit_non_remote(policy):
    result = prefilter_job(
        Job(source="x", title="Senior Frontend Engineer", location="Berlin - onsite", remote=False),
        policy,
    )
    assert result.hard_blocker is True
    assert result.reason_code == "not_remote"
    assert "remote" in result.reason.lower()


def test_prefilter_blocks_blocked_title(policy):
    result = prefilter_job(
        Job(source="x", title="Junior Frontend Engineer", description="React TypeScript"),
        policy,
    )
    assert result.hard_blocker is True
    assert result.reason_code == "blocked_title"


def test_prefilter_keeps_relevant_remote_role(policy):
    result = prefilter_job(
        Job(source="x", title="Senior Product Engineer", description="React TypeScript remote product ownership"),
        policy,
    )
    assert result.should_evaluate is True
    assert result.hard_blocker is False
    assert result.reason_code == "passed"


def test_prefilter_passes_ambiguous_location(policy):
    result = prefilter_job(
        Job(source="x", title="Senior Frontend Engineer", location=""),
        policy,
    )
    assert result.hard_blocker is False
    assert result.reason_code == "passed"


def test_prefilter_blocks_unrelated_title_and_no_keywords(policy):
    result = prefilter_job(
        Job(source="x", title="Data Scientist ML Research", description="Python pandas sklearn"),
        policy,
    )
    assert result.should_evaluate is False
    assert result.reason_code == "off_target_profession"


@pytest.mark.parametrize(
    "title",
    [
        "Senior Product Engineer",
        "Staff Frontend Engineer",
        "Senior Software Engineer, Product",
        "Founding Engineer",
        "Frontend Developer",
        "Frontend Technical Lead",
    ],
)
def test_prefilter_accepts_software_engineering_professions(policy, title):
    result = prefilter_job(
        Job(source="x", title=title, description="React TypeScript product ownership", remote=True),
        policy,
    )
    assert result.should_evaluate is True


@pytest.mark.parametrize(
    "title",
    [
        "Senior Product Manager",
        "Platform Product Manager",
        "Technical Product Manager",
        "Senior Product Designer",
        "Product Designer, AI",
        "Senior Sales Engineer",
        "Senior Data Engineer",
        "Machine Learning Engineer",
        "Senior iOS Engineer",
    ],
)
def test_prefilter_rejects_off_target_professions_even_with_positive_keywords(policy, title):
    result = prefilter_job(
        Job(
            source="x",
            title=title,
            description="React TypeScript SaaS product ownership architecture",
            remote=True,
        ),
        policy,
    )
    assert result.should_evaluate is False
    assert result.reason_code == "off_target_profession"


def test_prefilter_blocked_title_wins_before_profession_acceptance(policy):
    result = prefilter_job(
        Job(source="x", title="Junior Frontend Engineer", description="React TypeScript", remote=True),
        policy,
    )
    assert result.should_evaluate is False
    assert result.reason_code == "blocked_title"
