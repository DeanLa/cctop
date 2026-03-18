#!/bin/bash
set -euo pipefail

REPO_URL="https://github.com/DeanLa/cctop"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
BIN_DIR="$HOME/.local/bin"
BIN="$BIN_DIR/cctop"

# Parse flags
MODE="prod"
DEBUG=""
for arg in "$@"; do
    case "$arg" in
        --dev)   MODE="dev" ;;
        --prod)  MODE="prod" ;;
        --debug) DEBUG="1" ;;
    esac
done

# --- Clean slate: remove previous install ---
claude plugin marketplace remove cctop 2>/dev/null || true
claude plugin uninstall cctop-debug@cctop 2>/dev/null || true
rm -f "$BIN" "$HOME/bin/cctop"  # also clean legacy ~/bin location

# --- Install the Claude Code plugin ---
if [ "$MODE" = "dev" ] && [ -f "$SCRIPT_DIR/.claude-plugin/marketplace.json" ]; then
    claude plugin marketplace add "$SCRIPT_DIR"
else
    claude plugin marketplace add "$REPO_URL"
fi
claude plugin install cctop@cctop --scope user

# --- Optionally install the debug plugin ---
if [ -n "$DEBUG" ]; then
    claude plugin install cctop-debug@cctop --scope user
    echo "Installed cctop-debug plugin (full event logging)"
fi

# --- Install the cctop CLI entry point ---
mkdir -p "$BIN_DIR"
if [ "$MODE" = "dev" ]; then
    ln -sf "$SCRIPT_DIR/plugin/scripts/launch-cctop.sh" "$BIN"
    echo "Linked $BIN → local repo (dev mode)"
else
    cp "$SCRIPT_DIR/bin/cctop" "$BIN" 2>/dev/null || {
        curl -fsSL "$REPO_URL/raw/main/bin/cctop" -o "$BIN"
    }
    chmod +x "$BIN"
    echo "Installed cctop CLI to $BIN"
fi

# --- Check for jq ---
if ! command -v jq >/dev/null 2>&1; then
    echo ""
    echo "WARNING: jq is not installed. The cctop hook requires jq to track sessions."
    echo "Install it with: brew install jq (macOS) or apt install jq (Linux)"
fi

echo ""
echo "Done ($MODE)! Run 'cctop' in a separate terminal to launch the dashboard."
