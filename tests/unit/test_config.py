import json
from pathlib import Path


def test_api_keys_example_has_no_embedded_secret():
    path = Path("config/api_keys.example.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data.get("gemini_api_key", "") == ""