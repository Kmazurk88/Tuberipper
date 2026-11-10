"""Counts audio answers being sent right now (someone is listening or downloading a song), so the
update script (deploy.sh) can wait instead of cutting the music off with a restart."""

import threading

_lock = threading.Lock()
_streams = 0


def streams() -> int:
    return _streams


class CountAudio:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        counted = False

        async def wrapped(message):
            nonlocal counted
            if message["type"] == "http.response.start" and not counted:
                ctype = dict(message.get("headers", [])).get(b"content-type", b"")
                if ctype.startswith(b"audio/"):
                    counted = True
                    _add(1)
            await send(message)

        try:
            await self.app(scope, receive, wrapped)
        finally:
            if counted:
                _add(-1)


def _add(n: int):
    global _streams
    with _lock:
        _streams += n
