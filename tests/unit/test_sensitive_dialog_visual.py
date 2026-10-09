from pathlib import Path


EXTENSION_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "gnome-extension"
    / "deskwright@zeticle.com"
    / "indicator.js"
).read_text(encoding="utf-8")


def _confirmation_method() -> str:
    start = EXTENSION_SOURCE.index("    showConfirmation(reason, requestId) {")
    end = EXTENSION_SOURCE.index("    clearConfirmation() {", start)
    return EXTENSION_SOURCE[start:end]


def test_sensitive_confirmation_uses_vertical_workflow_card_visuals():
    method = _confirmation_method()

    assert "vertical: true" in method
    assert "x_expand: true" in method
    assert "background-color: rgba(255,255,255,0.045)" in method
    assert "border: 1px solid rgba(245,158,11,0.38)" in method
    assert "border-radius: 12px" in method
    assert "padding: 9px 11px 9px 11px" in method
    assert "style: 'spacing: 8px;'" in method
    assert "buttons.add_child(deny)" in method
    assert "buttons.add_child(allow)" in method
    assert "const expandedHeight = Math.min(205, availableHeight)" in method


def test_sensitive_confirmation_content_and_decision_contract_are_unchanged():
    method = _confirmation_method()

    assert "text: String(reason || 'JARVIS needs your confirmation.')" in method
    assert "_makeActionButton('Deny'" in method
    assert "request_id: confirmationId" in method
    assert "decision: 'deny'" in method
    assert "_makeActionButton('Allow'" in method
    assert "decision: 'allow'" in method
    assert "setAssistantState(AgentState.WAITING_CONFIRMATION, 'Confirmation required')" in method
    assert "ShowConfirmation" not in method
    assert "GetPendingWorkflowApprovalResponses" not in method


def test_closing_sensitive_confirmation_restores_base_hud_geometry():
    start = EXTENSION_SOURCE.index("    clearConfirmation() {")
    end = EXTENSION_SOURCE.index("    showWorkflowApproval(", start)
    method = EXTENSION_SOURCE[start:end]

    assert "this._hudActor.height = this._hudBaseHeight || 92" in method
    assert "this._hudActor.y = this._hudBaseY" in method
    assert "!this._workflowApprovalActor" in method
