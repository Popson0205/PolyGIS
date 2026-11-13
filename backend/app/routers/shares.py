"""Share links: a read-only map of some of a project's layers that opens from a link, without signing in (or only for
people of the organization, if the link says so). Editors make, change and revoke links; a link can end on a date and
can let its viewers download the layers it shows.

  /projects/{id}/shares, /shares/{id}                                   managing links (signed in)
  /public/shares/{token}                                               what the link shows
  /public/shares/{token}/layers/{layer}/tiles/{z}/{x}/{y}.pbf           its vector tiles
  /public/shares/{token}/layers/{layer}/raster/{z}/{x}/{y}.png          its raster tiles
  /public/shares/{token}/layers/{layer}/features/{fid}                  one feature (to identify it)
  /public/shares/{token}/layers/{layer}/raster/value                    a raster's values at a point
  /public/shares/{token}/layers/{layer}/export                          a download, when the link allows it"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Query, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import rasters
from ..audit import audit
from ..db import get_conn
from ..errors import Problem
from ..identity import current_user, need
from .layers import LAYER_SQL, _shape, export_file, mvt
from .projects import _view_ok, project_of

router = APIRouter(tags=["share links"])
PUBLIC_FIELDS = ("id", "name", "geometry_type", "fields", "style", "labels", "position", "bbox", "raster", "version", "feature_count")


class ShareIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    layer_ids: list[UUID] = Field(min_length=1, max_length=100)
    view: dict = Field(default_factory=dict)
    audience: Literal["anyone", "organization"] = "anyone"
    downloads: bool = False
    expires_in_days: int | None = Field(None, ge=1, le=3650)


class SharePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    layer_ids: list[UUID] | None = Field(None, min_length=1, max_length=100)
    view: dict | None = None
    audience: Literal["anyone", "organization"] | None = None
    downloads: bool | None = None
    expires_in_days: int | None = Field(None, ge=0, le=3650, description="0 for no end date")


async def _layers_ok(conn, project_id, ids: list[UUID]) -> list[UUID]:
    cur = await conn.execute("SELECT id FROM layers WHERE project_id = %s AND id = ANY(%s)", (project_id, ids))
    have = {r["id"] for r in await cur.fetchall()}
    missing = [str(i) for i in ids if i not in have]
    if missing:
        raise Problem(422, "Some of those layers are not in this project.", code="validation", fields={"layer_ids": missing})
    return list(dict.fromkeys(ids))


async def _share(conn, share_id, user) -> dict:
    cur = await conn.execute("SELECT * FROM shares WHERE id = %s AND org_id = %s", (share_id, user["org_id"]))
    row = await cur.fetchone()
    if not row:
        raise Problem(404, "Share link not found.", code="not_found")
    return row


@router.get("/projects/{project_id}/shares", summary="The project's share links, newest first")
async def list_shares(project_id: UUID, user=Depends(need()), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute("SELECT * FROM shares WHERE project_id = %s ORDER BY created_at DESC", (project_id,))
    return await cur.fetchall()


@router.post("/projects/{project_id}/shares", status_code=201, summary="Make a share link")
async def create_share(project_id: UUID, body: ShareIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    p = await project_of(conn, project_id, user)
    ids = await _layers_ok(conn, project_id, body.layer_ids)
    expires = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    cur = await conn.execute(
        "INSERT INTO shares (project_id, org_id, token, title, description, layer_ids, view, audience, downloads, expires_at, "
        "created_by, created_by_name) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (project_id, user["org_id"], secrets.token_urlsafe(24), body.title.strip(), body.description.strip(), ids,
         Jsonb(_view_ok(body.view or p["view"] or {})), body.audience, body.downloads, expires, user["id"], user["name"]))
    row = await cur.fetchone()
    await audit(conn, user, "share.create", "share", row["id"], name=row["title"], project_id=str(project_id), audience=body.audience,
                layers=len(ids), downloads=body.downloads)
    return row


@router.patch("/shares/{share_id}", summary="Change a share link (its address stays the same)")
async def update_share(share_id: UUID, body: SharePatch, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    s = await _share(conn, share_id, user)
    if s["revoked_at"]:
        raise Problem(409, "That link was revoked: make a new one.", code="revoked")
    sets, vals = ["updated_at = now()"], []
    for k in ("title", "description"):
        if getattr(body, k) is not None:
            sets.append(f"{k} = %s"); vals.append(getattr(body, k).strip())
    if body.layer_ids is not None:
        sets.append("layer_ids = %s"); vals.append(await _layers_ok(conn, s["project_id"], body.layer_ids))
    if body.view is not None:
        sets.append("view = %s"); vals.append(Jsonb(_view_ok(body.view)))
    if body.audience is not None:
        sets.append("audience = %s"); vals.append(body.audience)
    if body.downloads is not None:
        sets.append("downloads = %s"); vals.append(body.downloads)
    if body.expires_in_days is not None:
        sets.append("expires_at = %s")
        vals.append(datetime.now(timezone.utc) + timedelta(days=body.expires_in_days) if body.expires_in_days else None)
    cur = await conn.execute(f"UPDATE shares SET {', '.join(sets)} WHERE id = %s RETURNING *", (*vals, share_id))
    return await cur.fetchone()


@router.delete("/shares/{share_id}", summary="Revoke a share link: it stops working at once")
async def revoke_share(share_id: UUID, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    s = await _share(conn, share_id, user)
    cur = await conn.execute("UPDATE shares SET revoked_at = COALESCE(revoked_at, now()), updated_at = now() WHERE id = %s RETURNING *", (share_id,))
    await audit(conn, user, "share.revoke", "share", share_id, name=s["title"])
    return await cur.fetchone()


# ---------- what a link shows ----------
async def _viewer(request: Request, response: Response) -> dict | None:
    """Whoever is signed in, if anyone."""
    try:
        return await current_user(request, response)
    except Problem:
        return None


async def _open(conn, token: str, request: Request, response: Response) -> dict:
    cur = await conn.execute("SELECT s.*, p.name AS project_name FROM shares s JOIN projects p ON p.id = s.project_id WHERE s.token = %s", (token,))
    s = await cur.fetchone()
    if not s or s["revoked_at"]:
        raise Problem(404, "This link does not work any more: whoever shared it has turned it off.", code="not_found")
    if s["expires_at"] and s["expires_at"] < datetime.now(timezone.utc):
        raise Problem(410, f"This link ended on {s['expires_at']:%d %B %Y}. Ask whoever shared it for a new one.", code="expired")
    if s["audience"] == "organization":
        u = await _viewer(request, response)
        if not u:
            raise Problem(401, "This map is shared with the people of an organization: sign in to see it.", code="unauthenticated")
        if str(u["org_id"]) != str(s["org_id"]):
            raise Problem(403, "This map is shared only with the people of the organization that made it.", code="forbidden")
    return s


async def _layer(conn, s: dict, layer_id: UUID) -> dict:
    if layer_id not in s["layer_ids"]:
        raise Problem(404, "Layer not found.", code="not_found")
    cur = await conn.execute(LAYER_SQL + "WHERE l.id = %s AND l.project_id = %s", (layer_id, s["project_id"]))
    r = await cur.fetchone()
    if not r:
        raise Problem(404, "Layer not found.", code="not_found")
    return _shape(r)


@router.get("/public/shares/{token}", summary="A shared map: its title, view and layers (no sign-in for links open to anyone)")
async def open_share(token: str, request: Request, response: Response, conn=Depends(get_conn)):
    s = await _open(conn, token, request, response)
    cur = await conn.execute(LAYER_SQL + "WHERE l.project_id = %s AND l.id = ANY(%s) ORDER BY l.position, l.created_at", (s["project_id"], s["layer_ids"]))
    layers = []
    for r in await cur.fetchall():
        r = _shape(r)
        if r.get("raster"):
            r["raster"] = {k: v for k, v in r["raster"].items() if k != "key"}     # where the file is kept is not the viewer's business
        layers.append({k: r.get(k) for k in PUBLIC_FIELDS})
    await conn.execute("UPDATE shares SET views = views + 1, last_viewed_at = now() WHERE id = %s", (s["id"],))
    response.headers["Cache-Control"] = "no-store"
    return {"title": s["title"], "description": s["description"], "project_name": s["project_name"], "view": s["view"], "layers": layers,
            "downloads": s["downloads"], "audience": s["audience"], "expires_at": s["expires_at"], "shared_by": s["created_by_name"]}


def _tile_ok(z: int, x: int, y: int):
    if not (0 <= z <= 24 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise Problem(404, "No such tile.", code="not_found")


@router.get("/public/shares/{token}/layers/{layer_id}/tiles/{z}/{x}/{y}.pbf", response_class=Response, summary="A shared layer's vector tile")
async def share_tile(token: str, layer_id: UUID, z: int, x: int, y: int, request: Request, response: Response, v: str | None = None, conn=Depends(get_conn)):
    _tile_ok(z, x, y)
    s = await _open(conn, token, request, response)
    await _layer(conn, s, layer_id)
    return Response(await mvt(conn, layer_id, z, x, y), media_type="application/vnd.mapbox-vector-tile",
                    headers={"Cache-Control": "private, max-age=3600" if v else "no-cache"})


@router.get("/public/shares/{token}/layers/{layer_id}/raster/{z}/{x}/{y}.png", response_class=Response, summary="A shared raster's map tile")
async def share_raster_tile(token: str, layer_id: UUID, z: int, x: int, y: int, request: Request, response: Response, v: str | None = None,
                            conn=Depends(get_conn)):
    _tile_ok(z, x, y)
    s = await _open(conn, token, request, response)
    lay = await _layer(conn, s, layer_id)
    if lay["geometry_type"] != "Raster" or not lay.get("raster"):
        raise Problem(404, "Not a raster.", code="not_found")
    from .rasters import _empty_png
    try:
        png = await anyio.to_thread.run_sync(lambda: rasters.render(lay["raster"], lay["style"], z, x, y))
    except rasters.RasterError as e:
        raise Problem(404, str(e), code="not_found") from None
    return Response(png or _empty_png(), media_type="image/png", headers={"Cache-Control": "private, max-age=3600" if v else "no-cache"})


@router.get("/public/shares/{token}/layers/{layer_id}/raster/value", summary="A shared raster's values at a point")
async def share_raster_value(token: str, layer_id: UUID, request: Request, response: Response, lng: float = Query(ge=-180, le=180),
                             lat: float = Query(ge=-90, le=90), conn=Depends(get_conn)):
    s = await _open(conn, token, request, response)
    lay = await _layer(conn, s, layer_id)
    if lay["geometry_type"] != "Raster" or not lay.get("raster"):
        raise Problem(404, "Not a raster.", code="not_found")
    vals = await anyio.to_thread.run_sync(lambda: rasters.values_at(lay["raster"], lng, lat))
    return {"values": vals, "bands": [b.get("name") for b in lay["raster"]["bands"]]}


@router.get("/public/shares/{token}/layers/{layer_id}/features/{fid}", summary="One feature of a shared layer")
async def share_feature(token: str, layer_id: UUID, fid: int, request: Request, response: Response, conn=Depends(get_conn)):
    s = await _open(conn, token, request, response)
    await _layer(conn, s, layer_id)
    cur = await conn.execute("SELECT id, ST_AsGeoJSON(geom, 7)::json AS geometry, props FROM features WHERE layer_id = %s AND id = %s", (layer_id, fid))
    r = await cur.fetchone()
    if not r:
        raise Problem(404, "Feature not found.", code="not_found")
    return {"type": "Feature", "id": r["id"], "geometry": r["geometry"], "properties": r["props"]}


@router.get("/public/shares/{token}/layers/{layer_id}/export", response_class=Response, summary="Download a shared layer, if the link allows it")
async def share_export(token: str, layer_id: UUID, request: Request, response: Response,
                       format: Literal["geojson", "gpkg", "shp", "csv", "kml"] = "geojson", conn=Depends(get_conn)):
    s = await _open(conn, token, request, response)
    if not s["downloads"]:
        raise Problem(403, "This link shows the map but does not allow downloads.", code="forbidden")
    lay = await _layer(conn, s, layer_id)
    if lay["geometry_type"] == "Raster":
        raise Problem(422, "Rasters can't be downloaded from a share link.", code="validation")
    data, filename, media, n = await export_file(conn, lay, format)
    await conn.execute("INSERT INTO audit_log (org_id, actor_id, actor_name, action, target_type, target_id, details) "
                       "VALUES (%s, NULL, 'Share link viewer', 'share.download', 'layer', %s, %s)",
                       (s["org_id"], str(layer_id), Jsonb({"share": s["title"], "format": format, "features": n})))
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
