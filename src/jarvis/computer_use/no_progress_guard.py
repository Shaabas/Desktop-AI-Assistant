"""Fail-safe loop detection for repeated Gemini Computer Use actions.

The signature intentionally ignores natural-language intent strings and quantizes
normalized screen coordinates. This catches the same action when Gemini slightly
rephrases its intent or jitters a few pixels around the same target.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

# Actions that normally observe or wait for the screen rather than attempting to
# change it are not counted as progress-risk actions.
OBSERVATION_ACTIONS = {
    "wait",
    "wait_5_seconds",
    "take_screenshot",
    "hover_at",
    "move",
}

IGNORED_ARGUMENTS = {
    "intent",
    "safety_decision",
    "description",
    "reasoning",
    "rationale",
}

ACTION_ALIASES = {
    "click_at": "click",
    "left_click_at": "click",
    "mouse_click": "click",
    "keyboard_press": "press_key",
    "key_combination": "hotkey",
}

COORDINATE_ARGUMENTS = {
    "x",
    "y",
    "destination_x",
    "destination_y",
    "start_x",
    "start_y",
    "end_x",
    "end_y",
}


def _rounded_coordinate(value: Any, bucket: int) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return int(round(float(value) / bucket) * bucket)


def action_signature(function_call: Any, coordinate_bucket: int = 25) -> str | None:
    """Return a stable signature for an actionable call, or None for observers."""
    name = str(getattr(function_call, "name", "") or "").strip().lower()
    if not name or name in OBSERVATION_ACTIONS:
        return None

    raw_args = getattr(function_call, "args", None) or {}
    if not isinstance(raw_args, Mapping):
        return json.dumps([name, repr(raw_args)], sort_keys=True, separators=(",", ":"))

    normalized_args: dict[str, Any] = {}
    for key, value in raw_args.items():
        key_text = str(key)
        if key_text.lower() in IGNORED_ARGUMENTS:
            continue
        if key_text.lower() in COORDINATE_ARGUMENTS:
            value = _rounded_coordinate(value, coordinate_bucket)
        normalized_args[key_text] = value

    canonical_name = ACTION_ALIASES.get(name, name)
    try:
        canonical_args = json.dumps(
            normalized_args,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=repr,
        )
    except Exception:
        canonical_args = repr(normalized_args)
    return f"{canonical_name}:{canonical_args}"


class RepeatedActionGuard:
    """Stop after a small number of consecutive attempts at the same target."""

    def __init__(self, repeat_limit: int = 3, coordinate_bucket: int = 25) -> None:
        if repeat_limit < 2:
            raise ValueError("repeat_limit must be at least 2")
        self.repeat_limit = repeat_limit
        self.coordinate_bucket = coordinate_bucket
        self._last_signature: str | None = None
        self._streak = 0
        self._last_action_name: str | None = None

    @property
    def streak(self) -> int:
        return self._streak

    def reset(self) -> None:
        self._last_signature = None
        self._streak = 0
        self._last_action_name = None

    def observe(self, function_calls: Sequence[Any]) -> str | None:
        """Return a stop reason when the primary action repeats too often.

        The first non-observation action in each model response is used as the
        primary action. The check is performed before dispatch, so the action
        that trips the guard is never executed.
        """
        signature = None
        action_name = None
        for function_call in function_calls:
            candidate = action_signature(function_call, self.coordinate_bucket)
            if candidate is not None:
                signature = candidate
                action_name = str(getattr(function_call, "name", "action") or "action")
                break

        if signature is None:
            # Do not count observers/waits as a repeated attempt, but do not let
            # inserting a wait/screenshot between clicks evade loop detection.
            return None

        if signature == self._last_signature:
            self._streak += 1
        else:
            self._last_signature = signature
            self._streak = 1
        self._last_action_name = action_name

        if self._streak >= self.repeat_limit:
            return (
                f"Stopped before executing '{action_name}': the same action target "
                f"was attempted {self._streak} consecutive times without a change "
                "in approach. Inspect the current screen and try a different action."
            )
        return None
