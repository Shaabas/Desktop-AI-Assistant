"""Dependency-light safety confirmation preflight for Computer Use batches."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any


def preflight_safety_confirmations(
    function_calls: Sequence[Any],
    request_confirmation: Callable[[dict[str, Any]], str],
    *,
    on_error: Callable[[Exception], None] | None = None,
) -> set[int] | None:
    """Authorize every flagged call before allowing any call in the batch to run.

    Returns the indices whose confirmation was explicitly approved. Returns None
    for any denial, unknown result, malformed request, or confirmation failure.
    This module deliberately has no dependency on the Gemini SDK so its critical
    safety behavior can be covered by offline unit tests.
    """
    approved_indices: set[int] = set()
    for index, function_call in enumerate(function_calls):
        args = getattr(function_call, "args", None) or {}
        if not isinstance(args, Mapping) or "safety_decision" not in args:
            continue
        safety = args.get("safety_decision")
        if safety is None:
            continue
        if not isinstance(safety, dict):
            if on_error:
                on_error(ValueError("Malformed safety_decision payload"))
            return None
        try:
            decision = request_confirmation(safety)
        except Exception as exc:
            if on_error:
                on_error(exc)
            return None
        if decision != "CONTINUE":
            return None
        approved_indices.add(index)
    return approved_indices
