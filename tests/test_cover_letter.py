from datetime import date

import pytest

from job_hunter.cover_letter import generate_cover_letter
from job_hunter.models import Evaluation, Job


class FakeGemini:
    def __init__(self):
        self.text = ""
        self.prompts = []

    def generate_text(self, prompt, *, json_mode=False):
        self.prompts.append((prompt, json_mode))
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


def test_generate_cover_letter_returns_stripped_text(fake_gemini, job, evaluation):
    fake_gemini.text = "  Dear Hiring Team,\n\nI want to join Acme as Senior Product Engineer.\n\nBest,\nAmit  "
    result = generate_cover_letter(job, evaluation, "profile", "template", fake_gemini, date(2026, 8, 30))
    assert result.startswith("Dear Hiring Team,")
    assert result.endswith("Amit")


def test_cover_letter_rejects_unreplaced_placeholders(fake_gemini, job, evaluation):
    fake_gemini.text = "Dear Hiring Team, I want [Position] at [Company]."
    with pytest.raises(ValueError):
        generate_cover_letter(job, evaluation, "profile", "template", fake_gemini, date(2026, 8, 30))


def test_cover_letter_rejects_empty_output(fake_gemini, job, evaluation):
    fake_gemini.text = "   "
    with pytest.raises(ValueError):
        generate_cover_letter(job, evaluation, "profile", "template", fake_gemini, date(2026, 8, 30))


def test_cover_letter_prompt_includes_job_and_date(fake_gemini, job, evaluation):
    fake_gemini.text = "Dear Hiring Team, I am excited to apply."
    generate_cover_letter(job, evaluation, "profile", "template", fake_gemini, date(2026, 8, 30))
    prompt, _ = fake_gemini.prompts[0]
    assert "Acme" in prompt
    assert "Senior Product Engineer" in prompt
    assert "2026-08-30" in prompt
