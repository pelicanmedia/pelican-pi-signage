"""Run with:  python3 -m unittest discover -s tests"""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from schedule import ScheduleError, is_active, normalize_schedule  # noqa: E402

# 2026-11-27 is a Friday
FRI = datetime.date(2026, 11, 27)


def at(day, hh, mm=0):
    return datetime.datetime.combine(day, datetime.time(hh, mm))


class IsActiveTests(unittest.TestCase):
    def test_no_schedule_always_active(self):
        self.assertTrue(is_active({}, at(FRI, 3)))

    def test_date_range_inclusive(self):
        a = {"start_date": "2026-11-27", "end_date": "2026-11-28"}
        self.assertFalse(is_active(a, at(FRI - datetime.timedelta(days=1), 23, 59)))
        self.assertTrue(is_active(a, at(FRI, 0)))
        self.assertTrue(is_active(a, at(FRI + datetime.timedelta(days=1), 23, 59)))
        self.assertFalse(is_active(a, at(FRI + datetime.timedelta(days=2), 0)))

    def test_days_of_week(self):
        weekdays = {"play_days": "12345"}
        self.assertTrue(is_active(weekdays, at(FRI, 12)))
        self.assertFalse(is_active(weekdays, at(FRI + datetime.timedelta(days=1), 12)))

    def test_daytime_window(self):
        a = {"play_time_from": "09:00", "play_time_to": "17:00"}
        self.assertFalse(is_active(a, at(FRI, 8, 59)))
        self.assertTrue(is_active(a, at(FRI, 9, 0)))
        self.assertTrue(is_active(a, at(FRI, 16, 59)))
        self.assertFalse(is_active(a, at(FRI, 17, 0)))  # end is exclusive

    def test_overnight_window_uses_previous_day(self):
        # Fridays only, 22:00 until 02:00
        a = {"play_days": "5", "play_time_from": "22:00", "play_time_to": "02:00"}
        sat = FRI + datetime.timedelta(days=1)
        self.assertFalse(is_active(a, at(FRI, 21, 59)))
        self.assertTrue(is_active(a, at(FRI, 23, 30)))
        self.assertTrue(is_active(a, at(sat, 1, 30)))    # still Friday's window
        self.assertFalse(is_active(a, at(sat, 2, 0)))
        self.assertFalse(is_active(a, at(sat, 23, 0)))   # Saturday evening not allowed
        self.assertFalse(is_active(a, at(FRI, 1, 0)))    # belongs to Thursday's window

    def test_overnight_window_respects_end_date(self):
        a = {"end_date": "2026-11-27", "play_time_from": "22:00", "play_time_to": "02:00"}
        self.assertTrue(is_active(a, at(FRI + datetime.timedelta(days=1), 1)))
        self.assertFalse(is_active(a, at(FRI + datetime.timedelta(days=1), 23)))


class NormalizeTests(unittest.TestCase):
    def test_blank_clears_and_missing_keeps(self):
        out = normalize_schedule({"start_date": ""}, {"start_date": "2026-01-01", "play_days": "12"})
        self.assertIsNone(out["start_date"])
        self.assertEqual(out["play_days"], "12")

    def test_all_days_becomes_null(self):
        self.assertIsNone(normalize_schedule({"play_days": "7654321"})["play_days"])
        self.assertEqual(normalize_schedule({"play_days": "5,1,3"})["play_days"], "135")

    def test_rejects_bad_input(self):
        bad = [
            {"start_date": "27/11/2026"},
            {"start_date": "2026-02-30"},
            {"start_date": "2026-12-01", "end_date": "2026-11-01"},
            {"play_days": "08"},
            {"play_time_from": "25:00", "play_time_to": "02:00"},
            {"play_time_from": "09:00"},
            {"play_time_from": "09:00", "play_time_to": "09:00"},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ScheduleError):
                normalize_schedule(data)


if __name__ == "__main__":
    unittest.main()
