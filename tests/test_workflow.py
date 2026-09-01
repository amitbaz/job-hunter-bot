from pathlib import Path

import yaml


def test_gmail_sync_step_is_bounded_and_fail_open():
    workflow = yaml.safe_load(Path(".github/workflows/daily.yml").read_text())
    job = workflow["jobs"]["run"]
    gmail_step = next(
        step
        for step in job["steps"]
        if step.get("name") == "Sync Gmail intelligence"
    )

    assert job["timeout-minutes"] == 60
    assert gmail_step["timeout-minutes"] == 10
    assert gmail_step["continue-on-error"] is True
