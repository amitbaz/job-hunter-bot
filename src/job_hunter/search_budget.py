from __future__ import annotations

import math
import sqlite3
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_hunter.models import SearchQuery


_CREATE_SEARCH_API_USAGE = """
CREATE TABLE IF NOT EXISTS search_api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    occurred_at TEXT NOT NULL
)
"""

_CREATE_SEARCH_API_USAGE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_search_api_usage_provider_time
ON search_api_usage(provider, occurred_at)
"""


class SearchUsageLedger:
    """Tiny SQLite ledger for metered external search API requests."""

    def __init__(self, db_path: Path | str) -> None:
        self._path = str(db_path)
        with sqlite3.connect(self._path) as conn:
            conn.execute(_CREATE_SEARCH_API_USAGE)
            conn.execute(_CREATE_SEARCH_API_USAGE_INDEX)

    def record(self, *, provider: str, occurred_at: datetime) -> None:
        occurred_at = _normalize_utc(occurred_at)
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                "INSERT INTO search_api_usage (provider, occurred_at) VALUES (?, ?)",
                (provider, occurred_at.isoformat()),
            )

    def count(self, *, provider: str, start_at: datetime, end_at: datetime) -> int:
        start_at = _normalize_utc(start_at)
        end_at = _normalize_utc(end_at)
        with sqlite3.connect(self._path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM search_api_usage
                WHERE provider = ? AND occurred_at >= ? AND occurred_at < ?
                """,
                (provider, start_at.isoformat(), end_at.isoformat()),
            ).fetchone()
        return int(row[0]) if row is not None else 0


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _next_month_start(now: datetime) -> datetime:
    if now.month == 12:
        return now.replace(
            year=now.year + 1,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    return now.replace(
        month=now.month + 1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def brave_queries_available_today(
    ledger: SearchUsageLedger,
    *,
    monthly_limit: int,
    now: datetime,
) -> int:
    """Return today's remaining Brave allowance while respecting a monthly hard cap.

    Remaining monthly capacity is spread across the remaining calendar days.
    Because daily usage is persisted, manual reruns on the same day cannot spend
    another full daily allocation.
    """
    if monthly_limit <= 0:
        return 0

    now = _normalize_utc(now)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = _next_month_start(now)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_day = day_start + timedelta(days=1)

    used_month = ledger.count(provider="brave", start_at=month_start, end_at=next_month)
    remaining_month = max(0, monthly_limit - used_month)
    if remaining_month == 0:
        return 0

    used_today = ledger.count(provider="brave", start_at=day_start, end_at=next_day)
    days_remaining = max(1, (next_month.date() - now.date()).days)
    target_today = math.ceil(remaining_month / days_remaining)
    return max(0, min(remaining_month, target_today - used_today))


def split_queries_for_brave(
    queries: list[SearchQuery],
    *,
    limit: int,
) -> tuple[list[SearchQuery], list[SearchQuery]]:
    """Select scarce Brave queries round-robin across markets; preserve fallback order."""
    if limit <= 0 or not queries:
        return [], list(queries)
    if limit >= len(queries):
        return list(queries), []

    market_order: list[str] = []
    grouped: dict[str, deque[tuple[int, SearchQuery]]] = defaultdict(deque)
    for index, query in enumerate(queries):
        market_key = query.market_id or "legacy"
        if market_key not in grouped:
            market_order.append(market_key)
        grouped[market_key].append((index, query))

    selected_indices: list[int] = []
    selected: list[SearchQuery] = []
    while len(selected) < limit:
        progressed = False
        for market_key in market_order:
            queue = grouped[market_key]
            if not queue:
                continue
            index, query = queue.popleft()
            selected_indices.append(index)
            selected.append(query)
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break

    chosen = set(selected_indices)
    fallback = [query for index, query in enumerate(queries) if index not in chosen]
    return selected, fallback
