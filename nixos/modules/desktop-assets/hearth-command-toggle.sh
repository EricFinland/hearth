#!/usr/bin/env bash
# Toggle the hearth command center: a frameless Firefox kiosk window pointed at
# the local command page. If it is open, close it; otherwise open it.
set -euo pipefail
URL="http://localhost:8770/command"
if pgrep -f "hearth-command-kiosk" >/dev/null; then
  pkill -f "hearth-command-kiosk" || true
else
  firefox --kiosk --new-instance --class hearth-command-kiosk "$URL" \
    --profile "$HOME/.hearth-command-profile" >/dev/null 2>&1 &
fi
