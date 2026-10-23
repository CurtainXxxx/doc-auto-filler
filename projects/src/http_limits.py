"""Enforce request limits before multipart parsing can spool unbounded content."""
from fastapi import HTTPException
from starlette.responses import JSONResponse
from document_access import MAX_REQUEST_BYTES


class RequestSizeLimit:
    def __init__(self, app, limit=MAX_REQUEST_BYTES):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, 400)(scope, receive, send)
        if length > self.limit:
            return await JSONResponse({"detail": "Request too large"}, 413)(scope, receive, send)
        size = 0

        async def limited_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > self.limit:
                raise HTTPException(413, "Request too large")
            return message
        await self.app(scope, limited_receive, send)
