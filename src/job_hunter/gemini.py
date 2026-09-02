from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from job_hunter.gemini_usage import GeminiQuotaPaused

if TYPE_CHECKING:
    import requests

    from job_hunter.gemini_usage import GeminiPauseKind, GeminiPurpose, GeminiUsageTracker
    from job_hunter.http import HttpClient

logger = logging.getLogger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}


class GeminiError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _classify_429(response: requests.Response) -> tuple[GeminiPauseKind, str | None]:
    """Classify a Gemini 429 body into one of the design spec's three pause kinds.

    The real signal is the structured `quotaId`/`quotaMetric` substring match on
    `PerDay` vs `PerMinute`: those are realistic against Google's actual
    free-tier quota IDs (e.g.
    `GenerateRequestsPerDayPerProjectPerModel-FreeTier`). The snake_case
    message-text markers (`quota_exceeded`, `rate_limit_exceeded`,
    `too_many_requests`) are a cheap extra signal but are unlikely to appear
    verbatim in Google's actual prose error text — they are effectively
    fixture-shaped, matching this module's own test bodies more than anything
    Google is documented to return. Kept anyway because it's harmless and
    costs nothing when it doesn't match. An unparsable body or one matching
    neither classifies as `unknown`, which is paused just as conservatively.
    """
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return "unknown", None

    error = body.get("error")
    if not isinstance(error, dict):
        return "unknown", None

    tokens: list[str] = [str(error.get("status", "")), str(error.get("message", ""))]
    error_code = error.get("status") or None
    for detail in error.get("details") or []:
        if not isinstance(detail, dict):
            continue
        reason = detail.get("reason")
        if reason:
            tokens.append(str(reason))
            error_code = error_code or reason
        for violation in detail.get("violations") or []:
            if not isinstance(violation, dict):
                continue
            quota_id = violation.get("quotaId") or violation.get("quotaMetric")
            if quota_id:
                tokens.append(str(quota_id))

    haystack = " ".join(tokens).lower()
    if any(marker in haystack for marker in ("quota_exceeded", "perday", "per_day")):
        return "daily_quota", error_code
    if any(
        marker in haystack
        for marker in ("rate_limit_exceeded", "too_many_requests", "perminute", "per_minute")
    ):
        return "rate_limit", error_code
    return "unknown", error_code


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        http: HttpClient,
        tracker: GeminiUsageTracker | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._http = http
        self._tracker = tracker

    def generate_text(
        self,
        prompt: str,
        *,
        purpose: GeminiPurpose | None = None,
        thinking_level: str | None = None,
        max_output_tokens: int | None = None,
        json_mode: bool = False,
        json_schema: dict | None = None,
    ) -> str:
        now = _now()

        # A tracker-less client is a test affordance only (see class docstring);
        # production code always supplies one. Its own purpose validation is the
        # single guard for a missing/invalid `purpose`, so it must run before any
        # HTTP call is attempted.
        if self._tracker is not None:
            self._tracker.preflight(purpose, prompt, now)

        url = f"{_BASE_URL}/{self.model}:generateContent"
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"contents": [{"parts": [{"text": prompt}]}]}
        generation_config: dict[str, Any] = {}
        if thinking_level is not None:
            generation_config["thinkingConfig"] = {"thinkingLevel": thinking_level}
        if max_output_tokens is not None:
            generation_config["maxOutputTokens"] = max_output_tokens
        if json_mode or json_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            if json_schema is not None:
                generation_config["responseSchema"] = json_schema
        if generation_config:
            payload["generationConfig"] = generation_config

        response = self._http.post(
            url, json=payload, headers=headers, retry_status_codes=_RETRYABLE_STATUS_CODES
        )

        if response.status_code == 429:
            kind, error_code = _classify_429(response)
            if self._tracker is not None:
                # INVARIANT: every 429, of every kind, raises GeminiQuotaPaused
                # directly from what record_429 just persisted, writes exactly
                # one `quota_429` row, and writes zero `blocked_budget` rows —
                # regardless of rate_pause_seconds or how full the daily
                # budget is. Never re-derive the pause via a second
                # preflight() call: that re-runs the daily budget check, which
                # counts the quota_429 row just written and can trip
                # GeminiBudgetExceeded instead — the wrong exception type for
                # a call that indisputably reached Google. Tasks 5-8 branch on
                # GeminiQuotaPaused specifically.
                paused_until, reason = self._tracker.record_429(
                    purpose, prompt, now, kind=kind, error_code=error_code
                )
                raise GeminiQuotaPaused(
                    f"Gemini {self.model} is paused until {paused_until} ({reason})",
                    paused_until=paused_until,
                    reason=reason,
                )
            raise GeminiError(f"Gemini API error 429: {response.text}")

        if response.status_code >= 400:
            if self._tracker is not None:
                self._tracker.record_error(purpose, prompt, now, http_status=response.status_code)
            raise GeminiError(f"Gemini API error {response.status_code}: {response.text}")

        try:
            data = response.json()
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GeminiError("Gemini response missing content") from exc

        if not text:
            raise GeminiError("Gemini response missing content")

        if self._tracker is not None:
            usage = data.get("usageMetadata") if isinstance(data, dict) else None
            if usage:
                self._tracker.record_success(
                    purpose,
                    prompt,
                    now,
                    prompt_tokens=usage.get("promptTokenCount"),
                    output_tokens=usage.get("candidatesTokenCount"),
                    thinking_tokens=usage.get("thoughtsTokenCount"),
                    cached_tokens=usage.get("cachedContentTokenCount"),
                    total_tokens=usage.get("totalTokenCount"),
                )
            else:
                logger.warning(
                    "Gemini response for purpose %r missing usageMetadata; "
                    "recording estimated input tokens only",
                    purpose,
                )
                self._tracker.record_success(purpose, prompt, now)

        return text
