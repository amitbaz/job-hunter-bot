import requests

from job_hunter.sources.base import is_stale_board_error


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _http_error(status_code: int) -> requests.HTTPError:
    return requests.HTTPError(f"status {status_code}", response=_Response(status_code))


def test_is_stale_board_error_true_for_404():
    assert is_stale_board_error(_http_error(404)) is True


def test_is_stale_board_error_false_for_other_status_codes():
    assert is_stale_board_error(_http_error(500)) is False
    assert is_stale_board_error(_http_error(429)) is False


def test_is_stale_board_error_false_for_non_http_errors():
    assert is_stale_board_error(RuntimeError("network down")) is False
