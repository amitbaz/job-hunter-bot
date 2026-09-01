import pytest

from job_hunter.gemini import GeminiClient, GeminiError


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
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _candidate_response(text="hello world"):
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}]}}
        ]
    }


def test_generate_text_posts_to_expected_url_with_key_header():
    http = FakeHttp(FakeResponse(200, _candidate_response("hi")))
    client = GeminiClient("secret-key", "gemini-2.5-flash-lite", http)

    result = client.generate_text("say hi")

    assert result == "hi"
    url, kwargs = http.calls[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
    assert kwargs["headers"]["x-goog-api-key"] == "secret-key"
    assert kwargs["json"]["contents"][0]["parts"][0]["text"] == "say hi"
    assert "generationConfig" not in kwargs["json"]


def test_generate_text_json_mode_sets_response_mime_type():
    http = FakeHttp(FakeResponse(200, _candidate_response("{}")))
    client = GeminiClient("secret-key", "gemini-2.5-flash-lite", http)

    client.generate_text("give me json", json_mode=True)

    _, kwargs = http.calls[0]
    assert kwargs["json"]["generationConfig"]["responseMimeType"] == "application/json"


def test_generate_text_json_schema_sets_structured_output_config():
    http = FakeHttp(FakeResponse(200, _candidate_response("{}")))
    client = GeminiClient("secret-key", "gemini-2.5-flash-lite", http)
    schema = {
        "type": "OBJECT",
        "properties": {"kind": {"type": "STRING"}},
        "required": ["kind"],
    }

    client.generate_text("classify this", json_schema=schema)

    _, kwargs = http.calls[0]
    generation_config = kwargs["json"]["generationConfig"]
    assert generation_config["responseMimeType"] == "application/json"
    assert generation_config["responseSchema"] == schema


def test_generate_text_raises_on_non_2xx():
    http = FakeHttp(FakeResponse(429, None, "rate limited"))
    client = GeminiClient("secret-key", "gemini-2.5-flash-lite", http)

    with pytest.raises(GeminiError):
        client.generate_text("say hi")


def test_generate_text_raises_on_missing_content():
    http = FakeHttp(FakeResponse(200, {"candidates": []}))
    client = GeminiClient("secret-key", "gemini-2.5-flash-lite", http)

    with pytest.raises(GeminiError):
        client.generate_text("say hi")


def test_generate_text_concatenates_multiple_parts():
    data = {"candidates": [{"content": {"parts": [{"text": "hello "}, {"text": "world"}]}}]}
    http = FakeHttp(FakeResponse(200, data))
    client = GeminiClient("secret-key", "gemini-2.5-flash-lite", http)

    assert client.generate_text("say hi") == "hello world"
