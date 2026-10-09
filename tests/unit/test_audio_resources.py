from __future__ import annotations

import queue
from types import SimpleNamespace

import numpy as np

from src.jarvis.input.audio_resources import (
    AUDIO_QUEUE_MAX_CHUNKS,
    AUDIO_RETRY_MAX_SECONDS,
    AudioRecoveryBackoff,
    enqueue_latest_audio,
    update_input_overflow_streak,
)


def test_audio_queue_is_bounded_and_keeps_newest_chunks() -> None:
    audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=AUDIO_QUEUE_MAX_CHUNKS)

    for value in range(AUDIO_QUEUE_MAX_CHUNKS + 4):
        enqueue_latest_audio(audio_queue, np.full((4, 1), value, dtype=np.int16))

    assert audio_queue.qsize() == AUDIO_QUEUE_MAX_CHUNKS
    chunks = [audio_queue.get_nowait() for _ in range(AUDIO_QUEUE_MAX_CHUNKS)]
    assert [int(chunk[0]) for chunk in chunks] == list(range(4, AUDIO_QUEUE_MAX_CHUNKS + 4))


def test_enqueue_latest_reports_when_it_drops_stale_audio() -> None:
    audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
    assert enqueue_latest_audio(audio_queue, np.array([1], dtype=np.int16)) is False
    assert enqueue_latest_audio(audio_queue, np.array([2], dtype=np.int16)) is True
    assert int(audio_queue.get_nowait()[0]) == 2


def test_input_overflow_streak_triggers_bounded_stream_recovery() -> None:
    streak = 0
    for _ in range(4):
        streak, should_abort = update_input_overflow_streak(
            SimpleNamespace(input_overflow=True), streak
        )
        assert should_abort is False

    streak, should_abort = update_input_overflow_streak(
        SimpleNamespace(input_overflow=True), streak
    )
    assert should_abort is True

    streak, should_abort = update_input_overflow_streak(
        SimpleNamespace(input_overflow=False), streak
    )
    assert streak == 0
    assert should_abort is False


def test_audio_recovery_backoff_is_exponential_bounded_and_resettable() -> None:
    recovery = AudioRecoveryBackoff()
    delays = [recovery.register_failure() for _ in range(8)]

    assert delays[:5] == [0.5, 1.0, 2.0, 4.0, 8.0]
    assert delays[5:] == [16.0, AUDIO_RETRY_MAX_SECONDS, AUDIO_RETRY_MAX_SECONDS]
    assert recovery.failure_count == 8
    assert recovery.remaining() > 0

    recovery.reset()
    assert recovery.failure_count == 0
    assert recovery.remaining() == 0
    assert recovery.register_failure() == 0.5
