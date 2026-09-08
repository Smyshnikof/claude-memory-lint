# claude-memory-lint

Integrity checks for [Claude Code](https://claude.com/claude-code)'s file-based memory.

[![tests](https://github.com/Smyshnikof/claude-memory-lint/actions/workflows/ci.yml/badge.svg)](https://github.com/Smyshnikof/claude-memory-lint/actions/workflows/ci.yml)
[![VS Code extension](https://img.shields.io/visual-studio-marketplace/v/smyshnikof.claude-memory-lint-vscode?label=VS%20Code%20extension&color=BF4F33)](https://marketplace.visualstudio.com/items?itemName=smyshnikof.claude-memory-lint-vscode)
[![installs](https://img.shields.io/visual-studio-marketplace/i/smyshnikof.claude-memory-lint-vscode?color=BF4F33)](https://marketplace.visualstudio.com/items?itemName=smyshnikof.claude-memory-lint-vscode)

[Русская версия](README.ru.md)

## The problem

Claude Code keeps long-term memory as a folder of markdown notes:

```
~/.claude/projects/<project-slug>/memory/
    MEMORY.md          <- the index loaded into context every session
    some-note.md       <- one fact per file, linked with [[wikilinks]]
```

Nothing validates that folder, and it rots in a way you cannot see.

A note gets merged into another one. Six notes still link to the old name. Those
links now resolve to nothing — and there is no error, no warning, no red squiggle.
The assistant simply stops seeing that memory. You keep believing a fact is
remembered; it is not.

I found this in my own assistant repo: **145 dead link targets across 234
occurrences in a 505-note memory folder.** Some had been dead for months. The
root cause turned out to be one convention drift — 128 notes had a `name:` in
`kebab-case` while their file was `snake_case`, so every link written from a
note's own slug pointed at a filename that never existed.

This tool finds that, and repairs the unambiguous part of it.

As of the first release (September 2026), nothing else did — not the
ecosystem, not Claude Code itself. Anthropic, you're welcome 😌 — and if you
ever ship this natively, this repo will retire with honor: that would be the
best possible outcome for everyone's memory.

## Install

No dependencies, standard library only, Python 3.8+.

```bash
git clone https://github.com/Smyshnikof/claude-memory-lint
cd claude-memory-lint
python claude_memory_lint.py
```

## Use

```bash
python claude_memory_lint.py              # auto-discover the memory folder
python claude_memory_lint.py PATH         # lint a specific folder
python claude_memory_lint.py --all        # lint every project you have
python claude_memory_lint.py --fix        # repair what is unambiguous
python claude_memory_lint.py --json       # machine-readable, for CI
python claude_memory_lint.py --list       # show discovered memory folders
```

Exit code is `0` when clean and `1` when issues remain, so it drops into CI or a
git hook as is.

```
✗ ~/.claude/projects/my-project/memory
  505 notes, 3 issues

  broken-link: 2  (2 fixable with --fix)
    project_alpha.md:18  [[project_old_name]] does not exist -> did you mean [[project_new_name]]?
    ref_sources.md:16  [[tone-of-voice]] does not exist -> did you mean [[feedback_tone_of_voice]]?

  orphan: 1
    ref_stray_finding.md  unreachable from MEMORY.md - nothing links here
```

## VS Code extension

<img src="vscode-extension/icon.png" width="96" align="right" alt="extension icon">

The same checks as a sidebar panel:
[**Claude Memory Lint** on the Marketplace](https://marketplace.visualstudio.com/items?itemName=smyshnikof.claude-memory-lint-vscode)
— an Activity Bar icon with an issue-count badge, a findings list that jumps to
the offending line, editor squiggles, Ctrl+click navigation on `[[wikilinks]]`,
and an auto-check on startup and on save. Reads only
`~/.claude/projects/*/memory/`, no network, no telemetry.

![demo](vscode-extension/demo.gif)

Source lives in [`vscode-extension/`](vscode-extension/).

## What it checks

| Check | What it means |
|---|---|
| `broken-link` | `[[wikilink]]` pointing at a note that does not exist |
| `broken-path` | relative `./file.md` link pointing at a missing file |
| `orphan` | note unreachable from `MEMORY.md` across the whole link graph |
| `name` | frontmatter `name:` missing, or not equal to the filename |
| `description` | frontmatter `description:` missing — recall cannot rank the note |
| `duplicate` | two notes claiming the same `name:` |

Two decisions worth explaining:

**Orphans are computed over the whole graph, not over the index.** A note linked
only from a hub note is perfectly findable, and counting index coverage alone
would report hundreds of false orphans. Reachability from `MEMORY.md` is the
question that actually matters: can the assistant get here at all?

**`name:` must equal the filename.** Links resolve by filename. A `name:` that
disagrees with its file means every link written from that note's own slug lands
nowhere — which is the single most common cause of `broken-link`. Index notes
(`index-*.md`) and the entry note are exempt: they carry routes, not facts.

## What `--fix` will and will not do

It repairs only what is unambiguous:

- a broken link whose target matches exactly one existing note once case and
  separators are folded (`[[my-note]]` → `[[my_note]]`)
- a broken link that matches one note when a type prefix is allowed
  (`[[tone-of-voice]]` → `[[feedback_tone_of_voice]]`)
- a `name:` that disagrees with its filename

It will never invent a target, never pick between two candidates, and never
touch a `[[link]]` inside a code span — that is documentation, not a link.
Everything else is reported for you to decide. Your memory is not a place for a
tool to guess.

## Config

Optional, at `<memory>/.memory-lint.json`:

```json
{
  "entry": "MEMORY.md",
  "allow_dangling": ["note-i-still-have-to-write"],
  "ignore": ["scratch_*.md"],
  "index_glob": ["index-*.md"]
}
```

A dangling link is a legitimate way to mark *"this memory is worth writing"*.
List those under `allow_dangling` so they stop being reported — and keep that
list short, because every entry in it is a debt.

## In CI

```yaml
- run: python claude_memory_lint.py ~/.claude/projects/my-project/memory
```

Or as a pre-commit hook, if you keep a backup copy of your memory in a repo:

```bash
python claude_memory_lint.py "$MEMORY_DIR" || exit 1
```

## Tests

```bash
python -m unittest discover -s tests
```

27 tests, each building a throwaway memory folder with one specific defect, so a
failure names the check that broke.

## License

MIT.
