from job_hunter.models import CandidatePreferences, SearchPolicy
from job_hunter.preferences import extract_candidate_preferences


class FakeGemini:
    def __init__(self, response):
        self.model = "gemini-test"
        self.response = response
        self.calls = []

    def generate_text(self, prompt, *, json_mode=False):
        self.calls.append((prompt, json_mode))
        return self.response


def make_policy() -> SearchPolicy:
    return SearchPolicy(
        target_titles=["senior product engineer", "staff frontend engineer"],
        positive_keywords=["react", "typescript", "system design"],
        blocked_title_keywords=["junior"],
        salary_floor_eur=90000,
        thresholds={"package": 75, "possible": 65},
        role_families=["frontend engineering", "product engineering"],
        blocked_profession_title_phrases=["product manager", "designer"],
    )


def test_extract_candidate_preferences_returns_valid_json_fields():
    gemini = FakeGemini(
        """
        {
          "preferred_roles": ["Senior Product Engineer", "Staff Frontend Engineer"],
          "preferred_seniority": ["senior", "staff"],
          "must_have_signals": ["React", "TypeScript"],
          "nice_to_have_signals": ["system design", "mentorship"],
          "preferred_locations": ["Germany", "EU"],
          "avoid_signals": ["on-site", "manager"],
          "summary": "Senior frontend/product engineer focused on remote EU roles."
        }
        """
    )

    preferences = extract_candidate_preferences("candidate profile text", gemini, make_policy())

    assert preferences == CandidatePreferences(
        preferred_roles=["Senior Product Engineer", "Staff Frontend Engineer"],
        preferred_seniority=["senior", "staff"],
        must_have_signals=["React", "TypeScript"],
        nice_to_have_signals=["system design", "mentorship"],
        preferred_locations=["Germany", "EU"],
        avoid_signals=["on-site", "manager"],
        summary="Senior frontend/product engineer focused on remote EU roles.",
    )
    assert gemini.calls == [(gemini.calls[0][0], True)]
    assert "candidate profile text" in gemini.calls[0][0]


def test_extract_candidate_preferences_falls_back_on_malformed_json():
    policy = make_policy()
    gemini = FakeGemini('{"preferred_roles": "not-a-list"}')

    preferences = extract_candidate_preferences("candidate profile text", gemini, policy)

    assert preferences == CandidatePreferences(
        preferred_roles=["frontend engineering", "product engineering", "senior product engineer", "staff frontend engineer"],
        preferred_seniority=["senior", "staff"],
        must_have_signals=["react", "typescript", "system design"],
        nice_to_have_signals=[],
        preferred_locations=[],
        avoid_signals=["product manager", "designer"],
        summary="Fallback preferences derived from search policy.",
    )


def test_extract_candidate_preferences_empty_profile_uses_policy_fallback():
    policy = make_policy()
    gemini = FakeGemini("{}")

    preferences = extract_candidate_preferences("", gemini, policy)

    assert preferences.preferred_roles == [
        "frontend engineering",
        "product engineering",
        "senior product engineer",
        "staff frontend engineer",
    ]
    assert preferences.must_have_signals == ["react", "typescript", "system design"]
    assert preferences.avoid_signals == ["product manager", "designer"]
    assert preferences.summary == "Fallback preferences derived from search policy."
