"""JARVIS-native YAML workflow schema and validation helpers.

The local model is not assumed to have screenshot or accessibility-tree input.
Workflows therefore describe bounded actions and optional checks against facts the
computer adapter can expose, such as the current URL. Unsupported expectations
are rejected instead of silently ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Required and optional argument names for the action executor. Keep this in sync
# with WorkflowExecutor._run_step; registry and trace compilation both use it.
ACTION_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    "click": ({"x", "y"}, {"x", "y"}),
    "double_click": ({"x", "y"}, {"x", "y"}),
    "triple_click": ({"x", "y"}, {"x", "y"}),
    "middle_click": ({"x", "y"}, {"x", "y"}),
    "right_click": ({"x", "y"}, {"x", "y"}),
    "mouse_down": ({"x", "y"}, {"x", "y"}),
    "mouse_up": ({"x", "y"}, {"x", "y"}),
    "move": ({"x", "y"}, {"x", "y"}),
    "type": ({"text"}, {"text", "press_enter"}),
    "type_text": ({"text"}, {"text", "press_enter"}),
    "type_text_at": ({"x", "y", "text"}, {"x", "y", "text", "press_enter", "clear_before_typing"}),
    "hotkey": ({"keys"}, {"keys"}),
    "key_combination": ({"keys"}, {"keys"}),
    "press_key": ({"key"}, {"key"}),
    "key_down": ({"key"}, {"key"}),
    "key_up": ({"key"}, {"key"}),
    "scroll": ({"direction"}, {"x", "y", "direction", "magnitude"}),
    "scroll_document": ({"direction"}, {"direction"}),
    "wait": (set(), {"seconds"}),
    "wait_5_seconds": (set(), set()),
    "go_back": (set(), set()),
    "go_forward": (set(), set()),
    "navigate": ({"url"}, {"url"}),
    "open_web_browser": (set(), set()),
    "search": (set(), set()),
    "take_screenshot": (set(), set()),
    "drag_and_drop": ({"x", "y", "destination_x", "destination_y"}, {"x", "y", "destination_x", "destination_y"}),
}

SUPPORTED_EXPECTATIONS = {"url_contains", "url_equals", "url_startswith"}
MAX_WORKFLOW_STEPS = 200
MAX_WORKFLOW_RUNTIME_SECONDS = 300
DEFAULT_WORKFLOW_RUNTIME_SECONDS = 120


@dataclass(slots=True)
class WorkflowStep:
    action: str
    args: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] | None = None
    recovery: list[dict[str, Any]] = field(default_factory=list)
    intent: str = ""


@dataclass(slots=True)
class Workflow:
    name: str
    description: str = ""
    environment: str = "desktop"
    platform: str = "any"
    tags: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    steps: list[WorkflowStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    path: str | None = None

    def searchable_text(self) -> str:
        return " ".join([self.name, self.description, *self.tags]).lower()

    @property
    def status(self) -> str:
        return str(self.metadata.get("status", "")).strip().lower()

    @property
    def is_runnable(self) -> bool:
        """Whether this workflow may be matched for automatic execution.

        Hand-authored workflows outside learned/adapted directories remain runnable
        unless explicitly marked as drafts. Automatically generated/edited workflows
        require an explicit approved status.
        """
        blocked_statuses = {"unvalidated", "candidate", "draft", "rejected", "disabled"}
        approved_statuses = {"approved", "validated", "active", "ready"}
        if self.status in blocked_statuses:
            return False
        if self.status in approved_statuses:
            return True
        if self.path:
            path_parts = {part.lower() for part in Path(self.path).parts}
            if "learned" in path_parts or "adapted" in path_parts:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "environment": self.environment,
            "platform": self.platform,
            "tags": list(self.tags),
            "parameters": self.parameters,
            "metadata": self.metadata,
            "steps": [
                {
                    "action": s.action,
                    "args": s.args,
                    **({"intent": s.intent} if s.intent else {}),
                    **({"expect": s.expect} if s.expect else {}),
                    **({"recovery": s.recovery} if s.recovery else {}),
                }
                for s in self.steps
            ],
        }


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _expectation_kind(expect: dict[str, Any]) -> str | None:
    kind = expect.get("type")
    if isinstance(kind, str):
        return kind.strip().lower()
    # Permit concise YAML such as expect: {url_contains: "example.com"}.
    present = [key for key in SUPPORTED_EXPECTATIONS if key in expect]
    return present[0] if len(present) == 1 else None


def validate_expectation(expect: Any, *, path: str = "expect") -> list[str]:
    if expect is None:
        return []
    if not isinstance(expect, dict):
        return [f"{path} must be a mapping"]
    kind = _expectation_kind(expect)
    if kind not in SUPPORTED_EXPECTATIONS:
        return [f"{path}.type must be one of {', '.join(sorted(SUPPORTED_EXPECTATIONS))}; visual/accessibility checks are not supported by the current executor"]
    value = expect.get("value", expect.get(kind))
    if not _has_value(value):
        return [f"{path}.{kind if kind in expect else 'value'} must be a non-empty value"]
    timeout = expect.get("timeout_seconds", expect.get("timeout", 3.0))
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError):
        return [f"{path}.timeout_seconds must be a number"]
    if not 0 <= timeout_value <= 30:
        return [f"{path}.timeout_seconds must be between 0 and 30"]
    return []


def validate_step(step: WorkflowStep, *, path: str = "step") -> list[str]:
    errors: list[str] = []
    action = step.action.strip().lower()
    if action not in ACTION_FIELDS:
        return [f"{path}.action '{step.action}' is unsupported"]
    if not isinstance(step.args, dict):
        return [f"{path}.args must be a mapping"]
    required, allowed = ACTION_FIELDS[action]
    missing = sorted(key for key in required if key not in step.args or not _has_value(step.args[key]))
    if missing:
        errors.append(f"{path} ({action}) missing required argument(s): {', '.join(missing)}")
    extra = sorted(set(step.args) - allowed)
    if extra:
        errors.append(f"{path} ({action}) has unsupported argument(s): {', '.join(extra)}")
    if action in {"click", "double_click", "triple_click", "middle_click", "right_click", "mouse_down", "mouse_up", "move", "type_text_at", "drag_and_drop", "scroll"}:
        for key in allowed & set(step.args):
            value = step.args[key]
            if isinstance(value, bool):
                errors.append(f"{path}.args.{key} must be numeric, not boolean")
            elif isinstance(value, str) and ("{{" in value and "}}" in value):
                # Parameter placeholders are validated at execution time.
                continue
            else:
                try:
                    float(value)
                except (TypeError, ValueError):
                    errors.append(f"{path}.args.{key} must be numeric")
    if action in {"type", "type_text", "type_text_at"}:
        if "press_enter" in step.args and not isinstance(step.args["press_enter"], bool):
            errors.append(f"{path}.args.press_enter must be boolean")
    if action in {"hotkey", "key_combination"} and _has_value(step.args.get("keys")):
        keys = step.args["keys"]
        if not isinstance(keys, (str, list)) or (isinstance(keys, str) and not keys.strip()) or (isinstance(keys, list) and (not keys or not all(isinstance(k, str) and k.strip() for k in keys))):
            errors.append(f"{path}.args.keys must be a key string or a non-empty list of non-empty strings")
    if action == "scroll" and step.args.get("direction", "down") not in {"up", "down", "left", "right"}:
        errors.append(f"{path}.args.direction must be up, down, left, or right")
    if action == "scroll_document" and step.args.get("direction") not in {"up", "down", "left", "right"}:
        errors.append(f"{path}.args.direction must be up, down, left, or right")
    if action == "wait" and "seconds" in step.args:
        try:
            seconds = float(step.args["seconds"])
            if not 0 <= seconds <= 30:
                errors.append(f"{path}.args.seconds must be between 0 and 30")
        except (TypeError, ValueError):
            errors.append(f"{path}.args.seconds must be numeric")
    if action == "navigate" and _has_value(step.args.get("url")):
        if isinstance(step.args["url"], str) and "{{" not in step.args["url"] and not step.args["url"].startswith(("http://", "https://")):
            errors.append(f"{path}.args.url must start with http:// or https://")
    errors.extend(validate_expectation(step.expect, path=f"{path}.expect"))
    if not isinstance(step.recovery, list):
        errors.append(f"{path}.recovery must be a list")
    else:
        for i, recovery in enumerate(step.recovery):
            if not isinstance(recovery, dict) or not isinstance(recovery.get("action"), str):
                errors.append(f"{path}.recovery[{i}] must contain an action")
                continue
            recovery_step = WorkflowStep(
                action=recovery["action"],
                args=recovery.get("args") or {},
                expect=recovery.get("expect"),
            )
            errors.extend(validate_step(recovery_step, path=f"{path}.recovery[{i}]"))
    return errors


def validate_workflow(workflow: Workflow) -> list[str]:
    errors: list[str] = []
    if not workflow.name.strip():
        errors.append("workflow name is required")
    if not workflow.steps:
        errors.append("workflow must contain at least one step")
    if len(workflow.steps) > MAX_WORKFLOW_STEPS:
        errors.append(f"workflow cannot contain more than {MAX_WORKFLOW_STEPS} steps")
    if not isinstance(workflow.parameters, dict):
        errors.append("workflow parameters must be a mapping")
    if not isinstance(workflow.metadata, dict):
        errors.append("workflow metadata must be a mapping")
    if isinstance(workflow.metadata, dict):
        declared = workflow.metadata.get("validation_errors")
        if isinstance(declared, list):
            errors.extend(f"compiler: {item}" for item in declared if isinstance(item, str))
        configured_timeout = workflow.metadata.get("timeout_seconds", DEFAULT_WORKFLOW_RUNTIME_SECONDS)
        try:
            timeout_value = float(configured_timeout)
            if not 1 <= timeout_value <= MAX_WORKFLOW_RUNTIME_SECONDS:
                errors.append(f"metadata.timeout_seconds must be between 1 and {MAX_WORKFLOW_RUNTIME_SECONDS}")
        except (TypeError, ValueError):
            errors.append("metadata.timeout_seconds must be numeric")
    for index, step in enumerate(workflow.steps, start=1):
        errors.extend(validate_step(step, path=f"steps[{index}]"))
    return errors
