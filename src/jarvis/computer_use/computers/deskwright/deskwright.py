# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import io
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Literal, Optional

from PIL import Image, ImageDraw
import termcolor
from google.genai import types

from ..computer import Computer, EnvState

try:
    import deskwright
    from deskwright import capture, input as deskwright_input, remote_input, shell
except ImportError:
    deskwright = None
    capture = None
    deskwright_input = None
    remote_input = None
    shell = None


try:
    import gi
    try:
        gi.require_version("Gdk", "4.0")
    except ValueError:
        gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
except Exception:
    Gdk = None


# Key aliases to normalize various keyboard key naming styles into keysym names
KEY_ALIASES = {
    "command": "super",
    "cmd": "super",
    "windows": "super",
    "win": "super",
    "meta": "super",
    "super_l": "super",
    "super_r": "super",
    "control": "ctrl",
    "control_l": "ctrl",
    "control_r": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt_l": "alt",
    "alt_r": "alt",
    "return": "enter",
    "escape": "esc",
    "arrowleft": "left",
    "arrowright": "right",
    "arrowup": "up",
    "arrowdown": "down",
    "del": "delete",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "page_up": "pageup",
    "page_down": "pagedown",
    "spacebar": "space",
    "backspace": "backspace",
}


class DeskwrightComputer(Computer):
    """Connects to a native GNOME Shell Wayland desktop using Deskwright.

    Enables Gemini Computer Use on Fedora Linux (and other GNOME Wayland systems)
    via the deskwright@zeticle.com GNOME Shell extension and Mutter RemoteDesktop.
    """

    environment: types.Environment = types.Environment.ENVIRONMENT_DESKTOP

    def __init__(
        self,
        screen_size: Optional[tuple[int, int]] = None,
        initial_url: Optional[str] = None,
        search_engine_url: str = "https://www.google.com",
        highlight_mouse: bool = True,
        annotate_grid: bool = False,
        enable_indicator: bool = True,
        indicator_label: str = os.environ.get("DESKWRIGHT_INDICATOR_LABEL", "Observing desktop..."),
        enable_pointer_label: bool = True,
        pointer_label_ttl_seconds: float = 45.0,
        pointer_label_heartbeat_seconds: float = 20.0,
        settle_timeout: float = 0.3,
    ):
        if deskwright is None:
            raise ImportError(
                "deskwright is not installed. Please install it or ensure it is available in Python path."
            )

        self._screen_size = screen_size
        self._initial_url = initial_url
        self._search_engine_url = search_engine_url
        self._highlight_mouse = highlight_mouse
        self._annotate_grid = annotate_grid
        self._enable_indicator = enable_indicator
        self._indicator_label = indicator_label
        self._enable_pointer_label = enable_pointer_label
        self._pointer_label_ttl_seconds = max(10.0, float(pointer_label_ttl_seconds))
        self._pointer_label_heartbeat_seconds = max(
            2.0, min(float(pointer_label_heartbeat_seconds), self._pointer_label_ttl_seconds / 2)
        )
        self._pointer_label_stop_event: Optional[threading.Event] = None
        self._pointer_label_thread: Optional[threading.Thread] = None
        self._settle_timeout = settle_timeout
        self._input = None
        self._last_pointer: Optional[tuple[int, int]] = None

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        """Check if Deskwright and the GNOME Shell extension are available."""
        if os.environ.get("XDG_SESSION_TYPE") != "wayland":
            return False, f"Expected Wayland session, but got {os.environ.get('XDG_SESSION_TYPE')!r}"
        if deskwright is None:
            return False, "deskwright package is not installed"
        try:
            methods = shell.extension_methods()
            if not methods:
                return False, "deskwright@zeticle.com extension is not answering on D-Bus"
            return True, "GNOME Shell Wayland and deskwright@zeticle.com extension are ready"
        except Exception as e:
            return False, str(e)

    @classmethod
    def clear_halt(cls) -> bool:
        """Clear the emergency halt switch via D-Bus."""
        try:
            subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "com.zeticle.deskwright",
                    "--object-path",
                    "/com/zeticle/deskwright",
                    "--method",
                    "com.zeticle.deskwright.ClearHalt",
                ],
                check=True,
                capture_output=True,
            )
            return True
        except Exception:
            return False

    def _check_halt(self):
        """Check whether the GNOME compositor emergency halt is active."""
        if shell and shell.halt_active():
            raise RuntimeError(
                "Emergency stop triggered: the GNOME compositor halt switch is active. "
                "Clear it from the JARVIS control path before continuing."
            )

    def __enter__(self):
        termcolor.cprint(
            "Connecting to GNOME Wayland desktop via Deskwright...",
            color="cyan",
            attrs=["bold"],
        )
        self._input = remote_input.RemoteInput()

        # Auto-detect screen size from desktop bounds if not explicitly supplied
        if self._screen_size is None:
            try:
                bounds = self._input.desktop_bounds()
                self._screen_size = (bounds[2], bounds[3])
            except Exception:
                try:
                    shot = capture.tool_screenshot({"inline": False})
                    dims = shot.get("dimensions", "1920x1080")
                    w, h = map(int, dims.split("x"))
                    self._screen_size = (w, h)
                except Exception:
                    self._screen_size = (1920, 1080)

        # Set visual indicator in GNOME Shell to signal that the agent is driving the desktop
        if self._enable_indicator:
            try:
                shell._pick_bus()
                shell._gdbus("SetIndicator", self._indicator_label, "600.0")
            except Exception as e:
                logging.warning("Could not enable GNOME Shell activity indicator: %s", e)

        termcolor.cprint(
            f"Connected to Wayland desktop ({self._screen_size[0]}x{self._screen_size[1]}).",
            color="green",
            attrs=["bold"],
        )

        if self._initial_url:
            self.navigate(self._initial_url)

        # Use a GNOME Shell chrome actor rather than a Wayland client overlay.
        # The short D-Bus lease is renewed while the computer-control context is
        # alive, so a hard process crash cannot leave the label stuck forever.
        self._start_pointer_label()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_pointer_label()
        if self._enable_indicator:
            try:
                shell._gdbus("ClearIndicator")
            except Exception:
                pass
        if self._input:
            try:
                self._input.stop()
            except Exception:
                pass

    def _set_pointer_label_active(self, active: bool) -> bool:
        """Set the GNOME Shell pointer pill through the Deskwright D-Bus API."""
        if not self._enable_pointer_label:
            return False
        try:
            result = subprocess.run(
                [
                    "gdbus", "call", "--session",
                    "--dest", "com.zeticle.deskwright",
                    "--object-path", "/com/zeticle/deskwright",
                    "--method", "com.zeticle.deskwright.SetPointerLabelActive",
                    "true" if active else "false",
                    f"{self._pointer_label_ttl_seconds:.1f}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.5,
            )
            if result.returncode != 0:
                logging.debug(
                    "Could not %s JARVIS pointer label: %s",
                    "enable" if active else "clear",
                    (result.stderr or result.stdout or "D-Bus call failed").strip(),
                )
                return False
            return "(true,)" in (result.stdout or "").lower()
        except Exception as exc:
            logging.debug("Could not update JARVIS pointer label: %s", exc)
            return False

    def _pointer_label_heartbeat(self) -> None:
        stop_event = self._pointer_label_stop_event
        if stop_event is None:
            return
        while not stop_event.wait(self._pointer_label_heartbeat_seconds):
            if not self._set_pointer_label_active(True):
                logging.debug("JARVIS pointer label lease could not be renewed")

    def _start_pointer_label(self) -> None:
        if not self._enable_pointer_label:
            return
        if not self._set_pointer_label_active(True):
            # Older/disabled Shell extensions may not yet expose this method.
            # Computer use remains available; only this optional visual cue is lost.
            return
        self._pointer_label_stop_event = threading.Event()
        self._pointer_label_thread = threading.Thread(
            target=self._pointer_label_heartbeat,
            name="jarvis-pointer-label-heartbeat",
            daemon=True,
        )
        self._pointer_label_thread.start()

    def _stop_pointer_label(self) -> None:
        stop_event = self._pointer_label_stop_event
        thread = self._pointer_label_thread
        self._pointer_label_stop_event = None
        self._pointer_label_thread = None
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            # The renewal call has a 2.5-second subprocess timeout. Join before
            # clearing so a late heartbeat cannot re-show the pill after exit.
            thread.join(timeout=3.0)
        if self._enable_pointer_label:
            self._set_pointer_label_active(False)

    def screen_size(self) -> tuple[int, int]:
        if self._screen_size:
            return self._screen_size
        return (1920, 1080)

    def update_status(self, message: str) -> bool:
        """Update the on-screen Gemini indicator/capsule message dynamically."""
        if not self._enable_indicator:
            return False
        try:
            shell._pick_bus()
            shell._gdbus("SetIndicator", message, "600.0")
            return True
        except Exception as e:
            logging.debug("Could not update indicator status: %s", e)
            return False

    def get_pending_instructions(self) -> list[str]:
        """Retrieve and clear any live user instructions typed into the on-screen overlay chatbox."""
        if not self._enable_indicator:
            return []
        try:
            shell._pick_bus()
            res = shell._gdbus("GetPendingInstructions")
            if res and isinstance(res, (tuple, list)) and len(res) > 0:
                raw_list = res[0]
                if isinstance(raw_list, (list, tuple)):
                    return [str(item) for item in raw_list if str(item).strip()]
            return []
        except Exception as e:
            logging.debug("Could not fetch pending instructions: %s", e)
            return []

    def submit_instruction(self, text: str) -> bool:
        """Submit a user instruction to the overlay queue."""
        if not self._enable_indicator:
            return False
        try:
            shell._pick_bus()
            res = shell._gdbus("SubmitInstruction", text)
            return bool(res and res[0]) if isinstance(res, (tuple, list)) else bool(res)
        except Exception as e:
            logging.debug("Could not submit instruction: %s", e)
            return False

    def open_web_browser(self) -> EnvState:
        self._check_halt()
        url = self._initial_url or self._search_engine_url
        self.navigate(url)
        time.sleep(1.5)
        return self.current_state()

    def click_at(self, x: int, y: int) -> EnvState:
        self._check_halt()
        self._last_pointer = (x, y)
        self._input.move_to(x, y)
        self._input.click(x, y, button="left", count=1)
        return self.current_state()

    def double_click_at(self, x: int, y: int) -> EnvState:
        self._check_halt()
        self._last_pointer = (x, y)
        self._input.move_to(x, y)
        self._input.click(x, y, button="left", count=2)
        return self.current_state()

    def triple_click_at(self, x: int, y: int) -> EnvState:
        self._check_halt()
        self._last_pointer = (x, y)
        self._input.move_to(x, y)
        self._input.click(x, y, button="left", count=3)
        return self.current_state()

    def middle_click_at(self, x: int, y: int) -> EnvState:
        self._check_halt()
        self._last_pointer = (x, y)
        self._input.move_to(x, y)
        self._input.click(x, y, button="middle", count=1)
        return self.current_state()

    def right_click_at(self, x: int, y: int) -> EnvState:
        self._check_halt()
        self._last_pointer = (x, y)
        self._input.move_to(x, y)
        self._input.click(x, y, button="right", count=1)
        return self.current_state()

    def mouse_down(self, x: int, y: int) -> EnvState:
        self._check_halt()
        self._last_pointer = (x, y)
        self._input.move_to(x, y)
        self._input.button("left", True)
        return self.current_state()

    def mouse_up(self, x: int, y: int) -> EnvState:
        self._check_halt()
        self._last_pointer = (x, y)
        self._input.move_to(x, y)
        self._input.button("left", False)
        return self.current_state()

    def hover_at(self, x: int, y: int) -> EnvState:
        self._check_halt()
        self._last_pointer = (x, y)
        self._input.move_to(x, y)
        return self.current_state()

    def type_text(self, text: str, press_enter: bool = False) -> EnvState:
        self._check_halt()
        self._input.type_text(text)
        if press_enter:
            self.key_combination(["enter"])
        return self.current_state()

    def wait(self, seconds: int = 1) -> EnvState:
        self._check_halt()
        time.sleep(seconds)
        return self.current_state()

    def wait_5_seconds(self) -> EnvState:
        return self.wait(5)

    def type_text_at(
        self,
        x: int,
        y: int,
        text: str,
        press_enter: bool = False,
        clear_before_typing: bool = True,
    ) -> EnvState:
        self._check_halt()
        self._last_pointer = (x, y)
        self._input.move_to(x, y)
        self._input.click(x, y, button="left", count=1)
        time.sleep(0.1)

        if clear_before_typing:
            self.key_combination(["ctrl", "a"])
            self.press_key("backspace")
            time.sleep(0.05)

        self._input.type_text(text)
        if press_enter:
            self.key_combination(["enter"])
        return self.current_state()

    def scroll_at(
        self,
        x: int,
        y: int,
        direction: Literal["up", "down", "left", "right"],
        magnitude: int = 800,
    ) -> EnvState:
        self._check_halt()
        self._last_pointer = (x, y)
        # Convert pixel magnitude to discrete wheel clicks (~100px per click, minimum 1)
        steps = max(1, int(abs(magnitude) // 100))
        dx = 0
        dy = 0
        if direction == "up":
            dy = -steps
        elif direction == "down":
            dy = steps
        elif direction == "left":
            dx = -steps
        elif direction == "right":
            dx = steps
        else:
            raise ValueError(f"Unsupported direction: {direction}")

        self._input.scroll(x, y, dy=dy, dx=dx)
        return self.current_state()

    def scroll_document(
        self, direction: Literal["up", "down", "left", "right"]
    ) -> EnvState:
        self._check_halt()
        if direction == "down":
            return self.key_combination(["pagedown"])
        elif direction == "up":
            return self.key_combination(["pageup"])
        elif direction in ("left", "right"):
            cx = self._screen_size[0] // 2
            cy = self._screen_size[1] // 2
            return self.scroll_at(cx, cy, direction, magnitude=500)
        else:
            raise ValueError(f"Unsupported direction: {direction}")

    def go_back(self) -> EnvState:
        self._check_halt()
        return self.key_combination(["alt", "left"])

    def go_forward(self) -> EnvState:
        self._check_halt()
        return self.key_combination(["alt", "right"])

    def search(self) -> EnvState:
        return self.navigate(self._search_engine_url)

    def navigate(self, url: str) -> EnvState:
        self._check_halt()
        normalized_url = url
        if not normalized_url.startswith(("http://", "https://", "file://", "about:")):
            normalized_url = "https://" + normalized_url
        try:
            subprocess.Popen(
                ["xdg-open", normalized_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            subprocess.Popen(
                ["gio", "open", normalized_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        time.sleep(1.0)
        return self.current_state()

    def _resolve_keysym(self, key_str: str) -> int:
        norm = key_str.lower().strip()
        norm = KEY_ALIASES.get(norm, norm)
        if norm in remote_input.KEYSYMS:
            return remote_input.KEYSYMS[norm]

        # Use Gdk keyval_from_name for standard X11 / Wayland keysyms (e.g. Super_L, Return, Escape)
        if Gdk is not None:
            for candidate in [key_str, key_str.capitalize(), key_str.title(), norm]:
                val = Gdk.keyval_from_name(candidate)
                if val not in (0, 0xffffff):
                    return val

        if len(key_str) == 1:
            return remote_input.char_to_keysym(key_str)
        raise ValueError(f"Unknown key: {key_str}")

    def key_combination(self, keys: list[str]) -> EnvState:
        self._check_halt()
        # Handle string like 'ctrl+c' or list of keys
        parsed_keys: list[str] = []
        for k in keys:
            if "+" in k:
                parsed_keys.extend([part.strip() for part in k.split("+") if part.strip()])
            else:
                parsed_keys.append(k)

        syms = [self._resolve_keysym(k) for k in parsed_keys]
        self._input.combo(syms)
        return self.current_state()

    def press_key(self, key: str) -> EnvState:
        return self.key_combination([key])

    def key_down(self, key: str) -> EnvState:
        self._check_halt()
        sym = self._resolve_keysym(key)
        self._input.keysym(sym, True)
        return self.current_state()

    def key_up(self, key: str) -> EnvState:
        self._check_halt()
        sym = self._resolve_keysym(key)
        self._input.keysym(sym, False)
        return self.current_state()

    def drag_and_drop(
        self, x: int, y: int, destination_x: int, destination_y: int
    ) -> EnvState:
        self._check_halt()
        self._last_pointer = (destination_x, destination_y)
        self._input.drag(x, y, destination_x, destination_y, button="left")
        return self.current_state()

    def take_screenshot(self) -> EnvState:
        return self.current_state()

    def current_state(self) -> EnvState:
        self._check_halt()
        if self._settle_timeout > 0:
            time.sleep(self._settle_timeout)

        # Capture desktop screenshot using Deskwright via GNOME Shell extension D-Bus
        capture_opts = {"inline": False}
        if self._annotate_grid:
            capture_opts["annotate"] = {"grid": 100, "windows": True}

        shot = capture.tool_screenshot(capture_opts)
        shot_path = Path(shot["path"])
        screenshot_bytes = shot_path.read_bytes()

        # If highlight_mouse is requested and we know where the pointer is, draw visual reticle
        if self._highlight_mouse and self._last_pointer:
            try:
                with Image.open(shot_path) as img:
                    img = img.convert("RGB")
                    draw = ImageDraw.Draw(img)
                    px, py = self._last_pointer
                    # Draw a distinct red circular reticle with center dot and crosshair
                    r = 14
                    draw.ellipse([px - r, py - r, px + r, py + r], outline=(255, 0, 0), width=3)
                    draw.ellipse([px - 2, py - 2, px + 2, py + 2], fill=(255, 0, 0))
                    draw.line([(px - r - 5, py), (px + r + 5, py)], fill=(255, 0, 0), width=1)
                    draw.line([(px, py - r - 5), (px, py + r + 5)], fill=(255, 0, 0), width=1)
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    screenshot_bytes = buf.getvalue()
            except Exception as e:
                logging.debug("Could not draw mouse highlight on screenshot: %s", e)

        # Build context URL describing focused desktop application/window
        url = "desktop://gnome"
        try:
            windows = shell.list_windows()
            focused = [w for w in windows if w.get("focused")]
            if focused:
                wm_class = focused[0].get("wm_class", "app")
                title = focused[0].get("title", "")
                url = f"desktop://{wm_class}/{title}"
        except Exception:
            pass

        return EnvState(screenshot=screenshot_bytes, url=url)
