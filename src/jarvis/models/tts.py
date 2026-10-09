"""
src/jarvis/models/tts.py — Text-to-Speech Engine (Piper Local Neural TTS / Edge-TTS Fallback).
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from typing import Any, Callable, Iterable, Iterator, List, Optional

import numpy as np
import sounddevice as sd

from .process_utils import terminate_and_reap


class TTSEngine:
    def __init__(self, ui_callback: Callable[[str], None] | None = None, model_name: str = "en_GB-alan-medium") -> None:
        self.ui_callback = ui_callback
        self.model_name = model_name
        self.voice = None
        self._stop_event = threading.Event()
        self._is_speaking = False
        self._active_proc: subprocess.Popen[Any] | None = None
        self._lock = threading.Lock()
        self._init_piper()

    def _get_base_dir(self) -> pathlib.Path:
        return pathlib.Path(__file__).resolve().parent.parent.parent.parent

    def is_speaking(self) -> bool:
        return self._is_speaking and not self._stop_event.is_set()

    def stop(self) -> None:
        """Stops any currently active TTS playback immediately and safely."""
        self._stop_event.set()
        try:
            sd.stop()
        except Exception:
            pass

        with self._lock:
            proc = self._active_proc
            self._active_proc = None

        if proc is not None:
            terminate_and_reap(proc)

        self._is_speaking = False

    def _init_piper(self) -> None:
        try:
            from piper import PiperVoice

            tts_dir = self._get_base_dir() / "models" / "tts"
            tts_dir.mkdir(parents=True, exist_ok=True)
            onnx_path = tts_dir / f"{self.model_name}.onnx"
            json_path = tts_dir / f"{self.model_name}.onnx.json"

            if not onnx_path.exists() or not json_path.exists():
                parts = self.model_name.split("-")
                if len(parts) >= 3:
                    locale, voice_name, quality = parts[0], parts[1], parts[2]
                    lang = locale.split("_")[0]
                    base_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang}/{locale}/{voice_name}/{quality}/{self.model_name}"
                    url_onnx = f"{base_url}.onnx"
                    url_json = f"{base_url}.onnx.json"
                else:
                    url_onnx = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/{self.model_name}.onnx"
                    url_json = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/{self.model_name}.onnx.json"

                if not onnx_path.exists():
                    urllib.request.urlretrieve(url_onnx, onnx_path)
                if not json_path.exists():
                    urllib.request.urlretrieve(url_json, json_path)

            if onnx_path.exists() and json_path.exists():
                self.voice = PiperVoice.load(str(onnx_path))
        except Exception as err:
            print(f"[TTSEngine] Warning: Failed to load Piper TTS voice: {err}")
            self.voice = None

    def speak(self, text: str) -> bool:
        if not text or not text.strip():
            return False

        self.stop()
        self._stop_event.clear()
        self._is_speaking = True

        try:
            if self.ui_callback:
                self.ui_callback(text)

            # 1. Primary Unified Local Neural TTS (Piper)
            if self.voice is not None:
                try:
                    for chunk in self.voice.synthesize(text):
                        if self._stop_event.is_set():
                            sd.stop()
                            return False
                        audio_np = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                        sd.play(audio_np, samplerate=chunk.sample_rate)

                        while sd.get_stream().active:
                            if self._stop_event.is_set():
                                sd.stop()
                                return False
                            time.sleep(0.02)

                    return not self._stop_event.is_set()
                except Exception as err:
                    print(f"[TTSEngine] Piper TTS playback error: {err}")

            # 2. Fallback to Edge-TTS if Piper fails
            if self._stop_event.is_set():
                return False

            output_path = pathlib.Path(tempfile.gettempdir()) / "jarvis_tts_fallback.mp3"
            if output_path.exists():
                output_path.unlink(missing_ok=True)

            try:
                import edge_tts
                communicate = edge_tts.Communicate(text, "en-US-ChristopherNeural")
                asyncio.run(communicate.save(str(output_path)))
                if output_path.exists() and not self._stop_event.is_set():
                    return self._play_audio(output_path)
            except Exception:
                pass

            # 3. Fallback to espeak
            if self._stop_event.is_set():
                return False

            espeak = shutil.which("espeak") or shutil.which("espeak-ng")
            if espeak:
                proc: subprocess.Popen[Any] | None = None
                try:
                    proc = subprocess.Popen([espeak, text])
                    with self._lock:
                        self._active_proc = proc

                    while proc.poll() is None:
                        if self._stop_event.is_set():
                            self.stop()
                            return False
                        time.sleep(0.05)
                    return proc.returncode == 0 and not self._stop_event.is_set()
                except Exception:
                    pass
                finally:
                    if proc is not None:
                        terminate_and_reap(proc)
                        with self._lock:
                            if self._active_proc is proc:
                                self._active_proc = None

            return False
        finally:
            self._is_speaking = False

    def speak_async(self, text: str) -> None:
        """Asynchronously speaks text on a background daemon thread."""
        threading.Thread(target=self.speak, args=(text,), daemon=True, name="TTSSpeakThread").start()

    def stream_speak(self, token_generator: Iterable[str]) -> str:
        """
        Streams conversational tokens through real-time clause splitting and asynchronous queue playback.
        Synthesizes and speaks the first clause in <300ms while generating subsequent tokens concurrently.
        Returns the complete assembled spoken string.
        """
        clause_queue: queue.Queue[Optional[str]] = queue.Queue(maxsize=15)
        self.stop()
        self._stop_event.clear()
        self._is_speaking = True

        def _playback_worker() -> None:
            while not self._stop_event.is_set():
                try:
                    clause = clause_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if clause is None:
                    break

                if not clause.strip():
                    clause_queue.task_done()
                    continue

                if self._stop_event.is_set():
                    clause_queue.task_done()
                    break

                try:
                    if self.ui_callback:
                        self.ui_callback(clause)

                    # Synthesize with Piper if available
                    if self.voice is not None:
                        for chunk in self.voice.synthesize(clause):
                            if self._stop_event.is_set():
                                sd.stop()
                                return
                            audio_np = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                            sd.play(audio_np, samplerate=chunk.sample_rate)
                            while sd.get_stream().active:
                                if self._stop_event.is_set():
                                    sd.stop()
                                    return
                                time.sleep(0.015)
                    else:
                        self.speak(clause)
                except Exception:
                    pass
                finally:
                    clause_queue.task_done()

        worker_thread = threading.Thread(target=_playback_worker, daemon=True, name="TTSStreamWorker")
        worker_thread.start()

        buffer = ""
        full_response_parts: List[str] = []

        try:
            for token in token_generator:
                if self._stop_event.is_set():
                    break
                buffer += token
                full_response_parts.append(token)

                # Sentence / clause boundary matching:
                # 1. Punctuation (. ! ? \n) followed by whitespace
                # 2. Comma / colon / semicolon after >= 6 words
                match = re.search(r"([.!?\n]+)\s+|([,;:])\s+", buffer)
                if match:
                    punct_idx = match.end()
                    is_comma = match.group(2) is not None
                    words_count = len(buffer[:punct_idx].split())
                    if not is_comma or words_count >= 6:
                        clause = buffer[:punct_idx].strip()
                        buffer = buffer[punct_idx:]
                        if clause and not self._stop_event.is_set():
                            clause_queue.put(clause)

            # Flush trailing text
            if buffer.strip() and not self._stop_event.is_set():
                clause_queue.put(buffer.strip())

        finally:
            if self._stop_event.is_set():
                # The worker exits on the stop event and may no longer drain a
                # full queue. Clear stale clauses rather than blocking forever
                # while trying to enqueue the sentinel.
                while True:
                    try:
                        clause_queue.get_nowait()
                    except queue.Empty:
                        break
            try:
                clause_queue.put(None, timeout=1.0)
            except queue.Full:
                pass
            worker_thread.join(timeout=2.0)
            self._is_speaking = False

        return "".join(full_response_parts).strip()

    def _play_audio(self, audio_path: pathlib.Path) -> bool:
        player_candidates = ["ffplay", "mpv", "vlc", "aplay"]
        player = next((shutil.which(p) for p in player_candidates if shutil.which(p)), None)
        if not player:
            return False

        cmd = [player, "-nodisp", "-autoexit", str(audio_path)] if "ffplay" in player else [player, str(audio_path)]
        proc: subprocess.Popen[Any] | None = None
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with self._lock:
                self._active_proc = proc

            while proc.poll() is None:
                if self._stop_event.is_set():
                    self.stop()
                    return False
                time.sleep(0.05)
            return proc.returncode == 0 and not self._stop_event.is_set()
        except Exception:
            return False
        finally:
            if proc is not None:
                terminate_and_reap(proc)
            with self._lock:
                if self._active_proc is proc:
                    self._active_proc = None
            if audio_path.exists():
                audio_path.unlink(missing_ok=True)
