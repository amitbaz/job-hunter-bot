import pytest

from job_hunter.market_eligibility import evaluate_market_eligibility
from job_hunter.market_policy import market_by_id
from job_hunter.models import Job
from tests.market_fixtures import make_market_policy


# --- Required cases from the task brief -----------------------------------


def test_berlin_german_required_is_blocked():
    policy = make_market_policy()
    market = market_by_id(policy, "germany_eu")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Berlin",
            description="Fluent German is required. English is used daily.",
        ),
        market,
    )
    assert result.allowed is False
    assert result.reason_code == "required_language"


def test_berlin_german_nice_to_have_survives():
    policy = make_market_policy()
    market = market_by_id(policy, "germany_eu")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Berlin",
            description="English required. German is a nice to have.",
        ),
        market,
    )
    assert result.allowed is True


def test_london_sponsorship_omitted_is_unknown():
    policy = make_market_policy()
    market = market_by_id(policy, "london")
    result = evaluate_market_eligibility(
        Job(source="x", title="Senior Frontend Engineer", location="London"), market
    )
    assert result.allowed is True
    assert result.sponsorship_status == "unknown"


# --- Language ---------------------------------------------------------------


def test_israeli_hebrew_required_is_allowed():
    policy = make_market_policy()
    market = market_by_id(policy, "israel_remote")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Tel Aviv",
            description="Fluent Hebrew is required. English is used with the wider team.",
        ),
        market,
    )
    assert result.allowed is True


# --- Israel remote / work-mode ---------------------------------------------


def test_israel_remote_with_international_scope_omitted_is_unknown():
    policy = make_market_policy()
    market = market_by_id(policy, "israel_remote")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Remote",
            remote=True,
            description="This is a fully remote role on our platform team.",
        ),
        market,
    )
    assert result.allowed is True
    assert result.international_remote_status == "unknown"


def test_israel_explicit_international_remote_is_available():
    policy = make_market_policy()
    market = market_by_id(policy, "israel_remote")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Remote",
            remote=True,
            description="This role is worldwide remote, work from anywhere.",
        ),
        market,
    )
    assert result.allowed is True
    assert result.international_remote_status == "available"


def test_israel_onsite_is_blocked():
    policy = make_market_policy()
    market = market_by_id(policy, "israel_remote")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Tel Aviv - Onsite",
            remote=False,
        ),
        market,
    )
    assert result.allowed is False


def test_israel_hybrid_is_blocked():
    policy = make_market_policy()
    market = market_by_id(policy, "israel_remote")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Tel Aviv",
            remote=True,
            description="This is a hybrid role requiring 3 days a week in our Tel Aviv office.",
        ),
        market,
    )
    assert result.allowed is False


def test_israel_must_be_based_in_israel_is_blocked():
    policy = make_market_policy()
    market = market_by_id(policy, "israel_remote")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Remote",
            remote=True,
            description="Candidates must be based in Israel for this role.",
        ),
        market,
    )
    assert result.allowed is False


# --- Sponsorship -------------------------------------------------------------


def test_london_explicit_no_sponsorship_is_blocked():
    policy = make_market_policy()
    market = market_by_id(policy, "london")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="London",
            description="We are unable to sponsor a visa for this role.",
        ),
        market,
    )
    assert result.allowed is False
    assert result.reason_code == "sponsorship_unavailable"


def test_singapore_sponsorship_omitted_is_allowed():
    policy = make_market_policy()
    market = market_by_id(policy, "singapore")
    result = evaluate_market_eligibility(
        Job(source="x", title="Senior Frontend Engineer", location="Singapore"), market
    )
    assert result.allowed is True
    assert result.sponsorship_status == "unknown"


@pytest.mark.parametrize("market_id", ["us_nyc_sf"])
@pytest.mark.parametrize(
    "location",
    ["New York City", "San Francisco"],
)
def test_nyc_sf_explicit_no_sponsorship_is_blocked(market_id, location):
    policy = make_market_policy()
    market = market_by_id(policy, market_id)
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location=location,
            description="Applicants must already be authorized to work in the US; no visa sponsorship.",
        ),
        market,
    )
    assert result.allowed is False
    assert result.reason_code == "sponsorship_unavailable"


def test_singapore_explicit_sponsorship_available_is_a_positive_signal():
    policy = make_market_policy()
    market = market_by_id(policy, "singapore")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Singapore",
            description="Employment pass sponsorship is available for the right candidate.",
        ),
        market,
    )
    assert result.allowed is True
    assert result.sponsorship_status == "available"


# --- Time-zone overlap: warning only -----------------------------------------


def test_time_zone_overlap_is_warning_only():
    policy = make_market_policy()
    market = market_by_id(policy, "israel_remote")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Remote",
            remote=True,
            description="This fully remote role requires significant overlap with Tel Aviv core hours.",
        ),
        market,
    )
    assert result.allowed is True
    assert len(result.warnings) >= 1


# --- Salary -------------------------------------------------------------------


def test_missing_salary_is_allowed():
    policy = make_market_policy()
    market = market_by_id(policy, "london")
    result = evaluate_market_eligibility(
        Job(source="x", title="Senior Frontend Engineer", location="London", description="Great team, great product."),
        market,
    )
    assert result.allowed is True
    assert result.disclosed_salary_max is None


def test_london_salary_below_floor_is_blocked():
    policy = make_market_policy()
    market = market_by_id(policy, "london")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="London",
            description="The base salary maximum for this role is £80k.",
        ),
        market,
    )
    assert result.allowed is False
    assert result.reason_code == "salary_below_floor"
    assert result.disclosed_salary_max == 80000


def test_singapore_salary_below_floor_is_blocked():
    policy = make_market_policy()
    market = market_by_id(policy, "singapore")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Singapore",
            description="Base pay is S$9,000 per month.",
        ),
        market,
    )
    assert result.allowed is False
    assert result.reason_code == "salary_below_floor"
    assert result.disclosed_salary_max == 108000


def test_israel_salary_below_floor_is_blocked():
    policy = make_market_policy()
    market = market_by_id(policy, "israel_remote")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Remote",
            remote=True,
            description="Base salary is ₪34,000 per month.",
        ),
        market,
    )
    assert result.allowed is False
    assert result.reason_code == "salary_below_floor"
    assert result.disclosed_salary_max == 408000


def test_nyc_salary_at_floor_is_allowed():
    policy = make_market_policy()
    market = market_by_id(policy, "us_nyc_sf")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="New York City",
            description="Base salary is $190,000 annually.",
        ),
        market,
    )
    assert result.allowed is True
    assert result.disclosed_salary_max == 190000


def test_sf_salary_below_floor_is_blocked():
    policy = make_market_policy()
    market = market_by_id(policy, "us_nyc_sf")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="San Francisco",
            description="Base salary is $190,000 annually.",
        ),
        market,
    )
    assert result.allowed is False
    assert result.reason_code == "salary_below_floor"
    assert result.disclosed_salary_max == 190000


def test_total_comp_language_is_not_compared_against_floor():
    policy = make_market_policy()
    market = market_by_id(policy, "london")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="London",
            description="Total compensation including equity can reach £150k.",
        ),
        market,
    )
    assert result.allowed is True
    assert result.disclosed_salary_max is None


# --- Employment type -----------------------------------------------------------


@pytest.mark.parametrize(
    "description",
    [
        "This is a freelance engagement.",
        "We are hiring a contractor for this project.",
        "This is a part-time position.",
        "This is a fixed-term contract for 6 months.",
        "This is an internship for students.",
    ],
)
def test_explicit_non_permanent_employment_types_are_blocked(description):
    policy = make_market_policy()
    market = market_by_id(policy, "germany_eu")
    result = evaluate_market_eligibility(
        Job(source="x", title="Senior Frontend Engineer", location="Berlin", description=description),
        market,
    )
    assert result.allowed is False
    assert result.reason_code == "employment_type_blocked"


def test_generic_employment_contract_wording_is_not_blocked():
    policy = make_market_policy()
    market = market_by_id(policy, "germany_eu")
    result = evaluate_market_eligibility(
        Job(
            source="x",
            title="Senior Frontend Engineer",
            location="Berlin",
            description="You will sign a standard employment contract with our German entity.",
        ),
        market,
    )
    assert result.allowed is True
