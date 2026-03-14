#!/bin/bash
# Launch cctop — Claude Code Sessions dashboard with the background poller
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

# Start the poller in the background
uv run --script "$SCRIPT_DIR/cctop-poller.py" &
POLLER_PID=$!

# Kill the poller when this script exits (dashboard quit, ctrl-c, etc.)
trap "kill $POLLER_PID 2>/dev/null; wait $POLLER_PID 2>/dev/null" EXIT

# Run the dashboard in the foreground
uv run --script "$SCRIPT_DIR/cctop_dashboard.py"
