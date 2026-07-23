#!/usr/bin/env python3
"""Unit tests for the settings persistence module (pure logic, no AppKit)."""

import json
import os
import tempfile
import unittest

from settings import (
    DEFAULT_MESSAGES,
    DEFAULT_SETTINGS,
    LEVEL_KEYS,
    level_message,
    load_settings,
    render_message,
    save_settings,
    update_level,
    update_setting,
)


class LoadSettingsTest(unittest.TestCase):
    def test_returns_defaults_when_file_is_missing(self):
        # Arrange
        missing = os.path.join(tempfile.mkdtemp(), "nope.json")

        # Act
        loaded = load_settings(missing)

        # Assert
        self.assertEqual(loaded, DEFAULT_SETTINGS)

    def test_returns_defaults_when_file_is_corrupt(self):
        # Arrange
        path = os.path.join(tempfile.mkdtemp(), "settings.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")

        # Act / Assert
        self.assertEqual(load_settings(path), DEFAULT_SETTINGS)

    def test_merges_partial_file_over_defaults(self):
        # Arrange
        path = os.path.join(tempfile.mkdtemp(), "settings.json")
        partial = {"sound_enabled": False, "levels": {"alert_70": {"enabled": False}}}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(partial, fh)

        # Act
        loaded = load_settings(path)

        # Assert
        self.assertFalse(loaded["sound_enabled"])
        self.assertTrue(loaded["notifications_enabled"])
        self.assertFalse(loaded["levels"]["alert_70"]["enabled"])
        self.assertTrue(loaded["levels"]["alert_100"]["enabled"])
        self.assertEqual(loaded["levels"]["alert_70"]["message"], "")

    def test_coerces_invalid_types_back_to_defaults(self):
        # Arrange
        path = os.path.join(tempfile.mkdtemp(), "settings.json")
        bad = {
            "notifications_enabled": "yes",
            "levels": {"alert_90": {"enabled": 1, "message": 42}},
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bad, fh)

        # Act
        loaded = load_settings(path)

        # Assert
        self.assertTrue(loaded["notifications_enabled"])
        self.assertTrue(loaded["levels"]["alert_90"]["enabled"])
        self.assertEqual(loaded["levels"]["alert_90"]["message"], "")


class SaveSettingsTest(unittest.TestCase):
    def test_round_trips_through_disk(self):
        # Arrange
        path = os.path.join(tempfile.mkdtemp(), "sub", "settings.json")
        changed = update_level(
            update_setting(DEFAULT_SETTINGS, "sound_enabled", False),
            "alert_95",
            enabled=False,
            message="quase lá: {pct}%",
        )

        # Act
        saved = save_settings(changed, path)
        loaded = load_settings(path)

        # Assert
        self.assertTrue(saved)
        self.assertEqual(loaded, changed)

    def test_returns_false_when_directory_is_not_writable(self):
        # Act / Assert (path nested under a plain file, so makedirs must fail)
        blocker = tempfile.NamedTemporaryFile(delete=False)
        self.assertFalse(save_settings(DEFAULT_SETTINGS, os.path.join(blocker.name, "x.json")))


class UpdateHelpersTest(unittest.TestCase):
    def test_update_setting_does_not_mutate_original(self):
        # Act
        updated = update_setting(DEFAULT_SETTINGS, "notifications_enabled", False)

        # Assert
        self.assertTrue(DEFAULT_SETTINGS["notifications_enabled"])
        self.assertFalse(updated["notifications_enabled"])

    def test_update_setting_ignores_unknown_key(self):
        self.assertEqual(update_setting(DEFAULT_SETTINGS, "hack", True), DEFAULT_SETTINGS)

    def test_update_level_changes_only_the_target_level(self):
        # Act
        updated = update_level(DEFAULT_SETTINGS, "alert_70", enabled=False)

        # Assert
        self.assertFalse(updated["levels"]["alert_70"]["enabled"])
        self.assertTrue(DEFAULT_SETTINGS["levels"]["alert_70"]["enabled"])
        for key in LEVEL_KEYS:
            if key != "alert_70":
                self.assertEqual(updated["levels"][key], DEFAULT_SETTINGS["levels"][key])

    def test_update_level_ignores_unknown_key(self):
        self.assertEqual(update_level(DEFAULT_SETTINGS, "alert_50", enabled=False), DEFAULT_SETTINGS)


class MessageTest(unittest.TestCase):
    def test_level_message_falls_back_to_default_when_empty(self):
        self.assertEqual(level_message(DEFAULT_SETTINGS, "alert_90"), DEFAULT_MESSAGES["alert_90"])

    def test_level_message_prefers_custom_text(self):
        # Arrange
        custom = update_level(DEFAULT_SETTINGS, "renewal", message="voltamos!")

        # Act / Assert
        self.assertEqual(level_message(custom, "renewal"), "voltamos!")

    def test_level_message_treats_whitespace_as_empty(self):
        custom = update_level(DEFAULT_SETTINGS, "alert_80", message="   ")
        self.assertEqual(level_message(custom, "alert_80"), DEFAULT_MESSAGES["alert_80"])

    def test_render_message_fills_all_placeholders(self):
        # Act
        rendered = render_message("{nome} em {pct}%, renova em {reset}", "Sessão (5h)", 72, "1h 10m")

        # Assert
        self.assertEqual(rendered, "Sessão (5h) em 72%, renova em 1h 10m")


if __name__ == "__main__":
    unittest.main()
