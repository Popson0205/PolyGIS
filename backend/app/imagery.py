"""Satellite imagery from open sources, so nobody has to download scenes by hand.

Three providers, one result: a raster layer (a Cloud-Optimized GeoTIFF with red, green, blue and near-infrared bands, in
the UTM zone of the area) that the raster tools (NDVI and the rest) work on like any uploaded image.

* ``planetary-computer`` and ``earth-search``: public STAC catalogues (Microsoft, Element 84 on AWS). No account or key.
  The scenes are read straight from their Cloud-Optimized GeoTIFFs, only the part inside the area, and joined (least
  cloudy first) where one scene does not cover it.
* ``gee``: Google Earth Engine, when the server has an Earth Engine project (``GEE_PROJECT``, and normally a service
  account: ``GEE_SERVICE_ACCOUNT`` with ``GEE_PRIVATE_KEY``). Clouds are masked and the scenes are combined into a
  median composite, which Earth Engine clips and sends back as a GeoTIFF.

Values stay as the providers store them (surface reflectance times 10 000 for Sentinel-2; Landsat Collection 2 scaled
integers), as uint16 with 0 for no data.
"""
import datetime as dt
import logging
import math
import shutil
import threading
from pathlib import Path

import httpx
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

from . import rasters
from .config import settings

log = logging.getLogger("imagery")
BANDS = ["Red", "Green", "Blue", "NIR"]            # always this order: bands 1-3 draw as true colour
MAX_COARSEN = 8                                    # a request needing cells more than this many times the satellite's own is refused
MAX_SCENES = 8                                     # scenes joined when PolyGIS picks them itself
MAX_DAYS = 3 * 366
UA = {"User-Agent": "PolyGIS (imagery lookup)"}


class ImageryError(Exception):
    """The imagery cannot be had, in words for the person asking. `upstream` is True when the provider is at fault."""

    def __init__(self, message: str, upstream: bool = False):
        super().__init__(message)
        self.upstream = upstream


STAC = {
    "planetary-computer": {"name": "Microsoft Planetary Computer", "url": "https://planetarycomputer.microsoft.com/api/stac/v1", "sign": True},
    "earth-search": {"name": "Earth Search (Element 84, AWS)", "url": "https://earth-search.aws.element84.com/v1", "sign": False},
}
SATELLITES = {
    "sentinel2": {
        "name": "Sentinel-2 (10 m)", "res": 10,
        "stac": {"planetary-computer": ("sentinel-2-l2a", ["B04", "B03", "B02", "B08"]),
                 "earth-search": ("sentinel-2-l2a", ["red", "green", "blue", "nir"])},
        "gee": [("COPERNICUS/S2_SR_HARMONIZED", ["B4", "B3", "B2", "B8"], "CLOUDY_PIXEL_PERCENTAGE")],
    },
    "landsat": {
        "name": "Landsat 8 and 9 (30 m)", "res": 30,
        "stac": {"planetary-computer": ("landsat-c2-l2", ["red", "green", "blue", "nir08"])},
        "gee": [("LANDSAT/LC08/C02/T1_L2", ["SR_B4", "SR_B3", "SR_B2", "SR_B5"], "CLOUD_COVER"),
                ("LANDSAT/LC09/C02/T1_L2", ["SR_B4", "SR_B3", "SR_B2", "SR_B5"], "CLOUD_COVER")],
    },
}


def gee_ready() -> bool:
    return bool(settings.gee_project.strip())


def providers() -> list[dict]:
    """What the server can offer, for the dialog: each provider, whether it is usable, and the satellites it has."""
    out = [{"id": k, "name": v["name"], "ready": True, "note": "No account needed.",
            "satellites": [s for s, c in SATELLITES.items() if k in c["stac"]]} for k, v in STAC.items()]
    out.append({"id": "gee", "name": "Google Earth Engine", "ready": gee_ready(), "satellites": list(SATELLITES),
                "note": "Cloud-masked median composite." if gee_ready() else "Not set up on this server (the administrator sets GEE_PROJECT)."})
    return [{**p, "satellites": [{"id": s, "name": SATELLITES[s]["name"]} for s in p["satellites"]]} for p in out]


# =====================================================================================================================
# The request
# =====================================================================================================================
def check(provider: str, satellite: str, bbox, start: dt.date, end: dt.date) -> tuple[float, float, float, float]:
    """The request checked; returns the area as (west, south, east, north)."""
    if satellite not in SATELLITES:
        raise ImageryError("Choose Sentinel-2 or Landsat.")
    if provider == "gee":
        if not gee_ready():
            raise ImageryError("Google Earth Engine is not set up on this server. Choose another source, or ask the administrator to set GEE_PROJECT.")
    elif provider not in STAC:
        raise ImageryError("That imagery source is not known.")
    elif provider not in SATELLITES[satellite]["stac"]:
        raise ImageryError(f"{STAC[provider]['name']} does not have {SATELLITES[satellite]['name']}: choose another source or satellite.")
    try:
        w, s, e, n = (float(x) for x in bbox)
    except (TypeError, ValueError):
        raise ImageryError("The area is not valid.") from None
    if not all(math.isfinite(x) for x in (w, s, e, n)) or not (-180 <= w < e <= 180 and -85 <= s < n <= 85):
        raise ImageryError("The area is not valid: zoom the map to the place you want, within longitude -180 to 180 and latitude -85 to 85.")
    if end < start:
        raise ImageryError("The end date is before the start date.")
    if (end - start).days > MAX_DAYS:
        raise ImageryError("Choose a period of three years or less.")
    return w, s, e, n


def plan(bbox, res: float, cap: int) -> dict:
    """The output grid: the UTM zone of the area, and cells of `res` metres, made larger (in whole multiples) when the area would
    hold more than `cap` cells."""
    w, s, e, n = bbox
    lon, lat = (w + e) / 2, (s + n) / 2
    epsg = (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1
    crs = CRS.from_epsg(epsg)
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, w, s, e, n, densify_pts=21)
    cells = ((right - left) / res) * ((top - bottom) / res)
    k = max(1, math.ceil(math.sqrt(cells / cap)))
    if k > MAX_COARSEN:
        raise ImageryError(f"The area is too large ({(right - left) / 1000:.0f} by {(top - bottom) / 1000:.0f} km). Zoom the map in, or draw a smaller area.")
    cell = res * k
    width, height = max(1, math.ceil((right - left) / cell)), max(1, math.ceil((top - bottom) / cell))
    return {"epsg": epsg, "crs": crs, "cell": cell, "coarsened": k > 1, "width": width, "height": height,
            "transform": from_origin(left, top, cell, cell)}


# =====================================================================================================================
# STAC catalogues (Planetary Computer, Earth Search)
# =====================================================================================================================
def _stac_post(provider: str, body: dict) -> list[dict]:
    try:
        r = httpx.post(STAC[provider]["url"] + "/search", json=body, timeout=40, headers=UA)
        r.raise_for_status()
        return r.json().get("features", [])
    except httpx.HTTPStatusError as e:
        raise ImageryError(f"{STAC[provider]['name']} refused the search ({e.response.status_code}). Try again, or use another source.", upstream=True) from None
    except (httpx.HTTPError, ValueError):
        raise ImageryError(f"Could not reach {STAC[provider]['name']}. Try again in a moment, or use another source.", upstream=True) from None


def _scene(it: dict) -> dict:
    p = it.get("properties", {})
    return {"id": it["id"], "datetime": p.get("datetime"), "cloud": p.get("eo:cloud_cover"),
            "platform": p.get("platform") or p.get("constellation")}


def stac_search(provider: str, satellite: str, bbox, start: dt.date, end: dt.date, max_cloud: int, limit: int = 30) -> list[dict]:
    coll = SATELLITES[satellite]["stac"][provider][0]
    q: dict = {"eo:cloud_cover": {"lte": max_cloud}}
    if satellite == "landsat":
        q["platform"] = {"in": ["landsat-8", "landsat-9"]}
    items = _stac_post(provider, {"collections": [coll], "bbox": list(bbox), "limit": limit, "query": q,
                                  "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
                                  "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}]})
    return [_scene(i) for i in items]


def _stac_items(provider: str, satellite: str, bbox, start, end, max_cloud: int, scene_ids: list[str]) -> list[dict]:
    coll = SATELLITES[satellite]["stac"][provider][0]
    if scene_ids:
        got = {i["id"]: i for i in _stac_post(provider, {"collections": [coll], "ids": scene_ids, "limit": len(scene_ids)})}
        missing = [i for i in scene_ids if i not in got]
        if missing:
            raise ImageryError(f"{STAC[provider]['name']} has no scene {missing[0]}. Search again and choose from the list.")
        items = [got[i] for i in scene_ids]
    else:
        q: dict = {"eo:cloud_cover": {"lte": max_cloud}}
        if satellite == "landsat":
            q["platform"] = {"in": ["landsat-8", "landsat-9"]}
        items = _stac_post(provider, {"collections": [coll], "bbox": list(bbox), "limit": MAX_SCENES, "query": q,
                                      "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
                                      "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}]})
    if not items:
        raise ImageryError("No scenes found for that area and period. Try a longer period or allow more cloud.")
    return items


def _signed(provider: str, coll: str, tokens: dict, href: str) -> str:
    """Planetary Computer's files need a short-lived read token (free, no account); Earth Search's are public."""
    if not STAC[provider]["sign"]:
        return href
    if coll not in tokens:
        try:
            r = httpx.get(f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{coll}", timeout=30, headers=UA)
            r.raise_for_status()
            tokens[coll] = r.json()["token"]
        except (httpx.HTTPError, KeyError, ValueError):
            raise ImageryError("Could not get read access to Microsoft Planetary Computer. Try again, or use another source.", upstream=True) from None
    return href + ("&" if "?" in href else "?") + tokens[coll]


def stac_mosaic(provider: str, satellite: str, items: list[dict], grid: dict) -> tuple[np.ndarray, list[dict]]:
    """The scenes read onto the grid, least cloudy first, each filling only what the earlier ones left empty."""
    coll, assets = SATELLITES[satellite]["stac"][provider]
    w, h = grid["width"], grid["height"]
    out = np.zeros((len(assets), h, w), "uint16")
    filled = np.zeros((h, w), bool)
    tokens: dict = {}
    used: list[dict] = []
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff", GDAL_HTTP_MAX_RETRY=3,
               GDAL_HTTP_RETRY_DELAY=1, GDAL_HTTP_TIMEOUT=60, GDAL_CACHEMAX=128)
    items = sorted(items, key=lambda i: i.get("properties", {}).get("eo:cloud_cover", 100))
    for it in items:
        if filled.all():
            break
        try:
            hrefs = [_signed(provider, coll, tokens, it["assets"][a]["href"]) for a in assets]
        except KeyError:
            continue                                   # a scene without all four bands is left out
        bands = []
        try:
            with rasterio.Env(**env):
                for href in hrefs:
                    with rasterio.open(href) as src, WarpedVRT(src, crs=grid["crs"], transform=grid["transform"], width=w, height=h,
                                                                 resampling=Resampling.bilinear, src_nodata=0, nodata=0) as vrt:
                        bands.append(vrt.read(1))
        except rasterio.errors.RasterioError as e:
            log.warning("scene %s could not be read: %s", it.get("id"), e)
            continue
        a = np.stack(bands).astype("uint16")
        fresh = (a > 0).all(axis=0) & ~filled
        if fresh.any():
            out[:, fresh] = a[:, fresh]
            filled |= fresh
            used.append(_scene(it))
    if not used:
        raise ImageryError("The scenes found hold no usable pixels over that area (they may be mostly cloud or edge). Try another period.")
    return out, used


# =====================================================================================================================
# Google Earth Engine
# =====================================================================================================================
_ee = None
_ee_lock = threading.Lock()


def _earth_engine():
    global _ee
    with _ee_lock:
        if _ee is not None:
            return _ee
        try:
            import ee
        except ImportError:
            raise ImageryError("Earth Engine support is not installed on this server (the earthengine-api package).") from None
        try:
            key = settings.gee_private_key.strip()
            if settings.gee_service_account and key:
                cred = ee.ServiceAccountCredentials(settings.gee_service_account, key_data=key) if key.startswith("{") \
                    else ee.ServiceAccountCredentials(settings.gee_service_account, key_file=key)
                ee.Initialize(cred, project=settings.gee_project)
            else:
                ee.Initialize(project=settings.gee_project)      # the credentials the server's environment already holds
        except Exception as e:  # noqa: BLE001
            log.warning("Earth Engine did not start: %s", e)
            raise ImageryError("Earth Engine did not accept the server's credentials. The administrator should check GEE_PROJECT, GEE_SERVICE_ACCOUNT and GEE_PRIVATE_KEY.", upstream=True) from None
        _ee = ee
        return ee


def _gee_collection(ee, satellite: str, bbox, start: dt.date, end: dt.date, max_cloud: int):
    """The scenes in the area and period with their clouds masked, bands renamed to Red, Green, Blue, NIR, `cloud` set on each."""
    region = ee.Geometry.Rectangle(list(bbox))
    last = (end + dt.timedelta(days=1)).isoformat()                 # filterDate's end is exclusive

    def prepare(bands, cloud_prop, sentinel):
        def f(img):
            if sentinel:
                scl = img.select("SCL")
                good = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))   # shadow, cloud (medium, high), cirrus, snow
            else:
                good = img.select("QA_PIXEL").bitwiseAnd(0b11010).eq(0)                               # dilated cloud, cloud, cloud shadow
            return img.updateMask(good).select(bands, BANDS).set("cloud", img.get(cloud_prop))
        return f

    merged = None
    for cid, bands, cloud_prop in SATELLITES[satellite]["gee"]:
        c = ee.ImageCollection(cid).filterBounds(region).filterDate(start.isoformat(), last).filter(ee.Filter.lte(cloud_prop, max_cloud))
        c = c.map(prepare(bands, cloud_prop, satellite == "sentinel2"))
        merged = c if merged is None else merged.merge(c)
    return merged, region


def gee_search(satellite: str, bbox, start: dt.date, end: dt.date, max_cloud: int, limit: int = 30) -> list[dict]:
    ee = _earth_engine()
    try:
        col, _ = _gee_collection(ee, satellite, bbox, start, end, max_cloud)
        feats = col.sort("cloud").limit(limit).getInfo()["features"]
    except Exception as e:  # noqa: BLE001
        raise ImageryError(f"Earth Engine could not search: {str(e)[:200]}", upstream=True) from None
    out = []
    for f in feats:
        p = f.get("properties", {})
        t = p.get("system:time_start")
        out.append({"id": f["id"], "datetime": dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).isoformat() if t else None,
                    "cloud": p.get("cloud"), "platform": None})
    return out


def gee_download(satellite: str, bbox, start, end, max_cloud: int, scene_ids: list[str], grid: dict, dest: Path) -> list[dict]:
    """The composite (or the chosen scenes joined) clipped to the area and written to `dest` as a GeoTIFF."""
    ee = _earth_engine()
    try:
        col, region = _gee_collection(ee, satellite, bbox, start, end, 100 if scene_ids else max_cloud)
        if scene_ids:
            col = col.filter(ee.Filter.inList("system:index", [i.rsplit("/", 1)[-1] for i in scene_ids]))
            image = col.sort("cloud", False).mosaic()                # the least cloudy scene on top
        else:
            image = col.median()
        n = col.size().getInfo()
        if not n:
            raise ImageryError("No scenes found for that area and period. Try a longer period or allow more cloud.")
        used = [{"id": f["id"], "datetime": None, "cloud": f["properties"].get("cloud"), "platform": None}
                for f in col.sort("cloud").limit(MAX_SCENES).getInfo()["features"]]
        url = image.select(BANDS).round().toUint16().getDownloadURL(
            {"region": region, "crs": f"EPSG:{grid['epsg']}", "scale": grid["cell"], "format": "GEO_TIFF"})
    except ImageryError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ImageryError(f"Earth Engine could not make the image: {str(e)[:240]}", upstream=True) from None
    most = 120 * 1024 * 1024
    try:
        with httpx.stream("GET", url, timeout=httpx.Timeout(30, read=settings.imagery_timeout_s), headers=UA) as r:
            r.raise_for_status()
            size = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > most:
                        raise ImageryError("Earth Engine sent more than expected: choose a smaller area.", upstream=True)
                    f.write(chunk)
    except httpx.HTTPError:
        raise ImageryError("Earth Engine could not send the image. Choose a smaller area, or try again.", upstream=True) from None
    return used


# =====================================================================================================================
# The entry points the API calls
# =====================================================================================================================
def search(provider: str, satellite: str, bbox, start: dt.date, end: dt.date, max_cloud: int) -> dict:
    area = check(provider, satellite, bbox, start, end)
    scenes = gee_search(satellite, area, start, end, max_cloud) if provider == "gee" else stac_search(provider, satellite, area, start, end, max_cloud)
    return {"scenes": scenes, "count": len(scenes)}


def fetch(provider: str, satellite: str, bbox, start: dt.date, end: dt.date, max_cloud: int, scene_ids: list[str]) -> tuple[dict, dict]:
    """The imagery fetched, saved as a COG in storage; returns the raster's description and a summary of where it came from."""
    area = check(provider, satellite, bbox, start, end)
    sat = SATELLITES[satellite]
    cap = settings.imagery_gee_max_cells if provider == "gee" else settings.imagery_max_cells
    grid = plan(area, sat["res"], cap)
    if provider == "gee":
        work = rasters.workdir()
        try:
            tif = work / "gee.tif"
            used = gee_download(satellite, area, start, end, max_cloud, scene_ids, grid, tif)
            try:
                with rasterio.open(tif, "r+") as d:
                    if d.count == len(BANDS):
                        for i, n in enumerate(BANDS):
                            d.set_band_description(i + 1, n)
                meta = rasters.ingest(tif)
            except (rasterio.errors.RasterioError, rasters.RasterError):
                raise ImageryError("Earth Engine sent a file PolyGIS could not read. Try again, or choose a smaller area.", upstream=True) from None
        finally:
            shutil.rmtree(work, ignore_errors=True)
    else:
        items = _stac_items(provider, satellite, area, start, end, max_cloud, scene_ids)
        data, used = stac_mosaic(provider, satellite, items, grid)
        meta = rasters.save(data, grid["crs"], grid["transform"], nodata=0, dtype="uint16", names=BANDS)
    name = {"planetary-computer": "Planetary Computer", "earth-search": "Earth Search", "gee": "Earth Engine"}[provider]
    dates = sorted(s["datetime"][:10] for s in used if s.get("datetime"))
    when = dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}" if dates else f"{start.isoformat()} to {end.isoformat()}"
    summary = {"source": name, "satellite": sat["name"], "dates": when, "scenes": len(used), "cloud limit %": max_cloud, "cell size m": grid["cell"]}
    if grid["coarsened"]:
        summary["note"] = f"Cells enlarged from {sat['res']} m to fit the area."
    return meta, {"imagery": {"provider": provider, "satellite": satellite, "start": start.isoformat(), "end": end.isoformat(),
                              "max_cloud": max_cloud, "scenes": [s["id"] for s in used], "cell_m": grid["cell"]}, "summary": summary}
