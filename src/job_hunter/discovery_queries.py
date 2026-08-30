from job_hunter.models import SearchPolicy


def generate_search_queries(policy: SearchPolicy) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        normalized = " ".join(query.split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            queries.append(normalized)

    for role in policy.role_families:
        for template in policy.search_query_templates:
            base = template.format(role=role)
            add(base)
            for domain in policy.search_domains:
                add(f"site:{domain} {base}")

    for query in policy.search_queries:
        add(query)

    return queries[: policy.max_search_queries_per_run]
