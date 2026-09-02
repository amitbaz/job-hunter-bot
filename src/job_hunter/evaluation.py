from __future__ import annotations

import json
from typing import TYPE_CHECKING

from job_hunter.market_eligibility import evaluate_market_eligibility
from job_hunter.market_policy import market_by_id, salary_floor_for_job
from job_hunter.models import CandidateContext, Evaluation, Job, MarketPolicy, SearchPolicy

if TYPE_CHECKING:
    from job_hunter.gemini import GeminiClient

SCORE_MAXIMA = {
    "role_seniority": 30,
    "technical": 25,
    "product_architecture": 20,
    "career_direction": 10,
    "location_language": 10,
    "company_environment": 5,
}

HIGH_PRIORITY_THRESHOLD = 85


class EvaluationError(ValueError):
    pass


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


def _format_evidence(label: str, values: list[str]) -> str:
    if not values:
        return f"{label}: none noted"
    return f"{label}: " + "; ".join(values)


def _serialize_context(context: CandidateContext) -> str:
    """Render a CandidateContext as compact factual text for a prompt.

    Replaces the old wholesale full-profile dump: only the extracted,
    validated evidence and preferences are sent, once per job instead of
    the entire raw candidate profile.
    """
    prefs = context.preferences
    lines = [
        f"Candidate summary: {context.evaluation_summary}",
        _format_evidence("Preferred roles", prefs.preferred_roles),
        _format_evidence("Preferred seniority", prefs.preferred_seniority),
        _format_evidence("Must-have signals", prefs.must_have_signals),
        _format_evidence("Nice-to-have signals", prefs.nice_to_have_signals),
        _format_evidence("Preferred locations", prefs.preferred_locations),
        _format_evidence("Signals to avoid", prefs.avoid_signals),
        _format_evidence("Technical skills", context.technical_skills),
        _format_evidence("Architecture evidence", context.architecture_evidence),
        _format_evidence("Leadership/ownership evidence", context.leadership_ownership),
        _format_evidence("Agentic AI evidence", context.agentic_ai_evidence),
        _format_evidence("Product/domain evidence", context.product_domain_evidence),
        _format_evidence("Location/language facts", context.location_language_facts),
        _format_evidence("Career direction", context.career_direction),
        _format_evidence("Company environment preferences", context.company_environment),
    ]
    return "\n".join(lines)


_FULL_STACK_TITLE_MARKERS = ("full stack", "full-stack")

# Verbatim per the market-driven search strategy plan: the candidate is a
# senior frontend engineer, not a senior backend engineer, and Gemini must
# not credit backend seniority it has no evidence for.
_FULL_STACK_BACKEND_RAMP_PARAGRAPH = (
    "The candidate is a senior frontend engineer but is earlier than junior-level "
    "in backend depth today. Treat React/Next.js/TypeScript ownership as senior "
    "evidence. Node.js/TypeScript APIs, REST/GraphQL, PostgreSQL/Supabase and "
    "similar product-backend work may be realistic ramp-up areas. Do not invent "
    "senior backend experience. Backend-dominant ownership is a gap and may make "
    "the role unsuitable."
)


def _is_full_stack_role(title: str) -> bool:
    normalized = (title or "").lower()
    return any(marker in normalized for marker in _FULL_STACK_TITLE_MARKERS)


def _market_policy_block(job: Job, market: MarketPolicy) -> str:
    """Render the market's configured policy plus deterministic eligibility
    signals (reusing evaluate_market_eligibility rather than reimplementing
    sponsorship/remote/warning detection) for the prompt."""
    eligibility = evaluate_market_eligibility(job, market)
    salary_floor = salary_floor_for_job(job, market)
    allowed_languages = ", ".join(market.allowed_languages) or "none configured"
    warnings_text = "; ".join(eligibility.warnings) if eligibility.warnings else "none noted"

    return "\n".join(
        [
            f"Market ID: {market.id}",
            f"Gross base salary floor: {market.salary.currency} {salary_floor}",
            f"Allowed required languages: {allowed_languages}",
            f"Remote policy: {market.remote_policy}",
            f"Relocation policy: {market.relocation_policy}",
            f"Sponsorship policy: {market.sponsorship_policy}",
            f"Deterministic sponsorship status: {eligibility.sponsorship_status}",
            f"Deterministic international-remote status: {eligibility.international_remote_status}",
            f"Deterministic warnings: {warnings_text}",
        ]
    )


def _market_rules_block(job: Job) -> str:
    rules = """Rules:
- Only use evidence present in the candidate context and job description below. Never invent candidate facts.
- Unstated or unclear requirements are gaps, not invented facts.
- Missing salary is unknown, not a blocker.
- Disclosed gross base max below market floor is a blocker.
- Hybrid/onsite/relocation is not a blocker when market policy allows it.
- Explicit no-sponsorship is a blocker when sponsorship is required; omission is unknown.
- Disallowed language blocks only when explicitly required; nice-to-have does not.
- Time-zone overlap is informational and must be preserved in location_note.
- Sponsorship/international-remote uncertainty must be preserved in location_note.
- List every hard blocker in hard_blockers; otherwise leave it empty."""

    if _is_full_stack_role(job.title):
        rules += "\n\n" + _FULL_STACK_BACKEND_RAMP_PARAGRAPH

    return rules


def _build_evaluation_prompt(
    job: Job,
    context: CandidateContext,
    policy: SearchPolicy,
    market: MarketPolicy | None = None,
) -> str:
    maxima_lines = "\n".join(f"- {key}: max {value}" for key, value in SCORE_MAXIMA.items())

    if market is None:
        return f"""You are evaluating a job posting against a candidate profile for a remote-only job search.

Score EXACTLY these components, each an integer from 0 up to its stated maximum:
{maxima_lines}

Rules:
- Only use evidence present in the candidate context and job description below. Never invent candidate facts.
- Unstated or unclear requirements are gaps, not invented facts.
- Compensation floor is EUR {policy.salary_floor_eur}. A disclosed maximum below the floor is a hard blocker.
- A role that is not remote, or requires relocation, is a hard blocker.
- List every hard blocker in hard_blockers; otherwise leave it empty.

Return ONLY JSON with this exact shape and no markdown fences:
{{"scores": {{"role_seniority": int, "technical": int, "product_architecture": int, "career_direction": int, "location_language": int, "company_environment": int}}, "total_score": int, "hard_blockers": [string], "strengths": [string], "gaps": [string], "salary_note": string, "location_note": string, "decision": string, "rationale": string}}

Candidate context:
{_serialize_context(context)}

Job title: {job.title}
Company: {job.company}
Location: {job.location}
Remote: {job.remote}
Job description:
{job.description}
"""

    return f"""You are evaluating a job posting against a candidate profile for a remote-only job search.

Score EXACTLY these components, each an integer from 0 up to its stated maximum:
{maxima_lines}

{_market_rules_block(job)}

Market policy:
{_market_policy_block(job, market)}

Return ONLY JSON with this exact shape and no markdown fences:
{{"scores": {{"role_seniority": int, "technical": int, "product_architecture": int, "career_direction": int, "location_language": int, "company_environment": int}}, "total_score": int, "hard_blockers": [string], "strengths": [string], "gaps": [string], "salary_note": string, "location_note": string, "decision": string, "rationale": string}}

Candidate context:
{_serialize_context(context)}

Job title: {job.title}
Company: {job.company}
Location: {job.location}
Remote: {job.remote}
Job description:
{job.description}
"""


def evaluate_job(job: Job, context: CandidateContext, policy: SearchPolicy, gemini: "GeminiClient") -> Evaluation:
    market = market_by_id(policy, job.market_id) if job.market_id and policy.markets else None

    raw = gemini.generate_text(
        _build_evaluation_prompt(job, context, policy, market),
        purpose="job_evaluation",
        thinking_level="low",
        max_output_tokens=1200,
        json_mode=True,
    )
    cleaned = _strip_code_fences(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise EvaluationError(f"Gemini returned invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise EvaluationError("Gemini response must be a JSON object")

    scores = data.get("scores")
    if not isinstance(scores, dict) or set(scores.keys()) != set(SCORE_MAXIMA.keys()):
        raise EvaluationError(f"scores must contain exactly the keys {sorted(SCORE_MAXIMA)}")

    total = 0
    for key, maximum in SCORE_MAXIMA.items():
        value = scores[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise EvaluationError(f"score {key!r} must be an integer")
        if value < 0 or value > maximum:
            raise EvaluationError(f"score {key!r}={value} is outside 0..{maximum}")
        total += value

    declared_total = data.get("total_score")
    if declared_total != total:
        raise EvaluationError(f"total_score {declared_total!r} does not match component sum {total}")

    hard_blockers = data.get("hard_blockers") or []
    if not isinstance(hard_blockers, list):
        raise EvaluationError("hard_blockers must be a list")

    if hard_blockers:
        decision = "blocked"
    elif total >= HIGH_PRIORITY_THRESHOLD:
        decision = "high_priority"
    elif total >= policy.thresholds.get("package", 75):
        decision = "package_match"
    elif total >= policy.thresholds.get("possible", 65):
        decision = "possible_match"
    else:
        decision = "skip"

    return Evaluation(
        job_id=0,
        total_score=total,
        scores=scores,
        decision=decision,
        hard_blockers=hard_blockers,
        strengths=data.get("strengths") or [],
        gaps=data.get("gaps") or [],
        salary_note=data.get("salary_note", "") or "",
        location_note=data.get("location_note", "") or "",
        rationale=data.get("rationale", "") or "",
        model=gemini.model,
        market_id=job.market_id or "",
    )
