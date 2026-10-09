"""Bounded buffering and retry policy for microphone capture.

These helpers intentionally depend only on the Python standard library so the
resource-safety policy can be tested without opening a physical audio device.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any


# At 16 kHz, the wake-word queue holds at most ~640 ms of audio (8 x 80 ms).
# The speech queue holds at most ~800 ms (8 x 100 ms). Old audio is less useful
# than fresh audio for an interactive assistant, so overflow drops the oldest.
AUDIO_QUEUE_MAX_CHUNKS = 8
INPUT_OVERFLOW_CALLBACK_LIMIT = 5
AUDIO_CALLBACK_STALL_SECONDS = 3.0
AUDIO_RETRY_BASE_SECONDS = 0.5
AUDIO_RETRY_MAX_SECONDS = 30.0


def enqueue_latest_audio(audio_queue: queue.Queue, indata: Any) -> bool:
    """Enqueue a copied audio block, dropping the oldest block when full.

    Returns True if an old block had to be dropped. This function never waits
    for space, which is important when called from a PortAudio callback.
    """
    dropped = False
    frame = indata.copy().flatten()
    try:
        audio_queue.put_nowait(frame)
        return dropped
    except queue.Full:
        dropped = True

    try:
        audio_queue.get_nowait()
    except queue.Empty:
        # The consumer may have freed a slot between put_nowait and get_nowait.
        pass

    try:
        audio_queue.put_nowait(frame)
    except queue.Full:
        # Defensive race protection. There is normally one producer callback,
        # but correctness should not depend on that implementation detail.
        try:
            audio_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            audio_queue.put_nowait(frame)
        except queue.Full:
            return True
    return dropped


def update_input_overflow_streak(status: Any, current_streak: int) -> tuple[int, bool]:
    """Track consecutive PortAudio input-overflow callbacks.

    A clean callback resets the streak. The return value is (new_streak,
    should_reopen_stream).
    """
    if bool(getattr(status, "input_overflow", False)):
        current_streak += 1
    else:
        current_streak = 0
    return current_streak, current_streak >= INPUT_OVERFLOW_CALLBACK_LIMIT


class AudioRecoveryBackoff:
    """Exponential retry backoff for repeated audio-device/stream failures."""

    def __init__(
        self,
        base_seconds: float = AUDIO_RETRY_BASE_SECONDS,
        max_seconds: float = AUDIO_RETRY_MAX_SECONDS,
    ) -> None:
        if base_seconds <= 0 or max_seconds < base_seconds:
            raise ValueError("Audio retry bounds must satisfy 0 < base <= max")
        self._base_seconds = float(base_seconds)
        self._max_seconds = float(max_seconds)
        self._failure_count = 0
        self._retry_after = 0.0
        self._lock = threading.Lock()

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self._retry_after - time.monotonic())

    def register_failure(self) -> float:
        """Record a failure and return the next delay, bounded by max_seconds."""
        with self._lock:
            self._failure_count += 1
            delay = min(
                self._max_seconds,
                self._base_seconds * (2 ** min(self._failure_count - 1, 30)),
            )
            self._retry_after = time.monotonic() + delay
            return delay

    def reset(self) -> None:
        """Clear the consecutive-failure backoff after a healthy stream."""
        with self._lock:
            self._failure_count = 0
            self._retry_after = 0.0
