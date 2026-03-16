#!/bin/bash
set -euo pipefail

# Release script for cctop.
#
# Two subcommands:
#   ./release.sh bump <version>   Update version in plugin.json, print git log for Claude
#   ./release.sh tag              Read version from plugin.json, commit, tag, push
#
# Workflow (Claude orchestrates):
#   1. Claude runs: ./release.sh bump 0.2.0
#   2. Claude reads the git log output and writes a human-readable CHANGELOG.md entry
#   3. Claude commits plugin.json + CHANGELOG.md
#   4. Claude runs: ./release.sh tag

PLUGIN_JSON="plugin/.claude-plugin/plugin.json"
CHANGELOG="CHANGELOG.md"

cmd="${1:-}"

# ──────────────────────────────────────────────
# bump — update version, print raw git log
# ──────────────────────────────────────────────
if [[ "$cmd" == "bump" ]]; then
    VERSION="${2:-}"
    if [[ -z "$VERSION" ]]; then
        echo "Usage: ./release.sh bump <version>"
        exit 1
    fi
    if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Error: version must be semver (e.g. 1.0.0), got '$VERSION'"
        exit 1
    fi
    TAG="v$VERSION"
    if git rev-parse "$TAG" >/dev/null 2>&1; then
        echo "Error: tag $TAG already exists"
        exit 1
    fi

    CURRENT=$(python3 -c "import json; print(json.load(open('$PLUGIN_JSON'))['version'])")
    echo "Bumping: $CURRENT → $VERSION"

    # Update plugin.json
    python3 -c "
import json
with open('$PLUGIN_JSON') as f:
    data = json.load(f)
data['version'] = '$VERSION'
with open('$PLUGIN_JSON', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
"
    echo "Updated $PLUGIN_JSON"
    echo ""

    # Print git log since last tag for Claude to summarize
    LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
    echo "=== GIT LOG FOR CHANGELOG ==="
    if [[ -n "$LAST_TAG" ]]; then
        echo "(since $LAST_TAG)"
        git log "$LAST_TAG..HEAD" --pretty=format:"%h %s" --no-merges --reverse
    else
        echo "(all commits, no previous tag)"
        git log --pretty=format:"%h %s" --no-merges --reverse
    fi
    echo ""
    echo "=== END GIT LOG ==="
    echo ""
    echo "Next: write CHANGELOG.md entry, then run ./release.sh tag"

# ──────────────────────────────────────────────
# tag — commit, tag, push
# ──────────────────────────────────────────────
elif [[ "$cmd" == "tag" ]]; then
    VERSION=$(python3 -c "import json; print(json.load(open('$PLUGIN_JSON'))['version'])")
    TAG="v$VERSION"

    if git rev-parse "$TAG" >/dev/null 2>&1; then
        echo "Error: tag $TAG already exists"
        exit 1
    fi

    # Verify CHANGELOG.md mentions this version
    if [[ ! -f "$CHANGELOG" ]] || ! grep -q "$TAG\|$VERSION" "$CHANGELOG"; then
        echo "Error: $CHANGELOG does not mention $VERSION. Write the changelog entry first."
        exit 1
    fi

    # Verify plugin.json and CHANGELOG.md are staged or clean
    if [[ -z "$(git diff --cached --name-only)" ]]; then
        echo "Nothing staged. Stage your release commit first (plugin.json + CHANGELOG.md)."
        exit 1
    fi

    git commit --author="Dean's Agent <deanla+agent@gmail.com>" -m "$(cat <<EOF
Release $TAG
EOF
)"
    git tag "$TAG"
    echo "Created commit and tag $TAG"
    echo ""
    git push
    git push --tags
    echo ""

    # Extract this version's changelog entry for the GitHub Release body.
    # Grabs everything between "## vX.Y.Z" and the next "## v" heading (or EOF).
    RELEASE_BODY=$(sed -n "/^## $TAG/,/^## v/{/^## v/!p;}" "$CHANGELOG" | sed '/^## /d; /^$/N;/^\n$/d')

    # Create GitHub Release
    GH_HOST=github.com gh release create "$TAG" \
        --title "$TAG" \
        --notes "$RELEASE_BODY" \
        --latest
    echo ""
    echo "Release $TAG pushed and GitHub Release created."

# ──────────────────────────────────────────────
# help
# ──────────────────────────────────────────────
else
    echo "Usage:"
    echo "  ./release.sh bump <version>   Bump version in plugin.json, show git log"
    echo "  ./release.sh tag              Commit staged changes, tag, and push"
    echo ""
    echo "Workflow:"
    echo "  1. ./release.sh bump 0.2.0"
    echo "  2. Write CHANGELOG.md entry (Claude does this)"
    echo "  3. git add plugin.json CHANGELOG.md"
    echo "  4. ./release.sh tag"
    exit 1
fi
