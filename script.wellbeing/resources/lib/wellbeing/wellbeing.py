import datetime
import os
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.wellbeing import identity, playcount
from resources.lib.wellbeing import state as state_store
from resources.lib.wellbeing.player import Player

LOG_LEVELS = {
    "info": xbmc.LOGINFO,
    "warning": xbmc.LOGWARNING,
    "error": xbmc.LOGERROR,
}

CHECK_INTERVAL = 10

SAVE_INTERVAL = 60

SELF_WRITE_GRACE = 2.0

SLICE_INTERVAL = 1

OFF = 0
AUDIO_VIDEO = 1
VIDEO = 2

NOTIFY_OFF = 0
NOTIFY_1M = 1
NOTIFY_5M = 2
NOTIFY_15M = 3
NOTIFY_HOURLY = 4

TIME_1M = 60
TIME_5M = 300
TIME_15M = 900
TIME_20M = 1200
TIME_30M = 1800
TIME_1H = 3600
TIME_2H = 7200

DEFAULT_LIMIT = "24:00"
SECONDS_PER_DAY = 86400

AUTO_STOP_INTERVAL = [None, TIME_15M, TIME_20M, TIME_30M, TIME_1H]


class Wellbeing(xbmc.Monitor):

    def __init__(self) -> None:

        self._changed = .0

        self._addon = xbmcaddon.Addon()
        self._id: str = self._addon.getAddonInfo("id")

        self._countLock = threading.RLock()
        self._counter = self._buildCounter()

        self._blocked = None
        self._warnLast: bool = True

        self._player = Player(onStarted=self._onPlaybackStarted,
                              onStopped=self._onPlaybackStopped)

        self._wday: int = -1
        self._ignoreLimit: bool = False
        self._ignoreRestPeriod: int = -1

        self._limitation: int = OFF
        self._limits: list = list()

        self._restperiods: list = list()
        self._restfrom: list = list()
        self._restto: list = list()

        self._autostopInterval: int = 0

        self._notification: int = NOTIFY_HOURLY

        self._password: str = ""

        self._icon: str = os.path.join(xbmcvfs.translatePath(self._addon.getAddonInfo('path')),
                                       "resources",
                                       "assets", "icon.png")

        self._sum: int = self._addon.getSettingInt("sum") if self._addon.getSetting(
            "date") == datetime.datetime.strftime(datetime.datetime.now(), "%Y-%m-%d") else 0

        self._selfWriteUntil: float = .0
        self._savedSum: int = self._sum
        self._lastSaved: float = time.time()

        self.onSettingsChanged()


    def _log(self, message: str, level: str = "info") -> None:

        xbmc.log("%s: %s" % (self._id, message),
                 LOG_LEVELS.get(level, xbmc.LOGINFO))

    def _readCountConfig(self) -> playcount.Config:

        return playcount.Config(
            limitation=self._addon.getSettingInt("countlimitation"),
            plays=[self._addon.getSettingInt("plays_%i" % d) for d in range(7)],
            grace=self._addon.getSettingInt("countgrace"),
            rule=self._addon.getSettingInt("countrule"),
            reset_hour=self._addon.getSettingInt("countresethour"),
            bonus_size=self._addon.getSettingInt("countbonus"))

    def _buildCounter(self) -> playcount.PlayCounter:

        self._statePath = os.path.join(
            xbmcvfs.translatePath(self._addon.getAddonInfo("profile")),
            "state.json")

        config = self._readCountConfig()
        today = playcount.logical_day(time.time(), config.reset_hour)

        return playcount.PlayCounter(
            config,
            state_store.load(self._statePath, today, log=self._log),
            persist=lambda s: state_store.save(self._statePath, s, log=self._log),
            log=self._log)

    def _onPlaybackStarted(self) -> None:
        """Kodi's thread. Keep it short.

        Kodi's Python API has no way to refuse playback before it begins, so
        the only option is to let it start and stop it at once. Everything
        here is in memory; the dialog is left to the service loop, because
        blocking Kodi's callback thread on a modal is a good way to wedge the
        player.
        """

        key = identity.identity(self._player)
        name = identity.label(self._player)
        now = time.time()

        try:
            path = self._player.getPlayingFile()
        except Exception:
            path = ""

        with self._countLock:
            if self._counter.should_block(key, self._player.isPlayingVideo(), now):
                self._blocked = (key, name, path)
                self._log("refused '%s': no items left today" % (name or key))
                self._player.stop()
                return

            self._counter.begin(key, name, now)

    def _handleBlocked(self) -> None:
        """Explain a refusal, and offer the way past it. Service loop only."""

        with self._countLock:
            blocked = self._blocked
            self._blocked = None

        if blocked is None:
            return

        _key, _name, path = blocked

        self._notify(32223)

        if not self._askForExtraPlays():
            return

        with self._countLock:
            granted = self._counter.config.bonus_size
            self._counter.grant_bonus(granted, time.time())

        xbmcgui.Dialog().notification(
            self._addon.getLocalizedString(32000),
            self._addon.getLocalizedString(32224) % granted, icon=self._icon)

        if path:
            try:
                self._player.play(path)
            except Exception as e:
                self._log("could not restart '%s' after the override: %s"
                          % (path, e), "warning")

    def _runCountCommand(self, command: str) -> None:
        """Carry out a request left by script.py.

        The script runs in its own interpreter and cannot safely write the
        state file -- the service holds today's count in memory and would
        overwrite it on the next save -- so it leaves a request in a setting
        and the service, the only writer, acts on it here.
        """

        now = time.time()

        if command == "reset":
            with self._countLock:
                self._counter.reset(now)
                used, allowed = self._counter.used(), self._counter.allowed(now)

        elif command == "grant":
            with self._countLock:
                granted = self._counter.config.bonus_size
                self._counter.grant_bonus(granted, now)
                used, allowed = self._counter.used(), self._counter.allowed(now)

        else:
            self._log("ignoring unknown command '%s'" % command, "warning")
            return

        xbmcgui.Dialog().notification(
            self._addon.getLocalizedString(32000),
            self._addon.getLocalizedString(32220) % (used, allowed),
            icon=self._icon)

    def _askForExtraPlays(self) -> bool:
        """The same prompt the time limit uses, and the same password."""

        password = xbmcgui.Dialog().input(
            heading=self._addon.getLocalizedString(32035),
            type=xbmcgui.INPUT_ALPHANUM,
            option=xbmcgui.ALPHANUM_HIDE_INPUT, autoclose=60000)

        if password != self._password:
            self._notify(32036)
            return False

        return True

    def _onPlaybackStopped(self) -> None:
        """Kodi's thread. Keep it short."""

        with self._countLock:
            self._counter.end()

    def _handlePlayCount(self, _interval: int) -> None:

        now = time.time()

        with self._countLock:
            if not self._counter.applies(self._player.isPlayingVideo()):
                return
            if not self._counter.tick(_interval, now):
                return
            used = self._counter.used()
            allowed = self._counter.allowed(now)
            left = self._counter.remaining(now)

        if self._warnLast and left == 0:
            self._notify(32222)
        elif self._notification != NOTIFY_OFF:
            xbmcgui.Dialog().notification(
                self._addon.getLocalizedString(32000),
                self._addon.getLocalizedString(32220) % (used, allowed),
                icon=self._icon)

    def onSettingsChanged(self) -> None:

        if time.time() < self._selfWriteUntil:
            return

        ts = time.time()
        if self._changed + 1 > ts:
            return
        self._changed = ts

        limits = list()
        restperiods = list()
        restfrom = list()
        restto = list()
        for d in range(7):
            limits.append(self._timeformat_to_seconds(
                self._addon.getSetting("limit_%i" % d)))
            restperiods.append(self._addon.getSettingInt("restperiod_%i" % d))
            restfrom.append(self._timeformat_to_seconds(
                self._addon.getSetting("restfrom_%i" % d)))
            restto.append(self._timeformat_to_seconds(
                self._addon.getSetting("restto_%i" % d)))

        self._limits = limits
        self._restperiods = restperiods
        self._restfrom = restfrom
        self._restto = restto

        self._notification = self._addon.getSettingInt("notification")

        self._wday = time.localtime().tm_wday
        self._ignoreLimit = False
        self._ignoreRestPeriod = -1

        sum = self._addon.getSettingInt("sum")
        if sum < 0:
            self._sum = 0
            self._addon.setSettingInt("sum", 0)

        limit = self._addon.getSetting("limit")
        if limit and limit != DEFAULT_LIMIT:
            self._limits[self._wday] = self._timeformat_to_seconds(limit)
            self._addon.setSetting("limit", DEFAULT_LIMIT)

        self._password = self._addon.getSettingString("password")

        self._warnLast = self._addon.getSettingBool("countwarnlast")

        with self._countLock:
            self._counter.configure(self._readCountConfig())

        command = self._addon.getSetting("countcommand")
        if command:
            self._addon.setSetting("countcommand", "")
            self._runCountCommand(command)

        self._autostopInterval = AUTO_STOP_INTERVAL[self._addon.getSettingInt(
            "autostop")]

        self._limitation = self._addon.getSettingInt("limitation")
        if self._limitation != OFF:
            left = self._get_time_left(time.localtime())
            s1 = self._addon.getLocalizedString(32040) % (self._format_seconds(self._sum + 59),
                                                          self._addon.getLocalizedString(32013 if self._limitation == VIDEO else 32012))
            s2 = self._addon.getLocalizedString(
                32041) % self._format_seconds(left)
            xbmcgui.Dialog().notification(self._addon.getLocalizedString(
                32000), "%s. %s" % (s1, s2), icon=self._icon)

    def _notify(self, msgId):

        xbmcgui.Dialog().notification(self._addon.getLocalizedString(
            32000), self._addon.getLocalizedString(msgId), icon=self._icon)

    def _get_time_left(self, t_now: time.struct_time) -> int:

        limit = self._limits[t_now.tm_wday]
        left = limit - self._sum
        return max(0, left)

    def _timeformat_to_seconds(self, stime: str) -> int:

        hh_mm = stime.split(":")
        return int(hh_mm[0]) * TIME_1H + int(hh_mm[1]) * TIME_1M

    def _format_seconds(self, secs: int) -> str:

        return "%02i:%02i" % (secs // TIME_1H, (secs % TIME_1H) // TIME_1M)

    def _stopAndAskForReactivation(self) -> bool:

        self._player.pause()

        password = xbmcgui.Dialog().input(heading=self._addon.getLocalizedString(
            32035), type=xbmcgui.INPUT_ALPHANUM, option=xbmcgui.ALPHANUM_HIDE_INPUT, autoclose=60000)
        if password != self._password:
            self._notify(32036)
            self._player.stop()
            return False

        else:
            self._notify(32037)
            self._player.pause()
            return True

    def _handleLimit(self, t_now: time.struct_time, _interval: int) -> bool:

        if self._limitation == VIDEO and not self._player.isPlayingVideo():
            return False

        reached = False
        self._sum += _interval

        left = self._get_time_left(t_now)
        if not self._ignoreLimit and left <= 0:
            reached = True
            self.saveUsageToSettings()
            self._notify(32034)
            if self._stopAndAskForReactivation():
                self._ignoreLimit = True

        elif self._notification >= NOTIFY_1M and left > TIME_1M - _interval and left <= TIME_1M:
            self._notify(32033)

        elif self._notification >= NOTIFY_5M and left > TIME_5M - _interval and left <= TIME_5M:
            self._notify(32032)

        elif self._notification >= NOTIFY_15M and left > TIME_15M - _interval and left <= TIME_15M:
            self._notify(32031)

        elif self._notification == NOTIFY_HOURLY and self._sum % TIME_1H < _interval:
            s1 = self._addon.getLocalizedString(32040) % (self._format_seconds(self._sum + 59 - _interval),
                                                          self._addon.getLocalizedString(32013 if self._limitation == VIDEO else 32012))

            s2 = self._addon.getLocalizedString(32041) % self._format_seconds(
                left) if left <= TIME_2H and not self._ignoreLimit else ""

            xbmcgui.Dialog().notification(self._addon.getLocalizedString(
                32000), "%s. %s" % (s1, s2), icon=self._icon)

        return reached

    def _handleRestPeriod(self, t_now: time.struct_time) -> bool:

        def _handleEnteringRestPeriod():

            self._notify(32080)
            return self._stopAndAskForReactivation()

        def _handleAutostop() -> None:

            if not xbmcgui.Dialog().yesno(heading="%s - %s" % (self._addon.getLocalizedString(32000), self._addon.getLocalizedString(32004)), message=self._addon.getLocalizedString(32008), autoclose=60000):
                self._player.stop()

        entered = False
        secs_in_day = t_now.tm_sec + t_now.tm_min * TIME_1M + t_now.tm_hour * TIME_1H
        if self._restperiods[t_now.tm_wday] \
            and secs_in_day >= self._restfrom[self._wday] \
            and (self._restperiods[t_now.tm_wday] == AUDIO_VIDEO
                 or self._restperiods[t_now.tm_wday] == VIDEO and self._player.isPlayingVideo()):

            entered = True
            if self._ignoreRestPeriod != t_now.tm_wday * 2 + 1:
                if _handleEnteringRestPeriod():
                    self._ignoreRestPeriod = t_now.tm_wday * 2 + 1

            elif self._autostopInterval and (secs_in_day - self._restfrom[self._wday]) % self._autostopInterval < CHECK_INTERVAL:
                _handleAutostop()

        elif self._restperiods[(t_now.tm_wday - 1) % 7] \
            and secs_in_day < self._restto[self._wday] \
            and (self._restperiods[(t_now.tm_wday - 1) % 7] == AUDIO_VIDEO
                 or self._restperiods[(t_now.tm_wday - 1) % 7] == VIDEO and self._player.isPlayingVideo()):

            entered = True
            if self._ignoreRestPeriod != t_now.tm_wday * 2:
                if _handleEnteringRestPeriod():
                    self._ignoreRestPeriod = t_now.tm_wday * 2

            elif self._autostopInterval and (secs_in_day + SECONDS_PER_DAY - self._restfrom[(t_now.tm_wday - 1) % 7]) % self._autostopInterval < CHECK_INTERVAL:
                _handleAutostop()

        return entered

    def start(self) -> None:

        while not self.abortRequested():

            t_now = time.localtime()
            if t_now.tm_wday != self._wday:
                self._wday = time.localtime().tm_wday
                self._sum = 0
                self._ignoreLimit = False
                self._ignoreRestPeriod = -1
                self.saveUsageToSettings()

            _interval = CHECK_INTERVAL - t_now.tm_sec % CHECK_INTERVAL

            _playing = self._player.isPlaying() and not self._player.isPaused()

            if _playing:
                self._handlePlayCount(_interval)

            _playing \
                and not self._handleLimit(t_now, _interval) \
                and not self._handleRestPeriod(t_now)

            self._saveUsageIfDue()

            if self._waitAndHandleBlocks(_interval):
                break

        self.saveUsageToSettings()

    def _saveUsageIfDue(self) -> None:

        if self._sum == self._savedSum:
            return

        if time.time() - self._lastSaved < SAVE_INTERVAL:
            return

        self.saveUsageToSettings()

    def _waitAndHandleBlocks(self, seconds: int) -> bool:
        """Wait `seconds`, checking for refused playback along the way.

        The total wait is unchanged, so the time budget's arithmetic is
        untouched -- only the granularity differs. A refusal that arrives just
        after a tick would otherwise sit unexplained for the rest of the
        interval, with playback already stopped.
        """

        left = seconds
        while left > 0:
            self._handleBlocked()

            step = min(SLICE_INTERVAL, left)
            if self.waitForAbort(step):
                return True
            left -= step

        self._handleBlocked()
        return False

    def saveUsageToSettings(self) -> None:

        self._selfWriteUntil = time.time() + SELF_WRITE_GRACE

        self._addon.setSetting("date", datetime.datetime.strftime(
            datetime.datetime.now(), "%Y-%m-%d"))
        self._addon.setSettingInt("sum", self._sum)

        self._savedSum = self._sum
        self._lastSaved = time.time()
