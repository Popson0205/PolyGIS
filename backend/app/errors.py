"""One error shape everywhere: RFC 7807 style { type, title, status, detail, fields? }."""
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("errors")
TITLES = {400: "Bad request", 401: "Not authenticated", 403: "Forbidden", 404: "Not found",
          409: "Conflict", 413: "Payload too large", 422: "Validation failed", 429: "Too many requests"}


class ProblemOut(BaseModel):
    """Error body (application/problem+json)."""
    model_config = ConfigDict(extra="allow")
    type: str
    title: str
    status: int
    detail: str | None = None
    code: str | None = None
    fields: dict[str, list[str]] | None = None


class Problem(Exception):
    def __init__(self, status: int, detail: str | None = None, *, code: str = "error", title: str | None = None,
                 fields: dict | None = None, headers: dict | None = None, **extra):
        self.status, self.detail, self.code = status, detail, code
        self.title = title or TITLES.get(status, "Error")
        self.fields, self.headers, self.extra = fields, headers or {}, extra

    def body(self) -> dict:
        b = {"type": f"urn:geoestate-gis:problem:{self.code}", "title": self.title, "status": self.status,
             "detail": self.detail, "code": self.code}
        if self.fields:
            b["fields"] = self.fields
        return {**b, **self.extra}


def _resp(p: Problem) -> JSONResponse:
    return JSONResponse(jsonable_encoder(p.body()), status_code=p.status, headers=p.headers, media_type="application/problem+json")


def install(app: FastAPI):
    @app.exception_handler(Problem)
    async def _problem(_: Request, exc: Problem):
        return _resp(exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return _resp(Problem(exc.status_code, str(exc.detail), code=f"http_{exc.status_code}"))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields: dict[str, list[str]] = {}
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"] if p not in ("body", "query", "path"))
            fields.setdefault(loc or "request", []).append(e["msg"])
        return _resp(Problem(422, "The request has invalid fields.", code="validation", fields=fields))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return _resp(Problem(500, "Something went wrong on our side.", code="internal", title="Server error"))
