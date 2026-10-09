#!/usr/bin/env python3
"""
Speech-to-text transcription helper for Deskwright overlay mic input.
Uses the Gemini API to transcribe user speech audio accurately.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure environment is loaded from .env
project_root = Path(os.environ.get("JARVIS_ROOT", Path(__file__).resolve().parents[4]))
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path)
try:
    import json
    key_path = project_root / "config" / "api_keys.json"
    if key_path.is_file() and not os.environ.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = str(json.loads(key_path.read_text(encoding="utf-8")).get("gemini_api_key") or "")
except Exception:
    pass

def main():
    if len(sys.argv) < 2:
        sys.exit(1)

    audio_path = sys.argv[1]
    if not os.path.isfile(audio_path) or os.path.getsize(audio_path) == 0:
        sys.exit(1)

    try:
        from google import genai
        from google.genai import types

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            sys.exit(1)

        client = genai.Client(api_key=api_key)

        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        model_name = os.environ.get("GEMINI_TRANSCRIBE_MODEL", "gemini-2.5-flash")

        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                "You are an accurate voice dictation assistant. Transcribe the user's spoken words in this audio exactly as uttered into clean plain text for a computer command. Do not add quotes, introductory remarks, or any surrounding text. Output only the transcribed instruction.",
            ],
        )

        if response and response.text:
            text = response.text.strip()
            print(text)
    except Exception as e:
        sys.stderr.write(f"Transcription error: {e}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
