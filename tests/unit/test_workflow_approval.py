from pathlib import Path
from types import SimpleNamespace

from src.jarvis.agent.controller import JarvisController
from src.jarvis.workflows.registry import WorkflowRegistry
from src.jarvis.workflows.router import ExecutionResult
from src.jarvis.workflows.schema import Workflow, WorkflowStep


def _candidate_registry(root: Path) -> WorkflowRegistry:
    registry = WorkflowRegistry(root)
    workflow = Workflow(
        name="candidate_demo",
        description="Open a test page",
        environment="desktop",
        platform="linux",
        steps=[WorkflowStep(action="navigate", args={"url": "https://example.com"}, intent="Open example.com")],
        metadata={"source": "gemini_computer_use", "status": "unvalidated"},
    )
    registry.save(workflow, root / "learned" / "candidate_demo.yml")
    return registry


def _controller_with_fake_router(tmp_path: Path, approved: bool):
    statuses = []
    controller = JarvisController(
        status_callback=statuses.append,
        workflow_approval_callback=lambda workflow: approved,
    )
    controller.workflow_registry = _candidate_registry(tmp_path / "workflows")

    class FakeRouter:
        def execute(self, *args, **kwargs):
            return ExecutionResult(
                success=True,
                message="Computer-use task finished.",
                workflow="candidate_demo",
                used_gemini=True,
                outcome="completed_unverified",
            )

    controller.execution_router = FakeRouter()
    return controller, statuses


def test_learned_candidate_is_approved_after_explicit_callback(tmp_path: Path):
    controller, statuses = _controller_with_fake_router(tmp_path, approved=True)
    message = controller.process_request("open a test page")

    assert controller.workflow_registry.get("candidate_demo").status == "approved"
    assert "approved for future automatic execution" in message
    assert any("ready for your review" in item for item in statuses)


def test_declined_learned_candidate_remains_disabled(tmp_path: Path):
    controller, _ = _controller_with_fake_router(tmp_path, approved=False)
    message = controller.process_request("open a test page")

    assert controller.workflow_registry.get("candidate_demo").status == "unvalidated"
    assert "remains disabled" in message
