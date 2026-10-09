from __future__ import annotations

from .surface import AssistantState
from ..gnome.bridge import GnomeShellBridge


class GnomeAssistantSurface:
    """Platform UI adapter for the GNOME Shell Deskwright surface."""

    def __init__(self, bridge: GnomeShellBridge | None = None) -> None:
        self.bridge = bridge or GnomeShellBridge()

    @property
    def available(self) -> bool:
        return self.bridge.available

    def activate(self) -> bool:
        return self.bridge.activate_assistant()

    def close(self) -> bool:
        return self.bridge.close_assistant()

    def set_state(self, state: AssistantState | str, detail: str = "") -> bool:
        value = state.value if isinstance(state, AssistantState) else str(state)
        return self.bridge.set_assistant_state(value, detail)

    def add_message(self, role: str, text: str) -> bool:
        return self.bridge.append_conversation_message(role, text)

    def request_voice_capture(self) -> bool:
        return self.bridge.request_voice_capture()

    def set_activity(self, text: str) -> bool:
        return self.bridge.set_activity(text)

    def show_confirmation(self, reason: str) -> bool:
        return self.bridge.show_confirmation(reason)

    def clear_confirmation(self) -> bool:
        return self.bridge.clear_confirmation()
