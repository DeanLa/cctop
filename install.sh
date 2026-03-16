#!/bin/bash
set -euo pipefail

REPO_URL="https://github.com/DeanLa/cctop"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
BIN_DIR="$HOME/.local/bin"
BIN="$BIN_DIR/cctop"
SHARE_DIR="$HOME/.local/share/cctop"
SHARE_SCRIPTS_DIR="$SHARE_DIR/scripts"

# Parse flags
MODE="prod"
case "${1:-}" in
    --dev)  MODE="dev" ;;
    --prod) MODE="prod" ;;
esac

# --- Clean slate: remove previous install ---
rm -f "$BIN" "$HOME/bin/cctop"  # also clean legacy ~/bin location
rm -rf "$SHARE_DIR"

CLAUDE_AVAILABLE="false"
if command -v claude >/dev/null 2>&1; then
    CLAUDE_AVAILABLE="true"
    claude plugin marketplace remove cctop 2>/dev/null || true
fi

# --- Install the Claude Code plugin when Claude is available ---
if [ "$CLAUDE_AVAILABLE" = "true" ]; then
    if [ "$MODE" = "dev" ] && [ -f "$SCRIPT_DIR/.claude-plugin/marketplace.json" ]; then
        claude plugin marketplace add "$SCRIPT_DIR"
    else
        claude plugin marketplace add "$REPO_URL"
    fi
    claude plugin install cctop@cctop --scope user
fi

# --- Install standalone runtime for Codex / non-Claude usage ---
mkdir -p "$SHARE_SCRIPTS_DIR"
if [ "$MODE" = "dev" ]; then
    ln -snf "$SCRIPT_DIR/plugin/scripts" "$SHARE_SCRIPTS_DIR"
else
    cp -R "$SCRIPT_DIR/plugin/scripts/." "$SHARE_SCRIPTS_DIR/"
    chmod +x "$SHARE_SCRIPTS_DIR/"*.sh
fi

# --- Install the cctop CLI entry point ---
mkdir -p "$BIN_DIR"
if [ "$MODE" = "dev" ]; then
    ln -sf "$SCRIPT_DIR/bin/cctop" "$BIN"
    echo "Linked $BIN → local repo (dev mode)"
else
    cp "$SCRIPT_DIR/bin/cctop" "$BIN" 2>/dev/null || {
        curl -fsSL "$REPO_URL/raw/main/bin/cctop" -o "$BIN"
    }
    chmod +x "$BIN"
    echo "Installed cctop CLI to $BIN"
fi

# --- Check for jq ---
if [ "$CLAUDE_AVAILABLE" = "true" ] && ! command -v jq >/dev/null 2>&1; then
    echo ""
    echo "WARNING: jq is not installed. The cctop hook requires jq to track sessions."
    echo "Install it with: brew install jq (macOS) or apt install jq (Linux)"
fi

echo ""
if [ "$CLAUDE_AVAILABLE" = "true" ]; then
    echo "Claude plugin installed."
else
    echo "Claude CLI not found, skipped Claude plugin install."
fi
echo "Standalone runtime installed to $SHARE_DIR"
echo "Done ($MODE)! Run 'cctop' in a separate terminal to launch the dashboard."
