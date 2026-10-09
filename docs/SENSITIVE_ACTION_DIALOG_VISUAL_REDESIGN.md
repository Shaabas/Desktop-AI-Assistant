# Sensitive-action confirmation: visual-only redesign

The sensitive-action confirmation now uses the same vertical card treatment as the existing workflow-approval card:

- Dark translucent card background, amber border, 12px rounded corners, and matching padding/spacing.
- The existing reason text is displayed at the top and wraps across lines as needed.
- Existing `Deny` and `Allow` buttons are placed in a shared horizontal row and expand to use the card width.
- The HUD expands while the card is present and returns to its original height and bottom position when it closes.

## Intentionally unchanged

- The displayed reason string and its existing fallback, `JARVIS needs your confirmation.`
- Button labels (`Deny`, `Allow`) and callback meanings (`no`, `yes`).
- The sensitive-confirmation response queue and D-Bus methods.
- The `WAITING_CONFIRMATION` assistant state and confirmation status text.
- Existing timeout, cancellation, and fail-closed behavior in the Python/D-Bus flow.
- The workflow-approval dialog and its separate response queue.

No new title, fields, warning copy, or message preview is added. This is only a visual/layout change.

## Validation

`tests/unit/test_sensitive_dialog_visual.py` guards the visual layout and verifies the existing text/decision contract. Run `python -m pytest -q` from the project root. A real GNOME Shell session should still be used to confirm appearance and click behavior; use `gnome-extension/deskwright@zeticle.com/check-syntax.sh` for the Shell-native parser check when `gjs` is installed.
