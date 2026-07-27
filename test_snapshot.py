#!/usr/bin/env python3
"""Tests for the last-good usage snapshot.

The monitor kept its numbers in memory only, so any restart during a 429
backoff (which the endpoint can stretch past 25 minutes) left the menu bar
empty until the limit lifted. These tests pin the on-disk cache that fixes
it — including the guard that refuses to show percentages old enough to be
misleading.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from snapshot import EMPTY_SNAPSHOT, load_snapshot, save_snapshot

NOW = datetime(2026, 7, 27, 17, 0, 0, tzinfo=timezone.utc)
LIMITS = [
    {"kind": "session", "percent": 42, "resets_at": "2026-07-27T22:30:00+00:00"},
    {"kind": "weekly", "percent": 88, "resets_at": "2026-07-30T01:00:00+00:00"},
]


class SnapshotTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = os.path.join(self._dir.name, "last_usage.json")

    def write_raw(self, payload):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)


class TestRoundTrip(SnapshotTestCase):
    def test_saved_snapshot_loads_back_intact(self):
        # Arrange / Act
        saved = save_snapshot(LIMITS, "17:00:00", "Créditos extras: 12%",
                              path=self.path, now=NOW)
        loaded = load_snapshot(path=self.path, now=NOW + timedelta(minutes=5))

        # Assert
        self.assertTrue(saved)
        self.assertEqual(loaded["limits"], LIMITS)
        self.assertEqual(loaded["stamp"], "17:00:00")
        self.assertEqual(loaded["extra_line"], "Créditos extras: 12%")

    def test_null_extra_line_survives(self):
        save_snapshot(LIMITS, "17:00:00", None, path=self.path, now=NOW)
        self.assertIsNone(load_snapshot(path=self.path, now=NOW)["extra_line"])

    def test_save_creates_missing_directory(self):
        nested = os.path.join(self._dir.name, "deep", "last_usage.json")
        self.assertTrue(save_snapshot(LIMITS, "17:00:00", None, path=nested, now=NOW))
        self.assertEqual(load_snapshot(path=nested, now=NOW)["limits"], LIMITS)


class TestFreshness(SnapshotTestCase):
    def test_recent_snapshot_is_used(self):
        save_snapshot(LIMITS, "17:00:00", None, path=self.path, now=NOW)
        loaded = load_snapshot(path=self.path, now=NOW + timedelta(hours=11, minutes=59))
        self.assertEqual(len(loaded["limits"]), 2)

    def test_snapshot_past_max_age_is_discarded(self):
        # A 5h session window makes half-day-old percentages meaningless.
        save_snapshot(LIMITS, "17:00:00", None, path=self.path, now=NOW)
        loaded = load_snapshot(path=self.path, now=NOW + timedelta(hours=13))
        self.assertEqual(loaded, EMPTY_SNAPSHOT)

    def test_snapshot_from_the_future_is_discarded(self):
        # Clock changed between runs; trusting it would pin stale numbers forever.
        save_snapshot(LIMITS, "17:00:00", None, path=self.path, now=NOW)
        loaded = load_snapshot(path=self.path, now=NOW - timedelta(hours=1))
        self.assertEqual(loaded, EMPTY_SNAPSHOT)


class TestCorruptInput(SnapshotTestCase):
    def test_missing_file_is_empty(self):
        missing = os.path.join(self._dir.name, "nope.json")
        self.assertEqual(load_snapshot(path=missing, now=NOW), EMPTY_SNAPSHOT)

    def test_invalid_json_is_empty(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(load_snapshot(path=self.path, now=NOW), EMPTY_SNAPSHOT)

    def test_non_dict_payload_is_empty(self):
        self.write_raw([1, 2, 3])
        self.assertEqual(load_snapshot(path=self.path, now=NOW), EMPTY_SNAPSHOT)

    def test_missing_saved_at_is_empty(self):
        self.write_raw({"limits": LIMITS, "stamp": "17:00:00"})
        self.assertEqual(load_snapshot(path=self.path, now=NOW), EMPTY_SNAPSHOT)

    def test_unparseable_saved_at_is_empty(self):
        self.write_raw({"saved_at": "ontem", "limits": LIMITS, "stamp": "17:00:00"})
        self.assertEqual(load_snapshot(path=self.path, now=NOW), EMPTY_SNAPSHOT)

    def test_limits_without_percent_are_dropped(self):
        self.write_raw({
            "saved_at": NOW.isoformat(),
            "stamp": "17:00:00",
            "limits": [{"kind": "session"}, {"kind": "weekly", "percent": 88}, "junk"],
        })
        loaded = load_snapshot(path=self.path, now=NOW)
        self.assertEqual(loaded["limits"], [{"kind": "weekly", "percent": 88}])

    def test_snapshot_with_no_usable_limits_is_empty(self):
        self.write_raw({
            "saved_at": NOW.isoformat(),
            "stamp": "17:00:00",
            "limits": [{"kind": "session", "percent": None}],
        })
        self.assertEqual(load_snapshot(path=self.path, now=NOW), EMPTY_SNAPSHOT)

    def test_non_string_stamp_falls_back_to_none(self):
        self.write_raw({"saved_at": NOW.isoformat(), "stamp": 1700, "limits": LIMITS})
        self.assertIsNone(load_snapshot(path=self.path, now=NOW)["stamp"])

    def test_unwritable_path_returns_false_without_raising(self):
        blocked = os.path.join(self.path, "cannot", "write.json")  # parent is a file
        save_snapshot(LIMITS, "17:00:00", None, path=self.path, now=NOW)
        self.assertFalse(save_snapshot(LIMITS, "17:00:00", None, path=blocked, now=NOW))


if __name__ == "__main__":
    unittest.main()
