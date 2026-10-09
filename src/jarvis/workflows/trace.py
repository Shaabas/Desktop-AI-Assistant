from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ..computer_use.computers import Computer


class TracingComputer:
    """Proxy that records executable CUA actions without modifying the CUA engine."""

    RECORDED = {
        "open_web_browser", "click_at", "double_click_at", "triple_click_at", "middle_click_at",
        "right_click_at", "mouse_down", "mouse_up", "hover_at", "type_text", "type_text_at",
        "scroll_document", "scroll_at", "wait", "wait_5_seconds", "go_back", "go_forward",
        "search", "navigate", "key_combination", "press_key", "key_down", "key_up",
        "take_screenshot", "drag_and_drop",
    }

    def __init__(self, inner: Computer, *, on_action: Callable[[str, tuple[Any, ...], dict[str, Any]], None] | None = None) -> None:
        self._inner = inner
        self._on_action = on_action

    @property
    def actions(self) -> list[dict[str, Any]]:
        return getattr(self, "_actions", [])

    def _record(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        if not hasattr(self, "_actions"):
            self._actions = []
        item = {"method": name, "args": list(args), "kwargs": dict(kwargs)}
        self._actions.append(item)
        if self._on_action:
            self._on_action(name, args, kwargs)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._inner, name)
        if name not in self.RECORDED or not callable(target):
            return target

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self._record(name, args, kwargs)
            return target(*args, **kwargs)

        return wrapper
