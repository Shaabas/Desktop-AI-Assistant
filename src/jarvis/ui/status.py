from __future__ import annotations

import re


def summarize_hud_line(raw_text: str, *, max_chars: int = 150) -> str:
    """Condense a verbose JARVIS result into the single line used by the UI."""
    text = str(raw_text or "").strip()
    if not text:
        return ""

    clauses = [c.strip() for c in re.split(r"[|\n]", text) if c.strip()]
    cleaned: list[str] = []
    for clause in clauses:
        value = re.sub(r"\(\s*PID\s*\d+\s*\)", "", clause, flags=re.I).strip()
        value = re.sub(r"\(\s*\d+x\s*\)", "", value, flags=re.I).strip()
        value = re.sub(r"^(?:Step\s*\d+:|Failed\s+at\s+Step\s*\d+:|JARVIS:)\s*", "", value, flags=re.I).strip()
        value = re.sub(r"[.\s]+$", "", value).strip()
        if value:
            cleaned.append(value)

    result = " · ".join(cleaned) if cleaned else text.replace("\n", " ").strip()
    if len(result) > max_chars:
        result = result[: max_chars - 1].rstrip() + "…"
    return result
