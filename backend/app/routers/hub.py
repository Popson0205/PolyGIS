"""Publishing a layer to Geohub's Data hub, as the person signed in: the layer's features (or its selection) become a
dataset there, or the next version of one published before. Geohub keeps the versions, the catalogue, previews and
downloads; PolyGIS only sends the data (with the person's GeoEstate ID access token).

A raster layer goes as its GeoTIFF, uploaded in parts straight to Geohub's storage; Geohub then prepares it (a
Cloud-Optimised GeoTIFF with its statistics), and the dataset appears once that is done. It is drawn there with the
layer's colour ramp."""
import json
import re
from typing import Literal
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from .. import identity
from ..audit import audit
from ..config import settings
from ..db import get_conn
from ..errors import Problem
from ..geoestate_id import GeoEstateIDError
from ..identity import need
from .layers import layer_of
from .projects import project_of

router = APIRouter(tags=["data hub"])
KIND = {"Point": "point", "LineString": "line", "Polygon": "area"}
MAX = 50_000


class PublishIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=5000)
    license: Literal["ODbL-1.0", "CC-BY-4.0", "CC0-1.0", "Private"] = "CC-BY-4.0"
    tags: list[str] = Field([], max_length=20)
    visibility: Literal["private", "organization", "public"] = "private"
    ids: list[int] | None = Field(None, max_length=MAX, description="Only these features (the selection)")


class VersionIn(BaseModel):
    ids: list[int] | None = Field(None, max_length=MAX)


def _token(request: Request) -> str:
    t = getattr(request.state, "at", None) or request.cookies.get(identity.AT)
    if not t:
        raise Problem(401, "Sign in again to publish.", code="unauthenticated")
    return t


def _hub_url(slug: str) -> str:
    return f"{settings.geohub_url.rstrip('/')}/hub/{slug}"


async def _features(conn, layer: dict, ids: list[int] | None) -> list[dict]:
    q, vals = "SELECT ST_AsGeoJSON(geom, 7) AS g, props FROM features WHERE layer_id = %s", [layer["id"]]
    if ids:
        q += " AND id = ANY(%s)"; vals.append(ids)
    cur = await conn.execute(q + " ORDER BY id LIMIT %s", (*vals, MAX + 1))
    rows = await cur.fetchall()
    if not rows:
        raise Problem(422, "There is nothing to publish: the layer (or the selection) has no features.", code="empty")
    if len(rows) > MAX:
        raise Problem(422, f"The Data hub takes up to {MAX:,} features at a time: publish a selection, or split the layer.", code="too_many")
    return [{"geometry": json.loads(r["g"]), "properties": r["props"] or {}} for r in rows]


# PolyGIS's colour ramps, as Geohub (TiTiler) names them; an inverted ramp is the name with _r
RAMPS = {"Gray": "gray", "Terrain": "terrain", "Greens": "greens", "Blues": "blues", "Reds": "reds", "Viridis": "viridis",
         "Magma": "magma", "Red to green": "rdylgn", "Spectral": "spectral_r", "Blue to red": "rdbu_r"}


def _render(layer: dict) -> dict | None:
    """How Geohub should draw the raster: its classes (value, colour, name) when it is shown by class; else the layer's own
    ramp (its colours, and the nearest named ramp) and range; an RGB image is drawn as a photo there anyway."""
    st = layer.get("style") or {}
    if st.get("mode") == "classes":                       # land cover and the like: each shown class in its colour, with its name
        shown = [{"value": int(c["value"]), "color": c["color"].lower(), "label": str(c.get("label") or c["value"])[:100]}
                 for c in st.get("categories") or [] if not c.get("hidden") and float(c["value"]).is_integer()
                 and re.fullmatch(r"#[0-9a-fA-F]{6}", c.get("color") or "")]
        return {"classes": shown[:200]} if shown else None
    if st.get("mode", "ramp") != "ramp" or st.get("ramp") not in RAMPS:
        return None
    name = RAMPS[st["ramp"]]
    if st.get("invert"):
        name = name[:-2] if name.endswith("_r") else f"{name}_r"
    from ..raster_style import RAMPS as STOPS
    stops = list(STOPS[st["ramp"]])
    out: dict = {"colormap": name, "colors": stops[::-1] if st.get("invert") else stops}       # its exact colours; the name if not
    if st.get("min") is not None and st.get("max") is not None and st["max"] > st["min"]:
        out["rescale"] = [st["min"], st["max"]]
    return out


async def _upload(request: Request, layer: dict, body: dict) -> dict:
    """Sends the raster layer's GeoTIFF to Geohub (in parts, straight to its storage) and asks it to prepare it."""
    from .. import rasters
    token = _token(request)
    key = (layer.get("raster") or {}).get("key")
    if not key or not rasters.stored(key):
        raise Problem(409, "This raster's file is missing from storage: add the raster again before publishing it.", code="raster_missing")
    name = re.sub(r"[^\w .()-]+", "_", layer["name"]).strip()[:80] or "raster"
    try:
        path = await anyio.to_thread.run_sync(rasters.local, key)
        return await anyio.to_thread.run_sync(lambda: identity.client().upload(token, "rasters", {**body, "filename": f"{name}.tif"}, str(path)))
    except GeoEstateIDError as e:
        raise _refused(e, "rasters", (401, 403, 404, 409, 413, 422)) from None
    except Problem:
        raise
    except Exception:  # noqa: BLE001  (Geohub or its storage could not be reached)
        raise Problem(502, "Geohub could not be reached to publish the raster. Try again in a moment.", code="geohub_unreachable") from None


def _raster_out(out: dict) -> dict:
    """An upload's state as the dialog shows it: the dataset (once made), and whether Geohub is still preparing it."""
    d = out.get("dataset") or {}
    return {**d, "url": _hub_url(d["slug"]) if d.get("slug") else None, "upload": out["id"], "status": out["status"],
            "version": out.get("version"), "feature_count": 0, "error": out.get("error")}


def _refused(e: GeoEstateIDError, what: str, codes: tuple[int, ...]) -> Problem:
    """Geohub's refusal in its own words; or, when the address itself is missing (Geohub's own refusals always carry a
    code), that this Geohub has not been updated to take it yet."""
    if e.status in (404, 405) and e.code in ("error", None):
        return Problem(502, f"Geohub has not been updated to take {what} from PolyGIS yet. Once it is, publish again.", code="geohub_outdated")
    return Problem(e.status if e.status in codes else 502, f"Geohub: {e}", code=e.code or "geohub")


def _vector(layer: dict) -> str:
    kind = KIND.get(layer["geometry_type"])
    if not kind:
        raise Problem(422, "This layer cannot go to the Data hub.", code="validation")
    return kind


async def _send(request: Request, path: str, body: dict):
    token = _token(request)
    try:
        return await anyio.to_thread.run_sync(lambda: identity.client().send(token, path, body))
    except GeoEstateIDError as e:
        raise _refused(e, "layers", (401, 403, 404, 409, 422)) from None
    except Exception:  # noqa: BLE001  (Geohub could not be reached)
        raise Problem(502, "Geohub could not be reached to publish. Try again in a moment.", code="geohub_unreachable") from None


@router.get("/layers/{layer_id}/hub", summary="The Data hub datasets this person published from the layer")
async def published(layer_id: UUID, request: Request, user=Depends(need()), conn=Depends(get_conn)):
    await layer_of(conn, layer_id, user)
    token = _token(request)
    try:
        items = await anyio.to_thread.run_sync(lambda: identity.client().data(token, "datasets", {"ref": str(layer_id)}))
    except Exception:  # noqa: BLE001
        return []
    return [{**d, "url": _hub_url(d["slug"])} for d in items]


@router.post("/layers/{layer_id}/hub", status_code=201, summary="Publish the layer (or its selection) to the Data hub as a new dataset")
async def publish(layer_id: UUID, body: PublishIn, request: Request, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user)
    p = await project_of(conn, layer["project_id"], user)
    if layer["geometry_type"] == "Raster":
        out = _raster_out(await _upload(request, layer, {
            "title": body.title.strip(), "description": body.description.strip(), "license": body.license, "tags": body.tags,
            "visibility": body.visibility, "ref": str(layer_id), "label": f"{p['name']} › {layer['name']}", "render": _render(layer)}))
        await audit(conn, user, "layer.publish", "layer", layer_id, name=layer["name"], dataset=out.get("slug"), visibility=body.visibility, raster=True)
        return out
    kind = _vector(layer)
    feats = await _features(conn, layer, body.ids)
    out = await _send(request, "datasets", {
        "title": body.title.strip(), "description": body.description.strip(), "license": body.license, "tags": body.tags, "visibility": body.visibility,
        "ref": str(layer_id), "label": f"{p['name']} › {layer['name']}", "geometry_type": kind,
        "fields": [{"name": f["name"], "type": f["type"]} for f in layer["fields"]], "features": feats})
    d = out["dataset"]
    await audit(conn, user, "layer.publish", "layer", layer_id, name=layer["name"], dataset=d["slug"], visibility=body.visibility, features=len(feats))
    return {**d, "url": _hub_url(d["slug"]), "version": out.get("version", 1), "feature_count": out.get("feature_count", len(feats))}


@router.post("/layers/{layer_id}/hub/{slug}/versions", status_code=201, summary="Publish the layer again as the dataset's next version")
async def publish_version(layer_id: UUID, slug: str, body: VersionIn, request: Request, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user)
    if layer["geometry_type"] == "Raster":
        out = _raster_out(await _upload(request, layer, {"dataset": slug, "render": _render(layer)}))
        await audit(conn, user, "layer.publish", "layer", layer_id, name=layer["name"], dataset=slug, raster=True)
        return out
    kind = _vector(layer)
    feats = await _features(conn, layer, body.ids)
    out = await _send(request, f"datasets/{slug}/versions", {
        "geometry_type": kind, "fields": [{"name": f["name"], "type": f["type"]} for f in layer["fields"]], "features": feats})
    await audit(conn, user, "layer.publish", "layer", layer_id, name=layer["name"], dataset=slug, version=out.get("version"), features=len(feats))
    return {**out["dataset"], "url": _hub_url(slug), "version": out.get("version"), "feature_count": out.get("feature_count", len(feats))}


@router.get("/layers/{layer_id}/hub/uploads/{upload_id}", summary="How a raster's publishing is going: processing, ready or failed")
async def raster_status(layer_id: UUID, upload_id: UUID, request: Request, user=Depends(need()), conn=Depends(get_conn)):
    await layer_of(conn, layer_id, user)
    token = _token(request)
    try:
        out = await anyio.to_thread.run_sync(lambda: identity.client().data(token, f"rasters/{upload_id}"))
    except GeoEstateIDError as e:
        raise _refused(e, "rasters", (401, 403, 404)) from None
    except Exception:  # noqa: BLE001
        raise Problem(502, "Geohub could not be reached. Try again in a moment.", code="geohub_unreachable") from None
    return _raster_out(out)
