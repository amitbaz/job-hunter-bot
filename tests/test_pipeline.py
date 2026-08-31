import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from job_hunter.gmail_models import ExtractedJob
from job_hunter.models import CandidatePreferences, Job, RunSummary, SearchPolicy, Settings
from job_hunter.pipeline import run_pipeline, should_run_scheduled
from job_hunter.sources import GmailStagedSource
from job_hunter.store import JobStore


class FakeGemini:
    def __init__(self, *, preference_payload=None):
        self.model = "gemini-test"
        self.preference_calls = 0
        self.eval_calls = 0
        self.cover_letter_calls = 0
        self.preference_payload = preference_payload

    def generate_text(self, prompt, *, json_mode=False):
        if json_mode and "preferred_roles" in prompt:
            self.preference_calls += 1
            payload = self.preference_payload or {
                "preferred_roles": ["Senior Product Engineer"],
                "preferred_seniority": ["senior"],
                "must_have_signals": ["React"],
                "nice_to_have_signals": ["TypeScript"],
                "preferred_locations": ["Germany"],
                "avoid_signals": ["manager"],
                "summary": "Remote product-oriented frontend engineer.",
            }
            return json.dumps(payload)
        if json_mode:
            self.eval_calls += 1
            payload = {
                "scores": {
                    "role_seniority": 28,
                    "technical": 22,
                    "product_architecture": 18,
                    "career_direction": 8,
                    "location_language": 9,
                    "company_environment": 5,
                },
                "total_score": 90,
                "hard_blockers": [],
                "strengths": ["React expertise"],
                "gaps": [],
                "salary_note": "Not disclosed",
                "location_note": "Remote EU friendly",
                "decision": "high_priority",
                "rationale": "Strong fit",
            }
            return json.dumps(payload)
        self.cover_letter_calls += 1
        return "Dear Hiring Team,\n\nI would love to join Acme as Senior Product Engineer.\n\nBest,\nAmit"


class FakeTelegram:
    def __init__(self):
        self.messages = []
        self.documents = []

    def send_message(self, text):
        self.messages.append(text)
        return "msg-1"

    def send_document(self, path, caption):
        self.documents.append((path, caption))
        return "doc-1"


class FlakyTelegram:
    """Simulates transient Telegram failures: the Nth calls fail, then succeed."""

    def __init__(self, fail_message_times=0, fail_document_times=0):
        self.messages = []
        self.documents = []
        self._fail_message_times = fail_message_times
        self._fail_document_times = fail_document_times

    def send_message(self, text):
        if self._fail_message_times > 0:
            self._fail_message_times -= 1
            return None
        self.messages.append(text)
        return f"msg-{len(self.messages)}"

    def send_document(self, path, caption):
        if self._fail_document_times > 0:
            self._fail_document_times -= 1
            return None
        self.documents.append((path, caption))
        return f"doc-{len(self.documents)}"


class FakeSource:
    def __init__(self, jobs):
        self._jobs = jobs

    def discover(self):
        return self._jobs


class BrokenSource:
    def discover(self):
        raise RuntimeError("source is down")


def _job(**overrides):
    defaults = dict(
        source="ashby",
        source_job_id="job-1",
        title="Senior Product Engineer",
        company="Acme",
        location="Remote",
        remote=True,
        description="React TypeScript remote role",
    )
    defaults.update(overrides)
    return Job(**defaults)


def _jobs_for_source(source: str, count: int, *, title="Senior Product Engineer", description="React TypeScript remote role"):
    return [
        _job(
            source=source,
            source_job_id=f"{source}-{index}",
            company=f"{source.title()} {index:03d}",
            title=title,
            description=description,
        )
        for index in range(count)
    ]


@pytest.fixture
def policy():
    return SearchPolicy(
        target_titles=["senior product engineer"],
        positive_keywords=["react"],
        blocked_title_keywords=["junior"],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
        max_jobs_per_run=35,
    )


@pytest.fixture
def settings(tmp_path, policy):
    return Settings(
        gemini_api_key="key",
        candidate_profile="profile",
        cover_letter_template="template",
        timezone="Europe/Berlin",
        scheduled_hour=9,
        policy=policy,
        dry_run=False,
        telegram_bot_token="token",
        telegram_chat_id="chat",
        db_path=str(tmp_path / "state.sqlite3"),
    )


def test_pipeline_delivers_strong_match_and_dedupes_within_run(settings):
    strong_job = _job()
    duplicate_job = _job()
    source = FakeSource([strong_job, duplicate_job])
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    telegram = FakeTelegram()

    summary = run_pipeline(settings, sources=[source], store=store, gemini=gemini, telegram=telegram)

    assert summary.ready_to_apply == 1
    assert len(telegram.documents) == 1
    assert store.count_jobs() == 1
    job_id, _, _ = store.upsert_job(strong_job)
    assert store.has_delivery(job_id)
    assert gemini.eval_calls == 1

    # Second run rediscovers the same, unchanged job: no re-evaluation.
    source2 = FakeSource([strong_job])
    run_pipeline(settings, sources=[source2], store=store, gemini=gemini, telegram=telegram)
    assert gemini.eval_calls == 1


def test_pipeline_isolates_broken_source(settings):
    good_job = _job(source_job_id="job-2", company="Beta")
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    telegram = FakeTelegram()

    summary = run_pipeline(
        settings,
        sources=[BrokenSource(), FakeSource([good_job])],
        store=store,
        gemini=gemini,
        telegram=telegram,
    )

    assert summary.ready_to_apply == 1
    assert store.count_jobs() == 1


def test_pipeline_dry_run_persists_but_does_not_deliver(settings, policy):
    dry_settings = Settings(
        gemini_api_key=settings.gemini_api_key,
        candidate_profile=settings.candidate_profile,
        cover_letter_template=settings.cover_letter_template,
        timezone=settings.timezone,
        scheduled_hour=settings.scheduled_hour,
        policy=policy,
        dry_run=True,
        db_path=settings.db_path,
    )
    job = _job()
    store = JobStore(dry_settings.db_path)
    gemini = FakeGemini()
    telegram = FakeTelegram()

    summary = run_pipeline(dry_settings, sources=[FakeSource([job])], store=store, gemini=gemini, telegram=telegram)

    assert summary.ready_to_apply == 1
    assert len(telegram.messages) == 0
    assert len(telegram.documents) == 0
    job_id, _, _ = store.upsert_job(job)
    assert store.has_delivery(job_id) is False


def test_pipeline_evaluates_staged_gmail_job_through_normal_discovery(settings):
    store = JobStore(settings.db_path)
    store.stage_inbound_job(
        "message-1",
        "linkedin:job-1",
        ExtractedJob(
            source_platform="linkedin",
            source_job_id="job-1",
            url="https://linkedin.example/jobs/1",
            company="Acme",
            title="Senior Product Engineer",
            location="Remote",
            remote=True,
            description="React TypeScript remote role",
        ),
    )
    gemini = FakeGemini()
    telegram = FakeTelegram()

    summary = run_pipeline(
        settings,
        sources=[],
        store=store,
        gemini=gemini,
        telegram=telegram,
    )

    assert summary.ready_to_apply == 1
    assert gemini.eval_calls == 1
    assert store.count_jobs() == 1
    job = store.get_job(1)
    assert job is not None
    assert job.source == "gmail:linkedin"
    assert job.source_job_id == "linkedin:job-1"


def test_pipeline_keeps_richer_public_job_and_filters_staged_gmail_duplicate(settings):
    store = JobStore(settings.db_path)
    store.stage_inbound_job(
        "message-1",
        "linkedin:job-1",
        ExtractedJob(
            source_platform="linkedin",
            source_job_id="job-1",
            url="https://jobs.acme.example/roles/1?utm_source=linkedin",
            company="Acme",
            title="Senior Product Engineer",
        ),
    )
    public_job = _job(
        source="ashby",
        source_job_id="public-1",
        url="https://jobs.acme.example/roles/1",
        description="React TypeScript remote role with complete public details",
    )
    gemini = FakeGemini()
    telegram = FakeTelegram()

    run_pipeline(
        settings,
        sources=[FakeSource([public_job])],
        store=store,
        gemini=gemini,
        telegram=telegram,
    )

    persisted_job = store.get_job(1)
    assert persisted_job is not None
    assert persisted_job.source == "ashby"
    assert GmailStagedSource(store).discover() == []

    run_pipeline(
        settings,
        sources=[],
        store=store,
        gemini=gemini,
        telegram=telegram,
    )

    assert gemini.eval_calls == 1
    assert store.count_jobs() == 1


def test_pipeline_prefilters_non_matching_jobs(settings):
    irrelevant_job = _job(
        source_job_id="job-3",
        title="Junior QA Tester",
        description="manual testing",
    )
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    telegram = FakeTelegram()

    summary = run_pipeline(settings, sources=[FakeSource([irrelevant_job])], store=store, gemini=gemini, telegram=telegram)

    assert summary.ready_to_apply == 0
    assert summary.skipped == 1
    assert gemini.eval_calls == 0


class ExplodingHttp:
    def get(self, url, **kwargs):
        raise AssertionError(f"unexpected enrichment fetch for {url!r}")

    def post(self, url, **kwargs):
        raise AssertionError(f"unexpected post to {url!r}")


def test_pipeline_does_not_reenrich_job_with_existing_description(settings):
    job = _job(url="https://acme.example/jobs/1")
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    telegram = FakeTelegram()

    summary = run_pipeline(
        settings,
        sources=[FakeSource([job])],
        store=store,
        gemini=gemini,
        telegram=telegram,
        http=ExplodingHttp(),
    )

    assert summary.ready_to_apply == 1


def test_pipeline_retries_failed_telegram_delivery_on_next_run(settings):
    job = _job()
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    telegram = FlakyTelegram(fail_message_times=1, fail_document_times=1)

    # Run 1: evaluation + material generation succeed, both Telegram sends fail.
    run_pipeline(settings, sources=[FakeSource([job])], store=store, gemini=gemini, telegram=telegram)

    job_id, _, _ = store.upsert_job(job)
    assert store.has_delivery(job_id, "telegram_message") is False
    assert store.has_delivery(job_id, "telegram_document") is False
    assert len(telegram.messages) == 0
    assert len(telegram.documents) == 0

    # Run 2: same job rediscovered, Telegram now works -> retry succeeds.
    run_pipeline(settings, sources=[FakeSource([job])], store=store, gemini=gemini, telegram=telegram)

    assert len(telegram.messages) == 1
    assert len(telegram.documents) == 1
    assert store.has_delivery(job_id, "telegram_message") is True
    assert store.has_delivery(job_id, "telegram_document") is True


def test_pipeline_retry_does_not_call_gemini_again(settings):
    job = _job()
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    telegram = FlakyTelegram(fail_message_times=1, fail_document_times=1)

    run_pipeline(settings, sources=[FakeSource([job])], store=store, gemini=gemini, telegram=telegram)
    assert gemini.eval_calls == 1
    assert gemini.cover_letter_calls == 1

    run_pipeline(settings, sources=[FakeSource([job])], store=store, gemini=gemini, telegram=telegram)

    # Retry must reuse the persisted evaluation/cover letter, not call Gemini again.
    assert gemini.eval_calls == 1
    assert gemini.cover_letter_calls == 1


def test_pipeline_no_duplicate_sends_after_successful_delivery(settings):
    job = _job()
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    telegram = FakeTelegram()

    run_pipeline(settings, sources=[FakeSource([job])], store=store, gemini=gemini, telegram=telegram)
    assert len(telegram.messages) == 1
    assert len(telegram.documents) == 1

    # Job rediscovered on a later run after delivery already succeeded.
    run_pipeline(settings, sources=[FakeSource([job])], store=store, gemini=gemini, telegram=telegram)

    assert len(telegram.messages) == 1
    assert len(telegram.documents) == 1
    assert gemini.eval_calls == 1
    assert gemini.cover_letter_calls == 1


def test_pipeline_retries_only_missing_pdf_when_digest_already_sent(settings):
    job = _job()
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    telegram = FlakyTelegram(fail_message_times=0, fail_document_times=1)

    # Run 1: digest message succeeds, PDF document delivery fails.
    run_pipeline(settings, sources=[FakeSource([job])], store=store, gemini=gemini, telegram=telegram)

    job_id, _, _ = store.upsert_job(job)
    assert len(telegram.messages) == 1
    assert len(telegram.documents) == 0
    assert store.has_delivery(job_id, "telegram_message") is True
    assert store.has_delivery(job_id, "telegram_document") is False

    # Run 2: only the missing PDF should be retried, no duplicate digest message.
    run_pipeline(settings, sources=[FakeSource([job])], store=store, gemini=gemini, telegram=telegram)

    assert len(telegram.messages) == 1
    assert len(telegram.documents) == 1
    assert store.has_delivery(job_id, "telegram_document") is True
    assert gemini.eval_calls == 1
    assert gemini.cover_letter_calls == 1


def test_pipeline_extracts_preferences_once_without_logging_profile(settings, monkeypatch, caplog):
    settings.candidate_profile = "SENSITIVE_PROFILE_TEXT"
    job = _job()
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    telegram = FakeTelegram()
    extracted = []

    def fake_extract(profile, passed_gemini, policy):
        extracted.append((profile, passed_gemini, policy))
        return CandidatePreferences(
            preferred_roles=["Senior Product Engineer"],
            preferred_seniority=["senior"],
            must_have_signals=["React"],
            nice_to_have_signals=[],
            preferred_locations=["Germany"],
            avoid_signals=["manager"],
            summary="Compact summary",
        )

    monkeypatch.setattr("job_hunter.pipeline.extract_candidate_preferences", fake_extract)

    with caplog.at_level(logging.INFO):
        run_pipeline(settings, sources=[FakeSource([job])], store=store, gemini=gemini, telegram=telegram)

    assert extracted == [(settings.candidate_profile, gemini, settings.policy)]
    assert "profile extraction: source=" in caplog.text
    assert settings.candidate_profile not in caplog.text
    assert job.description not in caplog.text
    assert gemini.eval_calls == 1


def test_pipeline_evaluates_all_eligible_jobs_when_under_budget(settings):
    jobs = _jobs_for_source("ashby", 18)
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    telegram = FakeTelegram()

    summary = run_pipeline(settings, sources=[FakeSource(jobs)], store=store, gemini=gemini, telegram=telegram)

    assert summary.ready_to_apply == 18
    assert gemini.eval_calls == 18


def test_pipeline_caps_evaluations_at_diverse_shortlist_budget(settings, caplog):
    ashby_jobs = _jobs_for_source("ashby", 80)
    remotive_jobs = _jobs_for_source("remotive", 20)
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    telegram = FakeTelegram()

    with caplog.at_level(logging.INFO):
        summary = run_pipeline(
            settings,
            sources=[FakeSource(ashby_jobs), FakeSource(remotive_jobs)],
            store=store,
            gemini=gemini,
            telegram=telegram,
        )

    assert summary.ready_to_apply == 35
    assert gemini.eval_calls == 35
    assert "deferred_by_budget=65" in caplog.text
    assert "eligible sources: ashby=80 remotive=20" in caplog.text
    assert "selected sources: ashby=18 remotive=17" in caplog.text


def test_pipeline_logs_profile_fallback_without_private_content(settings, caplog):
    settings.candidate_profile = "PRIVATE_RESUME_TEXT"
    job = _job()
    store = JobStore(settings.db_path)
    gemini = FakeGemini(preference_payload="{not-json")
    telegram = FakeTelegram()

    with caplog.at_level(logging.INFO):
        run_pipeline(settings, sources=[FakeSource([job])], store=store, gemini=gemini, telegram=telegram)

    assert "profile extraction: source=fallback" in caplog.text
    assert "eligible sources: ashby=1" in caplog.text
    assert "selected sources: ashby=1" in caplog.text
    assert settings.candidate_profile not in caplog.text
    assert job.description not in caplog.text


def test_should_run_scheduled_matches_local_hour():
    now = datetime(2026, 8, 30, 7, 0, tzinfo=timezone.utc)  # 09:00 in Europe/Berlin (CEST, UTC+2)
    assert should_run_scheduled(now, "Europe/Berlin", 9) is True


def test_should_run_scheduled_rejects_other_hours():
    now = datetime(2026, 8, 30, 6, 0, tzinfo=timezone.utc)  # 08:00 in Europe/Berlin
    assert should_run_scheduled(now, "Europe/Berlin", 9) is False
