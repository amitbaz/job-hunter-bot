from job_hunter.models import SearchPolicy


def generate_search_queries(policy: SearchPolicy) -> list[str]:
    legacy_queries: list[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        normalized = " ".join(query.split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            legacy_queries.append(normalized)

    for role in policy.role_families:
        for template in policy.search_query_templates:
            base = template.format(role=role)
            add(base)
            for domain in policy.search_domains:
                add(f"site:{domain} {base}")
    for query in policy.search_queries:
        add(query)

    if not policy.specialist_search_domains or not policy.specialist_query_templates:
        return legacy_queries[: policy.max_search_queries_per_run]

    specialist_slots = min(4, policy.max_search_queries_per_run)
    queries = legacy_queries[: policy.max_search_queries_per_run - specialist_slots]
    seen = set(queries)
    specialist_queries: list[str] = []

    def add_specialist(query: str) -> None:
        normalized = " ".join(query.split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            specialist_queries.append(normalized)

    for role in policy.role_families:
        for template in policy.specialist_query_templates:
            base = template.format(role=role)
            for domain in policy.specialist_search_domains:
                add_specialist(f"site:{domain} {base}")

    return queries + specialist_queries[:specialist_slots]
