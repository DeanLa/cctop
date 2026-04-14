#!/bin/bash
set -euo pipefail

REPO_URL="https://github.com/DeanLa/cctop"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
BIN_DIR="$HOME/.local/bin"
BIN="$BIN_DIR/cctop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
    cat <<'EOF'
Usage: install.sh [OPTIONS]

Install or update the cctop Claude Code plugin and CLI.

Options:
  --dev           Install from local repo (symlinks CLI, re-registers plugin)
  --prod          Install from GitHub (default)
  --wt <prefix>   Dev-install from a worktree matching <prefix>
                  Matches start of worktree name, strips worktree[s]- prefix.
                  Errors on zero or ambiguous matches.
  --run           Launch cctop after successful install
  --debug         Also install the cctop-debug plugin (full event logging)
  --help          Show this help

Examples:
  ./install.sh                     # fresh install / upgrade from GitHub
  ./install.sh --dev               # dev install from current directory
  ./install.sh --dev --wt pr-v     # dev install from worktree starting with "pr-v"
  ./install.sh --dev --wt pr --run # dev install from worktree, then launch
EOF
    exit 0
}

parse_flags() {
    MODE="prod"
    DEBUG=""
    RUN=""
    WORKTREE_PREFIX=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help)  usage ;;
            --dev)   MODE="dev" ;;
            --prod)  MODE="prod" ;;
            --debug) DEBUG="1" ;;
            --run)   RUN="1" ;;
            --wt)    WORKTREE_PREFIX="${2:?--wt requires a prefix}"; shift ;;
        esac
        shift
    done
}

resolve_worktree() {
    # Delegates to the matching worktree's install.sh and never returns.
    local prefix="$1"

    if [[ "$MODE" != "dev" ]]; then
        echo "Error: --wt requires --dev" >&2
        exit 1
    fi

    local wt_dir="$SCRIPT_DIR/.claude/worktrees"
    if [[ ! -d "$wt_dir" ]]; then
        echo "Error: no worktrees directory at $wt_dir" >&2
        exit 1
    fi

    local matches=()
    for dir in "$wt_dir"/*/; do
        [[ -d "$dir" ]] || continue
        local name
        name=$(basename "$dir")
        # Strip optional worktree[s]- prefix
        local stripped="${name#worktrees-}"
        stripped="${stripped#worktree-}"
        # Match start of the (stripped) name
        if [[ "$stripped" == "$prefix"* ]]; then
            matches+=("$name")
        fi
    done

    if [[ ${#matches[@]} -eq 0 ]]; then
        echo "Error: no worktree matching '$prefix'" >&2
        exit 1
    elif [[ ${#matches[@]} -gt 1 ]]; then
        echo "Error: ambiguous prefix '$prefix', matches:" >&2
        for m in "${matches[@]}"; do
            echo "  $m" >&2
        done
        exit 1
    fi

    echo "Using worktree: ${matches[0]}"
    "$wt_dir/${matches[0]}/install.sh" --dev ${DEBUG:+--debug}
    # --run is handled by the outer script after this function returns
}

cleanup_legacy() {
    claude plugin uninstall cctop-debug@cctop 2>/dev/null || true
    rm -f "$BIN" "$HOME/bin/cctop"
}

install_plugin() {
    if [[ "$MODE" = "dev" ]] && [[ -f "$SCRIPT_DIR/.claude-plugin/marketplace.json" ]]; then
        claude plugin marketplace remove cctop 2>/dev/null || true
        claude plugin marketplace add "$SCRIPT_DIR"
        claude plugin install cctop@cctop --scope user
    elif claude plugin marketplace update cctop 2>/dev/null \
      && claude plugin update cctop@cctop --scope user 2>/dev/null; then
        # Upgrade in-place - avoids removing the plugin entry, which would
        # fire SessionEnd on running sessions and wipe their ~/.cctop/*.json files
        echo "Upgraded cctop plugin"
    else
        # Fresh install: marketplace not yet registered
        claude plugin marketplace add "$REPO_URL"
        claude plugin install cctop@cctop --scope user
    fi

    if [[ -n "$DEBUG" ]]; then
        claude plugin install cctop-debug@cctop --scope user
        echo "Installed cctop-debug plugin (full event logging)"
    fi
}

install_cli() {
    mkdir -p "$BIN_DIR"
    if [[ "$MODE" = "dev" ]]; then
        ln -sf "$SCRIPT_DIR/plugin/scripts/launch-cctop.sh" "$BIN"
        echo "Linked $BIN -> local repo (dev mode)"
    else
        cp "$SCRIPT_DIR/bin/cctop" "$BIN" 2>/dev/null || {
            curl -fsSL "$REPO_URL/raw/main/bin/cctop" -o "$BIN"
        }
        chmod +x "$BIN"
        echo "Installed cctop CLI to $BIN"
    fi
}

check_deps() {
    if ! command -v jq >/dev/null 2>&1; then
        echo ""
        echo "WARNING: jq is not installed. The cctop hook requires jq to track sessions."
        echo "Install it with: brew install jq (macOS) or apt install jq (Linux)"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

parse_flags "$@"

if [[ -n "$WORKTREE_PREFIX" ]]; then
    resolve_worktree "$WORKTREE_PREFIX"
else
    cleanup_legacy
    install_plugin
    install_cli
    check_deps
    echo ""
    echo "Done ($MODE)!"
fi

if [[ -n "$RUN" ]]; then
    echo "Launching cctop..."
    exec cctop
else
    echo "Run 'cctop' in a separate terminal to launch the dashboard."
fi
