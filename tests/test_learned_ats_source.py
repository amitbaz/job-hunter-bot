from datetime import datetime, timedelta, timezone

from job_hunter.sources.learned_ats import LearnedAtsSource, LearnedAtsStats
from job_hunter.store import JobStore


class RoutingHttp:
    def __init__(self, responses=None, fail_urls=None):
        self.responses = responses or {}
        self.fail_urls = fail_urls or set()
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(url)
        for marker in self.fail_urls:
            if marker in url:
                raise RuntimeError("network down")
        for marker, payload in self.responses.items():
            if marker in url:
                return payload
        raise RuntimeError(f"no fake response configured for {url}")


def _seed_board(store, provider, board_identifier, market_hint="berlin"):
    store.upsert_ats_board(
        provider=provider,
        board_identifier=board_identifier,
        company_name=board_identifier,
        market_hint=market_hint,
    )


def test_learned_ats_source_scans_due_boards_through_native_adapters():
    store = JobStore(":memory:")
    _seed_board(store, "ashby", "acme-ashby")
    _seed_board(store, "lever", "acme-lever")
    _seed_board(store, "greenhouse", "acme-greenhouse")
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)

    http = RoutingHttp(
        responses={
            "ashbyhq.com": {
                "jobs": [
                    {
                        "id": 1,
                        "title": "Senior Product Engineer",
                        "location": "Remote",
                        "jobUrl": "https://jobs.ashbyhq.com/acme-ashby/1",
                        "descriptionPlain": "React",
                        "isRemote": True,
                    }
                ]
            },
            "lever.co": [
                {
                    "id": "2",
                    "text": "Senior Product Engineer",
                    "categories": {"location": "Remote"},
                    "hostedUrl": "https://jobs.lever.co/acme-lever/2",
                    "descriptionPlain": "React",
                    "workplaceType": "remote",
                }
            ],
            "greenhouse.io": {
                "jobs": [
                    {
                        "id": 3,
                        "title": "Senior Product Engineer",
                        "location": {"name": "Remote"},
                        "absolute_url": "https://boards.greenhouse.io/acme-greenhouse/3",
                        "content": "React",
                    }
                ]
            },
        }
    )

    source = LearnedAtsSource(
        store, http, limit=10, market_order=["berlin"], now=lambda: now
    )
    jobs = source.discover()

    assert sorted(job.source for job in jobs) == ["ashby", "greenhouse", "lever"]
    assert source.stats == LearnedAtsStats(
        boards_scanned=3, boards_successful=3, boards_failed=0, jobs_raw=3
    )


def test_learned_ats_source_isolates_a_failing_board_from_a_healthy_one():
    store = JobStore(":memory:")
    _seed_board(store, "ashby", "broken-ashby")
    _seed_board(store, "lever", "healthy-lever")
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)

    http = RoutingHttp(
        responses={
            "lever.co": [
                {
                    "id": "2",
                    "text": "Senior Product Engineer",
                    "categories": {"location": "Remote"},
                    "hostedUrl": "https://jobs.lever.co/healthy-lever/2",
                    "descriptionPlain": "React",
                    "workplaceType": "remote",
                }
            ],
        },
        fail_urls={"ashbyhq.com"},
    )

    source = LearnedAtsSource(
        store, http, limit=10, market_order=["berlin"], now=lambda: now
    )
    jobs = source.discover()

    assert [job.source_job_id for job in jobs] == ["2"]
    assert jobs[0].source == "lever"
    assert source.stats.boards_scanned == 2
    assert source.stats.boards_successful == 1
    assert source.stats.boards_failed == 1
    assert source.stats.jobs_raw == 1

    later = now + timedelta(hours=25)
    entries = {entry.board_identifier: entry for entry in store.list_due_ats_boards(later)}
    assert entries["broken-ashby"].consecutive_failures == 1
    assert entries["broken-ashby"].last_success_at is None
    assert entries["healthy-lever"].consecutive_failures == 0
    assert entries["healthy-lever"].last_success_at == now.isoformat()
    assert entries["healthy-lever"].last_job_count == 1
