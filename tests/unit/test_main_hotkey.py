import main


def test_hotkey_mode_is_pure_ipc_client(monkeypatch):
    called = []

    def fake_send():
        called.append(True)
        return True

    monkeypatch.setattr("src.jarvis.input.hotkey.send_hotkey_trigger", fake_send)
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--hotkey"])

    assert main.main() == 0
    assert called == [True]


def test_hotkey_mode_propagates_ipc_failure(monkeypatch):
    monkeypatch.setattr("src.jarvis.input.hotkey.send_hotkey_trigger", lambda: False)
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--hotkey"])

    assert main.main() == 1
