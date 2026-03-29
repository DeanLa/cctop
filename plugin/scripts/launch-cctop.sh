#!/bin/bash
# Launch cctop — Claude Code Sessions dashboard with the background poller
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

# Handle --reset: wipe session data but preserve config
if [[ " $* " == *" --reset "* ]]; then
    rm -f ~/.cctop/*.json ~/.cctop/*.poller.json ~/.cctop/*.debug.jsonl
    mkdir -p ~/.cctop
    echo "cctop: session data cleared"
fi

# Start the poller in the background
uv run --script "$SCRIPT_DIR/cctop-poller.py" &
POLLER_PID=$!

# Clean up on exit: kill poller and restore tmux automatic window naming
trap "kill $POLLER_PID 2>/dev/null; wait $POLLER_PID 2>/dev/null; \
  [ -n \"\$TMUX\" ] && tmux set-option -w automatic-rename on" EXIT

# Set tmux tab title (if running inside tmux)
[ -n "$TMUX" ] && tmux rename-window "cctop"

# Run the dashboard in the foreground
uv run --script "$SCRIPT_DIR/cctop_dashboard.py" "$@"
