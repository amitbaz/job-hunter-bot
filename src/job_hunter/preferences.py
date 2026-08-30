from __future__ import annotations

import json
from typing import TYPE_CHECKING

from job_hunter.models import CandidatePreferences, SearchPolicy
from job_hunter.normalize import normalize_text

if TYPE_CHECKING:
    from job_hunter.gemini import GeminiClient

_FIELDS = (
    "preferred_roles",
    "preferred_seniority",
    "must_have_signals",
    "nice_to_have_signals",
    "preferred_locations",
    "avoid_signals",
    "summary",
)
_SENIORITY_WORDS = ("intern", "junior", "mid", "senior", "staff", "lead", "principal", "head")
_LOCATION_HINT_WORDS = frozenset(
    {
        "remote",
        "europe",
        "emea",
        "germany",
        "netherlands",
        "spain",
        "portugal",
        "poland",
        "france",
        "italy",
        "austria",
        "ireland",
        "switzerland",
        "uk",
    }
)
_MAX_LIST_ITEMS = 8
_MAX_ITEM_LENGTH = 80
_MAX_SUMMARY_LENGTH = 280
FALLBACK_PREFERENCES_SUMMARY = "Fallback preferences derived from search policy."


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _build_extraction_prompt(profile: str) -> str:
    return f"""Extract a compact job-search preference profile from the candidate profile below.

Return ONLY JSON with exactly these fields and no markdown fences:
{{"preferred_roles": [string], "preferred_seniority": [string], "must_have_signals": [string], "nice_to_have_signals": [string], "preferred_locations": [string], "avoid_signals": [string], "summary": string}}

Rules:
- Use only evidence present in the candidate profile.
- Keep lists compact and deduplicated.
- Do not invent credentials, industries, or locations.
- The summary must be one short sentence.

Candidate profile:
{profile}
"""


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(value.strip())
    return deduped


def _validate_string_list(data: dict, key: str) -> list[str]:
    values = data.get(key)
    if not isinstance(values, list):
        raise ValueError(f"{key} must be a list")
    if len(values) > _MAX_LIST_ITEMS:
        raise ValueError(f"{key} exceeds {_MAX_LIST_ITEMS} items")
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{key} entries must be strings")
        item = value.strip()
        if not item:
            raise ValueError(f"{key} entries must be non-empty")
        if len(item) > _MAX_ITEM_LENGTH:
            raise ValueError(f"{key} entries must be <= {_MAX_ITEM_LENGTH} characters")
        cleaned.append(item)
    return _dedupe_preserve_order(cleaned)


def _fallback_seniority(policy: SearchPolicy) -> list[str]:
    values: list[str] = []
    for source in [*policy.role_families, *policy.target_titles]:
        words = set(normalize_text(source).split())
        for word in _SENIORITY_WORDS:
            if word in words and word not in values:
                values.append(word)
    return values


def _fallback_locations(policy: SearchPolicy) -> list[str]:
    values: list[str] = []
    sources = [*policy.search_queries, *policy.search_query_templates]
    for source in sources:
        for word in normalize_text(source).replace('"', " ").split():
            if word in _LOCATION_HINT_WORDS and word not in values:
                values.append(word)
    return values[:_MAX_LIST_ITEMS]


def _build_fallback_preferences(policy: SearchPolicy) -> CandidatePreferences:
    preferred_roles = _dedupe_preserve_order([*policy.role_families, *policy.target_titles])[:_MAX_LIST_ITEMS]
    must_have_signals = _dedupe_preserve_order(list(policy.positive_keywords))[:_MAX_LIST_ITEMS]
    avoid_signals = _dedupe_preserve_order(list(policy.blocked_profession_title_phrases))[:_MAX_LIST_ITEMS]
    return CandidatePreferences(
        preferred_roles=preferred_roles,
        preferred_seniority=_fallback_seniority(policy),
        must_have_signals=must_have_signals,
        nice_to_have_signals=[],
        preferred_locations=_fallback_locations(policy),
        avoid_signals=avoid_signals,
        summary=FALLBACK_PREFERENCES_SUMMARY,
    )


def _parse_preferences(raw: str) -> CandidatePreferences:
    try:
        data = json.loads(_strip_code_fences(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("response must be a JSON object")
    if set(data) != set(_FIELDS):
        raise ValueError(f"response must contain exactly {_FIELDS}")

    summary = data.get("summary")
    if not isinstance(summary, str):
        raise ValueError("summary must be a string")
    summary = summary.strip()
    if not summary:
        raise ValueError("summary must be non-empty")
    if len(summary) > _MAX_SUMMARY_LENGTH:
        raise ValueError(f"summary must be <= {_MAX_SUMMARY_LENGTH} characters")

    return CandidatePreferences(
        preferred_roles=_validate_string_list(data, "preferred_roles"),
        preferred_seniority=_validate_string_list(data, "preferred_seniority"),
        must_have_signals=_validate_string_list(data, "must_have_signals"),
        nice_to_have_signals=_validate_string_list(data, "nice_to_have_signals"),
        preferred_locations=_validate_string_list(data, "preferred_locations"),
        avoid_signals=_validate_string_list(data, "avoid_signals"),
        summary=summary,
    )


def extract_candidate_preferences(
    profile: str,
    gemini: "GeminiClient",
    policy: SearchPolicy,
) -> CandidatePreferences:
    if not profile.strip():
        return _build_fallback_preferences(policy)

    try:
        return _parse_preferences(gemini.generate_text(_build_extraction_prompt(profile), json_mode=True))
    except Exception:
        return _build_fallback_preferences(policy)


def preferences_source(preferences: CandidatePreferences) -> str:
    if preferences.summary == FALLBACK_PREFERENCES_SUMMARY:
        return "fallback"
    return "gemini"
