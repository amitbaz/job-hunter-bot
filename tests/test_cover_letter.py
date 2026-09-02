from datetime import date

import pytest

from job_hunter.cover_letter import generate_cover_letter
from job_hunter.models import CandidateContext, CandidatePreferences, Evaluation, Job


class FakeGemini:
    def __init__(self):
        self.text = ""
        self.prompts = []

    def generate_text(
        self,
        prompt,
        *,
        purpose=None,
        thinking_level=None,
        max_output_tokens=None,
        json_mode=False,
        json_schema=None,
    ):
        self.prompts.append((prompt, purpose, thinking_level, max_output_tokens, json_mode))
        return self.text


@pytest.fixture
def fake_gemini():
    return FakeGemini()


@pytest.fixture
def job():
    return Job(source="ashby", title="Senior Product Engineer", company="Acme", description="React TypeScript remote")


@pytest.fixture
def evaluation():
    return Evaluation(
        job_id=1,
        total_score=89,
        scores={},
        decision="high_priority",
        hard_blockers=[],
        strengths=["React expertise"],
        gaps=["No Rust experience"],
        salary_note="",
        location_note="",
        rationale="",
        model="gemini-2.5-flash-lite",
    )


@pytest.fixture
def context():
    return CandidateContext(
        preferences=CandidatePreferences(
            preferred_roles=["Senior Product Engineer"],
            preferred_seniority=["senior"],
            must_have_signals=["React"],
            nice_to_have_signals=["TypeScript"],
            preferred_locations=["Germany"],
            avoid_signals=[],
            summary="Senior frontend/product engineer.",
        ),
        technical_skills=["React", "TypeScript"],
        architecture_evidence=[],
        leadership_ownership=[],
        agentic_ai_evidence=[],
        product_domain_evidence=[],
        location_language_facts=[],
        career_direction=[],
        company_environment=[],
        career_evidence=["Senior engineer at Acme for 5 years, shipped a React design system"],
        evaluation_summary="Strong senior product engineer.",
    )


def test_generate_cover_letter_returns_stripped_text(fake_gemini, job, evaluation, context):
    fake_gemini.text = "  Dear Hiring Team,\n\nI want to join Acme as Senior Product Engineer.\n\nBest,\nAmit  "
    result = generate_cover_letter(job, evaluation, context, "template", fake_gemini, date(2026, 8, 30))
    assert result.startswith("Dear Hiring Team,")
    assert result.endswith("Amit")


def test_cover_letter_rejects_unreplaced_placeholders(fake_gemini, job, evaluation, context):
    fake_gemini.text = "Dear Hiring Team, I want [Position] at [Company]."
    with pytest.raises(ValueError):
        generate_cover_letter(job, evaluation, context, "template", fake_gemini, date(2026, 8, 30))


def test_cover_letter_rejects_empty_output(fake_gemini, job, evaluation, context):
    fake_gemini.text = "   "
    with pytest.raises(ValueError):
        generate_cover_letter(job, evaluation, context, "template", fake_gemini, date(2026, 8, 30))


def test_cover_letter_prompt_includes_job_and_date(fake_gemini, job, evaluation, context):
    fake_gemini.text = "Dear Hiring Team, I am excited to apply."
    generate_cover_letter(job, evaluation, context, "template", fake_gemini, date(2026, 8, 30))
    prompt, _purpose, _thinking, _max_tokens, _json_mode = fake_gemini.prompts[0]
    assert "Acme" in prompt
    assert "Senior Product Engineer" in prompt
    assert "2026-08-30" in prompt


def test_cover_letter_prompt_uses_context_career_evidence_and_evaluation(fake_gemini, job, evaluation, context):
    fake_gemini.text = "Dear Hiring Team, I am excited to apply."
    generate_cover_letter(job, evaluation, context, "template", fake_gemini, date(2026, 8, 30))
    prompt, _purpose, _thinking, _max_tokens, _json_mode = fake_gemini.prompts[0]

    assert "Senior engineer at Acme for 5 years, shipped a React design system" in prompt
    assert "React expertise" in prompt
    assert "No Rust experience" in prompt
    assert "template" in prompt


def test_cover_letter_prompt_forbids_inventing_facts(fake_gemini, job, evaluation, context):
    fake_gemini.text = "Dear Hiring Team, I am excited to apply."
    generate_cover_letter(job, evaluation, context, "template", fake_gemini, date(2026, 8, 30))
    prompt, _purpose, _thinking, _max_tokens, _json_mode = fake_gemini.prompts[0]

    assert "NEVER invent facts about the candidate" in prompt


def test_cover_letter_uses_expected_resource_controls(fake_gemini, job, evaluation, context):
    fake_gemini.text = "Dear Hiring Team, I am excited to apply."
    generate_cover_letter(job, evaluation, context, "template", fake_gemini, date(2026, 8, 30))
    _prompt, purpose, thinking_level, max_output_tokens, _json_mode = fake_gemini.prompts[0]

    assert purpose == "cover_letter"
    assert thinking_level == "low"
    assert max_output_tokens == 800
