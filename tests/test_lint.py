"""Tests for claude-memory-lint.

Each test builds a throwaway memory folder containing one specific defect,
so a failure names the check that broke. Stdlib only: python -m unittest.
"""

import json
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import claude_memory_lint as lint  # noqa: E402


def note(name, body="body", description="a fact", extra_frontmatter=""):
    head = f"---\nname: {name}\ndescription: {description}\n{extra_frontmatter}---\n\n"
    return head + body + "\n"


class MemoryFixture(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="memlint-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def write(self, filename, content):
        (self.dir / filename).write_text(content, encoding="utf-8")

    def index(self, *targets):
        links = "\n".join(f"- [{t}]({t}.md)" for t in targets)
        self.write("MEMORY.md", "# Memory Index\n\n" + links + "\n")

    def kinds(self):
        result = lint.check(self.dir)
        return sorted(i.kind for i in result["issues"])

    def messages(self):
        return [i.message for i in lint.check(self.dir)["issues"]]


class TestClean(MemoryFixture):
    def test_clean_corpus_has_no_issues(self):
        self.index("alpha", "beta")
        self.write("alpha.md", note("alpha", "links to [[beta]]"))
        self.write("beta.md", note("beta"))
        self.assertEqual(self.kinds(), [])

    def test_note_reachable_only_through_a_hub_is_not_an_orphan(self):
        # the whole point: hubs are routes, index coverage alone proves nothing
        self.index("hub")
        self.write("hub.md", note("hub", "see [[deep]]"))
        self.write("deep.md", note("deep"))
        self.assertEqual(self.kinds(), [])

    def test_index_notes_need_no_frontmatter(self):
        self.index("index-topic")
        self.write("index-topic.md", "# Topic\n\n- [alpha](alpha.md)\n")
        self.write("alpha.md", note("alpha"))
        self.assertEqual(self.kinds(), [])


class TestDetection(MemoryFixture):
    def test_broken_wikilink(self):
        self.index("alpha")
        self.write("alpha.md", note("alpha", "points at [[ghost]]"))
        self.assertIn("broken-link", self.kinds())

    def test_link_inside_code_span_is_not_a_link(self):
        self.index("alpha")
        self.write("alpha.md", note("alpha", "syntax is `[[name]]`, written by hand"))
        self.assertEqual(self.kinds(), [])

    def test_orphan_is_reported(self):
        self.index("alpha")
        self.write("alpha.md", note("alpha"))
        self.write("lonely.md", note("lonely"))
        self.assertIn("orphan", self.kinds())

    def test_name_mismatch_is_reported(self):
        self.index("alpha")
        self.write("alpha.md", note("some-other-slug"))
        self.assertIn("name", self.kinds())

    def test_missing_description_is_reported(self):
        self.index("alpha")
        self.write("alpha.md", "---\nname: alpha\n---\n\nbody\n")
        self.assertIn("description", self.kinds())

    def test_duplicate_name_is_reported(self):
        self.index("alpha", "beta")
        self.write("alpha.md", note("alpha"))
        self.write("beta.md", note("alpha"))  # claims a name it does not own
        self.assertIn("duplicate", self.kinds())

    def test_broken_relative_path(self):
        self.index("alpha")
        self.write("alpha.md", note("alpha", "see [notes](missing-file.md)"))
        self.assertIn("broken-path", self.kinds())

    def test_allow_dangling_suppresses_a_deliberate_marker(self):
        self.index("alpha")
        self.write("alpha.md", note("alpha", "someday: [[not-written-yet]]"))
        self.assertIn("broken-link", self.kinds())
        self.write(".memory-lint.json", json.dumps({"allow_dangling": ["not-written-yet"]}))
        self.assertEqual(self.kinds(), [])

    def test_suggestion_offered_for_case_and_separator_drift(self):
        self.index("alpha", "my_note")
        self.write("alpha.md", note("alpha", "points at [[my-note]]"))
        self.write("my_note.md", note("my_note"))
        self.assertTrue(any("did you mean [[my_note]]" in m for m in self.messages()))

    def test_line_number_stays_accurate_after_a_fenced_block(self):
        self.index("alpha")
        body = "```\ncode line\n```\n\nbroken [[ghost]] here"
        self.write("alpha.md", note("alpha", body))
        broken = [i for i in lint.check(self.dir)["issues"] if i.kind == "broken-link"]
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0].line, 10)  # 5 frontmatter/blank lines + 5 body lines

    def test_cyrillic_link_is_not_matched_to_an_unrelated_cyrillic_note(self):
        # all-Cyrillic names used to normalize to the same empty string,
        # making any Cyrillic dangling link an "unambiguous" match
        self.index("alpha", "тестовая_заметка")
        self.write("alpha.md", note("alpha", "points at [[другое-имя]]"))
        self.write("тестовая_заметка.md", note("тестовая_заметка"))
        self.assertFalse(any("did you mean" in m for m in self.messages()))

    def test_cyrillic_separator_drift_gets_a_suggestion(self):
        self.index("alpha", "тестовая_заметка")
        self.write("alpha.md", note("alpha", "points at [[тестовая-заметка]]"))
        self.write("тестовая_заметка.md", note("тестовая_заметка"))
        self.assertTrue(
            any("did you mean [[тестовая_заметка]]" in m for m in self.messages())
        )

    def test_md_link_with_anchor_is_still_checked(self):
        self.index("alpha")
        self.write("alpha.md", note("alpha", "see [part](missing-file.md#section)"))
        self.assertIn("broken-path", self.kinds())


class TestFix(MemoryFixture):
    def test_fix_repairs_separator_drift(self):
        # the real-world bug: files are snake_case, links written kebab-case
        self.index("alpha", "my_note")
        self.write("alpha.md", note("alpha", "points at [[my-note]]"))
        self.write("my_note.md", note("my_note"))
        stats = lint.fix(self.dir)
        self.assertEqual(stats["links"], 1)
        self.assertIn("[[my_note]]", (self.dir / "alpha.md").read_text(encoding="utf-8"))
        self.assertEqual(self.kinds(), [])

    def test_fix_repairs_missing_type_prefix(self):
        self.index("alpha", "feedback_tone_of_voice")
        self.write("alpha.md", note("alpha", "see [[tone-of-voice]]"))
        self.write("feedback_tone_of_voice.md", note("feedback_tone_of_voice"))
        lint.fix(self.dir)
        self.assertIn("[[feedback_tone_of_voice]]", (self.dir / "alpha.md").read_text(encoding="utf-8"))

    def test_fix_aligns_name_with_filename(self):
        self.index("alpha")
        self.write("alpha.md", note("Some Human Readable Title"))
        stats = lint.fix(self.dir)
        self.assertEqual(stats["names"], 1)
        self.assertIn("name: alpha", (self.dir / "alpha.md").read_text(encoding="utf-8"))

    def test_fix_never_touches_links_inside_code(self):
        self.index("alpha", "my_note")
        self.write("alpha.md", note("alpha", "the syntax `[[my-note]]` is an example"))
        self.write("my_note.md", note("my_note"))
        lint.fix(self.dir)
        self.assertIn("`[[my-note]]`", (self.dir / "alpha.md").read_text(encoding="utf-8"))

    def test_fix_never_touches_links_inside_fenced_blocks(self):
        self.index("alpha", "my_note")
        body = "```markdown\nexample: [[my-note]]\n```\n\nreal link [[my-note]]"
        self.write("alpha.md", note("alpha", body))
        self.write("my_note.md", note("my_note"))
        stats = lint.fix(self.dir)
        text = (self.dir / "alpha.md").read_text(encoding="utf-8")
        self.assertIn("example: [[my-note]]", text)  # fenced example untouched
        self.assertIn("real link [[my_note]]", text)  # prose link repaired
        self.assertEqual(stats["links"], 1)

    def test_fix_does_not_rewrite_a_cyrillic_link_to_an_unrelated_note(self):
        self.index("alpha", "тестовая_заметка")
        self.write("alpha.md", note("alpha", "points at [[другое-имя]]"))
        self.write("тестовая_заметка.md", note("тестовая_заметка"))
        stats = lint.fix(self.dir)
        self.assertEqual(stats["links"], 0)
        self.assertIn(
            "[[другое-имя]]", (self.dir / "alpha.md").read_text(encoding="utf-8")
        )

    def test_fix_leaves_ambiguous_links_alone(self):
        self.index("alpha", "my_note", "my-note")
        self.write("alpha.md", note("alpha", "points at [[MyNote]]"))
        self.write("my_note.md", note("my_note"))
        self.write("my-note.md", note("my-note"))
        stats = lint.fix(self.dir)
        self.assertEqual(stats["links"], 0)
        self.assertIn("broken-link", self.kinds())

    def test_fix_does_not_invent_a_target(self):
        self.index("alpha")
        self.write("alpha.md", note("alpha", "points at [[nothing-like-this-exists]]"))
        stats = lint.fix(self.dir)
        self.assertEqual(stats["links"], 0)


class TestCli(MemoryFixture):
    def test_exit_code_zero_when_clean(self):
        self.index("alpha")
        self.write("alpha.md", note("alpha"))
        self.assertEqual(lint.main([str(self.dir)]), 0)

    def test_exit_code_one_when_dirty(self):
        self.index("alpha")
        self.write("alpha.md", note("alpha", "[[ghost]]"))
        self.assertEqual(lint.main([str(self.dir)]), 1)

    def test_missing_entry_note_is_reported(self):
        self.write("alpha.md", note("alpha"))
        self.assertIn("no-entry", self.kinds())


if __name__ == "__main__":
    unittest.main(verbosity=2)
