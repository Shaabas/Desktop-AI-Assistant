# GNOME workflow approval dialog

When Gemini Computer Use finishes a task and JARVIS compiles a learned workflow candidate, the daemon now asks the user to approve or decline the candidate in the existing Deskwright GNOME Shell HUD.

## Dialog behavior

- The card shows the workflow name, task description, number of recorded steps, a short action-intent summary, and a warning that recorded coordinates/waits may be fragile.
- **Approve workflow** promotes the candidate through `WorkflowRegistry.approve()`. That function revalidates the workflow before setting `metadata.status` to `approved`.
- **Decline** leaves the candidate unapproved and unavailable to automatic workflow matching.
- Only schema-valid candidates show the approve/decline dialog. Invalid candidates stay disabled and the task result reports validation errors.
- The approval dialog uses a dedicated D-Bus response queue that includes the workflow name. It cannot be confused with the existing Allow/Deny responses used for sensitive computer actions.
- If the dialog is unavailable, the request times out, JARVIS is halted, or the assistant session closes, the candidate remains disabled.
- The default timeout is 120 seconds and can be changed with `workflow_approval_timeout_seconds` in `config/settings.json`.

## Install on Fedora/GNOME

From the project root:

```bash
gnome-extension/install.sh
```

The extension's D-Bus API changed. GNOME Shell does not reliably reload edited extension modules on Wayland within an existing session. After installing, **log out of GNOME and log back in** (or reboot) so the running Shell loads the new extension build. Then verify the active extension:

```bash
gdbus call --session --dest com.zeticle.deskwright \
  --object-path /com/zeticle/deskwright \
  --method com.zeticle.deskwright.Ping
```

The returned JSON should include build `2026-10-09.1` and methods `ShowWorkflowApproval`, `ClearWorkflowApproval`, and `GetPendingWorkflowApprovalResponses`.

Start JARVIS normally:

```bash
python main.py
```

After a computer-use task that produces a valid candidate, JARVIS should show the card in its HUD. Declining or waiting for the timeout keeps the candidate disabled; approving enables it for future workflow selection. Approval checks schema validity, not real-world reliability, so review the listed actions before approving.

## Verification

The Python unit tests cover the bridge method contract, matching workflow-name decisions, approval promotion, and decline behavior. They do not emulate GNOME Shell's rendering. Test the extension on a real GNOME Wayland session after installation.
