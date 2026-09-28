"""Durable state for the play-count limit.

The play count cannot live in the add-on settings the way the time budget
does: it is a mapping of which items have been charged today, not a single
integer, and it has to be written the moment it changes rather than on a
timer. It lives in a small JSON file in the profile's addon_data directory,
which Kodi creates for every profile, so each user gets their own count
without any extra arrangement.

This module deliberately knows nothing about Kodi. It takes a path and
returns dictionaries, so the interesting behaviour -- recovering a truncated
file, refusing to lose a day's count to a parse error -- can be tested
without a running Kodi.

Failure policy: **fail open**. A child's tablet that has locked itself out
because of a corrupt state file is a worse outcome than a child who gets a
few extra episodes on the day the file was damaged. Every recovery path
therefore ends with a usable state, and says loudly in the log what it did.
"""

import json
import os
import tempfile

VERSION = 1


def empty(day: str = "") -> dict:
    """A fresh state for `day`, with nothing counted yet."""

    return {
        "version": VERSION,
        "day": day,
        "counted": {},
        "charges": 0,
        "bonus": 0,
        "last_seen": 0,
    }


def _coerce(raw: object, day: str) -> dict:
    """Force whatever was parsed into a usable state, keeping what we can.

    A file written by a newer version, or damaged in a way that still parses,
    must not take the add-on down: unknown keys are dropped, missing ones are
    defaulted, and wrong types are replaced.
    """

    state = empty(day)
    if not isinstance(raw, dict):
        return state

    if isinstance(raw.get("day"), str):
        state["day"] = raw["day"]
    if isinstance(raw.get("bonus"), int) and raw["bonus"] >= 0:
        state["bonus"] = raw["bonus"]
    if isinstance(raw.get("charges"), int) and raw["charges"] >= 0:
        state["charges"] = raw["charges"]
    if isinstance(raw.get("last_seen"), (int, float)) and raw["last_seen"] >= 0:
        state["last_seen"] = int(raw["last_seen"])

    counted = raw.get("counted")
    if isinstance(counted, dict):
        for key, record in counted.items():
            if not isinstance(key, str):
                continue
            if not isinstance(record, dict):
                state["counted"][key] = {"first": 0, "label": "", "count": 1}
                continue
            state["counted"][key] = {
                "first": int(record.get("first", 0) or 0),
                "label": str(record.get("label", "") or ""),
                "count": max(1, int(record.get("count", 1) or 1)),
            }

    state["charges"] = max(state["charges"], len(state["counted"]))

    return state


def load(path: str, day: str = "", log=None) -> dict:
    """Read the state, falling back to the backup and then to a fresh day.

    Returns a usable state in every case.
    """

    def note(message, level="warning"):
        if log is not None:
            log(message, level)

    for candidate, described in ((path, "state"), (path + ".bak", "backup state")):
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            continue
        except (ValueError, OSError) as exc:
            note("%s file %s is unreadable (%s)" % (described, candidate, exc))
            continue

        state = _coerce(raw, day)
        if described != "state":
            note("recovered the play count from %s" % candidate)
        return state

    note("no usable play-count state; starting today from zero", "error")
    return empty(day)


def save(path: str, state: dict, log=None) -> bool:
    """Write the state atomically, keeping the previous copy as `.bak`.

    Returns True if the state reached disk. A failure here is reported but not
    raised: losing a count update must not stop playback handling.
    """

    def note(message, level="warning"):
        if log is not None:
            log(message, level)

    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        note("cannot create %s for the play count (%s)" % (directory, exc), "error")
        return False

    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as src:
                previous = src.read()
            with open(path + ".bak", "w", encoding="utf-8") as dst:
                dst.write(previous)
    except OSError as exc:
        note("could not refresh the play-count backup (%s)" % exc)

    handle = None
    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
        handle = os.fdopen(fd, "w", encoding="utf-8")
        json.dump(state, handle, indent=1, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(tmp_path, path)
        return True
    except OSError as exc:
        note("could not write the play count to %s (%s)" % (path, exc), "error")
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False
