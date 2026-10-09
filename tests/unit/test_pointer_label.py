from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "gnome-extension" / "deskwright@zeticle.com"


def test_pointer_label_is_text_only_pill_and_does_not_intercept_input():
    source = (EXTENSION / "pointer_label.js").read_text(encoding="utf-8")

    assert "text: 'JARVIS'" in source
    assert "border-radius: 999px;" in source
    assert "reactive: false" in source
    assert "global.stage.add_child(this._actor)" in source
    assert "global.stage.remove_child(this._actor)" in source
    assert "global.get_pointer()" in source
    assert "POINTER_OFFSET_X" in source and "POINTER_OFFSET_Y" in source
    assert "Math.max(74, measuredWidth)" in source
    assert "GLib.get_monotonic_time()" in source


def test_pointer_label_tracks_native_cursor_visibility_and_hides_with_cursor():
    source = (EXTENSION / "pointer_label.js").read_text(encoding="utf-8")

    assert "import Meta from 'gi://Meta';" in source
    assert "get_cursor_tracker()" in source
    assert "get_pointer_visible()" in source
    assert "get_sprite()" in source
    assert "'visibility-changed'" in source
    assert "'cursor-changed'" in source
    assert "_syncCursorVisibility()" in source
    assert "this._actor.hide()" in source
    assert "this._actor.show()" in source
    assert "_disconnectCursorTracker()" in source


def test_pointer_label_dbus_method_has_expected_signature():
    source = (EXTENSION / "dbus.js").read_text(encoding="utf-8")
    match = re.search(r"const IFACE = `(?P<xml>.*?)`;", source, re.DOTALL)
    assert match, "Could not find D-Bus interface declaration"
    root = ET.fromstring(match.group("xml"))
    methods = {method.attrib["name"]: method for method in root.findall(".//method")}
    method = methods["SetPointerLabelActive"]
    args = [(arg.attrib["type"], arg.attrib["direction"]) for arg in method.findall("arg")]
    assert args == [("b", "in"), ("d", "in"), ("b", "out")]
    assert "'SetPointerLabelActive'" in source  # advertised through Ping()


def test_extension_sweeps_orphaned_pointer_pill_on_enable():
    source = (EXTENSION / "indicator.js").read_text(encoding="utf-8")
    assert "const POINTER_LABEL_NAME = 'jarvis-pointer-pill';" in source
    assert "actor.get_name() === POINTER_LABEL_NAME" in source
    assert "global.stage.get_children()" in source


def test_deskwright_controls_pointer_label_for_whole_context_with_lease():
    source = (
        ROOT / "src" / "jarvis" / "computer_use" / "computers" / "deskwright" / "deskwright.py"
    ).read_text(encoding="utf-8")

    assert "enable_pointer_label: bool = True" in source
    assert "self._start_pointer_label()" in source
    assert "self._stop_pointer_label()" in source
    assert "SetPointerLabelActive" in source
    assert "jarvis-pointer-label-heartbeat" in source
    assert "thread.join(timeout=3.0)" in source
    assert "self._set_pointer_label_active(False)" in source
