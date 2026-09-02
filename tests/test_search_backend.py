from job_hunter.search_backend import (
    BraveSearchBackend,
    DuckDuckGoSearchBackend,
    FallbackSearchBackend,
    build_search_backend,
)


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Http:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.payload)


def test_brave_search_normalizes_web_results_and_authenticates():
    http = _Http(
        {
            "web": {
                "results": [
                    {
                        "title": " Senior Frontend Engineer ",
                        "url": "https://example.com/jobs/42?utm_source=search",
                    },
                    {"title": "", "url": "https://example.com/ignored"},
                ]
            }
        }
    )

    response = BraveSearchBackend(http, "secret-key").search("frontend london")

    assert response.backend == "brave"
    assert len(response.hits) == 1
    assert response.hits[0].title == "Senior Frontend Engineer"
    assert response.hits[0].url == "https://example.com/jobs/42"
    _url, kwargs = http.calls[0]
    assert kwargs["headers"]["X-Subscription-Token"] == "secret-key"
    assert kwargs["params"]["q"] == "frontend london"


def test_build_search_backend_keeps_duckduckgo_when_no_brave_key():
    backend = build_search_backend(_Http({}), None)

    assert isinstance(backend, FallbackSearchBackend)
    assert backend._primary is None
    assert isinstance(backend._secondary, DuckDuckGoSearchBackend)
