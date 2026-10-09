"""
src/jarvis/input/wake_word.py — OpenWakeWord Model Loader & Detector.
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional
import openwakeword
import openwakeword.utils
from openwakeword.model import Model


def load_wake_word_model(model_filename: str = "hey_jarvis_v0.1.onnx") -> tuple[Optional[Model], str]:
    """Loads custom or default openwakeword model."""
    base_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent
    oww_dir = base_dir / "models" / "openwakeword"
    target_wake_path = oww_dir / model_filename

    if target_wake_path.exists():
        try:
            return Model([str(target_wake_path)]), target_wake_path.stem
        except Exception as err:
            print(f"[WakeWord] Warning: Failed to load custom model '{target_wake_path}': {err}")

    try:
        return Model(["hey_jarvis"]), "hey_jarvis"
    except Exception:
        try:
            dl_fn = getattr(openwakeword.utils, "download_models", None)
            if callable(dl_fn):
                dl_fn()
            return Model(["hey_jarvis"]), "hey_jarvis"
        except Exception as err:
            print(f"[WakeWord] Warning: openwakeword default model unavailable: {err}")

    return None, "hey_jarvis"
