"""
src/jarvis/app/application.py — Main Application Entry Point & Core Lifecycle Manager.
Coordinates PySide6 HUD UI, Voice Engine STT/Wake-Word Thread, AgentController,
and Typed/Voice Command Event Dispatching.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from src.jarvis.agent.agent import AgentController
from src.jarvis.input.speech import VoiceEngine
from src.jarvis.models.llm import get_model_provider
from src.jarvis.ui.window import JarvisHUDWindow
from src.jarvis.utils.terminal import suppress_library_warnings

FOLLOW_UP_TIMEOUT: float = 8.0


class JarvisApp:
    def __init__(self, start_minimized: bool = False) -> None:
        suppress_library_warnings()
        self.qt_app = QApplication.instance() or QApplication(sys.argv)
        self.qt_app.setApplicationName("JARVIS Desktop Assistant")

        self.ui_window = JarvisHUDWindow(start_minimized=start_minimized)
        from src.jarvis.domain.mode_manager import get_mode_manager
        from src.jarvis.utils.mode import is_online_mode, is_physical_network_available
        
        # Initial mode sync on startup
        has_net = is_physical_network_available()
        online_pref = is_online_mode()
        init_mode = "offline" if not has_net else ("cloud" if online_pref else "local")
        get_mode_manager().set_mode(init_mode)

        self.agent = AgentController(ui_callback=self._on_agent_ui_update)

        self.voice_engine = VoiceEngine()
        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None

        self._command_event = threading.Event()
        self._command_queue: list[str] = []
        self._command_queue_lock = threading.Lock()

        self._wake_event = threading.Event()
        self._is_active_task = threading.Event()
        self._current_state: str = "STANDBY"
        self._last_abort_time: float = 0.0
        self._hotkey_handler_lock = threading.Lock()

        self.ui_window.on_text_command = self.submit_command
        self.ui_window.on_shutdown = self.shutdown
        self.ui_window.on_hotkey = self.handle_wake_or_interrupt

    def _set_state(self, state: str) -> None:
        """Threadsafe state tracker and UI HUD synchronizer."""
        self._current_state = state
        self.ui_window.set_state(state)

    def _on_agent_ui_update(self, text: str) -> None:
        self.ui_window.write_log(text)

    def submit_command(self, text: str) -> None:
        cmd = text.strip()
        if not cmd:
            return
        with self._command_queue_lock:
            self._command_queue.append(cmd)
            self._command_event.set()

    def handle_wake_or_interrupt(self) -> None:
        """
        Universal dual-role hotkey handler:
        - When JARVIS is active (LISTENING, FOLLOW_UP, THINKING, ACTING, SPEAKING, task running, or TTS playing):
          Acts as an ABSOLUTE KILLSWITCH: halts all execution, silences audio, releases motor controls,
          and immediately drops state back to STANDBY.
        - When JARVIS is in pure idle STANDBY (doing nothing):
          Acts as a WAKE ALTERNATIVE: starts listening immediately for the user's command.
        """
        now = time.monotonic()
        with self._hotkey_handler_lock:
            # 1. Post-abort cooldown: Ignore trailing key-repeat / key-release bounces within 500ms
            if (now - self._last_abort_time) < 0.50:
                return

            is_speaking = bool(hasattr(self.agent, "tts") and self.agent.tts and self.agent.tts.is_speaking())
            is_active_flag = self._is_active_task.is_set()
            cur_state = getattr(self, "_current_state", "STANDBY")
            is_busy = (cur_state != "STANDBY") or is_active_flag or is_speaking

            if is_busy:
                # ====== ABSOLUTE KILLSWITCH ======
                self._last_abort_time = now
                print(f"\n[JarvisApp] 🛑 ABSOLUTE KILLSWITCH TRIGGERED (State was: '{cur_state}', ActiveTask: {is_active_flag}, TTS: {is_speaking})")

                # 1. Signal task abort controller immediately (halts workflows, loops, tools)
                if hasattr(self.agent, "abort_current_task"):
                    self.agent.abort_current_task(reason="user_hotkey")

                # 2. Cancel and discard any active microphone recording / transcription
                if hasattr(self.voice_engine, "cancel_recording"):
                    self.voice_engine.cancel_recording()

                # 3. Terminate TTS audio playback immediately
                if hasattr(self.agent, "tts") and self.agent.tts:
                    self.agent.tts.stop()

                # 4. Emergency hardware motor release (release stuck mouse buttons & modifier keys)
                from src.jarvis.agent.action_executor import ActionExecutor
                ActionExecutor.emergency_motor_release()

                # 5. Clear pending command events and queues
                with self._command_queue_lock:
                    self._command_queue.clear()
                    self._command_event.clear()
                self._wake_event.clear()
                self._is_active_task.clear()

                # 6. Reset UI & disable click-through
                if hasattr(self.ui_window, "set_click_through"):
                    self.ui_window.set_click_through(False)

                self._set_state("STANDBY")
                self.ui_window.set_subtitle("Task cancelled.")
            else:
                # ====== WAKE ALTERNATIVE ======
                print("\n[JarvisApp] ⚡ Hotkey WAKE triggered from STANDBY.")
                if hasattr(self.agent, "abort_controller") and self.agent.abort_controller:
                    self.agent.abort_controller.reset()
                self._set_state("LISTENING")
                self.ui_window.set_subtitle("Listening...")
                self._wake_event.set()

    def start(self) -> None:
        self.stop_event.clear()
        self.worker_thread = threading.Thread(target=self._voice_loop, daemon=True, name="JarvisVoiceLoop")
        self.worker_thread.start()

    def shutdown(self) -> None:
        print("[JarvisApp] Shutting down...")
        self.stop_event.set()
        self._command_event.set()
        self._wake_event.set()
        if self.agent and hasattr(self.agent, "tts"):
            self.agent.tts.stop()

    def _voice_loop(self) -> None:
        print("[JarvisApp] Voice loop started.")
        while not self.stop_event.is_set():
            # Check for typed commands first
            if self._command_event.is_set():
                cmd = None
                with self._command_queue_lock:
                    if self._command_queue:
                        cmd = self._command_queue.pop(0)
                    if not self._command_queue:
                        self._command_event.clear()

                if cmd:
                    self._process_command_pipeline(cmd)
                    continue

            self._set_state("STANDBY")
            wake_detected = self.voice_engine.listen_for_wake_word(
                stop_event=self.stop_event,
                interrupt_event=self._command_event,
                wake_event=self._wake_event,
                on_level=self.ui_window.set_audio_level,
            )

            if not wake_detected or self.stop_event.is_set():
                continue

            self._set_state("LISTENING")
            abort_token = self.agent.abort_controller.abort_event if hasattr(self.agent, "abort_controller") else None
            utterance = self.voice_engine.stream_and_transcribe(
                on_partial_text=self.ui_window.set_subtitle,
                on_level=self.ui_window.set_audio_level,
                abort_event=abort_token,
            )

            if not utterance:
                self._set_state("STANDBY")
                continue

            self._process_command_pipeline(utterance)

            # Follow-up listening loop (8s window)
            follow_up_start = time.time()
            while not self.stop_event.is_set() and (time.time() - follow_up_start < FOLLOW_UP_TIMEOUT):
                if self._command_event.is_set() or (hasattr(self.agent, "abort_controller") and self.agent.abort_controller.is_aborted()):
                    break

                self._set_state("FOLLOW_UP")
                follow_up_utt = self.voice_engine.stream_and_transcribe(
                    on_partial_text=self.ui_window.set_subtitle,
                    on_level=self.ui_window.set_audio_level,
                    wait_timeout=2.0,
                    abort_event=abort_token,
                )

                if follow_up_utt:
                    self._process_command_pipeline(follow_up_utt)
                    follow_up_start = time.time()
                else:
                    break

    def _get_conversational_acknowledgment(self, command: str) -> Optional[str]:
        """Generates a natural, intelligent conversational micro-acknowledgment."""
        return "Right away, sir."

    def _process_command_pipeline(self, command: str) -> None:
        print(f"[JarvisApp Pipeline] Executing command: '{command}'")
        self._is_active_task.set()
        self._set_state("THINKING")
        self.ui_window.set_subtitle(command)

        # 1. Parallel Conversational Presence: Speak immediate intelligent acknowledgment
        ack_phrase = self._get_conversational_acknowledgment(command)
        if ack_phrase and hasattr(self.agent, "tts") and self.agent.tts:
            self.agent.tts.speak_async(ack_phrase)

        is_computer_use = any(
            k in command.lower() for k in [
                "open", "launch", "draw", "circle", "presentation", "ppt", "slide",
                "write", "calc", "spreadsheet", "click", "pinta", "impress", "document", "gimp"
            ]
        )
        if is_computer_use and hasattr(self.ui_window, "set_click_through"):
            self.ui_window.set_click_through(True)

        try:
            response = self.agent.process_request(command)
        finally:
            if is_computer_use and hasattr(self.ui_window, "set_click_through"):
                self.ui_window.set_click_through(False)

        # Check if aborted before speaking
        if hasattr(self.agent, "abort_controller") and self.agent.abort_controller.is_aborted():
            self._is_active_task.clear()
            self._set_state("STANDBY")
            self.ui_window.set_subtitle("Task cancelled.")
            return

        self._set_state("SPEAKING")
        spoken_response = response
        self.ui_window.set_subtitle(spoken_response)

        # Speak response cleanly without sound barge-in
        if spoken_response and hasattr(self.agent, "tts") and self.agent.tts:
            tts = self.agent.tts
            tts.speak_async(spoken_response)
            while tts.is_speaking() and not self.stop_event.is_set():
                if self._command_event.is_set() or (hasattr(self.agent, "abort_controller") and self.agent.abort_controller.is_aborted()):
                    tts.stop()
                    break
                time.sleep(0.05)

        self._is_active_task.clear()
        self._set_state("STANDBY")


def main() -> None:
    if "--trigger" in sys.argv or "--wake-or-interrupt" in sys.argv:
        from src.jarvis.input.hotkey import send_hotkey_trigger
        ok = send_hotkey_trigger()
        sys.exit(0 if ok else 1)

    if "--prompt" in sys.argv:
        idx = sys.argv.index("--prompt")
        if idx + 1 < len(sys.argv):
            prompt_text = sys.argv[idx + 1]
            from src.jarvis.agent.agent import AgentController
            controller = AgentController()
            print(f"\n[JARVIS CLI] Processing: '{prompt_text}'")
            resp = controller.handle_user_prompt(prompt_text)
            print(f"\n[JARVIS Response]\n{resp}\n")
            return

    if "--cli" in sys.argv:
        from src.jarvis.agent.agent import AgentController
        controller = AgentController()
        print("\n" + "=" * 50 + "\n🤖 JARVIS Interactive Terminal Mode (Type 'exit' to quit)\n" + "=" * 50)
        while True:
            try:
                user_input = input("\nJARVIS ❯ ").strip()
                if not user_input:
                    continue
                if user_input.lower() in {"exit", "quit", "q"}:
                    print("Goodbye, sir.")
                    break
                resp = controller.handle_user_prompt(user_input)
                print(f"\n[JARVIS]: {resp}")
            except (KeyboardInterrupt, EOFError):
                print("\nSession ended.")
                break
        return

    start_min = "--minimized" in sys.argv
    app = JarvisApp(start_minimized=start_min)
    app.start()
    sys.exit(app.qt_app.exec())

if __name__ == "__main__":
    main()
