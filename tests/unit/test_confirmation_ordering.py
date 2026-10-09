from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


import pytest

from src.jarvis.agent.security import SecurityGuard
from src.jarvis.computer_use.safety_gate import preflight_safety_confirmations
from src.jarvis.workflows.executor import WorkflowAuthorizationError, WorkflowExecutor
from src.jarvis.workflows.registry import WorkflowRegistry
from src.jarvis.workflows.secure import SecureComputerProxy


class FakeComputer:
    def __init__(self):
        self.calls = []

    def screen_size(self):
        return (1000, 500)

    def click_at(self, x, y):
        self.calls.append(("click", x, y))

    def type_text(self, text, press_enter=False):
        self.calls.append(("type", text, press_enter))

    def type_text_at(self, x, y, text, press_enter=False, clear_before_typing=True):
        self.calls.append(("type_at", x, y, text, press_enter, clear_before_typing))


def test_cua_preflight_denial_blocks_entire_response_batch():
    events = []
    calls = [
        SimpleNamespace(name="click", args={"x": 100, "y": 100}),
        SimpleNamespace(name="click", args={
            "x": 900, "y": 450,
            "safety_decision": {"decision": "require_confirmation", "explanation": "Send message?"},
        }),
    ]

    def deny(safety):
        events.append(("confirm", safety["explanation"]))
        return "TERMINATE"

    approved = preflight_safety_confirmations(calls, deny)
    dispatch_events = []
    if approved is not None:
        dispatch_events.extend(("execute", call.name) for call in calls)

    assert approved is None
    assert events == [("confirm", "Send message?")]
    assert dispatch_events == []


def test_cua_preflight_confirms_all_flagged_calls_before_dispatch_can_start():
    events = []
    calls = [
        SimpleNamespace(name="click", args={"x": 100, "y": 100}),
        SimpleNamespace(name="click", args={
            "x": 900, "y": 450,
            "safety_decision": {"decision": "require_confirmation", "explanation": "Send message?"},
        }),
        SimpleNamespace(name="press_key", args={
            "key": "Return",
            "safety_decision": {"decision": "require_confirmation", "explanation": "Submit form?"},
        }),
    ]

    def approve(safety):
        events.append(("confirm", safety["explanation"]))
        return "CONTINUE"

    approved = preflight_safety_confirmations(calls, approve)
    assert approved == {1, 2}
    assert events == [("confirm", "Send message?"), ("confirm", "Submit form?")]

    # Structural regression: BrowserAgent must call preflight before its first dispatch.
    agent_source = Path("src/jarvis/computer_use/agent.py").read_text(encoding="utf-8")
    method_source = agent_source[agent_source.index("    def run_one_iteration("):]
    assert method_source.index("self._preflight_safety_confirmations(function_calls)") < method_source.index("fc_result = self.handle_action(")


def test_cua_preflight_fails_closed_if_confirmation_callback_raises():
    calls = [SimpleNamespace(args={
        "safety_decision": {"decision": "require_confirmation", "explanation": "Send message?"},
    })]
    errors = []
    def unavailable(_safety):
        raise RuntimeError("HUD unavailable")
    approved = preflight_safety_confirmations(calls, unavailable, on_error=errors.append)
    assert approved is None
    assert len(errors) == 1
    assert "HUD unavailable" in str(errors[0])


def test_secure_proxy_blocks_text_when_confirmation_denied_or_unavailable():
    inner = FakeComputer()
    proxy = SecureComputerProxy(inner, SecurityGuard(), lambda _reason: False)
    with pytest.raises(PermissionError):
        proxy.type_text("sudo reboot")
    assert inner.calls == []

    def unavailable(_reason):
        raise RuntimeError("confirmation UI unavailable")

    proxy = SecureComputerProxy(inner, SecurityGuard(), unavailable)
    with pytest.raises(PermissionError):
        proxy.type_text("sudo reboot")
    assert inner.calls == []


def test_workflow_confirmation_denial_does_not_execute_recovery(tmp_path: Path):
    root = tmp_path / "workflows"
    root.mkdir()
    path = root / "guarded.yml"
    path.write_text(
        """
name: guarded
description: Test that denied confirmation cannot run recovery
steps:
  - action: type_text
    args:
      text: "sudo reboot"
    recovery:
      - action: click
        args: {x: 100, y: 100}
""",
        encoding="utf-8",
    )
    workflow = WorkflowRegistry(root).get("guarded")
    assert workflow is not None
    computer = FakeComputer()
    executor = WorkflowExecutor(SecurityGuard(), lambda _reason: False)

    with pytest.raises(WorkflowAuthorizationError):
        executor.run(workflow, computer)
    assert computer.calls == []
