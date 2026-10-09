from src.jarvis.gnome.bridge import GnomeShellBridge


def test_tuple_parser():
    bridge = GnomeShellBridge()
    assert bridge._tuple_value("('abc',)") == "abc"
    assert bridge._tuple_value("(['yes', 'no'],)") == ["yes", "no"]
    assert bridge._tuple_value("(true,)") is True


def test_semantic_ui_calls():
    bridge = GnomeShellBridge()
    calls = []

    def fake_call(method, *args):
        calls.append((method, args))
        return "(true,)"

    bridge.call = fake_call

    assert bridge.activate_assistant() is True
    assert bridge.close_assistant() is True
    assert bridge.set_assistant_state("thinking", "Thinking…") is True
    assert bridge.append_conversation_message("assistant", "Done") is True
    assert bridge.set_activity("Checking workflow") is True
    assert bridge.show_confirmation("sudo requires confirmation", "request-1") is True
    assert bridge.clear_confirmation() is True
    assert bridge.request_voice_capture() is True

    assert calls == [
        ("ActivateAssistant", ()),
        ("CloseAssistant", ()),
        ("SetAssistantState", ("thinking", "Thinking…")),
        ("AppendConversationMessage", ("assistant", "Done")),
        ("SetActivity", ("Checking workflow",)),
        ("ShowConfirmation", ("sudo requires confirmation", "request-1")),
        ("ClearConfirmation", ()),
        ("RequestVoiceCapture", ()),
    ]


def test_workflow_approval_bridge_uses_separate_response_queue():
    bridge = GnomeShellBridge()
    calls = []

    def fake_call(method, *args):
        calls.append((method, args))
        if method == "GetPendingWorkflowApprovalResponses":
            return "(['{\"workflow\": \"demo\", \"decision\": \"approve\"}'],)"
        return "(true,)"

    bridge.call = fake_call
    assert bridge.show_workflow_approval("demo", "Demo task", "1. click • 2. type", 2) is True
    assert bridge.clear_workflow_approval() is True
    assert bridge.get_pending_workflow_approval_responses() == ['{"workflow": "demo", "decision": "approve"}']
    assert calls == [
        ("ShowWorkflowApproval", ("demo", "Demo task", "1. click • 2. type", 2)),
        ("ClearWorkflowApproval", ()),
        ("GetPendingWorkflowApprovalResponses", ()),
    ]


def test_wait_for_workflow_approval_requires_matching_named_button_response(monkeypatch):
    import json
    from types import SimpleNamespace

    bridge = GnomeShellBridge()
    workflow = SimpleNamespace(
        name="demo",
        description="Open the demo site",
        steps=[SimpleNamespace(intent="Navigate to demo", action="navigate")],
    )
    responses = iter([
        [],
        [json.dumps({"workflow": "other", "decision": "approve"})],
        [json.dumps({"workflow": "demo", "decision": "approve"})],
    ])
    calls = []
    monkeypatch.setattr(bridge, "get_pending_workflow_approval_responses", lambda: next(responses, []))
    monkeypatch.setattr(bridge, "set_assistant_state", lambda *args: True)
    monkeypatch.setattr(bridge, "show_workflow_approval", lambda *args: calls.append(("show", args)) or True)
    monkeypatch.setattr(bridge, "clear_workflow_approval", lambda: calls.append(("clear", ())) or True)
    monkeypatch.setattr(bridge, "halt_active", lambda: False)
    monkeypatch.setattr("time.sleep", lambda _duration: None)

    assert bridge.wait_for_workflow_approval(workflow, timeout=1.0) is True
    assert calls[0][0] == "show"
    assert calls[0][1][0] == "demo"
    assert calls[-1][0] == "clear"


def test_wait_for_confirmation_ignores_stale_responses_and_never_uses_instructions(monkeypatch):
    import json
    from types import SimpleNamespace

    bridge = GnomeShellBridge()
    monkeypatch.setattr(
        "src.jarvis.gnome.bridge.uuid.uuid4",
        lambda: SimpleNamespace(hex="request-current"),
    )
    responses = iter([
        ["yes"],  # legacy response format must not grant approval
        [json.dumps({"request_id": "request-old", "decision": "allow"})],
        [json.dumps({"request_id": "request-current", "decision": "deny"})],
        [],  # final drain
    ])
    shown = []
    cleared = []
    monkeypatch.setattr(bridge, "clear_confirmation", lambda: cleared.append(True) or True)
    monkeypatch.setattr(bridge, "get_pending_confirmation_responses", lambda: next(responses, []))
    monkeypatch.setattr(bridge, "set_assistant_state", lambda *args: True)
    monkeypatch.setattr(bridge, "show_confirmation", lambda *args: shown.append(args) or True)
    monkeypatch.setattr(bridge, "halt_active", lambda: False)
    monkeypatch.setattr(bridge, "get_pending_instructions", lambda: (_ for _ in ()).throw(AssertionError("instructions must not authorize actions")))
    monkeypatch.setattr("time.sleep", lambda _duration: None)

    assert bridge.wait_for_confirmation("Send message?", timeout=1.0) is False
    assert shown == [("Send message?", "request-current")]
    assert len(cleared) == 2


def test_wait_for_confirmation_fails_closed_if_dialog_cannot_be_shown(monkeypatch):
    bridge = GnomeShellBridge()
    calls = []
    monkeypatch.setattr(bridge, "clear_confirmation", lambda: calls.append("clear") or True)
    monkeypatch.setattr(bridge, "get_pending_confirmation_responses", lambda: calls.append("drain") or [])
    monkeypatch.setattr(bridge, "set_assistant_state", lambda *args: True)
    monkeypatch.setattr(bridge, "show_confirmation", lambda *args: False)
    assert bridge.wait_for_confirmation("Delete file?", timeout=1.0) is False
    assert calls == ["clear", "drain", "clear", "drain"]


def test_confirmation_dbus_contract_has_request_id_and_structured_responses():
    import re
    import xml.etree.ElementTree as ET
    from pathlib import Path

    ext_root = Path(__file__).resolve().parents[2] / "gnome-extension" / "deskwright@zeticle.com"
    dbus_source = (ext_root / "dbus.js").read_text(encoding="utf-8")
    indicator_source = (ext_root / "indicator.js").read_text(encoding="utf-8")
    xml_match = re.search(r"(<node>.*?</node>)`", dbus_source, flags=re.DOTALL)
    assert xml_match is not None
    root = ET.fromstring(xml_match.group(1))
    method = next(m for m in root.findall(".//method") if m.attrib.get("name") == "ShowConfirmation")
    inputs = [arg.attrib.get("name") for arg in method.findall("arg") if arg.attrib.get("direction") == "in"]
    assert inputs == ["reason", "requestId"]
    assert "ShowConfirmation(reason, requestId)" in dbus_source
    assert "request_id: confirmationId" in indicator_source
    assert "decision: 'allow'" in indicator_source
    assert "decision: 'deny'" in indicator_source
