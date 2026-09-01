from datetime import UTC, datetime

from job_hunter.gmail_client import GmailPage
from job_hunter.gmail_models import GmailMessage
from job_hunter.gmail_sync import GmailSyncService, build_backfill_query
from job_hunter.store import JobStore


NOW = datetime(2026, 9, 1, 8, tzinfo=UTC)
LEGACY_RATIONALE = "semantic classification unavailable or invalid"


class FailingGemini:
    def generate_text(self, prompt: str, *, json_mode: bool = False) -> str:
        raise RuntimeError("provider unavailable")


class OneMessageGmail:
    def __init__(self, message: GmailMessage) -> None:
        self.message = message

    def get_profile(self) -> tuple[str, str]:
        return "candidate@example.com", "100"

    def list_message_ids(self, query: str, page_token: str | None = None) -> GmailPage:
        return GmailPage([self.message.message_id], None)

    def get_message(self, message_id: str) -> GmailMessage:
        return self.message


def test_semantic_provider_failure_is_error_not_review_and_remains_retryable(tmp_path):
    message = GmailMessage(
        message_id="m1",
        thread_id="t1",
        sender="updates@example.com",
        subject="Hiring conversation",
        sent_at=NOW,
        snippet="Can we discuss an engineering role?",
        body="Can we discuss an engineering role?",
    )
    store = JobStore(tmp_path / "state.sqlite3")
    service = GmailSyncService(
        gmail=OneMessageGmail(message),
        gemini=FailingGemini(),
        store=store,
    )

    summary = service.sync(NOW)

    assert summary.errors == 1
    assert summary.review_needed == 0
    assert store.has_processed_gmail_message("m1") is False
    assert store.pending_review_events() == []


def test_backfill_query_uses_job_specific_offer_phrases():
    query = build_backfill_query(NOW)

    assert '"job offer"' in query
    assert '"offer letter"' in query
    assert '"thanks for applying"' in query
    assert '"received your application"' in query
    assert " position " not in f" {query} "
    assert " offer " not in f" {query} "


def test_release_legacy_semantic_failures_removes_only_exact_technical_artifacts(tmp_path):
    store = JobStore(tmp_path / "state.sqlite3")

    for message_id, rationale in (
        ("legacy", LEGACY_RATIONALE),
        ("real-review", "ambiguous scheduling language"),
    ):
        store.record_gmail_message(
            message_id=message_id,
            thread_id=f"thread-{message_id}",
            sender="recruiter@example.com",
            subject="Application update",
            occurred_at=NOW.isoformat(),
            classification="REVIEW_NEEDED",
            confidence=1.0,
            rationale=rationale,
        )
        event_id = store.save_application_event(
            job_id=None,
            event_type="REVIEW_NEEDED",
            occurred_at=NOW.isoformat(),
            source_message_id=message_id,
            source_thread_id=f"thread-{message_id}",
            confidence=1.0,
            company="",
            role_title="",
            rationale=rationale,
        )
        store.mark_review_delivered([event_id], "telegram-1")

    released = store.release_legacy_gmail_semantic_failures()

    assert released == 1
    assert store.has_processed_gmail_message("legacy") is False
    assert store.has_processed_gmail_message("real-review") is True
    remaining = store._conn.execute(
        "SELECT source_message_id, rationale FROM application_events ORDER BY source_message_id"
    ).fetchall()
    assert [(row["source_message_id"], row["rationale"]) for row in remaining] == [
        ("real-review", "ambiguous scheduling language")
    ]
    assert store.release_legacy_gmail_semantic_failures() == 0
