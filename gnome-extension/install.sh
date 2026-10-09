#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_SRC="$SCRIPT_DIR/deskwright@zeticle.com"
EXT_DST="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/deskwright@zeticle.com"
mkdir -p "$(dirname "$EXT_DST")"
rm -rf "$EXT_DST"
cp -a "$EXT_SRC" "$EXT_DST"
printf '%s\n' "Installed Deskwright GNOME extension to $EXT_DST"
printf '%s\n' "Voice capture is handled by JARVIS's Python VoiceEngine."

# The extension must be enabled for its D-Bus service (com.zeticle.deskwright)
# to exist. GNOME Settings still owns the global keyboard shortcut itself.
if command -v gnome-extensions >/dev/null 2>&1; then
    if gnome-extensions enable deskwright@zeticle.com >/dev/null 2>&1; then
        printf '%s\n' "Enabled deskwright@zeticle.com."
    else
        printf '%s\n' "Could not enable deskwright@zeticle.com automatically; enable it in GNOME Extensions."
    fi
fi
