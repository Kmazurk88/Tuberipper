"""Gzip for text answers (pages, script, stylesheet, JSON) so the app opens faster on mobile data.
Audio, covers, the APK and partial (Range) answers pass through untouched: they are already compressed
and the player needs their Content-Length/Range headers intact."""

import zlib

_TYPES = ("text/", "application/json", "application/javascript", "image/svg+xml", "application/manifest+json")
_MIN_BYTES = 1024


class Gzip:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") == "HEAD":
            return await self.app(scope, receive, send)
        accepts = b""
        for name, value in scope.get("headers", []):
            if name == b"accept-encoding":
                accepts = value
        if b"gzip" not in accepts:
            return await self.app(scope, receive, send)

        start = None        # the held-back response start, until the first body chunk decides
        gz = None

        async def wrapped(message):
            nonlocal start, gz
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] != "http.response.body" or start is None and gz is None:
                return await send(message)
            body, more = message.get("body", b""), message.get("more_body", False)
            if start is not None:
                first, start = start, None
                if _compressible(first, body, more):
                    gz = zlib.compressobj(6, zlib.DEFLATED, 31)
                    headers = [(k, v) for k, v in first.get("headers", []) if k.lower() != b"content-length"]
                    headers += [(b"content-encoding", b"gzip"), (b"vary", b"Accept-Encoding")]
                    first = {**first, "headers": headers}
                await send(first)
            if gz is not None:
                body = gz.compress(body) + (gz.flush(zlib.Z_SYNC_FLUSH) if more else gz.flush())
            await send({**message, "body": body})

        await self.app(scope, receive, wrapped)


def _compressible(start, body, more) -> bool:
    if start.get("status") != 200:
        return False
    headers = {k.lower(): v for k, v in start.get("headers", [])}
    if b"content-encoding" in headers or b"content-range" in headers:
        return False
    ctype = headers.get(b"content-type", b"").decode("latin-1").lower()
    if ctype.startswith("text/event-stream") or not ctype.startswith(_TYPES):
        return False
    return more or len(body) >= _MIN_BYTES
