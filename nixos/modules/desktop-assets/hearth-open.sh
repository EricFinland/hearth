#!/usr/bin/env bash
# Open the hearth cockpit (the animated world) in a frameless Chrome app window.
# Idempotent: if a hearth window is already open, do nothing. Used by the desktop
# icon, the login autostart, and the app-menu entry. Chrome app mode with a
# dedicated profile dir avoids the Firefox profile-lock error.
set -euo pipefail
URL="http://localhost:8770/world"
PROFILE="$HOME/.hearth-app-profile"
if pgrep -f "hearth-app-profile" >/dev/null; then
  exit 0
fi
mkdir -p "$PROFILE"
google-chrome-stable \
  --user-data-dir="$PROFILE" \
  --app="$URL" \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=Translate >/dev/null 2>&1 &
