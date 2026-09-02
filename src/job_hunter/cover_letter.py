from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from job_hunter.models import CandidateContext, Evaluation, Job

if TYPE_CHECKING:
    from job_hunter.gemini import GeminiClient

_KNOWN_PLACEHOLDERS = (
    "[Company]",
    "[Position]",
    "[Role]",
    "[Date]",
    "[Hiring Manager]",
    "[Your Name]",
    "[Team]",
)


def _build_cover_letter_prompt(
    job: Job, evaluation: Evaluation, context: CandidateContext, template: str, today: date
) -> str:
    strengths = ", ".join(evaluation.strengths) or "none noted"
    gaps = ", ".join(evaluation.gaps) or "none noted"
    career_evidence = "; ".join(context.career_evidence) or "none noted"
    return f"""Write a concise, ready-to-send cover letter for this job application.

NEVER invent facts about the candidate. Only use what is stated in the candidate context below.
Use the template's voice and structure as a reference, but replace every bracket placeholder
(such as [Company], [Position], [Date]) with the real values given here. The final letter must
not contain any remaining bracket placeholder text.

Candidate career evidence:
{career_evidence}

Cover letter template (voice/structure reference only):
{template}

Job title: {job.title}
Company: {job.company}
Today's date: {today.isoformat()}

Candidate strengths for this role: {strengths}
Known gaps to acknowledge tactfully or omit: {gaps}

Return only the final cover letter text. No markdown code fences, no commentary.
"""


def generate_cover_letter(
    job: Job,
    evaluation: Evaluation,
    context: CandidateContext,
    template: str,
    gemini: "GeminiClient",
    today: date,
) -> str:
    text = gemini.generate_text(
        _build_cover_letter_prompt(job, evaluation, context, template, today),
        purpose="cover_letter",
        thinking_level="low",
        max_output_tokens=800,
    )
    text = text.strip()

    if not text:
        raise ValueError("Gemini returned an empty cover letter")

    lowered = text.lower()
    for placeholder in _KNOWN_PLACEHOLDERS:
        if placeholder.lower() in lowered:
            raise ValueError(f"Cover letter contains unreplaced placeholder {placeholder!r}")

    return text
