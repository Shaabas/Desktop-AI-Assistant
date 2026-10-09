from __future__ import annotations

from enum import Enum
from typing import Protocol


class AssistantState(str, Enum):
    IDLE = "idle"
    OPENING = "opening"
    LISTENING = "listening"
    THINKING = "thinking"
    WORKING = "working"
    SPEAKING = "speaking"
    WAITING_CONFIRMATION = "waiting_confirmation"
    STOPPING = "stopping"
    ERROR = "error"


class AssistantSurface(Protocol):
    """Semantic contract between JARVIS core and a platform UI."""

    @property
    def available(self) -> bool: ...

    def activate(self) -> bool: ...

    def close(self) -> bool: ...

    def set_state(self, state: AssistantState | str, detail: str = "") -> bool: ...

    def add_message(self, role: str, text: str) -> bool: ...

    def set_activity(self, text: str) -> bool: ...

    def show_confirmation(self, reason: str) -> bool: ...

    def clear_confirmation(self) -> bool: ...
