"""
Per-asset scheduling for Pelican Pi Signage.

Schedule fields on an asset (all optional; NULL/empty means "no limit"):

    start_date      "YYYY-MM-DD"  first day the asset may play (inclusive)
    end_date        "YYYY-MM-DD"  last day the asset may play (inclusive)
    play_days       e.g. "12345"  ISO weekdays it may play (Mon=1 ... Sun=7)
    play_time_from  "HH:MM"       daily window start (inclusive)
    play_time_to    "HH:MM"       daily window end (exclusive)

If play_time_from is later than play_time_to the window crosses midnight,
e.g. 22:00-02:00. The part after midnight belongs to the previous day's
window, so "Fri 22:00-02:00" still plays at 01:00 on Saturday morning.

All times are the Pi's local time.
"""

import datetime
import re

SCHEDULE_FIELDS = ("start_date", "end_date", "play_days", "play_time_from", "play_time_to")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ScheduleError(ValueError):
    """Raised when schedule input is invalid; message is shown to the user."""


def _parse_date(value):
    return datetime.date.fromisoformat(value) if value else None


def _parse_time(value):
    if not value:
        return None
    h, m = value.split(":")
    return datetime.time(int(h), int(m))


def is_active(asset, now=None):
    """Return True if the asset's schedule allows it to play at `now` (local time)."""
    now = now or datetime.datetime.now()

    t_from = _parse_time(asset.get("play_time_from"))
    t_to   = _parse_time(asset.get("play_time_to"))
    t_now  = now.time().replace(second=0, microsecond=0)

    # Which calendar day's window are we in?
    window_day = now.date()
    if t_from and t_to:
        if t_from < t_to:
            if not (t_from <= t_now < t_to):
                return False
        else:  # crosses midnight
            if t_now >= t_from:
                pass                      # evening part: today's window
            elif t_now < t_to:
                window_day = now.date() - datetime.timedelta(days=1)  # early-morning part
            else:
                return False

    start = _parse_date(asset.get("start_date"))
    end   = _parse_date(asset.get("end_date"))
    if start and window_day < start:
        return False
    if end and window_day > end:
        return False

    days = asset.get("play_days")
    if days and str(window_day.isoweekday()) not in days:
        return False

    return True


def has_schedule(asset):
    return any(asset.get(f) for f in SCHEDULE_FIELDS)


def normalize_schedule(data, current=None):
    """
    Validate schedule fields from user/API input.

    `data` may contain any subset of SCHEDULE_FIELDS; missing keys keep the
    value from `current`. Empty strings / None clear a field.
    Returns a dict with all five fields. Raises ScheduleError on bad input.
    """
    current = current or {}
    out = {}
    for f in SCHEDULE_FIELDS:
        raw = data[f] if f in data else current.get(f)
        if raw is None:
            out[f] = None
            continue
        raw = str(raw).strip()
        out[f] = raw or None

    for f in ("start_date", "end_date"):
        v = out[f]
        if v is not None:
            if not _DATE_RE.match(v):
                raise ScheduleError(f"{f.replace('_', ' ').capitalize()} must be YYYY-MM-DD.")
            try:
                datetime.date.fromisoformat(v)
            except ValueError:
                raise ScheduleError(f"{f.replace('_', ' ').capitalize()} is not a real date.")
    if out["start_date"] and out["end_date"] and out["start_date"] > out["end_date"]:
        raise ScheduleError("End date must be on or after the start date.")

    days = out["play_days"]
    if days is not None:
        digits = sorted(set(days.replace(",", "").replace(" ", "")))
        if not digits or any(d not in "1234567" for d in digits):
            raise ScheduleError("Days must be numbers 1-7 (Mon=1 ... Sun=7).")
        # Every day selected is the same as no restriction
        out["play_days"] = None if len(digits) == 7 else "".join(digits)

    for f in ("play_time_from", "play_time_to"):
        v = out[f]
        if v is not None and not _TIME_RE.match(v):
            raise ScheduleError("Times must be HH:MM (24-hour).")
    if bool(out["play_time_from"]) != bool(out["play_time_to"]):
        raise ScheduleError("Set both a start and an end time, or neither.")
    if out["play_time_from"] and out["play_time_from"] == out["play_time_to"]:
        raise ScheduleError("Start and end time can't be the same.")

    return out
