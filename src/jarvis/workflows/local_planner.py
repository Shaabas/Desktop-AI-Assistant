"""Text-only local-LLM workflow selection and parameter binding.

The planner never receives screenshots, coordinates from live perception, or
accessibility trees. It chooses only from validated and approved workflows, and
it cannot modify the workflow action sequence.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from ..models.ollama import OllamaClient
from .matcher import WorkflowMatch, WorkflowMatcher
from .registry import WorkflowRegistry
from .schema import Workflow, validate_workflow


class WorkflowLocalPlanner:
    SYSTEM = """You are JARVIS's text-only workflow selector. You have NO screenshot,
visual perception, or accessibility tree. Select a matching workflow only from
the candidates supplied. Do not invent workflow names, actions, URLs, or values.
Extract parameter values only when grounded in the user's request. If no candidate
clearly matches, a required value is missing, or a candidate would perform a
different task, return workflow=null. Return JSON only with this schema:
{"workflow": string|null, "parameters": object, "confidence": number, "reason": string}.
Never choose an unlisted workflow. Never modify steps."""

    def __init__(
        self,
        ollama: OllamaClient,
        registry: WorkflowRegistry,
        matcher: WorkflowMatcher | None = None,
        *,
        minimum_score: float = 0.40,
        selection_timeout_seconds: float = 20.0,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.ollama = ollama
        self.registry = registry
        self.matcher = matcher or WorkflowMatcher(registry, minimum_score=minimum_score)
        self.selection_timeout_seconds = max(3.0, min(60.0, float(selection_timeout_seconds)))
        self.status_callback = status_callback or (lambda _message: None)
        self.last_reason = ""
        self.last_error = ""

    def _status(self, message: str) -> None:
        try:
            self.status_callback(message)
        except Exception:
            # Status UI failures must never break workflow routing.
            pass

    @staticmethod
    def _placeholder_names(value: Any) -> set[str]:
        names: set[str] = set()
        if isinstance(value, str):
            names.update(match.group(1).strip().removeprefix("param.") for match in re.finditer(r"\{\{\s*([^}]+?)\s*\}\}", value))
        elif isinstance(value, dict):
            for child in value.values():
                names.update(WorkflowLocalPlanner._placeholder_names(child))
        elif isinstance(value, list):
            for child in value:
                names.update(WorkflowLocalPlanner._placeholder_names(child))
        return names

    @classmethod
    def _parameter_schema(cls, workflow: Workflow) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, definition in workflow.parameters.items():
            if isinstance(definition, dict):
                result[name] = {
                    key: definition[key]
                    for key in ("description", "required", "default", "type")
                    if key in definition
                }
            else:
                result[name] = {"default": definition, "required": False}
        all_step_data = [
            {"args": step.args, "expect": step.expect, "recovery": step.recovery}
            for step in workflow.steps
        ]
        placeholders: set[str] = set()
        for item in all_step_data:
            placeholders.update(cls._placeholder_names(item))
        for name in placeholders:
            existing = result.get(name)
            if not isinstance(existing, dict):
                result[name] = {"description": "Value used by a workflow placeholder", "required": True}
            else:
                existing.setdefault("description", "Value used by a workflow placeholder")
                existing["required"] = True
                result[name] = existing
        return result

    def _minimum_candidate_score(self, workflow: Workflow) -> float:
        """Require a stronger lexical match for workflows with no input slots.

        Parameterized workflows may describe a reusable intent whose literal
        parameter value is absent from its title/tags. Unparameterized workflows
        should match the overall request closely rather than one subtask phrase.
        """
        schema = self._parameter_schema(workflow)
        has_required_parameters = any(
            isinstance(definition, dict) and definition.get("required") is True
            for definition in schema.values()
        )
        base = max(0.30, self.matcher.minimum_score)
        return base if has_required_parameters else max(0.50, base)

    @staticmethod
    def _step_summary(workflow: Workflow) -> list[dict[str, Any]]:
        # Deliberately exclude literal action arguments; the model needs semantic
        # intent and placeholder names, not personal messages or coordinate values.
        summary: list[dict[str, Any]] = []
        for index, step in enumerate(workflow.steps, start=1):
            placeholders = WorkflowLocalPlanner._placeholder_names(step.args)
            summary.append({
                "step": index,
                "intent": step.intent or step.action,
                "action": step.action,
                "argument_fields": sorted(step.args.keys()),
                "parameters_used": sorted(placeholders),
                "has_postcondition": bool(step.expect),
            })
        return summary


    def authorize_next_step(
        self,
        workflow: Workflow,
        *,
        step_index: int,
        total_steps: int,
        task_query: str,
        previous_steps: list[dict[str, Any]],
        latest_url: str = "",
    ) -> tuple[bool, str]:
        """Authorize the next fixed YAML step using text-only execution feedback.

        The model can continue or stop; it cannot reorder steps or create actions.
        The action broker separately validates and executes the YAML step.
        """
        if step_index < 1 or step_index > total_steps:
            return False, "invalid next-step index"
        step = workflow.steps[step_index - 1]
        current = {
            "step_index": step_index,
            "total_steps": total_steps,
            "intent": step.intent or step.action,
            "action": step.action,
            "argument_fields": sorted(step.args.keys()),
            "has_postcondition": bool(step.expect),
            "postcondition": step.expect,
        }
        payload = {
            "task": task_query or workflow.description,
            "workflow": workflow.name,
            "workflow_description": workflow.description,
            "next_step": current,
            "previous_steps": previous_steps[-5:],
            "last_known_url": latest_url,
        }
        system = """You are JARVIS's text-only workflow step controller. You have no screenshot,
visual perception, or accessibility tree. The YAML workflow was approved and its
step order is fixed. Return JSON only: {"decision":"execute_next"|"stop","step_index":integer,"confidence":number,"reason":string}.
Authorize only the exact next_step.step_index supplied. Normally continue when
previous steps returned without errors and any declared postconditions passed.
Stop if a prior outcome failed, the task intent conflicts with the next step, or
there is a textual safety concern. Never infer visual state. For a step without
a postcondition, success only means its tool call returned without an exception."""
        try:
            decision = json.loads(self.ollama.generate(
                "Authorize the next step from this text-only execution context:\n"
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                system=system, json_format=True, timeout=30.0,
            ))
        except Exception as exc:
            self.last_error = f"local step controller failed: {exc}"
            return False, self.last_error
        if not isinstance(decision, dict):
            return False, "local step controller returned a non-object response"
        if decision.get("decision") != "execute_next":
            return False, str(decision.get("reason") or "local model stopped workflow execution")
        try:
            chosen_index = int(decision.get("step_index", -1))
            confidence = float(decision.get("confidence", 0.0))
        except (TypeError, ValueError):
            return False, "local step controller returned invalid index/confidence"
        if chosen_index != step_index:
            return False, f"local step controller selected step {chosen_index}; only step {step_index} is permitted"
        if confidence < 0.60:
            return False, f"local step controller confidence too low ({confidence:.2f})"
        return True, str(decision.get("reason") or "authorized by local workflow controller")

    def choose_recovery(
        self,
        workflow: Workflow,
        *,
        step_index: int,
        error: str,
        latest_url: str = "",
    ) -> int | None:
        """Choose only among a step's declared recovery actions; never invent one."""
        step = workflow.steps[step_index - 1]
        recovery_options = []
        for index, recovery in enumerate(step.recovery):
            recovery_options.append({
                "recovery_index": index,
                "intent": str(recovery.get("intent") or recovery.get("action") or "recovery"),
                "action": recovery.get("action"),
                "argument_fields": sorted((recovery.get("args") or {}).keys()) if isinstance(recovery.get("args") or {}, dict) else [],
                "has_postcondition": bool(recovery.get("expect")),
            })
        if not recovery_options:
            return None
        payload = {
            "task": workflow.description,
            "workflow": workflow.name,
            "failed_step": {"index": step_index, "action": step.action, "intent": step.intent},
            "error": error[:500],
            "last_known_url": latest_url,
            "recovery_options": recovery_options,
        }
        system = """You are JARVIS's text-only workflow recovery controller. You have no
screenshot, visual perception, or accessibility tree. Choose only a listed recovery
option or stop. Return JSON only: {"decision":"recover"|"stop","recovery_index":integer|null,"confidence":number,"reason":string}.
Never invent recovery actions. Since you cannot see the screen, stop when the
listed options do not clearly address the textual failure."""
        try:
            decision = json.loads(self.ollama.generate(
                "Choose recovery from this text-only context:\n"
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                system=system, json_format=True, timeout=30.0,
            ))
        except Exception as exc:
            self.last_error = f"local recovery selection failed: {exc}"
            return None
        if not isinstance(decision, dict) or decision.get("decision") != "recover":
            return None
        try:
            recovery_index = int(decision.get("recovery_index", -1))
            confidence = float(decision.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        if confidence < 0.60 or not 0 <= recovery_index < len(recovery_options):
            return None
        return recovery_index

    def choose(
        self,
        query: str,
        *,
        environment: str,
        platform: str,
    ) -> WorkflowMatch | None:
        self.last_reason = ""
        self.last_error = ""
        self._status("Checking approved workflows for a close match...")
        # Rank first so a task with no plausible workflow never waits on an
        # Ollama availability probe or model startup before entering CUA.
        ranked = self.matcher.rank(query, environment=environment, platform=platform, limit=8)
        # Do not spend a slow local-model inference on candidates which share only
        # a few generic words with the request. rank() intentionally returns any
        # positive score; selection must enforce a useful minimum similarity.
        candidates = [
            candidate for candidate in ranked
            if candidate.score >= self._minimum_candidate_score(candidate.workflow)
        ]
        if not candidates:
            self.last_reason = "no approved workflow has a sufficiently close match"
            if ranked:
                best_threshold = self._minimum_candidate_score(ranked[0].workflow)
                self._status(
                    f"No close workflow match (best score {ranked[0].score:.2f}; "
                    f"required {best_threshold:.2f} for '{ranked[0].workflow.name}'). "
                    "Skipping local-model selection; routing this task to Computer Use."
                )
            else:
                self._status("No approved, valid workflows are eligible for this task; routing to Computer Use.")
            return None

        if not self.ollama.available():
            self.last_reason = "local model unavailable"
            self._status(f"Local workflow model '{getattr(self.ollama, 'model', 'local model')}' is unavailable; routing this task to Computer Use.")
            return None

        by_name = {candidate.workflow.name: candidate for candidate in candidates}
        candidate_payload = []
        for candidate in candidates:
            workflow = candidate.workflow
            candidate_payload.append({
                "name": workflow.name,
                "description": workflow.description,
                "tags": workflow.tags[:12],
                "environment": workflow.environment,
                "platform": workflow.platform,
                "parameters": self._parameter_schema(workflow),
                "steps": self._step_summary(workflow),
            })
        self._status(
            f"Asking local model '{getattr(self.ollama, 'model', 'local model')}' to choose among {len(candidate_payload)} "
            f"workflow candidate(s) (timeout {self.selection_timeout_seconds:.0f}s)..."
        )
        prompt = (
            "User task: " + query.strip() + "\n"
            + "Runtime environment: " + environment + "\n"
            + "Runtime platform: " + platform + "\n"
            + "Approved workflow candidates (the step order is fixed and must not be changed):\n"
            + json.dumps(candidate_payload, ensure_ascii=False, separators=(",", ":"))
            + "\nChoose only if the task has the same intent. Use parameters only for values clearly stated or directly inferable from the user task."
        )
        try:
            raw = self.ollama.generate(
                prompt,
                system=self.SYSTEM,
                json_format=True,
                timeout=self.selection_timeout_seconds,
            )
            decision = json.loads(raw)
        except Exception as exc:
            self.last_error = f"local workflow planner failed: {exc}"
            self._status(f"Local workflow selection failed: {exc}. Falling back to Computer Use.")
            return None
        if not isinstance(decision, dict):
            self.last_error = "local workflow planner returned a non-object response"
            self._status("Local workflow model returned an invalid response; falling back to Computer Use.")
            return None
        selected_name = decision.get("workflow")
        reason = str(decision.get("reason") or "").strip()
        self.last_reason = reason
        if selected_name is None:
            self.last_reason = reason or "local model did not select a workflow"
            self._status(f"No workflow selected by local model ({self.last_reason}); using Computer Use for this task.")
            return None
        if not isinstance(selected_name, str) or selected_name not in by_name:
            self.last_error = "local workflow planner selected a workflow outside the approved candidate set"
            return None
        candidate = by_name[selected_name]
        if not candidate.workflow.is_runnable or validate_workflow(candidate.workflow):
            self.last_error = "local workflow planner selected a workflow that failed validation"
            return None
        try:
            confidence = float(decision.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < 0.72:
            self.last_reason = f"planner confidence too low ({confidence:.2f}): {reason}"
            self._status(f"Local model confidence was too low ({confidence:.2f}); using Computer Use.")
            return None
        supplied = decision.get("parameters") or {}
        if not isinstance(supplied, dict):
            self.last_error = "local workflow planner parameters must be an object"
            return None
        permitted = set(self._parameter_schema(candidate.workflow))
        if set(supplied) - permitted:
            self.last_error = "local workflow planner supplied undeclared parameter names"
            return None
        for name, definition in self._parameter_schema(candidate.workflow).items():
            required = isinstance(definition, dict) and definition.get("required") is True
            empty = name not in supplied or supplied[name] is None or (isinstance(supplied.get(name), str) and not supplied[name].strip())
            if required and empty and "default" not in definition:
                self.last_reason = f"required value '{name}' was not extracted from the task"
                return None
        candidate.parameters = supplied
        self._status(f"Selected approved workflow '{candidate.workflow.name}'. Starting workflow execution.")
        return candidate
