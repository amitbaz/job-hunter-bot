from job_hunter.models import Evaluation, Job, NavigationCard, NavigationSession
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


def test_pending_delivery_job_ids_excludes_score_sixty_possible_match(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="x", source_job_id="1", title="Senior Product Engineer")
    job_id, _, _ = store.upsert_job(job)
    store.save_evaluation(job_id, _evaluation(job_id, total_score=60, decision="possible_match"))
    assert store.pending_delivery_job_ids() == []


def test_pending_delivery_job_ids_excludes_score_sixty_ready_match(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="x", source_job_id="1", title="Senior Product Engineer")
    job_id, _, _ = store.upsert_job(job)
    store.save_evaluation(job_id, _evaluation(job_id, total_score=60, decision="high_priority"))
    assert store.pending_delivery_job_ids() == []


def test_pending_delivery_job_ids_keeps_score_sixty_one_possible_match_until_message_sent(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="x", source_job_id="1", title="Senior Product Engineer")
    job_id, _, _ = store.upsert_job(job)
    store.save_evaluation(job_id, _evaluation(job_id, total_score=61, decision="possible_match"))
    assert store.pending_delivery_job_ids() == [job_id]
    store.mark_delivered(job_id, "telegram_message")
    assert store.pending_delivery_job_ids() == []


def test_pending_delivery_job_ids_keeps_score_sixty_one_ready_match_until_both_deliveries_sent(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job = Job(source="x", source_job_id="1", title="Senior Product Engineer")
    job_id, _, _ = store.upsert_job(job)
    store.save_evaluation(job_id, _evaluation(job_id, total_score=61, decision="package_match"))
    assert store.pending_delivery_job_ids() == [job_id]
    store.mark_delivered(job_id, "telegram_message")
    assert store.pending_delivery_job_ids() == [job_id]
    store.mark_delivered(job_id, "telegram_document")
    assert store.pending_delivery_job_ids() == []


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


def test_navigation_session_round_trip(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    session = NavigationSession(
        session_id="session-1",
        cards=[NavigationCard(1, "Senior FE", "Acme", "Berlin", 91, "https://example.test/1")],
        telegram_message_id=None,
        created_at="2026-08-31T12:00:00+00:00",
        expires_at="2026-09-30T12:00:00+00:00",
    )
    store.create_navigation_session(session)
    store.attach_navigation_message_id("session-1", "42")
    loaded = store.get_navigation_session("session-1")
    assert loaded is not None
    assert loaded.telegram_message_id == "42"
    assert loaded.cards[0].location == "Berlin"


def test_prune_navigation_sessions_deletes_expired_only(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    expired = NavigationSession(
        session_id="expired",
        cards=[],
        telegram_message_id=None,
        created_at="2026-08-01T00:00:00+00:00",
        expires_at="2026-08-31T00:00:00+00:00",
    )
    active = NavigationSession(
        session_id="active",
        cards=[],
        telegram_message_id=None,
        created_at="2026-08-31T00:00:00+00:00",
        expires_at="2026-09-30T00:00:00+00:00",
    )
    store.create_navigation_session(expired)
    store.create_navigation_session(active)
    assert store.prune_navigation_sessions("2026-09-01T00:00:00+00:00") == 1
    assert store.get_navigation_session("expired") is None
    assert store.get_navigation_session("active") is not None
