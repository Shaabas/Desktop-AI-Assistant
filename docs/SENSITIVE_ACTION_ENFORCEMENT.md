# Sensitive-action confirmation enforcement

This patch makes the existing confirmation UI an execution barrier rather than a best-effort prompt.

## Enforcement rules

- For each Gemini Computer Use response, JARVIS scans the entire function-call batch for `safety_decision` requests before dispatching any call from that response. All requested confirmations must be explicitly approved before the batch can begin. If any is denied, malformed, times out, or cannot be displayed, no action from that batch is dispatched.
- If an action-level text guard requests confirmation, typing is performed only after the callback returns the literal boolean `True`. Denial, exceptions, timeout, and unavailable UI all block the attempted input.
- A denied workflow authorization is terminal for that step; the executor does not attempt recovery actions after a denied confirmation.
- A proxy authorization error stops the remainder of the current Computer Use response. The router reports a blocked result and does not save a partial trace as a successful candidate.
- Confirmation responses carry a per-dialog `request_id`. Only an Allow/Deny response with the active ID is accepted. Responses from older prompts are drained or ignored.
- Ordinary pending instructions such as the text `yes` are never treated as confirmation. Consent must come from the sensitive-action dialog's dedicated buttons.
- If the dialog cannot be shown or its response channel fails, JARVIS fails closed and does not dispatch the gated action.

## Important scope boundary

This patch guarantees ordering for actions for which JARVIS's current security pathways actually request confirmation. It prevents a later confirmation request in a multi-call Gemini response from appearing only after earlier calls in that same response have run. It does not add a new independent visual classifier for every unmarked coordinate click. If the model never marks an action for confirmation and no existing deterministic guard classifies it as sensitive, this patch cannot infer the clicked control's meaning from coordinates alone.

## Tests

The unit suite covers batch preflight, denial and callback failure, proxy fail-closed behavior, workflow recovery suppression, request-ID correlation, stale-response rejection, no use of normal instructions as consent, and the D-Bus signature contract.

On Fedora, also run the GNOME-native extension syntax check before logging out, and verify that the D-Bus extension and Python bridge are installed together. An old extension that does not accept the new request ID will return a failed ShowConfirmation call; JARVIS blocks the action rather than treating the call as approved.
