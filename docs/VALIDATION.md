# Validation

Last validation pass:

- 21/21 Python unit tests pass with `pytest -q`.
- `python -m compileall -q src main.py` passes.
- `node --check gnome-extension/deskwright@zeticle.com/indicator.js` passes.
- `node --check gnome-extension/deskwright@zeticle.com/dbus.js` passes.
- The D-Bus XML embedded in `dbus.js` parses successfully; all 37 methods are unique and the semantic assistant-surface methods are present.
- No active imports of the retired JARVIS perception / closed-loop / legacy desktop-control stack remain.
- The Google Computer Use source tree remains under `src/jarvis/computer_use/` and was not rewritten.

## Not validated in this environment

The validation environment does not provide a GNOME Shell/GJS session, a live
Wayland compositor, microphone hardware, the Deskwright Python package with its
full desktop dependencies, or a live Gemini API session. The following therefore
still require a real Fedora/GNOME session test:

1. Enable the extension and confirm `com.zeticle.deskwright` registers.
2. Test `ActivateAssistant` from `gdbus` directly.
3. Configure the GNOME Settings custom shortcut to run `python main.py --hotkey`.
4. Verify hotkey open / close / active-task cancellation behavior.
5. Verify typing, wake-word capture, and the extension microphone all enter the same JARVIS command pipeline.
6. Verify a protected Computer Use action displays the in-surface confirmation card.
7. Verify a hotkey cancellation keeps the compositor halt engaged until the CUA task has unwound, then releases it.
