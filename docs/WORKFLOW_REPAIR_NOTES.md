# JARVIS workflow repair notes

## What changed

This repair pass strengthens the existing workflow implementation rather than introducing a second workflow system.

- Trace compilation now maps positional computer-use call arguments to named YAML fields, normalizes recorded coordinates to a 0–1000 range, canonicalizes old action names, and skips malformed or unsupported trace steps with explicit warnings.
- Trace-learned workflows and LLM-adapted workflows are saved as `unvalidated` candidates. Candidates are excluded from automatic matching until they are reviewed and explicitly approved.
- Workflow loading, schema validation, action arguments, required parameters, expectation types, and workflow runtime limits are checked before side effects.
- The matcher only considers runnable, valid workflows and is more conservative when multiple workflows have similar scores.
- The executor evaluates the currently supported URL postconditions, supports bounded polling and cancellation, reports `succeeded_verified` separately from `completed_unverified`, and can run only valid, approved workflows.
- If a workflow fails after starting, the router returns a failure/unknown outcome instead of automatically replaying the same user task through Gemini. This avoids duplicating side effects after an uncertain partial completion.
- A local-LLM planner has been added. It only sees task text and compact workflow metadata. It selects from approved candidates and extracts only declared parameters. The default configured Ollama model is `qwen3.5:2b`, while `LOCAL_LLM_MODEL` or `config/settings.json` can override it.
- Workflow selection now filters weak lexical matches before calling the local model. Unparameterized workflows require a stronger match; parameterized workflows can use a lower threshold because user-supplied values may not appear in the workflow name. This avoids spending a long Qwen inference deciding whether a partial workflow such as `open_firefox` matches a larger request such as playing a song.
- The workflow selection call has a configurable timeout (default 20 seconds), and status messages report whether candidates were found, the best match score, when Qwen is consulted, and why JARVIS falls back to Computer Use. Configure `workflow_planner_min_score` and `workflow_selection_timeout_seconds` in `config/settings.json`.
- `python main.py --list-workflows` now shows approval status and schema-validation state. Use `python main.py --workflow-review NAME` to inspect the workflow YAML and validation errors, and `python main.py --workflow-approve NAME` only after reviewing it. Approval enables matching but does not prove that coordinates or behavior remain correct.
- During an LLM-controlled workflow run, the local model is asked to authorize the next fixed YAML step. It cannot reorder steps, create actions, or supply action arguments. On failure, it can select only from recovery actions already declared in the YAML. Invalid, low-confidence, or unavailable model responses stop the controlled run safely.
- API key values in `config/api_keys.json` have been blanked in this repair package. Configure credentials locally using the project's supported configuration mechanism; do not commit secrets.

## Important limitation: text-only local model

Qwen/Ollama is not given screenshots or the accessibility tree by this implementation. The local model can select a workflow, bind parameters from user text, authorize the next step, and choose among predeclared recovery actions. Desktop actions remain fixed in YAML and are performed by the computer adapter.

The current executor supports URL-based postconditions (`url_contains`, `url_equals`, and `url_startswith`). A step without a supported postcondition is reported as `completed_unverified`, not as proven successful. This repair does **not** add semantic GNOME/AT-SPI selectors or visual-state verification; those are a separate adapter/perception phase.

When the local planner is configured (as in the product controller) but the local model is unavailable or declines the task, the router does not bypass it with lexical matching. It routes to the general computer-use path instead; only the compatibility path for a router explicitly constructed without a local planner uses lexical workflow matching. The workflow deadline is checked again after model authorization so a slow inference cannot authorize an action after the time budget has expired.

## Candidate review and approval

Learned candidates are placed under `workflows/learned/` with `metadata.status: unvalidated`. Inspect each candidate's `steps`, `metadata.trace_warnings`, coordinate assumptions, and outcome before approval. Approval checks the workflow schema, but it does not prove the workflow is behaviorally correct; test it in a safe environment first.

Review and approve from the project root:

```bash
python main.py --list-workflows
python main.py --workflow-review open_firefox_and_play_a_song
python main.py --workflow-approve open_firefox_and_play_a_song
```

Use the candidate name that is actually listed on your machine. Approval changes its metadata status to `approved`; it is intentionally not automatic for every trace because a syntactically valid coordinate macro may still be unsafe or brittle. Do not approve a candidate whose YAML is incomplete or whose side effects have not been reviewed.

## Validation performed

From the project root:

```bash
python -m pytest -q
python -m compileall -q src/jarvis tests
```

After the routing fix, the suite reports 41 passing tests and Python compilation succeeds. Device-specific GNOME integration and real Qwen/Ollama inference should still be tested on the target Fedora desktop before using workflows for consequential tasks.

## Credential note

The original uploaded archive contained a non-empty Gemini API credential. This output package removes that value, but packaging cannot revoke the provider-side credential. Rotate/revoke it in the provider dashboard and check any other copies, shell history, backups, or repository history where it may have been stored.
