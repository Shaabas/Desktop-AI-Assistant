"""
src/jarvis/input/speech.py — VoiceEngine handling Wake-Word, Whisper Small Single-Pass STT & Barge-In.
"""

from __future__ import annotations

import ctypes
import queue
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import scipy.signal as signal  # type: ignore[import-untyped,import-not-found]
import sounddevice as sd

import pathlib
from faster_whisper import WhisperModel
from src.jarvis.input.wake_word import load_wake_word_model
from src.jarvis.input.audio_resources import (
    AUDIO_CALLBACK_STALL_SECONDS,
    AUDIO_QUEUE_MAX_CHUNKS,
    AudioRecoveryBackoff,
    enqueue_latest_audio,
    update_input_overflow_streak,
)


def load_whisper_small_model(
    model_name: Optional[str] = None,
    cpu_threads: int = 6,
    beam_size: int = 1,
) -> WhisperModel:
    """Loads single shared Whisper model (supporting large-v3-turbo, small.en, base.en with disk caching)."""
    base_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent
    
    if not model_name:
        try:
            import json
            cfg_path = base_dir / "config" / "settings.json"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    model_name = cfg.get("whisper_model")
        except Exception:
            pass
    if not model_name:
        model_name = "small.en"

    local_turbo = base_dir / "models" / "whisper-large-v3-turbo"
    local_small = base_dir / "models" / "whisper-small-en"
    local_base = base_dir / "models" / "whisper-base-en"

    if "turbo" in model_name.lower() and local_turbo.exists() and any(local_turbo.iterdir()):
        model_target = str(local_turbo)
    elif local_small.exists() and any(local_small.iterdir()):
        model_target = str(local_small)
    elif local_base.exists() and any(local_base.iterdir()):
        model_target = str(local_base)
    else:
        model_target = model_name

    print(f"[WhisperSTT] Initializing Whisper Model '{model_target}' (CPU Threads: {cpu_threads}, Beam: {beam_size})")
    return WhisperModel(
        model_target,
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
        local_files_only=pathlib.Path(str(model_target)).exists(),
    )


ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)

def _py_error_handler(filename, line, function, err, fmt):
    pass

try:
    asound = ctypes.cdll.LoadLibrary("libasound.so.2")
    # Keep the CFUNCTYPE wrapper alive for as long as libasound retains the C
    # function pointer. Passing a temporary callback can leave a dangling
    # pointer that becomes unsafe when ALSA reports a later error.
    _ASOUND_ERROR_CALLBACK = ERROR_HANDLER_FUNC(_py_error_handler)
    asound.snd_lib_error_set_handler(_ASOUND_ERROR_CALLBACK)
except Exception:
    _ASOUND_ERROR_CALLBACK = None
    pass


MIN_SPEECH_DURATION: float = 0.4
SILENCE_TIMEOUT: float = 0.7
DEFAULT_CPU_THREADS: int = 6
DEFAULT_WHISPER_MODEL: str = "small.en"
DEFAULT_BEAM_SIZE: int = 1


def preprocess_mic_audio_lightweight(
    audio_data: np.ndarray,
    sample_rate: int = 16000,
) -> np.ndarray:
    if len(audio_data) == 0:
        return audio_data

    arr = audio_data.astype(np.float32)

    nyquist = 0.5 * sample_rate
    low = max(0.001, min(0.99, 80.0 / nyquist))
    high = max(0.001, min(0.99, 7500.0 / nyquist))

    try:
        filt_res = signal.butter(4, [low, high], btype="band")
        if filt_res is not None:
            b, a = filt_res[0], filt_res[1]
            filtered = signal.filtfilt(b, a, arr)
        else:
            filtered = arr
    except Exception:
        filtered = arr

    max_val = np.max(np.abs(filtered))
    if max_val > 1e-5:
        boosted = filtered * (0.85 / max_val)
    else:
        boosted = filtered

    return boosted.astype(np.float32)


KNOWN_HALLUCINATIONS = [
    r"the speaker is using indian english",
    r"the user is speaking indian english",
    r"subtitles by",
    r"amara\.org",
    r"thank you for watching",
    r"subscribe for more",
    r"thanks for watching",
    r"you for watching",
]


def _clean_transcription(text: str) -> str:
    if not text or not text.strip():
        return ""
    t = text.strip()
    for pattern in KNOWN_HALLUCINATIONS:
        if re.search(pattern, t, flags=re.I):
            return ""
    cleaned = re.sub(r"^(hey\s+)?jarvis[\s,]*", "", t, flags=re.I).strip()
    return cleaned or t


class VoiceEngine:
    DEFAULT_INDIAN_ENGLISH_PROMPT = (
        "Open YouTube, play music, open browser, search Google, volume up, "
        "take screenshot, system status, close application, open terminal."
    )

    def __init__(
        self,
        wake_word_model: str = "hey_jarvis_v0.1.onnx",
        sample_rate: int = 16000,
        activation_threshold: float = 0.40,
        silence_timeout: float = SILENCE_TIMEOUT,
        max_command_duration: float = 20.0,
        language: str = "en",
        initial_prompt: str | None = DEFAULT_INDIAN_ENGLISH_PROMPT,
        beam_size: int = DEFAULT_BEAM_SIZE,
        cpu_threads: int = DEFAULT_CPU_THREADS,
    ) -> None:
        self.sample_rate = sample_rate
        self.activation_threshold = activation_threshold
        self.silence_timeout = silence_timeout
        self.max_command_duration = max_command_duration
        self.language = language
        self.initial_prompt = initial_prompt
        self.beam_size = max(1, beam_size)
        self.cpu_threads = max(1, cpu_threads)

        self.stream_lock = threading.Lock()
        self.model_lock = threading.Lock()
        self._cancel_requested = threading.Event()
        self._active_stream_lock = threading.Lock()
        self._active_stream = None
        self._audio_recovery = AudioRecoveryBackoff()

        self.oww_model, self._wake_key = load_wake_word_model(wake_word_model)
        self.whisper = load_whisper_small_model(
            model_name=DEFAULT_WHISPER_MODEL,
            cpu_threads=self.cpu_threads,
            beam_size=self.beam_size,
        )

    def cancel_recording(self) -> None:
        """Immediately cancels ongoing audio recording and transcription."""
        self._cancel_requested.set()
        # Wake a blocked capture loop and ask PortAudio to stop callback work.
        # The stream context remains responsible for final close/cleanup.
        with self._active_stream_lock:
            stream = self._active_stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass

    def _wait_for_audio_retry(self, *events: threading.Event | None) -> bool:
        """Wait out backoff in short, cancellable intervals."""
        while True:
            if self._cancel_requested.is_set() or any(event and event.is_set() for event in events):
                return False
            remaining = self._audio_recovery.remaining()
            if remaining <= 0:
                return True
            time.sleep(min(0.1, remaining))

    def _register_audio_failure(self, operation: str, error: Exception) -> None:
        delay = self._audio_recovery.register_failure()
        count = self._audio_recovery.failure_count
        print(
            f"[VoiceEngine] {operation} failed ({error}); "
            f"retrying microphone capture in {delay:.1f}s "
            f"(consecutive failures: {count})."
        )

    def _set_active_stream(self, stream) -> None:
        with self._active_stream_lock:
            self._active_stream = stream

    def _clear_active_stream(self, stream) -> None:
        with self._active_stream_lock:
            if self._active_stream is stream:
                self._active_stream = None

    @staticmethod
    def _check_capture_health(
        stream_failed: threading.Event,
        failure_reason: list[str],
        last_callback_time: list[float],
    ) -> None:
        if stream_failed.is_set():
            raise RuntimeError(failure_reason[0] or "microphone stream reported repeated input overflow")
        if time.monotonic() - last_callback_time[0] > AUDIO_CALLBACK_STALL_SECONDS:
            raise RuntimeError(
                f"microphone callback stalled for more than {AUDIO_CALLBACK_STALL_SECONDS:.0f} seconds"
            )

    def _get_rms(self, chunk: np.ndarray) -> float:
        if len(chunk) == 0:
            return 0.0
        return float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))

    def listen_for_wake_word(
        self,
        stop_event: threading.Event,
        interrupt_event: threading.Event | None = None,
        wake_event: threading.Event | None = None,
        on_level: Callable[[float], None] | None = None,
    ) -> bool:
        if self.oww_model is None:
            time.sleep(0.2)
            return False

        self._cancel_requested.clear()
        if not self._wait_for_audio_retry(stop_event, interrupt_event):
            return False
        chunk_samples = 1280
        audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=AUDIO_QUEUE_MAX_CHUNKS)
        stream_failed = threading.Event()
        failure_reason = [""]
        overflow_streak = [0]
        last_callback_time = [time.monotonic()]

        def callback(indata, frames, time_info, status):
            if stream_failed.is_set():
                return
            last_callback_time[0] = time.monotonic()
            overflow_streak[0], abort_stream = update_input_overflow_streak(status, overflow_streak[0])
            if abort_stream:
                failure_reason[0] = "repeated PortAudio input-overflow callbacks"
                stream_failed.set()
                return
            enqueue_latest_audio(audio_q, indata)

        with self.stream_lock:
            if not self._wait_for_audio_retry(stop_event, interrupt_event):
                return False
            stream = None
            try:
                last_callback_time[0] = time.monotonic()
                stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=chunk_samples,
                    callback=callback,
                )
                self._set_active_stream(stream)
                with stream:
                    while not stop_event.is_set():
                        if self._cancel_requested.is_set():
                            return False

                        if wake_event and wake_event.is_set():
                            wake_event.clear()
                            self._audio_recovery.reset()
                            return True

                        if interrupt_event and interrupt_event.is_set():
                            self._audio_recovery.reset()
                            return False

                        self._check_capture_health(stream_failed, failure_reason, last_callback_time)

                        try:
                            chunk = audio_q.get(timeout=0.02)
                        except queue.Empty:
                            continue

                        float_chunk = chunk.astype(np.float32) / 32768.0
                        rms = self._get_rms(float_chunk)
                        prev_floor = getattr(self, "_ambient_noise_floor", None)
                        self._ambient_noise_floor = (0.92 * prev_floor + 0.08 * rms) if prev_floor else rms
                        if on_level:
                            on_level(min(1.0, rms * 18.0))

                        prediction = self.oww_model.predict(chunk)
                        pred_dict: Dict[Any, Any] = {}
                        if isinstance(prediction, tuple) and len(prediction) > 0:
                            if isinstance(prediction[0], dict):
                                pred_dict = prediction[0]
                        elif isinstance(prediction, dict):
                            pred_dict = prediction
                        score = float(pred_dict.get(self._wake_key, 0.0))
                        if score >= self.activation_threshold:
                            self.oww_model.reset()
                            self._audio_recovery.reset()
                            return True
            except Exception as err:
                if self._cancel_requested.is_set() or stop_event.is_set() or (
                    interrupt_event and interrupt_event.is_set()
                ):
                    return False
                self._register_audio_failure("Wake-word audio capture", err)
                return False
            finally:
                if stream is not None:
                    self._clear_active_stream(stream)
            if not stream_failed.is_set():
                self._audio_recovery.reset()
        return False

    def stream_and_transcribe(
        self,
        on_partial_text: Callable[[str], None] | None = None,
        on_level: Callable[[float], None] | None = None,
        wait_timeout: float = 6.0,
        abort_event: threading.Event | None = None,
    ) -> str:
        self._cancel_requested.clear()
        if not self._wait_for_audio_retry(abort_event):
            return ""
        chunk_size = int(self.sample_rate * 0.1)
        audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=AUDIO_QUEUE_MAX_CHUNKS)
        stream_failed = threading.Event()
        failure_reason = [""]
        overflow_streak = [0]
        last_callback_time = [time.monotonic()]

        def callback(indata, frames, time_info, status):
            if stream_failed.is_set():
                return
            last_callback_time[0] = time.monotonic()
            overflow_streak[0], abort_stream = update_input_overflow_streak(status, overflow_streak[0])
            if abort_stream:
                failure_reason[0] = "repeated PortAudio input-overflow callbacks"
                stream_failed.set()
                return
            enqueue_latest_audio(audio_q, indata)

        collected_audio: list[np.ndarray] = []
        speech_started = False
        silence_start = None
        start_time = time.time()

        t0_speech_start: float | None = None
        t1_endpoint: float | None = None
        t2_final_stt: float | None = None

        with self.stream_lock:
            if not self._wait_for_audio_retry(abort_event):
                return ""
            stream = None
            try:
                last_callback_time[0] = time.monotonic()
                stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=chunk_size,
                    callback=callback,
                )
                self._set_active_stream(stream)
                with stream:
                    # Backoff and waiting for the stream lock are not part of
                    # the user's maximum recording duration.
                    start_time = time.time()
                    noise_floor = getattr(self, "_ambient_noise_floor", 0.003) or 0.003
                    threshold = max(noise_floor * 1.15, 0.003)

                    while True:
                        if self._cancel_requested.is_set() or (abort_event and abort_event.is_set()):
                            return ""
                        elapsed = time.time() - start_time
                        if elapsed > self.max_command_duration:
                            break

                        self._check_capture_health(stream_failed, failure_reason, last_callback_time)

                        try:
                            chunk = audio_q.get(timeout=0.15)
                        except queue.Empty:
                            self._check_capture_health(stream_failed, failure_reason, last_callback_time)
                            continue

                        rms = self._get_rms(chunk)
                        if on_level:
                            on_level(min(1.0, rms * 18.0))

                        if not speech_started:
                            if rms > threshold:
                                speech_started = True
                                t0_speech_start = time.time()
                                print(f"[TIMING] T0 (Speech Starts) detected at {t0_speech_start:.3f}")
                                collected_audio.append(chunk)
                                silence_start = None
                            elif elapsed > wait_timeout:
                                self._audio_recovery.reset()
                                return ""
                        else:
                            collected_audio.append(chunk)
                            total_speech_sec = (len(collected_audio) * chunk_size) / self.sample_rate

                            if rms < threshold:
                                if silence_start is None:
                                    silence_start = time.time()
                                elif total_speech_sec >= MIN_SPEECH_DURATION and (time.time() - silence_start >= SILENCE_TIMEOUT):
                                    t1_endpoint = time.time()
                                    if t0_speech_start:
                                        dt1 = (t1_endpoint - t0_speech_start) * 1000.0
                                        print(f"[TIMING] T0 -> T1 (Speech Endpoint): {dt1:.1f} ms total duration")
                                    break
                            else:
                                silence_start = None
            except Exception as err:
                if self._cancel_requested.is_set() or (abort_event and abort_event.is_set()):
                    return ""
                self._register_audio_failure("Speech-recognition audio capture", err)
                return ""
            finally:
                if stream is not None:
                    self._clear_active_stream(stream)
            if not stream_failed.is_set():
                self._audio_recovery.reset()

        if not collected_audio or not speech_started:
            return ""

        full_audio = preprocess_mic_audio_lightweight(np.concatenate(collected_audio), self.sample_rate)
        if len(full_audio) < self.sample_rate * 0.3:
            return ""

        final_text = ""
        with self.model_lock:
            try:
                segments, _ = self.whisper.transcribe(
                    full_audio,
                    beam_size=self.beam_size,
                    language=self.language,
                    initial_prompt=self.initial_prompt,
                    condition_on_previous_text=False,
                    no_speech_threshold=0.6,
                    compression_ratio_threshold=2.4,
                    log_prob_threshold=-1.0,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=250),
                )
                raw = " ".join(seg.text for seg in segments).strip()
                final_text = _clean_transcription(raw)
            except Exception as err:
                print(f"[VoiceEngine Error] Final STT pass error: {err}")

        t2_final_stt = time.time()
        if t1_endpoint:
            dt2 = (t2_final_stt - t1_endpoint) * 1000.0
            print(f"[TIMING] T1 -> T2 (Whisper Small Final Pass): {dt2:.1f} ms | Final Text: '{final_text}'")

        if on_partial_text and final_text:
            on_partial_text(final_text)

        return final_text
