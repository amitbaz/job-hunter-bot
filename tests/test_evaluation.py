import json

import pytest

from job_hunter.evaluation import EvaluationError, evaluate_job
from job_hunter.models import Job, SearchPolicy


class FakeGemini:
    def __init__(self):
        self.text = ""
        self.model = "gemini-2.5-flash-lite"
        self.prompts = []

    def generate_text(self, prompt, *, json_mode=False):
        self.prompts.append((prompt, json_mode))
        return self.text


@pytest.fixture
def fake_gemini():
    return FakeGemini()


@pytest.fixture
def policy():
    return SearchPolicy(
        target_titles=["senior product engineer"],
        positive_keywords=["react"],
        blocked_title_keywords=["junior"],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
    )


@pytest.fixture
def job():
    return Job(source="ashby", title="Senior Product Engineer", description="React TypeScript remote")


def _valid_payload(**overrides):
    payload = {
        "scores": {
            "role_seniority": 28,
            "technical": 22,
            "product_architecture": 18,
            "career_direction": 8,
            "location_language": 9,
            "company_environment": 4,
        },
        "total_score": 89,
        "hard_blockers": [],
        "strengths": ["React expertise"],
        "gaps": ["No Rust experience"],
        "salary_note": "Not disclosed",
        "location_note": "Remote EU friendly",
        "decision": "high_priority",
        "rationale": "Strong fit",
    }
    payload.update(overrides)
    return payload


def test_evaluate_job_maps_high_priority_decision(fake_gemini, job, policy):
    fake_gemini.text = json.dumps(_valid_payload())
    evaluation = evaluate_job(job, "profile", policy, fake_gemini)
    assert evaluation.total_score == 89
    assert evaluation.decision == "high_priority"
    assert evaluation.model == "gemini-2.5-flash-lite"


def test_evaluate_job_recomputes_total_from_components(fake_gemini, job, policy):
    payload = _valid_payload(total_score=89)
    payload["scores"]["company_environment"] = 3
    fake_gemini.text = json.dumps(payload)
    with pytest.raises(EvaluationError):
        evaluate_job(job, "profile", policy, fake_gemini)


def test_evaluate_job_strips_markdown_code_fences(fake_gemini, job, policy):
    fake_gemini.text = "```json\n" + json.dumps(_valid_payload()) + "\n```"
    evaluation = evaluate_job(job, "profile", policy, fake_gemini)
    assert evaluation.total_score == 89


def test_evaluation_rejects_component_over_max(fake_gemini, job, policy):
    payload = _valid_payload()
    payload["scores"]["role_seniority"] = 31
    payload["total_score"] = 92
    fake_gemini.text = json.dumps(payload)
    with pytest.raises(EvaluationError):
        evaluate_job(job, "profile", policy, fake_gemini)


def test_evaluation_rejects_unknown_score_key(fake_gemini, job, policy):
    payload = _valid_payload()
    payload["scores"]["extra_key"] = 1
    fake_gemini.text = json.dumps(payload)
    with pytest.raises(EvaluationError):
        evaluate_job(job, "profile", policy, fake_gemini)


def test_evaluation_rejects_missing_score_key(fake_gemini, job, policy):
    payload = _valid_payload()
    del payload["scores"]["company_environment"]
    fake_gemini.text = json.dumps(payload)
    with pytest.raises(EvaluationError):
        evaluate_job(job, "profile", policy, fake_gemini)


def test_evaluation_hard_blocker_forces_blocked_decision(fake_gemini, job, policy):
    payload = _valid_payload(hard_blockers=["Not remote"])
    fake_gemini.text = json.dumps(payload)
    evaluation = evaluate_job(job, "profile", policy, fake_gemini)
    assert evaluation.decision == "blocked"


def test_evaluation_maps_skip_band(fake_gemini, job, policy):
    payload = _valid_payload(
        scores={
            "role_seniority": 15,
            "technical": 15,
            "product_architecture": 10,
            "career_direction": 5,
            "location_language": 5,
            "company_environment": 2,
        },
        total_score=52,
    )
    fake_gemini.text = json.dumps(payload)
    evaluation = evaluate_job(job, "profile", policy, fake_gemini)
    assert evaluation.decision == "skip"


def test_evaluation_rejects_invalid_json(fake_gemini, job, policy):
    fake_gemini.text = "not json"
    with pytest.raises(EvaluationError):
        evaluate_job(job, "profile", policy, fake_gemini)
