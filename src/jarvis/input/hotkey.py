"""Lightweight process-level hotkey signaling for JARVIS on Linux.

GNOME Settings owns the actual keyboard shortcut. The shortcut executes
``python main.py --hotkey``. If JARVIS is running, the trigger is delivered to
it with SIGUSR1. If JARVIS is not running, the same command directly asks the
GNOME extension to activate the assistant surface. No hotkey socket, helper
daemon, or launcher process is required.
"""

from __future__ import annotations

import os
import signal
from pathlib import Path

_DAEMON_LOCK_FD: int | None = None
_DAEMON_LOCK_PATH: Path | None = None


def default_pid_file() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "jarvis" / "jarvis.pid"
    return Path(f"/tmp/jarvis-{os.getuid()}.pid")


def default_lock_file() -> Path:
    """Return the per-user singleton lock path for the long-running daemon."""
    return default_pid_file().with_name("jarvis.lock")


def acquire_daemon_lock(path: Path | None = None) -> bool:
    """Acquire an advisory process lock before initializing large models.

    The file is intentionally left in place after release; flock ownership is
    tied to the open file description, not the file's existence. This avoids
    unlink/recreate races between a service restart and a manual launch.
    """
    global _DAEMON_LOCK_FD, _DAEMON_LOCK_PATH
    target = path or default_lock_file()
    if _DAEMON_LOCK_FD is not None:
        return _DAEMON_LOCK_PATH == target

    try:
        import fcntl

        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(fd)
            return False
        _DAEMON_LOCK_FD = fd
        _DAEMON_LOCK_PATH = target
        return True
    except (ImportError, OSError):
        return False


def release_daemon_lock() -> None:
    """Release the singleton lock held by this process, if any."""
    global _DAEMON_LOCK_FD, _DAEMON_LOCK_PATH
    fd = _DAEMON_LOCK_FD
    _DAEMON_LOCK_FD = None
    _DAEMON_LOCK_PATH = None
    if fd is None:
        return
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    try:
        os.close(fd)
    except OSError:
        pass


PID_FILE = default_pid_file()


def write_pid_file(pid: int | None = None, path: Path | None = None) -> Path:
    """Write the daemon PID so a user-configured shortcut can signal it."""
    pid = pid or os.getpid()
    path = path or default_pid_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def remove_pid_file(path: Path | None = None) -> None:
    path = path or default_pid_file()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _toggle_gnome_directly() -> bool:
    """Activate the GNOME assistant surface when JARVIS itself is not running.

    The Deskwright extension must be enabled for its D-Bus name to exist.
    Normally the extension installer enables it; this path makes the one-time
    state self-healing when a user has disabled it.
    """
    try:
        from src.jarvis.gnome.bridge import GnomeShellBridge, GnomeBridgeError
    except Exception as exc:
        print(f"[JARVIS Hotkey] Could not load the GNOME bridge: {exc}")
        return False

    bridge = GnomeShellBridge(timeout=2.0)
    if not bridge.available:
        print("[JARVIS Hotkey] gdbus is not available; cannot activate the GNOME assistant.")
        return False

    def try_activate() -> bool:
        try:
            return bridge.toggle_assistant()
        except GnomeBridgeError as exc:
            print(f"[JARVIS Hotkey] GNOME D-Bus error: {exc}")
            return False

    if try_activate():
        print("[JARVIS Hotkey] GNOME assistant toggled.")
        return True

    # If the extension is installed but disabled, enabling it is enough to
    # make the existing D-Bus service appear. Do not start JARVIS or create
    # another resident process here.
    import shutil
    import subprocess
    if shutil.which("gnome-extensions"):
        enable = subprocess.run(
            ["gnome-extensions", "enable", "deskwright@zeticle.com"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if enable.returncode == 0:
            import time
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if try_activate():
                    print("[JARVIS Hotkey] GNOME assistant toggled.")
                    return True
                time.sleep(0.1)

    print("[JARVIS Hotkey] GNOME Deskwright extension is not running or does not expose the assistant toggle.")
    print("[JARVIS Hotkey] Enable deskwright@zeticle.com in GNOME Extensions and try again.")
    return False


# Backwards-compatible internal name for older tests/integrations.
_activate_gnome_directly = _toggle_gnome_directly

def send_hotkey_trigger(pid_file: Path | None = None) -> bool:
    """Handle a Linux hotkey without starting another JARVIS instance.

    If JARVIS is running, signal it so the same hotkey can open/close or
    interrupt the active task. If JARVIS is not running, toggle the GNOME
    extension directly so the interface remains usable on its own.
    """
    pid_file = pid_file or default_pid_file()
    if os.name != "posix" or not hasattr(signal, "SIGUSR1"):
        print("[JARVIS Hotkey] The signal-based hotkey is only available on Linux/POSIX.")
        return False

    pid: int | None = None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        # No daemon: the GNOME extension is still able to present the UI.
        return _toggle_gnome_directly()

    if pid <= 0:
        remove_pid_file(pid_file)
        return _toggle_gnome_directly()

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        remove_pid_file(pid_file)
        return _toggle_gnome_directly()
    except PermissionError:
        print(f"[JARVIS Hotkey] Cannot access JARVIS process {pid}.")
        return False

    try:
        os.kill(pid, signal.SIGUSR1)
    except ProcessLookupError:
        remove_pid_file(pid_file)
        return _toggle_gnome_directly()
    except PermissionError:
        print(f"[JARVIS Hotkey] Permission denied while signaling JARVIS process {pid}.")
        return False

    print("[JARVIS Hotkey] Trigger sent to running JARVIS daemon.")
    return True


__all__ = [
    "PID_FILE",
    "default_pid_file",
    "default_lock_file",
    "acquire_daemon_lock",
    "release_daemon_lock",
    "write_pid_file",
    "remove_pid_file",
    "send_hotkey_trigger",
]
