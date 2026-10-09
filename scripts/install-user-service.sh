#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_PATH="$SERVICE_DIR/jarvis.service"
PYTHON_EXECUTABLE="$(command -v python3)"
if [[ -z "$PYTHON_EXECUTABLE" ]]; then
  printf '%s\n' "python3 was not found on PATH" >&2
  exit 1
fi
mkdir -p "$SERVICE_DIR"
sed \
  -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
  -e "s|__PYTHON_EXECUTABLE__|$PYTHON_EXECUTABLE|g" \
  "$PROJECT_ROOT/systemd/user/jarvis.service.in" > "$SERVICE_PATH"
if grep -Eq '__PROJECT_ROOT__|__PYTHON_EXECUTABLE__' "$SERVICE_PATH"; then
  printf '%s\n' "Service template contains unresolved placeholders: $SERVICE_PATH" >&2
  exit 1
fi
systemctl --user daemon-reload
systemctl --user enable --now jarvis.service
printf '%s\n' "Installed and started: $SERVICE_PATH"
printf '%s\n' "Check status with: systemctl --user status jarvis.service"
printf '%s\n' "View logs with: journalctl --user -u jarvis.service -f"
