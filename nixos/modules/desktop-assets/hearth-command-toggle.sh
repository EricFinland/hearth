#!/usr/bin/env bash
# Toggle the hearth command center: a frameless Chrome app window pointed at the
# local world page. If it is open, close it; otherwise open it.
#
# Chrome app mode with a DEDICATED user-data-dir is used instead of Firefox: the
# Firefox kiosk path threw "your Firefox profile cannot be loaded" because it
# fought the default profile lock. A private Chrome profile dir never collides.
set -euo pipefail
URL="http://localhost:8770/world"
PROFILE="$HOME/.hearth-kiosk-profile"
# The user-data-dir path is unique to this window, so it is a safe toggle marker.
if pgrep -f "hearth-kiosk-profile" >/dev/null; then
  pkill -f "hearth-kiosk-profile" || true
else
  mkdir -p "$PROFILE"
  google-chrome-stable \
    --user-data-dir="$PROFILE" \
    --app="$URL" \
    --start-fullscreen \
    --no-first-run \
    --no-default-browser-check \
    --disable-features=Translate >/dev/null 2>&1 &
fi
