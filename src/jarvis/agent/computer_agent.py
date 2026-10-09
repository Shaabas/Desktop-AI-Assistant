from __future__ import annotations

from typing import Any, Callable, Literal
import threading

from ..computer_use import BrowserAgent


class JarvisComputerUseAgent(BrowserAgent):
    """Thin JARVIS adapter; Google CUA action/reasoning code remains inherited."""

    def __init__(self, *args: Any, confirmation_callback: Callable[[str], bool] | None = None, cancel_event: threading.Event | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._confirmation_callback = confirmation_callback
        self._cancel_event = cancel_event or threading.Event()

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise RuntimeError("Computer-use task cancelled by user")

    def run_one_iteration(self):
        self._check_cancelled()
        result = super().run_one_iteration()
        self._check_cancelled()
        return result

    def handle_action(self, action, use_legacy_actions):
        self._check_cancelled()
        return super().handle_action(action, use_legacy_actions)

    def _get_safety_confirmation(self, safety: dict[str, Any]) -> Literal["CONTINUE", "TERMINATE"]:
        explanation = str(safety.get("explanation") or "The model requested a safety confirmation.")
        # A missing or failed confirmation channel is never implicit approval.
        if self._confirmation_callback is None:
            print("[JARVIS] Confirmation callback unavailable; blocking sensitive action.")
            return "TERMINATE"
        try:
            approved = self._confirmation_callback(explanation)
        except Exception as exc:
            print(f"[JARVIS] Confirmation failed; blocking sensitive action: {exc}")
            return "TERMINATE"
        return "CONTINUE" if approved is True else "TERMINATE"
