# JARVIS Workflows

These YAML workflows are the reusable task-memory layer of JARVIS.

## Runtime model

- The local Ollama model receives the user task and compact, text-only metadata for approved workflow candidates. It chooses a matching workflow and binds explicit parameters. It is not given screenshots or an accessibility tree.
- The workflow action broker validates every action and executes the fixed YAML sequence through the selected computer adapter. The model cannot invent a new action sequence while dispatching a workflow.
- Optional `expect` blocks currently support only URL predicates (`url_contains`, `url_equals`, `url_startswith`) because the current computer adapters do not expose a dependable, uniform text/semantic UI observation interface.
- Workflows without expectations may execute, but their result is reported as `completed_unverified`, not verified success.

## Approval rules

Hand-authored workflows outside `learned/` and `adapted/` can be executed when they pass validation and are not marked as drafts. Trace-derived and LLM-adapted workflows are candidates and are not automatically executed. Review their arguments, parameters and side effects, then explicitly approve them with `WorkflowRegistry.approve(name)` or set `metadata.status: approved` after review. Invalid workflows cannot be approved.

## Learning

Gemini Computer Use traces are compiled to named-argument YAML and saved under `workflows/learned/`. Invalid or incomplete trace actions are skipped and listed in `metadata.trace_warnings` instead of being turned into empty actions. Review those warnings and the remaining sequence carefully; these workflows remain unvalidated until reviewed and approved.

## Supported actions

The schema currently supports click variants, text entry, key presses/combinations, scrolling, waits, navigation, browser open/search/back/forward, screenshots and drag-and-drop. Coordinates are stored in normalized 0–1000 screen units. A local model is not expected to infer screen locations from pixels; workflow coordinates must come from a reviewed trace or an explicit author.
