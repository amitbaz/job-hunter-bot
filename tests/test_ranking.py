from job_hunter.models import CandidatePreferences, Job, SearchPolicy
from job_hunter.ranking import (
    priority_score,
    profile_priority_score,
    rank_jobs,
    select_diverse_candidates,
    source_quality,
)
from tests.market_fixtures import make_market_policy


def make_policy() -> SearchPolicy:
    return SearchPolicy(
        target_titles=["senior product engineer", "staff product engineer", "senior frontend engineer"],
        positive_keywords=["react", "typescript", "next.js", "product ownership", "design system"],
        blocked_title_keywords=["junior"],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
    )


def make_preferences() -> CandidatePreferences:
    return CandidatePreferences(
        preferred_roles=["staff product engineer", "senior frontend engineer"],
        preferred_seniority=["senior", "staff"],
        must_have_signals=["react", "typescript"],
        nice_to_have_signals=["design system", "mentorship"],
        preferred_locations=["europe", "germany"],
        avoid_signals=["on-site", "manager"],
        summary="Senior frontend/product engineer focused on remote Europe roles.",
    )


def test_product_engineer_outranks_generic_react_role():
    policy = make_policy()
    strong = Job(
        source="ashby",
        title="Staff Product Engineer",
        company="A",
        location="Remote Europe",
        remote=True,
        description="React TypeScript product ownership architecture end-to-end ownership",
    )
    generic = Job(
        source="remotive",
        title="React Developer",
        company="B",
        location="Worldwide",
        remote=True,
        description="React React React React React",
    )
    assert priority_score(strong, policy) > priority_score(generic, policy)


def test_explicit_europe_remote_outranks_unknown_location():
    policy = make_policy()
    europe = Job(source="remotive", title="Senior Frontend Engineer", location="Remote Europe", remote=True, description="React TypeScript")
    unknown = Job(source="remotive", title="Senior Frontend Engineer", location="", remote=True, description="React TypeScript")
    assert priority_score(europe, policy) > priority_score(unknown, policy)


def test_ats_url_gets_higher_source_quality_than_general_web_result():
    ats = Job(source="duckduckgo", title="Senior Product Engineer", url="https://jobs.ashbyhq.com/acme/123")
    web = Job(source="duckduckgo", title="Senior Product Engineer", url="https://example.com/jobs/123")
    assert source_quality(ats) > source_quality(web)


def test_keyword_repetition_is_capped():
    policy = make_policy()
    normal = Job(source="remotive", title="React Developer", description="React TypeScript product ownership")
    spammy = Job(source="remotive", title="React Developer", description="React " * 100)
    assert priority_score(spammy, policy) < priority_score(normal, policy) + 20


def test_rank_jobs_is_stable_for_equal_scores():
    policy = make_policy()
    jobs = [
        (2, Job(source="remotive", title="Senior Frontend Engineer", company="Beta", description="React TypeScript")),
        (1, Job(source="remotive", title="Senior Frontend Engineer", company="Acme", description="React TypeScript")),
    ]
    ranked = rank_jobs(jobs, policy)
    assert [job_id for job_id, _job, _score in ranked] == [1, 2]


def test_profile_priority_score_prefers_role_fit_over_generic_keyword_spam():
    policy = make_policy()
    preferences = make_preferences()
    preferred = Job(
        source="ashby",
        title="Staff Product Engineer",
        company="Acme",
        location="Remote Europe",
        remote=True,
        description="React TypeScript design system mentorship architecture",
    )
    generic = Job(
        source="remotive",
        title="Frontend Developer",
        company="Beta",
        location="Remote Worldwide",
        remote=True,
        description="React " * 30,
    )

    assert profile_priority_score(preferred, preferences, policy) > profile_priority_score(generic, preferences, policy)


def test_profile_priority_score_deducts_avoid_signals():
    policy = make_policy()
    preferences = make_preferences()
    safe = Job(
        source="ashby",
        title="Senior Frontend Engineer",
        company="Acme",
        location="Remote Germany",
        remote=True,
        description="React TypeScript design system",
    )
    risky = Job(
        source="ashby",
        title="Senior Frontend Manager",
        company="Beta",
        location="Berlin on-site",
        remote=False,
        description="React TypeScript design system on-site manager",
    )

    assert profile_priority_score(safe, preferences, policy) > profile_priority_score(risky, preferences, policy)


def test_profile_priority_score_counts_unique_signals_once():
    policy = make_policy()
    preferences = make_preferences()
    unique = Job(
        source="ashby",
        title="Senior Frontend Engineer",
        company="Acme",
        location="Remote Europe",
        remote=True,
        description="React TypeScript design system mentorship",
    )
    repeated = Job(
        source="ashby",
        title="Senior Frontend Engineer",
        company="Beta",
        location="Remote Europe",
        remote=True,
        description="React React React React TypeScript",
    )

    assert profile_priority_score(unique, preferences, policy) > profile_priority_score(repeated, preferences, policy)


def test_select_diverse_candidates_takes_minimum_per_source_first():
    ranked = [
        (101, Job(source="ashby", title="Role", company="A1"), 95),
        (102, Job(source="ashby", title="Role", company="A2"), 94),
        (201, Job(source="remotive", title="Role", company="B1"), 70),
        (202, Job(source="remotive", title="Role", company="B2"), 69),
        (301, Job(source="hackernews", title="Role", company="C1"), 60),
    ]

    selected = select_diverse_candidates(ranked, limit=4, minimum_per_source=1, max_share=0.75)

    assert [job_id for job_id, _job, _score in selected] == [101, 201, 301, 102]


def test_select_diverse_candidates_respects_max_share_when_filling():
    ranked = [
        (101, Job(source="ashby", title="Role", company="A1"), 99),
        (102, Job(source="ashby", title="Role", company="A2"), 98),
        (103, Job(source="ashby", title="Role", company="A3"), 97),
        (201, Job(source="remotive", title="Role", company="B1"), 96),
        (202, Job(source="remotive", title="Role", company="B2"), 95),
        (301, Job(source="hackernews", title="Role", company="C1"), 94),
    ]

    selected = select_diverse_candidates(ranked, limit=4, minimum_per_source=1, max_share=0.5)

    assert [job_id for job_id, _job, _score in selected] == [101, 201, 301, 102]


def test_select_diverse_candidates_preserves_rank_order_for_ties():
    ranked = [
        (2, Job(source="ashby", title="Role", company="Beta"), 90),
        (1, Job(source="ashby", title="Role", company="Acme"), 90),
        (3, Job(source="remotive", title="Role", company="Gamma"), 80),
    ]

    selected = select_diverse_candidates(ranked, limit=3, minimum_per_source=2, max_share=1.0)

    assert [job_id for job_id, _job, _score in selected] == [2, 1, 3]


def test_select_diverse_candidates_fills_budget_when_share_cap_blocks_remaining_slots():
    ranked = [
        *[(100 + index, Job(source="ashby", title="Role", company=f"A{index:02d}"), 100 - index) for index in range(20)],
        *[(200 + index, Job(source="remotive", title="Role", company=f"B{index:02d}"), 80 - index) for index in range(20)],
    ]

    selected = select_diverse_candidates(ranked, limit=35, minimum_per_source=2, max_share=0.5)

    assert len(selected) == 35
    counts = {}
    for _job_id, job, _score in selected:
        counts[job.source] = counts.get(job.source, 0) + 1
    assert counts == {"ashby": 18, "remotive": 17}


def test_frontend_heavy_full_stack_beats_backend_heavy_full_stack():
    policy = make_market_policy()
    preferences = make_preferences()
    frontend_heavy = Job(source="wellfound", title="Full-Stack Engineer", market_id="germany_eu", description="React Next.js TypeScript frontend, Node.js REST APIs and PostgreSQL")
    backend_heavy = Job(source="wellfound", title="Full-Stack Engineer", market_id="germany_eu", description="Go Java Kubernetes distributed systems event-driven backend architecture")
    assert profile_priority_score(frontend_heavy, preferences, policy) > profile_priority_score(backend_heavy, preferences, policy)


def test_london_hybrid_gets_nonzero_location_fit():
    policy = make_market_policy()
    job = Job(source="x", title="Senior Frontend Engineer", location="London - Hybrid", remote=False, market_id="london", description="React TypeScript")
    assert profile_priority_score(job, make_preferences(), policy) > 0


def test_strong_london_role_can_outrank_weak_germany_role():
    # Germany is the highest-priority market (first in policy.markets), so its
    # market_priority_bonus outweighs London's. A much stronger overall London
    # candidate should still outrank a weak Germany candidate: the market
    # bonus must be a modest nudge, not an absolute partition.
    policy = make_market_policy()
    preferences = make_preferences()
    strong_london = Job(
        source="ashby",
        title="Senior Frontend Engineer",
        company="A",
        location="London",
        remote=False,
        market_id="london",
        description="React TypeScript design system mentorship architecture",
    )
    weak_germany = Job(
        source="duckduckgo",
        title="Backend Developer",
        company="B",
        location="",
        remote=False,
        market_id="germany_eu",
        description="Some role at a startup.",
    )
    assert profile_priority_score(strong_london, preferences, policy) > profile_priority_score(weak_germany, preferences, policy)


def test_market_priority_bonus_rewards_higher_priority_market():
    policy = make_market_policy()
    from job_hunter.ranking import market_priority_bonus

    germany_job = Job(source="ashby", title="x", market_id="germany_eu")
    london_job = Job(source="ashby", title="x", market_id="london")
    no_market_job = Job(source="ashby", title="x")
    unknown_market_job = Job(source="ashby", title="x", market_id="nowhere")

    assert market_priority_bonus(germany_job, policy) > market_priority_bonus(london_job, policy) > 0
    assert market_priority_bonus(no_market_job, policy) == 0
    assert market_priority_bonus(unknown_market_job, policy) == 0


def test_specialist_board_url_source_quality_between_ats_and_generic_web():
    ats = Job(source="ashby", title="x", url="https://jobs.ashbyhq.com/acme/1")
    specialist = Job(source="duckduckgo", title="x", url="https://wellfound.com/jobs/123")
    generic = Job(source="duckduckgo", title="x", url="https://example.com/jobs/123")
    assert source_quality(ats) > source_quality(specialist) > source_quality(generic)
