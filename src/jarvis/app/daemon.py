from __future__ import annotations

import signal
import threading
import time
from typing import Optional

from ..agent.controller import JarvisController
from ..gnome.bridge import GnomeShellBridge
from ..ui.gnome_surface import GnomeAssistantSurface
from ..input.hotkey import remove_pid_file, write_pid_file
from ..ui.surface import AssistantState
from ..ui.status import summarize_hud_line

MAX_PENDING_COMMANDS = 8
MAX_COMMAND_CHARACTERS = 32768
WORKER_SHUTDOWN_TIMEOUT_SECONDS = 1.5


class _NullTTS:
    def speak_async(self, _text: str) -> None:
        return None

    def stop(self) -> None:
        return None


class JarvisDaemon:
    """Persistent JARVIS runtime; platform UI is a separate assistant surface."""

    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.command_event = threading.Event()
        self._commands: list[str] = []
        self._lock = threading.Lock()
        self._worker_threads: list[threading.Thread] = []
        self.bridge = GnomeShellBridge()
        self.surface = GnomeAssistantSurface(self.bridge)

        try:
            from ..models.tts import TTSEngine
            self.tts = TTSEngine(ui_callback=self._status)
        except Exception as exc:
            print(f"[JARVIS] TTS subsystem unavailable: {exc}")
            self.tts = _NullTTS()

        self.controller = JarvisController(
            status_callback=self._status,
            confirmation_callback=self._confirm,
            workflow_approval_callback=self._approve_workflow,
        )
        self.voice = None
        try:
            from ..input.speech import VoiceEngine
            self.voice = VoiceEngine()
        except Exception as exc:
            print(f"[JARVIS] Voice subsystem unavailable: {exc}")
            self.voice = None

        self._hotkey_event = threading.Event()
        self._active = threading.Event()          # command / TTS currently executing
        self._session_active = threading.Event()  # user-facing assistant surface is open
        self._cancel_event = threading.Event()
        self._hotkey_lock = threading.Lock()
        self._last_hotkey_mono = 0.0
        self._voice_capture_event = threading.Event()
        self._voice_capture_active = threading.Event()

    def _status(self, message: str) -> None:
        message = str(message or "").strip()
        if not message:
            return
        print(f"[JARVIS] {message}")
        if not self.bridge.available or not self._session_active.is_set():
            return

        low = message.lower()
        if "checking workflows" in low or "thinking" in low or "planning" in low or "analyzing" in low:
            self.surface.set_state(AssistantState.THINKING.value, message)
        elif "working" in low or "connecting" in low or "generating response" in low:
            self.surface.set_state(AssistantState.WORKING.value, message)
        else:
            self.surface.set_activity(summarize_hud_line(message))

    def _confirm(self, reason: str) -> bool:
        if self.bridge.available and self._session_active.is_set():
            return self.bridge.wait_for_confirmation(reason)
        return self.controller._default_confirmation(reason)

    def _approve_workflow(self, workflow) -> bool:
        """Use GNOME's explicit workflow approval card in daemon mode."""
        if not (self.bridge.available and self._session_active.is_set()):
            print("[JARVIS] Workflow approval UI is unavailable; leaving candidate disabled.")
            return False
        return self.bridge.wait_for_workflow_approval(
            workflow,
            timeout=float(self.controller.settings.get("workflow_approval_timeout_seconds", 120.0)),
        )

    def _enqueue(self, command: str) -> None:
        command = command.strip()
        if not command:
            return
        if len(command) > MAX_COMMAND_CHARACTERS:
            print(
                f"[JARVIS] Request exceeds the {MAX_COMMAND_CHARACTERS}-character limit; "
                "it was not queued."
            )
            return
        with self._lock:
            if len(self._commands) >= MAX_PENDING_COMMANDS:
                print(
                    f"[JARVIS] Command queue is full ({MAX_PENDING_COMMANDS} pending); "
                    "discarding the newest request to keep memory bounded."
                )
                return
            self._commands.append(command)
            self.command_event.set()
        if self.voice is not None:
            try:
                self.voice.cancel_recording()
            except Exception:
                pass

    def _drain_one(self) -> Optional[str]:
        with self._lock:
            if not self._commands:
                self.command_event.clear()
                return None
            command = self._commands.pop(0)
            if not self._commands:
                self.command_event.clear()
            return command

    def _open_session(self, *, start_voice: bool = False) -> bool:
        if not self.bridge.available:
            return False
        self._cancel_event.clear()
        try:
            self.bridge.clear_halt()
        except Exception:
            pass
        opened = self.surface.activate()
        if opened:
            self._session_active.set()
            self.surface.set_state(AssistantState.LISTENING.value, "Listening…")
            if start_voice and self.voice is not None:
                try:
                    self.voice.cancel_recording()
                except Exception:
                    pass
                self._voice_capture_event.set()
        return opened

    def _close_session(self) -> None:
        self._session_active.clear()
        self._voice_capture_event.clear()
        if self.voice is not None:
            try:
                self.voice.cancel_recording()
            except Exception:
                pass
        with self._lock:
            self._commands.clear()
            self.command_event.clear()
        try:
            self.surface.clear_confirmation()
            self.surface.close()
        except Exception:
            pass

    def _stop_active_work(self, close_session: bool = True) -> None:
        self._cancel_event.set()
        try:
            self.tts.stop()
        except Exception:
            pass
        if self.voice is not None:
            try:
                self.voice.cancel_recording()
            except Exception:
                pass
        if self.bridge.available:
            try:
                self.surface.set_state(AssistantState.STOPPING.value, "Stopping…")
                self.bridge.halt()
            except Exception:
                pass
        self._voice_capture_event.clear()
        if close_session:
            # The user-facing surface closes immediately. The cancellation
            # barrier remains engaged until the running task unwinds.
            if not self._active.is_set():
                try:
                    self.bridge.clear_halt()
                except Exception:
                    pass
            self._close_session()

    def _on_hotkey_signal(self, _signum, _frame) -> None:
        self._hotkey_event.set()

    def _hotkey_loop(self) -> None:
        while not self.stop_event.is_set():
            if not self._hotkey_event.wait(0.5):
                continue
            self._hotkey_event.clear()
            now = time.monotonic()
            if now - self._last_hotkey_mono < 0.25:
                continue
            self._last_hotkey_mono = now
            self._handle_hotkey()

    def _handle_hotkey(self) -> None:
        with self._hotkey_lock:
            # One key has one semantic meaning: open when closed, close when
            # open/idle, and cancel+close when a task is executing.
            if self._active.is_set():
                self._stop_active_work(close_session=True)
                return
            if self._session_active.is_set():
                self._close_session()
                return
            if not self._open_session():
                print("[JARVIS] Could not activate the configured assistant surface.")

    def _handle_control_event(self, event: str) -> None:
        if event == "close":
            with self._hotkey_lock:
                if self._active.is_set():
                    self._stop_active_work(close_session=True)
                else:
                    self._close_session()
            return
        if event == "voice":
            if not self._session_active.is_set():
                if not self._open_session(start_voice=False):
                    return
            if self._active.is_set():
                return
            try:
                self.voice.cancel_recording() if self.voice is not None else None
            except Exception:
                pass
            self.surface.set_state(AssistantState.LISTENING.value, "Listening…")
            self._voice_capture_event.set()

    def _poll_gnome_input(self) -> None:
        while not self.stop_event.is_set():
            for event in self.bridge.get_pending_control_events():
                self._handle_control_event(event)

            # Avoid consuming and discarding queued user instructions while
            # the assistant surface is closed. Gemini owns live steering only
            # during a running CUA task; otherwise the daemon drains commands
            # from an active assistant session.
            if self._session_active.is_set() and not self._active.is_set():
                for command in self.bridge.get_pending_instructions():
                    self._enqueue(command)
            time.sleep(0.12)

    def _start_worker(self, target, name: str) -> None:
        thread = threading.Thread(target=target, daemon=True, name=name)
        self._worker_threads.append(thread)
        thread.start()

    def _capture_voice_command(self) -> None:
        if self.voice is None or not self._session_active.is_set() or self._active.is_set():
            return
        self._voice_capture_active.set()
        try:
            self.surface.set_state(AssistantState.LISTENING.value, "Listening…")
            utterance = self.voice.stream_and_transcribe(abort_event=self._cancel_event)
            if self._cancel_event.is_set() or not self._session_active.is_set():
                return
            if utterance:
                self.surface.add_message("user", utterance)
                self._enqueue(utterance)
            else:
                self.surface.set_state(AssistantState.LISTENING.value, "Listening…")
        finally:
            self._voice_capture_active.clear()

    def _command_worker_loop(self) -> None:
        """Execute queued text/voice commands independently of the audio loop."""
        while not self.stop_event.is_set():
            if not self.command_event.wait(0.25):
                continue
            command = self._drain_one()
            if command:
                self._run_command(command)

    def _voice_loop(self) -> None:
        """Own OpenWakeWord and user-requested microphone capture only."""
        if self.voice is None:
            return
        while not self.stop_event.is_set():
            # Once the assistant surface is open, microphone capture is
            # explicitly user-driven by the UI. Do not run the wake-word
            # detector over the active session.
            if self._session_active.is_set():
                if self._voice_capture_event.wait(0.25):
                    self._voice_capture_event.clear()
                    self._capture_voice_command()
                continue

            # While the surface is closed, OpenWakeWord can wake the same
            # assistant session that the global hotkey opens.
            wake_detected = self.voice.listen_for_wake_word(
                self.stop_event,
            )
            if not wake_detected or self.stop_event.is_set():
                continue

            if self.bridge.available and not self._session_active.is_set():
                if not self._open_session(start_voice=False):
                    continue
            self._capture_voice_command()

    def _run_command(self, command: str) -> None:
        command = command.strip()
        if not command:
            return
        self._cancel_event.clear()
        self._active.set()
        try:
            if self.bridge.available and self._session_active.is_set():
                self.surface.set_state(AssistantState.THINKING.value, "Thinking…")
            result = self.controller.process_request(command, cancel_event=self._cancel_event)
            if self._cancel_event.is_set():
                return
            if result:
                summary = summarize_hud_line(result)
                if self.bridge.available and self._session_active.is_set():
                    self.surface.set_state(AssistantState.SPEAKING.value, summary or "Speaking…")
                # Keep TTS on the same lifecycle as the legacy HUD: it is part
                # of the active task and is interrupted by the global hotkey.
                self.tts.speak(result)
        except Exception as exc:
            if self._cancel_event.is_set():
                return
            print(f"[JARVIS] Error: {exc}")
            if self.bridge.available and self._session_active.is_set():
                self.surface.set_state(AssistantState.ERROR.value, summarize_hud_line(str(exc)) or "Something went wrong")
        finally:
            cancelled = self._cancel_event.is_set()
            self._active.clear()
            if cancelled:
                try:
                    self.tts.stop()
                except Exception:
                    pass
                try:
                    self.bridge.clear_halt()
                except Exception:
                    pass
                self._close_session()
                self._cancel_event.clear()
            elif not self.stop_event.is_set() and self._session_active.is_set():
                self.surface.set_state(AssistantState.LISTENING.value, "Listening…")

    def start(self) -> None:
        if any(thread.is_alive() for thread in self._worker_threads):
            raise RuntimeError("JARVIS daemon workers are already running")
        self._worker_threads = []
        self.stop_event.clear()
        if self.bridge.available:
            try:
                self.bridge.clear_halt()
            except Exception:
                pass
        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, self._on_hotkey_signal)
        write_pid_file()
        self._start_worker(self._hotkey_loop, "JarvisHotkeySignalLoop")
        if self.bridge.available:
            self._start_worker(self._poll_gnome_input, "JarvisGnomePoller")
        self._start_worker(self._command_worker_loop, "JarvisCommandWorker")
        if self.voice:
            self._start_worker(self._voice_loop, "JarvisVoiceLoop")

        signal.signal(signal.SIGINT, lambda *_: self.stop())
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
        print("[JARVIS] Daemon running. Press Ctrl+C to stop.")
        try:
            while not self.stop_event.is_set():
                time.sleep(0.5)
        finally:
            self.stop()

    def stop(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        remove_pid_file()
        self._cancel_event.set()
        try:
            self.tts.stop()
        except Exception:
            pass
        if self._active.is_set():
            self._cancel_event.set()
        self._close_session()
        try:
            self.bridge.clear_indicator()
            self.bridge.clear_halt()
        except Exception:
            pass

        current = threading.current_thread()
        for thread in self._worker_threads:
            if thread is current:
                continue
            thread.join(timeout=WORKER_SHUTDOWN_TIMEOUT_SECONDS)
        alive = [thread.name for thread in self._worker_threads if thread.is_alive()]
        if alive:
            print("[JARVIS] Warning: workers did not stop before shutdown timeout: " + ", ".join(alive))
