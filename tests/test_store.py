import sqlite3

from job_hunter.gmail_models import ExtractedJob
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


def _create_r1_jobs_only_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL DEFAULT '',
            source_job_id TEXT,
            url TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            remote INTEGER,
            description TEXT NOT NULL DEFAULT '',
            description_hash TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO jobs
            (fingerprint, source, url, company, title, description_hash,
             first_seen_at, last_seen_at)
        VALUES ('legacy', 'gmail:linkedin', 'https://example.test/job',
                'Acme', 'Frontend Engineer', '',
                '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')
        """
    )
    conn.commit()
    conn.close()


def test_r2_schema_upgrades_legacy_jobs_table(tmp_path):
    db = tmp_path / "state.sqlite3"
    _create_r1_jobs_only_db(db)

    store = JobStore(db)

    columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(jobs)")}
    assert "canonical_url" in columns
    assert "ats_provider" in columns
    assert "ats_board" in columns
    assert "ats_job_id" in columns
    assert store.count_jobs() == 1
    tables = {
        row["name"]
        for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "job_sources" in tables
    assert "company_watch" in tables


def test_record_job_source_is_idempotent(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job_id, _, _ = store.upsert_job(
        Job(source="yc", title="Frontend Engineer", company="Acme", url="https://yc.test/job/123")
    )

    store.record_job_source(
        job_id,
        source="yc",
        source_job_id="123",
        source_url="https://yc.test/job/123",
    )
    store.record_job_source(
        job_id,
        source="yc",
        source_job_id="123",
        source_url="https://yc.test/job/123",
    )

    rows = store.list_job_sources(job_id)
    assert len(rows) == 1
    assert rows[0]["source"] == "yc"
    assert rows[0]["source_job_id"] == "123"


def test_provenance_without_source_id_uses_canonical_url(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job_id, _, _ = store.upsert_job(
        Job(source="yc", title="Frontend Engineer", company="Acme")
    )

    store.record_job_source(
        job_id,
        source="yc",
        source_job_id=None,
        source_url="https://yc.test/job/123?utm_source=digest",
    )
    store.record_job_source(
        job_id,
        source="yc",
        source_job_id=None,
        source_url="https://yc.test/job/123",
    )

    rows = store.list_job_sources(job_id)
    assert len(rows) == 1
    assert rows[0]["identity_key"] == "url:https://yc.test/job/123"


def test_strong_lookups_find_the_single_matching_job(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job_id, _, _ = store.upsert_job(
        Job(
            source="greenhouse",
            source_job_id="posting-1",
            title="Senior Frontend Engineer",
            company="Acme GmbH",
            location="Berlin, Germany",
            url="https://boards.greenhouse.io/acme/jobs/posting-1?gh_src=feed",
            canonical_url="https://boards.greenhouse.io/acme/jobs/posting-1",
            ats_provider="greenhouse",
            ats_board="acme",
            ats_job_id="posting-1",
        )
    )

    assert store.find_job_by_canonical_url(
        "https://boards.greenhouse.io/acme/jobs/posting-1?utm_source=email"
    ) == job_id
    assert store.find_job_by_ats("greenhouse", "acme", "posting-1") == job_id
    assert store.find_job_by_identity("ACME", "senior frontend engineer", "Berlin") == job_id


def test_identity_lookup_rejects_ambiguous_matches(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    for source_job_id in ("1", "2"):
        store.upsert_job(
            Job(
                source="source",
                source_job_id=source_job_id,
                title="Frontend Engineer",
                company="Acme",
                location="Berlin",
            )
        )

    assert store.find_job_by_identity("Acme", "Frontend Engineer", "Berlin") is None


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


def test_gmail_persistence_schema_minimizes_private_email_data(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")

    tables = {
        row["name"]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "gmail_sync_state",
        "gmail_messages",
        "inbound_job_candidates",
        "application_events",
        "review_deliveries",
    } <= tables

    for table in ("gmail_messages", "inbound_job_candidates", "application_events"):
        columns = {
            row["name"] for row in store._conn.execute(f"PRAGMA table_info({table})")
        }
        assert "body" not in columns
        assert "email_body" not in columns


def test_gmail_message_and_sync_state_roundtrip(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")

    assert store.has_processed_gmail_message("m1") is False
    store.record_gmail_message(
        message_id="m1",
        thread_id="t1",
        sender="alerts@example.com",
        subject="Frontend roles",
        occurred_at="2026-08-31T10:00:00+00:00",
        classification="JOB_ALERT",
        confidence=0.98,
        rationale="sender rule",
    )
    assert store.has_processed_gmail_message("m1") is True

    assert store.get_gmail_sync_state("primary") is None
    store.save_gmail_sync_state(
        account_id="primary",
        history_id="h1",
        last_successful_sync_at="2026-08-31T10:05:00+00:00",
        backfill_completed_at="2026-08-31T10:05:00+00:00",
    )
    state = store.get_gmail_sync_state("primary")
    assert state is not None
    assert dict(state)["history_id"] == "h1"
    assert dict(state)["backfill_completed_at"] == "2026-08-31T10:05:00+00:00"


def test_inbound_candidate_source_message_and_key_are_idempotent(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "state.sqlite3")
    timestamps = iter(("2026-08-31T10:00:00+00:00", "2026-08-31T10:01:00+00:00"))
    monkeypatch.setattr("job_hunter.store._now_iso", lambda: next(timestamps))
    job = ExtractedJob(
        source_platform="linkedin",
        source_job_id="job-1",
        url="https://jobs.example.com/1",
        company="Acme",
        title="Frontend Engineer",
    )

    first = store.stage_inbound_job("m1", "linkedin:job-1", job)
    second = store.stage_inbound_job("m1", "linkedin:job-1", job)

    row = store._conn.execute(
        "SELECT id, created_at, last_seen_at FROM inbound_job_candidates"
    ).fetchone()
    assert second == first == row["id"]
    assert row["created_at"] == "2026-08-31T10:00:00+00:00"
    assert row["last_seen_at"] == "2026-08-31T10:01:00+00:00"


def test_application_event_source_message_is_idempotent(tmp_path):
    store = JobStore(tmp_path / "db.sqlite3")
    first = store.save_application_event(
        job_id=None,
        event_type="REVIEW_NEEDED",
        occurred_at="2026-08-31T10:00:00+00:00",
        source_message_id="m1",
        source_thread_id="t1",
        confidence=0.4,
        company="Acme",
        role_title="Frontend Engineer",
        rationale="ambiguous",
    )
    second = store.save_application_event(
        job_id=None,
        event_type="REVIEW_NEEDED",
        occurred_at="2026-08-31T10:00:00+00:00",
        source_message_id="m1",
        source_thread_id="t1",
        confidence=0.4,
        company="Acme",
        role_title="Frontend Engineer",
        rationale="ambiguous",
    )
    assert second == first


def test_current_application_state_derives_the_latest_eligible_event(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job_id, _, _ = store.upsert_job(
        Job(source="manual", source_job_id="1", title="Frontend Engineer")
    )
    store.save_application_event(
        job_id=job_id,
        event_type="OFFER",
        occurred_at="2026-08-01T10:00:00+00:00",
        source_message_id="m1",
        source_thread_id="t1",
        confidence=0.95,
        company="Acme",
        role_title="Frontend Engineer",
        rationale="offer",
    )
    store.save_application_event(
        job_id=job_id,
        event_type="APPLIED",
        occurred_at="2026-08-02T10:00:00+00:00",
        source_message_id="m2",
        source_thread_id="t1",
        confidence=0.95,
        company="Acme",
        role_title="Frontend Engineer",
        rationale="application",
    )

    assert store.current_application_state(job_id) == "APPLIED"


def test_pending_reviews_include_subject_and_are_marked_delivered(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    job_id, _, _ = store.upsert_job(
        Job(source="manual", source_job_id="1", title="Frontend Engineer")
    )
    store.record_gmail_message(
        message_id="m1",
        thread_id="t1",
        sender="recruiter@example.com",
        subject="A role to discuss",
        occurred_at="2026-08-31T10:00:00+00:00",
        classification="REVIEW_NEEDED",
        confidence=0.4,
        rationale="ambiguous",
    )
    event_id = store.save_application_event(
        job_id=job_id,
        event_type="REVIEW_NEEDED",
        occurred_at="2026-08-31T10:00:00+00:00",
        source_message_id="m1",
        source_thread_id="t1",
        confidence=0.4,
        company="Acme",
        role_title="Frontend Engineer",
        rationale="ambiguous",
    )

    assert [row["id"] for row in store.list_application_events(job_id)] == [event_id]
    pending = store.pending_review_events()
    assert [(row["id"], row["subject"]) for row in pending] == [
        (event_id, "A role to discuss")
    ]

    store.mark_review_delivered([event_id], "telegram-1")
    assert store.pending_review_events() == []


def test_candidate_not_emitted_when_any_job_has_same_canonical_url(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    store.stage_inbound_job(
        "m1",
        "linkedin:1",
        ExtractedJob(
            source_platform="linkedin",
            source_job_id="1",
            url="https://jobs.example.com/role?utm_source=linkedin",
            company="Different Company",
            title="Different Title",
        ),
    )
    store.upsert_job(
        Job(
            source="public",
            source_job_id="public-1",
            url="https://jobs.example.com/role",
            company="Acme",
            title="Frontend Engineer",
        )
    )

    assert store.list_unmaterialized_inbound_jobs() == []


def test_candidate_not_emitted_when_gmail_source_and_candidate_key_match(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    store.stage_inbound_job(
        "m1",
        "candidate-key-1",
        ExtractedJob(
            source_platform="talentboard",
            url="https://email.example/jobs/one",
            company="Email Company",
            title="Email Role",
        ),
    )
    store.upsert_job(
        Job(
            source="gmail:talentboard",
            source_job_id="candidate-key-1",
            url="https://materialized.example/jobs/different",
            company="Materialized Company",
            title="Materialized Role",
        )
    )

    assert store.list_unmaterialized_inbound_jobs() == []


def test_candidate_not_emitted_when_url_missing_but_identity_matches(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    store.stage_inbound_job(
        "m1",
        "linkedin:1",
        ExtractedJob(
            source_platform="linkedin",
            source_job_id="1",
            company="  ACME  ",
            title="Senior   Frontend Engineer",
            location="Berlin",
        ),
    )
    store.upsert_job(
        Job(
            source="public",
            source_job_id="public-1",
            url="https://jobs.example.com/role",
            company="Acme",
            title="senior frontend engineer",
            location=" berlin ",
        )
    )

    assert store.list_unmaterialized_inbound_jobs() == []


def test_candidate_emitted_when_no_existing_job_matches(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")
    candidate_id = store.stage_inbound_job(
        "m1",
        "linkedin:1",
        ExtractedJob(
            source_platform="linkedin",
            source_job_id="1",
            url="https://jobs.example.com/role",
            company="Acme",
            title="Frontend Engineer",
        ),
    )

    rows = store.list_unmaterialized_inbound_jobs()
    assert [(row["id"], row["source_candidate_key"]) for row in rows] == [
        (candidate_id, "linkedin:1")
    ]
