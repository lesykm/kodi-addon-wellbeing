import os
import re
import sys
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.wellbeing import playcount
from resources.lib.wellbeing import state as state_store

addon = xbmcaddon.Addon()


def _ask_password() -> bool:
    """The same prompt and password the limits themselves use."""

    given = xbmcgui.Dialog().input(
        heading=addon.getLocalizedString(32035),
        type=xbmcgui.INPUT_ALPHANUM,
        option=xbmcgui.ALPHANUM_HIDE_INPUT, autoclose=60000)

    if given != addon.getSettingString("password"):
        xbmcgui.Dialog().notification(addon.getLocalizedString(32000),
                                      addon.getLocalizedString(32036))
        return False

    return True


def _request(command: str) -> None:
    """Ask the service to do something, rather than doing it here.

    This script runs in its own interpreter: the service has the state file
    open and holds today's count in memory, so writing it from here would be
    overwritten the next time the service saved. Signalling through a setting
    keeps the service the only writer -- the same approach the existing reset
    action uses for the time budget.
    """

    addon.setSetting("countcommand", command)


def _read_count_state():
    """Today's count, as last written by the service.

    Reading is safe: the state file is replaced atomically, so a reader sees
    either the previous contents or the new ones, never a half-written file.
    """

    path = os.path.join(
        xbmcvfs.translatePath(addon.getAddonInfo("profile")), "state.json")
    reset_hour = addon.getSettingInt("countresethour")
    today = playcount.logical_day(time.time(), reset_hour)

    config = playcount.Config(
        limitation=addon.getSettingInt("countlimitation"),
        plays=[addon.getSettingInt("plays_%i" % d) for d in range(7)],
        grace=addon.getSettingInt("countgrace"),
        rule=addon.getSettingInt("countrule"),
        reset_hour=reset_hour,
        bonus_size=addon.getSettingInt("countbonus"))

    stored = state_store.load(path, today)

    if stored.get("day") != today:
        stored = state_store.empty(today)

    return playcount.PlayCounter(config, stored)


if __name__ == "__main__":

    if len(sys.argv) > 1 and sys.argv[1] == "status":

        pass

    elif len(sys.argv) > 1 and sys.argv[1] == "action=reset":

        addon.setSettingInt("sum", -1)

    elif len(sys.argv) > 1 and sys.argv[1] == "reset":

        time.sleep(1)  # wait for settings to be saved
        addon.setSettingInt("sum", -1)

    elif len(sys.argv) > 1 and sys.argv[1] == "action=limit" and sys.args[2].startswith("limit="):

        time_ = sys.argv[2][6:]
        if re.match(r"^\d{1,2}:\d{2}$", time_):
            xbmc.log(f"script.wellbeing: setting time limit to '{time_}'", xbmc.LOGINFO)
            addon.setSetting("limit", time_)
        elif time_.isdigit():
            xbmc.log(f"script.wellbeing: converting time '{time_}' to mm:ss", xbmc.LOGINFO)
            addon.setSetting("limit", f"{(int(time_) // 60):02d}:{(int(time_) % 60):02d}")
        else:
            xbmc.log(f"script.wellbeing: invalid time format '{time_}'", xbmc.LOGERROR)

    elif len(sys.argv) > 1 and sys.argv[1] == "limit":

        time.sleep(1)  # wait for settings to be saved
        time_ = xbmcgui.Dialog().numeric(
            2, addon.getLocalizedString(32075), addon.getSetting("limit_%i" % time.localtime().tm_wday))
        if time_:
            addon.setSetting("limit", time_)

    elif len(sys.argv) > 1 and sys.argv[1] == "countstatus":

        time.sleep(1)  # wait for settings to be saved
        counter = _read_count_state()
        now = time.time()
        status = counter.status(now)

        lines = [addon.getLocalizedString(32220) % (status["used"],
                                                    status["allowed"])]
        if status["bonus"]:
            lines.append(addon.getLocalizedString(32224) % status["bonus"])
        lines.append("")

        if status["items"]:
            for number, (name, count) in enumerate(status["items"], 1):
                lines.append("%2i.  %s%s" % (
                    number, name, "  (x%i)" % count if count > 1 else ""))
        else:
            lines.append(addon.getLocalizedString(32225))

        xbmcgui.Dialog().textviewer(addon.getLocalizedString(32210),
                                    "\n".join(lines))

    elif len(sys.argv) > 1 and sys.argv[1] == "countreset":

        time.sleep(1)  # wait for settings to be saved
        if _ask_password():
            _request("reset")

    elif len(sys.argv) > 1 and sys.argv[1] == "countgrant":

        time.sleep(1)  # wait for settings to be saved
        if _ask_password():
            _request("grant")

    else:
        addon.openSettings()
