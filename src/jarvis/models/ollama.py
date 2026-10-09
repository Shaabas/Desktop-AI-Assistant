"""Small, local-only Ollama client used by JARVIS.

Gemini remains isolated inside the Computer Use engine. Ollama handles
conversation and workflow adaptation so the two responsibilities stay cleanly
separated.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, host: str | None = None, model: str | None = None) -> None:
        self.host = (host or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
        self.model = model or os.getenv("LOCAL_LLM_MODEL") or self._load_model_from_config() or "qwen3.5:2b"

    def _load_model_from_config(self) -> str | None:
        root = Path(__file__).resolve().parents[3]
        path = root / "config" / "settings.json"
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
                return data.get("ollama_model") or data.get("local_llm_model")
        except Exception:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data.get("ollama_model") or data.get("local_llm_model")
            except Exception:
                return None

    def available(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", headers={"User-Agent": "JARVIS/1.0"})
            with urllib.request.urlopen(req, timeout=2.5) as response:
                return response.status == 200
        except Exception:
            return False

    def generate(self, prompt: str, system: str = "", *, json_format: bool = False, timeout: float = 120.0) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "keep_alive": "60m",
            "options": {"temperature": 0.0 if json_format else 0.2, "num_ctx": 4096},
        }
        if json_format:
            payload["format"] = "json"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "JARVIS/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                return str(data.get("response", ""))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc

    def answer(self, prompt: str) -> str:
        return self.generate(
            prompt,
            system="You are JARVIS. Answer clearly and concisely. Do not claim to have taken desktop actions unless you actually did them.",
        ).strip()
