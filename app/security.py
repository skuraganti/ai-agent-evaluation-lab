"""Local-only HTTP boundaries, kept as independent project source."""

from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

MAX_REQUEST_BYTES = 65536


class BodyLimit:
    """Bound chunked request bodies before any JSON parser sees them."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        header = dict(scope.get("headers", [])).get(b"content-length")
        declared = None
        if header is not None:
            try:
                declared = int(header)
                if declared < 0:
                    raise ValueError()
            except ValueError:
                return await JSONResponse({"error": "invalid content length"}, status_code=400)(
                    scope, receive, send
                )
            if declared > MAX_REQUEST_BYTES:
                return await JSONResponse({"error": "body too large"}, status_code=413)(
                    scope, receive, send
                )
        chunks, total = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > MAX_REQUEST_BYTES:
                return await JSONResponse({"error": "body too large"}, status_code=413)(
                    scope, receive, send
                )
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        if declared is not None and declared != total:
            return await JSONResponse({"error": "content length mismatch"}, status_code=400)(
                scope, receive, send
            )
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


def protect(app):
    app.add_middleware(BodyLimit)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_boundary(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            expected = str(request.base_url).rstrip("/")
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin and origin != expected
            ):
                return JSONResponse(
                    {"error": "Same-origin local requests required"}, status_code=403
                )
        response = await call_next(request)
        response.headers.update(
            {
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'"
                ),
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            }
        )
        return response
