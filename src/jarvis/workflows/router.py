from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable
import threading

from ..agent.security import SecurityGuard

if TYPE_CHECKING:
    from ..computer_use.computers import Computer
from .compiler import WorkflowCompiler
from .executor import WorkflowCancelled, WorkflowExecutionError, WorkflowExecutor
from .matcher import WorkflowMatch, WorkflowMatcher
from .registry import WorkflowRegistry
from .trace import TracingComputer
from .secure import SecureComputerProxy


@dataclass(slots=True)
class ExecutionResult:
    success: bool
    message: str
    workflow: str | None = None
    used_gemini: bool = False
    outcome: str = "unknown"


class ExecutionRouter:
    """Workflow-first execution with a cautious Gemini general fallback.

    Learned traces are always saved as unvalidated candidates. They cannot be
    auto-matched until reviewed and explicitly approved. A failed workflow does
    not immediately trigger Gemini against the same task because its side effects
    may already have happened.
    """

    def __init__(
        self,
        registry: WorkflowRegistry,
        security: SecurityGuard,
        confirm: Callable[[str], bool],
        workflow_executor: WorkflowExecutor | None = None,
        match_threshold: float = 0.45,
        compiler: WorkflowCompiler | None = None,
        local_planner: Any | None = None,
    ) -> None:
        self.registry = registry
        self.matcher = WorkflowMatcher(registry, minimum_score=match_threshold)
        self.security = security
        self.confirm = confirm
        self.local_planner = local_planner
        self.workflow_executor = workflow_executor or WorkflowExecutor(
            security, confirm, step_controller=local_planner
        )
        self.compiler = compiler or WorkflowCompiler()

    @staticmethod
    def _computer_for_environment(environment: str, *, initial_url: str | None = None):
        from ..computer_use.computers import BrowserbaseComputer, DeskwrightComputer, PlaywrightComputer

        if environment == "playwright":
            return PlaywrightComputer(screen_size=(1440, 900), initial_url=initial_url or "https://www.google.com")
        if environment == "browserbase":
            if BrowserbaseComputer is None:
                raise RuntimeError("Browserbase support is not installed")
            return BrowserbaseComputer(screen_size=(1440, 900), initial_url=initial_url or "https://www.google.com")
        return DeskwrightComputer(initial_url=initial_url)

    def run_workflow(
        self,
        match: WorkflowMatch,
        parameters: dict[str, Any] | None = None,
        cancel_event: threading.Event | None = None,
        *,
        task_query: str = "",
        use_local_llm: bool = True,
    ) -> ExecutionResult:
        workflow = match.workflow
        computer = self._computer_for_environment(workflow.environment)
        with computer as active:
            effective_parameters = dict(match.parameters or {})
            effective_parameters.update(parameters or {})
            report = self.workflow_executor.run_with_report(
                workflow,
                active,
                parameters=effective_parameters,
                cancel_event=cancel_event,
                task_query=task_query or workflow.description,
                use_local_llm=use_local_llm,
            )
        if report.outcome == "succeeded_verified":
            message = f"Workflow '{workflow.name}' completed and its declared postconditions were satisfied."
        else:
            message = (
                f"Workflow '{workflow.name}' finished executing its actions, but completion is unverified "
                "because one or more steps had no supported postcondition."
            )
        return ExecutionResult(True, message, workflow=workflow.name, outcome=report.outcome)

    def _learn_candidate(self, query: str, actions: list[dict[str, Any]], active: Any, environment: str) -> str | None:
        if not actions:
            return None
        workflow = self.compiler.compile_from_trace(
            query,
            actions,
            active.screen_size(),
            environment=environment,
            platform="linux" if environment == "desktop" else "any",
        )
        if not workflow.steps:
            return None
        # Do not overwrite an approved workflow with a new trace from a single run.
        target = self.registry.root / "learned" / f"{workflow.name}.yml"
        existing = self.registry.get(workflow.name)
        if existing is not None and existing.is_runnable:
            target = self.registry.root / "learned" / f"{workflow.name}_candidate.yml"
            workflow.name = f"{workflow.name}_candidate"
        self.registry.save(workflow, target)
        return workflow.name

    def run_gemini(
        self,
        query: str,
        *,
        environment: str,
        model_name: str,
        initial_url: str | None = None,
        wrap: Callable[[Computer, str], Any] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ExecutionResult:
        computer = self._computer_for_environment(environment, initial_url=initial_url)
        with computer as active:
            tracing = TracingComputer(active)
            wrapped = wrap(tracing, query) if wrap else tracing
            wrapped = SecureComputerProxy(wrapped, self.security, self.confirm)
            from ..agent.computer_agent import JarvisComputerUseAgent
            agent = JarvisComputerUseAgent(
                browser_computer=wrapped,
                query=query,
                model_name=model_name,
                verbose=True,
                confirmation_callback=self.confirm,
                cancel_event=cancel_event,
            )
            agent.agent_loop()
            if getattr(agent, "security_aborted", False):
                return ExecutionResult(
                    False,
                    agent.final_reasoning or "Sensitive action was blocked; no pending actions were executed.",
                    used_gemini=True,
                    outcome="blocked",
                )
            if getattr(agent, "loop_guard_aborted", False):
                return ExecutionResult(
                    False,
                    agent.final_reasoning or "Computer-use stopped after repeated actions made no progress.",
                    used_gemini=True,
                    outcome="stalled",
                )
            candidate = self._learn_candidate(query, tracing.actions, active, environment)
        message = "Computer-use task finished."
        if candidate:
            message += f" A workflow candidate '{candidate}' was saved for review; it is not enabled for automatic execution."
        return ExecutionResult(True, message, workflow=candidate, used_gemini=True, outcome="completed_unverified")

    def execute(
        self,
        query: str,
        *,
        environment: str,
        model_name: str,
        platform: str,
        initial_url: str | None = None,
        parameters: dict[str, Any] | None = None,
        allow_learning: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> ExecutionResult:
        match = None
        use_local_llm = False
        if self.local_planner is not None:
            # When configured, the local LLM is the workflow selector and step
            # controller. If it is unavailable or declines the task, do not bypass
            # it with lexical matching; route to the general computer-use path.
            # The local LLM sees text-only task/workflow metadata, never screenshots
            # or an accessibility tree.
            match = self.local_planner.choose(query, environment=environment, platform=platform)
            use_local_llm = match is not None
        else:
            # Compatibility for callers that intentionally construct a router
            # without a local planner. The product controller always supplies one.
            match = self.matcher.find(query, environment=environment, platform=platform)
        if match:
            try:
                return self.run_workflow(
                    match,
                    parameters=parameters,
                    cancel_event=cancel_event,
                    task_query=query,
                    use_local_llm=use_local_llm,
                )
            except WorkflowCancelled:
                raise
            except WorkflowExecutionError as exc:
                # A click/type may have succeeded before a postcondition timed out.
                # Never replay the original user task automatically after that.
                return ExecutionResult(
                    False,
                    f"Workflow '{match.workflow.name}' stopped safely: {exc}. I did not automatically replay the task to avoid duplicate side effects.",
                    workflow=match.workflow.name,
                    outcome="failed_or_unknown",
                )

        computer = self._computer_for_environment(environment, initial_url=initial_url)
        with computer as active:
            tracing = TracingComputer(active)
            wrapped = SecureComputerProxy(tracing, self.security, self.confirm)
            from ..agent.computer_agent import JarvisComputerUseAgent
            agent = JarvisComputerUseAgent(
                browser_computer=wrapped,
                query=query,
                model_name=model_name,
                verbose=True,
                confirmation_callback=self.confirm,
                cancel_event=cancel_event,
            )
            agent.agent_loop()
            if getattr(agent, "security_aborted", False):
                return ExecutionResult(
                    False,
                    agent.final_reasoning or "Sensitive action was blocked; no pending actions were executed.",
                    used_gemini=True,
                    outcome="blocked",
                )
            if getattr(agent, "loop_guard_aborted", False):
                return ExecutionResult(
                    False,
                    agent.final_reasoning or "Computer-use stopped after repeated actions made no progress.",
                    used_gemini=True,
                    outcome="stalled",
                )
            candidate = self._learn_candidate(query, tracing.actions, active, environment) if allow_learning else None
        message = "Computer-use task finished."
        if candidate:
            message += f" A workflow candidate '{candidate}' was saved for review; it is not enabled for automatic execution."
        return ExecutionResult(True, message, workflow=candidate, used_gemini=True, outcome="completed_unverified")
