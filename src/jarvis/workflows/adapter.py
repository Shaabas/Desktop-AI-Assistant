from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..models.ollama import OllamaClient
from .registry import WorkflowFormatError, WorkflowRegistry
from .schema import Workflow, validate_workflow


class WorkflowAdapter:
    """Use the local LLM to propose bounded edits to a workflow candidate.

    Adaptation is never auto-approved, even when the source workflow was approved.
    The adapted result is validated before it can be written to disk.
    """

    SYSTEM = """You edit JARVIS desktop YAML workflows. Return ONLY a YAML mapping.
Preserve the workflow schema and safety semantics. Do not invent unsupported
actions. Prefer changing parameters, intent text, URLs, text, and existing step
arguments over restructuring the workflow. Never add shell commands, executable
commands, or actions that are not present in the supported schema. The workflow
has no guaranteed screenshot or accessibility-tree access, so do not invent
selectors or visual expectations."""

    def __init__(self, ollama: OllamaClient, registry: WorkflowRegistry) -> None:
        self.ollama = ollama
        self.registry = registry

    @staticmethod
    def _parse_yaml(raw: str) -> dict[str, Any]:
        source = raw.strip()
        if source.startswith("```"):
            source = source.split("\n", 1)[-1]
            if source.endswith("```"):
                source = source[:-3].rstrip()
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise WorkflowFormatError(f"local LLM returned invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise WorkflowFormatError("local LLM did not return a YAML mapping")
        return data

    def adapt(self, workflow: Workflow, request: str) -> Workflow:
        prompt = (
            "Existing workflow:\n"
            + yaml.safe_dump(workflow.to_dict(), sort_keys=False, allow_unicode=True)
            + "\nUser requested change:\n"
            + request
            + "\nReturn the complete adapted workflow YAML. Preserve the action sequence unless the request explicitly requires changing it."
        )
        raw = self.ollama.generate(prompt, system=self.SYSTEM, timeout=90.0)
        data = self._parse_yaml(raw)

        # Reuse registry deserialization in a short-lived validation file.
        temp_path = self.registry.root / ".adapter_validation.yml"
        self.registry.root.mkdir(parents=True, exist_ok=True)
        try:
            temp_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            adapted = self.registry._load(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

        # Model output is untrusted and may copy the parent's approved status or
        # stale compiler metadata. Always require a fresh approval of the edit.
        adapted.metadata.pop("validation_errors", None)
        adapted.metadata["status"] = "unvalidated"
        adapted.metadata["source"] = "local_llm_adapter"
        adapted.metadata["adapted_from"] = workflow.name
        adapted.metadata["adapted_at"] = datetime.now(timezone.utc).isoformat()
        errors = validate_workflow(adapted)
        if errors:
            adapted.metadata["validation_errors"] = errors
            raise WorkflowFormatError("adapted workflow failed validation: " + "; ".join(errors))
        adapted.metadata.pop("validation_errors", None)
        return adapted
