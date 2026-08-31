from datetime import UTC, datetime

import pytest

from job_hunter.gmail_classifier import classify_deterministically, classify_email, source_candidate_key
from job_hunter.gmail_models import ExtractedJob, GmailMessage


def message(subject: str, body: str, *, sender: str = "jobs@example.com", links: list[str] | None = None) -> GmailMessage:
    return GmailMessage(
        message_id="m1",
        thread_id="t1",
        sender=sender,
        subject=subject,
        sent_at=datetime(2026, 8, 31, tzinfo=UTC),
        snippet=body[:80],
        body=body,
        links=links or [],
    )


@pytest.mark.parametrize(
    ("subject", "body", "expected"),
    [
        ("Thanks for applying", "We received your application for Frontend Engineer.", "APPLIED"),
        ("Update on your application", "We will not be moving forward with your application.", "REJECTED"),
        ("Interview invitation", "Choose a time for your interview with our team.", "INTERVIEW"),
        ("Technical assessment", "Please complete this coding challenge by Friday.", "TECHNICAL"),
        ("Offer", "We are pleased to offer you the position.", "OFFER"),
    ],
)
def test_high_signal_templates_are_deterministic(subject, body, expected):
    result = classify_deterministically(message(subject, body))

    assert result is not None
    assert result.kind == expected
    assert result.confidence == 1.0


def test_linkedin_job_alert_extracts_known_job_url():
    result = classify_deterministically(
        message(
            "New jobs for you",
            "See jobs matching your preferences.",
            sender="jobalerts-noreply@linkedin.com",
            links=["https://www.linkedin.com/jobs/view/1234567890/"],
        )
    )

    assert result is not None
    assert result.kind == "JOB_ALERT"
    assert result.confidence == 1.0
    assert [job.url for job in result.jobs] == ["https://www.linkedin.com/jobs/view/1234567890/"]


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (ExtractedJob(source_platform="LinkedIn", url="https://www.linkedin.com/jobs/view/42/?utm_source=email"), "url:https://www.linkedin.com/jobs/view/42/"),
        (ExtractedJob(source_platform="Greenhouse", source_job_id="ABC-42"), "id:greenhouse:ABC-42"),
        (ExtractedJob(source_platform="email", company="Acme", title="Frontend Engineer", index=3), "fallback:acme|frontend engineer|3"),
    ],
)
def test_source_candidate_key_uses_url_id_then_normalized_fallback(job, expected):
    assert source_candidate_key(job) == expected


class FakeGemini:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, bool]] = []

    def generate_text(self, prompt: str, *, json_mode: bool = False) -> str:
        self.calls.append((prompt, json_mode))
        return self.response


def semantic_response(**overrides) -> str:
    data = {
        "kind": "RECRUITER_CONTACT",
        "confidence": 0.96,
        "company": "Acme",
        "role_title": "Frontend Engineer",
        "source_job_id": None,
        "job_urls": ["https://jobs.acme.example/frontend"],
        "jobs": [
            {
                "source_platform": "acme",
                "source_job_id": None,
                "url": "https://jobs.acme.example/frontend",
                "company": "Acme",
                "title": "Frontend Engineer",
                "location": "Remote",
                "remote": True,
                "description": "Frontend role",
            }
        ],
        "rationale": "Recruiter outreach about a frontend role.",
    }
    data.update(overrides)
    import json

    return json.dumps(data)


def test_confident_semantic_recruiter_outreach_accepts_gemini_identified_job_url():
    gemini = FakeGemini(semantic_response())

    result = classify_email(
        message("Hiring conversation", "I would like to discuss a Frontend Engineer role with you."),
        gemini,
    )

    assert result.kind == "RECRUITER_CONTACT"
    assert result.confidence == 0.96
    assert result.job_urls == ["https://jobs.acme.example/frontend"]
    assert [job.url for job in result.jobs] == ["https://jobs.acme.example/frontend"]


def test_low_confidence_semantic_lifecycle_becomes_review_needed():
    gemini = FakeGemini(
        semantic_response(
            kind="INTERVIEW",
            confidence=0.72,
            company="Acme",
            role_title="Frontend Engineer",
            source_job_id=None,
            job_urls=[],
            jobs=[],
            rationale="possibly scheduling",
        )
    )

    result = classify_email(message("Hiring process", "Can we find time to chat?"), gemini)

    assert result.kind == "REVIEW_NEEDED"


def test_malformed_semantic_json_becomes_review_needed():
    result = classify_email(message("Hiring conversation", "Can we discuss this role?"), FakeGemini("not json"))

    assert result.kind == "REVIEW_NEEDED"


def test_unsupported_semantic_kind_becomes_review_needed():
    result = classify_email(
        message("Hiring conversation", "Can we discuss this role?"),
        FakeGemini(semantic_response(kind="FOLLOW_UP")),
    )

    assert result.kind == "REVIEW_NEEDED"


def test_low_confidence_semantic_irrelevant_becomes_review_needed():
    result = classify_email(
        message("Hiring conversation", "Can we discuss this?"),
        FakeGemini(
            semantic_response(
                kind="IRRELEVANT",
                confidence=0.55,
                company="",
                role_title="",
                source_job_id=None,
                job_urls=[],
                jobs=[],
                rationale="possibly unrelated",
            )
        ),
    )

    assert result.kind == "REVIEW_NEEDED"


def test_semantic_prompt_includes_only_allowed_message_content_and_body_prefix():
    body_prefix = "body-prefix " + "x" * (20_000 - len("body-prefix "))
    gemini = FakeGemini(semantic_response(job_urls=[], jobs=[]))

    result = classify_email(
        message(
            "Hiring conversation",
            body_prefix + " SECRET_AFTER_LIMIT",
            sender="updates@company.example",
            links=["https://example.com/jobs/42"],
        ),
        gemini,
    )

    assert result.kind == "RECRUITER_CONTACT"
    assert gemini.calls[0][1] is True
    prompt = gemini.calls[0][0]
    assert "updates@company.example" in prompt
    assert "Hiring conversation" in prompt
    assert "body-prefix" in prompt
    assert "https://example.com/jobs/42" in prompt
    assert "SECRET_AFTER_LIMIT" not in prompt
