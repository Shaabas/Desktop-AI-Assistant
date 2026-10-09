from __future__ import annotations

import subprocess
import sys
import time

from src.jarvis.models.process_utils import terminate_and_reap


def test_terminate_and_reap_collects_an_active_child() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        terminate_and_reap(proc, timeout=0.5)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def test_terminate_and_reap_handles_an_already_exited_child() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    assert proc.wait(timeout=2) == 0
    terminate_and_reap(proc, timeout=0.1)
    assert proc.returncode == 0
