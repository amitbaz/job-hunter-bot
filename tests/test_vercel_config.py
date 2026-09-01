import json
from pathlib import Path


def test_vercel_config_does_not_map_root_main_as_legacy_function():
    config = json.loads(Path("vercel.json").read_text())

    assert config["installCommand"] == "pip install -e '.[webhook]'"

    functions = config.get("functions", {})
    assert "main.py" not in functions
    assert all(pattern.startswith("api/") for pattern in functions)
