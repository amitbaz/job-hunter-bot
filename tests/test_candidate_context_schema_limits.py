from job_hunter.candidate_context import CANDIDATE_CONTEXT_SCHEMA


def test_candidate_context_schema_enforces_parser_array_limits():
    properties = CANDIDATE_CONTEXT_SCHEMA["properties"]

    preference_properties = properties["preferences"]["properties"]
    for name in (
        "preferred_roles",
        "preferred_seniority",
        "must_have_signals",
        "nice_to_have_signals",
        "preferred_locations",
        "avoid_signals",
    ):
        assert preference_properties[name]["maxItems"] == 8

    for name in (
        "technical_skills",
        "architecture_evidence",
        "leadership_ownership",
        "agentic_ai_evidence",
        "product_domain_evidence",
        "location_language_facts",
        "career_direction",
        "company_environment",
        "career_evidence",
    ):
        assert properties[name]["maxItems"] == 20
