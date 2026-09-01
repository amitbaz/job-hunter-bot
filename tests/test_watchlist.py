import pytest

from job_hunter.models import CompanyWatchSeed, Evaluation, Job
from job_hunter.store import JobStore
from job_hunter.watchlist import (
    promote_company,
    should_auto_promote,
    sync_manual_watch_seeds,
)


def _evaluation(decision, **overrides):
    values = dict(
        job_id=1,
        total_score=80,
        scores={},
        decision=decision,
        hard_blockers=[],
        strengths=[],
        gaps=[],
        salary_note="",
        location_note="",
        rationale="",
        model="test",
    )
    values.update(overrides)
    return Evaluation(**values)


@pytest.mark.parametrize(
    "decision,expected",
    [
        ("high_priority", True),
        ("package_match", True),
        ("possible_match", False),
        ("skip", False),
        ("blocked", False),
    ],
)
def test_auto_promotion_uses_final_decision(decision, expected):
    assert should_auto_promote(_evaluation(decision)) is expected


def test_auto_promotion_rejects_hard_blockers():
    evaluation = _evaluation("high_priority", hard_blockers=["work authorization"])

    assert should_auto_promote(evaluation) is False


def test_auto_promotion_rejects_failed_evaluation():
    evaluation = _evaluation("high_priority", status="failed")

    assert should_auto_promote(evaluation) is False


def test_syncing_manual_greenhouse_seed_is_idempotent(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    seed = CompanyWatchSeed(
        company_name="Acme GmbH",
        ats_provider="greenhouse",
        ats_identifier="acme",
    )

    sync_manual_watch_seeds(store, [seed])
    sync_manual_watch_seeds(store, [seed])

    row = store.get_company_watch("ACME")
    assert row is not None
    assert row["company_name"] == "Acme GmbH"
    assert row["promotion_source"] == "manual"
    assert row["ats_provider"] == "greenhouse"
    assert row["ats_identifier"] == "acme"
    assert store._conn.execute("SELECT COUNT(*) FROM company_watch").fetchone()[0] == 1


def test_syncing_manual_generic_careers_seed(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    seed = CompanyWatchSeed(
        company_name="Beta",
        careers_url="https://beta.test/careers",
    )

    sync_manual_watch_seeds(store, [seed])

    row = store.get_company_watch("Beta")
    assert row is not None
    assert row["careers_url"] == "https://beta.test/careers"
    assert row["promotion_source"] == "manual"


def test_automatic_promotion_prefers_supported_ats_metadata(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(
        source="greenhouse",
        title="Frontend Engineer",
        company="Acme",
        canonical_url="https://boards.greenhouse.io/acme/jobs/123",
        ats_provider="greenhouse",
        ats_board="acme",
        ats_job_id="123",
    )
    job_id, _, _ = store.upsert_job(job)

    watch_id = promote_company(
        store,
        job_id=job_id,
        job=job,
        evaluation=_evaluation("package_match"),
        package_threshold=75,
    )

    assert watch_id is not None
    row = store.get_company_watch("Acme")
    assert row["ats_provider"] == "greenhouse"
    assert row["ats_identifier"] == "acme"
    assert row["careers_url"] == ""
    assert row["promotion_source"] == "automatic"


def test_automatic_promotion_uses_canonical_url_without_supported_ats(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(
        source="public",
        title="Frontend Engineer",
        company="Beta",
        canonical_url="https://beta.test/careers/frontend-engineer",
    )
    job_id, _, _ = store.upsert_job(job)

    promote_company(
        store,
        job_id=job_id,
        job=job,
        evaluation=_evaluation("high_priority"),
    )

    row = store.get_company_watch("Beta")
    assert row["careers_url"] == "https://beta.test/careers/frontend-engineer"
    assert row["ats_provider"] is None
    assert row["ats_identifier"] is None


def test_automatic_promotion_uses_canonical_url_for_whitespace_ats_board(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(
        source="greenhouse",
        title="Frontend Engineer",
        company="Beta",
        canonical_url="https://beta.test/careers/frontend-engineer",
        ats_provider="greenhouse",
        ats_board="   ",
    )
    job_id, _, _ = store.upsert_job(job)

    promote_company(
        store,
        job_id=job_id,
        job=job,
        evaluation=_evaluation("high_priority"),
    )

    row = store.get_company_watch("Beta")
    assert row["careers_url"] == "https://beta.test/careers/frontend-engineer"
    assert row["ats_provider"] is None
    assert row["ats_identifier"] is None


def test_automatic_promotion_stores_company_only_without_usable_endpoint(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="public", title="Frontend Engineer", company="No Endpoint GmbH")
    job_id, _, _ = store.upsert_job(job)

    promote_company(
        store,
        job_id=job_id,
        job=job,
        evaluation=_evaluation("high_priority"),
    )

    row = store.get_company_watch("No Endpoint")
    assert row is not None
    assert row["careers_url"] == ""
    assert row["ats_provider"] is None
    assert row["ats_identifier"] is None


def test_automatic_promotion_rejects_empty_company_identity(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="public", title="Frontend Engineer", company="GmbH")
    job_id, _, _ = store.upsert_job(job)

    watch_id = promote_company(
        store,
        job_id=job_id,
        job=job,
        evaluation=_evaluation("high_priority"),
    )

    assert watch_id is None
    assert store._conn.execute("SELECT COUNT(*) FROM company_watch").fetchone()[0] == 0


def test_automatic_promotion_rejects_inconsistent_score_below_configured_threshold(
    tmp_path,
):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="public", title="Frontend Engineer", company="Acme")
    job_id, _, _ = store.upsert_job(job)

    watch_id = promote_company(
        store,
        job_id=job_id,
        job=job,
        evaluation=_evaluation("package_match", total_score=74),
        package_threshold=75,
    )

    assert watch_id is None
    assert store.get_company_watch("Acme") is None


def test_automatic_promotion_rejects_non_promotable_decision(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="public", title="Frontend Engineer", company="Acme")
    job_id, _, _ = store.upsert_job(job)

    watch_id = promote_company(
        store,
        job_id=job_id,
        job=job,
        evaluation=_evaluation("possible_match"),
        package_threshold=65,
    )

    assert watch_id is None
    assert store.get_company_watch("Acme") is None
