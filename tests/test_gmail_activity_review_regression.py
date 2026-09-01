from datetime import UTC, datetime

from job_hunter.gmail_classifier import classify_email
from job_hunter.gmail_models import GmailMessage
from job_hunter.gmail_sync import build_backfill_query


NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


class NoCallGemini:
    def generate_text(self, *args, **kwargs):
        raise AssertionError("deterministic classification should not call Gemini")


def _message(subject: str, body: str, *, sender: str = "jobs@example.com") -> GmailMessage:
    return GmailMessage(
        message_id="m1",
        thread_id="t1",
        sender=sender,
        subject=subject,
        sent_at=NOW,
        snippet=body[:80],
        body=body,
        links=[],
    )


def test_non_job_pleased_to_offer_promotion_is_irrelevant():
    result = classify_email(
        _message(
            "Travel together, Save up to 15%!",
            "Vietnam Airlines is pleased to offer exclusive fares for friends and family.",
            sender="no-reply@e-news.vietnamairlines.com",
        ),
        NoCallGemini(),
    )

    assert result.kind == "IRRELEVANT"


def test_employment_offer_phrase_remains_deterministic_offer():
    result = classify_email(
        _message(
            "Your offer",
            "We are pleased to offer you the position of Senior Frontend Engineer.",
        ),
        NoCallGemini(),
    )

    assert result.kind == "OFFER"


def test_linkedin_recruiter_message_remains_recruiter_activity():
    result = classify_email(
        _message(
            "Ofer just messaged you",
            "1 new message awaits your response. Senior Talent Acquisition Manager | IT recruiter",
            sender="messaging-digest-noreply@linkedin.com",
        ),
        NoCallGemini(),
    )

    assert result.kind == "RECRUITER_CONTACT"


def test_backfill_query_uses_only_employment_specific_offer_terms():
    query = build_backfill_query(NOW)

    assert '"job offer"' in query
    assert '"offer letter"' in query
    assert '"offer of employment"' in query
    assert '"pleased to offer you"' in query
    assert " coding challenge\" offer}" not in query
