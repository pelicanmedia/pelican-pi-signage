"""
Per-item content options, shared by the client and the controller
(keep both copies identical).

Options are stored as a JSON object in the `options` TEXT column and sent
over sync as that same JSON string (or null).

    refresh_seconds  int   web pages: reload this often while on screen
    source_url       str   videos downloaded from YouTube etc. (set by the server)
"""

import json

MIN_REFRESH = 10
MAX_REFRESH = 24 * 60 * 60


class OptionsError(ValueError):
    pass


def parse_options(text):
    """JSON text (or None) -> dict. Bad data gives an empty dict."""
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def dump_options(options):
    """dict -> JSON text, or None when empty."""
    clean = {k: v for k, v in (options or {}).items() if v not in (None, "")}
    return json.dumps(clean, sort_keys=True) if clean else None


def normalize_options(data, current_text=None):
    """
    Merge user-editable options from a request into the stored options.

    `data` is the request body; only data["options"] is looked at. Keys that
    are missing keep their current value; null/""/0 clears refresh_seconds.
    Returns the new JSON text (or None). Raises OptionsError on bad input.
    """
    options = parse_options(current_text)
    incoming = data.get("options")
    if incoming is None:
        return dump_options(options)
    if not isinstance(incoming, dict):
        raise OptionsError("options must be an object")

    if "refresh_seconds" in incoming:
        value = incoming["refresh_seconds"]
        if value in (None, "", 0, "0"):
            options.pop("refresh_seconds", None)
        else:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise OptionsError("Refresh interval must be a whole number of seconds")
            if not MIN_REFRESH <= value <= MAX_REFRESH:
                raise OptionsError(
                    f"Refresh interval must be between {MIN_REFRESH} seconds and 24 hours"
                )
            options["refresh_seconds"] = value

    return dump_options(options)
