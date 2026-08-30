from job_hunter.models import Job, SearchPolicy
from job_hunter.ranking import priority_score, rank_jobs, source_quality


def make_policy() -> SearchPolicy:
    return SearchPolicy(
        target_titles=["senior product engineer", "staff product engineer", "senior frontend engineer"],
        positive_keywords=["react", "typescript", "next.js", "product ownership", "design system"],
        blocked_title_keywords=["junior"],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
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
