import json
import logging

import pytest

from job_hunter.candidate_context import get_candidate_context
from job_hunter.gemini import GeminiClient, GeminiIncompleteResponse
from job_hunter.models import SearchPolicy
from job_hunter.preferences import FALLBACK_PREFERENCES_SUMMARY
from job_hunter.store import JobStore


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        return self._json_data


class FakeHttp:
    def __init__(self, response):
        self.response = response

    def post(self, *_args, **_kwargs):
        return self.response


class FakeTracker:
    def __init__(self):
        self.success_calls = []

    def preflight(self, _purpose, _prompt, _now):
        return None

    def record_success(self, purpose, prompt, now, **kwargs):
        self.success_calls.append((purpose, prompt, now, kwargs))


class FakeGemini:
    def __init__(self, outcomes, model="gemini-test"):
        self.model = model
        self._outcomes = list(outcomes)
        self.calls = []

    def generate_text(
        self,
        prompt,
        *,
        purpose=None,
        thinking_level=None,
        max_output_tokens=None,
        json_mode=False,
        json_schema=None,
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "purpose": purpose,
                "thinking_level": thinking_level,
                "max_output_tokens": max_output_tokens,
                "json_mode": json_mode,
                "json_schema": json_schema,
            }
        )
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_policy() -> SearchPolicy:
    return SearchPolicy(
        target_titles=["senior product engineer", "staff frontend engineer"],
        positive_keywords=["react", "typescript", "system design"],
        blocked_title_keywords=["junior"],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
        role_families=["frontend engineering", "product engineering"],
        blocked_profession_title_phrases=["product manager", "designer"],
    )


def valid_context_json() -> str:
    return json.dumps(
        {
            "preferences": {
                "preferred_roles": ["Senior Product Engineer"],
                "preferred_seniority": ["senior"],
                "must_have_signals": ["React"],
                "nice_to_have_signals": ["mentorship"],
                "preferred_locations": ["Germany"],
                "avoid_signals": ["on-site"],
                "summary": "Senior frontend/product engineer focused on remote EU roles.",
            },
            "technical_skills": ["React", "TypeScript", "Node.js"],
            "architecture_evidence": ["Designed a micro-frontend platform"],
            "leadership_ownership": ["Led a team of 4 engineers"],
            "agentic_ai_evidence": ["Built an LLM-based support triage tool"],
            "product_domain_evidence": ["5 years in fintech"],
            "location_language_facts": ["Based in Berlin and fluent in English"],
            "career_direction": ["Seeking staff-level scope"],
            "company_environment": ["Prefers small, product-led teams"],
            "career_evidence": ["8 years of professional software engineering"],
            "evaluation_summary": "Senior full-stack engineer with product and leadership experience.",
        }
    )


def test_generate_text_classifies_max_tokens_and_records_usage():
    response = {
        "candidates": [
            {
                "finishReason": "MAX_TOKENS",
                "content": {"parts": [{"text": '{"technical_skills": ["React"]'}]},
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 1800,
            "thoughtsTokenCount": 20,
            "cachedContentTokenCount": 0,
            "totalTokenCount": 1920,
        },
    }
    tracker = FakeTracker()
    client = GeminiClient("secret-key", "gemini-test", FakeHttp(FakeResponse(200, response)), tracker)

    with pytest.raises(GeminiIncompleteResponse) as excinfo:
        client.generate_text("extract context", purpose="candidate_context", json_mode=True)

    assert excinfo.value.finish_reason == "MAX_TOKENS"
    assert len(tracker.success_calls) == 1
    assert tracker.success_calls[0][3]["output_tokens"] == 1800


def test_candidate_context_retries_once_after_provider_truncation(caplog):
    store = JobStore(":memory:")
    gemini = FakeGemini(
        [
            GeminiIncompleteResponse(finish_reason="MAX_TOKENS"),
            valid_context_json(),
        ]
    )

    with caplog.at_level(logging.WARNING):
        context = get_candidate_context("candidate profile text", make_policy(), gemini, store)

    assert context.source == "gemini"
    assert context.technical_skills == ["React", "TypeScript", "Node.js"]
    assert len(gemini.calls) == 2
    assert gemini.calls[0]["max_output_tokens"] == 1800
    assert gemini.calls[1]["max_output_tokens"] == 3600
    assert "category=provider_truncated" in caplog.text

    cached = get_candidate_context("candidate profile text", make_policy(), gemini, store)
    assert cached.source == "cache"
    assert len(gemini.calls) == 2


def test_candidate_context_bounds_recovery_after_second_truncation(caplog):
    store = JobStore(":memory:")
    gemini = FakeGemini(
        [
            GeminiIncompleteResponse(finish_reason="MAX_TOKENS"),
            GeminiIncompleteResponse(finish_reason="MAX_TOKENS"),
        ]
    )

    with caplog.at_level(logging.WARNING):
        context = get_candidate_context("candidate profile text", make_policy(), gemini, store)

    assert context.source == "fallback_error"
    assert context.preferences.summary == FALLBACK_PREFERENCES_SUMMARY
    assert len(gemini.calls) == 2
    assert "category=provider_truncated" in caplog.text


def test_candidate_context_does_not_retry_generic_malformed_json(caplog):
    store = JobStore(":memory:")
    gemini = FakeGemini(["not-json-sensitive-profile-output", valid_context_json()])

    with caplog.at_level(logging.WARNING):
        context = get_candidate_context("candidate profile text", make_policy(), gemini, store)

    assert context.source == "fallback_error"
    assert context.preferences.summary == FALLBACK_PREFERENCES_SUMMARY
    assert len(gemini.calls) == 1
    assert "category=invalid_structured_output" in caplog.text
    assert "not-json-sensitive-profile-output" not in caplog.text
