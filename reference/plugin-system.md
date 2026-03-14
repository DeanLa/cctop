# Plugin System

> How Claude Code plugins are structured, installed, and managed.

## Directory Structure

```
~/.claude/plugins/
  installed_plugins.json        # Registry of all installed plugins
  known_marketplaces.json       # Registered marketplace sources
  cache/<marketplace>/<plugin>/<version>/   # Installed plugin files (COPIES)
  marketplaces/<name>/          # Cloned marketplace repos
```

## Installing a Local Plugin as a Marketplace

**Step 1:** Add a marketplace manifest to your plugin source:

`.claude-plugin/marketplace.json`:
```json
{
  "name": "my-plugin",
  "owner": { "name": "Author Name" },
  "plugins": [
    {
      "name": "my-plugin",
      "source": "./",
      "description": "Description of the plugin"
    }
  ]
}
```

**Step 2:** Register the local directory as a marketplace:
```bash
claude plugin marketplace add /path/to/plugin-source
```

**Step 3:** Install the plugin:
```bash
claude plugin install my-plugin@my-plugin --scope user
```

**Step 4:** The plugin is now COPIED into the cache directory and activated.

## Plugin Manifest

`.claude-plugin/plugin.json`:
```json
{
  "name": "plugin-name",
  "description": "What this plugin does",
  "author": { "name": "Author Name" }
}
```

## Plugin Hooks

`hooks/hooks.json`:
```json
{
  "description": "Hook descriptions for this plugin",
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/scripts/handler.sh"
          }
        ]
      }
    ]
  }
}
```

- `${CLAUDE_PLUGIN_ROOT}` resolves to the **cache installPath**, NOT the original source directory
- Plugin hooks are merged with user-scope and project-scope hooks; no conflicts between them
- Multiple plugins can hook the same event

## Activation

In `~/.claude/settings.json`, the `enabledPlugins` object controls which plugins are active:

```json
{
  "enabledPlugins": {
    "my-plugin@my-marketplace": true
  }
}
```

The key format is `<plugin-name>@<marketplace-name>`.

## Critical Gotchas

1. **Cache is a COPY, not a symlink.** After editing plugin source files, you MUST re-run `claude plugin install <name>@<marketplace> --scope user` to update the cached copy.

2. **Cache is aggressively managed.** Claude Code may replace symlinks with copies and delete unrecognized directories inside the cache tree. Do not manually place files in the cache.

3. **`claude plugin update` is broken for directory-source marketplaces.** It DELETES the cache copy without rebuilding it. Always use `claude plugin install` instead of `update` for local development.

4. **`${CLAUDE_PLUGIN_ROOT}` resolves to the cache path.** Scripts referenced in `hooks.json` must use this variable to point to files within the installed (cached) copy.

5. **Re-install workflow during development:**
   ```bash
   # After editing any plugin source file:
   claude plugin install my-plugin@my-marketplace --scope user
   # Then restart Claude Code (or start a new session) for hooks to take effect
   ```
