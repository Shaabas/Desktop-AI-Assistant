from __future__ import annotations

import re
from dataclasses import dataclass

from .registry import WorkflowRegistry
from .schema import Workflow, validate_workflow


@dataclass(slots=True)
class WorkflowMatch:
    workflow: Workflow
    score: float
    parameters: dict[str, object] | None = None


_STOPWORDS = {
    "the", "a", "an", "to", "and", "in", "on", "for", "my", "me", "please", "with", "then", "of",
    "from", "at", "is", "it", "this", "that", "do", "i", "want", "need", "can", "could",
}


def _tokens(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-z0-9]+", text.lower()) if x not in _STOPWORDS and len(x) > 1}


class WorkflowMatcher:
    def __init__(self, registry: WorkflowRegistry, minimum_score: float = 0.45, ambiguity_margin: float = 0.08) -> None:
        self.registry = registry
        self.minimum_score = max(0.0, min(1.0, minimum_score))
        self.ambiguity_margin = max(0.0, ambiguity_margin)

    @staticmethod
    def _score(query_tokens: set[str], workflow: Workflow, query: str) -> float:
        workflow_tokens = _tokens(workflow.searchable_text())
        if not workflow_tokens:
            return 0.0
        intersection = len(query_tokens & workflow_tokens)
        union = len(query_tokens | workflow_tokens)
        jaccard = intersection / union if union else 0.0
        query_coverage = intersection / len(query_tokens) if query_tokens else 0.0
        score = (0.55 * query_coverage) + (0.45 * jaccard)
        normalized_name = workflow.name.lower().replace("_", " ")
        if normalized_name and normalized_name in query.lower():
            score = min(1.0, score + 0.12)
        return score

    def rank(
        self,
        query: str,
        *,
        environment: str | None = None,
        platform: str | None = None,
        limit: int = 8,
    ) -> list[WorkflowMatch]:
        q = _tokens(query)
        if not q:
            return []
        candidates: list[WorkflowMatch] = []
        for workflow in self.registry.all():
            if not workflow.is_runnable or validate_workflow(workflow):
                continue
            if environment and workflow.environment not in {"any", environment}:
                continue
            if platform and workflow.platform not in {"any", platform}:
                continue
            score = self._score(q, workflow, query)
            if score > 0:
                candidates.append(WorkflowMatch(workflow, score))
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        return candidates[:max(1, limit)]

    def find(self, query: str, *, environment: str | None = None, platform: str | None = None) -> WorkflowMatch | None:
        candidates = self.rank(query, environment=environment, platform=platform)
        if not candidates or candidates[0].score < self.minimum_score:
            return None
        if len(candidates) > 1 and candidates[0].score - candidates[1].score < self.ambiguity_margin:
            return None
        return candidates[0]
