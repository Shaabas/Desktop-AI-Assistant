from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ..computer_use.computers import Computer

from ..agent.security import SecurityGuard
from .schema import DEFAULT_WORKFLOW_RUNTIME_SECONDS, Workflow, WorkflowStep, validate_workflow


class WorkflowExecutionError(RuntimeError):
    """A workflow is invalid or an action/postcondition failed."""


class WorkflowCancelled(WorkflowExecutionError):
    pass


class WorkflowAuthorizationError(WorkflowExecutionError):
    """A required confirmation was denied, unavailable, or failed."""


@dataclass(slots=True)
class StepExecution:
    index: int
    action: str
    outcome: str
    detail: str = ""


@dataclass(slots=True)
class WorkflowExecutionReport:
    workflow: str
    outcome: str
    states: list[Any] = field(default_factory=list)
    steps: list[StepExecution] = field(default_factory=list)


class WorkflowExecutor:
    """Execute only validated workflows and report what was actually verified.

    This executor does not pass screenshots to the local LLM and does not assume
    that Qwen has visual or accessibility access. Expectations are limited to
    observable state exposed by the Computer adapter (currently URL predicates).
    """

    def __init__(
        self,
        security: SecurityGuard,
        confirm: Callable[[str], bool],
        *,
        step_controller: Any | None = None,
    ) -> None:
        self.security = security
        self.confirm = confirm
        self.step_controller = step_controller

    @staticmethod
    def _substitute(value: Any, parameters: dict[str, Any]) -> Any:
        if isinstance(value, str):
            def repl(match: re.Match[str]) -> str:
                key = match.group(1).strip()
                if key.startswith("param."):
                    key = key[6:]
                return str(parameters.get(key, match.group(0)))
            return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", repl, value)
        if isinstance(value, list):
            return [WorkflowExecutor._substitute(v, parameters) for v in value]
        if isinstance(value, dict):
            return {k: WorkflowExecutor._substitute(v, parameters) for k, v in value.items()}
        return value

    @staticmethod
    def _find_placeholders(value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, str):
            found.update(match.group(1).strip().removeprefix("param.") for match in re.finditer(r"\{\{\s*([^}]+?)\s*\}\}", value))
        elif isinstance(value, dict):
            for item in value.values():
                found.update(WorkflowExecutor._find_placeholders(item))
        elif isinstance(value, list):
            for item in value:
                found.update(WorkflowExecutor._find_placeholders(item))
        return found

    @staticmethod
    def _parameters_for(workflow: Workflow, supplied: dict[str, Any] | None) -> dict[str, Any]:
        parameters: dict[str, Any] = {}
        for name, definition in workflow.parameters.items():
            if isinstance(definition, dict):
                if "default" in definition:
                    parameters[name] = definition["default"]
                elif definition.get("required") is True:
                    parameters[name] = None
            elif definition is not None:
                # Shorthand: `parameters: {query: "default query"}`.
                parameters[name] = definition
        parameters.update(supplied or {})
        required_missing = [
            name for name, definition in workflow.parameters.items()
            if isinstance(definition, dict) and definition.get("required") is True
            and (name not in parameters or parameters[name] is None or str(parameters[name]).strip() == "")
        ]
        if required_missing:
            raise WorkflowExecutionError("Missing required workflow parameter(s): " + ", ".join(sorted(required_missing)))
        all_template_values = [
            {"args": step.args, "expect": step.expect, "recovery": step.recovery}
            for step in workflow.steps
        ]
        unresolved = set()
        for value in all_template_values:
            unresolved.update(WorkflowExecutor._find_placeholders(value))
        missing = sorted(name for name in unresolved if name not in parameters or parameters[name] is None)
        if missing:
            raise WorkflowExecutionError("Missing value(s) for workflow placeholder(s): " + ", ".join(missing))
        return parameters

    @staticmethod
    def _px(computer: Computer, value: Any, axis: str) -> int:
        size = computer.screen_size()
        max_value = size[0] if axis == "x" else size[1]
        if max_value <= 0:
            raise WorkflowExecutionError("Computer adapter returned invalid screen dimensions")
        try:
            f = float(value)
        except (TypeError, ValueError) as exc:
            raise WorkflowExecutionError(f"Coordinate {axis} must be numeric, got {value!r}") from exc
        if 0 <= f <= 1000:
            return max(0, min(max_value - 1, int(f / 1000.0 * max_value)))
        return max(0, min(max_value - 1, int(f)))

    def _run_step(self, computer: Computer, step: WorkflowStep, params: dict[str, Any]) -> Any:
        args = self._substitute(step.args, params)
        if not isinstance(args, dict):
            raise WorkflowExecutionError("Step arguments must resolve to a mapping")
        action = step.action.lower().strip()

        if action in {"type", "type_text", "type_text_at"}:
            text = str(args["text"])
            risky, reason = self.security.evaluate_text_input(text)
            if risky:
                try:
                    approved = self.confirm(reason)
                except Exception as exc:
                    raise WorkflowAuthorizationError(
                        f"Confirmation failed; blocked action: {reason}"
                    ) from exc
                if approved is not True:
                    raise WorkflowAuthorizationError(
                        f"Confirmation denied or unavailable; blocked action: {reason}"
                    )

        if action in {"click", "double_click", "triple_click", "middle_click", "right_click", "mouse_down", "mouse_up", "move"}:
            x = self._px(computer, args["x"], "x")
            y = self._px(computer, args["y"], "y")
            mapping = {
                "click": "click_at",
                "double_click": "double_click_at",
                "triple_click": "triple_click_at",
                "middle_click": "middle_click_at",
                "right_click": "right_click_at",
                "mouse_down": "mouse_down",
                "mouse_up": "mouse_up",
                "move": "hover_at",
            }
            return getattr(computer, mapping[action])(x=x, y=y)

        if action in {"type", "type_text"}:
            return computer.type_text(text=str(args["text"]), press_enter=bool(args.get("press_enter", False)))
        if action == "type_text_at":
            return computer.type_text_at(
                x=self._px(computer, args["x"], "x"),
                y=self._px(computer, args["y"], "y"),
                text=str(args["text"]),
                press_enter=bool(args.get("press_enter", False)),
                clear_before_typing=bool(args.get("clear_before_typing", True)),
            )
        if action in {"hotkey", "key_combination"}:
            keys = args["keys"]
            if isinstance(keys, str):
                keys = [x.strip() for x in keys.split("+") if x.strip()]
            return computer.key_combination(list(keys))
        if action == "press_key":
            return computer.press_key(str(args["key"]))
        if action == "key_down":
            return computer.key_down(str(args["key"]))
        if action == "key_up":
            return computer.key_up(str(args["key"]))
        if action == "scroll":
            return computer.scroll_at(
                x=self._px(computer, args.get("x", 500), "x"),
                y=self._px(computer, args.get("y", 500), "y"),
                direction=str(args.get("direction", "down")),
                magnitude=int(args.get("magnitude", 800)),
            )
        if action == "scroll_document":
            return computer.scroll_document(direction=str(args["direction"]))
        if action == "wait":
            return computer.wait(int(args.get("seconds", 1)))
        if action == "wait_5_seconds":
            return computer.wait_5_seconds()
        if action == "go_back":
            return computer.go_back()
        if action == "go_forward":
            return computer.go_forward()
        if action in {"navigate", "open_web_browser"}:
            if action == "open_web_browser":
                return computer.open_web_browser()
            return computer.navigate(str(args["url"]))
        if action == "search":
            return computer.search()
        if action == "drag_and_drop":
            return computer.drag_and_drop(
                x=self._px(computer, args["x"], "x"),
                y=self._px(computer, args["y"], "y"),
                destination_x=self._px(computer, args["destination_x"], "x"),
                destination_y=self._px(computer, args["destination_y"], "y"),
            )
        if action == "take_screenshot":
            return computer.take_screenshot()
        raise WorkflowExecutionError(f"Unsupported workflow action: {action}")

    @staticmethod
    def _expectation_parts(expect: dict[str, Any]) -> tuple[str, str, float]:
        kind = expect.get("type")
        if not isinstance(kind, str):
            kinds = ("url_contains", "url_equals", "url_startswith")
            found = [candidate for candidate in kinds if candidate in expect]
            kind = found[0] if len(found) == 1 else ""
        value = expect.get("value", expect.get(kind, ""))
        timeout = float(expect.get("timeout_seconds", expect.get("timeout", 3.0)))
        return kind.lower(), str(value), timeout

    @staticmethod
    def _state_url(state: Any) -> str:
        url = getattr(state, "url", "") if state is not None else ""
        return url if isinstance(url, str) else ""

    @staticmethod
    def _matches_expectation(kind: str, expected: str, url: str) -> bool:
        if kind == "url_contains":
            return expected.lower() in url.lower()
        if kind == "url_equals":
            return expected.rstrip("/").lower() == url.rstrip("/").lower()
        if kind == "url_startswith":
            return url.lower().startswith(expected.lower())
        return False

    def _check_expectation(
        self,
        computer: Computer,
        expect: dict[str, Any],
        parameters: dict[str, Any],
        last_state: Any,
        cancel_event: threading.Event | None,
        workflow_deadline: float,
    ) -> bool:
        resolved = self._substitute(expect, parameters)
        kind, expected, timeout = self._expectation_parts(resolved)
        deadline = time.monotonic() + timeout
        while True:
            if cancel_event and cancel_event.is_set():
                raise WorkflowCancelled("Workflow cancelled by user")
            if time.monotonic() >= workflow_deadline:
                raise WorkflowExecutionError("Workflow runtime deadline exceeded")
            url = self._state_url(last_state)
            if self._matches_expectation(kind, expected, url):
                return True
            # Only query current_state when a step has a URL postcondition. The
            # screenshot field is ignored and never handed to the local LLM.
            current_state_fn = getattr(computer, "current_state", None)
            if callable(current_state_fn):
                try:
                    current_state = current_state_fn()
                except Exception:
                    current_state = None
                url = self._state_url(current_state) or url
                if self._matches_expectation(kind, expected, url):
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def run_with_report(
        self,
        workflow: Workflow,
        computer: Computer,
        parameters: dict[str, Any] | None = None,
        cancel_event: threading.Event | None = None,
        *,
        task_query: str = "",
        use_local_llm: bool = True,
    ) -> WorkflowExecutionReport:
        if not workflow.is_runnable:
            raise WorkflowExecutionError(f"Workflow '{workflow.name}' is not approved for automatic execution (status={workflow.status or 'unapproved'})")
        errors = validate_workflow(workflow)
        if errors:
            raise WorkflowExecutionError("Invalid workflow: " + "; ".join(errors))
        params = self._parameters_for(workflow, parameters)
        max_runtime = float(workflow.metadata.get("timeout_seconds", DEFAULT_WORKFLOW_RUNTIME_SECONDS))
        workflow_deadline = time.monotonic() + max_runtime
        report = WorkflowExecutionReport(workflow=workflow.name, outcome="completed_unverified")
        all_verified = True
        controller = self.step_controller if use_local_llm else None
        previous_steps: list[dict[str, Any]] = []
        latest_url = ""

        for index, step in enumerate(workflow.steps, start=1):
            if cancel_event and cancel_event.is_set():
                raise WorkflowCancelled("Workflow cancelled by user")
            if time.monotonic() >= workflow_deadline:
                raise WorkflowExecutionError(f"Workflow '{workflow.name}' exceeded its {max_runtime:g}s runtime limit")
            authorization_reason = ""
            if controller is not None:
                authorized, authorization_reason = controller.authorize_next_step(
                    workflow,
                    step_index=index,
                    total_steps=len(workflow.steps),
                    task_query=task_query,
                    previous_steps=previous_steps,
                    latest_url=latest_url,
                )
                if not authorized:
                    raise WorkflowExecutionError(
                        f"Local LLM stopped before step {index} ({step.action}): {authorization_reason}"
                    )
                # Model inference may be slow on a small local model. Do not run
                # an action if its authorization call consumed the workflow budget.
                if cancel_event and cancel_event.is_set():
                    raise WorkflowCancelled("Workflow cancelled by user")
                if time.monotonic() >= workflow_deadline:
                    raise WorkflowExecutionError("Workflow runtime deadline exceeded during local authorization")
            try:
                result = self._run_step(computer, step, params)
                report.states.append(result)
                if step.expect:
                    if not self._check_expectation(computer, step.expect, params, result, cancel_event, workflow_deadline):
                        raise WorkflowExecutionError(
                            f"postcondition not met after {step.action}: {step.expect}"
                        )
                    report.steps.append(StepExecution(index, step.action, "succeeded_verified", authorization_reason))
                else:
                    all_verified = False
                    detail = "No postcondition defined"
                    if authorization_reason:
                        detail += f"; local controller: {authorization_reason}"
                    report.steps.append(StepExecution(index, step.action, "completed_unverified", detail))
                latest_url = self._state_url(result) or latest_url
                step_report = report.steps[-1]
                previous_steps.append({
                    "step_index": index,
                    "action": step.action,
                    "outcome": step_report.outcome,
                    "detail": step_report.detail[:240],
                })
            except WorkflowCancelled:
                raise
            except WorkflowAuthorizationError:
                # Authorization failures are terminal. Do not run recovery actions
                # after the user denied a sensitive operation or the dialog failed.
                raise
            except Exception as exc:
                if time.monotonic() >= workflow_deadline:
                    raise WorkflowExecutionError(
                        f"Workflow runtime deadline exceeded while handling step {index}"
                    ) from exc
                recovered = False
                recovery_details: list[str] = []
                recovery_items = step.recovery
                if controller is not None and step.recovery:
                    selected_recovery = controller.choose_recovery(
                        workflow, step_index=index, error=str(exc), latest_url=latest_url
                    )
                    recovery_items = [step.recovery[selected_recovery]] if selected_recovery is not None and 0 <= selected_recovery < len(step.recovery) else []
                for recovery in recovery_items:
                    if cancel_event and cancel_event.is_set():
                        raise WorkflowCancelled("Workflow cancelled by user")
                    try:
                        recovery_step = WorkflowStep(
                            action=str(recovery["action"]),
                            args=dict(recovery.get("args") or {}),
                            expect=recovery.get("expect"),
                            intent=str(recovery.get("intent") or "recovery"),
                        )
                        recovery_result = self._run_step(computer, recovery_step, params)
                        report.states.append(recovery_result)
                        recovery_details.append(f"{recovery_step.action} executed")
                        if step.expect and self._check_expectation(computer, step.expect, params, recovery_result, cancel_event, workflow_deadline):
                            recovered = True
                            latest_url = self._state_url(recovery_result) or latest_url
                            break
                    except WorkflowCancelled:
                        raise
                    except Exception as recovery_exc:
                        recovery_details.append(f"{recovery.get('action', 'unknown')} failed: {recovery_exc}")
                if recovered:
                    report.steps.append(StepExecution(index, step.action, "succeeded_verified", "Postcondition reached by recovery"))
                    previous_steps.append({"step_index": index, "action": step.action, "outcome": "succeeded_verified", "detail": "Postcondition reached by recovery"})
                    continue
                detail = f"Step {index} ({step.action}) failed: {exc}"
                if recovery_details:
                    detail += "; recovery: " + ", ".join(recovery_details)
                raise WorkflowExecutionError(detail) from exc

        report.outcome = "succeeded_verified" if all_verified else "completed_unverified"
        return report

    def run(
        self,
        workflow: Workflow,
        computer: Computer,
        parameters: dict[str, Any] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[Any]:
        """Backward-compatible result API; use run_with_report for outcome details."""
        return self.run_with_report(workflow, computer, parameters, cancel_event).states
