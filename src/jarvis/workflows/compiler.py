from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .schema import Workflow, WorkflowStep, validate_step


class WorkflowCompilationError(ValueError):
    pass


def _slug(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return (s[:70] or "workflow")


# Positional call arguments are captured by TracingComputer for actions invoked
# positionally by the Google Computer Use runtime. Map them to stable YAML fields.
_POSITIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "click_at": ("x", "y"),
    "double_click_at": ("x", "y"),
    "triple_click_at": ("x", "y"),
    "middle_click_at": ("x", "y"),
    "right_click_at": ("x", "y"),
    "mouse_down": ("x", "y"),
    "mouse_up": ("x", "y"),
    "hover_at": ("x", "y"),
    "type_text": ("text", "press_enter"),
    "type_text_at": ("x", "y", "text", "press_enter", "clear_before_typing"),
    "navigate": ("url",),
    "press_key": ("key",),
    "key_down": ("key",),
    "key_up": ("key",),
    "key_combination": ("keys",),
    "wait": ("seconds",),
    "scroll_at": ("x", "y", "direction", "magnitude"),
    "scroll_document": ("direction",),
    "drag_and_drop": ("x", "y", "destination_x", "destination_y"),
}

_COORDINATE_ACTIONS = {
    "click_at", "double_click_at", "triple_click_at", "middle_click_at",
    "right_click_at", "mouse_down", "mouse_up", "hover_at", "type_text_at",
    "scroll_at", "drag_and_drop",
}

_ACTION_MAP = {
    "click_at": "click",
    "double_click_at": "double_click",
    "triple_click_at": "triple_click",
    "middle_click_at": "middle_click",
    "right_click_at": "right_click",
    "hover_at": "move",
    "scroll_at": "scroll",
}


class WorkflowCompiler:
    """Compile Gemini computer-use traces to safe, reviewable workflow candidates."""

    def compile_from_trace(
        self,
        query: str,
        actions: list[dict[str, Any]],
        screen_size: tuple[int, int],
        *,
        environment: str,
        platform: str = "any",
    ) -> Workflow:
        width, height = screen_size
        steps: list[WorkflowStep] = []
        issues: list[str] = []

        def norm(value: Any, axis: str) -> int:
            maximum = width if axis == "x" else height
            if maximum <= 0:
                raise ValueError(f"cannot normalize {axis} coordinate without screen dimensions")
            number = float(value)
            return max(0, min(1000, round(number / maximum * 1000)))

        for index, item in enumerate(actions, start=1):
            if not isinstance(item, dict):
                issues.append(f"trace action {index} is not a mapping")
                continue
            method = item.get("method")
            if not isinstance(method, str) or not method:
                issues.append(f"trace action {index} has no method name")
                continue

            kwargs = item.get("kwargs") or {}
            positional = item.get("args") or []
            if not isinstance(kwargs, dict) or not isinstance(positional, (list, tuple)):
                issues.append(f"trace action {index} ({method}) has malformed arguments")
                continue

            raw_args: dict[str, Any] = {}
            for position, field_name in enumerate(_POSITIONAL_FIELDS.get(method, ())):
                if position < len(positional):
                    raw_args[field_name] = positional[position]
            # Explicit keyword arguments win; in real Python calls the same
            # argument cannot validly be supplied both ways, so this is mostly a
            # robust normalization for traces from different runtime versions.
            raw_args.update(kwargs)

            action = _ACTION_MAP.get(method, method)
            if action not in {
                "click", "double_click", "triple_click", "middle_click", "right_click",
                "mouse_down", "mouse_up", "move", "type", "type_text", "type_text_at",
                "hotkey", "key_combination", "press_key", "key_down", "key_up", "scroll",
                "scroll_document", "wait", "wait_5_seconds", "go_back", "go_forward",
                "navigate", "open_web_browser", "search", "take_screenshot", "drag_and_drop",
            }:
                issues.append(f"trace action {index} ({method}) is unsupported and was skipped")
                continue

            if method in _COORDINATE_ACTIONS or method == "click":
                coordinate_fields = {
                    "click": ("x", "y"),
                    "click_at": ("x", "y"), "double_click_at": ("x", "y"),
                    "triple_click_at": ("x", "y"), "middle_click_at": ("x", "y"),
                    "right_click_at": ("x", "y"), "mouse_down": ("x", "y"),
                    "mouse_up": ("x", "y"), "hover_at": ("x", "y"),
                    "type_text_at": ("x", "y"), "scroll_at": ("x", "y"),
                    "drag_and_drop": ("x", "y", "destination_x", "destination_y"),
                }[method]
                try:
                    for field_name in coordinate_fields:
                        if field_name not in raw_args:
                            raise ValueError(f"missing {field_name}")
                        axis = "x" if field_name in {"x", "destination_x"} else "y"
                        raw_args[field_name] = norm(raw_args[field_name], axis)
                except (TypeError, ValueError) as exc:
                    issues.append(f"trace action {index} ({method}) is incomplete: {exc}; skipped")
                    continue

            # type_text_at and scroll_at are converted into canonical executor actions.
            if action == "type_text_at":
                action = "type_text_at"
            elif action == "scroll":
                raw_args.setdefault("direction", "down")
                raw_args.setdefault("magnitude", 800)

            # Keep only action arguments the executor knows how to interpret.
            # The validator reports absent required keys rather than inventing them.
            step = WorkflowStep(action=action, args=raw_args, intent=f"Recorded {method}")
            step_errors = validate_step(step, path=f"trace[{index}]")
            if step_errors:
                issues.extend(step_errors)
                continue
            steps.append(step)

        name = _slug(query)
        return Workflow(
            name=name,
            description=query.strip(),
            environment=environment,
            platform=platform,
            tags=[x for x in re.findall(r"[A-Za-z0-9]+", query.lower()) if len(x) > 2][:12],
            steps=steps,
            metadata={
                "source": "gemini_computer_use",
                "status": "unvalidated",
                "compiled_at": datetime.now(timezone.utc).isoformat(),
                "source_query": query,
                "trace_warnings": issues,
                "execution_note": "Trace-derived workflow candidate; review skipped/incomplete actions and verify before approval.",
            },
        )
