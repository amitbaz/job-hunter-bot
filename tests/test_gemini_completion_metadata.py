import pytest

from job_hunter import gemini as gemini_module


class FakeResponse:
    def __init__(self, json_data):
        self.status_code = 200
        self._json_data = json_data
        self.text = ""

    def json(self):
        return self._json_data


class FakeHttp:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


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


def test_generate_text_classifies_max_tokens_after_recording_usage():
    partial_output = '{"technical_skills": ["React"], "career_evidence": ["unterminated'
    response = FakeResponse(
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": partial_output}]},
                    "finishReason": "MAX_TOKENS",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 120,
                "candidatesTokenCount": 1800,
                "thoughtsTokenCount": 300,
                "cachedContentTokenCount": 0,
                "totalTokenCount": 2220,
            },
        }
    )
    tracker = FakeTracker()
    client = gemini_module.GeminiClient("secret", "gemini-test", FakeHttp(response), tracker)
    incomplete_error = getattr(gemini_module, "GeminiIncompleteResponse")

    with pytest.raises(incomplete_error) as excinfo:
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
    assert usage["thinking_tokens"] == 300
