from job_hunter.discovery_queries import generate_search_queries
from job_hunter.models import SearchPolicy


def make_policy(limit: int = 10) -> SearchPolicy:
    return SearchPolicy(
        target_titles=[],
        positive_keywords=[],
        blocked_title_keywords=[],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
        search_queries=['"Senior Product Engineer" remote'],
        role_families=["staff product engineer", "senior software engineer frontend"],
        search_query_templates=['"{role}" React TypeScript remote Europe'],
        search_domains=["jobs.ashbyhq.com"],
        max_search_queries_per_run=limit,
    )


def test_generate_search_queries_is_deterministic():
    queries = generate_search_queries(make_policy())
    assert queries == [
        '"staff product engineer" React TypeScript remote Europe',
        'site:jobs.ashbyhq.com "staff product engineer" React TypeScript remote Europe',
        '"senior software engineer frontend" React TypeScript remote Europe',
        'site:jobs.ashbyhq.com "senior software engineer frontend" React TypeScript remote Europe',
        '"Senior Product Engineer" remote',
    ]


def test_generate_search_queries_enforces_limit():
    assert len(generate_search_queries(make_policy(limit=3))) == 3


def test_generate_search_queries_includes_specialist_domain_queries_within_limit():
    policy = SearchPolicy(
        target_titles=[],
        positive_keywords=[],
        blocked_title_keywords=[],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
        search_queries=[],
        role_families=["senior frontend engineer"],
        search_query_templates=[],
        search_domains=[],
        max_search_queries_per_run=2,
        specialist_search_domains=["wellfound.com", "app.welcometothejungle.com"],
        specialist_query_templates=['"{role}" remote Europe'],
        yc_job_pages=[],
        manual_company_watch=[],
    )

    queries = generate_search_queries(policy)

    assert queries == [
        'site:wellfound.com "senior frontend engineer" remote Europe',
        'site:app.welcometothejungle.com "senior frontend engineer" remote Europe',
    ]
    assert len(queries) <= policy.max_search_queries_per_run


def test_generate_search_queries_reserves_specialist_slots_without_dropping_role_coverage():
    policy = SearchPolicy(
        target_titles=[],
        positive_keywords=[],
        blocked_title_keywords=[],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
        search_queries=[],
        role_families=[
            "senior product engineer",
            "senior frontend engineer",
            "staff frontend engineer",
            "product engineer",
        ],
        search_query_templates=[
            '"{role}" remote React TypeScript',
            '"{role}" remote Europe',
        ],
        search_domains=["jobs.ashbyhq.com", "jobs.lever.co", "boards.greenhouse.io"],
        specialist_search_domains=["wellfound.com", "app.welcometothejungle.com"],
        specialist_query_templates=['"{role}" remote Europe', '"{role}" Berlin'],
        max_search_queries_per_run=30,
    )

    queries = generate_search_queries(policy)

    assert len(queries) == 30
    for role in policy.role_families:
        assert f'"{role}" remote React TypeScript' in queries
    assert queries[-4:] == [
        'site:wellfound.com "senior product engineer" remote Europe',
        'site:app.welcometothejungle.com "senior product engineer" remote Europe',
        'site:wellfound.com "senior product engineer" Berlin',
        'site:app.welcometothejungle.com "senior product engineer" Berlin',
    ]


def test_generate_search_queries_with_empty_role_families():
    policy = SearchPolicy(
        target_titles=[],
        positive_keywords=[],
        blocked_title_keywords=[],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
        search_queries=['"Senior Product Engineer" remote', '"Frontend Engineer" React'],
        role_families=[],
        search_query_templates=[],
        search_domains=[],
        max_search_queries_per_run=10,
    )
    queries = generate_search_queries(policy)
    assert queries == ['"Senior Product Engineer" remote', '"Frontend Engineer" React']
