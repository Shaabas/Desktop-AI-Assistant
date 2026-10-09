from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from .schema import Workflow, WorkflowStep, validate_workflow


class WorkflowFormatError(ValueError):
    pass


class WorkflowRegistry:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._workflows: dict[str, Workflow] = {}
        self.load_errors: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self._workflows.clear()
        self.load_errors.clear()
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            return
        paths = sorted(self.root.rglob("*.yml")) + sorted(self.root.rglob("*.yaml"))
        for path in paths:
            try:
                workflow = self._load(path)
                if workflow.name in self._workflows:
                    # Deterministic registry behavior: first sorted file wins;
                    # silently shadowing a workflow by filename order is unsafe.
                    self.load_errors[str(path)] = f"duplicate workflow name '{workflow.name}'"
                    continue
                self._workflows[workflow.name] = workflow
            except Exception as exc:
                self.load_errors[str(path)] = str(exc)
                print(f"[WorkflowRegistry] Skipping {path}: {exc}")

    def _load(self, path: Path) -> Workflow:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise WorkflowFormatError(f"invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise WorkflowFormatError("workflow must be a mapping")
        name = str(raw.get("name") or path.stem).strip()
        if not name:
            raise WorkflowFormatError("workflow name is required")
        steps_raw = raw.get("steps")
        if not isinstance(steps_raw, list):
            raise WorkflowFormatError("steps must be a list")
        steps: list[WorkflowStep] = []
        for index, item in enumerate(steps_raw, start=1):
            if not isinstance(item, dict) or not isinstance(item.get("action"), str) or not item["action"].strip():
                raise WorkflowFormatError(f"steps[{index}] must contain a non-empty action")
            args = item.get("args") or {}
            if not isinstance(args, dict):
                raise WorkflowFormatError(f"steps[{index}].args must be a mapping")
            expect = item.get("expect")
            if expect is not None and not isinstance(expect, dict):
                raise WorkflowFormatError(f"steps[{index}].expect must be a mapping")
            recovery = item.get("recovery") or []
            if not isinstance(recovery, list):
                raise WorkflowFormatError(f"steps[{index}].recovery must be a list")
            action_name = item["action"].strip()
            # Backward-compatible canonicalization for older learned YAML.
            action_name = {"scroll_at": "scroll", "hover_at": "move"}.get(action_name, action_name)
            steps.append(
                WorkflowStep(
                    action=action_name,
                    args=dict(args),
                    expect=dict(expect) if expect is not None else None,
                    recovery=recovery,
                    intent=str(item.get("intent") or ""),
                )
            )
        parameters = raw.get("parameters") or {}
        metadata = raw.get("metadata") or {}
        tags = raw.get("tags") or []
        if not isinstance(parameters, dict):
            raise WorkflowFormatError("parameters must be a mapping")
        if not isinstance(metadata, dict):
            raise WorkflowFormatError("metadata must be a mapping")
        if not isinstance(tags, list):
            raise WorkflowFormatError("tags must be a list")
        workflow = Workflow(
            name=name,
            description=str(raw.get("description") or ""),
            environment=str(raw.get("environment") or "desktop"),
            platform=str(raw.get("platform") or "any"),
            tags=[str(x) for x in tags],
            parameters=dict(parameters),
            metadata=dict(metadata),
            steps=steps,
            path=str(path),
        )
        # Keep structurally-readable but invalid workflows available for inspection;
        # matcher/executor both block them using validate_workflow().
        return workflow

    def all(self) -> list[Workflow]:
        return list(self._workflows.values())

    def get(self, name: str) -> Workflow | None:
        return self._workflows.get(name)

    def save(self, workflow: Workflow, path: Path | None = None) -> Path:
        target = Path(path) if path is not None else (Path(workflow.path) if workflow.path else self.root / f"{workflow.name}.yml")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(workflow.to_dict(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        workflow.path = str(target)
        self._workflows[workflow.name] = workflow
        return target

    def names(self) -> Iterable[str]:
        return self._workflows.keys()

    def validation_errors(self, name: str) -> list[str]:
        workflow = self.get(name)
        if workflow is None:
            return [f"unknown workflow '{name}'"]
        return validate_workflow(workflow)

    def approve(self, name: str) -> Workflow:
        """Explicitly approve a valid candidate after human review."""
        workflow = self.get(name)
        if workflow is None:
            raise KeyError(f"unknown workflow: {name}")
        errors = validate_workflow(workflow)
        if errors:
            raise WorkflowFormatError("workflow cannot be approved: " + "; ".join(errors))
        workflow.metadata["status"] = "approved"
        self.save(workflow)
        return workflow
