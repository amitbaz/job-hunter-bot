from pathlib import Path

from job_hunter.models import DigestItem
from job_hunter.telegram import TelegramClient, build_digest, chunk_message


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


class FakeHttp:
    def __init__(self, response=None):
        self.response = response or FakeResponse(200, {"result": {"message_id": 42}})
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _item(**overrides):
    defaults = dict(
        job_id=1,
        company="Acme",
        title="Senior Product Engineer",
        score=90,
        decision="high_priority",
        url="https://acme.example/jobs/1",
        hard_blockers=[],
    )
    defaults.update(overrides)
    return DigestItem(**defaults)


def test_chunk_message_splits_long_text_under_limit():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    chunks = chunk_message(text, limit=3900)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 3900


def test_chunk_message_returns_single_chunk_for_short_text():
    assert chunk_message("hello") == ["hello"]


def test_build_digest_groups_by_decision():
    items = [
        _item(decision="high_priority", company="Acme", score=90),
        _item(decision="package_match", company="Beta", score=80),
        _item(decision="possible_match", company="Gamma", score=70),
        _item(decision="blocked", company="Delta", score=50, hard_blockers=["visa"]),
    ]
    digest = build_digest(items)
    assert "Ready to apply" in digest
    assert "Possible matches" in digest
    assert "Needs review / blockers" in digest
    assert digest.index("Ready to apply") < digest.index("Acme")
    assert digest.index("Ready to apply") < digest.index("Beta")
    assert digest.index("Possible matches") < digest.index("Gamma")
    assert digest.index("Needs review / blockers") < digest.index("Delta")
    assert "90" in digest and "Senior Product Engineer" in digest
    assert "https://acme.example/jobs/1" in digest


def test_build_digest_empty_items_returns_placeholder():
    digest = build_digest([])
    assert digest


def test_send_message_posts_to_send_message_endpoint():
    http = FakeHttp()
    client = TelegramClient("token123", "chat456", http)

    result = client.send_message("hello world")

    assert result == "42"
    url, kwargs = http.calls[0]
    assert url == "https://api.telegram.org/bottoken123/sendMessage"
    assert kwargs["json"]["chat_id"] == "chat456"
    assert kwargs["json"]["text"] == "hello world"


def test_send_message_chunks_long_text_into_multiple_calls():
    http = FakeHttp()
    client = TelegramClient("token123", "chat456", http)
    long_text = "\n".join(f"line {i} " + "x" * 50 for i in range(200))

    client.send_message(long_text)

    assert len(http.calls) > 1
    for url, kwargs in http.calls:
        assert url.endswith("/sendMessage")
        assert kwargs["json"]["chat_id"] == "chat456"


def test_send_message_returns_none_on_failure():
    http = FakeHttp(FakeResponse(400, {}, "bad request"))
    client = TelegramClient("token123", "chat456", http)

    assert client.send_message("hello") is None


def test_send_document_posts_multipart_with_chat_id(tmp_path):
    doc_path = tmp_path / "letter.pdf"
    doc_path.write_bytes(b"%PDF-1.4 fake")
    http = FakeHttp()
    client = TelegramClient("token123", "chat456", http)

    result = client.send_document(doc_path, "Acme - Senior Product Engineer - 90 - https://acme.example/jobs/1")

    assert result == "42"
    url, kwargs = http.calls[0]
    assert url == "https://api.telegram.org/bottoken123/sendDocument"
    assert kwargs["data"]["chat_id"] == "chat456"
    assert "document" in kwargs["files"]
    assert kwargs["data"]["caption"].startswith("Acme - Senior Product Engineer")


def test_send_document_truncates_long_caption(tmp_path):
    doc_path = tmp_path / "letter.pdf"
    doc_path.write_bytes(b"%PDF-1.4 fake")
    http = FakeHttp()
    client = TelegramClient("token123", "chat456", http)

    client.send_document(doc_path, "x" * 2000)

    _, kwargs = http.calls[0]
    assert len(kwargs["data"]["caption"]) < 1024


def test_send_document_returns_none_on_failure(tmp_path):
    doc_path = tmp_path / "letter.pdf"
    doc_path.write_bytes(b"%PDF-1.4 fake")
    http = FakeHttp(FakeResponse(500, {}, "server error"))
    client = TelegramClient("token123", "chat456", http)

    assert client.send_document(doc_path, "caption") is None
