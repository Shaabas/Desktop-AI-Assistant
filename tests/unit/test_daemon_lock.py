from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from src.jarvis.input.hotkey import acquire_daemon_lock, release_daemon_lock


def test_daemon_lock_blocks_a_second_process_and_releases_cleanly(tmp_path: Path) -> None:
    lock_path = tmp_path / "jarvis.lock"
    assert acquire_daemon_lock(lock_path) is True
    child_code = (
        "from pathlib import Path; "
        "from src.jarvis.input.hotkey import acquire_daemon_lock; "
        "import sys; print(acquire_daemon_lock(Path(sys.argv[1])))"
    )
    try:
        child = subprocess.run(
            [sys.executable, "-c", child_code, str(lock_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert child.stdout.strip() == "False"
    finally:
        release_daemon_lock()

    child = subprocess.run(
        [sys.executable, "-c", child_code, str(lock_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert child.stdout.strip() == "True"


def test_reacquiring_same_lock_is_idempotent_in_one_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "jarvis.lock"
    try:
        assert acquire_daemon_lock(lock_path) is True
        assert acquire_daemon_lock(lock_path) is True
    finally:
        release_daemon_lock()
