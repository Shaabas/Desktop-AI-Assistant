from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_service_installer_resolves_project_and_python_placeholders(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    systemctl.chmod(0o755)

    config_home = tmp_path / "config"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(config_home)

    subprocess.run(
        ["bash", str(project / "scripts" / "install-user-service.sh")],
        cwd=project,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    service = config_home / "systemd" / "user" / "jarvis.service"
    content = service.read_text(encoding="utf-8")
    assert "__PROJECT_ROOT__" not in content
    assert "__PYTHON_EXECUTABLE__" not in content
    assert f"WorkingDirectory={project}" in content
    assert "ExecStart=/" in content
