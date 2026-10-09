from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_settings() -> dict[str, Any]:
    path = project_root() / "config" / "settings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_api_keys() -> dict[str, Any]:
    path = project_root() / "config" / "api_keys.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def bootstrap_environment() -> dict[str, Any]:
    # Environment variables remain the authoritative Google CUA interface;
    # config/api_keys.json is simply a local convenience loader.
    keys = load_api_keys()
    mappings = {
        "gemini_api_key": "GEMINI_API_KEY",
        "browserbase_api_key": "BROWSERBASE_API_KEY",
        "browserbase_project_id": "BROWSERBASE_PROJECT_ID",
        "vertexai_project": "VERTEXAI_PROJECT",
        "vertexai_location": "VERTEXAI_LOCATION",
    }
    for key, env_name in mappings.items():
        value = keys.get(key)
        if value and not os.environ.get(env_name):
            os.environ[env_name] = str(value)

    settings = load_settings()
    if settings.get("cloud_computer_use_model") and not os.environ.get("GEMINI_MODEL"):
        os.environ["GEMINI_MODEL"] = str(settings["cloud_computer_use_model"])
    if settings.get("ollama_base_url") and not os.environ.get("OLLAMA_BASE_URL"):
        os.environ["OLLAMA_BASE_URL"] = str(settings["ollama_base_url"])
    if settings.get("ollama_model") and not os.environ.get("LOCAL_LLM_MODEL"):
        os.environ["LOCAL_LLM_MODEL"] = str(settings["ollama_model"])
    return settings
