# JARVIS Interaction Model

## Linux / GNOME

The GNOME Shell extension is the only user-facing JARVIS surface on Linux. It is intentionally small and bottom-anchored:

- one status/information line
- one text-entry line
- one microphone button

There is no conversation drawer, activity drawer, or large centered HUD in the active Linux UI.

### Hotkey

The user-configured GNOME shortcut runs `python main.py --hotkey`.

- No active session: open the GNOME assistant surface.
- Active session, no task: close the surface.
- Active task: cancel the task, stop audio/input, engage the Deskwright halt barrier, release execution, then close the surface.

The JARVIS daemon remains alive in the background; the hotkey toggles the user session/task, not the daemon process.

### Voice

OpenWakeWord and Whisper are owned by Python's shared `VoiceEngine`, the same path used by the legacy HUD. The GNOME microphone button only requests a voice capture from that engine; the extension does not run a second STT implementation.

Wake-word detection can open the session automatically and immediately capture a voice command. When a session is already open, microphone capture is explicitly requested by the microphone control. Text and voice commands enter the same command queue.

### TTS

Python owns TTS. A response remains in the active task lifecycle while Piper/Edge-TTS/espeak speaks. The global hotkey can interrupt TTS together with the computer-use task.

### Computer-use isolation

JARVIS does not add a custom hide/show cycle around screenshots. Deskwright uses the native GNOME Shell screenshot implementation; the JARVIS surface is made non-reactive during computer use so pointer events pass through it.

While JARVIS is thinking/working/speaking, the small surface is also non-reactive. A computer-use click therefore passes through to the application underneath instead of interacting with the JARVIS UI.

During `LISTENING`/confirmation states the input field and required controls are reactive so the human can type, press Enter, or use the microphone/confirmation controls.

### Command ownership

The command queue has its own worker and is independent of the audio loop. Typed commands therefore execute even when OpenWakeWord/STT is unavailable or idle. The voice loop is responsible only for wake-word detection and explicit microphone capture.
