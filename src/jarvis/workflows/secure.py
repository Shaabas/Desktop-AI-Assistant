from __future__ import annotations

from typing import Any, Callable

from ..agent.security import SecurityGuard


class SecureComputerProxy:
    """Action-level safety boundary around any Computer implementation."""

    def __init__(self, inner: Any, security: SecurityGuard, confirm: Callable[[str], bool]) -> None:
        self._inner = inner
        self._security = security
        self._confirm = confirm

    def _check_text(self, text: str) -> None:
        risky, reason = self._security.evaluate_text_input(text)
        if not risky:
            return
        try:
            approved = self._confirm(reason)
        except Exception as exc:
            raise PermissionError(
                f"Sensitive text input blocked because confirmation failed: {reason}"
            ) from exc
        if approved is not True:
            raise PermissionError(f"Sensitive text input blocked: confirmation denied or unavailable: {reason}")

    def type_text(self, text: str, *args: Any, **kwargs: Any) -> Any:
        self._check_text(text)
        return self._inner.type_text(text, *args, **kwargs)

    def type_text_at(self, *args: Any, **kwargs: Any) -> Any:
        text = kwargs.get("text") if "text" in kwargs else (args[2] if len(args) > 2 else "")
        self._check_text(str(text))
        return self._inner.type_text_at(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
