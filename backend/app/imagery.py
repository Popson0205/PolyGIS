"""Open remote-sensing data fetched for an area, so nobody has to download scenes or tiles by hand.

What can be had (each as a raster layer in the UTM zone of the area, with only the bands asked for):

* optical imagery: Sentinel-2 (10 m) and Landsat 8/9 (30 m), with red, green, blue, near-infrared, red-edge and shortwave bands;
* radar: Sentinel-1 (10 m, VV and VH), which sees through cloud;
* elevation: Copernicus DEM and NASADEM (30 m);
* land cover: ESA WorldCover (10 m, 2021);
* rainfall: CHIRPS totals over a period (about 5.5 km; Earth Engine only).

Three sources, one result:

* ``planetary-computer`` and ``earth-search``: public STAC catalogues (Microsoft, Element 84 on AWS). No account or key.
  Scenes and tiles are read straight from their Cloud-Optimized GeoTIFFs, only the part inside the area and only the bands
  asked for, and joined (least cloudy first, for optical imagery) where one does not cover the area.
* ``gee``: Google Earth Engine, when the server has an Earth Engine project (``GEE_PROJECT``, and normally a service
  account: ``GEE_SERVICE_ACCOUNT`` with ``GEE_PRIVATE_KEY``). Clouds are masked and scenes combined into a median composite,
  which Earth Engine clips and sends back as a GeoTIFF.

Optical bands come out the same from every source: surface reflectance times 10 000, unsigned 16-bit, 0 for no data (1 for
reflectance at or below zero). Two corrections make that so:

* Sentinel-2 scenes processed with baseline 04.00 or later (from 25 January 2022) carry a +1000 offset as ESA delivers them;
  Planetary Computer serves them that way, so it is taken off (Earth Search has already done so; Earth Engine's
  S2_SR_HARMONIZED likewise), and scenes from before and after can be joined and compared.
* Landsat Collection 2 Level-2 stores reflectance as DN x 0.0000275 - 0.2; it is turned into reflectance times 10 000.

Without these, an NDVI or any band ratio would be wrong, and a mosaic of older and newer Sentinel-2 scenes would show seams.
Other values are as the providers hold them: radar backscatter as linear power, elevation in metres, rainfall in millimetres.
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
MAX_COARSEN = 8                                    # a request needing cells more than this many times the data's own is refused
MAX_SCENES = 8                                     # scenes joined when PolyGIS picks them itself
MAX_TILES = 40                                     # tiles of a static dataset (elevation, land cover) joined for one area
MAX_DAYS = 3 * 366
UA = {"User-Agent": "PolyGIS (imagery lookup)"}
PC, ES, GEE = "planetary-computer", "earth-search", "gee"


class ImageryError(Exception):
    """The data cannot be had, in words for the person asking. `upstream` is True when the provider is at fault."""

    def __init__(self, message: str, upstream: bool = False):
        super().__init__(message)
        self.upstream = upstream


STAC = {
    PC: {"name": "Microsoft Planetary Computer", "url": "https://planetarycomputer.microsoft.com/api/stac/v1", "sign": True},
    ES: {"name": "Earth Search (Element 84, AWS)", "url": "https://earth-search.aws.element84.com/v1", "sign": False},
}


def _b(name, res, pc=None, es=None, gee=None, label=None):
    """A band: its name, cell size in metres, and what each provider calls it (None: the provider does not have it)."""
    return {"id": name, "label": label or name, "res": res, PC: pc, ES: es, GEE: gee}


_S2 = [_b("Red", 10, "B04", "red", "B4"), _b("Green", 10, "B03", "green", "B3"), _b("Blue", 10, "B02", "blue", "B2"),
       _b("NIR", 10, "B08", "nir", "B8", "Near-infrared"),
       _b("RedEdge1", 20, "B05", "rededge1", "B5", "Red edge 1"), _b("RedEdge2", 20, "B06", "rededge2", "B6", "Red edge 2"),
       _b("RedEdge3", 20, "B07", "rededge3", "B7", "Red edge 3"), _b("NIR2", 20, "B8A", "nir08", "B8A", "Near-infrared (narrow)"),
       _b("SWIR1", 20, "B11", "swir16", "B11", "Shortwave infrared 1"), _b("SWIR2", 20, "B12", "swir22", "B12", "Shortwave infrared 2")]
_LS = [_b("Red", 30, "red", None, "SR_B4"), _b("Green", 30, "green", None, "SR_B3"), _b("Blue", 30, "blue", None, "SR_B2"),
       _b("NIR", 30, "nir08", None, "SR_B5", "Near-infrared"), _b("SWIR1", 30, "swir16", None, "SR_B6", "Shortwave infrared 1"),
       _b("SWIR2", 30, "swir22", None, "SR_B7", "Shortwave infrared 2")]

# kind: optical (clouds, a date range, scenes to choose from), radar (a date range), static (one tile set: no dates), total (summed over a period)
DATASETS = {
    "sentinel2": {"name": "Sentinel-2 imagery (10 m)", "short": "Sentinel-2", "kind": "optical", "bands": _S2, "default": ["Red", "Green", "Blue", "NIR"],
                  "dtype": "uint16", "nodata": 0, "cloud": True, "dated": True, "resampling": "bilinear",
                  "stac": {PC: "sentinel-2-l2a", ES: "sentinel-2-l2a"}, "gee": [("COPERNICUS/S2_SR_HARMONIZED", "CLOUDY_PIXEL_PERCENTAGE")]},
    "landsat": {"name": "Landsat 8 and 9 imagery (30 m)", "short": "Landsat", "kind": "optical", "bands": _LS, "default": ["Red", "Green", "Blue", "NIR"],
                "dtype": "uint16", "nodata": 0, "cloud": True, "dated": True, "resampling": "bilinear", "platforms": ["landsat-8", "landsat-9"],
                "stac": {PC: "landsat-c2-l2"}, "gee": [("LANDSAT/LC08/C02/T1_L2", "CLOUD_COVER"), ("LANDSAT/LC09/C02/T1_L2", "CLOUD_COVER")]},
    "sentinel1": {"name": "Sentinel-1 radar (10 m, sees through cloud)", "short": "Sentinel-1", "kind": "radar",
                  "bands": [_b("VV", 10, "vv", None, "VV", "VV backscatter"), _b("VH", 10, "vh", None, "VH", "VH backscatter")], "default": ["VV", "VH"],
                  "dtype": "float32", "nodata": -32768.0, "cloud": False, "dated": True, "resampling": "bilinear", "query": {"sar:instrument_mode": {"eq": "IW"}},
                  "stac": {PC: "sentinel-1-rtc"}, "gee": ["COPERNICUS/S1_GRD"]},
    "copdem": {"name": "Copernicus elevation model (30 m)", "short": "Copernicus DEM", "kind": "static", "bands": [_b("Elevation", 30, "data", "data", "DEM", "Elevation (m)")],
               "default": ["Elevation"], "dtype": "float32", "nodata": -32768.0, "cloud": False, "dated": False, "resampling": "bilinear",
               "stac": {PC: "cop-dem-glo-30", ES: "cop-dem-glo-30"}, "gee": ["COPERNICUS/DEM/GLO30"]},
    "nasadem": {"name": "NASADEM elevation model (SRTM, 30 m)", "short": "NASADEM", "kind": "static", "bands": [_b("Elevation", 30, "elevation", None, "elevation", "Elevation (m)")],
                "default": ["Elevation"], "dtype": "float32", "nodata": -32768.0, "cloud": False, "dated": False, "resampling": "bilinear",
                "stac": {PC: "nasadem"}, "gee": ["NASA/NASADEM_HGT/001"]},
    "worldcover": {"name": "ESA WorldCover land cover (10 m, 2021)", "short": "WorldCover", "kind": "static", "bands": [_b("Class", 10, "map", None, "Map", "Land cover class")],
                   "default": ["Class"], "dtype": "uint8", "nodata": 0, "cloud": False, "dated": False, "resampling": "nearest", "year": 2021,
                   "stac": {PC: "esa-worldcover"}, "gee": ["ESA/WorldCover/v200"]},
    "chirps": {"name": "CHIRPS rainfall total (about 5.5 km)", "short": "CHIRPS rainfall", "kind": "total", "bands": [_b("Rainfall", 5566, None, None, "precipitation", "Rainfall (mm)")],
               "default": ["Rainfall"], "dtype": "float32", "nodata": -32768.0, "cloud": False, "dated": True, "resampling": "bilinear",
               "stac": {}, "gee": ["UCSB-CHG/CHIRPS/DAILY"]},
}
WORLDCOVER = {10: ("Tree cover", "#006400"), 20: ("Shrubland", "#ffbb22"), 30: ("Grassland", "#ffff4c"), 40: ("Cropland", "#f096ff"),
              50: ("Built-up", "#fa0000"), 60: ("Bare or sparse vegetation", "#b4b4b4"), 70: ("Snow and ice", "#f0f0f0"),
              80: ("Permanent water", "#0064c8"), 90: ("Herbaceous wetland", "#0096a0"), 95: ("Mangroves", "#00cf75"), 100: ("Moss and lichen", "#fae6a0")}


def gee_ready() -> bool:
    return bool(settings.gee_project.strip())


def _offers(provider: str, ds: dict) -> bool:
    return bool(ds["gee"]) if provider == GEE else provider in ds["stac"]


def _scenes_listed(provider: str, ds: dict) -> bool:
    """Whether the scenes can be listed for choosing: optical imagery from anywhere, radar from the catalogues."""
    return ds["kind"] == "optical" or (ds["kind"] == "radar" and provider != GEE)


def providers() -> list[dict]:
    """What the server can offer, for the dialog: each source, whether it is usable, and the datasets (with their bands) it has."""
    out = []
    for pid, name, ready, note in [(PC, STAC[PC]["name"], True, "No account needed."), (ES, STAC[ES]["name"], True, "No account needed."),
                                   (GEE, "Google Earth Engine", gee_ready(),
                                    "Clouds masked, scenes combined into a composite." if gee_ready() else "Not set up on this server (the administrator sets GEE_PROJECT).")]:
        sets = []
        for did, ds in DATASETS.items():
            if not _offers(pid, ds):
                continue
            bands = [b for b in ds["bands"] if b[pid]]
            sets.append({"id": did, "name": ds["name"], "kind": ds["kind"], "dated": ds["dated"], "cloud": ds["cloud"], "scenes": _scenes_listed(pid, ds),
                         "bands": [{"id": b["id"], "label": b["label"], "res": b["res"], "default": b["id"] in ds["default"]} for b in bands]})
        out.append({"id": pid, "name": name, "ready": ready, "note": note, "datasets": sets})
    return out


# =====================================================================================================================
# The request
# =====================================================================================================================
def check(provider: str, dataset: str, bbox, start: dt.date | None, end: dt.date | None, bands: list[str] | None = None) -> tuple[tuple, list[dict]]:
    """The request checked; returns the area as (west, south, east, north) and the bands (in the dataset's own order)."""
    ds = DATASETS.get(dataset)
    if not ds:
        raise ImageryError("That dataset is not known.")
    if provider == GEE:
        if not gee_ready():
            raise ImageryError("Google Earth Engine is not set up on this server. Choose another source, or ask the administrator to set GEE_PROJECT.")
    elif provider not in STAC:
        raise ImageryError("That imagery source is not known.")
    if not _offers(provider, ds):
        name = "Google Earth Engine" if provider == GEE else STAC[provider]["name"]
        raise ImageryError(f"{name} does not have {ds['short']}: choose another source or dataset.")
    have = [b for b in ds["bands"] if b[provider]]
    want = list(dict.fromkeys(bands or ds["default"]))
    unknown = [w for w in want if w not in {b["id"] for b in have}]
    if unknown:
        raise ImageryError(f"{ds['short']} from this source has no band {unknown[0]}.")
    chosen = [b for b in have if b["id"] in want]
    try:
        w, s, e, n = (float(x) for x in bbox)
    except (TypeError, ValueError):
        raise ImageryError("The area is not valid.") from None
    if not all(math.isfinite(x) for x in (w, s, e, n)) or not (-180 <= w < e <= 180 and -85 <= s < n <= 85):
        raise ImageryError("The area is not valid: zoom the map to the place you want, within longitude -180 to 180 and latitude -85 to 85.")
    if ds["dated"]:
        if not start or not end:
            raise ImageryError("Choose the dates.")
        if end < start:
            raise ImageryError("The end date is before the start date.")
        if (end - start).days > MAX_DAYS:
            raise ImageryError("Choose a period of three years or less.")
    return (w, s, e, n), chosen


def cell_budget(provider: str, ds: dict, bands: int) -> int:
    """The most cells (per band) one request may hold: the memory (or, for Earth Engine, download) allowance shared by its bands."""
    mb = settings.imagery_gee_max_mb if provider == GEE else settings.imagery_max_mb
    return max(1, int(mb * 1024 * 1024 / (max(1, bands) * np.dtype(ds["dtype"]).itemsize)))


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
        raise ImageryError(f"The area is too large ({(right - left) / 1000:.0f} by {(top - bottom) / 1000:.0f} km) for this many bands. Zoom the map in, draw a smaller area, or choose fewer bands.")
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
    return {"id": it["id"], "datetime": p.get("datetime") or p.get("start_datetime"), "cloud": p.get("eo:cloud_cover"),
            "platform": p.get("platform") or p.get("constellation")}


def _query(provider: str, ds: dict, bbox, start, end, max_cloud: int, limit: int) -> dict:
    body: dict = {"collections": [ds["stac"][provider]], "bbox": list(bbox), "limit": limit}
    q = dict(ds.get("query", {}))
    if ds["dated"]:
        body["datetime"] = f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z"
    if ds.get("year"):
        body["datetime"] = f"{ds['year']}-01-01T00:00:00Z/{ds['year']}-12-31T23:59:59Z"
    if ds["cloud"]:
        q["eo:cloud_cover"] = {"lte": max_cloud}
        body["sortby"] = [{"field": "properties.eo:cloud_cover", "direction": "asc"}]
    elif ds["dated"]:
        body["sortby"] = [{"field": "properties.datetime", "direction": "desc"}]            # the most recent first
    if ds.get("platforms"):
        q["platform"] = {"in": ds["platforms"]}
    if q:
        body["query"] = q
    return body


def stac_search(provider: str, dataset: str, bbox, start, end, max_cloud: int, limit: int = 30) -> list[dict]:
    return [_scene(i) for i in _stac_post(provider, _query(provider, DATASETS[dataset], bbox, start, end, max_cloud, limit))]


def _stac_items(provider: str, dataset: str, bbox, start, end, max_cloud: int, scene_ids: list[str]) -> list[dict]:
    ds = DATASETS[dataset]
    if scene_ids:
        got = {i["id"]: i for i in _stac_post(provider, {"collections": [ds["stac"][provider]], "ids": scene_ids, "limit": len(scene_ids)})}
        missing = [i for i in scene_ids if i not in got]
        if missing:
            raise ImageryError(f"{STAC[provider]['name']} has no scene {missing[0]}. Search again and choose from the list.")
        items = [got[i] for i in scene_ids]
    else:
        items = _stac_post(provider, _query(provider, ds, bbox, start, end, max_cloud, MAX_TILES if ds["kind"] == "static" else MAX_SCENES))
    if not items:
        raise ImageryError("No data found for that area" + (" and period. Try a longer period or allow more cloud." if ds["dated"] else "."))
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


S2_OFFSET_FROM = dt.date(2022, 1, 25)          # processing baseline 04.00: L2A values gain +1000


def _baseline_offset(it: dict) -> bool:
    """Whether a Sentinel-2 scene's values carry ESA's +1000 offset (processing baseline 04.00 or later)."""
    p = it.get("properties", {})
    b = p.get("s2:processing_baseline")
    if b not in (None, ""):
        try:
            return float(b) >= 4.0
        except (TypeError, ValueError):
            pass
    when = (p.get("datetime") or p.get("start_datetime") or "")[:10]
    try:
        return dt.date.fromisoformat(when) >= S2_OFFSET_FROM
    except ValueError:
        return False


def to_reflectance(provider: str, dataset: str, it: dict, a: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """A scene's optical band as surface reflectance x 10 000 (uint16; 0 stays no data, values at or below 0 become 1)."""
    if dataset == "sentinel2" and provider == PC and _baseline_offset(it):
        v = a.astype(np.int32) - 1000
    elif dataset == "landsat":
        v = np.rint(a.astype(np.float64) * 0.275 - 2000)
    else:
        return a
    return np.where(ok, np.clip(v, 1, 65535), 0).astype(np.uint16)


def stac_mosaic(provider: str, dataset: str, items: list[dict], grid: dict, bands: list[dict]) -> tuple[np.ndarray, list[dict]]:
    """The scenes read onto the grid (only the bands asked for), least cloudy first, each filling only what the earlier ones left empty."""
    ds = DATASETS[dataset]
    coll, dtype, nodata = ds["stac"][provider], np.dtype(ds["dtype"]), ds["nodata"]
    rs = Resampling.nearest if ds["resampling"] == "nearest" else Resampling.bilinear
    w, h = grid["width"], grid["height"]
    out = np.full((len(bands), h, w), nodata, dtype)
    filled = np.zeros((h, w), bool)
    tokens: dict = {}
    used: list[dict] = []
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff", GDAL_HTTP_MAX_RETRY=3,
               GDAL_HTTP_RETRY_DELAY=1, GDAL_HTTP_TIMEOUT=60, GDAL_CACHEMAX=128)
    if ds["cloud"]:
        items = sorted(items, key=lambda i: i.get("properties", {}).get("eo:cloud_cover", 100))
    for it in items:
        if filled.all():
            break
        try:
            hrefs = [_signed(provider, coll, tokens, it["assets"][b[provider]]["href"]) for b in bands]
        except KeyError:
            continue                                   # a scene without all the bands asked for is left out
        got, ok = [], np.ones((h, w), bool)
        try:
            with rasterio.Env(**env):
                for href in hrefs:
                    with rasterio.open(href) as src, WarpedVRT(src, crs=grid["crs"], transform=grid["transform"], width=w, height=h, resampling=rs,
                                                                 src_nodata=src.nodata, nodata=nodata, add_alpha=src.nodata is None) as vrt:
                        a = vrt.read(1)
                        good = (vrt.dataset_mask() > 0) & np.isfinite(a)
                        if ds["kind"] == "optical":
                            good &= a != 0
                            a = to_reflectance(provider, dataset, it, a, good)
                        ok &= good
                        got.append(a)
        except rasterio.errors.RasterioError as e:
            log.warning("scene %s could not be read: %s", it.get("id"), e)
            continue
        fresh = ok & ~filled
        if fresh.any():
            a = np.stack(got).astype(dtype)
            out[:, fresh] = a[:, fresh]
            filled |= fresh
            used.append(_scene(it))
    if not used:
        raise ImageryError("The data found holds nothing usable over that area (it may be mostly cloud or edge). Try another period.")
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


def _gee_collection(ee, dataset: str, bands: list[dict], bbox, start: dt.date, end: dt.date, max_cloud: int):
    """Optical scenes in the area and period with their clouds masked, the bands renamed as ours, `cloud` set on each."""
    ds = DATASETS[dataset]
    region = ee.Geometry.Rectangle(list(bbox))
    last = (end + dt.timedelta(days=1)).isoformat()                 # filterDate's end is exclusive
    names, gbands = [b["id"] for b in bands], [b[GEE] for b in bands]

    def prepare(cloud_prop):
        def f(img):
            if dataset == "sentinel2":
                scl = img.select("SCL")
                good = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))   # shadow, cloud (medium, high), cirrus, snow
            else:
                good = img.select("QA_PIXEL").bitwiseAnd(0b11010).eq(0)                               # dilated cloud, cloud, cloud shadow
            out = img.updateMask(good).select(gbands, names)
            if dataset == "landsat":                                                                  # DN to reflectance x 10 000
                out = out.multiply(0.275).subtract(2000).max(1)
            # merge() renames system:index ("1_…", "2_…"), so each scene keeps its own id as "scene"
            return out.set("cloud", img.get(cloud_prop)).set("scene", img.get("system:id")).set("system:time_start", img.get("system:time_start"))
        return f

    merged = None
    for cid, cloud_prop in ds["gee"]:
        c = ee.ImageCollection(cid).filterBounds(region).filterDate(start.isoformat(), last).filter(ee.Filter.lte(cloud_prop, max_cloud))
        c = c.map(prepare(cloud_prop))
        merged = c if merged is None else merged.merge(c)
    return merged, region


def gee_search(dataset: str, bands: list[dict], bbox, start: dt.date, end: dt.date, max_cloud: int, limit: int = 30) -> list[dict]:
    ee = _earth_engine()
    try:
        col, _ = _gee_collection(ee, dataset, bands, bbox, start, end, max_cloud)
        feats = col.sort("cloud").limit(limit).getInfo()["features"]
    except Exception as e:  # noqa: BLE001
        raise ImageryError(f"Earth Engine could not search: {str(e)[:200]}", upstream=True) from None
    out = []
    for f in feats:
        p = f.get("properties", {})
        t = p.get("system:time_start")
        out.append({"id": p.get("scene") or f["id"], "datetime": dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).isoformat() if t else None,
                    "cloud": p.get("cloud"), "platform": None})
    return out


def _gee_image(ee, dataset: str, bands: list[dict], bbox, start, end, max_cloud: int, scene_ids: list[str]):
    """The Earth Engine image for the request (typed as the layer will be) with the scenes it was made from, and the region."""
    ds = DATASETS[dataset]
    names, gbands = [b["id"] for b in bands], [b[GEE] for b in bands]
    region = ee.Geometry.Rectangle(list(bbox))
    used: list[dict] = []
    if ds["kind"] == "optical":
        col, region = _gee_collection(ee, dataset, bands, bbox, start, end, 100 if scene_ids else max_cloud)
        if scene_ids:
            col = col.filter(ee.Filter.inList("scene", list(scene_ids)))
            image = col.sort("cloud", False).mosaic()                # the least cloudy scene on top
        else:
            image = col.median()
        if not col.size().getInfo():
            raise ImageryError("No scenes found for that area and period. Try a longer period or allow more cloud.")
        used = [{"id": f["properties"].get("scene") or f["id"], "datetime": None, "cloud": f["properties"].get("cloud"), "platform": None}
                for f in col.sort("cloud").limit(MAX_SCENES).getInfo()["features"]]
        return image.select(names).round().toUint16(), used, region
    last = (end + dt.timedelta(days=1)).isoformat() if end else None
    cid = ds["gee"][0]
    if dataset == "sentinel1":
        col = ee.ImageCollection(cid).filterBounds(region).filterDate(start.isoformat(), last).filter(ee.Filter.eq("instrumentMode", "IW"))
        for g in gbands:
            col = col.filter(ee.Filter.listContains("transmitterReceiverPolarisation", g))
        if not col.size().getInfo():
            raise ImageryError("No radar scenes found for that area and period. Try a longer period.")
        linear = col.select(gbands).map(lambda i: ee.Image(10).pow(i.divide(10)).copyProperties(i, ["system:time_start"]))   # dB to linear power, as Planetary Computer holds it
        return linear.median().rename(names).toFloat(), used, region
    if dataset == "copdem":
        col = ee.ImageCollection(cid).filterBounds(region).select(gbands)
        return col.mosaic().setDefaultProjection(col.first().projection()).rename(names).toFloat(), used, region
    if dataset == "nasadem":
        return ee.Image(cid).select(gbands).rename(names).toFloat(), used, region
    if dataset == "worldcover":
        return ee.ImageCollection(cid).mosaic().select(gbands).rename(names).toUint8(), used, region
    if dataset == "chirps":
        col = ee.ImageCollection(cid).filterDate(start.isoformat(), last).select(gbands)
        return col.sum().rename(names).toFloat(), used, region
    raise ImageryError("That dataset is not available from Earth Engine.")


def gee_download(dataset: str, bands: list[dict], bbox, start, end, max_cloud: int, scene_ids: list[str], grid: dict, dest: Path) -> list[dict]:
    """The data for the area written to `dest` as a GeoTIFF by Earth Engine."""
    ee = _earth_engine()
    try:
        image, used, region = _gee_image(ee, dataset, bands, bbox, start, end, max_cloud, scene_ids)
        url = image.getDownloadURL({"region": region, "crs": f"EPSG:{grid['epsg']}", "scale": grid["cell"], "format": "GEO_TIFF"})
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
                        raise ImageryError("Earth Engine sent more than expected: choose a smaller area or fewer bands.", upstream=True)
                    f.write(chunk)
    except httpx.HTTPError:
        raise ImageryError("Earth Engine could not send the image. Choose a smaller area, or try again.", upstream=True) from None
    return used


# =====================================================================================================================
# How the layer is drawn
# =====================================================================================================================
def style_for(dataset: str, meta: dict, names: list[str]) -> dict:
    """True colour when red, green and blue are there; false colour (near-infrared, red, green) when those are; land cover in its
    own colours; otherwise the first band as a colour ramp."""
    b = meta["bands"]
    at = {n: i for i, n in enumerate(names)}
    rng = lambda ids, k: [rasters._r(b[i].get(k, 0)) for i in ids]  # noqa: E731
    for combo in (("Red", "Green", "Blue"), ("NIR", "Red", "Green")):
        if all(c in at for c in combo):
            ids = [at[c] for c in combo]
            return {"type": "raster", "mode": "rgb", "bands": [i + 1 for i in ids], "mins": rng(ids, "p2"), "maxs": rng(ids, "p98"), "opacity": 1}
    if dataset == "worldcover":
        got = b[0].get("values") or sorted(WORLDCOVER)
        return {"type": "raster", "mode": "classes", "band": 1, "opacity": 1,
                "categories": [{"value": v, "color": WORLDCOVER.get(v, ("", "#7f7f7f"))[1], "label": WORLDCOVER.get(v, (str(v),))[0]} for v in got]}
    ramp = {"copdem": "Terrain", "nasadem": "Terrain", "chirps": "Blues", "sentinel1": "Gray"}.get(dataset, "Viridis")
    return {"type": "raster", "mode": "ramp", "band": 1, "min": rasters._r(b[0].get("p2", 0)), "max": rasters._r(b[0].get("p98", 1)), "ramp": ramp, "opacity": 1}


# =====================================================================================================================
# The entry points the API calls
# =====================================================================================================================
def search(provider: str, dataset: str, bbox, start, end, max_cloud: int, bands: list[str] | None = None) -> dict:
    area, chosen = check(provider, dataset, bbox, start, end, bands)
    if not _scenes_listed(provider, DATASETS[dataset]):
        raise ImageryError(f"The scenes of {DATASETS[dataset]['short']} cannot be listed from this source: PolyGIS combines them itself.")
    scenes = gee_search(dataset, chosen, area, start, end, max_cloud) if provider == GEE else stac_search(provider, dataset, area, start, end, max_cloud)
    return {"scenes": scenes, "count": len(scenes)}


def fetch(provider: str, dataset: str, bbox, start, end, max_cloud: int, scene_ids: list[str], bands: list[str] | None = None) -> tuple[dict, dict, dict]:
    """The data fetched, saved as a COG in storage; returns the raster's description, a summary of where it came from, and its style."""
    area, chosen = check(provider, dataset, bbox, start, end, bands)
    ds = DATASETS[dataset]
    names = [b["id"] for b in chosen]
    grid = plan(area, min(b["res"] for b in chosen), cell_budget(provider, ds, len(chosen)))
    if provider == GEE:
        work = rasters.workdir()
        try:
            tif = work / "gee.tif"
            used = gee_download(dataset, chosen, area, start, end, max_cloud, scene_ids, grid, tif)
            try:
                with rasterio.open(tif, "r+") as d:
                    if d.count == len(names):
                        for i, n in enumerate(names):
                            d.set_band_description(i + 1, n)
                    if d.nodata is None and ds["dtype"].startswith("uint"):
                        d.nodata = ds["nodata"]
                meta = rasters.ingest(tif)
            except (rasterio.errors.RasterioError, rasters.RasterError):
                raise ImageryError("Earth Engine sent a file PolyGIS could not read. Try again, or choose a smaller area.", upstream=True) from None
        finally:
            shutil.rmtree(work, ignore_errors=True)
    else:
        items = _stac_items(provider, dataset, area, start, end, max_cloud, scene_ids)
        data, used = stac_mosaic(provider, dataset, items, grid, chosen)
        meta = rasters.save(data, grid["crs"], grid["transform"], nodata=ds["nodata"], dtype=ds["dtype"], names=names, categorical=dataset == "worldcover")
    name = {PC: "Planetary Computer", ES: "Earth Search", GEE: "Earth Engine"}[provider]
    dates = sorted(s["datetime"][:10] for s in used if s.get("datetime"))
    summary: dict = {"source": name, "dataset": ds["name"], "bands": ", ".join(names)}
    if ds["dated"]:
        summary["dates"] = dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}" if dates else f"{start.isoformat()} to {end.isoformat()}"
    if used:
        summary["scenes"] = len(used)
    if ds["cloud"]:
        summary["cloud limit %"] = max_cloud
    summary["cell size m"] = grid["cell"]
    if grid["coarsened"]:
        summary["note"] = f"Cells enlarged from {min(b['res'] for b in chosen):g} m to fit the area."
    info = {"imagery": {"provider": provider, "dataset": dataset, "bands": names, "start": start.isoformat() if start and ds["dated"] else None,
                        "end": end.isoformat() if end and ds["dated"] else None, "max_cloud": max_cloud if ds["cloud"] else None,
                        "scenes": [s["id"] for s in used], "cell_m": grid["cell"]}, "summary": summary}
    return meta, info, style_for(dataset, meta, names)
