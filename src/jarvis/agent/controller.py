from __future__ import annotations

import platform
from pathlib import Path
from typing import Any, Callable
import threading

from .security import SecurityGuard
from ..config import load_settings
from ..models.ollama import OllamaClient
from ..workflows import ExecutionRouter, WorkflowRegistry, WorkflowAdapter
from ..workflows.local_planner import WorkflowLocalPlanner


class JarvisController:
    """Single product-level orchestration boundary for JARVIS."""

    def __init__(self, *, status_callback: Callable[[str], None] | None = None, confirmation_callback: Callable[[str], bool] | None = None, workflow_approval_callback: Callable[[Any], bool] | None = None) -> None:
        self.status_callback = status_callback or (lambda _message: None)
        self.confirmation_callback = confirmation_callback or self._default_confirmation
        self.workflow_approval_callback = workflow_approval_callback or self._default_workflow_approval
        self.security = SecurityGuard()
        self.settings = load_settings()
        root = Path(__file__).resolve().parents[3]
        self.workflow_registry = WorkflowRegistry(root / "workflows")
        self.ollama = OllamaClient()
        self.workflow_adapter = WorkflowAdapter(self.ollama, self.workflow_registry)
        self.workflow_local_planner = WorkflowLocalPlanner(
            self.ollama,
            self.workflow_registry,
            minimum_score=float(self.settings.get("workflow_planner_min_score", 0.40)),
            selection_timeout_seconds=float(self.settings.get("workflow_selection_timeout_seconds", 20.0)),
            status_callback=self.status_callback,
        )
        self.execution_router = ExecutionRouter(
            self.workflow_registry,
            self.security,
            self.confirmation_callback,
            match_threshold=float(self.settings.get("workflow_match_threshold", 0.45)),
            local_planner=self.workflow_local_planner,
        )

    @staticmethod
    def _default_confirmation(reason: str) -> bool:
        answer = input(f"\n[JARVIS] Confirmation required: {reason}\nProceed? [yes/no] ").strip().lower()
        return answer in {"y", "yes"}

    @staticmethod
    def _default_workflow_approval(workflow: Any) -> bool:
        """Console approval for non-daemon/CLI use; GUI daemons inject the GNOME card."""
        step_lines = [
            f"  {index}. {step.intent or step.action}"
            for index, step in enumerate(workflow.steps[:8], start=1)
        ]
        if len(workflow.steps) > len(step_lines):
            step_lines.append(f"  ... and {len(workflow.steps) - len(step_lines)} more steps")
        print(f"\n[JARVIS] Learned workflow candidate: {workflow.name}")
        print(f"Description: {workflow.description}")
        print("Recorded steps:\n" + "\n".join(step_lines))
        print("This enables future automatic execution; recorded coordinates may be fragile.")
        answer = input("Approve this workflow? [y/N] ").strip().lower()
        return answer in {"y", "yes", "approve", "approved"}

    @property
    def platform(self) -> str:
        return "windows" if platform.system().lower().startswith("win") else "linux"

    def _environment(self, environment: str | None = None) -> str:
        if environment:
            return environment
        return "desktop" if self.platform == "linux" else "playwright"

    def process_request(self, query: str, *, environment: str | None = None, model_name: str | None = None, allow_learning: bool | None = None, cancel_event: threading.Event | None = None) -> str:
        query = query.strip()
        if not query:
            return ""

        # Conversational requests stay local. Gemini is reserved for Computer Use.
        low = query.lower()
        conversational_prefixes = (
            "hello", "hi ", "hey ", "what is ", "what are ", "who is ", "why ",
            "how do ", "how does ", "explain ", "define ", "tell me about ", "can you explain",
        )
        looks_conversational = low.startswith(conversational_prefixes) or query.endswith("?")
        if looks_conversational and self.ollama.available():
            self.status_callback("Thinking locally...")
            return self.ollama.answer(query)

        env = self._environment(environment)
        model_name = model_name or str(self.settings.get("cloud_computer_use_model") or "gemini-3.6-flash")
        if allow_learning is None:
            allow_learning = bool(self.settings.get("workflow_auto_learning", True))
        self.status_callback("Checking workflows...")
        result = self.execution_router.execute(
            query,
            environment=env,
            model_name=model_name,
            platform=self.platform,
            allow_learning=allow_learning,
            cancel_event=cancel_event,
        )
        message = result.message

        # A Gemini run may produce a learned workflow candidate. Prompt only for
        # schema-valid candidates; invalid traces remain disabled and explain why.
        if result.used_gemini and result.workflow:
            candidate = self.workflow_registry.get(result.workflow)
            if candidate is not None and not candidate.is_runnable:
                errors = self.workflow_registry.validation_errors(candidate.name)
                if errors:
                    message += (
                        f" The learned workflow '{candidate.name}' was not offered for approval "
                        "because validation failed: " + "; ".join(errors[:4])
                    )
                else:
                    self.status_callback(f"Workflow '{candidate.name}' is ready for your review.")
                    approved = False
                    try:
                        approved = bool(self.workflow_approval_callback(candidate))
                    except Exception as exc:
                        self.status_callback(f"Workflow approval failed safely: {exc}")
                    if approved:
                        try:
                            self.workflow_registry.approve(candidate.name)
                            message += f" Workflow '{candidate.name}' was approved for future automatic execution."
                        except Exception as exc:
                            message += f" Workflow '{candidate.name}' remains disabled because approval failed: {exc}"
                    else:
                        message += f" Workflow '{candidate.name}' remains disabled; approval was declined or timed out."
        return message

    def adapt_workflow(self, workflow_name: str, request: str) -> str:
        workflow = self.workflow_registry.get(workflow_name)
        if workflow is None:
            raise KeyError(f"Unknown workflow: {workflow_name}")
        adapted = self.workflow_adapter.adapt(workflow, request)
        path = self.workflow_registry.save(adapted, self.workflow_registry.root / "adapted" / f"{adapted.name}.yml")
        return str(path)
