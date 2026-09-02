import hashlib
from datetime import date

from job_hunter.models import MarketPolicy, SearchPolicy, SearchQuery


def _rotation_key(run_date: date, market_id: str, text: str) -> str:
    return hashlib.sha256(
        f"{run_date.isoformat()}:{market_id}:{text}".encode("utf-8")
    ).hexdigest()


def allocate_market_query_slots(markets: list[MarketPolicy], budget: int) -> dict[str, int]:
    enabled = [m for m in markets if m.enabled]
    if not enabled or budget <= 0:
        return {}

    if budget < len(enabled):
        return {m.id: 1 for m in enabled[:budget]}

    allocation = {m.id: max(1, round(m.query_share * budget)) for m in enabled}

    def get_order_idx(market_id: str) -> int:
        for i, m in enumerate(enabled):
            if m.id == market_id:
                return i
        return 0

    while sum(allocation.values()) > budget:
        def over_alloc(m: MarketPolicy) -> tuple[float, int]:
            return (allocation[m.id] - (m.query_share * budget), -get_order_idx(m.id))
        target = max((m for m in enabled if allocation[m.id] > 1), key=over_alloc)
        allocation[target.id] -= 1

    while sum(allocation.values()) < budget:
        def under_alloc(m: MarketPolicy) -> tuple[float, int]:
            return ((m.query_share * budget) - allocation[m.id], -get_order_idx(m.id))
        target = max(enabled, key=under_alloc)
        allocation[target.id] += 1

    return allocation


def _generate_legacy_search_queries(policy: SearchPolicy) -> list[str]:
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


def generate_search_queries(policy: SearchPolicy, run_date: date | None = None) -> list[SearchQuery]:
    if not policy.markets:
        legacy_str_queries = _generate_legacy_search_queries(policy)
        return [SearchQuery(text=q, market_id=None) for q in legacy_str_queries]

    if run_date is None:
        run_date = date.today()

    budget = policy.max_search_queries_per_run
    allocations = allocate_market_query_slots(policy.markets, budget)

    results: list[SearchQuery] = []
    seen: set[str] = set()

    for market in policy.markets:
        if market.id not in allocations:
            continue

        slots = allocations[market.id]
        candidates = []
        roles = market.role_families or policy.role_families

        for r_idx, role in enumerate(roles):
            for t_idx, template in enumerate(market.query_templates):
                tier = (r_idx, t_idx)
                base = template.format(role=role)
                candidates.append((tier, base))
                for domain in market.source_domains:
                    candidates.append((tier, f"site:{domain} {base}"))

        market_candidates = []
        local_seen = set()
        for tier, text in candidates:
            text = " ".join(text.split())
            if text and text not in local_seen:
                local_seen.add(text)
                market_candidates.append((tier, _rotation_key(run_date, market.id, text), text))

        market_candidates.sort(key=lambda x: (x[0], x[1]))

        selected = 0
        for _, _, text in market_candidates:
            if selected >= slots:
                break
            if text not in seen:
                seen.add(text)
                results.append(SearchQuery(text=text, market_id=market.id))
                selected += 1

    return results
