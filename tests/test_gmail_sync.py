from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from job_hunter.gmail_client import GmailHistoryExpired, GmailHistoryPage, GmailPage
from job_hunter.gmail_models import GmailMessage
from job_hunter.gmail_sync import GmailSyncService, build_backfill_query
from job_hunter.store import JobStore


NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


def _message(message_id: str, *, sent_at: datetime = NOW) -> GmailMessage:
    return GmailMessage(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        sender="newsletter@example.com",
        subject="Weekly newsletter",
        sent_at=sent_at,
        snippet="General product news",
        body="General product news",
    )


def _job_alert(message_id: str, *, sent_at: datetime) -> GmailMessage:
    return GmailMessage(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        sender="jobalerts-noreply@linkedin.com",
        subject="Job alert",
        sent_at=sent_at,
        snippet="A new frontend role",
        body="A new frontend role",
        links=[f"https://www.linkedin.com/jobs/view/{message_id}/"],
    )


class FakeGemini:
    def generate_text(self, prompt: str, *, json_mode: bool = False) -> str:
        raise AssertionError("irrelevant fixture must not call Gemini")


class FakeGmail:
    def __init__(
        self,
        *,
        profile: tuple[str, str] = ("candidate@example.com", "100"),
        message_ids: list[str] | None = None,
        messages: dict[str, GmailMessage | Exception] | None = None,
    ) -> None:
        self.profile = profile
        self.message_ids = message_ids or []
        self.messages = messages or {}
        self.profile_calls = 0
        self.history_pages: dict[str | None, GmailHistoryPage | Exception] = {}
        self.search_calls: list[tuple[str, str | None]] = []
        self.history_calls: list[tuple[str, str | None]] = []
        self.message_calls: list[str] = []

    def get_profile(self) -> tuple[str, str]:
        self.profile_calls += 1
        return self.profile

    def list_message_ids(self, query: str, page_token: str | None = None) -> GmailPage:
        self.search_calls.append((query, page_token))
        return GmailPage(self.message_ids, None)

    def list_history(
        self, start_history_id: str, page_token: str | None = None
    ) -> GmailHistoryPage:
        self.history_calls.append((start_history_id, page_token))
        result = self.history_pages.get(
            page_token, GmailHistoryPage([], start_history_id, None)
        )
        if isinstance(result, Exception):
            raise result
        return result

    def get_message(self, message_id: str) -> GmailMessage:
        self.message_calls.append(message_id)
        result = self.messages[message_id]
        if isinstance(result, Exception):
            raise result
        return result


def _service(tmp_path, gmail: FakeGmail) -> tuple[GmailSyncService, JobStore]:
    store = JobStore(tmp_path / "state.sqlite3")
    return GmailSyncService(gmail=gmail, gemini=FakeGemini(), store=store), store


def _save_completed_state(
    store: JobStore,
    *,
    history_id: str = "100",
    completed_at: datetime = NOW - timedelta(days=1),
) -> None:
    store.save_gmail_sync_state(
        account_id="candidate@example.com",
        history_id=history_id,
        last_successful_sync_at=completed_at.isoformat(),
        backfill_completed_at=completed_at.isoformat(),
    )


def test_first_sync_uses_profile_email_as_account_id(tmp_path):
    gmail = FakeGmail(profile=("real.account@example.com", "checkpoint-1"))
    service, store = _service(tmp_path, gmail)

    service.sync(NOW)

    assert store.get_gmail_sync_state("real.account@example.com") is not None
    assert store.get_gmail_sync_state("primary") is None


def test_first_sync_scans_12_months_and_marks_backfill_complete(tmp_path):
    gmail = FakeGmail(
        message_ids=["m1"],
        messages={"m1": _message("m1")},
    )
    service, store = _service(tmp_path, gmail)

    summary = service.sync(NOW)

    assert gmail.search_calls == [
        (
            'after:2025/08/31 {application interview recruiter hiring "job alert" '
            'position "technical assessment" "coding challenge" offer}',
            None,
        )
    ]
    assert build_backfill_query(datetime(2024, 2, 29, tzinfo=UTC)) == (
        'after:2023/02/28 {application interview recruiter hiring "job alert" '
        'position "technical assessment" "coding challenge" offer}'
    )
    state = store.get_gmail_sync_state("candidate@example.com")
    assert state is not None
    assert state["history_id"] == "100"
    assert state["last_successful_sync_at"] == NOW.isoformat()
    assert state["backfill_completed_at"] == NOW.isoformat()
    assert summary.fetched == 1
    assert summary.processed == 1


def test_backfill_rerun_skips_processed_message_ids(tmp_path):
    gmail = FakeGmail(
        message_ids=["m1"],
        messages={"m1": _message("m1")},
    )
    service, store = _service(tmp_path, gmail)
    service.sync(NOW)

    summary = service.sync(NOW, force_backfill=True)

    assert gmail.message_calls == ["m1"]
    assert store.has_processed_gmail_message("m1") is True
    assert summary.fetched == 1
    assert summary.processed == 0


def test_backfill_error_prevents_completion_marker(tmp_path):
    gmail = FakeGmail(
        message_ids=["broken", "ok"],
        messages={"broken": RuntimeError("decode failed"), "ok": _message("ok")},
    )
    service, store = _service(tmp_path, gmail)

    summary = service.sync(NOW)

    assert summary.errors == 1
    assert summary.processed == 1
    assert store.has_processed_gmail_message("ok") is True
    assert store.get_gmail_sync_state("candidate@example.com") is None


def test_message_arriving_during_backfill_is_not_skipped_by_saved_history_checkpoint(
    tmp_path,
):
    gmail = FakeGmail(
        profile=("candidate@example.com", "before-scan"),
        messages={"arrived-during-scan": _message("arrived-during-scan")},
    )
    service, store = _service(tmp_path, gmail)

    service.sync(NOW)
    gmail.history_pages = {
        None: GmailHistoryPage(["arrived-during-scan"], "after-scan", None)
    }
    second_summary = service.sync(NOW + timedelta(minutes=1))

    state = store.get_gmail_sync_state("candidate@example.com")
    assert gmail.history_calls == [("before-scan", None)]
    assert second_summary.processed == 1
    assert store.has_processed_gmail_message("arrived-during-scan") is True
    assert state is not None
    assert state["history_id"] == "after-scan"


def test_related_write_failure_does_not_record_message_or_advance_backfill(
    tmp_path, monkeypatch
):
    gmail = FakeGmail(
        message_ids=["alert"],
        messages={"alert": _job_alert("alert", sent_at=NOW)},
    )
    service, store = _service(tmp_path, gmail)

    def fail_staging(*args, **kwargs):
        raise RuntimeError("staging failed")

    monkeypatch.setattr(store, "stage_inbound_job", fail_staging)

    summary = service.sync(NOW)

    assert summary.errors == 1
    assert store.has_processed_gmail_message("alert") is False
    assert store.get_gmail_sync_state("candidate@example.com") is None


def test_six_month_old_job_alert_is_not_staged(tmp_path):
    gmail = FakeGmail(
        message_ids=["old-alert"],
        messages={
            "old-alert": _job_alert("old-alert", sent_at=NOW - timedelta(days=180))
        },
    )
    service, store = _service(tmp_path, gmail)

    summary = service.sync(NOW)

    assert store.has_processed_gmail_message("old-alert") is True
    assert (
        store._conn.execute("SELECT COUNT(*) FROM inbound_job_candidates").fetchone()[0]
        == 0
    )
    assert summary.job_alerts == 1


def test_three_day_old_job_alert_is_staged(tmp_path):
    gmail = FakeGmail(
        message_ids=["recent-alert"],
        messages={
            "recent-alert": _job_alert("recent-alert", sent_at=NOW - timedelta(days=3))
        },
    )
    service, store = _service(tmp_path, gmail)

    service.sync(NOW)

    candidate = store._conn.execute(
        "SELECT source_message_id FROM inbound_job_candidates"
    ).fetchone()
    assert candidate is not None
    assert candidate["source_message_id"] == "recent-alert"


def test_second_sync_uses_saved_history_id(tmp_path):
    gmail = FakeGmail(messages={"m1": _message("m1"), "m2": _message("m2")})
    gmail.history_pages = {
        None: GmailHistoryPage(["m1"], "110", "page-2"),
        "page-2": GmailHistoryPage(["m2"], "112", None),
    }
    service, store = _service(tmp_path, gmail)
    _save_completed_state(store, history_id="100")

    summary = service.sync(NOW)

    assert gmail.history_calls == [("100", None), ("100", "page-2")]
    assert store.get_gmail_sync_state("candidate@example.com")["history_id"] == "112"
    assert summary.processed == 2


def test_history_message_ids_are_idempotent(tmp_path):
    gmail = FakeGmail(messages={"new": _message("new")})
    gmail.history_pages = {
        None: GmailHistoryPage(["already-processed", "new"], "101", None)
    }
    service, store = _service(tmp_path, gmail)
    _save_completed_state(store)
    store.record_gmail_message(
        message_id="already-processed",
        thread_id="thread-old",
        sender="newsletter@example.com",
        subject="Old",
        occurred_at=NOW.isoformat(),
        classification="IRRELEVANT",
        confidence=1.0,
        rationale="test fixture",
    )

    summary = service.sync(NOW)

    assert gmail.message_calls == ["new"]
    assert summary.fetched == 2
    assert summary.processed == 1
    assert (
        store._conn.execute("SELECT COUNT(*) FROM gmail_messages").fetchone()[0]
        == 2
    )


def test_history_hard_error_does_not_advance_cursor(tmp_path):
    gmail = FakeGmail(
        messages={
            "broken": RuntimeError("decode failed"),
            "ok": _message("ok"),
        }
    )
    gmail.history_pages = {
        None: GmailHistoryPage(["broken", "ok"], "new-cursor", None)
    }
    service, store = _service(tmp_path, gmail)
    original_sync_at = NOW - timedelta(days=1)
    _save_completed_state(store, history_id="old-cursor", completed_at=original_sync_at)

    summary = service.sync(NOW)

    state = store.get_gmail_sync_state("candidate@example.com")
    assert summary.errors == 1
    assert summary.processed == 1
    assert state["history_id"] == "old-cursor"
    assert state["last_successful_sync_at"] == original_sync_at.isoformat()


def test_expired_history_uses_one_day_overlap_search(tmp_path):
    gmail = FakeGmail(
        profile=("candidate@example.com", "recovery-start-checkpoint"),
        message_ids=["overlap-message"],
        messages={"overlap-message": _message("overlap-message")},
    )
    gmail.history_pages = {None: GmailHistoryExpired()}
    service, store = _service(tmp_path, gmail)
    _save_completed_state(
        store,
        history_id="expired-cursor",
        completed_at=datetime(2026, 8, 30, 12, tzinfo=UTC),
    )

    service.sync(NOW)

    assert gmail.search_calls == [
        (
            'after:2026/08/29 {application interview recruiter hiring "job alert" '
            'position "technical assessment" "coding challenge" offer}',
            None,
        )
    ]
    state = store.get_gmail_sync_state("candidate@example.com")
    assert state["history_id"] == "recovery-start-checkpoint"
    assert state["last_successful_sync_at"] == NOW.isoformat()


def test_dry_run_writes_nothing_and_does_not_advance_state(tmp_path):
    gmail = FakeGmail(
        profile=("candidate@example.com", "dry-run-checkpoint"),
        messages={"dry-alert": _job_alert("dry-alert", sent_at=NOW)},
    )
    gmail.history_pages = {
        None: GmailHistoryPage(["dry-alert"], "dry-run-new-cursor", None)
    }
    service, store = _service(tmp_path, gmail)
    _save_completed_state(store, history_id="dry-run-old-cursor")
    state_before = dict(store.get_gmail_sync_state("candidate@example.com"))

    summary = service.sync(NOW, dry_run=True)

    state = store.get_gmail_sync_state("candidate@example.com")
    assert summary.processed == 1
    assert dict(state) == state_before
    for table in (
        "gmail_messages",
        "inbound_job_candidates",
        "application_events",
        "review_deliveries",
    ):
        assert store._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_force_backfill_is_idempotent_and_non_destructive(tmp_path):
    recruiter = GmailMessage(
        message_id="recruiter-message",
        thread_id="recruiter-thread",
        sender="recruiter@example.com",
        subject="Recruiter outreach",
        sent_at=NOW,
        snippet="Recruiter role",
        body="Recruiter role",
        links=["https://www.linkedin.com/jobs/view/42/"],
    )
    gmail = FakeGmail(
        message_ids=["recruiter-message"],
        messages={"recruiter-message": recruiter},
    )
    service, store = _service(tmp_path, gmail)
    service.sync(NOW)
    gmail.profile = ("candidate@example.com", "fresh-force-checkpoint")

    service.sync(NOW + timedelta(hours=1), force_backfill=True)

    assert (
        store._conn.execute("SELECT COUNT(*) FROM gmail_messages").fetchone()[0]
        == 1
    )
    assert (
        store._conn.execute("SELECT COUNT(*) FROM inbound_job_candidates").fetchone()[0]
        == 1
    )
    assert (
        store._conn.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]
        == 1
    )
    assert (
        store.get_gmail_sync_state("candidate@example.com")["history_id"]
        == "fresh-force-checkpoint"
    )


def test_sync_logs_exact_compact_metrics(tmp_path, caplog):
    gmail = FakeGmail(message_ids=["m1"], messages={"m1": _message("m1")})
    service, _ = _service(tmp_path, gmail)
    caplog.set_level(logging.INFO, logger="job_hunter.gmail_sync")

    service.sync(NOW)

    assert caplog.messages[-1] == (
        "gmail_fetched=1 gmail_processed=1 gmail_job_alerts=0 "
        "gmail_application_events=0 gmail_review_needed=0 "
        "gmail_irrelevant=1 gmail_errors=0"
    )
