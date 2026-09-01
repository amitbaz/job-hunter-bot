import json

from job_hunter.models import Job, SearchPolicy, Settings
from job_hunter.navigation_store import get_navigation_session
from job_hunter.pipeline import run_pipeline
from job_hunter.store import JobStore
from job_hunter.telegram_navigation import parse_callback


class FakeGemini:
    model = "gemini-test"

    def generate_text(self, prompt, *, json_mode=False):
        if json_mode and "preferred_roles" in prompt:
            return json.dumps(
                {
                    "preferred_roles": ["Senior Product Engineer"],
                    "preferred_seniority": ["senior"],
                    "must_have_signals": ["React"],
                    "nice_to_have_signals": ["TypeScript"],
                    "preferred_locations": ["Germany"],
                    "avoid_signals": [],
                    "summary": "Senior frontend/product engineer.",
                }
            )
        if json_mode:
            return json.dumps(
                {
                    "scores": {
                        "role_seniority": 25,
                        "technical": 20,
                        "product_architecture": 15,
                        "career_direction": 8,
                        "location_language": 7,
                        "company_environment": 5,
                    },
                    "total_score": 80,
                    "hard_blockers": [],
                    "strengths": ["React"],
                    "gaps": [],
                    "salary_note": "",
                    "location_note": "",
                    "decision": "possible_match",
                    "rationale": "Good fit",
                }
            )
        raise AssertionError("cover letter should not be generated for possible_match")


class FakeSource:
    def __init__(self, jobs):
        self.jobs = jobs

    def discover(self):
        return self.jobs


class NavigatorTelegram:
    def __init__(self, *, card_result="nav-msg-1"):
        self.cards = []
        self.messages = []
        self.documents = []
        self.card_result = card_result

    def send_job_card(self, text, keyboard):
        self.cards.append((text, keyboard))
        return self.card_result

    def send_message(self, text):
        self.messages.append(text)
        return "msg-1"

    def send_document(self, path, caption):
        self.documents.append((path, caption))
        return "doc-1"


def _settings(tmp_path):
    return Settings(
        gemini_api_key="key",
        candidate_profile="profile",
        cover_letter_template="template",
        timezone="Europe/Berlin",
        scheduled_hour=9,
        policy=SearchPolicy(
            target_titles=["senior product engineer"],
            positive_keywords=["react"],
            blocked_title_keywords=["junior"],
            salary_floor_eur=90000,
            thresholds={"package": 75, "possible": 65},
            max_jobs_per_run=35,
        ),
        dry_run=False,
        telegram_bot_token="token",
        telegram_chat_id="chat",
        db_path=str(tmp_path / "state.sqlite3"),
    )


def _job(source_job_id, company, location):
    return Job(
        source="ashby",
        source_job_id=source_job_id,
        title="Senior Product Engineer",
        company=company,
        location=location,
        url=f"https://example.test/{source_job_id}",
        remote=True,
        description="React TypeScript product engineering role",
    )


def _session_id_from_keyboard(keyboard):
    parsed = parse_callback(keyboard[0][-1]["callback_data"])
    assert parsed is not None
    return parsed[1]


def test_pipeline_sends_one_sorted_navigator_and_persists_location(tmp_path):
    settings = _settings(tmp_path)
    store = JobStore(settings.db_path)
    telegram = NavigatorTelegram()
    jobs = [
        _job("2", "Beta", "Remote EU"),
        _job("1", "Acme", "Berlin"),
    ]

    run_pipeline(
        settings,
        sources=[FakeSource(jobs)],
        store=store,
        gemini=FakeGemini(),
        telegram=telegram,
    )

    assert len(telegram.cards) == 1
    text, keyboard = telegram.cards[0]
    assert "Company: Acme" in text
    assert "Location: Berlin" in text
    assert "Match: 80%" in text
    assert "1 / 2" == keyboard[1][1]["text"]

    session = get_navigation_session(store, _session_id_from_keyboard(keyboard))
    assert session is not None
    assert session.telegram_message_id == "nav-msg-1"
    assert [card.company for card in session.cards] == ["Acme", "Beta"]
    assert [card.location for card in session.cards] == ["Berlin", "Remote EU"]

    for job in jobs:
        job_id, _, _ = store.upsert_job(job)
        assert store.has_delivery(job_id, "telegram_message") is True


def test_pipeline_failed_navigator_send_keeps_jobs_pending(tmp_path):
    settings = _settings(tmp_path)
    store = JobStore(settings.db_path)
    telegram = NavigatorTelegram(card_result=None)
    job = _job("1", "Acme", "Berlin")

    run_pipeline(
        settings,
        sources=[FakeSource([job])],
        store=store,
        gemini=FakeGemini(),
        telegram=telegram,
    )

    job_id, _, _ = store.upsert_job(job)
    assert len(telegram.cards) == 1
    assert store.has_delivery(job_id, "telegram_message") is False
    assert job_id in store.pending_delivery_job_ids()


def test_pipeline_with_no_deliverable_jobs_sends_nothing(tmp_path):
    settings = _settings(tmp_path)
    store = JobStore(settings.db_path)
    telegram = NavigatorTelegram()
    irrelevant = Job(
        source="ashby",
        source_job_id="irrelevant",
        title="Junior QA Tester",
        company="Nope",
        location="Berlin",
        description="manual testing",
    )

    run_pipeline(
        settings,
        sources=[FakeSource([irrelevant])],
        store=store,
        gemini=FakeGemini(),
        telegram=telegram,
    )

    assert telegram.cards == []
    assert telegram.messages == []
