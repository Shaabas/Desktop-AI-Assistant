"""Small subprocess lifecycle helpers shared by JARVIS model adapters."""
from __future__ import annotations

import subprocess
from typing import Any


def terminate_and_reap(proc: subprocess.Popen[Any], timeout: float = 1.0) -> None:
    """Terminate a live child and always attempt to collect its exit status.

    A child that ignores terminate() is escalated to kill(). Every wait is
    bounded so shutdown cannot hang indefinitely on an unresponsive process.
    """
    try:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, OSError):
                # systemd will clean up any remaining process in the service
                # cgroup if the child cannot be collected during shutdown.
                pass
    except (OSError, ProcessLookupError):
        try:
            proc.wait(timeout=0.1)
        except (subprocess.TimeoutExpired, OSError):
            pass


__all__ = ["terminate_and_reap"]
