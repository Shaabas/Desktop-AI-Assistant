from __future__ import annotations

import threading

from src.jarvis.app.daemon import (
    MAX_COMMAND_CHARACTERS,
    MAX_PENDING_COMMANDS,
    JarvisDaemon,
)


def _uninitialized_daemon_for_queue_test() -> JarvisDaemon:
    daemon = JarvisDaemon.__new__(JarvisDaemon)
    daemon.voice = None
    daemon._lock = threading.Lock()
    daemon._commands = []
    daemon.command_event = threading.Event()
    return daemon


def test_command_queue_has_a_hard_item_limit(capsys) -> None:
    daemon = _uninitialized_daemon_for_queue_test()
    for index in range(MAX_PENDING_COMMANDS + 3):
        daemon._enqueue(f"command {index}")

    assert len(daemon._commands) == MAX_PENDING_COMMANDS
    assert daemon.command_event.is_set()
    assert "Command queue is full" in capsys.readouterr().out


def test_command_queue_rejects_oversized_payloads(capsys) -> None:
    daemon = _uninitialized_daemon_for_queue_test()
    daemon._enqueue("x" * (MAX_COMMAND_CHARACTERS + 1))

    assert daemon._commands == []
    assert not daemon.command_event.is_set()
    assert "exceeds the" in capsys.readouterr().out
