from __future__ import annotations

import json
from typing import TYPE_CHECKING

from job_hunter.models import CandidateContext, Evaluation, Job, SearchPolicy

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


def _build_evaluation_prompt(job: Job, context: CandidateContext, policy: SearchPolicy) -> str:
    maxima_lines = "\n".join(f"- {key}: max {value}" for key, value in SCORE_MAXIMA.items())
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


def evaluate_job(job: Job, context: CandidateContext, policy: SearchPolicy, gemini: "GeminiClient") -> Evaluation:
    raw = gemini.generate_text(
        _build_evaluation_prompt(job, context, policy),
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
    )
