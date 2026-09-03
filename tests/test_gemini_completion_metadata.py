import json

import pytest

from job_hunter import gemini as gemini_module
from job_hunter.candidate_context import FALLBACK_CONTEXT_SUMMARY, get_candidate_context
from job_hunter.models import SearchPolicy
from job_hunter.store import JobStore


class FakeResponse:
    def __init__(self, json_data):
        self.status_code = 200
        self._json_data = json_data
        self.text = ""

    def json(self):
        return self._json_data


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses) if isinstance(responses, list) else [responses]
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class FakeTracker:
    def __init__(self):
        self.preflight_calls = []
        self.success_calls = []
        self.error_calls = []

    def preflight(self, purpose, prompt, now):
        self.preflight_calls.append((purpose, prompt, now))

    def record_success(self, purpose, prompt, now, **kwargs):
        self.success_calls.append((purpose, prompt, now, kwargs))

    def record_error(self, purpose, prompt, now, **kwargs):
        self.error_calls.append((purpose, prompt, now, kwargs))


def _api_response(text, *, finish_reason="STOP", output_tokens=400):
    return FakeResponse(
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": text}]},
                    "finishReason": finish_reason,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 120,
                "candidatesTokenCount": output_tokens,
                "thoughtsTokenCount": 100,
                "cachedContentTokenCount": 0,
                "totalTokenCount": 120 + output_tokens + 100,
            },
        }
    )


def _policy() -> SearchPolicy:
    return SearchPolicy(
        target_titles=["senior frontend engineer"],
        positive_keywords=["react", "typescript"],
        blocked_title_keywords=["junior"],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
        role_families=["frontend engineering"],
        blocked_profession_title_phrases=["product manager"],
    )


def _valid_context_json() -> str:
    return json.dumps(
        {
            "preferences": {
                "preferred_roles": ["Senior Frontend Engineer"],
                "preferred_seniority": ["senior"],
                "must_have_signals": ["React"],
                "nice_to_have_signals": ["mentorship"],
                "preferred_locations": ["Germany"],
                "avoid_signals": ["junior"],
                "summary": "Senior frontend engineer focused on strong product teams.",
            },
            "technical_skills": ["React", "TypeScript"],
            "architecture_evidence": ["Owned frontend architecture"],
            "leadership_ownership": ["Led frontend delivery"],
            "agentic_ai_evidence": [],
            "product_domain_evidence": ["Product engineering"],
            "location_language_facts": ["Based in Berlin"],
            "career_direction": ["Seeking senior frontend roles"],
            "company_environment": ["Product-led teams"],
            "career_evidence": ["Senior frontend experience"],
            "evaluation_summary": "Senior frontend engineer with architecture ownership.",
        }
    )


def test_generate_text_classifies_max_tokens_after_recording_usage():
    partial_output = '{"technical_skills": ["React"], "career_evidence": ["unterminated'
    tracker = FakeTracker()
    client = gemini_module.GeminiClient(
        "secret",
        "gemini-test",
        FakeHttp(_api_response(partial_output, finish_reason="MAX_TOKENS", output_tokens=1800)),
        tracker,
    )

    with pytest.raises(gemini_module.GeminiIncompleteResponse) as excinfo:
        client.generate_text(
            "extract profile",
            purpose="candidate_context",
            max_output_tokens=1800,
            json_mode=True,
        )

    assert excinfo.value.finish_reason == "MAX_TOKENS"
    assert "MAX_TOKENS" in str(excinfo.value)
    assert partial_output not in str(excinfo.value)
    assert len(tracker.success_calls) == 1
    assert tracker.error_calls == []
    _, _, _, usage = tracker.success_calls[0]
    assert usage["output_tokens"] == 1800
    assert usage["thinking_tokens"] == 100


def test_candidate_context_retries_truncation_once_tracks_both_attempts_and_caches_success():
    profile = "PRIVATE PROFILE CONTENT THAT MUST NOT BE LOGGED"
    partial_output = '{"technical_skills": ["React"], "career_evidence": ["unterminated'
    http = FakeHttp(
        [
            _api_response(partial_output, finish_reason="MAX_TOKENS", output_tokens=1800),
            _api_response(_valid_context_json(), output_tokens=900),
        ]
    )
    tracker = FakeTracker()
    client = gemini_module.GeminiClient("secret", "gemini-test", http, tracker)
    store = JobStore(":memory:")

    context = get_candidate_context(profile, _policy(), client, store)

    assert context.source == "gemini"
    assert context.technical_skills == ["React", "TypeScript"]
    assert len(http.calls) == 2
    assert len(tracker.preflight_calls) == 2
    assert len(tracker.success_calls) == 2

    first_config = http.calls[0][1]["json"]["generationConfig"]
    second_config = http.calls[1][1]["json"]["generationConfig"]
    assert first_config["thinkingConfig"] == {"thinkingLevel": "medium"}
    assert first_config["maxOutputTokens"] == 1800
    assert second_config["thinkingConfig"] == {"thinkingLevel": "low"}
    assert second_config["maxOutputTokens"] == 6000

    get_candidate_context(profile, _policy(), client, store)
    assert len(http.calls) == 2


def test_candidate_context_falls_back_after_second_truncation_without_third_attempt(caplog):
    profile = "PRIVATE PROFILE CONTENT THAT MUST NOT BE LOGGED"
    partial_output = '{"technical_skills": ["React"], "career_evidence": ["unterminated'
    http = FakeHttp(
        [
            _api_response(partial_output, finish_reason="MAX_TOKENS", output_tokens=1800),
            _api_response(partial_output, finish_reason="MAX_TOKENS", output_tokens=6000),
        ]
    )
    tracker = FakeTracker()
    client = gemini_module.GeminiClient("secret", "gemini-test", http, tracker)

    with caplog.at_level("WARNING"):
        context = get_candidate_context(profile, _policy(), client, JobStore(":memory:"))

    assert context.source == "fallback_error"
    assert context.evaluation_summary == FALLBACK_CONTEXT_SUMMARY
    assert len(http.calls) == 2
    assert len(tracker.success_calls) == 2
    assert "category=provider_truncation" in caplog.text
    assert "finish_reason=MAX_TOKENS" in caplog.text
    assert profile not in caplog.text
    assert partial_output not in caplog.text


def test_candidate_context_malformed_json_does_not_retry_and_logs_category(caplog):
    profile = "PRIVATE PROFILE CONTENT THAT MUST NOT BE LOGGED"
    http = FakeHttp(_api_response("not json"))
    tracker = FakeTracker()
    client = gemini_module.GeminiClient("secret", "gemini-test", http, tracker)

    with caplog.at_level("WARNING"):
        context = get_candidate_context(profile, _policy(), client, JobStore(":memory:"))

    assert context.source == "fallback_error"
    assert len(http.calls) == 1
    assert len(tracker.success_calls) == 1
    assert "category=invalid_json" in caplog.text
    assert profile not in caplog.text
