"""Applying an approved change to a destination, and taking it back."""

import tempfile
import unittest
from pathlib import Path

from gamedev import apply as apply_module

SCOPE = ["Content/**/*.verse"]
DIFF = """diff --git a/Content/one.verse b/Content/one.verse
index 1111111..2222222 100644
--- a/Content/one.verse
+++ b/Content/one.verse
@@ -1 +1 @@
-old
+new
"""


class ScopeTests(unittest.TestCase):
    def test_a_pattern_matches_the_files_it_names(self):
        self.assertTrue(apply_module.matches_scope("Content/one.verse", SCOPE))
        self.assertTrue(apply_module.matches_scope("Content/deep/two.verse", SCOPE))
        self.assertFalse(apply_module.matches_scope("Content/one.py", SCOPE))
        self.assertFalse(apply_module.matches_scope("tools/one.verse", SCOPE))

    def test_a_pattern_with_a_single_star_stays_in_one_folder(self):
        self.assertTrue(apply_module.matches_scope("Content/one.verse", ["Content/*.verse"]))
        self.assertFalse(apply_module.matches_scope("Content/deep/two.verse", ["Content/*.verse"]))

    def test_an_empty_scope_names_nothing(self):
        self.assertFalse(apply_module.matches_scope("Content/one.verse", []))


class PathTests(unittest.TestCase):
    def test_a_relative_path_is_kept(self):
        self.assertEqual(apply_module.normalize("Content/one.verse"), "Content/one.verse")
        self.assertEqual(apply_module.normalize("Content\\one.verse"), "Content/one.verse")

    def test_an_absolute_path_is_refused(self):
        for path in ("/etc/passwd", "C:/Windows/system32", "\\\\server\\share"):
            with self.assertRaises(apply_module.Refusal):
                apply_module.normalize(path)

    def test_a_path_that_climbs_out_is_refused(self):
        with self.assertRaises(apply_module.Refusal):
            apply_module.normalize("../outside")
        with self.assertRaises(apply_module.Refusal):
            apply_module.normalize("Content/../../outside")

    def test_an_empty_path_is_refused(self):
        for path in ("", ".", "/"):
            with self.assertRaises(apply_module.Refusal):
                apply_module.normalize(path)


class DiffTests(unittest.TestCase):
    def test_the_paths_a_diff_touches_are_read(self):
        self.assertEqual(apply_module.changed_paths(DIFF), [("Content/one.verse", False)])

    def test_a_removed_file_is_seen_as_removed(self):
        diff = "\n".join(
            [
                "diff --git a/Content/one.verse b/Content/one.verse",
                "deleted file mode 100644",
                "--- a/Content/one.verse",
                "+++ /dev/null",
            ]
        )
        self.assertEqual(apply_module.changed_paths(diff), [("Content/one.verse", True)])

    def test_a_quoted_path_is_refused_rather_than_guessed(self):
        diff = "\n".join(['diff --git "a/x" "b/x"', '--- "a/x"', '+++ "b/x"'])
        with self.assertRaises(apply_module.Refusal):
            apply_module.changed_paths(diff)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.destination = self.root / "island"
        self.worktree = self.root / "work"
        (self.destination / "Content").mkdir(parents=True)
        (self.worktree / "Content").mkdir(parents=True)
        (self.destination / "Content" / "one.verse").write_bytes(b"old\n")
        (self.worktree / "Content" / "one.verse").write_bytes(b"new\n")

    def base(self, relative):
        return b"old\n" if relative == "Content/one.verse" else None

    def current(self, path):
        path = Path(path)
        return path.read_bytes() if path.is_file() else None

    def plan(self, paths=("Content/one.verse",), scope=SCOPE, base=None):
        return apply_module.plan(
            self.destination,
            self.worktree,
            list(paths),
            scope,
            base or self.base,
            self.current,
        )

    def test_the_plan_names_the_file_and_what_it_replaces(self):
        changes = self.plan()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].relative, "Content/one.verse")
        self.assertFalse(changes[0].created)
        self.assertEqual(changes[0].previous, b"old\n")
        self.assertEqual(changes[0].content, b"new\n")

    def test_a_file_outside_the_scope_is_refused(self):
        with self.assertRaises(apply_module.Refusal):
            self.plan(paths=("Content/one.verse", "tools/other.py"))

    def test_a_path_that_climbs_out_is_refused(self):
        with self.assertRaises(apply_module.Refusal):
            self.plan(paths=("../outside.verse",))

    def test_a_file_that_changed_in_the_destination_is_a_conflict(self):
        (self.destination / "Content" / "one.verse").write_bytes(b"changed by hand\n")
        with self.assertRaises(apply_module.Refusal) as raised:
            self.plan()
        self.assertIn("changed in the destination", str(raised.exception))

    def test_a_link_in_the_destination_is_refused(self):
        target = self.destination / "Content" / "linked.verse"
        try:
            target.symlink_to(self.destination / "Content" / "one.verse")
        except OSError:
            self.skipTest("this account may not create links")
        (self.worktree / "Content" / "linked.verse").write_bytes(b"new\n")
        with self.assertRaises(apply_module.Refusal) as raised:
            self.plan(paths=("Content/linked.verse",), base=lambda relative: None)
        self.assertIn("is a link", str(raised.exception))

    def test_a_link_in_the_approved_changes_is_refused(self):
        (self.worktree / "Content" / "linked.verse").write_bytes(b"new\n")
        try:
            (self.worktree / "Content" / "linked.verse").symlink_to(self.worktree / "Content" / "one.verse")
        except OSError:
            self.skipTest("this account may not create links")
        with self.assertRaises(apply_module.Refusal) as raised:
            self.plan(paths=("Content/linked.verse",), base=lambda relative: None)
        self.assertIn("is a link", str(raised.exception))

    def test_a_new_file_is_created(self):
        (self.worktree / "Content" / "two.verse").write_bytes(b"brand new\n")
        changes = self.plan(paths=("Content/two.verse",), base=lambda relative: None)
        self.assertTrue(changes[0].created)
        self.assertIsNone(changes[0].previous)

    def test_writing_and_rolling_back_leave_the_destination_as_it_was(self):
        changes = self.plan()
        directory = self.root / "snapshot"
        manifest = apply_module.snapshot(self.destination, changes, directory)
        apply_module.save_manifest(self.root / "apply", manifest)
        apply_module.write(self.destination, changes)
        self.assertEqual((self.destination / "Content" / "one.verse").read_bytes(), b"new\n")
        restored = apply_module.rollback(self.destination, manifest, directory)
        self.assertEqual(restored, ["Content/one.verse"])
        self.assertEqual((self.destination / "Content" / "one.verse").read_bytes(), b"old\n")

    def test_a_rollback_removes_a_file_the_apply_created(self):
        (self.worktree / "Content" / "two.verse").write_bytes(b"brand new\n")
        changes = self.plan(paths=("Content/two.verse",), base=lambda relative: None)
        directory = self.root / "snapshot"
        manifest = apply_module.snapshot(self.destination, changes, directory)
        apply_module.write(self.destination, changes)
        self.assertTrue((self.destination / "Content" / "two.verse").is_file())
        apply_module.rollback(self.destination, manifest, directory)
        self.assertFalse((self.destination / "Content" / "two.verse").exists())

    def test_a_rollback_without_a_snapshot_puts_nothing_back(self):
        changes = self.plan()
        manifest = apply_module.snapshot(self.destination, changes, self.root / "snapshot")
        apply_module.write(self.destination, changes)
        (self.root / "snapshot" / "Content" / "one.verse").unlink()
        with self.assertRaises(apply_module.Refusal):
            apply_module.rollback(self.destination, manifest, self.root / "snapshot")


if __name__ == "__main__":
    unittest.main()
