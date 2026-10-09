"""GNOME Shell bridge for the Deskwright extension.

The extension remains the compositor-side authority. This module only speaks
its D-Bus contract; it does not duplicate screenshot/window/input logic.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import uuid
from typing import Any


BUS_NAME = "com.zeticle.deskwright"
OBJECT_PATH = "/com/zeticle/deskwright"
INTERFACE = "com.zeticle.deskwright"


class GnomeBridgeError(RuntimeError):
    pass


class GnomeShellBridge:
    def __init__(self, timeout: float = 2.0) -> None:
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return shutil.which("gdbus") is not None

    def call(self, method: str, *args: str) -> str:
        if not self.available:
            raise GnomeBridgeError("gdbus is not available on this system")
        cmd = [
            "gdbus",
            "call",
            "--session",
            "--dest",
            BUS_NAME,
            "--object-path",
            OBJECT_PATH,
            "--method",
            f"{INTERFACE}.{method}",
            *[str(a) for a in args],
        ]
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise GnomeBridgeError(proc.stderr.strip() or proc.stdout.strip() or f"D-Bus call failed: {method}")
        return proc.stdout.strip()

    def _tuple_value(self, raw: str) -> Any:
        # gdbus prints simple return values in a Python-literal-like form:
        # ('value',), (true,), (['a', 'b'],), etc.
        normalized = raw.strip()
        if normalized == "(true,)":
            return True
        if normalized == "(false,)":
            return False
        try:
            parsed = ast.literal_eval(raw)
        except Exception as exc:
            raise GnomeBridgeError(f"Could not parse D-Bus response: {raw!r}") from exc
        if isinstance(parsed, tuple) and len(parsed) == 1:
            return parsed[0]
        return parsed

    def ping(self) -> dict[str, Any]:
        result = self._tuple_value(self.call("Ping"))
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return {"build": result}
        return dict(result) if isinstance(result, dict) else {}

    def set_indicator(self, message: str, ttl_seconds: float = 600.0) -> bool:
        try:
            return bool(self._tuple_value(self.call("SetIndicator", message, str(ttl_seconds))))
        except GnomeBridgeError:
            return False

    def close_assistant(self) -> bool:
        try:
            return bool(self._tuple_value(self.call("CloseAssistant")))
        except GnomeBridgeError:
            return False

    def set_assistant_state(self, state: str, detail: str = "") -> bool:
        try:
            return bool(self._tuple_value(self.call("SetAssistantState", state, detail)))
        except GnomeBridgeError:
            return False

    def append_conversation_message(self, role: str, text: str) -> bool:
        try:
            return bool(self._tuple_value(self.call("AppendConversationMessage", role, text)))
        except GnomeBridgeError:
            return False

    def set_activity(self, text: str) -> bool:
        try:
            return bool(self._tuple_value(self.call("SetActivity", text)))
        except GnomeBridgeError:
            return False

    def show_confirmation(self, reason: str, request_id: str | None = None) -> bool:
        """Show a confirmation carrying an ID used to correlate its decision."""
        confirmation_id = str(request_id or uuid.uuid4().hex)
        try:
            return bool(self._tuple_value(self.call("ShowConfirmation", reason, confirmation_id)))
        except GnomeBridgeError:
            return False

    def clear_confirmation(self) -> bool:
        try:
            return bool(self._tuple_value(self.call("ClearConfirmation")))
        except GnomeBridgeError:
            return False

    def show_workflow_approval(
        self, name: str, description: str, steps_summary: str, step_count: int
    ) -> bool:
        try:
            return bool(self._tuple_value(self.call(
                "ShowWorkflowApproval", name, description, steps_summary, int(step_count)
            )))
        except GnomeBridgeError:
            return False

    def clear_workflow_approval(self) -> bool:
        try:
            return bool(self._tuple_value(self.call("ClearWorkflowApproval")))
        except GnomeBridgeError:
            return False

    def get_pending_workflow_approval_responses(self) -> list[str]:
        try:
            value = self._tuple_value(self.call("GetPendingWorkflowApprovalResponses"))
            return [str(item) for item in value] if isinstance(value, (list, tuple)) else []
        except GnomeBridgeError:
            return []

    def wait_for_workflow_approval(self, workflow: Any, timeout: float = 120.0) -> bool:
        """Show a dedicated GNOME approval card and wait for its explicit decision.

        Workflow approval has its own D-Bus response queue; a confirmation for a
        sensitive action can never accidentally approve a workflow.
        """
        import time

        name = str(getattr(workflow, "name", "") or "").strip()
        if not name:
            return False
        steps = list(getattr(workflow, "steps", []) or [])
        visible_steps = []
        for index, step in enumerate(steps[:6], start=1):
            intent = str(getattr(step, "intent", "") or getattr(step, "action", "step"))
            visible_steps.append(f"{index}. {intent[:72]}")
        if len(steps) > len(visible_steps):
            visible_steps.append(f"+ {len(steps) - len(visible_steps)} more steps")
        summary = "  •  ".join(visible_steps) or "No steps recorded"
        metadata = getattr(workflow, "metadata", {}) or {}
        warnings = metadata.get("trace_warnings", []) if isinstance(metadata, dict) else []
        if isinstance(warnings, list) and warnings:
            warning_text = "; ".join(str(item) for item in warnings[:2])
            suffix = f"  |  Compiler warnings: {warning_text[:130]}"
            summary = summary[: max(0, 350 - len(suffix))] + suffix
        description = str(getattr(workflow, "description", "") or "").strip()[:240]

        # Drain stale decisions before displaying a new, named approval request.
        self.get_pending_workflow_approval_responses()
        self.set_assistant_state("waiting_confirmation", f"Review workflow: {name}")
        if not self.show_workflow_approval(name, description, summary, len(steps)):
            self.set_assistant_state("thinking", "Workflow approval UI unavailable")
            return False

        deadline = time.monotonic() + max(1.0, float(timeout))
        try:
            while time.monotonic() < deadline:
                if self.halt_active():
                    return False
                for response in self.get_pending_workflow_approval_responses():
                    try:
                        parsed = json.loads(response)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(parsed, dict) or parsed.get("workflow") != name:
                        continue
                    decision = str(parsed.get("decision", "")).strip().lower()
                    if decision == "approve":
                        return True
                    if decision == "decline":
                        return False
                time.sleep(0.20)
            return False
        finally:
            self.clear_workflow_approval()
            self.set_assistant_state("thinking", "Workflow decision received")

    def get_pending_control_events(self) -> list[str]:
        try:
            value = self._tuple_value(self.call("GetPendingControlEvents"))
            return [str(item) for item in value] if isinstance(value, (list, tuple)) else []
        except GnomeBridgeError:
            return []

    def get_pending_confirmation_responses(self) -> list[str]:
        try:
            value = self._tuple_value(self.call("GetPendingConfirmationResponses"))
            return [str(item) for item in value] if isinstance(value, (list, tuple)) else []
        except GnomeBridgeError:
            return []

    def clear_indicator(self) -> bool:
        try:
            return bool(self._tuple_value(self.call("ClearIndicator")))
        except GnomeBridgeError:
            return False

    def activate_assistant(self) -> bool:
        try:
            return bool(self._tuple_value(self.call("ActivateAssistant")))
        except GnomeBridgeError:
            return False

    def indicator_active(self) -> bool:
        return bool(self._tuple_value(self.call("IndicatorActive")))

    def toggle_assistant(self) -> bool:
        if self.indicator_active():
            return self.close_assistant()
        return self.activate_assistant()

    def request_voice_capture(self) -> bool:
        try:
            return bool(self._tuple_value(self.call("RequestVoiceCapture")))
        except GnomeBridgeError:
            return False

    def get_pending_instructions(self) -> list[str]:
        try:
            value = self._tuple_value(self.call("GetPendingInstructions"))
            return [str(item) for item in value] if isinstance(value, (list, tuple)) else []
        except GnomeBridgeError:
            return []

    def submit_instruction(self, text: str) -> bool:
        try:
            return bool(self._tuple_value(self.call("SubmitInstruction", text)))
        except GnomeBridgeError:
            return False

    def halt_active(self) -> bool:
        try:
            return bool(self._tuple_value(self.call("HaltActive")))
        except GnomeBridgeError:
            return False

    def halt(self) -> None:
        self.call("Halt")

    def clear_halt(self) -> None:
        self.call("ClearHalt")

    def list_windows(self) -> list[dict[str, Any]]:
        raw = self._tuple_value(self.call("ListWindows"))
        if not isinstance(raw, str):
            return list(raw) if isinstance(raw, list) else []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def wait_for_confirmation(self, reason: str, timeout: float = 60.0) -> bool:
        """Show one confirmation and accept only an explicit response for its ID.

        Stale responses are drained before the prompt is shown and ignored unless
        their request_id matches this request. Ordinary overlay instructions are
        never interpreted as approval. Any UI/D-Bus error, timeout, halt, malformed
        response, or denial returns False (fail closed).
        """
        import time

        request_id = uuid.uuid4().hex
        # Remove any old prompt and clear responses left by a prior timeout/halt.
        self.clear_confirmation()
        self.get_pending_confirmation_responses()
        self.set_assistant_state("waiting_confirmation", reason)
        if not self.show_confirmation(reason, request_id):
            # The D-Bus call may have failed after the extension began creating the
            # dialog. Clean it up and drain any response before returning denied.
            self.clear_confirmation()
            self.get_pending_confirmation_responses()
            self.set_assistant_state("thinking", "Confirmation UI unavailable")
            return False

        deadline = time.monotonic() + max(1.0, float(timeout))
        try:
            while time.monotonic() < deadline:
                if self.halt_active():
                    return False
                for response in self.get_pending_confirmation_responses():
                    try:
                        parsed = json.loads(response)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(parsed, dict):
                        continue
                    if parsed.get("request_id") != request_id:
                        continue
                    decision = str(parsed.get("decision", "")).strip().lower()
                    if decision == "allow":
                        return True
                    if decision == "deny":
                        return False
                time.sleep(0.20)
            return False
        finally:
            # Clear the prompt and drain any late response so it cannot authorize
            # a later request. Request IDs also protect against any remaining stale
            # event that races with this cleanup.
            self.clear_confirmation()
            self.get_pending_confirmation_responses()
            self.set_assistant_state("thinking", "Confirmation decision received")
