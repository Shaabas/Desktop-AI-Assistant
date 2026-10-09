"""
src/jarvis/ui/window.py — Main Application Window & Tray Interface.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import random
import sys
import time
from typing import Any, Callable

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPen,
    QRadialGradient,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import socket


def check_internet_connection(host: str = "8.8.8.8", port: int = 53, timeout: float = 2.5) -> bool:
    import urllib.request
    import urllib.error

    # 1. Primary: probe reliable HTTPS endpoints (Gemini API & Cloudflare)
    for url in ["https://generativelanguage.googleapis.com", "https://1.1.1.1", "https://www.google.com"]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-HealthCheck"})
            with urllib.request.urlopen(req, timeout=timeout):
                return True
        except urllib.error.HTTPError:
            # Reached endpoint with an HTTP status (e.g. 404/403) -> Internet is online!
            return True
        except Exception:
            continue

    # 2. Secondary fallback: socket connection
    for h, p in [(host, port), ("1.1.1.1", 53)]:
        try:
            with socket.create_connection((h, p), timeout=timeout):
                return True
        except OSError:
            continue
    return False


from src.jarvis.ui.hud import sanitize_hud_summary

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return BASE_DIR


def lerp_rgb(c_off: tuple[int, int, int], c_on: tuple[int, int, int], factor: float) -> str:
    r = int(c_off[0] + (c_on[0] - c_off[0]) * factor)
    g = int(c_off[1] + (c_on[1] - c_off[1]) * factor)
    b = int(c_off[2] + (c_on[2] - c_off[2]) * factor)
    return f"rgb({max(0, min(255, r))}, {max(0, min(255, g))}, {max(0, min(255, b))})"


def lerp_rgba(c_off: tuple[int, int, int, float], c_on: tuple[int, int, int, float], factor: float) -> str:
    r = int(c_off[0] + (c_on[0] - c_off[0]) * factor)
    g = int(c_off[1] + (c_on[1] - c_off[1]) * factor)
    b = int(c_off[2] + (c_on[2] - c_off[2]) * factor)
    a = c_off[3] + (c_on[3] - c_off[3]) * factor
    return f"rgba({max(0, min(255, r))}, {max(0, min(255, g))}, {max(0, min(255, b))}, {a:.2f})"


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, color_factor: float = 1.0) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(500, 250)
        self.setModal(True)

        self._color_factor = color_factor
        self.config_path = get_base_dir() / "config" / "api_keys.json"
        self._drag_pos: QPoint | None = None

        self._build_ui()
        self._load_key()
        self._apply_styling_factor(self._color_factor)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        self._drag_pos = None
        event.accept()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.container = QWidget(self)
        self.container.setObjectName("DialogContainer")
        outer.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(24, 16, 24, 20)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()
        top_bar.addStretch()
        btn_close = QPushButton("✕", self)
        btn_close.setObjectName("CloseBtn")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.reject)
        top_bar.addWidget(btn_close)
        layout.addLayout(top_bar)

        self.lbl_gemini = QLabel("ENTER GEMINI API KEY:")
        self.lbl_gemini.setObjectName("LabelGemini")
        layout.addWidget(self.lbl_gemini)

        self.txt_key = QLineEdit()
        self.txt_key.setObjectName("KeyInput")
        self.txt_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_key.setPlaceholderText("AIzaSy...")
        layout.addWidget(self.txt_key)

        self.lbl_groq = QLabel("ENTER GROQ API KEY (OPTIONAL FOR FAST STT):")
        self.lbl_groq.setObjectName("LabelGroq")
        layout.addWidget(self.lbl_groq)

        self.txt_groq = QLineEdit()
        self.txt_groq.setObjectName("KeyInput")
        self.txt_groq.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_groq.setPlaceholderText("gsk_...")
        layout.addWidget(self.txt_groq)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        self.btn_cancel = QPushButton("CANCEL")
        self.btn_cancel.setObjectName("CancelBtn")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("SAVE")
        self.btn_save.setObjectName("SaveBtn")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self._save_key)
        btn_box.addWidget(self.btn_save)

        layout.addLayout(btn_box)

    def _apply_styling_factor(self, factor: float) -> None:
        accent_color = lerp_rgb((243, 194, 103), (125, 211, 252), factor)
        inp_border = lerp_rgba((229, 169, 60, 0.35), (56, 189, 248, 0.35), factor)
        inp_focus = lerp_rgb((229, 169, 60), (56, 189, 248), factor)
        btn_g0 = lerp_rgb((217, 145, 32), (2, 132, 199), factor)
        btn_g1 = lerp_rgb((245, 185, 66), (56, 189, 248), factor)
        btn_h0 = lerp_rgb((232, 160, 44), (3, 105, 161), factor)
        btn_h1 = lerp_rgb((255, 208, 102), (125, 211, 252), factor)
        dlg_border = lerp_rgba((229, 169, 60, 0.4), (56, 189, 248, 0.4), factor)

        self.setStyleSheet(f"""
            QWidget#DialogContainer {{
                background-color: #0d111a;
                border: 1px solid {dlg_border};
                border-radius: 14px;
            }}
            QLabel#LabelGemini, QLabel#LabelGroq {{
                color: {accent_color};
                font-weight: bold;
                font-family: 'Consolas', 'JetBrains Mono', monospace;
                font-size: 12px;
            }}
            QLineEdit#KeyInput {{
                background: #121824;
                border: 1px solid {inp_border};
                border-radius: 8px;
                color: #ffffff;
                padding: 8px 12px;
                font-size: 13px;
            }}
            QLineEdit#KeyInput:focus {{
                border: 1.5px solid {inp_focus};
            }}
            QPushButton#CloseBtn {{
                background: transparent;
                border: none;
                color: #94a3b8;
                font-size: 14px;
                font-weight: bold;
                padding: 2px 6px;
            }}
            QPushButton#CloseBtn:hover {{
                color: #ef4444;
            }}
            QPushButton#CancelBtn {{
                background: transparent;
                border: 1px solid #475569;
                color: #94a3b8;
                border-radius: 6px;
                padding: 6px 16px;
                font-weight: bold;
            }}
            QPushButton#CancelBtn:hover {{
                background: rgba(255, 255, 255, 0.05);
            }}
            QPushButton#SaveBtn {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {btn_g0}, stop:1 {btn_g1});
                border: none;
                color: #0b0e14;
                border-radius: 6px;
                padding: 6px 20px;
                font-weight: bold;
            }}
            QPushButton#SaveBtn:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {btn_h0}, stop:1 {btn_h1});
            }}
        """)

    def _load_key(self) -> None:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.txt_key.setText(data.get("gemini_api_key", ""))
                    self.txt_groq.setText(data.get("groq_api_key", ""))
            except Exception:
                pass

    def _save_key(self) -> None:
        key = self.txt_key.text().strip()
        groq = self.txt_groq.text().strip()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data["gemini_api_key"] = key
        data["groq_api_key"] = groq
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self.accept()


class JarvisHoloOrb(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(360, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._start_time = time.time()
        self._state = "LISTENING"
        self._audio_level = 0.0
        self._is_online = True
        self._color_factor = 1.0

        self._nodes = []
        random.seed(2048)
        for _ in range(85):
            self._nodes.append({
                "theta": random.uniform(0, 2 * math.pi),
                "phi": random.uniform(-math.pi / 2, math.pi / 2),
                "speed_t": random.uniform(0.003, 0.012) * random.choice([-1, 1]),
                "speed_p": random.uniform(0.001, 0.005) * random.choice([-1, 1]),
                "size": random.uniform(1.6, 4.0),
                "shimmer_phase": random.uniform(0, 2 * math.pi),
                "shimmer_speed": random.uniform(1.2, 3.2),
                "base_alpha": random.randint(150, 255),
            })

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def _tick(self) -> None:
        self._audio_level *= 0.88
        target = 1.0 if self._is_online else 0.0
        self._color_factor += (target - self._color_factor) * 0.06
        self.update()

    def set_state(self, state: str) -> None:
        self._state = state.upper()
        self.update()

    def set_audio_level(self, level: float) -> None:
        self._audio_level = max(self._audio_level, float(level))
        self.update()

    def set_online(self, is_online: bool) -> None:
        if self._is_online != is_online:
            self._is_online = is_online
            self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        t = time.time() - self._start_time
        w = self.width()
        h = self.height()
        cx = w / 2.0
        cy = h / 2.0

        if self._state == "THINKING":
            breath_speed = 1.9
            drift_speed = 1.5
        elif self._state == "SPEAKING":
            breath_speed = 1.4
            drift_speed = 1.2
        else:
            breath_speed = 0.85
            drift_speed = 0.7

        c_off = QColor(255, 160, 30)
        c_on = QColor(56, 189, 248)

        r = int(c_off.red() + (c_on.red() - c_off.red()) * self._color_factor)
        g = int(c_off.green() + (c_on.green() - c_off.green()) * self._color_factor)
        b = int(c_off.blue() + (c_on.blue() - c_off.blue()) * self._color_factor)
        c_outer = QColor(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))

        breath_phase = t * breath_speed
        harmonic_breathe = (
            math.sin(breath_phase) * 0.04
            + math.sin(breath_phase * 0.5 + 1.2) * 0.02
            + math.sin(breath_phase * 1.3 - 0.7) * 0.01
        )
        audio_boost = self._audio_level * 0.35
        breath_scale = 1.0 + harmonic_breathe + audio_boost

        glow_intensity = 0.7 + 0.3 * math.sin(breath_phase * 0.8) + self._audio_level * 0.5

        cy = h * 0.44
        base_r = min(w, h) * 0.38 * breath_scale

        amb_r = max(w, h) * 0.70
        amb_glow = QRadialGradient(cx, cy, amb_r)
        amb_glow.setColorAt(0.0, QColor(c_outer.red(), c_outer.green(), c_outer.blue(), min(255, int(45 * glow_intensity))))
        amb_glow.setColorAt(0.35, QColor(c_outer.red(), int(c_outer.green() * 0.7), 20, min(255, int(18 * glow_intensity))))
        amb_glow.setColorAt(0.70, QColor(c_outer.red(), int(c_outer.green() * 0.5), 10, min(255, int(5 * glow_intensity))))
        amb_glow.setColorAt(1.0, QColor(c_outer.red(), c_outer.green(), c_outer.blue(), 0))
        painter.setBrush(QBrush(amb_glow))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), amb_r, amb_r)

        halo_r = base_r * 1.45
        halo_glow = QRadialGradient(cx, cy, halo_r)
        halo_glow.setColorAt(0.0, QColor(c_outer.red(), c_outer.green(), c_outer.blue(), min(255, int(80 * glow_intensity))))
        halo_glow.setColorAt(0.55, QColor(c_outer.red(), int(c_outer.green() * 0.8), 30, min(255, int(30 * glow_intensity))))
        halo_glow.setColorAt(1.0, QColor(c_outer.red(), c_outer.green(), c_outer.blue(), 0))
        painter.setBrush(QBrush(halo_glow))
        painter.drawEllipse(QPointF(cx, cy), halo_r, halo_r)

        for ring_idx, (r_mod, ecc, t_mult, rot_bias, alpha_base, width) in enumerate([
            (0.96, 0.35, 0.45, 0.0, 100, 1.8),
            (0.88, 0.60, -0.32, 45.0, 80, 1.5),
            (0.92, 0.85, 0.22, 120.0, 70, 1.2),
            (0.72, 0.40, -0.60, 200.0, 120, 2.0),
            (0.60, 0.75, 0.40, 310.0, 140, 2.2),
        ]):
            painter.save()
            painter.translate(cx, cy)
            current_rot = rot_bias + (t * 22.0 * t_mult * drift_speed)
            painter.rotate(current_rot)

            ring_rx = base_r * r_mod * (1.0 + 0.015 * math.sin(breath_phase + ring_idx))
            ring_ry = base_r * (r_mod * ecc) * (1.0 + 0.02 * math.cos(breath_phase * 1.2 + ring_idx))

            a = min(255, int((alpha_base + self._audio_level * 100) * (0.8 + 0.2 * math.sin(t * 1.5 + ring_idx))))
            pen = QPen(QColor(c_outer.red(), c_outer.green(), c_outer.blue(), a), width + self._audio_level * 1.5)
            if ring_idx % 2 == 1:
                pen.setStyle(Qt.PenStyle.DashLine)
                pen.setDashPattern([3, 7])
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(-ring_rx, -ring_ry, ring_rx * 2, ring_ry * 2))
            painter.restore()

        for arc_idx, (r_mult, span_deg, speed_fac, base_a, stroke_w) in enumerate([
            (0.84, 55, 0.8, 170, 2.8),
            (0.84, 75, 0.8, 150, 2.8),
            (0.68, 110, -1.1, 190, 2.4),
            (0.48, 140, 1.4, 220, 3.0),
        ]):
            painter.save()
            painter.translate(cx, cy)
            start_ang = (t * 40.0 * speed_fac * drift_speed + (arc_idx * 90.0)) % 360
            arc_r = base_r * r_mult

            a = min(255, int(base_a + self._audio_level * 60))
            pen = QPen(QColor(c_outer.red(), c_outer.green(), c_outer.blue(), a), stroke_w + self._audio_level * 1.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(
                QRectF(-arc_r, -arc_r, arc_r * 2, arc_r * 2),
                int(start_ang * 16),
                span_deg * 16,
            )
            painter.restore()

        for idx, n in enumerate(self._nodes):
            theta = n["theta"] + (n["speed_t"] * t * 60.0 * drift_speed)
            phi = n["phi"] + (math.sin(t * n["speed_p"] * 20.0) * 0.2)

            r = base_r * (0.80 + 0.05 * math.sin(breath_phase + idx * 0.1))
            x = cx + r * math.cos(phi) * math.sin(theta)
            y = cy + r * math.sin(phi)

            depth = (math.cos(phi) * math.cos(theta) + 1.0) / 2.0
            shimmer = 0.5 + 0.5 * math.sin(t * n["shimmer_speed"] + n["shimmer_phase"])
            alpha = min(255, int(n["base_alpha"] * (0.3 + 0.7 * depth) * shimmer + self._audio_level * 80))
            pt_size = n["size"] * (0.6 + 0.5 * depth) + self._audio_level * 2.0

            if depth > 0.6:
                p_glow = QColor(255, 230, 150, int(alpha * 0.45))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(p_glow))
                painter.drawEllipse(QPointF(x, y), pt_size * 2.4, pt_size * 2.4)

            p_core = QColor(255, 255, 240, alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(p_core))
            painter.drawEllipse(QPointF(x, y), pt_size * 1.0, pt_size * 1.0)

        core_r = base_r * (0.28 + 0.04 * math.sin(breath_phase * 1.1) + self._audio_level * 0.15)
        core_grad = QRadialGradient(cx, cy, core_r)

        c_core_mid = QColor(
            int(255 + (125 - 255) * self._color_factor),
            int(210 + (211 - 210) * self._color_factor),
            int(80 + (252 - 80) * self._color_factor),
            min(255, int(220 * glow_intensity)),
        )
        c_core_outer = QColor(
            int(235 + (14 - 235) * self._color_factor),
            int(120 + (165 - 120) * self._color_factor),
            int(15 + (233 - 15) * self._color_factor),
            min(255, int(110 * glow_intensity)),
        )

        core_grad.setColorAt(0.0, QColor(255, 255, 235, 255))
        core_grad.setColorAt(0.25, c_core_mid)
        core_grad.setColorAt(0.65, c_core_outer)
        core_grad.setColorAt(1.0, QColor(20, 10, 5, 0))

        painter.setBrush(QBrush(core_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), core_r, core_r)


class QSegmentedModeSwitch(QWidget):
    """Custom animated dual-segment mode switch (CLOUD LLM vs LOCAL LLM).

    Segments:
      - Left (0.0):  "CLOUD LLM" (or "OFFLINE" when disconnected)
      - Right (1.0): "LOCAL LLM"
    """

    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(196, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._checked = True  # True = CLOUD LLM (Left segment), False = LOCAL LLM (Right segment)
        self._enabled = True
        self._position = 0.0  # 0.0 = Left (Cloud), 1.0 = Right (Local)
        self._target_position = 0.0

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate_step)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if self._checked != checked:
            self._checked = checked
            self._target_position = 0.0 if self._checked else 1.0
            if not self._anim_timer.isActive():
                self._anim_timer.start(16)
            self.update()

    def setEnabled(self, enabled: bool) -> None:
        self._enabled = enabled
        super().setEnabled(enabled)
        if not enabled:
            self.setCursor(Qt.CursorShape.ForbiddenCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update()

    def _animate_step(self) -> None:
        diff = self._target_position - self._position
        if abs(diff) > 0.008:
            self._position += diff * 0.22
            self.update()
        else:
            self._position = self._target_position
            self._anim_timer.stop()
            self.update()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._enabled:
                return
            click_x = event.position().x()
            half_width = self.width() / 2.0
            new_checked = click_x < half_width
            if self._checked != new_checked:
                self.setChecked(new_checked)
                self.toggled.emit(new_checked)
            event.accept()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        outer_radius = 8.0  # Matches SecondaryBtn border-radius: 8px
        inner_radius = 6.0  # Matches button corner radius

        # Outer track background & border matching SecondaryBtn glass aesthetic
        track_bg = QColor(18, 24, 38, 240)
        rb = int(56 + (229 - 56) * self._position)
        gb = int(189 + (169 - 189) * self._position)
        bb = int(248 + (60 - 248) * self._position)
        border_color = QColor(max(0, min(255, rb)), max(0, min(255, gb)), max(0, min(255, bb)), 100)

        painter.setPen(QPen(border_color, 1))
        painter.setBrush(QBrush(track_bg))
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), outer_radius, outer_radius)

        # Active Pill Slider dimensions
        margin = 3.0
        active_h = h - (margin * 2.0)  # 26px
        active_w = (w - (margin * 2.0)) / 2.0  # 95px

        x_min = margin
        x_max = w - margin - active_w
        active_x = x_min + (x_max - x_min) * self._position

        # Active button gradient (Cloud Sky Cyan vs Local Amber)
        r1 = int(2 + (217 - 2) * self._position)
        g1 = int(132 + (145 - 132) * self._position)
        b1 = int(199 + (32 - 199) * self._position)

        r2 = int(56 + (245 - 56) * self._position)
        g2 = int(189 + (185 - 189) * self._position)
        b2 = int(248 + (66 - 248) * self._position)

        pill_c1 = QColor(max(0, min(255, r1)), max(0, min(255, g1)), max(0, min(255, b1)))
        pill_c2 = QColor(max(0, min(255, r2)), max(0, min(255, g2)), max(0, min(255, b2)))

        pill_grad = QLinearGradient(QPointF(active_x, margin), QPointF(active_x + active_w, margin + active_h))
        pill_grad.setColorAt(0.0, pill_c1)
        pill_grad.setColorAt(1.0, pill_c2)

        # Draw subtle shadow under active button
        shadow = QColor(0, 0, 0, 90)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(shadow))
        painter.drawRoundedRect(QRectF(active_x + 0.5, margin + 1.0, active_w, active_h), inner_radius, inner_radius)

        # Draw active button body
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        painter.setBrush(QBrush(pill_grad))
        painter.drawRoundedRect(QRectF(active_x, margin, active_w, active_h), inner_radius, inner_radius)

        # Render Segment Text Labels ("CLOUD LLM" / "OFFLINE" & "LOCAL LLM")
        font = painter.font()
        font.setFamily("Consolas, JetBrains Mono, Segoe UI, sans-serif")
        font.setPointSizeF(8.5)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        painter.setFont(font)

        left_text = "OFFLINE" if not self._enabled else "CLOUD LLM"
        right_text = "LOCAL LLM"


        if not self._enabled:
            left_color = QColor(148, 163, 184, 120)
            right_color = QColor(11, 14, 20)
        else:
            lr = int(11 + (125 - 11) * self._position)
            lg = int(14 + (211 - 14) * self._position)
            lb = int(20 + (252 - 20) * self._position)
            left_color = QColor(max(0, min(255, lr)), max(0, min(255, lg)), max(0, min(255, lb)))

            rr = int(243 + (11 - 243) * self._position)
            rg = int(194 + (14 - 194) * self._position)
            rb = int(103 + (20 - 103) * self._position)
            right_color = QColor(max(0, min(255, rr)), max(0, min(255, rg)), max(0, min(255, rb)))

        left_rect = QRectF(margin, margin, active_w, active_h)
        painter.setPen(QPen(left_color))
        painter.drawText(left_rect, int(Qt.AlignmentFlag.AlignCenter), left_text)

        right_rect = QRectF(margin + active_w, margin, active_w, active_h)
        painter.setPen(QPen(right_color))
        painter.drawText(right_rect, int(Qt.AlignmentFlag.AlignCenter), right_text)



QToggleSwitch = QSegmentedModeSwitch





class _Signals(QObject):
    log_signal = Signal(str)
    state_signal = Signal(str)
    subtitle_signal = Signal(str)
    level_signal = Signal(float)
    net_status_signal = Signal(bool)
    minimize_signal = Signal()
    restore_signal = Signal()
    click_through_signal = Signal(bool)


class JarvisHUDWindow(QMainWindow):
    def __init__(self, logo_path: str | None = None, start_minimized: bool = False) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._drag_pos: QPoint | None = None

        self._signals = _Signals()
        self._signals.log_signal.connect(self._append_log_threadsafe)
        self._signals.state_signal.connect(self._set_state_threadsafe)
        self._signals.subtitle_signal.connect(self._set_subtitle_threadsafe)
        self._signals.level_signal.connect(self._set_level_threadsafe)
        self._signals.net_status_signal.connect(self._on_network_status_updated)
        self._signals.minimize_signal.connect(self.showMinimized)
        self._signals.restore_signal.connect(self.showNormal)
        self._signals.click_through_signal.connect(self._set_click_through_threadsafe)

        self.on_text_command: Callable[[str], None] | None = None
        self.on_shutdown: Callable[[], None] | None = None
        self.on_hotkey: Callable[[], None] | None = None

        self._esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        self._esc_shortcut.activated.connect(self._trigger_in_app_hotkey)
        self._ctrl_space_shortcut = QShortcut(QKeySequence("Ctrl+Space"), self)
        self._ctrl_space_shortcut.activated.connect(self._trigger_in_app_hotkey)

        self._target_online = True
        self._current_color_factor = 1.0

        self._build_ui()
        self._apply_styling_factor(self._current_color_factor)
        self._check_network()

        self.net_timer = QTimer(self)
        self.net_timer.timeout.connect(self._check_network)
        self.net_timer.start(3000)

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._animate_theme_step)
        self.anim_timer.start(16)

        if start_minimized:
            self.showMinimized()
        else:
            self.show()

    def eventFilter(self, watched: QObject, event: Any) -> bool:
        if hasattr(self, "centralWidget") and watched == self.centralWidget():
            ev_type = event.type()
            if ev_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                if event.position().y() <= 65:
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            elif ev_type == QEvent.Type.MouseMove and bool(event.buttons() & Qt.MouseButton.LeftButton):
                if getattr(self, "_drag_pos", None) is not None:
                    if self.isFullScreen() or self.isMaximized():
                        self.showNormal()
                    self.move(event.globalPosition().toPoint() - self._drag_pos)
                    return True
            elif ev_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                if event.position().y() <= 65:
                    if self.isFullScreen() or self.isMaximized():
                        self.showNormal()
                    else:
                        self.showFullScreen()
                    return True
            elif ev_type == QEvent.Type.MouseButtonRelease:
                self._drag_pos = None

        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if event.position().y() <= 65:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if event.position().y() <= 65:
                if self.isFullScreen() or self.isMaximized():
                    self.showNormal()
                else:
                    self.showFullScreen()
                event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and hasattr(self, "_drag_pos") and self._drag_pos is not None:
            if self.isFullScreen() or self.isMaximized():
                self.showNormal()
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        self._drag_pos = None
        event.accept()

    def _check_network(self) -> None:
        import threading

        def worker() -> None:
            try:
                is_online = check_internet_connection()
                self._signals.net_status_signal.emit(is_online)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_network_status_updated(self, is_online: bool) -> None:
        from src.jarvis.utils.mode import update_physical_network_state
        update_physical_network_state(is_online)
        self._update_mode_ui()

    def _on_switch_toggled(self, checked: bool) -> None:
        from src.jarvis.utils.mode import (
            is_physical_network_available,
            set_user_online_preference,
        )
        if not is_physical_network_available():
            self.toggle_switch.setChecked(False)
            return

        set_user_online_preference(checked)
        self._update_mode_ui()

    def _update_mode_ui(self) -> None:
        from src.jarvis.utils.mode import (
            is_online_mode,
            is_physical_network_available,
        )
        has_net = is_physical_network_available()
        online_active = is_online_mode()

        self._target_online = online_active
        if hasattr(self, "orb"):
            self.orb.set_online(online_active)

        if hasattr(self, "toggle_switch"):
            self.toggle_switch.setToolTip("")
            if not has_net:
                self.toggle_switch.setEnabled(False)
                self.toggle_switch.setChecked(False)
            else:
                self.toggle_switch.setEnabled(True)
                self.toggle_switch.setChecked(online_active)


    @property
    def btn_online_toggle(self) -> QToggleSwitch:
        return self.toggle_switch

    def _animate_theme_step(self) -> None:
        target = 1.0 if self._target_online else 0.0
        diff = target - self._current_color_factor
        if abs(diff) > 0.002:
            self._current_color_factor += diff * 0.08
            self._apply_styling_factor(self._current_color_factor)
        elif self._current_color_factor != target:
            self._current_color_factor = target
            self._apply_styling_factor(self._current_color_factor)

    def _build_ui(self) -> None:
        self.resize(1080, 760)
        self.setMinimumSize(850, 620)

        central_widget = QWidget(self)
        central_widget.setObjectName("CentralWidget")
        central_widget.installEventFilter(self)
        self.setCentralWidget(central_widget)

        self.orb = JarvisHoloOrb(central_widget)
        self.orb.lower()

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(32, 20, 32, 32)
        layout.setSpacing(18)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        self.lbl_brand = QLabel("J.A.R.V.I.S.")
        self.lbl_brand.setObjectName("BrandLabel")

        self.brand_glow = QGraphicsDropShadowEffect(self.lbl_brand)
        self.brand_glow.setBlurRadius(20)
        self.brand_glow.setOffset(0, 0)
        self.lbl_brand.setGraphicsEffect(self.brand_glow)

        top_bar.addWidget(self.lbl_brand)
        top_bar.addStretch()

        self.lbl_mode_status = QLabel()  # Fallback property
        self.toggle_switch = QSegmentedModeSwitch(central_widget)
        self.toggle_switch.toggled.connect(self._on_switch_toggled)

        self.btn_settings = QPushButton("SETTINGS")
        self.btn_settings.setObjectName("SecondaryBtn")
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setAutoDefault(False)
        self.btn_settings.setDefault(False)
        self.btn_settings.clicked.connect(self._open_settings)
        top_bar.addWidget(self.btn_settings)

        self.btn_toggle_log = QPushButton("ACTIVITY LOG")
        self.btn_toggle_log.setObjectName("SecondaryBtn")
        self.btn_toggle_log.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_log.clicked.connect(self._toggle_log_drawer)
        top_bar.addWidget(self.btn_toggle_log)

        csd_box = QHBoxLayout()
        csd_box.setSpacing(6)

        self.btn_min = QPushButton("−")
        self.btn_min.setObjectName("CsdWindowBtn")
        self.btn_min.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_min.clicked.connect(self.showMinimized)
        csd_box.addWidget(self.btn_min)

        self.btn_max = QPushButton("□")
        self.btn_max.setObjectName("CsdWindowBtn")
        self.btn_max.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_max.clicked.connect(self._toggle_maximize)
        csd_box.addWidget(self.btn_max)

        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("CsdCloseBtn")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self.close)
        csd_box.addWidget(self.btn_close)

        top_bar.addLayout(csd_box)

        layout.addLayout(top_bar)
        layout.addStretch(1)

        self.lbl_subtitle = QLabel("Say 'Hey Jarvis' or type a command...")
        self.lbl_subtitle.setObjectName("SubtitleLabel")
        self.lbl_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_subtitle.setWordWrap(True)
        layout.addWidget(self.lbl_subtitle)

        bottom_hud = QHBoxLayout()
        bottom_hud.setContentsMargins(80, 0, 80, 0)

        self.txt_input = QLineEdit()
        self.txt_input.setObjectName("CommandInput")
        self.txt_input.setPlaceholderText("Ask Jarvis anything...")
        self.txt_input.returnPressed.connect(self._handle_submit)

        self.btn_send = QPushButton("ENTER", self.txt_input)
        self.btn_send.setObjectName("SendBtn")
        self.btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send.clicked.connect(self._handle_submit)

        input_layout = QHBoxLayout(self.txt_input)
        input_layout.setContentsMargins(4, 4, 6, 4)
        input_layout.addStretch()
        input_layout.addWidget(self.btn_send)

        bottom_hud.addWidget(self.txt_input, stretch=1)
        layout.addLayout(bottom_hud)

        self.log_drawer = QTextEdit()
        self.log_drawer.setObjectName("LogDrawer")
        self.log_drawer.setReadOnly(True)
        self.log_drawer.setFixedHeight(180)
        self.log_drawer.hide()
        layout.addWidget(self.log_drawer)
        self.txt_input.setFocus()

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _apply_styling_factor(self, factor: float) -> None:
        brand_color = lerp_rgb((255, 160, 30), (56, 189, 248), factor)
        glow_r = int(255 + (56 - 255) * factor)
        glow_g = int(160 + (189 - 160) * factor)
        glow_b = int(30 + (248 - 30) * factor)
        if hasattr(self, "brand_glow"):
            self.brand_glow.setColor(QColor(glow_r, glow_g, glow_b, 220))

        sec_bg = lerp_rgba((229, 169, 60, 0.08), (56, 189, 248, 0.08), factor)
        sec_border = lerp_rgba((229, 169, 60, 0.35), (56, 189, 248, 0.35), factor)
        sec_color = lerp_rgb((243, 194, 103), (125, 211, 252), factor)
        sec_hover_bg = lerp_rgba((229, 169, 60, 0.20), (56, 189, 248, 0.20), factor)
        sec_hover_border = lerp_rgba((229, 169, 60, 0.70), (56, 189, 248, 0.70), factor)

        inp_border = lerp_rgba((229, 169, 60, 0.30), (56, 189, 248, 0.30), factor)
        inp_focus = lerp_rgb((229, 169, 60), (56, 189, 248), factor)

        btn_g0 = lerp_rgb((217, 145, 32), (2, 132, 199), factor)
        btn_g1 = lerp_rgb((245, 185, 66), (56, 189, 248), factor)
        btn_h0 = lerp_rgb((232, 160, 44), (3, 105, 161), factor)
        btn_h1 = lerp_rgb((255, 208, 102), (125, 211, 252), factor)

        log_border = lerp_rgba((229, 169, 60, 0.35), (56, 189, 248, 0.35), factor)

        self.setStyleSheet(f"""
            QWidget#CentralWidget {{
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 #10141f,
                    stop: 0.5 #0a0d14,
                    stop: 1 #06070c
                );
                border: 1px solid {sec_border};
                border-radius: 16px;
            }}
            QLabel#BrandLabel {{
                color: {brand_color};
                font-family: 'Orbitron', 'Rajdhani', 'Consolas', 'JetBrains Mono', 'Segoe UI', sans-serif;
                font-size: 24px;
                font-weight: 900;
                letter-spacing: 0px;
                padding: 2px 6px;
            }}
            QPushButton#SecondaryBtn {{
                background: {sec_bg};
                border: 1px solid {sec_border};
                border-radius: 8px;
                color: {sec_color};
                font-size: 12px;
                font-weight: bold;
                letter-spacing: 2px;
                padding: 6px 16px;
            }}
            QPushButton#SecondaryBtn:hover {{
                background: {sec_hover_bg};
                border-color: {sec_hover_border};
            }}
            QPushButton#SecondaryBtn:disabled {{
                background: rgba(100, 116, 139, 0.12);
                border: 1px solid rgba(100, 116, 139, 0.3);
                color: #64748b;
            }}
            QPushButton#CsdWindowBtn {{
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                color: #94a3b8;
                font-size: 13px;
                font-weight: bold;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
            }}
            QPushButton#CsdWindowBtn:hover {{
                background: rgba(255, 255, 255, 0.15);
                color: #ffffff;
            }}
            QPushButton#CsdCloseBtn {{
                background: rgba(239, 68, 68, 0.12);
                border: 1px solid rgba(239, 68, 68, 0.3);
                border-radius: 6px;
                color: #f87171;
                font-size: 13px;
                font-weight: bold;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
            }}
            QPushButton#CsdCloseBtn:hover {{
                background: #ef4444;
                color: #ffffff;
            }}
            QLabel#SubtitleLabel {{
                color: {sec_color};
                font-family: 'Segoe UI', 'SF Pro Text', 'Ubuntu', sans-serif;
                font-size: 23px;
                font-weight: 400;
                line-height: 1.5;
                padding: 0 40px;
                min-height: 56px;
            }}
            QLineEdit#CommandInput {{
                background: rgba(18, 24, 38, 0.85);
                border: 1px solid {inp_border};
                border-radius: 12px;
                color: #ffffff;
                font-size: 16px;
                padding: 10px 100px 10px 20px;
            }}
            QLineEdit#CommandInput:focus {{
                border: 1.5px solid {inp_focus};
                background: rgba(24, 32, 50, 0.95);
            }}
            QPushButton#SendBtn {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {btn_g0}, stop:1 {btn_g1});
                border: none;
                border-radius: 8px;
                color: #0b0e14;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1.5px;
                padding: 8px 18px;
            }}
            QPushButton#SendBtn:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {btn_h0}, stop:1 {btn_h1});
            }}
            QTextEdit#LogDrawer {{
                background: rgba(9, 12, 18, 0.94);
                border: 1px solid {log_border};
                border-radius: 12px;
                color: #d1d5db;
                font-family: 'Consolas', 'JetBrains Mono', 'Segoe UI', monospace;
                font-size: 13px;
                padding: 12px;
            }}
        """)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self, color_factor=self._current_color_factor)
        if dlg.exec():
            self.write_log("SYS: API configuration updated.")

    def _toggle_log_drawer(self) -> None:
        if self.log_drawer.isVisible():
            self.log_drawer.hide()
            self.btn_toggle_log.setText("ACTIVITY LOG")
        else:
            self.log_drawer.show()
            self.btn_toggle_log.setText("HIDE LOG")

    def _handle_submit(self) -> None:
        text = self.txt_input.text().strip()
        if not text:
            return
        self.txt_input.clear()
        self.write_log(f"You: {text}")
        self.set_subtitle(text)
        if callable(self.on_text_command):
            self.on_text_command(text)

    def _trigger_in_app_hotkey(self) -> None:
        if callable(self.on_hotkey):
            self.on_hotkey()

    def set_state(self, state: str) -> None:
        self._signals.state_signal.emit(state)

    def set_subtitle(self, text: str) -> None:
        self._signals.subtitle_signal.emit(text)

    def set_audio_level(self, level: float) -> None:
        self._signals.level_signal.emit(level)

    def set_model(self, model_name: str) -> None:
        pass

    def write_log(self, text: str) -> None:
        self._signals.log_signal.emit(text)

    def minimize_window(self) -> None:
        self._signals.minimize_signal.emit()

    def restore_window(self) -> None:
        self._signals.restore_signal.emit()

    def _set_click_through_threadsafe(self, enabled: bool) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)

    def set_click_through(self, enabled: bool) -> None:
        self._signals.click_through_signal.emit(enabled)

    def _set_state_threadsafe(self, state: str) -> None:
        self.orb.set_state(state)
        st = state.strip().upper()
        if st in {"THINKING", "PROCESSING"}:
            self.lbl_subtitle.setText("Thinking...")
        elif st == "LISTENING":
            self.lbl_subtitle.setText("Listening...")
        elif st == "FOLLOW_UP":
            self.lbl_subtitle.setText("Listening for follow-up...")
        elif st in {"STANDBY", "IDLE"}:
            self.lbl_subtitle.setText("Say 'Hey Jarvis' or type a command...")

    def _set_subtitle_threadsafe(self, text: str) -> None:
        if not text or not text.strip():
            self.lbl_subtitle.setText("Say 'Hey Jarvis' or type a command...")
        elif text.startswith(("Say ", "Listening...", "Thinking...", "Speaking...")):
            self.lbl_subtitle.setText(text)
        else:
            hud_text = sanitize_hud_summary(text)
            self.lbl_subtitle.setText(hud_text)

    def _set_level_threadsafe(self, level: float) -> None:
        self.orb.set_audio_level(level)

    def _append_log_threadsafe(self, text: str) -> None:
        self.log_drawer.append(text)

    def _position_top_switch(self) -> None:
        cw = self.centralWidget()
        if hasattr(self, "toggle_switch") and cw is not None:
            w = cw.width()
            sw = self.toggle_switch.width()
            sx = int((w - sw) / 2.0)
            sy = 20
            self.toggle_switch.move(sx, sy)
            self.toggle_switch.raise_()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        cw = self.centralWidget()
        if hasattr(self, "orb") and cw is not None:
            self.orb.setGeometry(cw.rect())
            self.orb.lower()
        self._position_top_switch()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        cw = self.centralWidget()
        if hasattr(self, "orb") and cw is not None:
            self.orb.setGeometry(cw.rect())
            self.orb.lower()
        self._position_top_switch()
        if hasattr(self, "txt_input"):
            self.txt_input.setFocus()

    def closeEvent(self, event: Any) -> None:
        if callable(self.on_shutdown):
            self.on_shutdown()
        event.accept()
