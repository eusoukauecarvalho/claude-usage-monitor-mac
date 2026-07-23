#!/usr/bin/env python3
"""Unit tests for the Claude Code settings.json accessor (pure logic)."""

import json
import os
import tempfile
import unittest

from claude_config import (
    read_claude_settings,
    set_claude_option,
    with_option,
    write_claude_settings,
)


def _tmp_path():
    return os.path.join(tempfile.mkdtemp(), "settings.json")


class ReadTest(unittest.TestCase):
    def test_missing_file_reads_as_empty_dict(self):
        self.assertEqual(read_claude_settings(_tmp_path()), {})

    def test_corrupt_file_reads_as_none(self):
        # Arrange
        path = _tmp_path()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{broken")

        # Act / Assert
        self.assertIsNone(read_claude_settings(path))

    def test_non_object_json_reads_as_none(self):
        path = _tmp_path()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(["not", "a", "dict"], fh)
        self.assertIsNone(read_claude_settings(path))


class WithOptionTest(unittest.TestCase):
    def test_sets_key_without_mutating_original(self):
        # Arrange
        original = {"model": "opus", "theme": "dark"}

        # Act
        updated = with_option(original, "model", "sonnet")

        # Assert
        self.assertEqual(updated, {"model": "sonnet", "theme": "dark"})
        self.assertEqual(original["model"], "opus")

    def test_none_removes_the_key(self):
        original = {"model": "opus", "theme": "dark"}
        self.assertEqual(with_option(original, "model", None), {"theme": "dark"})

    def test_none_on_absent_key_is_a_no_op(self):
        self.assertEqual(with_option({"theme": "dark"}, "model", None), {"theme": "dark"})


class SetOptionTest(unittest.TestCase):
    def test_round_trip_preserves_unrelated_keys(self):
        # Arrange
        path = _tmp_path()
        existing = {"theme": "dark", "enabledPlugins": {"x": True}, "effortLevel": "high"}
        self.assertTrue(write_claude_settings(existing, path))

        # Act
        changed = set_claude_option("model", "haiku", path)
        loaded = read_claude_settings(path)

        # Assert
        self.assertTrue(changed)
        self.assertEqual(loaded["model"], "haiku")
        self.assertEqual(loaded["theme"], "dark")
        self.assertEqual(loaded["enabledPlugins"], {"x": True})
        self.assertEqual(loaded["effortLevel"], "high")

    def test_refuses_to_write_over_a_corrupt_file(self):
        # Arrange
        path = _tmp_path()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{broken")

        # Act / Assert (file must remain untouched)
        self.assertFalse(set_claude_option("model", "opus", path))
        with open(path, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{broken")

    def test_boolean_option_sets_and_removes(self):
        # Arrange
        path = _tmp_path()

        # Act / Assert (on -> true, off -> key removed)
        self.assertTrue(set_claude_option("ultracode", True, path))
        self.assertEqual(read_claude_settings(path), {"ultracode": True})
        self.assertTrue(set_claude_option("ultracode", None, path))
        self.assertEqual(read_claude_settings(path), {})

    def test_creates_file_when_missing(self):
        path = _tmp_path()
        self.assertTrue(set_claude_option("effortLevel", "medium", path))
        self.assertEqual(read_claude_settings(path), {"effortLevel": "medium"})


if __name__ == "__main__":
    unittest.main()
