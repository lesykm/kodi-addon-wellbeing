"""How many items may still be played today.

The rules, and nothing else: no Kodi, no files, no dialogs. What goes in is a
configuration, a state dictionary from state.py and the current time; what
comes out is whether an item may play and what has been counted so far.

The counting rule
-----------------
An item costs one play once it has actually been watched for `grace` seconds.
Starting something and changing your mind costs nothing, which is what makes
browsing possible. Pausing, seeking and resuming cost nothing either, because
only real playing time is fed in.

Under the default rule (`distinct_per_day`) an item costs at most one play per
day however often it is opened, so re-watching this morning's episode is free.
Under `every_start` each viewing is charged. The first matches how a daily
allowance is usually meant -- "you may watch three things today" -- and leans
on a separate screen-time limit to stop an afternoon of re-watching one
episode; the second is stricter and is there for setups without one.

The day
-------
A "day" runs from `reset_hour` local time. With the default of 0 that is the
calendar day; setting it to 4 means a film still running at one in the morning
is yesterday's.

Clocks
------
Moving the clock forward is indistinguishable from time passing, and buys a
new day. Moving it *backwards* is refused: the count is kept rather than
reset, because a reset is the outcome a child would want. `last_seen` only
ever moves forward.
"""

import time

from resources.lib.wellbeing import state as state_module

COUNT_OFF = 0
COUNT_AUDIO_VIDEO = 1
COUNT_VIDEO = 2

RULE_DISTINCT_PER_DAY = 0
RULE_EVERY_START = 1

SECONDS_PER_HOUR = 3600


class Config:
    """The settings the counter needs, already parsed."""

    def __init__(self, limitation=COUNT_OFF, plays=None, grace=90,
                 rule=RULE_DISTINCT_PER_DAY, reset_hour=0, bonus_size=1):
        self.limitation = limitation
        self.plays = list(plays) if plays else [3] * 7
        self.grace = max(0, int(grace))
        self.rule = rule
        self.reset_hour = min(23, max(0, int(reset_hour)))
        self.bonus_size = max(1, int(bonus_size))


def logical_day(now: float, reset_hour: int) -> str:
    """The date the moment `now` belongs to, given a day that starts at
    `reset_hour`."""

    shifted = time.localtime(now - reset_hour * SECONDS_PER_HOUR)
    return "%04i-%02i-%02i" % (shifted.tm_year, shifted.tm_mon, shifted.tm_mday)


def logical_wday(now: float, reset_hour: int) -> int:
    """tm_wday of the logical day (Mon=0), so late-night viewing is charged to
    the evening it started."""

    return time.localtime(now - reset_hour * SECONDS_PER_HOUR).tm_wday


class PlayCounter:

    def __init__(self, config: Config, state: dict, persist=None, log=None):
        self._config = config
        self._state = state
        self._persist = persist if persist is not None else (lambda s: None)
        self._log = log if log is not None else (lambda m, level="info": None)

        self._key = ""
        self._label = ""
        self._played = 0.0
        self._charged = False


    def configure(self, config: Config) -> None:
        self._config = config

    @property
    def config(self) -> Config:
        return self._config

    @property
    def state(self) -> dict:
        return self._state

    def applies(self, is_video: bool) -> bool:
        """Whether the count covers what is playing at all."""

        if self._config.limitation == COUNT_OFF:
            return False
        if self._config.limitation == COUNT_VIDEO and not is_video:
            return False
        return True


    def _touch(self, now: float) -> None:
        self._state["last_seen"] = max(int(self._state.get("last_seen", 0)), int(now))

    def rollover(self, now: float) -> bool:
        """Start a new day if one has begun. Returns True if it did."""

        day = logical_day(now, self._config.reset_hour)
        if day == self._state.get("day"):
            self._touch(now)
            return False

        last_seen = int(self._state.get("last_seen", 0))
        if last_seen and now < last_seen:
            self._log("clock moved backwards (%i < %i); keeping today's play "
                      "count rather than resetting it"
                      % (int(now), last_seen), "warning")
            return False

        self._log("new day (%s); play count reset" % day)
        carried_last_seen = max(last_seen, int(now))
        self._state.clear()
        self._state.update(state_module.empty(day))
        self._state["last_seen"] = carried_last_seen

        self._charged = False
        self._played = 0.0

        self._persist(self._state)
        return True


    def used(self) -> int:
        return int(self._state.get("charges", 0))

    def allowed(self, now: float) -> int:
        wday = logical_wday(now, self._config.reset_hour)
        base = self._config.plays[wday] if 0 <= wday < len(self._config.plays) else 0
        return max(0, int(base)) + int(self._state.get("bonus", 0))

    def remaining(self, now: float) -> int:
        return max(0, self.allowed(now) - self.used())

    def is_free(self, key: str) -> bool:
        """True when playing `key` costs nothing, because it is already paid
        for today and the rule charges an item only once."""

        return (self._config.rule == RULE_DISTINCT_PER_DAY
                and key in self._state.get("counted", {}))

    def should_block(self, key: str, is_video: bool, now: float) -> bool:
        """Whether this item must be refused."""

        if not self.applies(is_video):
            return False

        if not key:
            return False

        self.rollover(now)

        if self.is_free(key):
            return False

        return self.used() >= self.allowed(now)


    def begin(self, key: str, label: str, now: float) -> None:
        """A new item has started playing."""

        self.rollover(now)
        self._key = key
        self._label = label
        self._played = 0.0
        self._charged = False

    def end(self) -> None:
        """Playback stopped; nothing is owed for an item that never reached
        its grace period."""

        self._key = ""
        self._label = ""
        self._played = 0.0
        self._charged = False

    def tick(self, seconds: float, now: float) -> bool:
        """Feed in `seconds` of *actual* playing time. True if that charged a
        play."""

        if not self._key or self._charged:
            return False

        self._played += max(0.0, float(seconds))
        if self._played < self._config.grace:
            return False

        return self._charge(now)

    def _charge(self, now: float) -> bool:
        self._charged = True

        counted = self._state.setdefault("counted", {})
        record = counted.get(self._key)

        if record is not None and self._config.rule == RULE_DISTINCT_PER_DAY:
            return False

        if record is not None:
            record["count"] = int(record.get("count", 1)) + 1
        else:
            counted[self._key] = {
                "first": int(now),
                "label": self._label,
                "count": 1,
            }

        self._state["charges"] = self.used() + 1
        self._touch(now)
        self._persist(self._state)

        self._log("counted '%s' (%s); %i of %i used"
                  % (self._label or self._key, self._key,
                     self.used(), self.allowed(now)))
        return True


    def grant_bonus(self, plays: int, now: float) -> int:
        """Give `plays` extra items for today. Returns the new allowance."""

        self.rollover(now)
        self._state["bonus"] = max(0, int(self._state.get("bonus", 0)) + int(plays))
        self._touch(now)
        self._persist(self._state)
        self._log("granted %i extra play(s); %i of %i used"
                  % (plays, self.used(), self.allowed(now)))
        return self.allowed(now)

    def reset(self, now: float) -> None:
        """Clear today's count entirely."""

        last_seen = max(int(self._state.get("last_seen", 0)), int(now))
        self._state.clear()
        self._state.update(state_module.empty(
            logical_day(now, self._config.reset_hour)))
        self._state["last_seen"] = last_seen
        self._charged = False
        self._played = 0.0
        self._persist(self._state)
        self._log("play count reset by request")


    def status(self, now: float) -> dict:
        self._touch(now)
        counted = self._state.get("counted", {})
        return {
            "day": self._state.get("day", ""),
            "used": self.used(),
            "allowed": self.allowed(now),
            "remaining": self.remaining(now),
            "bonus": int(self._state.get("bonus", 0)),
            "items": [
                (record.get("label") or key, int(record.get("count", 1)))
                for key, record in sorted(
                    counted.items(), key=lambda kv: kv[1].get("first", 0))
            ],
        }
