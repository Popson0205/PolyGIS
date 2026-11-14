"""Georeferencing: bring in an image without coordinates (or choose a raster that is in the wrong place), match pixels
to places on the map, check the residuals and a preview, and save the result as a new raster layer."""
import base64
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Literal
from uuid import UUID

import anyio
import numpy as np
import rasterio
from fastapi import APIRouter, Depends, File, Response, UploadFile
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import georef, rasters
from ..audit import audit
from ..config import settings
from ..db import get_conn
from ..errors import Problem
from ..identity import need
from .layers import _touch, layer_of
from .projects import project_of
from .rasters import _insert, raster_of

router = APIRouter(tags=["georeference"])
IMAGES = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", *rasters.EXTENSIONS)
EDIT = need("editor", write=True, feature="raster")
VIEW = need(feature="raster")


class Point(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8], max_length=40)
    col: float
    row: float
    lng: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    on: bool = True


Method = Literal["similarity", "affine", "poly2", "poly3", "tps"]


class WorkIn(BaseModel):
    points: list[Point] = Field(default_factory=list, max_length=500)
    method: Method = "affine"


class FitIn(WorkIn):
    epsg: int | None = None
    preview: bool = False


class SaveIn(WorkIn):
    name: str | None = Field(None, max_length=200)
    epsg: int | None = None
    resampling: Literal["bilinear", "nearest"] = "bilinear"
    cell: float | None = Field(None, gt=0)


def _out(r: dict) -> dict:
    return {"id": r["id"], "project_id": r["project_id"], "name": r["name"], "width": r["width"], "height": r["height"],
            "band_count": r["band_count"], "layer_id": r["layer_id"], "had_crs": r["had_crs"], "points": r["points"], "method": r["method"],
            "methods": [{"id": k, "label": v[0], "min_points": v[1]} for k, v in georef.METHODS.items()],
            "updated_at": r["updated_at"]}


async def _source(conn, gid: UUID, user) -> dict:
    cur = await conn.execute("SELECT * FROM georef_sources WHERE id = %s AND org_id = %s", (gid, user["org_id"]))
    r = await cur.fetchone()
    if not r:
        raise Problem(404, "That georeferencing work was not found.", code="not_found")
    await project_of(conn, r["project_id"], user)
    return r


def _problem(e: Exception) -> Problem:
    return Problem(422, str(e), code="georeference") if isinstance(e, (georef.GeorefError, rasters.RasterError)) else \
        Problem(422, "The image could not be read.", code="unreadable")


@router.post("/projects/{project_id}/georeference", status_code=201, summary="Start georeferencing an image (a scan, plan or photo)")
async def start(project_id: UUID, file: UploadFile = File(...), user=Depends(EDIT), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    name = Path(file.filename or "image").name
    if not name.lower().endswith(IMAGES):
        raise Problem(422, "Choose an image: JPEG, PNG, TIFF, BMP, WebP, GIF, or another raster.", code="validation")
    d = Path(tempfile.mkdtemp(prefix="polygis-g-"))
    try:
        path, size, most = d / name, 0, settings.max_raster_mb * 1024 * 1024
        with open(path, "wb") as f:
            while chunk := await file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > most:
                    raise Problem(413, f"The image is larger than {settings.max_raster_mb} MB.", code="too_large")
                f.write(chunk)
        if not size:
            raise Problem(422, "The file is empty.", code="validation")

        def keep():
            try:
                src = rasterio.open(path)
            except rasterio.errors.RasterioIOError:
                raise rasters.RasterError("PolyGIS could not read that image. JPEG, PNG and TIFF work best.") from None
            with src:
                w, h, n = src.width, src.height, src.count
                if w * h > settings.max_raster_cells:
                    raise rasters.RasterError(f"The image has {w * h:,} pixels; georeferencing works on up to {settings.max_raster_cells:,}. "
                                              "Shrink it first.")
                if n > 16:
                    raise rasters.RasterError(f"The image has {n} bands; the most for georeferencing is 16.")
                plain = d / "plain.tif"
                prof = {"driver": "GTiff", "width": w, "height": h, "count": n, "dtype": src.dtypes[0], "tiled": True, "compress": "DEFLATE",
                        "nodata": src.nodata}
                with rasterio.open(plain, "w", **prof) as dst:         # pixels only: any coordinates the file had are not used
                    for _, win in src.block_windows(1):
                        dst.write(src.read(window=win), window=win)
            return rasters._keep(plain), w, h, n
        try:
            key, w, h, n = await anyio.to_thread.run_sync(keep)
        except (rasters.RasterError, rasterio.errors.RasterioError) as e:
            raise _problem(e) from None
    finally:
        shutil.rmtree(d, ignore_errors=True)
    stem = name.rsplit(".", 1)[0] or "Image"
    cur = await conn.execute("INSERT INTO georef_sources (project_id, org_id, name, key, width, height, band_count, created_by) "
                             "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *", (project_id, user["org_id"], stem[:200], key, w, h, n, user["id"]))
    return _out(await cur.fetchone())


class FromLayerIn(BaseModel):
    layer_id: UUID


@router.post("/projects/{project_id}/georeference/from-layer", status_code=201, summary="Re-place a raster layer that is in the wrong place")
async def from_layer(project_id: UUID, body: FromLayerIn, user=Depends(EDIT), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    lay = await raster_of(conn, body.layer_id, user)
    if str(lay["project_id"]) != str(project_id):
        raise Problem(422, f"{lay['name']} is in another project.", code="validation")
    m = lay["raster"]
    if m["width"] * m["height"] > settings.max_raster_cells:
        raise Problem(422, f"{lay['name']} is too large to georeference: clip or resample it first.", code="validation")
    # its current placement gives a starting point: none, the person adds points; its old system is noted
    cur = await conn.execute("INSERT INTO georef_sources (project_id, org_id, name, key, owns_key, layer_id, width, height, band_count, had_crs, created_by) "
                             "VALUES (%s, %s, %s, %s, false, %s, %s, %s, %s, %s, %s) RETURNING *",
                             (project_id, user["org_id"], lay["name"][:200], m["key"], lay["id"], m["width"], m["height"], m["count"], m.get("crs"), user["id"]))
    return _out(await cur.fetchone())


@router.get("/projects/{project_id}/georeference", summary="Georeferencing work in this project, latest first")
async def listing(project_id: UUID, user=Depends(VIEW), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute("SELECT * FROM georef_sources WHERE project_id = %s ORDER BY updated_at DESC LIMIT 50", (project_id,))
    return [_out(r) for r in await cur.fetchall()]


@router.get("/georeference/{gid}", summary="One piece of georeferencing work")
async def one(gid: UUID, user=Depends(VIEW), conn=Depends(get_conn)):
    return _out(await _source(conn, gid, user))


@router.get("/georeference/{gid}/image.png", summary="The image itself, for placing points on", response_class=Response)
async def image(gid: UUID, size: int = 2048, user=Depends(VIEW), conn=Depends(get_conn)):
    r = await _source(conn, gid, user)
    try:
        png, k = await anyio.to_thread.run_sync(lambda: georef.source_png(rasters.local(r["key"]), max(256, min(size, 4096))))
    except (rasters.RasterError, rasterio.errors.RasterioError) as e:
        raise _problem(e) from None
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600", "X-Scale": f"{k:.6f}"})


@router.put("/georeference/{gid}", summary="Save the control points and transformation (as they are added)")
async def save_work(gid: UUID, body: WorkIn, user=Depends(EDIT), conn=Depends(get_conn)):
    await _source(conn, gid, user)
    cur = await conn.execute("UPDATE georef_sources SET points = %s, method = %s, updated_at = now() WHERE id = %s RETURNING *",
                             (Jsonb([p.model_dump() for p in body.points]), body.method, gid))
    return _out(await cur.fetchone())


@router.post("/georeference/{gid}/fit", summary="Each point's residual and the RMS error; optionally a preview on the map")
async def fit(gid: UUID, body: FitIn, user=Depends(VIEW), conn=Depends(get_conn)):
    r = await _source(conn, gid, user)
    pts = [p.model_dump() for p in body.points]

    def work():
        on = georef.active(pts)
        crs = georef.target_crs(on, body.epsg)
        res = georef.residuals(on, body.method, crs, (r["width"], r["height"]))
        if body.preview:
            png, corners = georef.preview_png(rasters.local(r["key"]), on, body.method)
            res["preview"] = {"url": "data:image/png;base64," + base64.b64encode(png).decode(), "coordinates": corners}
        return res
    try:
        return await anyio.to_thread.run_sync(work)
    except (georef.GeorefError, rasters.RasterError) as e:
        raise _problem(e) from None


@router.post("/georeference/{gid}/save", status_code=201, summary="Warp the image with the points and add it as a raster layer")
async def save(gid: UUID, body: SaveIn, user=Depends(EDIT), conn=Depends(get_conn)):
    r = await _source(conn, gid, user)
    pts = [p.model_dump() for p in body.points]

    def work():
        on = georef.active(pts)
        crs = georef.target_crs(on, body.epsg)
        res = georef.residuals(on, body.method, crs, (r["width"], r["height"]))
        data, transform, _ = georef.warp(rasters.local(r["key"]), on, body.method, crs, cell=body.cell, resampling=body.resampling,
                                         most=settings.max_raster_cells)
        with rasterio.open(rasters.local(r["key"])) as src:
            dtype, names = src.dtypes[0], [d or f"Band {i + 1}" for i, d in enumerate(src.descriptions or [None] * src.count)]
        # bytes stay bytes (0 kept for no data); other whole numbers keep their type when sampled without blending
        if dtype == "uint8":
            keep_type, nodata = "uint8", 0
            data = np.where(np.isfinite(data), np.clip(np.round(data), 1, 255), np.nan)
        elif dtype in ("uint16", "int16", "uint32", "int32") and body.resampling == "nearest":
            keep_type, nodata = dtype, None
        else:
            keep_type, nodata = "float32", None
        meta = rasters.save(data, crs, transform, nodata=nodata, dtype=keep_type, names=names)
        return meta, res
    try:
        meta, res = await anyio.to_thread.run_sync(work)
    except (georef.GeorefError, rasters.RasterError) as e:
        raise _problem(e) from None
    name = ((body.name or "").strip() or f"{r['name']} (georeferenced)")[:200]
    source = {"tool": "georeference", "tool_name": "Georeference", "crs": meta["crs_name"],
              "summary": {"method": georef.METHODS[body.method][0], "points": len(res["points"]), "rmse_m": res["rmse_m"], "cell_m": meta["res_m"][0]}}
    style = rasters.default_style(meta)
    if meta["dtype"] == "uint8" and style.get("mode") == "rgb":         # a scan or photo keeps its own colours, not a stretch
        style = {**style, "mins": [0, 0, 0], "maxs": [255, 255, 255]}
    async with conn.transaction():
        lid = await _insert(conn, r["project_id"], user, name, meta, style, source)
        await conn.execute("UPDATE georef_sources SET points = %s, method = %s, updated_at = now() WHERE id = %s",
                           (Jsonb(pts), body.method, gid))
    await audit(conn, user, "layer.georeference", "layer", lid, name=name, method=body.method, points=len(res["points"]), rmse_m=res["rmse_m"])
    await _touch(conn, lid, r["project_id"])
    return {"layer": await layer_of(conn, lid, user), "fit": res}


@router.delete("/georeference/{gid}", status_code=204, summary="Discard georeferencing work (layers already saved stay)")
async def discard(gid: UUID, user=Depends(EDIT), conn=Depends(get_conn)):
    r = await _source(conn, gid, user)
    await conn.execute("DELETE FROM georef_sources WHERE id = %s", (gid,))
    if r["owns_key"]:
        rasters.remove(r["key"])
    return Response(status_code=204)
