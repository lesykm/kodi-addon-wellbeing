import xbmc


class Player(xbmc.Player):

    def __init__(self, onStarted=None, onStopped=None) -> None:
        super().__init__()
        self._paused: bool = False
        self._onStarted = onStarted
        self._onStopped = onStopped

    def _call(self, callback) -> None:
        """Run a listener without letting it break Kodi's callback.

        These run on Kodi's thread. An exception raised here is not reported
        anywhere useful and can stop later callbacks from arriving, so the
        add-on would quietly stop noticing playback at all.
        """

        if callback is None:
            return

        try:
            callback()
        except Exception as e:
            xbmc.log("wellbeing: playback listener failed: %s" % e, xbmc.LOGERROR)

    def onPlayBackStarted(self) -> None:

        self._paused = False

    def onAVStarted(self) -> None:

        self._paused = False
        self._call(self._onStarted)

    def onPlayBackStopped(self) -> None:

        self._paused = False
        self._call(self._onStopped)

    def onPlayBackEnded(self) -> None:

        self._paused = False
        self._call(self._onStopped)

    def onPlayBackError(self) -> None:

        self._paused = False
        self._call(self._onStopped)

    def onPlayBackPaused(self) -> None:

        self._paused = True

    def onPlayBackResumed(self) -> None:

        self._paused = False

    def isPaused(self) -> bool:

        return self._paused
