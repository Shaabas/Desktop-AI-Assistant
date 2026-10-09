import os
import signal
from pathlib import Path
import threading

from src.jarvis.input.hotkey import remove_pid_file, send_hotkey_trigger, write_pid_file


class _FakeTTS:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeVoice:
    def __init__(self):
        self.cancelled = False

    def cancel_recording(self):
        self.cancelled = True


class _FakeBridge:
    available = True

    def __init__(self):
        self.actions = []

    def clear_halt(self):
        self.actions.append("clear_halt")

    def halt(self):
        self.actions.append("halt")

    def get_pending_control_events(self):
        return []

    def get_pending_instructions(self):
        return []




class _FakeController:
    def __init__(self):
        self.commands = []

    def process_request(self, command, *, cancel_event=None):
        self.commands.append(command)
        return f"Done: {command}"

class _FakeSurface:
    available = True

    def __init__(self):
        self.actions = []

    def activate(self):
        self.actions.append("activate")
        return True

    def close(self):
        self.actions.append("close")
        return True

    def set_state(self, state, detail=""):
        self.actions.append(("state", state, detail))
        return True

    def add_message(self, role, text):
        self.actions.append(("message", role, text))
        return True

    def set_activity(self, text):
        self.actions.append(("activity", text))
        return True

    def show_confirmation(self, reason):
        self.actions.append(("confirm", reason))
        return True

    def clear_confirmation(self):
        self.actions.append("clear_confirmation")
        return True


def test_pid_file_round_trip(tmp_path: Path):
    path = tmp_path / "jarvis" / "jarvis.pid"
    write_pid_file(12345, path)
    assert path.read_text(encoding="utf-8").strip() == "12345"
    remove_pid_file(path)
    assert not path.exists()


def test_hotkey_signals_existing_process(monkeypatch, tmp_path: Path):
    path = tmp_path / "jarvis.pid"
    path.write_text(str(os.getpid()), encoding="utf-8")
    calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append((pid, sig)))
    assert send_hotkey_trigger(path)
    assert calls == [(os.getpid(), 0), (os.getpid(), signal.SIGUSR1)]


def test_hotkey_falls_back_to_gnome_when_pid_is_stale(tmp_path: Path, monkeypatch):
    path = tmp_path / "jarvis.pid"
    path.write_text("99999999", encoding="utf-8")

    def fake_kill(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", fake_kill)
    calls = []
    monkeypatch.setattr(
        "src.jarvis.input.hotkey._toggle_gnome_directly",
        lambda: calls.append(True) or True,
    )

    assert send_hotkey_trigger(path)
    assert not path.exists()
    assert calls == [True]


def test_hotkey_falls_back_to_gnome_when_no_pid_file(tmp_path: Path, monkeypatch):
    path = tmp_path / "missing.pid"
    calls = []
    monkeypatch.setattr(
        "src.jarvis.input.hotkey._toggle_gnome_directly",
        lambda: calls.append(True) or True,
    )

    assert send_hotkey_trigger(path)
    assert calls == [True]


def _make_daemon(active=False, session=False):
    from src.jarvis.app.daemon import JarvisDaemon

    daemon = JarvisDaemon.__new__(JarvisDaemon)
    daemon._active = threading.Event()
    daemon._session_active = threading.Event()
    daemon._cancel_event = threading.Event()
    daemon._wake_event = threading.Event()
    daemon._voice_capture_event = threading.Event()
    daemon._voice_capture_active = threading.Event()
    daemon._hotkey_lock = threading.Lock()
    daemon._last_hotkey_mono = 0.0
    daemon._commands = []
    daemon._lock = threading.Lock()
    daemon.command_event = threading.Event()
    daemon.stop_event = threading.Event()
    daemon.tts = _FakeTTS()
    daemon.voice = _FakeVoice()
    daemon.bridge = _FakeBridge()
    daemon.surface = _FakeSurface()
    daemon.controller = _FakeController()
    if active:
        daemon._active.set()
    if session:
        daemon._session_active.set()
    return daemon


def test_idle_hotkey_opens_session():
    daemon = _make_daemon(active=False, session=False)
    daemon._handle_hotkey()
    assert daemon._session_active.is_set()
    assert daemon.surface.actions[0] == "activate"
    assert any(item[0] == "state" and item[1] == "listening" for item in daemon.surface.actions if isinstance(item, tuple))


def test_open_idle_hotkey_closes_session_without_starting_work():
    daemon = _make_daemon(active=False, session=True)
    daemon._handle_hotkey()
    assert not daemon._session_active.is_set()
    assert daemon.surface.actions[-1] == "close"
    assert daemon.bridge.actions == []
    assert daemon.tts.stopped is False


def test_running_hotkey_cancels_and_closes_ui_immediately():
    daemon = _make_daemon(active=True, session=True)
    daemon._handle_hotkey()
    assert daemon._cancel_event.is_set()
    assert daemon.bridge.actions == ["halt"]
    assert not daemon._session_active.is_set()
    assert any(item[0] == "state" and item[1] == "stopping" for item in daemon.surface.actions if isinstance(item, tuple))
    assert daemon.surface.actions[-1] == "close"


def test_close_discards_queued_work():
    daemon = _make_daemon(active=False, session=True)
    daemon._enqueue("open Firefox")
    assert daemon.command_event.is_set()
    daemon._handle_control_event("close")
    assert daemon._commands == []
    assert not daemon.command_event.is_set()


def test_command_worker_executes_queued_text_without_voice(monkeypatch):
    daemon = _make_daemon(active=False, session=True)
    daemon.voice = None
    done = threading.Event()
    original = daemon.controller.process_request

    def fake_process(command, *, cancel_event=None):
        result = original(command, cancel_event=cancel_event)
        done.set()
        return result

    daemon.controller.process_request = fake_process
    daemon._enqueue("open Firefox")
    worker = threading.Thread(target=daemon._command_worker_loop, daemon=True)
    worker.start()
    assert done.wait(1.0)
    daemon.stop_event.set()
    daemon.command_event.set()
    worker.join(timeout=1.0)
    assert daemon.controller.commands == ["open Firefox"]


def test_direct_fallback_enables_extension(monkeypatch):
    class FakeBridge:
        available = True
        def __init__(self, timeout=2.0):
            self.calls = 0
        def indicator_active(self):
            return False if self.calls == 0 else False
        def toggle_assistant(self):
            self.calls += 1
            if self.calls == 1:
                return False
            return True

    monkeypatch.setattr("src.jarvis.gnome.bridge.GnomeShellBridge", FakeBridge)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gnome-extensions" if name == "gnome-extensions" else "/usr/bin/gdbus")

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: Result())

    from src.jarvis.input.hotkey import _toggle_gnome_directly
    assert _toggle_gnome_directly() is True


def test_open_session_hotkey_does_not_leave_queued_work():
    daemon = _make_daemon(active=False, session=True)
    daemon._enqueue("open Firefox")
    daemon._handle_hotkey()
    assert daemon._commands == []
    assert not daemon.command_event.is_set()


def test_direct_fallback_toggles_existing_interface(monkeypatch):
    class FakeBridge:
        available = True
        def __init__(self, timeout=2.0):
            pass
        def toggle_assistant(self):
            return True

    monkeypatch.setattr("src.jarvis.gnome.bridge.GnomeShellBridge", FakeBridge)
    from src.jarvis.input.hotkey import _toggle_gnome_directly
    assert _toggle_gnome_directly() is True


def test_control_voice_requests_python_voice_capture():
    daemon = _make_daemon(active=False, session=True)
    daemon._voice_capture_event = threading.Event()
    daemon._handle_control_event("voice")
    assert daemon._voice_capture_event.is_set()
    assert daemon.voice.cancelled is True


def test_open_session_does_not_start_microphone_capture():
    daemon = _make_daemon(active=False, session=False)
    daemon._voice_capture_event = threading.Event()
    daemon._open_session()
    assert not daemon._voice_capture_event.is_set()


def test_open_session_clears_previous_cancel_state():
    daemon = _make_daemon(active=False, session=False)
    daemon._voice_capture_event = threading.Event()
    daemon._cancel_event.set()
    daemon._open_session(start_voice=False)
    assert not daemon._cancel_event.is_set()
