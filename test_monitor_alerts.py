#!/usr/bin/env python3
"""Tests for the reset-window identity check used by countdown alerts.

The usage endpoint recomputes `resets_at` on every request, so the same
window drifts by ~1s between polls (e.g. ...T01:00:00.944574 vs
...T00:59:59.852190). String equality treats that as a new window and
silently kills every countdown alert — these tests pin the tolerant
comparison that fixes it.
"""

import unittest

from monitor import same_reset_window


class TestSameResetWindow(unittest.TestCase):
    def test_identical_strings_are_same_window(self):
        stamp = "2026-07-30T01:00:00.944574+00:00"
        self.assertTrue(same_reset_window(stamp, stamp))

    def test_subsecond_jitter_is_same_window(self):
        a = "2026-07-30T01:00:00.944574+00:00"
        b = "2026-07-30T01:00:00.123456+00:00"
        self.assertTrue(same_reset_window(a, b))

    def test_one_second_jitter_is_same_window(self):
        # Real pair observed from the API for the same weekly window.
        a = "2026-07-30T01:00:00.944574+00:00"
        b = "2026-07-30T00:59:59.852190+00:00"
        self.assertTrue(same_reset_window(a, b))

    def test_one_minute_drift_is_same_window(self):
        a = "2026-07-30T01:00:00+00:00"
        b = "2026-07-30T00:59:00+00:00"
        self.assertTrue(same_reset_window(a, b))

    def test_renewed_session_window_is_different(self):
        # A real renewal jumps the stamp hours ahead (5h session window).
        a = "2026-07-27T22:30:00.944556+00:00"
        b = "2026-07-28T03:30:00.102030+00:00"
        self.assertFalse(same_reset_window(a, b))

    def test_renewed_weekly_window_is_different(self):
        a = "2026-07-30T01:00:00.944574+00:00"
        b = "2026-08-06T01:00:00.111111+00:00"
        self.assertFalse(same_reset_window(a, b))

    def test_none_or_invalid_input_is_never_same_window(self):
        stamp = "2026-07-30T01:00:00.944574+00:00"
        self.assertFalse(same_reset_window(None, stamp))
        self.assertFalse(same_reset_window(stamp, None))
        self.assertFalse(same_reset_window(None, None))
        self.assertFalse(same_reset_window("not-a-date", stamp))
        self.assertFalse(same_reset_window(stamp, "not-a-date"))

    def test_mixed_timezone_offsets_compare_by_instant(self):
        # Same instant expressed in UTC and in -03:00 local time.
        a = "2026-07-30T01:00:00+00:00"
        b = "2026-07-29T22:00:00-03:00"
        self.assertTrue(same_reset_window(a, b))


if __name__ == "__main__":
    unittest.main()
