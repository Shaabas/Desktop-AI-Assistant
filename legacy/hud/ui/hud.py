from __future__ import annotations

import re


def sanitize_hud_summary(raw_text: str) -> str:
    if not raw_text or not raw_text.strip():
        return ""

    clauses = [c.strip() for c in re.split(r"[|\n]", raw_text) if c.strip()]
    summaries: list[str] = []

    for clause in clauses:
        clean = re.sub(r"\(\s*PID\s*\d+\s*\)", "", clause, flags=re.IGNORECASE).strip()
        clean = re.sub(r"\(\s*\d+x\s*\)", "", clean, flags=re.IGNORECASE).strip()

        prev = None
        while prev != clean and "[" in clean and "]" in clean:
            prev = clean
            clean = re.sub(r"\[[^\[\]]*\]", "", clean).strip()

        clean = re.sub(
            r"^(?:Step\s*\d+:|Failed\s+at\s+Step\s*\d+:|JARVIS:)\s*",
            "",
            clean,
            flags=re.IGNORECASE,
        ).strip()

        clean = re.sub(
            r"\s+(?:natively\s+in\s+[\w-]+|via\s+[\w\s-]+|with\s+left\s+button.*)$",
            "",
            clean,
            flags=re.IGNORECASE,
        ).strip()

        clean = re.sub(r"[\.\s]+$", "", clean).strip()

        if not clean:
            continue

        c_lower = clean.lower()

        m_app = re.search(
            r"^(?:opened|focused|launched)\s+(?:running\s+)?(?:application\s+|browser\s+|app\s+)?['\"]?([\w\s.-]+?)['\"]?$",
            clean,
            re.IGNORECASE,
        )
        if m_app:
            app_name = m_app.group(1).strip()
            if app_name:
                app_name = app_name[0].upper() + app_name[1:]
                summaries.append(f"Opened {app_name}")
                continue

        if "youtube.com" in c_lower or ("youtube" in c_lower and any(k in c_lower for k in ["navigated", "opened", "http", "browser"])):
            if "search" in c_lower:
                m_q = re.search(
                    r"search(?:ed)?(?:\s+youtube)?(?:\s+for)?\s+['\"]?(.*?)['\"]?$",
                    clean,
                    re.IGNORECASE,
                )
                q_str = m_q.group(1).strip() if m_q else ""
                summaries.append(f"Searched YouTube for '{q_str}'" if q_str else "Searched YouTube")
            else:
                summaries.append("Opened YouTube")
            continue

        if re.search(r"\b(?:played|playing|playback|video)\b", c_lower) and any(k in c_lower for k in ["youtube", "result", "triggered", "stream"]):
            m_q = re.search(r"for\s+['\"]?(.*?)['\"]?$", clean, re.IGNORECASE)
            q_str = m_q.group(1).strip() if m_q else ""
            if q_str and q_str.lower() not in {"video", "youtube video", "a video", "first youtube video result"}:
                summaries.append(f"Played video '{q_str}'")
            else:
                summaries.append("Played a YouTube video")
            continue

        if "http://" in c_lower or "https://" in c_lower:
            m_url = re.search(r"https?://(?:www\.)?([\w.-]+)", clean, re.IGNORECASE)
            if m_url:
                domain = m_url.group(1).split(".")[0].capitalize()
                summaries.append(f"Opened {domain}")
                continue

        if "terminal" in c_lower or "executed" in c_lower:
            m_cmd = re.search(
                r"executed\s+['\"]?(.*?)['\"]?(?:\s+command)?(?:\s+in\s+terminal)?$",
                clean,
                re.IGNORECASE,
            )
            if m_cmd and m_cmd.group(1).strip():
                summaries.append(f"Executed '{m_cmd.group(1).strip()}' in Terminal")
                continue

        summaries.append(clean)

    if not summaries:
        return raw_text.strip()

    return "\n".join(summaries)