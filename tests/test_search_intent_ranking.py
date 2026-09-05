from job_hunter.models import CandidatePreferences, Job, MarketPolicy, SalaryPolicy, SearchPolicy
from job_hunter.ranking import profile_priority_score


def _preferences() -> CandidatePreferences:
    return CandidatePreferences(
        preferred_roles=["frontend architecture"],
        preferred_seniority=["senior"],
        must_have_signals=["react", "typescript"],
        nice_to_have_signals=[],
        preferred_locations=["Berlin"],
        avoid_signals=[],
        summary="CV-inferred preferences that intentionally contradict search intent.",
    )


def _policy() -> SearchPolicy:
    london = MarketPolicy(
        id="london",
        query_share=1.0,
        locations=["London", "UK", "United Kingdom"],
        allowed_languages=["English"],
        salary=SalaryPolicy(currency="GBP", gross_base_floor=90000),
        remote_policy="allowed",
        relocation_policy="allowed",
        sponsorship_policy="required",
    )
    return SearchPolicy(
        target_titles=["senior full-stack engineer"],
        positive_keywords=["react", "typescript"],
        blocked_title_keywords=["junior"],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
        role_families=["senior full-stack engineer"],
        markets=[london],
    )


def test_configured_target_role_outranks_cv_inferred_role_preference():
    policy = _policy()
    preferences = _preferences()
    configured_target = Job(
        source="ashby",
        title="Senior Full-Stack Engineer",
        location="London",
        remote=True,
        description="React TypeScript",
    )
    cv_inferred_target = Job(
        source="ashby",
        title="Senior Frontend Architecture",
        location="London",
        remote=True,
        description="React TypeScript",
    )

    assert profile_priority_score(configured_target, preferences, policy) > profile_priority_score(
        cv_inferred_target, preferences, policy
    )


def test_configured_market_location_outranks_cv_inferred_location_when_unattributed():
    policy = _policy()
    preferences = _preferences()
    configured_market = Job(
        source="ashby",
        title="Senior Full-Stack Engineer",
        location="London",
        remote=True,
        description="React TypeScript",
    )
    cv_inferred_location = Job(
        source="ashby",
        title="Senior Full-Stack Engineer",
        location="Berlin",
        remote=True,
        description="React TypeScript",
    )

    assert profile_priority_score(configured_market, preferences, policy) > profile_priority_score(
        cv_inferred_location, preferences, policy
    )
