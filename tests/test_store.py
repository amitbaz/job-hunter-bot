from job_hunter.models import Evaluation, Job
from job_hunter.store import JobStore


def _evaluation(job_id, **overrides):
    defaults = dict(
        job_id=job_id,
        total_score=90,
        scores={},
        decision="high_priority",
        hard_blockers=[],
        strengths=[],
        gaps=[],
        salary_note="",
        location_note="",
        rationale="",
        model="m",
    )
    defaults.update(overrides)
    return Evaluation(**defaults)


def test_upsert_dedupes_and_detects_description_change(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="lever", source_job_id="1", title="Senior Product Engineer", description="React")
    job_id, is_new, changed = store.upsert_job(job)
    assert (is_new, changed) == (True, False)

    same_id, is_new, changed = store.upsert_job(job)
    assert same_id == job_id
    assert (is_new, changed) == (False, False)

    job.description = "React TypeScript"
    same_id, is_new, changed = store.upsert_job(job)
    assert (is_new, changed) == (False, True)


def test_needs_evaluation_new_job(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="x", source_job_id="1", title="Senior Product Engineer")
    job_id, _, _ = store.upsert_job(job)
    assert store.needs_evaluation(job_id) is True


def test_needs_evaluation_false_after_rediscovering_unchanged_job(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="x", source_job_id="1", title="Senior Product Engineer", description="React")
    job_id, _, _ = store.upsert_job(job)
    store.save_evaluation(job_id, _evaluation(job_id))
    assert store.needs_evaluation(job_id) is False

    # Simulate a later run rediscovering the same, unchanged job.
    same_id, is_new, changed = store.upsert_job(job)
    assert (is_new, changed) == (False, False)
    assert store.needs_evaluation(same_id) is False


def test_needs_evaluation_true_after_description_changes_post_evaluation(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="x", source_job_id="1", title="Senior Product Engineer", description="React")
    job_id, _, _ = store.upsert_job(job)
    store.save_evaluation(job_id, _evaluation(job_id))
    assert store.needs_evaluation(job_id) is False

    job.description = "React and TypeScript"
    store.upsert_job(job)
    assert store.needs_evaluation(job_id) is True


def test_count_and_delivery(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="x", source_job_id="1", title="Senior Product Engineer")
    job_id, _, _ = store.upsert_job(job)
    assert store.count_jobs() == 1
    assert store.has_delivery(job_id) is False
    store.mark_delivered(job_id, "telegram_message")
    assert store.has_delivery(job_id) is True


def test_has_delivery_filters_by_type(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="x", source_job_id="1", title="Senior Product Engineer")
    job_id, _, _ = store.upsert_job(job)

    assert store.has_delivery(job_id, "telegram_message") is False
    assert store.has_delivery(job_id, "telegram_document") is False

    store.mark_delivered(job_id, "telegram_message")

    assert store.has_delivery(job_id, "telegram_message") is True
    assert store.has_delivery(job_id, "telegram_document") is False
    assert store.has_delivery(job_id) is True


def test_get_evaluation_and_material_roundtrip(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="x", source_job_id="1", title="Senior Product Engineer", company="Acme", description="React")
    job_id, _, _ = store.upsert_job(job)

    assert store.get_evaluation(job_id) is None
    assert store.get_material(job_id) is None

    store.save_evaluation(job_id, _evaluation(job_id, hard_blockers=["visa"], strengths=["React"]))
    evaluation = store.get_evaluation(job_id)
    assert evaluation is not None
    assert evaluation.decision == "high_priority"
    assert evaluation.hard_blockers == ["visa"]
    assert evaluation.strengths == ["React"]

    from job_hunter.models import Material

    store.save_material(job_id, Material(job_id=job_id, cover_letter_text="Dear Hiring Team,"))
    material = store.get_material(job_id)
    assert material is not None
    assert material.cover_letter_text == "Dear Hiring Team,"

    fetched_job = store.get_job(job_id)
    assert fetched_job is not None
    assert fetched_job.company == "Acme"
