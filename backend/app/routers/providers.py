"""Imagery providers: the organization's own imagery services, ready for commercial ones (Maxar, Planet, Airbus,
UP42, Satellogic…) as soon as a client licenses one, beside the free imagery PolyGIS already uses.

* Tile services, an XYZ template ({z}/{x}/{y}) or a WMS endpoint, become basemaps and image layers.
* STAC catalogues are searched by area, dates and cloud cover; a scene's image is added for the area looked at.

The provider's key (in the query, a header or as a username and password) never leaves the server: tiles, thumbnails
and scenes are fetched through PolyGIS. A free catalogue (Earth Search, Sentinel-2) is built in."""
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlparse
from uuid import UUID

import anyio
import httpx
from fastapi import APIRouter, Depends, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from ..audit import audit
from ..db import get_conn, pool
from ..errors import Problem
from ..identity import RANK, need
from .projects import project_of

router = APIRouter(tags=["imagery providers"])
TRANSPORT: httpx.AsyncBaseTransport | None = None        # the tests answer for the providers
READ_SCENE = None                                        # the tests stand in for reading a scene's image
BUILTIN = {
    "builtin-earth-search": {"id": "builtin-earth-search", "name": "Earth Search · Sentinel-2 (free)", "kind": "stac",
                             "url": "https://earth-search.aws.element84.com/v1",
                             "config": {"collections": ["sentinel-2-l2a"], "asset": "visual", "cloud_property": "eo:cloud_cover",
                                        "attribution": "Contains modified Copernicus Sentinel data, via Earth Search (Element 84)",
                                        "hosts": ["sentinel-cogs.s3.us-west-2.amazonaws.com"]},
                             "auth": {}, "enabled": True, "builtin": True},
}
MAX_SCENE_CELLS = 40_000_000


def _public(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k != "auth"}
    a = p.get("auth") or {}
    out["auth_mode"] = a.get("mode") or "none"
    out["key_set"] = bool(a.get("value") or a.get("password"))
    out["builtin"] = bool(p.get("builtin"))
    if p["kind"] in ("xyz", "wms"):
        out["tiles"] = f"/imagery-providers/{p['id']}/tiles/{{z}}/{{x}}/{{y}}"
    return out


def _admin(user):
    if RANK[user["role"]] < RANK["admin"] or not user["can_write"]:
        raise Problem(403, "Only an organization admin can manage imagery providers.", code="forbidden")


async def provider_short(pid: str, user) -> dict:
    """The provider, read on a connection given back at once: the fetches that follow may take a while."""
    if pid in BUILTIN:
        return BUILTIN[pid]
    async with pool.connection() as conn:
        return await provider_of(conn, pid, user)


async def provider_of(conn, pid: str, user) -> dict:
    if pid in BUILTIN:
        return BUILTIN[pid]
    try:
        UUID(pid)
    except ValueError:
        raise Problem(404, "That imagery provider was not found.", code="not_found") from None
    cur = await conn.execute("SELECT * FROM imagery_providers WHERE id = %s AND org_id = %s", (pid, user["org_id"]))
    p = await cur.fetchone()
    if not p:
        raise Problem(404, "That imagery provider was not found.", code="not_found")
    return {**p, "id": str(p["id"])}


class AuthIn(BaseModel):
    mode: Literal["none", "query", "header", "basic"] = "none"
    name: str | None = Field(None, max_length=100, description="The query parameter or header that carries the key (e.g. api_key, Authorization)")
    value: str | None = Field(None, max_length=2000, description="The key (or 'Bearer …')")
    user: str | None = Field(None, max_length=500)
    password: str | None = Field(None, max_length=2000)


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["xyz", "wms", "stac"]
    url: str = Field(min_length=8, max_length=2000)
    config: dict = Field(default_factory=dict)
    auth: AuthIn = Field(default_factory=AuthIn)
    enabled: bool = True


class ProviderPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    url: str | None = Field(None, min_length=8, max_length=2000)
    config: dict | None = None
    auth: AuthIn | None = None
    enabled: bool | None = None


def _check(kind: str, url: str, cfg: dict) -> dict:
    u = urlparse(url)
    if u.scheme != "https" and not (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1")):
        raise Problem(422, "The address must start with https://.", code="validation", fields={"url": ["https"]})
    if kind == "xyz" and not all(t in url for t in ("{z}", "{x}")) or kind == "xyz" and "{y}" not in url and "{-y}" not in url:
        raise Problem(422, "A tile template has {z}, {x} and {y} in it.", code="validation", fields={"url": ["template"]})
    if kind == "wms" and not cfg.get("layers"):
        raise Problem(422, "Give the WMS layer names (config.layers).", code="validation", fields={"config.layers": ["missing"]})
    keep = {k: v for k, v in cfg.items() if k in ("attribution", "max_zoom", "min_zoom", "layers", "format", "collections", "asset",
                                                  "cloud_property", "hosts", "tile_size", "notes")}
    keep["max_zoom"] = max(1, min(24, int(keep.get("max_zoom") or 20)))
    if kind == "stac":
        keep["collections"] = [str(c) for c in (keep.get("collections") or [])][:20]
        keep["asset"] = str(keep.get("asset") or "visual")
    keep["hosts"] = [str(h).lower() for h in (keep.get("hosts") or [])][:20]
    return keep


def _auth(a: AuthIn | None, old: dict | None = None) -> dict:
    if a is None:
        return old or {}
    out = {"mode": a.mode, "name": a.name, "user": a.user}
    # a key left empty keeps the one already saved
    out["value"] = a.value if a.value else (old or {}).get("value")
    out["password"] = a.password if a.password else (old or {}).get("password")
    if a.mode == "none":
        return {"mode": "none"}
    if a.mode in ("query", "header") and not (out["name"] and out["value"]):
        raise Problem(422, "Give the parameter or header name and the key.", code="validation", fields={"auth": ["missing"]})
    if a.mode == "basic" and not out["user"]:
        raise Problem(422, "Give the username (for Planet: the API key).", code="validation", fields={"auth": ["missing"]})
    return out


@router.get("/imagery-providers", summary="The organization's imagery providers, and the built-in free ones")
async def listing(user=Depends(need()), conn=Depends(get_conn)):
    cur = await conn.execute("SELECT * FROM imagery_providers WHERE org_id = %s ORDER BY created_at", (user["org_id"],))
    own = [{**r, "id": str(r["id"])} for r in await cur.fetchall()]
    return [_public(p) for p in [*BUILTIN.values(), *own]]


@router.post("/imagery-providers", status_code=201, summary="Add an imagery provider (organization admins)")
async def create(body: ProviderIn, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    _admin(user)
    cfg = _check(body.kind, body.url, body.config)
    cur = await conn.execute("INSERT INTO imagery_providers (org_id, name, kind, url, config, auth, enabled, created_by) "
                             "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                             (user["org_id"], body.name.strip(), body.kind, body.url.strip(), Jsonb(cfg), Jsonb(_auth(body.auth)), body.enabled, user["id"]))
    p = await cur.fetchone()
    await audit(conn, user, "imagery_provider.create", "imagery_provider", p["id"], name=p["name"], kind=p["kind"])
    return _public({**p, "id": str(p["id"])})


@router.patch("/imagery-providers/{pid}", summary="Change an imagery provider (organization admins)")
async def update(pid: str, body: ProviderPatch, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    _admin(user)
    p = await provider_of(conn, pid, user)
    if p.get("builtin"):
        raise Problem(422, "The built-in providers cannot be changed.", code="builtin")
    url = body.url.strip() if body.url else p["url"]
    cfg = _check(p["kind"], url, {**p["config"], **(body.config or {})})
    cur = await conn.execute("UPDATE imagery_providers SET name = %s, url = %s, config = %s, auth = %s, enabled = %s, updated_at = now() "
                             "WHERE id = %s RETURNING *",
                             ((body.name or p["name"]).strip(), url, Jsonb(cfg), Jsonb(_auth(body.auth, p["auth"])),
                              p["enabled"] if body.enabled is None else body.enabled, pid))
    r = await cur.fetchone()
    return _public({**r, "id": str(r["id"])})


@router.delete("/imagery-providers/{pid}", status_code=204, summary="Remove an imagery provider (organization admins)")
async def delete(pid: str, user=Depends(need("editor", write=True)), conn=Depends(get_conn)):
    _admin(user)
    p = await provider_of(conn, pid, user)
    if p.get("builtin"):
        raise Problem(422, "The built-in providers cannot be removed.", code="builtin")
    await conn.execute("DELETE FROM imagery_providers WHERE id = %s", (pid,))
    await audit(conn, user, "imagery_provider.delete", "imagery_provider", pid, name=p["name"])
    return Response(status_code=204)


# ---------- fetching through PolyGIS, with the key added here ----------
def _with_key(p: dict, url: str) -> tuple[str, dict, tuple | None]:
    a = p.get("auth") or {}
    headers, auth = {"User-Agent": "PolyGIS imagery"}, None
    if a.get("mode") == "query":
        url += ("&" if "?" in url else "?") + urlencode({a["name"]: a["value"]})
    elif a.get("mode") == "header":
        headers[a["name"]] = a["value"]
    elif a.get("mode") == "basic":
        auth = (a.get("user") or "", a.get("password") or "")
    return url, headers, auth


def _allowed(p: dict, href: str) -> bool:
    """Only the provider's own hosts (and those it lists) are fetched for it: never somewhere a scene points at at random."""
    u = urlparse(href)
    if u.scheme != "https":
        return False
    hosts = {urlparse(p["url"]).hostname, *(p["config"].get("hosts") or [])}
    return (u.hostname or "").lower() in {h.lower() for h in hosts if h}


async def _get(p: dict, url: str, **kw) -> httpx.Response:
    full, headers, auth = _with_key(p, url)
    try:
        async with httpx.AsyncClient(timeout=30, transport=TRANSPORT, follow_redirects=True) as c:
            return await c.get(full, headers=headers, auth=auth, **kw)
    except httpx.HTTPError as e:
        raise Problem(502, f"{p['name']} could not be reached ({e.__class__.__name__}).", code="provider") from None


def _wms_url(p: dict, z: int, x: int, y: int) -> str:
    n = 2 ** z
    R = 20037508.342789244
    size = 2 * R / n
    minx, maxy = -R + x * size, R - y * size
    q = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0", "LAYERS": p["config"]["layers"], "STYLES": "", "CRS": "EPSG:3857",
         "BBOX": f"{minx},{maxy - size},{minx + size},{maxy}", "WIDTH": 256, "HEIGHT": 256, "FORMAT": p["config"].get("format") or "image/png",
         "TRANSPARENT": "TRUE"}
    return p["url"] + ("&" if "?" in p["url"] else "?") + urlencode(q)


@router.get("/imagery-providers/{pid}/tiles/{z}/{x}/{y}", summary="A tile of a tile service, fetched with the organization's key")
async def tile(pid: str, z: int, x: int, y: int, user=Depends(need())):
    p = await provider_short(pid, user)
    if p["kind"] not in ("xyz", "wms") or not p.get("enabled", True):
        raise Problem(404, "That provider has no tiles.", code="not_found")
    if not (0 <= z <= 24 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise Problem(404, "No such tile.", code="not_found")
    if p["kind"] == "wms":
        url = _wms_url(p, z, x, y)
    else:
        url = (p["url"].replace("{z}", str(z)).replace("{x}", str(x)).replace("{-y}", str(2 ** z - 1 - y)).replace("{y}", str(y))
               .replace("{s}", "abc"[(x + y) % 3]))
    r = await _get(p, url)
    if r.status_code == 404:
        return Response(status_code=204)
    if r.status_code >= 400:
        raise Problem(502, f"{p['name']} answered {r.status_code} for a tile: check the address and the key.", code="provider")
    return Response(r.content, media_type=r.headers.get("content-type", "image/png"), headers={"Cache-Control": "private, max-age=86400"})


class SearchIn(BaseModel):
    bbox: tuple[float, float, float, float]
    start: str = Field(description="YYYY-MM-DD")
    end: str
    max_cloud: float = Field(30, ge=0, le=100)
    limit: int = Field(30, ge=1, le=100)


@router.post("/imagery-providers/{pid}/search", summary="Scenes of a STAC catalogue over an area, between dates, under a cloud cover")
async def search(pid: str, body: SearchIn, user=Depends(need())):
    p = await provider_short(pid, user)
    if p["kind"] != "stac":
        raise Problem(422, "Only catalogues (STAC) can be searched.", code="validation")
    cfg = p["config"]
    cloud = cfg.get("cloud_property") or "eo:cloud_cover"
    q = {"bbox": list(body.bbox), "datetime": f"{body.start}T00:00:00Z/{body.end}T23:59:59Z", "limit": body.limit,
         "sortby": [{"field": "properties.datetime", "direction": "desc"}]}
    if cfg.get("collections"):
        q["collections"] = cfg["collections"]
    if body.max_cloud < 100:
        q["query"] = {cloud: {"lte": body.max_cloud}}
    full, headers, auth = _with_key(p, p["url"].rstrip("/") + "/search")
    try:
        async with httpx.AsyncClient(timeout=45, transport=TRANSPORT) as c:
            r = await c.post(full, json=q, headers=headers, auth=auth)
            if r.status_code == 400 and "sortby" in r.text:              # not every catalogue sorts
                q.pop("sortby", None)
                r = await c.post(full, json=q, headers=headers, auth=auth)
    except httpx.HTTPError as e:
        raise Problem(502, f"{p['name']} could not be reached ({e.__class__.__name__}).", code="provider") from None
    if r.status_code >= 400:
        raise Problem(502, f"{p['name']} answered {r.status_code}: check the address, the collections and the key.", code="provider")
    out = []
    for it in r.json().get("features") or []:
        props, assets = it.get("properties") or {}, it.get("assets") or {}
        thumb = next((assets[k]["href"] for k in ("thumbnail", "preview", "overview", "rendered_preview") if k in assets and assets[k].get("href")), None)
        out.append({"id": it.get("id"), "collection": it.get("collection"), "datetime": props.get("datetime"), "cloud": props.get(cloud),
                    "gsd": props.get("gsd"), "platform": props.get("platform") or props.get("constellation"), "geometry": it.get("geometry"),
                    "bbox": it.get("bbox"), "thumbnail": thumb, "has_image": (cfg.get("asset") or "visual") in assets,
                    "self": next((ln["href"] for ln in it.get("links") or [] if ln.get("rel") == "self"), None)})
    return {"scenes": out}


@router.get("/imagery-providers/{pid}/thumbnail", summary="A scene's thumbnail, fetched with the organization's key")
async def thumbnail(pid: str, href: str, user=Depends(need())):
    p = await provider_short(pid, user)
    if not _allowed(p, href):
        raise Problem(422, "That picture is not from this provider.", code="validation")
    r = await _get(p, href)
    if r.status_code >= 400:
        raise Problem(502, f"{p['name']} answered {r.status_code}.", code="provider")
    return Response(r.content, media_type=r.headers.get("content-type", "image/jpeg"), headers={"Cache-Control": "private, max-age=86400"})


class AddSceneIn(BaseModel):
    collection: str | None = None
    scene_id: str = Field(min_length=1, max_length=300)
    bbox: tuple[float, float, float, float] = Field(description="The area to add (the map view)")
    name: str | None = Field(None, max_length=200)


def _read_window(href: str, bbox, headers: dict, auth, out_path: Path) -> None:
    """The part of a scene's image over the area, as a GeoTIFF (read over HTTP: only the needed bytes)."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    env = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MAX_RETRY": "3"}
    hdr = "\r\n".join(f"{k}: {v}" for k, v in headers.items() if k.lower() != "user-agent")
    if hdr:
        env["GDAL_HTTP_HEADERS"] = hdr
    if auth:
        env["GDAL_HTTP_USERPWD"] = f"{auth[0]}:{auth[1]}"
    with rasterio.Env(**env), rasterio.open(href) as src:
        b = transform_bounds("EPSG:4326", src.crs, *bbox)
        win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
        win = win.intersection(rasterio.windows.Window(0, 0, src.width, src.height))
        if win.width * win.height * src.count > MAX_SCENE_CELLS:
            raise Problem(422, "That area is too large to add at full resolution: zoom in.", code="too_large")
        data = src.read(window=win)
        prof = src.profile.copy()
        prof.update(driver="GTiff", width=int(win.width), height=int(win.height), transform=src.window_transform(win), compress="deflate",
                    tiled=True, blockxsize=256, blockysize=256)
        with rasterio.open(out_path, "w", **prof) as dst:
            dst.write(data)


@router.post("/projects/{project_id}/imagery-providers/{pid}/add", status_code=201, summary="Add a scene's image, for the area looked at, as a layer")
async def add_scene(project_id: UUID, pid: str, body: AddSceneIn, user=Depends(need("editor", write=True, feature="raster"))):
    async with pool.connection() as conn:
        await project_of(conn, project_id, user)
    p = await provider_short(pid, user)
    if p["kind"] != "stac":
        raise Problem(422, "Scenes come from catalogues (STAC).", code="validation")
    w, s, e, n = body.bbox
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90) or (e - w) * (n - s) > 4:
        raise Problem(422, "Zoom in: a scene is added for at most about 2° × 2°.", code="validation")
    coll = body.collection or (p["config"].get("collections") or [None])[0]
    if not coll:
        raise Problem(422, "Which collection is the scene in?", code="validation")
    r = await _get(p, f"{p['url'].rstrip('/')}/collections/{coll}/items/{body.scene_id}")
    if r.status_code >= 400:
        raise Problem(404 if r.status_code == 404 else 502, f"{p['name']} has no scene {body.scene_id} ({r.status_code}).", code="provider")
    item = r.json()
    asset = (item.get("assets") or {}).get(p["config"].get("asset") or "visual")
    if not asset or not asset.get("href"):
        raise Problem(422, f"That scene has no '{p['config'].get('asset') or 'visual'}' image.", code="validation")
    href = asset["href"]
    if not (_allowed(p, href) or p.get("builtin")):
        raise Problem(422, "That scene's image is on a host this provider does not list (config.hosts).", code="validation")
    _, headers, auth = _with_key(p, href)
    from . import rasters as rasters_router
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / f"{(body.name or body.scene_id)[:80].replace('/', '_')}.tif"
        reader = READ_SCENE or _read_window
        try:
            await anyio.to_thread.run_sync(lambda: reader(href, body.bbox, headers, auth, out))
        except Problem:
            raise
        except Exception as ex:  # noqa: BLE001
            raise Problem(502, f"The scene's image could not be read: {ex}", code="provider") from None
        dt = (item.get("properties") or {}).get("datetime", "")[:10]
        name = (body.name or f"{p['name'].split(' (')[0]} {dt} {body.scene_id}")[:200]
        from .layers import layer_of
        async with pool.connection() as conn:
            lay = await rasters_router.import_raster(conn, project_id, out, out.name, None, user)
            await conn.execute("UPDATE layers SET name = %s, source = source || %s WHERE id = %s",
                               (name, Jsonb({"provider": p["name"], "scene": body.scene_id, "collection": coll, "datetime": dt,
                                             "attribution": p["config"].get("attribution")}), lay["id"]))
            await audit(conn, user, "imagery_provider.add_scene", "layer", lay["id"], provider=p["name"], scene=body.scene_id)
            return await layer_of(conn, lay["id"], user)
