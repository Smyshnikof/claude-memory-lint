# Memory Lint for Claude Code — VS Code extension

<img src="icon.png" width="96" align="right" alt="extension icon">

Sidebar companion for the linter in this repo: the same integrity checks for
Claude Code's file-based memory, one click away in the editor.

**Install:** [VS Code Marketplace →](https://marketplace.visualstudio.com/items?itemName=smyshnikof.claude-memory-lint-vscode)
(or search for “Memory Lint for Claude Code” in the Extensions view).

![demo](https://raw.githubusercontent.com/Smyshnikof/claude-memory-lint/main/vscode-extension/demo.gif)

## Features

- **`[✓]` icon in the Activity Bar** → “Claude Memory” panel. ↻ checks every
  memory folder (`--all`), 🔧 runs `--fix` after a modal confirmation.
- **Badge on the icon** — the number of issues (like the git counter).
- **Findings grouped by project** — one row per memory folder with note/issue
  counts; click a project to open its `MEMORY.md`. Red — broken
  `[[links]]`/paths/duplicates, yellow — orphans and frontmatter. Click a
  finding to open the note at the offending line. The panel header shows when
  the last check ran.
- **Editor squiggles** + the Problems panel — the same findings.
- **Ctrl+click on a `[[wikilink]]`** in a memory note opens its target.
- **Auto-check**: on VS Code startup and whenever a memory note is saved.

## Privacy

The extension reads **only** `~/.claude/projects/*/memory/*.md` — Claude
Code's memory folders — regardless of the open workspace. No network, no
telemetry: nothing leaves your machine. Files are written only by the explicit
`--fix` command, behind a modal confirmation. The startup check can be disabled
with `memoryLint.runOnStartup`.

## Requirements

Python 3.8+ on `PATH` (or set `memoryLint.pythonPath`). The linter itself is
bundled — no other setup.

## Settings

- `memoryLint.runOnStartup` — check automatically on startup (default: on).
- `memoryLint.pythonPath` — interpreter for `check.py` (default: `python`).
- `memoryLint.scriptPath` — custom `check.py` instead of the bundled copy.

## Development

Plain JS, zero npm dependencies. Open this folder in VS Code and press **F5**
(the dev host launches with `--disable-extensions`). In dev mode the extension
prefers the sibling `../claude_memory_lint.py`, so linter changes apply without
rebuilding.

`check.py` here is a **build artifact** — a verbatim copy of
`../claude_memory_lint.py` bundled into the `.vsix`. After changing the linter,
refresh it before packaging:

```bash
cp ../claude_memory_lint.py check.py
npx @vscode/vsce package
```

UI strings ship in English with a Russian locale (`package.nls.ru.json`).
