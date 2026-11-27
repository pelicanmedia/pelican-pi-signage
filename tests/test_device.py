"""Run with:  python3 -m unittest discover -s tests"""

import datetime
import os
import sys
import subprocess
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import device  # noqa: E402
from device import normalize_display_settings, screen_should_be_on  # noqa: E402
from schedule import ScheduleError  # noqa: E402

FRI = datetime.date(2026, 11, 27)  # a Friday


def at(day, hh, mm=0):
    return datetime.datetime.combine(day, datetime.time(hh, mm))


class ScreenScheduleTests(unittest.TestCase):
    def test_no_schedule_always_on(self):
        self.assertTrue(screen_should_be_on({}, at(FRI, 3)))
        self.assertTrue(screen_should_be_on({"display_on": "", "display_off": "", "display_days": ""}, at(FRI, 3)))

    def test_opening_hours(self):
        s = {"display_on": "08:00", "display_off": "18:00", "display_days": "12345"}
        self.assertFalse(screen_should_be_on(s, at(FRI, 7, 59)))
        self.assertTrue(screen_should_be_on(s, at(FRI, 8)))
        self.assertFalse(screen_should_be_on(s, at(FRI, 18)))
        self.assertFalse(screen_should_be_on(s, at(FRI + datetime.timedelta(days=1), 12)))  # Saturday

    def test_days_only(self):
        s = {"display_days": "67"}
        self.assertFalse(screen_should_be_on(s, at(FRI, 12)))
        self.assertTrue(screen_should_be_on(s, at(FRI + datetime.timedelta(days=1), 12)))

    def test_overnight(self):
        s = {"display_on": "18:00", "display_off": "02:00"}
        self.assertTrue(screen_should_be_on(s, at(FRI, 23)))
        self.assertTrue(screen_should_be_on(s, at(FRI, 1)))
        self.assertFalse(screen_should_be_on(s, at(FRI, 12)))


class NormalizeDisplayTests(unittest.TestCase):
    def test_normalizes(self):
        out = normalize_display_settings(
            {"display_on": "08:00", "display_off": "18:00", "display_days": "5,1", "display_cec": True}, {})
        self.assertEqual(out, {"display_on": "08:00", "display_off": "18:00",
                               "display_days": "15", "display_cec": "1"})

    def test_all_days_and_blank(self):
        out = normalize_display_settings({"display_on": "", "display_off": "", "display_days": "1234567"}, {})
        self.assertEqual(out["display_days"], "")
        self.assertEqual(out["display_cec"], "0")

    def test_keeps_missing_keys(self):
        current = {"display_on": "07:00", "display_off": "19:00", "display_days": "12345", "display_cec": "1"}
        out = normalize_display_settings({"display_cec": "0"}, current)
        self.assertEqual(out["display_on"], "07:00")
        self.assertEqual(out["display_cec"], "0")

    def test_rejects_bad(self):
        for bad in ({"display_on": "08:00"}, {"display_on": "25:00", "display_off": "02:00"},
                    {"display_on": "09:00", "display_off": "09:00"}, {"display_days": "8"}):
            with self.subTest(bad=bad), self.assertRaises(ScheduleError):
                normalize_display_settings(bad, {})


class UpdateTests(unittest.TestCase):
    def test_update_is_remote_action(self):
        self.assertIn("update", device.REMOTE_ACTIONS)
        # Must match the installer's sudoers rule exactly
        self.assertEqual(device.ACTIONS["update"],
                         ["/usr/bin/systemctl", "start", "--no-block", "pi-signage-update.service"])

    def _state(self, stdout):
        done = subprocess.CompletedProcess([], 3, stdout=stdout + "\n", stderr="")
        with mock.patch("device.subprocess.run", return_value=done):
            return device.update_state()

    def test_update_state(self):
        self.assertEqual(self._state("activating"), "activating")
        self.assertEqual(self._state("failed"), "failed")
        self.assertIsNone(self._state("inactive"))

    def test_update_state_without_systemctl(self):
        with mock.patch("device.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(device.update_state())

    def test_failed_update_warns(self):
        with mock.patch("device.update_state", return_value="failed"):
            info = device.system_info()
        self.assertFalse(info["updating"])
        self.assertTrue(any("update failed" in w for w in info["warnings"]))


if __name__ == "__main__":
    unittest.main()
