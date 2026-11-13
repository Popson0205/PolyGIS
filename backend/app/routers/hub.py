"""Publishing a layer to Geohub's Data hub, as the person signed in: the layer's features (or its selection) become a
dataset there, or the next version of one published before. Geohub keeps the versions, the catalogue, previews and
downloads; PolyGIS only sends the data (with the person's GeoEstate ID access token)."""
import json
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


def _vector(layer: dict) -> str:
    kind = KIND.get(layer["geometry_type"])
    if not kind:
        raise Problem(422, "Only vector layers (points, lines, polygons) can go to the Data hub.", code="validation")
    return kind


async def _send(request: Request, path: str, body: dict):
    token = _token(request)
    try:
        return await anyio.to_thread.run_sync(lambda: identity.client().send(token, path, body))
    except GeoEstateIDError as e:
        status = e.status if e.status in (401, 403, 404, 409, 422) else 502
        raise Problem(status, f"Geohub: {e}", code=e.code or "geohub") from None
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
    kind = _vector(layer)
    p = await project_of(conn, layer["project_id"], user)
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
    kind = _vector(layer)
    feats = await _features(conn, layer, body.ids)
    out = await _send(request, f"datasets/{slug}/versions", {
        "geometry_type": kind, "fields": [{"name": f["name"], "type": f["type"]} for f in layer["fields"]], "features": feats})
    await audit(conn, user, "layer.publish", "layer", layer_id, name=layer["name"], dataset=slug, version=out.get("version"), features=len(feats))
    return {**out["dataset"], "url": _hub_url(slug), "version": out.get("version"), "feature_count": out.get("feature_count", len(feats))}
