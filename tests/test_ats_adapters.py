import logging

import requests

from job_hunter.sources.ashby import AshbySource
from job_hunter.sources.base import is_stale_board_error
from job_hunter.sources.greenhouse import GreenhouseSource
from job_hunter.sources.lever import LeverSource


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


class _RaisingHttp:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def get_json(self, url, **kwargs):
        raise self._exc


def test_greenhouse_404_logs_compact_without_traceback(caplog):
    with caplog.at_level(logging.INFO):
        jobs = GreenhouseSource("dead-token", _RaisingHttp(_http_error(404))).discover()
    assert jobs == []
    records = [r for r in caplog.records if "dead-token" in r.getMessage()]
    assert len(records) == 1
    assert records[0].exc_info is None


def test_greenhouse_unexpected_error_logs_full_traceback(caplog):
    with caplog.at_level(logging.WARNING):
        jobs = GreenhouseSource("acme", _RaisingHttp(RuntimeError("boom"))).discover()
    assert jobs == []
    records = [r for r in caplog.records if "acme" in r.getMessage()]
    assert len(records) == 1
    assert records[0].exc_info is not None


def test_lever_404_logs_compact_without_traceback(caplog):
    with caplog.at_level(logging.INFO):
        jobs = LeverSource("dead-site", _RaisingHttp(_http_error(404))).discover()
    assert jobs == []
    records = [r for r in caplog.records if "dead-site" in r.getMessage()]
    assert len(records) == 1
    assert records[0].exc_info is None


def test_ashby_404_logs_compact_without_traceback(caplog):
    with caplog.at_level(logging.INFO):
        jobs = AshbySource("dead-board", _RaisingHttp(_http_error(404))).discover()
    assert jobs == []
    records = [r for r in caplog.records if "dead-board" in r.getMessage()]
    assert len(records) == 1
    assert records[0].exc_info is None
