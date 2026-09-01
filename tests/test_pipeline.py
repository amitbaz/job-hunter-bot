import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from job_hunter.gmail_models import ExtractedJob
from job_hunter.models import (
    CandidatePreferences,
    CompanyWatchSeed,
    Job,
    RunSummary,
    SearchPolicy,
    Settings,
)
from job_hunter.pipeline import run_pipeline, should_run_scheduled
from job_hunter.sources import GmailStagedSource
from job_hunter.sources.company_watch import CompanyWatchSource
from job_hunter.store import JobStore
from job_hunter.watchlist import promote_company as persist_promoted_company


class FakeGemini:
    def __init__(self, *, preference_payload=None, evaluation_payload=None):
        self.model = "gemini-test"
        self.preference_calls = 0
        self.eval_calls = 0
        self.cover_letter_calls = 0
        self.preference_payload = preference_payload
        self.evaluation_payload = evaluation_payload

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
            payload = self.evaluation_payload or {
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


class FailOnSecondMessageTelegram:
    def __init__(self):
        self.attempts = []
        self.messages = []

    def send_message(self, text):
        self.attempts.append(text)
        if len(self.attempts) == 2:
            return None
        self.messages.append(text)
        return f"msg-{len(self.messages)}"

    def send_document(self, path, caption):
        raise AssertionError("review-only fixture must not send documents")


class OrderedFakeTelegram(FakeTelegram):
    def __init__(self):
        super().__init__()
        self.calls = []

    def send_message(self, text):
        self.calls.append(("message", text))
        return super().send_message(text)

    def send_document(self, path, caption):
        self.calls.append(("document", caption))
        return super().send_document(path, caption)


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


def _evaluation_payload(scores, decision):
    return {
        "scores": scores,
        "total_score": sum(scores.values()),
        "hard_blockers": [],
        "strengths": ["React expertise"],
        "gaps": [],
        "salary_note": "Not disclosed",
        "location_note": "Remote EU friendly",
        "decision": decision,
        "rationale": "Strong fit",
    }


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


def _record_review_event(store, *, message_id, occurred_at, company="Acme", role_title="Frontend Engineer"):
    store.record_gmail_message(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        sender="recruiter@example.com",
        subject=f"Interview update for {company}",
        occurred_at=occurred_at,
        classification="REVIEW_NEEDED",
        confidence=0.4,
        rationale="ambiguous scheduling language",
    )
    return store.save_application_event(
        job_id=None,
        event_type="REVIEW_NEEDED",
        occurred_at=occurred_at,
        source_message_id=message_id,
        source_thread_id=f"thread-{message_id}",
        confidence=0.4,
        company=company,
        role_title=role_title,
        rationale="ambiguous scheduling language",
    )


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


def test_pipeline_promotes_package_match_only_after_evaluation_is_persisted(
    settings, monkeypatch, caplog
):
    store = JobStore(settings.db_path)
    gemini = FakeGemini(
        evaluation_payload=_evaluation_payload(
            {
                "role_seniority": 25,
                "technical": 20,
                "product_architecture": 15,
                "career_direction": 7,
                "location_language": 8,
                "company_environment": 5,
            },
            "package_match",
        )
    )
    promotion_calls = []

    def assert_persisted_then_promote(
        passed_store, *, job_id, job, evaluation, package_threshold
    ):
        assert passed_store.get_evaluation(job_id) is not None
        promotion_calls.append((job_id, package_threshold))
        return persist_promoted_company(
            passed_store,
            job_id=job_id,
            job=job,
            evaluation=evaluation,
            package_threshold=package_threshold,
        )

    monkeypatch.setattr(
        "job_hunter.pipeline.promote_company",
        assert_persisted_then_promote,
        raising=False,
    )

    settings.candidate_profile = "PRIVATE_CV_TEXT"
    job = _job(description="PRIVATE_GMAIL_BODY React TypeScript")
    store.upsert_company_watch(
        company_name="Healthy Watch",
        careers_url="https://healthy.test/careers",
        ats_provider=None,
        ats_identifier=None,
        discovered_from_job_id=None,
        promotion_source="manual",
        confidence=1.0,
    )
    failing_watch_id = store.upsert_company_watch(
        company_name="Failing Watch",
        careers_url="https://failing.test/careers",
        ats_provider=None,
        ats_identifier=None,
        discovered_from_job_id=None,
        promotion_source="manual",
        confidence=1.0,
    )
    now = datetime.now(timezone.utc)
    store.record_watch_failure(failing_watch_id, now)
    store.record_watch_failure(failing_watch_id, now)

    class WatchResponse:
        text = "<html></html>"

        def raise_for_status(self):
            return None

    class WatchHttp:
        def get(self, url, **kwargs):
            if url == "https://healthy.test/careers":
                return WatchResponse()
            if url == "https://failing.test/careers":
                raise RuntimeError("watch unavailable")
            raise AssertionError(f"unexpected request for {url}")

    with caplog.at_level(logging.INFO):
        summary = run_pipeline(
            settings,
            sources=[FakeSource([job])],
            store=store,
            gemini=gemini,
            telegram=FakeTelegram(),
            http=WatchHttp(),
        )

    row = store.get_company_watch("Acme")
    assert row is not None
    assert row["promotion_source"] == "automatic"
    assert promotion_calls == [(1, 75)]
    assert summary.ready_to_apply == 1
    assert store.get_company_watch("Healthy Watch")["consecutive_failures"] == 0
    failing_watch = store.get_company_watch("Failing Watch")
    assert failing_watch["consecutive_failures"] == 3
    assert failing_watch["paused_until"] is not None
    assert "companies_promoted=1" in caplog.text
    assert "watch_checks=2" in caplog.text
    assert "watch_paused=1" in caplog.text
    assert "PRIVATE_CV_TEXT" not in caplog.text
    assert "PRIVATE_GMAIL_BODY" not in caplog.text


def test_pipeline_aggregates_untrusted_gmail_source_labels_in_logs(settings, caplog):
    settings.dry_run = True
    store = JobStore(settings.db_path)
    platform = "MODEL_PLATFORM\nPRIVATE_PLATFORM_SECRET"
    store.stage_inbound_job(
        "message-1",
        "candidate-1",
        ExtractedJob(
            source_platform=platform,
            company="Acme",
            title="Senior Product Engineer",
            location="Remote",
            remote=True,
            description="React TypeScript remote role",
        ),
    )

    with caplog.at_level(logging.INFO):
        summary = run_pipeline(
            settings,
            sources=[],
            store=store,
            gemini=FakeGemini(),
            telegram=FakeTelegram(),
        )

    assert summary.ready_to_apply == 1
    assert "gmail=1" in caplog.text
    assert platform not in caplog.text
    assert "PRIVATE_PLATFORM_SECRET" not in caplog.text


def test_pipeline_counts_only_meaningful_company_watch_promotions(settings, caplog):
    settings.dry_run = True
    store = JobStore(settings.db_path)
    watch_seeds = (
        ("Repeat", "automatic"),
        ("Manual", "manual"),
        ("Upgrade", "automatic"),
    )
    for company_name, promotion_source in watch_seeds:
        store.upsert_company_watch(
            company_name=company_name,
            careers_url="",
            ats_provider=None,
            ats_identifier=None,
            discovered_from_job_id=None,
            promotion_source=promotion_source,
            confidence=1.0,
        )
    jobs = [
        _job(company="Repeat", source_job_id="repeat"),
        _job(company="Manual", source_job_id="manual"),
        _job(company="New", source_job_id="new"),
        _job(
            company="Upgrade",
            source_job_id="upgrade",
            canonical_url="https://upgrade.test/careers",
        ),
    ]

    with caplog.at_level(logging.INFO):
        summary = run_pipeline(
            settings,
            sources=[FakeSource(jobs)],
            store=store,
            gemini=FakeGemini(),
            telegram=FakeTelegram(),
        )

    assert summary.ready_to_apply == 4
    assert store.get_company_watch("Repeat")["promotion_source"] == "automatic"
    assert store.get_company_watch("Manual")["promotion_source"] == "manual"
    assert store.get_company_watch("Upgrade")["careers_url"] == "https://upgrade.test/careers"
    assert "companies_promoted=2" in caplog.text


def test_pipeline_counts_a_failed_expired_watch_retry_as_a_new_pause(settings, caplog):
    settings.dry_run = True
    store = JobStore(settings.db_path)
    watch_id = store.upsert_company_watch(
        company_name="Retry Watch",
        careers_url="https://retry.test/careers",
        ats_provider=None,
        ats_identifier=None,
        discovered_from_job_id=None,
        promotion_source="manual",
        confidence=1.0,
    )
    past = datetime.now(timezone.utc) - timedelta(days=2)
    for _ in range(3):
        store.record_watch_failure(watch_id, past)
    previous_pause = store.get_company_watch("Retry Watch")["paused_until"]

    class FailingWatchHttp:
        def get(self, url, **kwargs):
            raise RuntimeError("retry unavailable")

    with caplog.at_level(logging.INFO):
        run_pipeline(
            settings,
            sources=[],
            store=store,
            gemini=FakeGemini(),
            telegram=FakeTelegram(),
            http=FailingWatchHttp(),
        )

    watch = store.get_company_watch("Retry Watch")
    assert watch["consecutive_failures"] == 4
    assert watch["paused_until"] != previous_pause
    assert "watch_checks=1" in caplog.text
    assert "watch_paused=1" in caplog.text


def test_pipeline_does_not_promote_possible_match(settings, monkeypatch):
    store = JobStore(settings.db_path)
    gemini = FakeGemini(
        evaluation_payload=_evaluation_payload(
            {
                "role_seniority": 20,
                "technical": 18,
                "product_architecture": 14,
                "career_direction": 6,
                "location_language": 7,
                "company_environment": 5,
            },
            "possible_match",
        )
    )
    promotion_calls = []

    def record_promotion_attempt(
        passed_store, *, job_id, job, evaluation, package_threshold
    ):
        promotion_calls.append((job_id, package_threshold))
        return persist_promoted_company(
            passed_store,
            job_id=job_id,
            job=job,
            evaluation=evaluation,
            package_threshold=package_threshold,
        )

    monkeypatch.setattr(
        "job_hunter.pipeline.promote_company",
        record_promotion_attempt,
        raising=False,
    )

    summary = run_pipeline(
        settings,
        sources=[FakeSource([_job()])],
        store=store,
        gemini=gemini,
        telegram=FakeTelegram(),
    )

    assert store.get_company_watch("Acme") is None
    assert promotion_calls == [(1, 75)]
    assert summary.possible_matches == 1


def test_pipeline_passes_configured_package_threshold_to_promotion(
    settings, monkeypatch
):
    settings.policy.thresholds["package"] = 95
    store = JobStore(settings.db_path)
    promotion_calls = []

    def record_threshold(
        passed_store, *, job_id, job, evaluation, package_threshold
    ):
        promotion_calls.append(package_threshold)
        return persist_promoted_company(
            passed_store,
            job_id=job_id,
            job=job,
            evaluation=evaluation,
            package_threshold=package_threshold,
        )

    monkeypatch.setattr(
        "job_hunter.pipeline.promote_company",
        record_threshold,
        raising=False,
    )

    summary = run_pipeline(
        settings,
        sources=[FakeSource([_job()])],
        store=store,
        gemini=FakeGemini(),
        telegram=FakeTelegram(),
    )

    assert summary.ready_to_apply == 1
    assert promotion_calls == [95]
    assert store.get_company_watch("Acme") is None


def test_pipeline_isolates_company_watch_source_failure(
    settings, monkeypatch
):
    store = JobStore(settings.db_path)
    gemini = FakeGemini()
    attempts = []

    def raise_watch_failure(self):
        attempts.append(self)
        raise RuntimeError("watch down")

    monkeypatch.setattr(CompanyWatchSource, "discover", raise_watch_failure)

    summary = run_pipeline(
        settings,
        sources=[FakeSource([_job()])],
        store=store,
        gemini=gemini,
        telegram=FakeTelegram(),
    )

    assert gemini.eval_calls == 1
    assert len(attempts) == 1
    assert summary.ready_to_apply == 1
    assert summary.errors == 0


def test_pipeline_syncs_structured_manual_watch_seeds(settings):
    settings.policy.manual_company_watch = [
        CompanyWatchSeed(company_name="Manual Co")
    ]
    store = JobStore(settings.db_path)

    run_pipeline(
        settings,
        sources=[],
        store=store,
        gemini=FakeGemini(),
        telegram=FakeTelegram(),
    )

    row = store.get_company_watch("Manual Co")
    assert row is not None
    assert row["promotion_source"] == "manual"


def test_pipeline_injects_resolver_for_direct_ats_canonical_metadata(settings):
    job = _job(
        source="remotive",
        url="https://jobs.lever.co/acme/job-1",
    )
    store = JobStore(settings.db_path)

    run_pipeline(
        settings,
        sources=[FakeSource([job])],
        store=store,
        gemini=FakeGemini(),
        telegram=FakeTelegram(),
        http=ExplodingHttp(),
    )

    persisted = store._conn.execute(
        "SELECT canonical_url, ats_provider, ats_board, ats_job_id "
        "FROM jobs WHERE id = 1"
    ).fetchone()
    assert persisted is not None
    assert persisted["canonical_url"] == "https://jobs.lever.co/acme/job-1"
    assert persisted["ats_provider"] == "lever"
    assert persisted["ats_board"] == "acme"
    assert persisted["ats_job_id"] == "job-1"


def test_pipeline_uses_one_targeted_duckduckgo_query_for_canonical_resolution(
    settings
):
    class Response:
        def __init__(self, *, url, text):
            self.url = url
            self.text = text

        def raise_for_status(self):
            return None

    class TargetedSearchHttp:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url == "https://aggregator.test/jobs/1":
                return Response(url=url, text="<html></html>")
            if url == "https://duckduckgo.com/html/":
                return Response(
                    url=url,
                    text=(
                        '<a class="result__a" '
                        'href="https://jobs.ashbyhq.com/acme/ats-1">'
                        "Senior Product Engineer</a>"
                    ),
                )
            raise AssertionError(f"unexpected GET {url}")

    http = TargetedSearchHttp()
    store = JobStore(settings.db_path)

    run_pipeline(
        settings,
        sources=[
            FakeSource(
                [
                    _job(
                        source="aggregator",
                        url="https://aggregator.test/jobs/1",
                    )
                ]
            )
        ],
        store=store,
        gemini=FakeGemini(),
        telegram=FakeTelegram(),
        http=http,
    )

    persisted = store._conn.execute(
        "SELECT canonical_url FROM jobs WHERE id = 1"
    ).fetchone()
    assert persisted is not None
    assert persisted["canonical_url"] == "https://jobs.ashbyhq.com/acme/ats-1"
    search_calls = [
        kwargs["params"]["q"]
        for url, kwargs in http.calls
        if url == "https://duckduckgo.com/html/"
    ]
    assert len(search_calls) == 1
    assert '"Acme"' in search_calls[0]
    assert '"Senior Product Engineer"' in search_calls[0]
    assert "site:jobs.ashbyhq.com" in search_calls[0]


def test_pipeline_rejects_targeted_ats_result_for_wrong_company(settings):
    class Response:
        def __init__(self, *, url, text):
            self.url = url
            self.text = text

        def raise_for_status(self):
            return None

    class WrongCompanySearchHttp:
        def get(self, url, **kwargs):
            if url == "https://aggregator.test/jobs/1":
                return Response(url=url, text="<html></html>")
            if url == "https://duckduckgo.com/html/":
                return Response(
                    url=url,
                    text=(
                        '<a class="result__a" '
                        'href="https://jobs.ashbyhq.com/wrong-company/123">'
                        "Senior Product Engineer</a>"
                    ),
                )
            raise AssertionError(f"unexpected GET {url}")

    store = JobStore(settings.db_path)

    run_pipeline(
        settings,
        sources=[
            FakeSource(
                [
                    _job(
                        source="aggregator",
                        url="https://aggregator.test/jobs/1",
                    )
                ]
            )
        ],
        store=store,
        gemini=FakeGemini(),
        telegram=FakeTelegram(),
        http=WrongCompanySearchHttp(),
    )

    persisted = store._conn.execute(
        "SELECT url, canonical_url, ats_provider, ats_board, ats_job_id "
        "FROM jobs WHERE id = 1"
    ).fetchone()
    assert persisted is not None
    assert persisted["url"] == "https://aggregator.test/jobs/1"
    assert persisted["canonical_url"] == "https://aggregator.test/jobs/1"
    assert persisted["ats_provider"] is None
    assert persisted["ats_board"] is None
    assert persisted["ats_job_id"] is None


def test_pipeline_counts_promotion_failure_but_continues_delivery(
    settings, monkeypatch
):
    store = JobStore(settings.db_path)
    telegram = FakeTelegram()

    def raise_promotion_failure(*args, **kwargs):
        raise RuntimeError("watch persistence down")

    monkeypatch.setattr(
        "job_hunter.pipeline.promote_company",
        raise_promotion_failure,
        raising=False,
    )

    summary = run_pipeline(
        settings,
        sources=[FakeSource([_job()])],
        store=store,
        gemini=FakeGemini(),
        telegram=telegram,
    )

    assert summary.errors == 1
    assert summary.ready_to_apply == 1
    assert len(telegram.messages) == 1
    assert len(telegram.documents) == 1


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


def test_pipeline_delivers_all_pending_gmail_reviews_in_one_message(settings):
    store = JobStore(settings.db_path)
    first_event_id = _record_review_event(
        store,
        message_id="review-2",
        occurred_at="2026-08-31T11:00:00+00:00",
        company="Beta",
    )
    second_event_id = _record_review_event(
        store,
        message_id="review-1",
        occurred_at="2026-08-31T10:00:00+00:00",
    )
    telegram = FakeTelegram()

    run_pipeline(settings, sources=[], store=store, gemini=FakeGemini(), telegram=telegram)

    assert telegram.messages == [
        "Gmail review needed\n"
        "- Acme — Frontend Engineer | ambiguous scheduling language\n"
        "- Beta — Frontend Engineer | ambiguous scheduling language"
    ]
    assert store.pending_review_events() == []
    delivered = store._conn.execute(
        "SELECT event_id, telegram_message_id FROM review_deliveries ORDER BY event_id"
    ).fetchall()
    assert [(row["event_id"], row["telegram_message_id"]) for row in delivered] == [
        (first_event_id, "msg-1"),
        (second_event_id, "msg-1"),
    ]


def test_pipeline_retries_gmail_reviews_after_a_failed_telegram_send(settings):
    store = JobStore(settings.db_path)
    event_id = _record_review_event(
        store,
        message_id="review-1",
        occurred_at="2026-08-31T10:00:00+00:00",
    )
    telegram = FlakyTelegram(fail_message_times=1)

    run_pipeline(settings, sources=[], store=store, gemini=FakeGemini(), telegram=telegram)

    assert [row["id"] for row in store.pending_review_events()] == [event_id]
    assert telegram.messages == []

    run_pipeline(settings, sources=[], store=store, gemini=FakeGemini(), telegram=telegram)

    assert store.pending_review_events() == []
    assert telegram.messages == [
        "Gmail review needed\n"
        "- Acme — Frontend Engineer | ambiguous scheduling language"
    ]


def test_pipeline_marks_each_review_chunk_before_retrying_partial_failure(settings):
    store = JobStore(settings.db_path)
    first_event_id = _record_review_event(
        store,
        message_id="review-1",
        occurred_at="2026-08-31T10:00:00+00:00",
        company="A" * 2000,
    )
    second_event_id = _record_review_event(
        store,
        message_id="review-2",
        occurred_at="2026-08-31T11:00:00+00:00",
        company="B" * 2000,
    )
    telegram = FailOnSecondMessageTelegram()

    run_pipeline(settings, sources=[], store=store, gemini=FakeGemini(), telegram=telegram)

    assert len(telegram.attempts) == 2
    assert all(len(message) <= 3900 for message in telegram.attempts)
    assert [row["id"] for row in store.pending_review_events()] == [second_event_id]
    delivered = store._conn.execute(
        "SELECT event_id FROM review_deliveries ORDER BY event_id"
    ).fetchall()
    assert [row["event_id"] for row in delivered] == [first_event_id]

    run_pipeline(settings, sources=[], store=store, gemini=FakeGemini(), telegram=telegram)

    assert len(telegram.attempts) == 3
    assert "A" * 2000 not in telegram.attempts[2]
    assert "B" * 2000 in telegram.attempts[2]
    assert store.pending_review_events() == []


def test_pipeline_sends_gmail_reviews_after_normal_job_delivery_without_scoring_them(settings):
    store = JobStore(settings.db_path)
    _record_review_event(
        store,
        message_id="review-1",
        occurred_at="2026-08-31T10:00:00+00:00",
        company="Review Co",
        role_title="Review Role",
    )
    telegram = OrderedFakeTelegram()

    run_pipeline(
        settings,
        sources=[FakeSource([_job()])],
        store=store,
        gemini=FakeGemini(),
        telegram=telegram,
    )

    assert [kind for kind, _content in telegram.calls] == ["message", "document", "message"]
    assert telegram.messages[0].startswith("Ready to apply\n- 90 | Acme - Senior Product Engineer")
    assert "Gmail review needed" not in telegram.messages[0]
    assert telegram.messages[1] == (
        "Gmail review needed\n"
        "- Review Co — Review Role | ambiguous scheduling language"
    )


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


def test_targeted_canonical_search_stops_after_shared_breaker_opens():
    """One breaker spans every per-job search, so a dead host is called once."""
    from job_hunter.circuit_breaker import CircuitBreaker
    from job_hunter.pipeline import _targeted_canonical_candidates

    class FailingHttp:
        def __init__(self):
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            raise RuntimeError("network down")

    http = FailingHttp()
    breaker = CircuitBreaker(failure_threshold=1)
    jobs = [
        Job(source="aggregator", title=f"Senior Product Engineer {i}", company="Acme")
        for i in range(4)
    ]

    for job in jobs:
        assert _targeted_canonical_candidates(http, job, breaker) == []

    assert http.calls == 1
