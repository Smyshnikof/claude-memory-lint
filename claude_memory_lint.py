#!/usr/bin/env python3
"""claude-memory-lint - integrity checks for Claude Code's file-based memory.

Claude Code keeps long-term memory as a folder of markdown notes:

    ~/.claude/projects/<project-slug>/memory/
        MEMORY.md          <- the index loaded into context every session
        some-note.md       <- one fact per file, linked with [[wikilinks]]

Nothing validates that folder. Links rot silently: a note gets merged or
renamed, the links pointing at it keep resolving to nothing, and the assistant
simply stops seeing that memory. There is no error - just a fact that quietly
stops being recalled.

This script finds that rot, and can repair the unambiguous part of it.

Checks
    1. broken-link    [[wikilink]] pointing at a note that does not exist
    2. broken-path    relative ./file.md link pointing at a missing file
    3. orphan         note unreachable from MEMORY.md across the whole link
                      graph (hub notes count as routes, not just the index)
    4. name           frontmatter `name:` missing, or not equal to the filename
    5. description    frontmatter `description:` missing or empty
    6. duplicate      two notes claiming the same `name:`

Checks 4-6 skip the entry note and index notes (index-*.md by default): an
index carries routes, not a remembered fact. They still count as routes for
check 3, which is the whole point of having them.

Why check 4 is strict: links resolve by filename, so a `name:` that disagrees
with the filename means every [[link]] written from that note's own slug lands
nowhere. That single mismatch is the most common cause of check 1.

Usage
    claude_memory_lint.py                 # auto-discover the memory folder
    claude_memory_lint.py PATH            # lint a specific folder
    claude_memory_lint.py --all           # lint every project found
    claude_memory_lint.py --fix           # repair what is unambiguous
    claude_memory_lint.py --json          # machine-readable output (CI)
    claude_memory_lint.py --list          # list discovered memory folders

Exit code 0 when clean, 1 when issues remain, 2 on usage errors.

Config (optional), at <memory>/.memory-lint.json:

    {
      "entry": "MEMORY.md",
      "allow_dangling": ["note-i-still-have-to-write"],
      "ignore": ["scratch_*.md"]
    }

A dangling link is a legitimate way to mark "this memory is worth writing".
List those in allow_dangling so they stop being reported - and keep the list
short, because every entry is a debt.

MIT licensed. Stdlib only, Python 3.8+.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import pathlib
import re
import sys
from typing import Dict, List, Optional, Set

__version__ = "1.0.0"

WIKILINK = re.compile(r"\[\[([^\[\]]+)\]\]")
MDLINK = re.compile(r"\]\(([^)\s]+\.md)\)")
INLINE_CODE = re.compile(r"`[^`\n]*`")
FENCED_CODE = re.compile(r"```.*?```", re.S)
NAME_FIELD = re.compile(r"^name:[ \t]*(.*)$", re.M)
DESC_FIELD = re.compile(r"^description:[ \t]*(.*)$", re.M)

DEFAULT_CONFIG = {
    "entry": "MEMORY.md",
    "allow_dangling": [],
    "ignore": [],
    # Index notes are routes, not facts: they carry links, not a remembered
    # fact, so `name:`/`description:` are not required of them. They still
    # take part in the reachability graph.
    "index_glob": ["index-*.md", "index_*.md"],
}


# --------------------------------------------------------------------------
# output helpers


def _supports_unicode() -> bool:
    """Force UTF-8 on both streams; Windows consoles default to a legacy codepage."""
    ok = True
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            ok = ok and "utf" in (getattr(stream, "encoding", "") or "").lower()
    return ok


UNICODE = _supports_unicode()
OK_MARK = "✓" if UNICODE else "OK"
BAD_MARK = "✗" if UNICODE else "FAIL"


# --------------------------------------------------------------------------
# discovery


def discover_memory_dirs() -> List[pathlib.Path]:
    """Find every Claude Code project that has a memory folder."""
    root = pathlib.Path.home() / ".claude" / "projects"
    if not root.is_dir():
        return []
    found = [p / "memory" for p in sorted(root.iterdir()) if (p / "memory").is_dir()]
    return found


def resolve_target(path: Optional[str], use_all: bool) -> List[pathlib.Path]:
    if path:
        p = pathlib.Path(path).expanduser()
        if not p.is_dir():
            raise SystemExit(f"{BAD_MARK} not a directory: {p}")
        return [p]
    found = discover_memory_dirs()
    if not found:
        raise SystemExit(
            f"{BAD_MARK} no memory folder found under ~/.claude/projects/*/memory\n"
            "   pass a path explicitly: claude_memory_lint.py PATH"
        )
    if use_all or len(found) == 1:
        return found
    listing = "\n".join(f"   {p}" for p in found)
    raise SystemExit(
        f"{BAD_MARK} several projects have memory folders - pick one, or pass --all:\n{listing}"
    )


# --------------------------------------------------------------------------
# model


class Note:
    __slots__ = ("stem", "path", "text")

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.stem = path.stem
        self.text = path.read_text(encoding="utf-8")

    def frontmatter(self) -> str:
        if not self.text.startswith("---"):
            return ""
        parts = self.text.split("---", 2)
        return parts[1] if len(parts) >= 3 else ""

    def prose(self) -> str:
        """Body with code stripped - links inside code are examples, not links."""
        return INLINE_CODE.sub(" ", FENCED_CODE.sub(" ", self.text))


class Issue:
    __slots__ = ("kind", "note", "line", "message", "fixable")

    def __init__(self, kind: str, note: str, line: int, message: str, fixable: bool = False) -> None:
        self.kind = kind
        self.note = note
        self.line = line
        self.message = message
        self.fixable = fixable

    def location(self) -> str:
        return f"{self.note}.md:{self.line}" if self.line else f"{self.note}.md"

    def as_dict(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "note": self.note,
            "line": self.line,
            "message": self.message,
            "fixable": self.fixable,
        }


def load_config(memory: pathlib.Path) -> Dict[str, object]:
    cfg = dict(DEFAULT_CONFIG)
    f = memory / ".memory-lint.json"
    if f.is_file():
        try:
            cfg.update(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            print(f"{BAD_MARK} bad config {f}: {exc}", file=sys.stderr)
    return cfg


def load_notes(memory: pathlib.Path, ignore: List[str]) -> Dict[str, Note]:
    notes: Dict[str, Note] = {}
    for p in sorted(memory.glob("*.md")):
        if any(fnmatch.fnmatch(p.name, pat) for pat in ignore):
            continue
        try:
            notes[p.stem] = Note(p)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"{BAD_MARK} cannot read {p.name}: {exc}", file=sys.stderr)
    return notes


# --------------------------------------------------------------------------
# checks


def is_index(filename: str, patterns: List[str]) -> bool:
    """Index notes carry routes rather than a fact, so frontmatter is optional."""
    return any(fnmatch.fnmatch(filename, pat) for pat in patterns)


def normalize(s: str) -> str:
    """Fold case and separators: 'My-Note' and 'my_note' compare equal."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def build_rename_map(notes: Dict[str, Note]) -> Dict[str, str]:
    """Map a normalized form to a note, when that form is unambiguous.

    Also indexes each note without its type prefix (feedback_, project_, ref_,
    user_), so `[[tone-of-voice]]` still finds
    `feedback_tone_of_voice.md`.
    """
    exact: Dict[str, List[str]] = {}
    stems: Dict[str, List[str]] = {}
    for stem in notes:
        exact.setdefault(normalize(stem), []).append(stem)
        bare = re.sub(r"^(feedback|project|ref|user|infra|note|index)[_-]", "", stem)
        stems.setdefault(normalize(bare), []).append(stem)
    out: Dict[str, str] = {}
    for table in (exact, stems):
        for key, hits in table.items():
            if len(hits) == 1:
                out.setdefault(key, hits[0])
    return out


def outgoing_links(note: Note, known: Set[str]) -> Set[str]:
    body = note.prose()
    targets = {t.strip() for t in WIKILINK.findall(body)}
    targets |= {os.path.basename(t)[:-3] for t in MDLINK.findall(body)}
    return {t for t in targets if t in known}


def check(memory: pathlib.Path) -> Dict[str, object]:
    cfg = load_config(memory)
    ignore = list(cfg.get("ignore") or [])
    allow_dangling = set(cfg.get("allow_dangling") or [])
    entry = str(cfg.get("entry") or "MEMORY.md")
    entry_stem = entry[:-3] if entry.endswith(".md") else entry

    notes = load_notes(memory, ignore)
    issues: List[Issue] = []
    if not notes:
        return {"memory": str(memory), "notes": 0, "issues": [Issue("empty", "-", 0, "no notes found")]}

    known = set(notes)
    rename_map = build_rename_map(notes)

    # 1/2. broken links
    for stem, note in notes.items():
        for lineno, line in enumerate(note.prose().split("\n"), 1):
            for raw in WIKILINK.findall(line):
                target = raw.strip()
                if not target or target in known or target in allow_dangling:
                    continue
                suggestion = rename_map.get(normalize(target))
                if suggestion == stem:
                    suggestion = None
                hint = f" -> did you mean [[{suggestion}]]?" if suggestion else ""
                issues.append(
                    Issue("broken-link", stem, lineno, f"[[{target}]] does not exist{hint}", bool(suggestion))
                )
            for raw in MDLINK.findall(line):
                if raw.startswith(("http://", "https://", "#")):
                    continue
                if (memory / raw).exists() or (note.path.parent / raw).exists():
                    continue
                issues.append(Issue("broken-path", stem, lineno, f"link to missing file: {raw}"))

    # 3. orphans
    if entry_stem not in notes:
        issues.append(Issue("no-entry", entry_stem, 0, f"entry note {entry} is missing"))
    else:
        seen = {entry_stem}
        queue = [entry_stem]
        while queue:
            for nxt in outgoing_links(notes[queue.pop()], known):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        for stem in sorted(known - seen):
            issues.append(Issue("orphan", stem, 0, f"unreachable from {entry} - nothing links here"))

    # 4/5/6. frontmatter
    index_glob = list(cfg.get("index_glob") or [])
    claimed: Dict[str, List[str]] = {}
    for stem, note in notes.items():
        if stem == entry_stem or is_index(note.path.name, index_glob):
            continue
        head = note.frontmatter()
        if not head.strip():
            issues.append(Issue("frontmatter", stem, 1, "no frontmatter block"))
            continue
        m = NAME_FIELD.search(head)
        if not m:
            issues.append(Issue("name", stem, 1, "no `name:` field", True))
        else:
            value = m.group(1).strip().strip("\"'")
            if value != stem:
                shown = value or "(empty)"
                issues.append(Issue("name", stem, 1, f"`name: {shown}` != filename", True))
            if value:
                claimed.setdefault(value, []).append(stem)
        d = DESC_FIELD.search(head)
        if not d or not d.group(1).strip().strip("\"'"):
            issues.append(Issue("description", stem, 1, "no `description:` - recall cannot rank this note"))

    for value, holders in sorted(claimed.items()):
        if len(holders) > 1:
            issues.append(Issue("duplicate", holders[0], 1, f"`name: {value}` also used by {', '.join(holders[1:])}"))

    return {"memory": str(memory), "notes": len(notes), "issues": issues, "allow_dangling": sorted(allow_dangling)}


# --------------------------------------------------------------------------
# fix


def fix(memory: pathlib.Path) -> Dict[str, int]:
    """Repair the unambiguous subset: resolvable links and name mismatches."""
    cfg = load_config(memory)
    ignore = list(cfg.get("ignore") or [])
    allow_dangling = set(cfg.get("allow_dangling") or [])
    entry = str(cfg.get("entry") or "MEMORY.md")
    entry_stem = entry[:-3] if entry.endswith(".md") else entry

    index_glob = list(cfg.get("index_glob") or [])
    notes = load_notes(memory, ignore)
    known = set(notes)
    rename_map = build_rename_map(notes)
    links_fixed = names_fixed = 0

    for stem, note in notes.items():
        text = note.text

        def repair(m: "re.Match[str]") -> str:
            nonlocal links_fixed
            target = m.group(1).strip()
            if target in known or target in allow_dangling:
                return m.group(0)
            hit = rename_map.get(normalize(target))
            if hit and hit != stem:
                links_fixed += 1
                return f"[[{hit}]]"
            return m.group(0)

        # only rewrite links outside code spans
        pieces = []
        last = 0
        for span in INLINE_CODE.finditer(text):
            pieces.append(WIKILINK.sub(repair, text[last:span.start()]))
            pieces.append(span.group(0))
            last = span.end()
        pieces.append(WIKILINK.sub(repair, text[last:]))
        text = "".join(pieces)

        if (
            stem != entry_stem
            and not is_index(note.path.name, index_glob)
            and text.startswith("---")
            and text.count("---") >= 2
        ):
            head, body = text.split("---", 2)[1], text.split("---", 2)[2]
            m = NAME_FIELD.search(head)
            if m and m.group(1).strip().strip("\"'") != stem:
                head = NAME_FIELD.sub(f"name: {stem}", head, count=1)
                names_fixed += 1
                text = "---" + head + "---" + body

        if text != note.text:
            note.path.write_text(text, encoding="utf-8")

    return {"links": links_fixed, "names": names_fixed}


# --------------------------------------------------------------------------
# reporting


def report(result: Dict[str, object], quiet_ok: bool = False) -> int:
    memory = result["memory"]
    issues: List[Issue] = result.get("issues", [])  # type: ignore[assignment]
    count = result.get("notes", 0)

    if not issues:
        if not quiet_ok:
            print(f"{OK_MARK} {memory}")
            print(f"  {count} notes, all reachable, no broken links")
            allow = result.get("allow_dangling") or []
            if allow:
                print(f"  deliberate dangling links: {', '.join(allow)}")
        return 0

    by_kind: Dict[str, List[Issue]] = {}
    for i in issues:
        by_kind.setdefault(i.kind, []).append(i)

    print(f"{BAD_MARK} {memory}")
    print(f"  {count} notes, {len(issues)} issues\n")
    for kind in sorted(by_kind):
        group = by_kind[kind]
        fixable = sum(1 for i in group if i.fixable)
        tail = f"  ({fixable} fixable with --fix)" if fixable else ""
        print(f"  {kind}: {len(group)}{tail}")
        for i in group[:10]:
            print(f"    {i.location()}  {i.message}")
        if len(group) > 10:
            print(f"    ... and {len(group) - 10} more")
        print()
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="claude-memory-lint",
        description="Integrity checks for Claude Code's file-based memory.",
    )
    ap.add_argument("path", nargs="?", help="memory folder (default: auto-discover)")
    ap.add_argument("--all", action="store_true", help="lint every discovered project")
    ap.add_argument("--fix", action="store_true", help="repair unambiguous issues in place")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--list", action="store_true", help="list discovered memory folders and exit")
    ap.add_argument("--version", action="version", version=f"claude-memory-lint {__version__}")
    args = ap.parse_args(argv)

    if args.list:
        found = discover_memory_dirs()
        if not found:
            print("no memory folders under ~/.claude/projects/*/memory")
            return 1
        for p in found:
            print(f"{p}  ({len(list(p.glob('*.md')))} notes)")
        return 0

    targets = resolve_target(args.path, args.all)
    worst = 0
    payload = []

    for memory in targets:
        if args.fix:
            done = fix(memory)
            if not args.json:
                print(f"fixed in {memory}: {done['links']} links, {done['names']} names\n")
        result = check(memory)
        if args.json:
            payload.append(
                {
                    "memory": result["memory"],
                    "notes": result["notes"],
                    "issues": [i.as_dict() for i in result["issues"]],  # type: ignore[union-attr]
                }
            )
            worst = max(worst, 1 if result["issues"] else 0)
        else:
            worst = max(worst, report(result))

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return worst


if __name__ == "__main__":
    sys.exit(main())
