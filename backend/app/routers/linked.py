"""Linked layers: data that lives in another GeoEstate app, shown in PolyGIS as a layer that follows it. Land
Registry's parcel fabric, FarmScan's farms, or the features of a Geohub form. The features are copied into the layer
(so it is styled, labelled, queried, analysed, printed and shared like any other) and refreshed from the app when
asked or when the project opens; they are changed in the app, not here.

PolyGIS reads as the person signed in: Geohub's data with their own access token; Land Registry and FarmScan with a
short token for that app from Geohub's token exchange, so each shows only what the person may see there."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

import anyio
import httpx
import shapely
from fastapi import APIRouter, Depends, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from .. import identity
from ..audit import audit
from ..config import settings
from ..db import get_conn
from ..errors import Problem
from ..geoestate_id import GeoEstateIDError
from ..identity import need
from .layers import _copy_features, _next_position, _touch, default_style, layer_of
from .projects import project_of

router = APIRouter(tags=["linked layers"])
TRANSPORT: httpx.AsyncBaseTransport | None = None        # the tests answer for Land Registry and FarmScan
KIND = {"point": "Point", "line": "LineString", "area": "Polygon"}
HUB_TYPES = {"number": "number", "integer": "integer", "decimal": "number", "date": "date", "boolean": "boolean"}

# what each app offers: its layer, the fields it brings and how to read them
LAND = {"app": "land", "app_name": "Land Registry", "kind": "parcels", "label": "Parcel fabric", "geometry_type": "Polygon",
        "fields": [("pin", "string"), ("title_no", "string"), ("title_kind", "string"), ("land_use", "string"), ("area_sqm", "number"),
                   ("locality", "string"), ("address", "string"), ("status", "string"), ("survey_plan_no", "string"), ("surveyor", "string"),
                   ("survey_date", "date")], "label_field": "pin", "colour_by": "land_use"}
FARM = {"app": "farm", "app_name": "FarmScan", "kind": "farms", "label": "Farms", "geometry_type": "Polygon",
        "fields": [("name", "string"), ("farmer", "string"), ("crop", "string"), ("area_ha", "number"), ("ndvi", "number"), ("ndvi_date", "date"),
                   ("source", "string"), ("added", "date")], "label_field": "name", "graduate_by": "ndvi"}


class LinkIn(BaseModel):
    app: Literal["land", "farm", "hub"]
    form_id: UUID | None = None
    status: Literal["approved", "all"] = "approved"


def _token(request: Request) -> str:
    t = getattr(request.state, "at", None) or request.cookies.get(identity.AT)
    if not t:
        raise Problem(401, "Sign in again.", code="unauthenticated")
    return t


async def _apps(token: str) -> dict[str, dict]:
    try:
        return {a["product"]: a for a in await anyio.to_thread.run_sync(lambda: identity.client().apps(token))}
    except Exception:  # noqa: BLE001
        return {}


def _api(app: str, apps: dict) -> str:
    if app == "land" and settings.land_api:
        return settings.land_api.rstrip("/")
    if app == "farm" and settings.farm_api:
        return settings.farm_api.rstrip("/")
    url = (apps.get(app) or {}).get("url")
    if not url:
        raise Problem(409, f"{'Land Registry' if app == 'land' else 'FarmScan'}'s address is not known: ask GeoEstate to set it in Geohub.", code="no_app_url")
    return url.rstrip("/") + ("/api" if app == "land" else "")


@router.get("/projects/{project_id}/linked/sources", summary="What can be linked: the apps the organization has and Geohub's forms")
async def sources(project_id: UUID, request: Request, user=Depends(need()), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    token = _token(request)
    apps = await _apps(token)
    out = []
    for spec in (LAND, FARM):
        a = apps.get(spec["app"])
        ok = bool(a) and a.get("status") not in ("suspended", "none", None)
        out.append({"app": spec["app"], "app_name": spec["app_name"], "label": spec["label"], "geometry_type": spec["geometry_type"],
                    "available": ok, "why": None if ok else f"{user['org_name'] or 'Your organization'} does not have {spec['app_name']}."})
    try:
        projects = await anyio.to_thread.run_sync(lambda: identity.client().data(token, "projects"))
    except Exception:  # noqa: BLE001
        projects = []
    for p in projects:
        for f in p.get("forms") or []:
            if f.get("geometry_type") in KIND:
                out.append({"app": "hub", "app_name": "Geohub", "form_id": f["id"], "label": f"{p['name']} › {f['name']}", "geometry_type": KIND[f["geometry_type"]],
                            "features": f.get("features"), "approved": f.get("approved"), "available": True, "why": None})
    return out


async def _read(request: Request, link: dict) -> tuple[list, list, list[dict]]:
    """The app's features now: shapes, properties, and the fields they have."""
    token = _token(request)
    g = identity.client()
    if link["app"] in ("land", "farm"):
        try:
            other = (await anyio.to_thread.run_sync(lambda: g.token_for(token, link["app"])))["access_token"]
        except GeoEstateIDError as e:
            raise Problem(403 if e.status == 403 else 502, f"{link['app_name']}: {e}", code=e.code or "no_product") from None
        base = _api(link["app"], await _apps(token))
        path = "/parcels.geojson" if link["app"] == "land" else "/api/boundaries.geojson"
        try:
            async with httpx.AsyncClient(timeout=120, transport=TRANSPORT) as c:
                r = await c.get(base + path, headers={"Authorization": f"Bearer {other}"})
        except httpx.HTTPError:
            raise Problem(502, f"{link['app_name']} could not be reached. Try again in a moment.", code="app_unreachable") from None
        if r.status_code >= 400:
            try:
                why = r.json().get("detail")
            except ValueError:
                why = None
            raise Problem(502 if r.status_code >= 500 else r.status_code, f"{link['app_name']}: {why or f'answered {r.status_code}'}", code="app_refused")
        feats = r.json().get("features") or []
        spec = LAND if link["app"] == "land" else FARM
        fields = [{"name": n, "type": t} for n, t in spec["fields"]]
        keep = [f["name"] for f in fields]
        props = [{k: (f.get("properties") or {}).get(k) for k in keep} for f in feats]
    else:
        feats, after = [], None
        params = {"limit": 1000, **({"status": "approved"} if link.get("status") == "approved" else {})}
        while True:
            try:
                page = await anyio.to_thread.run_sync(lambda: g.data(token, f"forms/{link['form_id']}/features", {**params, **({"after": after} if after else {})}))
            except GeoEstateIDError as e:
                raise Problem(404 if e.status == 404 else 502, f"Geohub: {e}", code=e.code or "geohub") from None
            feats += page.get("features") or []
            after = page.get("next")
            if not after or len(feats) > settings.linked_max:
                break
        fields = [{"name": f["id"], "type": HUB_TYPES.get(f.get("type"), "string")} for f in link.get("fields") or []]
        keep = [f["name"] for f in fields]
        fields += [{"name": "status", "type": "string"}, {"name": "collected_by", "type": "string"}]
        props = [{**{k: _flat((f.get("properties") or {}).get(k)) for k in keep}, "status": f.get("status"), "collected_by": f.get("created_by_name")}
                 for f in feats]
    if len(feats) > settings.linked_max:
        raise Problem(422, f"That is more than {settings.linked_max:,} features: too many to show as one layer.", code="too_many")
    geoms, kept = [], []
    for f, p in zip(feats, props):
        try:
            geoms.append(shapely.geometry.shape(f["geometry"])); kept.append(p)
        except Exception:  # noqa: BLE001  (a feature without a usable shape is left out)
            continue
    return geoms, kept, fields


def _flat(v):
    return ", ".join(map(str, v)) if isinstance(v, list) else v


async def _fill(conn, request: Request, layer_id, link: dict, actor) -> dict:
    geoms, props, _ = await _read(request, link)
    await conn.execute("DELETE FROM features WHERE layer_id = %s", (layer_id,))
    await _copy_features(conn, layer_id, geoms, props, actor)
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute("UPDATE layers SET source = jsonb_set(source, '{linked,refreshed_at}', to_jsonb(%s::text)), updated_at = clock_timestamp() "
                       "WHERE id = %s", (now, layer_id))
    return {"features": len(geoms), "refreshed_at": now}


USE_COLOURS = {"residential": "#f4c542", "commercial": "#e66d4f", "industrial": "#8c6bb1", "institutional": "#6b8de6", "agricultural": "#8cc56b",
               "recreational": "#4fb3a9", "mixed": "#f39c4a", "": "#b8bfcc"}


async def _first_style(conn, layer_id, app: str) -> dict:
    """How a new linked layer is first drawn: parcels coloured by land use, farms by their latest NDVI (red to green)."""
    base = {"color": "#2f6db5", "stroke": "#4b5563", "width": 0.8, "size": 6, "opacity": 0.7, "method": "manual", "ramp": "", "extrude": None}
    if app == "land":
        cur = await conn.execute("SELECT DISTINCT coalesce(props->>'land_use', '') AS v FROM features WHERE layer_id = %s ORDER BY 1", (layer_id,))
        vals = [r["v"] for r in await cur.fetchall()]
        cats = [{"value": v, "label": v.capitalize() or "(not given)", "color": USE_COLOURS.get(v.lower(), ["#2f6db5", "#e07b1f", "#2f8a3b", "#c0392b"][i % 4])}
                for i, v in enumerate(vals[:40])]
        return {**base, "type": "categorized", "field": "land_use", "categories": cats, "classes": []}
    edges, cols = [-1, 0.2, 0.4, 0.6, 0.8, 1], ["#d7191c", "#fdae61", "#ffffbf", "#a6d96a", "#1a9641"]
    return {**base, "type": "graduated", "field": "ndvi", "categories": [], "ramp": "Red to green",
            "classes": [{"min": edges[i], "max": edges[i + 1], "color": cols[i]} for i in range(5)]}


@router.post("/projects/{project_id}/layers/linked", status_code=201, summary="Add a layer that follows Land Registry, FarmScan or a Geohub form")
async def add_linked(project_id: UUID, body: LinkIn, request: Request, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    if body.app == "hub":
        if not body.form_id:
            raise Problem(422, "Choose the form.", code="validation", fields={"form_id": ["missing"]})
        src = next((s for s in await sources(project_id, request, user, conn) if s.get("form_id") == str(body.form_id)), None)
        if not src:
            raise Problem(404, "That form is not one you can open in Geohub.", code="not_found")
        projects = await anyio.to_thread.run_sync(lambda: identity.client().data(_token(request), "projects"))
        form = next(f for p in projects for f in p.get("forms") or [] if f["id"] == str(body.form_id))
        link = {"app": "hub", "app_name": "Geohub", "kind": "form", "form_id": str(body.form_id), "label": src["label"], "status": body.status,
                "fields": [{"id": f["id"], "label": f.get("label"), "type": f.get("type")} for f in form.get("fields") or []]}
        gtype, name = src["geometry_type"], src["label"].split(" › ")[-1]
        fields = [{"name": f["id"], "type": HUB_TYPES.get(f.get("type"), "string")} for f in form.get("fields") or []] + \
                 [{"name": "status", "type": "string"}, {"name": "collected_by", "type": "string"}]
        style = None
    else:
        spec = LAND if body.app == "land" else FARM
        link = {"app": spec["app"], "app_name": spec["app_name"], "kind": spec["kind"], "label": spec["label"]}
        gtype, name = spec["geometry_type"], spec["label"]
        fields = [{"name": n, "type": t} for n, t in spec["fields"]]
        style = None
    pos = await _next_position(conn, project_id)
    cur = await conn.execute(
        "INSERT INTO layers (project_id, org_id, name, geometry_type, fields, style, position, source, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (project_id, user["org_id"], name[:200], gtype, Jsonb(fields), Jsonb(style or default_style(gtype, pos)), pos, Jsonb({"linked": link}), user["id"]))
    lid = (await cur.fetchone())["id"]
    got = await _fill(conn, request, lid, link, user["id"])
    if body.app in ("land", "farm"):
        spec = LAND if body.app == "land" else FARM
        await conn.execute("UPDATE layers SET labels = %s, style = %s WHERE id = %s",
                           (Jsonb({"field": spec["label_field"], "size": 11, "color": "#1f2937", "halo": "#ffffff"}), Jsonb(await _first_style(conn, lid, body.app)), lid))
    await audit(conn, user, "layer.link", "layer", lid, name=name, app=link["app"], features=got["features"])
    await _touch(conn, lid, project_id)
    return await layer_of(conn, lid, user)


@router.post("/layers/{layer_id}/refresh", summary="Bring a linked layer up to date with its app")
async def refresh(layer_id: UUID, request: Request, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    layer = await layer_of(conn, layer_id, user, lock=True)
    link = (layer.get("source") or {}).get("linked")
    if not link:
        raise Problem(422, f"{layer['name']} is not linked to another app.", code="validation")
    got = await _fill(conn, request, layer_id, link, user["id"])
    await audit(conn, user, "layer.refresh", "layer", layer_id, name=layer["name"], app=link["app"], features=got["features"])
    return await layer_of(conn, layer_id, user)
