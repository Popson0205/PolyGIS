import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import errors
from .config import settings
from .db import migrate, pool
from .routers import auth, cameras, crs, features, georef, geoint, hub, imports, maps, providers, tracking, visibility, scenarios, imagery, layers, layouts, linked, models, polyearth, projects, rasters, shares, thumbs, tools, geodatabases, projectfile

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app):
    import anyio
    await anyio.to_thread.run_sync(migrate)
    from .rasters import warn_if_not_kept
    warn_if_not_kept()
    await pool.open()
    from . import feedjob
    job = feedjob.start()
    yield
    if job:
        job.cancel()
    await pool.close()


app = FastAPI(title="PolyGIS API", version="1.0.0", lifespan=lifespan,
              docs_url=f"{settings.api_prefix}/docs", openapi_url=f"{settings.api_prefix}/openapi.json")
errors.install(app)
P = settings.api_prefix
for r in (auth.router, projects.router, layouts.router, shares.router, hub.router, linked.router, models.router, thumbs.router, layers.router, rasters.router, georef.router, maps.router, visibility.router, geoint.router, tracking.router, cameras.router, providers.router, scenarios.router, imagery.router, polyearth.router, features.router, crs.router, tools.router, geodatabases.router, projectfile.router, imports.router):
    app.include_router(r, prefix=P)


@app.get(f"{P}/health", include_in_schema=False)
async def health():
    try:
        async with pool.connection() as c:
            await c.execute("SELECT 1")
        return {"ok": True}
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False}, status_code=503)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Signed in by cookie, so changes must come from our own page: a cross-site form cannot set this header."""
    # (a tracker's address is the exception: devices post to it, and its secret token, not a cookie, says who may)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith(f"{P}/") \
            and not request.url.path.startswith((f"{P}/track/", f"{P}/cam/")) and request.headers.get("x-geoestate") != "1":
        return JSONResponse({"type": "urn:geoestate-gis:problem:csrf", "title": "Forbidden", "status": 403, "code": "csrf",
                             "detail": "Changes must come from PolyGIS's own pages."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    if not request.url.path.startswith("/share/"):           # a shared map may be embedded in another site
        response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


if settings.web_dir:
    _web = Path(settings.web_dir).resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def web_app(full_path: str):
        if P and ("/" + full_path).startswith(P + "/"):
            raise HTTPException(404)
        f = (_web / full_path).resolve()
        if full_path and f.is_file() and _web in f.parents:
            return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"} if full_path.startswith("assets/")
                                else {"Cache-Control": "no-cache"})
        if full_path.startswith("assets/"):
            raise HTTPException(404)
        return FileResponse(_web / "index.html", headers={"Cache-Control": "no-cache"})
