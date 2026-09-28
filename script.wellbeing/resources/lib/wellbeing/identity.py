"""A stable identity for the item currently playing.

The play-count limit has to answer "is this the same thing they were watching
before?" across stops, restarts of Kodi, and a day's worth of browsing. The
answer has to survive a restart, so it cannot be an object reference, and it
has to be the same for the same episode however it was reached.

Library items get their database id, which is exactly the identity Kodi
itself uses. Everything else falls back to the playing URL, which is stable
for files on a share. Title is the last resort.

Kodi's info tag getters raise when asked for the wrong media type, and
getPlayingFile() raises when playback has already stopped -- which happens
routinely, because these are called from player callbacks. Every access here
is therefore defensive: an identity we cannot determine returns "", and the
caller treats that as "do not count this".
"""


def _library_key(player) -> str:
    """`video:episode:412`, `music:song:99`, ... or "" if not a library item."""

    for getter, kind in ((getattr(player, "getVideoInfoTag", None), "video"),
                         (getattr(player, "getMusicInfoTag", None), "music")):
        if getter is None:
            continue
        try:
            tag = getter()
        except Exception:
            continue
        if tag is None:
            continue

        try:
            dbid = int(tag.getDbId())
        except Exception:
            continue
        if dbid <= 0:
            continue

        media = ""
        try:
            media = str(tag.getMediaType() or "")
        except Exception:
            media = ""

        return "%s:%s:%i" % (kind, media or "item", dbid)

    return ""


def _normalise(url: str) -> str:
    """Trim the parts of a URL that vary without the item varying."""

    url = (url or "").strip()
    if not url:
        return ""

    fragment = url.find("#")
    if fragment > 0:
        url = url[:fragment]

    if len(url) > 1 and url.endswith("/"):
        url = url[:-1]

    return url


def _title(player) -> str:
    for getter in (getattr(player, "getVideoInfoTag", None),
                   getattr(player, "getMusicInfoTag", None)):
        if getter is None:
            continue
        try:
            tag = getter()
            title = str(tag.getTitle() or "").strip()
        except Exception:
            continue
        if title:
            return title
    return ""


def identity(player) -> str:
    """The key this item is counted under, or "" if it cannot be determined."""

    key = _library_key(player)
    if key:
        return key

    try:
        url = _normalise(player.getPlayingFile())
    except Exception:
        url = ""
    if url:
        return "url:%s" % url

    title = _title(player)
    if title:
        return "title:%s" % title

    return ""


def label(player) -> str:
    """A human-readable name for notifications and the status screen."""

    title = _title(player)
    if title:
        return title

    try:
        url = _normalise(player.getPlayingFile())
    except Exception:
        return ""

    return url.rsplit("/", 1)[-1] if url else ""
