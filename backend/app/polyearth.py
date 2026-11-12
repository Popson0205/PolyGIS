"""Poly Earth: the analyses people write Earth Engine scripts for, as tools with a form, run on open data.

Nothing here needs Google: scenes come from the public STAC catalogues (Microsoft Planetary Computer, Earth Search on AWS),
rainfall from CHIRPS (Climate Hazards Center, UC Santa Barbara), surface water history from the JRC Global Surface Water
layers on Planetary Computer. PolyGIS reads only the area and the bands it needs from each file and does the rest itself.

What Earth Engine does, and what is done here the same way:

* Clouds are masked pixel by pixel before scenes are combined: Sentinel-2 by its scene classification (SCL: no data,
  saturated, cloud shadow, cloud medium and high probability, thin cirrus, snow), Landsat by QA_PIXEL (fill, dilated cloud,
  cirrus, cloud, cloud shadow). Then the clear pixels of all scenes are reduced (median, mean, min, max, a percentile, the
  greenest pixel, or the most recent), band by band, like ``ImageCollection.median()`` or ``qualityMosaic``.
* Optical values are surface reflectance (0 to 1): Sentinel-2's +1000 offset of processing baseline 04.00 is removed where the
  catalogue has not, and Landsat Collection 2 is scaled (DN x 0.0000275 - 0.2). Landsat surface temperature is DN x 0.00341802
  + 149 kelvin, shown in degrees Celsius.
* Spectral indices come from the composite, as an Earth Engine script would compute ``normalizedDifference`` on a median.
* Flood mapping follows UN-SPIDER's Recommended Practice for Sentinel-1 in Earth Engine: after / before ratio of the smoothed
  backscatter (dB, 50 m circle) above 1.25, without permanent water (water 10 or more months a year), slopes of 5 degrees or
  more, and patches of fewer than 8 connected pixels.
* Burn severity is dNBR (NBR before - NBR after) in the USGS classes (Key and Benson).

A tool is a plain function of its settings: it reports progress through ``ctx.say`` and returns new layers (``raster_tools.Out``),
and, for time series and classifications, a chart or an accuracy report.
"""
import calendar
import datetime as dt
import logging
import math
import threading
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import numpy as np
import rasterio
import shapely
from rasterio import features as rfeatures
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import rowcol
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds
from scipy import ndimage

from . import imagery
from . import raster_tools as rt
from .config import settings
from .imagery import DATASETS, ES, PC, ImageryError
from .tools import _p

log = logging.getLogger("polyearth")
ETOOLS: dict[str, dict] = {}
EBUILD: dict[str, Callable] = {}
MAX_SCENES_READ = 240           # scene files read for one run, all periods together
MAX_PERIOD_DAYS = 366           # one composite's date range
MAX_SERIES_DAYS = 6 * 366
MAX_ZONES = 50
bad = rt.bad


def etool(tid: str, name: str, group: str, about: str, *params):
    def reg(fn):
        ETOOLS[tid] = {"name": name, "group": group, "about": about, "params": list(params), "kind": "earth"}
        EBUILD[tid] = fn
        return fn
    return reg


def catalog() -> list[dict]:
    return [{"id": k, **v} for k, v in ETOOLS.items()]


_clip = threading.local()


def set_clip(geom) -> None:
    """The polygons (WGS 84) a run is clipped to, for the thread running it; None for a rectangle."""
    _clip.geom = geom


def get_clip():
    return getattr(_clip, "geom", None)


def outside(g: dict, shape: tuple | None = None):
    """The grid's cells outside the clip polygons (True), or None when the run is not clipped."""
    geom = get_clip()
    if geom is None:
        return None
    gg = rt.to_crs([geom], g["crs"])[0]
    shape = shape or (g["height"], g["width"])
    inside = rfeatures.geometry_mask([gg], shape, g["transform"], invert=True, all_touched=True)
    return ~inside


def clip_array(a: np.ndarray, g: dict) -> np.ndarray:
    out = outside(g, a.shape[-2:])
    if out is None:
        return a
    a = a.astype(np.float32, copy=True)
    a[..., out] = np.nan
    return a


@dataclass
class Ctx:
    params: dict
    inputs: rt.Inputs
    say: Callable = lambda message, done=None: None      # progress, in words, and how far along (0 to 1)
    clip: object = None                                  # polygons (WGS 84) the area is clipped to


@dataclass
class Result:
    outs: list[rt.Out]
    chart: dict | None = None          # {title, unit, x: [...], series: [{name, values}]}
    report: dict | None = None         # a classification's accuracy: {classes, matrix, overall, kappa, producers, users, note}
    notes: list[str] = field(default_factory=list)


# =====================================================================================================================
# Settings shared by the tools
# =====================================================================================================================
SENSOR = _p("sensor", "Satellite", "choice", default="sentinel2",
            choices=[["sentinel2", "Sentinel-2 (10 m, every 5 days)"], ["landsat", "Landsat 8 and 9 (30 m, every 8 days)"]])
SOURCE = _p("source", "Catalogue", "choice", default=PC, when={"sensor": "sentinel2"},
            choices=[[PC, "Microsoft Planetary Computer"], [ES, "Earth Search (AWS)"]])
AREA = _p("bbox", "Area", "area")                 # the app sends the map view's or a layer's box, or a polygon layer to clip to
CLOUD = _p("max_cloud", "Leave out scenes cloudier than", "number", unit="%", min=0, default=50)
SCENES = _p("scenes", "Scenes to combine (at most, for each satellite tile)", "number", min=1, default=6)
MAX_PERIOD_SCENES = 36          # scenes combined for one period, all tiles together


def dates(prefix: str, label: str, ago: int, length: int) -> list[dict]:
    """A date range: two date settings, by default `length` days ending `ago` days ago."""
    return [_p(f"{prefix}start", f"{label}: from", "date", default={"ago": ago + length}),
            _p(f"{prefix}end", f"{label}: to", "date", default={"ago": ago})]


PERIOD = dates("", "Dates", 0, 120)
BEFORE = dates("before_", "Before", 365, 120)
AFTER = dates("after_", "After", 0, 120)

INDICES: dict[str, dict] = {
    "ndvi": {"name": "NDVI: vegetation", "bands": ["NIR", "Red"], "range": (-0.2, 0.9), "ramp": "Red to green"},
    "evi": {"name": "EVI: vegetation (dense canopies)", "bands": ["NIR", "Red", "Blue"], "range": (-0.2, 0.9), "ramp": "Red to green"},
    "savi": {"name": "SAVI: vegetation over bare soil", "bands": ["NIR", "Red"], "range": (-0.2, 0.9), "ramp": "Red to green"},
    "msavi": {"name": "MSAVI: vegetation, sparse cover", "bands": ["NIR", "Red"], "range": (-0.2, 0.9), "ramp": "Red to green"},
    "gndvi": {"name": "GNDVI: crop chlorophyll", "bands": ["NIR", "Green"], "range": (-0.2, 0.9), "ramp": "Red to green"},
    "ndre": {"name": "NDRE: crop nitrogen (Sentinel-2 red edge)", "bands": ["NIR", "RedEdge1"], "range": (-0.1, 0.6), "ramp": "Red to green"},
    "ndwi": {"name": "NDWI: open water (McFeeters)", "bands": ["Green", "NIR"], "range": (-0.6, 0.6), "ramp": "Blues"},
    "mndwi": {"name": "MNDWI: water, also in towns (Xu)", "bands": ["Green", "SWIR1"], "range": (-0.6, 0.6), "ramp": "Blues"},
    "ndmi": {"name": "NDMI: vegetation moisture", "bands": ["NIR", "SWIR1"], "range": (-0.4, 0.6), "ramp": "Blues"},
    "nbr": {"name": "NBR: burnt areas", "bands": ["NIR", "SWIR2"], "range": (-0.4, 0.8), "ramp": "Red to green"},
    "ndbi": {"name": "NDBI: built-up land", "bands": ["SWIR1", "NIR"], "range": (-0.5, 0.3), "ramp": "Reds"},
    "bsi": {"name": "BSI: bare soil", "bands": ["SWIR1", "Red", "NIR", "Blue"], "range": (-0.4, 0.4), "ramp": "Reds"},
}
INDEX = _p("index", "Index", "choice", default="ndvi", choices=[[k, v["name"]] for k, v in INDICES.items()])

SENSORS = {
    "sentinel2": {"name": "Sentinel-2", "res": 10, "dataset": "sentinel2", "qa": {PC: "SCL", ES: "scl"}},
    "landsat": {"name": "Landsat", "res": 30, "dataset": "landsat", "qa": {PC: "qa_pixel"}},
}
SCL_BAD = (0, 1, 3, 8, 9, 10, 11)        # no data, saturated, cloud shadow, cloud (medium, high), thin cirrus, snow
QA_BAD = 0b11111                         # QA_PIXEL bits 0-4: fill, dilated cloud, cirrus, cloud, cloud shadow
ST_ASSET = "lwir11"                      # Landsat surface temperature (ST_B10)


def sensor_of(p) -> tuple[str, str]:
    s = p.get("sensor") or "sentinel2"
    if s not in SENSORS:
        raise bad("Choose Sentinel-2 or Landsat.", "sensor")
    src = PC if s == "landsat" else (p.get("source") or PC)
    if src not in (PC, ES):
        raise bad("Choose a catalogue.", "source")
    return s, src


def band_names(sensor: str) -> list[str]:
    return [b["id"] for b in DATASETS[SENSORS[sensor]["dataset"]]["bands"]]


def check_index(sensor: str, idx: str) -> dict:
    spec = INDICES.get(idx)
    if not spec:
        raise bad("Choose an index.", "index")
    missing = [b for b in spec["bands"] if b not in band_names(sensor)]
    if missing:
        raise bad(f"{spec['name'].split(':')[0]} needs the {missing[0]} band, which {SENSORS[sensor]['name']} does not have: choose Sentinel-2.", "index")
    return spec


def area_of(p) -> tuple[float, float, float, float]:
    try:
        w, s, e, n = (float(x) for x in p.get("bbox") or [])
    except (TypeError, ValueError):
        raise bad("Choose the area: the map's view or a layer's extent.", "bbox") from None
    if not all(math.isfinite(x) for x in (w, s, e, n)) or not (-180 <= w < e <= 180 and -85 <= s < n <= 85):
        raise bad("The area is not valid: zoom the map to the place you want.", "bbox")
    return w, s, e, n


def date_of(p, name: str, label: str) -> dt.date:
    v = p.get(name)
    try:
        return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        raise bad(f"Choose the date ({label}).", name) from None


def span(p, prefix: str = "", label: str = "Dates", most: int = MAX_PERIOD_DAYS) -> tuple[dt.date, dt.date]:
    a, b = date_of(p, f"{prefix}start", f"{label}: from"), date_of(p, f"{prefix}end", f"{label}: to")
    if b < a:
        raise bad(f"{label}: the end is before the start.", f"{prefix}end")
    if (b - a).days > most:
        raise bad(f"{label}: choose a period of {'a year' if most <= 366 else f'{most // 365} years'} or less.", f"{prefix}end")
    if a > dt.date.today():
        raise bad(f"{label}: the dates are in the future.", f"{prefix}start")
    return a, b


def whole(p, name: str, lo: int, hi: int, label: str, default: int) -> int:
    return int(round(rt.num(p, name, lo, hi, label, default)))


# =====================================================================================================================
# Grids and reading
# =====================================================================================================================
GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff,.cog,.TIF", GDAL_HTTP_MAX_RETRY=3,
                GDAL_HTTP_RETRY_DELAY=1, GDAL_HTTP_TIMEOUT=60, GDAL_CACHEMAX=512, GDAL_HTTP_MULTIRANGE="YES",
                GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", VSI_CACHE="TRUE", VSI_CACHE_SIZE=64_000_000,
                GDAL_HTTP_VERSION="2", GDAL_HTTP_MULTIPLEX="YES",                       # many reads share one connection per host
                GDAL_INGESTED_BYTES_AT_OPEN=65536,                                       # a COG's header in one request, not several
                CPL_VSIL_CURL_CHUNK_SIZE=262144, CPL_VSIL_CURL_CACHE_SIZE=128_000_000)


def plan(bbox, res: float, bytes_per_cell: float, what: str = "scenes") -> dict:
    """The grid (UTM zone of the area) with cells of `res` metres, enlarged to keep the run within memory."""
    cap = max(1, int(settings.earth_max_mb * 1024 * 1024 / max(1.0, bytes_per_cell)))
    try:
        return imagery.plan(bbox, res, cap)
    except ImageryError:
        raise bad(f"The area is too large for this many {what}: zoom in, use a smaller area, a shorter period or fewer scenes.", "bbox") from None


def grid_of(g: dict, data: np.ndarray) -> rt.Grid:
    return rt.Grid(np.ma.masked_invalid(data), g["crs"], g["transform"])


def overview_for(src, cell_m: float) -> int | None:
    """The coarsest overview of a file whose cells are still no larger than the grid's (None: the full-detail data). Reading
    a 10 km area at 40 m from a 10 m file then fetches about 1/16 of the bytes, as Earth Engine reads its pyramids."""
    ovs = src.overviews(1)
    if not ovs or src.crs is None:
        return None
    res = abs(src.res[0]) * (111_320 if src.crs.is_geographic else (src.crs.linear_units_factor[1] if src.crs.linear_units_factor else 1))
    fit = [i for i, f in enumerate(ovs) if res * f <= cell_m * 1.01]
    return fit[-1] if fit else None


def read_onto(href: str, g: dict, nearest: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """One band of a (remote) file warped onto the grid: its values and where they are valid. Values are read from the file's
    overviews when the grid is coarser; masks (nearest) always from the full-detail data, since averaged overviews of class or
    bit-flag bands would be meaningless."""
    rs = Resampling.nearest if nearest else Resampling.bilinear
    with rasterio.Env(**GDAL_ENV):
        lvl = None
        if not nearest:
            with rasterio.open(href) as src:
                lvl = overview_for(src, g["cell"])
        with (rasterio.open(href, overview_level=lvl) if lvl is not None else rasterio.open(href)) as src:
            nd = src.nodata
            with WarpedVRT(src, crs=g["crs"], transform=g["transform"], width=g["width"], height=g["height"], resampling=rs,
                           src_nodata=nd, nodata=nd if nd is not None else 0, add_alpha=nd is None) as vrt:
                a = vrt.read(1)
                ok = vrt.dataset_mask() > 0
    if np.issubdtype(a.dtype, np.floating):
        ok &= np.isfinite(a)
    return a, ok


def warm(src: str, coll: str, tokens: dict) -> None:
    """Get Planetary Computer's read token once, before the files are read in parallel."""
    imagery._signed(src, coll, tokens, "")


def in_parallel(jobs: list[Callable], ctx: Ctx, what: str, start: float = 0.05, end: float = 0.9) -> list:
    """Run the jobs (reads of remote files) a few at a time, saying how far along; results in the jobs' order."""
    out: list = [None] * len(jobs)
    if not jobs:
        return out
    ctx.say(f"Reading {what}: 0 of {len(jobs)}", start)
    with ThreadPoolExecutor(max_workers=max(1, settings.earth_workers)) as ex:
        futs = {ex.submit(j): i for i, j in enumerate(jobs)}
        for k, f in enumerate(as_completed(futs), 1):
            out[futs[f]] = f.result()
            ctx.say(f"Reading {what}: {k} of {len(jobs)}", start + (end - start) * k / len(jobs))
    return out


# ---------- optical scenes ----------
def _tile(it: dict) -> str:
    p = it.get("properties", {})
    if p.get("s2:mgrs_tile"):
        return str(p["s2:mgrs_tile"])
    if p.get("mgrs:grid_square"):
        return f"{p.get('mgrs:utm_zone')}{p.get('mgrs:latitude_band')}{p['mgrs:grid_square']}"
    if p.get("landsat:wrs_path"):
        return f"{p['landsat:wrs_path']}/{p.get('landsat:wrs_row')}"
    if p.get("grid:code"):
        return str(p["grid:code"])
    return ",".join(f"{v:.1f}" for v in (it.get("bbox") or [0, 0])[:2])


def pick(items: list[dict], per_tile: int, most: int = MAX_PERIOD_SCENES) -> list[dict]:
    """The least cloudy scenes of each satellite tile over the area, taken in turns so a large area is covered evenly, and at
    most `most` in all."""
    groups: dict[str, list] = {}
    for it in sorted(items, key=lambda i: i.get("properties", {}).get("eo:cloud_cover", 100)):
        groups.setdefault(_tile(it), []).append(it)
    out: list[dict] = []
    for k in range(per_tile):
        for g in groups.values():
            if k < len(g) and len(out) < most:
                out.append(g[k])
    return out


def find_scenes(sensor: str, src: str, bbox, start, end, max_cloud: int, per_tile: int, clip=None) -> list[dict]:
    ds = DATASETS[SENSORS[sensor]["dataset"]]
    body = imagery._query(src, ds, bbox, start, end, max_cloud, 100)
    items = imagery._stac_post(src, body)
    if clip is not None:                    # only scenes whose footprint touches the polygons, not just their bounding box
        items = [it for it in items if not it.get("geometry") or shapely.geometry.shape(it["geometry"]).intersects(clip)]
    return pick(items, per_tile)


def read_scene(sensor: str, src: str, item: dict, bands: list[str], g: dict, tokens: dict) -> dict | None:
    """A scene's bands on the grid as reflectance (0 to 1; surface temperature "ST" in deg C), NaN where cloudy or empty.
    None when the scene has nothing clear over the area, lacks a band, or cannot be read."""
    sp = SENSORS[sensor]
    dataset = sp["dataset"]
    ds = DATASETS[dataset]
    coll = ds["stac"][src]
    assets = item.get("assets", {})
    keys = {b["id"]: b[src] for b in ds["bands"]}
    try:
        def href(k):
            return imagery._signed(src, coll, tokens, assets[k]["href"])
        qa, qok = read_onto(href(sp["qa"][src]), g, nearest=True)
        clear = qok & (~np.isin(qa, SCL_BAD) if sensor == "sentinel2" else ((qa.astype(np.uint32) & QA_BAD) == 0))
        if not clear.any():
            return None
        out = {"_date": (item.get("properties", {}).get("datetime") or "")[:10], "_id": item.get("id")}
        for b in bands:
            if b == "ST":
                a, ok = read_onto(href(ST_ASSET), g)
                good = ok & clear & (a != 0)
                v = (a.astype(np.float64) * 0.00341802 + 149.0 - 273.15).astype(np.float32)
            else:
                a, ok = read_onto(href(keys[b]), g)
                good = ok & clear & (a != 0)
                v = imagery.to_reflectance(src, dataset, item, a, good).astype(np.float32) / 10000
            v[~good] = np.nan
            out[b] = v
        return out
    except KeyError:
        return None
    except rasterio.errors.RasterioError as e:
        log.warning("scene %s could not be read: %s", item.get("id"), e)
        return None


def reduce(stack: np.ndarray, how: str, pct: float = 50) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        if how == "mean":
            return np.nanmean(stack, 0)
        if how == "min":
            return np.nanmin(stack, 0)
        if how == "max":
            return np.nanmax(stack, 0)
        if how == "percentile":
            return np.nanpercentile(stack, pct, axis=0)
        return np.nanmedian(stack, 0)


def composite(scenes: list[dict], bands: list[str], how: str = "median", pct: float = 50) -> dict[str, np.ndarray]:
    """The scenes' clear pixels combined band by band (median, mean, min, max, percentile), or whole pixels taken from the
    greenest observation (highest NDVI, Earth Engine's qualityMosaic) or the most recent clear one."""
    if how in ("greenest", "latest"):
        if how == "greenest":
            with np.errstate(all="ignore"):
                key = np.stack([(s["NIR"] - s["Red"]) / (s["NIR"] + s["Red"]) for s in scenes])
        else:
            order = sorted(range(len(scenes)), key=lambda i: scenes[i]["_date"])
            rank = {i: r for r, i in enumerate(order)}
            key = np.stack([np.where(np.isfinite(s[bands[0]]), rank[i], np.nan) for i, s in enumerate(scenes)])
        valid = np.isfinite(key)
        best = np.where(valid, key, -np.inf).argmax(0)
        anyv = valid.any(0)
        out = {}
        for b in bands:
            st = np.stack([s[b] for s in scenes])
            v = np.take_along_axis(st, best[None], 0)[0]
            out[b] = np.where(anyv, v, np.nan).astype(np.float32)
        return out
    return {b: reduce(np.stack([s[b] for s in scenes]), how, pct).astype(np.float32) for b in bands}


def optical(ctx: Ctx, sensor: str, src: str, periods: list[tuple[dt.date, dt.date, str]], bbox, max_cloud: int, per_tile: int,
            bands: list[str], each: Callable, g: dict | None = None, extra_layers: int = 1, need_each: bool = True) -> dict:
    """For each period: find its scenes, read them (clouds masked), and hand them to `each(period_index, scenes, grid)`, one
    period at a time so memory holds one period's scenes. Returns the grid."""
    sp = SENSORS[sensor]
    ctx.say(f"Finding {sp['name']} scenes", 0.02)
    clip = get_clip()
    found = in_parallel([lambda a=a, b=b: find_scenes(sensor, src, bbox, a, b, max_cloud, per_tile, clip) for a, b, _ in periods], ctx, "the catalogue", 0.02, 0.06)
    total = sum(len(f) for f in found)
    if total == 0:
        raise bad(f"No {sp['name']} scenes found for the area and dates" + (f" ({periods[0][2]})" if len(periods) == 1 and periods[0][2] else "")
                  + ". Choose a longer period or allow more cloud.")
    for (a, b, label), f in zip(periods, found):
        if not f and need_each and len(periods) <= 2:
            raise bad(f"No {sp['name']} scenes found for {label or 'the dates'} ({a.isoformat()} to {b.isoformat()}). Choose a longer period or allow more cloud.")
    if total > MAX_SCENES_READ:
        raise bad(f"That needs {total} scenes; the most for one run is {MAX_SCENES_READ}. Choose fewer scenes per period, a shorter period or a smaller area.")
    most = max(len(f) for f in found)
    if g is None:
        g = plan(bbox, sp["res"], (most * len(bands) * 4 * 2) + 4 * len(periods) * extra_layers)
    tokens: dict = {}
    warm(src, DATASETS[sp["dataset"]]["stac"][src], tokens)
    done = 0
    for k, ((a, b, label), items) in enumerate(zip(periods, found)):
        if not items:
            each(k, [], g)
            continue
        start, end = 0.06 + 0.84 * done / total, 0.06 + 0.84 * (done + len(items)) / total
        got = in_parallel([lambda it=it: read_scene(sensor, src, it, bands, g, tokens) for it in items], ctx,
                          f"{sp['name']} scenes" + (f" for {label}" if label else ""), start, end)
        done += len(items)
        each(k, [s for s in got if s], g)
    return g


def index_of(idx: str, b: dict) -> np.ndarray:
    with np.errstate(all="ignore"):
        nd = lambda x, y: (b[x] - b[y]) / (b[x] + b[y])  # noqa: E731
        if idx == "ndvi":
            return nd("NIR", "Red")
        if idx == "evi":
            return 2.5 * (b["NIR"] - b["Red"]) / (b["NIR"] + 6 * b["Red"] - 7.5 * b["Blue"] + 1)
        if idx == "savi":
            return 1.5 * (b["NIR"] - b["Red"]) / (b["NIR"] + b["Red"] + 0.5)
        if idx == "msavi":
            n, r = b["NIR"], b["Red"]
            return (2 * n + 1 - np.sqrt(np.clip((2 * n + 1) ** 2 - 8 * (n - r), 0, None))) / 2
        if idx == "gndvi":
            return nd("NIR", "Green")
        if idx == "ndre":
            return nd("NIR", "RedEdge1")
        if idx == "ndwi":
            return nd("Green", "NIR")
        if idx == "mndwi":
            return nd("Green", "SWIR1")
        if idx == "ndmi":
            return nd("NIR", "SWIR1")
        if idx == "nbr":
            return nd("NIR", "SWIR2")
        if idx == "ndbi":
            return nd("SWIR1", "NIR")
        if idx == "bsi":
            a, c = b["SWIR1"] + b["Red"], b["NIR"] + b["Blue"]
            return (a - c) / (a + c)
    raise bad("That index is not known.", "index")


def index_composites(ctx: Ctx, p, periods, idx: str, bbox, how: str = "median") -> tuple[dict, list[np.ndarray], list[int]]:
    """The index of each period's composite; also how many scenes each period had."""
    sensor, src = sensor_of(p)
    spec = check_index(sensor, idx)
    outs: list = [None] * len(periods)
    counts = [0] * len(periods)

    def each(k, scenes, g):
        counts[k] = len(scenes)
        if scenes:
            outs[k] = index_of(idx, composite(scenes, spec["bands"], how)).astype(np.float32)
        else:
            outs[k] = np.full((g["height"], g["width"]), np.nan, np.float32)
    g = optical(ctx, sensor, src, periods, bbox, whole(p, "max_cloud", 0, 100, "Cloud", 50), whole(p, "scenes", 1, 30, "Scenes", 6),
                spec["bands"], each, extra_layers=len(periods))
    return g, outs, counts


# ---------- Sentinel-1 radar ----------
def find_radar(bbox, start, end, orbit: str, limit: int = 60) -> list[dict]:
    ds = DATASETS["sentinel1"]
    body = imagery._query(PC, ds, bbox, start, end, 100, limit)
    if orbit in ("ascending", "descending"):
        body.setdefault("query", {})["sat:orbit_state"] = {"eq": orbit}
    return imagery._stac_post(PC, body)


def read_radar(item: dict, pol: str, g: dict, tokens: dict) -> np.ndarray | None:
    try:
        href = imagery._signed(PC, "sentinel-1-rtc", tokens, item["assets"][pol]["href"])
        a, ok = read_onto(href, g)
    except KeyError:
        return None
    except rasterio.errors.RasterioError as e:
        log.warning("radar scene %s could not be read: %s", item.get("id"), e)
        return None
    v = a.astype(np.float32)
    v[~ok | ~(v > 0)] = np.nan
    return v if np.isfinite(v).any() else None


def db(linear: np.ndarray) -> np.ndarray:
    with np.errstate(all="ignore"):
        return (10 * np.log10(linear)).astype(np.float32)


# ---------- static layers: elevation, land cover, surface water ----------
def elevation(g: dict, bbox) -> np.ndarray:
    bands = [b for b in DATASETS["copdem"]["bands"]]
    items = imagery._stac_items(PC, "copdem", bbox, None, None, 0, [])
    data, _ = imagery.stac_mosaic(PC, "copdem", items, g, bands)
    z = data[0].astype(np.float32)
    z[z <= -32767] = np.nan
    return z


def land_cover(g: dict, bbox) -> np.ndarray:
    items = imagery._stac_items(PC, "worldcover", bbox, None, None, 0, [])
    data, _ = imagery.stac_mosaic(PC, "worldcover", items, g, DATASETS["worldcover"]["bands"])
    return data[0]


def surface_water(g: dict, bbox, what: str) -> np.ndarray:
    """A JRC Global Surface Water layer (1984-2020) on the grid: occurrence (% of time), seasonality (months of water in 2020),
    transitions (classes 1-10); NaN where none."""
    items = imagery._stac_post(PC, {"collections": ["jrc-gsw"], "bbox": list(bbox), "limit": 30})
    if not items:
        raise bad("The JRC surface water layers do not cover that area.")
    top = {"occurrence": 100, "seasonality": 12, "transitions": 10}[what]
    out = np.full((g["height"], g["width"]), np.nan, np.float32)
    tokens: dict = {}
    for it in items:
        try:
            a, ok = read_onto(imagery._signed(PC, "jrc-gsw", tokens, it["assets"][what]["href"]), g, nearest=True)
        except (KeyError, rasterio.errors.RasterioError):
            continue
        fill = ok & (a <= top) & np.isnan(out)
        out[fill] = a[fill]
    return out


# ---------- CHIRPS rainfall ----------
CHIRPS = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_monthly/cogs/chirps-v2.0.{y}.{m:02d}.cog"


def months(a: dt.date, b: dt.date) -> list[tuple[int, int]]:
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def rainfall(ctx: Ctx, bbox, wanted: list[tuple[int, int]], start: float = 0.05, end: float = 0.9) -> tuple[dict, dict[tuple, np.ndarray], list]:
    """CHIRPS monthly rainfall (mm) over the area, on CHIRPS's own 0.05 degree grid. Months not yet published are left out."""
    w, s, e, n = bbox
    if s < -50 or n > 50:
        raise bad("CHIRPS rainfall covers latitudes 50 degrees south to 50 degrees north only.", "bbox")
    grid: dict = {}

    def one(ym):
        y, m = ym
        try:
            with rasterio.Env(**GDAL_ENV), rasterio.open(CHIRPS.format(y=y, m=m)) as src:
                win = from_bounds(w, s, e, n, src.transform)
                c0, r0 = math.floor(win.col_off), math.floor(win.row_off)
                c1, r1 = max(c0 + 1, math.ceil(win.col_off + win.width)), max(r0 + 1, math.ceil(win.row_off + win.height))
                win = rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0)
                a = src.read(1, window=win, boundless=True, fill_value=-9999).astype(np.float32)
                if not grid:
                    grid.update({"crs": src.crs, "transform": src.window_transform(win), "width": c1 - c0, "height": r1 - r0})
        except rasterio.errors.RasterioError:
            return None
        a[a < 0] = np.nan
        return a
    got = in_parallel([lambda ym=ym: one(ym) for ym in wanted], ctx, "CHIRPS rainfall months", start, end)
    data = {ym: a for ym, a in zip(wanted, got) if a is not None}
    missing = [ym for ym, a in zip(wanted, got) if a is None]
    if not data:
        raise ImageryError("CHIRPS rainfall could not be read for those months (they may not be published yet, or the CHIRPS server "
                           "is not answering). Try earlier months, or try again later.", upstream=True)
    return grid, data, missing


def month_words(ym: tuple[int, int]) -> str:
    return f"{calendar.month_abbr[ym[1]]} {ym[0]}"


# =====================================================================================================================
# Outputs
# =====================================================================================================================
def ramp_style(name: str, lo: float, hi: float, invert: bool = False) -> dict:
    return {"type": "raster", "mode": "ramp", "band": 1, "min": float(f"{lo:.6g}"), "max": float(f"{hi:.6g}"), "ramp": name, "invert": invert, "opacity": 1}


def float_out(name: str, data: np.ndarray, g: dict, style: dict, summary: dict | None = None) -> rt.Out:
    data = clip_array(data, g)
    if summary and "mean" in summary:
        summary = {**summary, "mean": mean_of(data)}
    d = np.ma.masked_invalid(data.astype(np.float32))
    return rt.Out(name=name, data=d, crs=g["crs"], transform=g["transform"], dtype="float32", style=style, summary=summary or {})


def class_out(name: str, cls: np.ndarray, g: dict, classes: list[tuple[int, str, str]], summary: dict | None = None) -> rt.Out:
    """A class raster (0 = none) with its legend [(value, label, colour)], and the area of each class in hectares."""
    out = outside(g, cls.shape)
    if out is not None:
        cls = np.where(out, 0, cls)
    d = np.ma.masked_array(cls.astype(np.uint8), mask=cls == 0)
    ha = cell_ha(g)
    summary = dict(summary or {})
    for v, label, _ in classes:
        summary[f"{label} (ha)"] = round(float((cls == v).sum() * ha), 2)
    style = {"type": "raster", "mode": "classes", "band": 1, "opacity": 1,
             "categories": [{"value": v, "label": label, "color": c} for v, label, c in classes]}
    return rt.Out(name=name, data=d, crs=g["crs"], transform=g["transform"], dtype="uint8", categorical=True, style=style, summary=summary)


def cell_ha(g: dict) -> float:
    if CRS.from_user_input(g["crs"]).is_geographic:
        return 0.0
    return abs(g["transform"].a * g["transform"].e) / 10_000


def mean_of(a: np.ndarray) -> float | None:
    v = a[np.isfinite(a)]
    return round(float(v.mean()), 4) if v.size else None


def span_words(a: dt.date, b: dt.date) -> str:
    return f"{a.isoformat()} to {b.isoformat()}"


# =====================================================================================================================
# Places: points or polygons where values are summarised
# =====================================================================================================================
@dataclass
class Zone:
    fid: int | None
    label: str
    geom: object            # in WGS 84


def zones_of(ctx: Ctx, key: str = "zones", label_field: str | None = None, bbox=None) -> list[Zone]:
    if ctx.inputs.layers.get(key):
        feats = ctx.inputs.vector(key)
        if not feats:
            raise bad("The layer has no features.", key)
        if len(feats) > MAX_ZONES:
            raise bad(f"The layer has {len(feats)} features; a chart takes up to {MAX_ZONES}. Select some of them first.", key)
        return [Zone(f["id"], str(f["props"].get(label_field)) if label_field and f["props"].get(label_field) not in (None, "")
                     else f"Feature {f['id']}", f["geom"]) for f in feats]
    if get_clip() is not None:
        return [Zone(None, "The polygons", get_clip())]
    w, s, e, n = bbox
    return [Zone(None, "The area", shapely.box(w, s, e, n))]


def zones_bbox(zs: list[Zone], pad_deg: float = 0.0005) -> tuple:
    w, s, e, n = shapely.total_bounds([z.geom for z in zs])
    return (w - pad_deg, s - pad_deg, e + pad_deg, n + pad_deg)


def zone_masks(zs: list[Zone], g: dict) -> list[np.ndarray]:
    """Each place as the cells it covers (a point: its cell; a polygon: the cells whose centres fall in it, or the cells it
    touches when it is smaller than a cell)."""
    geoms = rt.to_crs([z.geom for z in zs], g["crs"])
    shape = (g["height"], g["width"])
    out = []
    for gg in geoms:
        m = np.zeros(shape, bool)
        if gg.geom_type in ("Point", "MultiPoint"):
            for pt in getattr(gg, "geoms", [gg]):
                r, c = rowcol(g["transform"], pt.x, pt.y)
                if 0 <= r < shape[0] and 0 <= c < shape[1]:
                    m[r, c] = True
        else:
            m = rfeatures.geometry_mask([gg], shape, g["transform"], invert=True)
            if not m.any():
                m = rfeatures.geometry_mask([gg], shape, g["transform"], invert=True, all_touched=True)
        out.append(m)
    return out


def place_pixels(geoms_wgs84: list, g: dict, valid: np.ndarray) -> list[np.ndarray]:
    """The (flat) indices of the valid cells under each point or polygon, worked out at once for many places."""
    shape = (g["height"], g["width"])
    geoms = rt.to_crs(geoms_wgs84, g["crs"])
    out: list[np.ndarray] = [np.zeros(0, np.int64)] * len(geoms)
    polys = [(gg, k + 1) for k, gg in enumerate(geoms) if gg.geom_type not in ("Point", "MultiPoint")]
    if polys:
        lab = rfeatures.rasterize(polys, out_shape=shape, transform=g["transform"], fill=0, dtype="int32").ravel()
        sel = np.flatnonzero((lab > 0) & valid.ravel())
        order = np.argsort(lab[sel], kind="stable")
        keys, starts = np.unique(lab[sel][order], return_index=True)
        for key, part in zip(keys, np.split(sel[order], starts[1:])):
            out[key - 1] = part
    for k, gg in enumerate(geoms):
        if gg.geom_type in ("Point", "MultiPoint"):
            cells = []
            for pt in getattr(gg, "geoms", [gg]):
                r, c = rowcol(g["transform"], pt.x, pt.y)
                if 0 <= r < shape[0] and 0 <= c < shape[1] and valid[r, c]:
                    cells.append(r * shape[1] + c)
            out[k] = np.array(cells, np.int64)
        elif not out[k].size:                      # smaller than a cell: the cells it touches
            m = rfeatures.geometry_mask([gg], shape, g["transform"], invert=True, all_touched=True) & valid
            out[k] = np.flatnonzero(m)
    return out


def zone_stats(a: np.ndarray, masks: list[np.ndarray]) -> list[float | None]:
    out = []
    for m in masks:
        v = a[m]
        v = v[np.isfinite(v)]
        out.append(float(v.mean()) if v.size else None)
    return out


def series_layer(ctx: Ctx, zs: list[Zone], x: list[str], values: list[list], name: str, unit: str) -> rt.Out | None:
    """The places with a field for each period, for the attribute table and export (only when they come from a layer)."""
    if zs[0].fid is None:
        return None
    lay = ctx.inputs.layers["zones"]
    gt = lay["geometry_type"]
    fields = [{"name": "place", "type": "string"}] + [{"name": k, "type": "number"} for k in x]
    rows = [(z.geom, {"place": z.label, **{k: (None if v is None else round(v, 4)) for k, v in zip(x, vals)}}) for z, vals in zip(zs, values)]
    return rt.Out(name=name, kind="vector", geometry_type=gt, fields=fields, rows=rows, summary={"unit": unit} if unit else {})


# =====================================================================================================================
# Images and indices
# =====================================================================================================================
METHOD = _p("method", "Combine the scenes by", "choice", default="median",
            choices=[["median", "Median (the usual cloud-free composite)"], ["greenest", "Greenest pixel (highest NDVI)"],
                     ["latest", "Most recent clear pixel"], ["mean", "Mean"], ["min", "Minimum"], ["max", "Maximum"],
                     ["percentile", "Percentile"]])
PCT = _p("percentile", "Percentile", "number", min=0, default=25, when={"method": "percentile"})


@etool("composite", "Cloud-free composite", "Images and indices",
       "One clear image of the area from many scenes: clouds and shadows masked pixel by pixel, then the scenes combined "
       "(median by default). Shown in true colour; all bands kept as surface reflectance x 10 000.",
       AREA, SENSOR, SOURCE, *PERIOD, CLOUD, SCENES, METHOD, PCT,
       _p("bands", "Bands", "choice", default="all", choices=[["all", "All the satellite's bands"], ["rgbn", "Red, green, blue and near-infrared"]]))
def composite_tool(p, ctx: Ctx) -> Result:
    sensor, src = sensor_of(p)
    a, b = span(p)
    bbox = area_of(p)
    how = p.get("method") or "median"
    if how not in {c[0] for c in METHOD["choices"]}:
        raise bad("Choose how to combine the scenes.", "method")
    pct = rt.num(p, "percentile", 0, 100, "Percentile", 25)
    bands = band_names(sensor) if p.get("bands", "all") == "all" else ["Red", "Green", "Blue", "NIR"]
    need = list(dict.fromkeys(bands + (["NIR", "Red"] if how == "greenest" else [])))
    got: dict = {}

    def each(k, scenes, g):
        if scenes:
            got.update(composite(scenes, need, how, pct))
            got["_n"] = len(scenes)
    g = optical(ctx, sensor, src, [(a, b, "")], bbox, whole(p, "max_cloud", 0, 100, "Cloud", 50), whole(p, "scenes", 1, 30, "Scenes", 6), need, each)
    if not got:
        raise bad("Every scene found was cloudy over the area. Choose a longer period.")
    ctx.say("Saving the composite", 0.95)
    stack = np.stack([got[x] for x in bands])
    clear = np.isfinite(stack).all(0)
    out = outside(g)
    if out is not None:
        clear &= ~out
    data = np.ma.masked_array(np.clip(np.rint(np.nan_to_num(stack) * 10000), 1, 65535).astype(np.uint16), mask=np.repeat(~clear[None], len(bands), 0))
    name = f"{SENSORS[sensor]['name']} {how} composite {span_words(a, b)}"
    summary = {"scenes": got["_n"], "dates": span_words(a, b), "method": how, "cell size m": g["cell"], "values": "surface reflectance x 10000",
               "clear %": round(100 * float(clear.sum()) / max(1, (clear.size if out is None else int((~out).sum()))), 1)}
    return Result([rt.Out(name=name, data=data, crs=g["crs"], transform=g["transform"], dtype="uint16", names=bands, summary=summary)])


@etool("index", "Spectral index", "Images and indices",
       "A vegetation, water, moisture, burn, built-up or soil index of the area from a cloud-free composite (NDVI, EVI, SAVI, "
       "MSAVI, GNDVI, NDRE, NDWI, MNDWI, NDMI, NBR, NDBI, BSI).",
       AREA, SENSOR, SOURCE, INDEX, *PERIOD, CLOUD, SCENES)
def index_tool(p, ctx: Ctx) -> Result:
    a, b = span(p)
    idx = p.get("index") or "ndvi"
    g, outs, counts = index_composites(ctx, p, [(a, b, "")], idx, area_of(p))
    v = outs[0]
    if not np.isfinite(v).any():
        raise bad("Every scene found was cloudy over the area. Choose a longer period.")
    spec = INDICES[idx]
    short = spec["name"].split(":")[0]
    return Result([float_out(f"{short} {span_words(a, b)}", v, g, ramp_style(spec["ramp"], *spec["range"]),
                             {"index": short, "mean": mean_of(v), "scenes": counts[0], "dates": span_words(a, b), "cell size m": g["cell"]})])


@etool("band_formula", "Band formula", "Images and indices",
       "Your own formula of the composite's bands, as reflectance from 0 to 1: e.g. (NIR - Red) / (NIR + Red), "
       "con(SWIR1 > NIR, 1, 0), or (Green - SWIR1) / (Green + SWIR1). Band names: Red, Green, Blue, NIR, SWIR1, SWIR2, and for "
       "Sentinel-2 also RedEdge1, RedEdge2, RedEdge3, NIR2.",
       AREA, SENSOR, SOURCE, _p("expression", "Formula", "text", default="(NIR - Red) / (NIR + Red)"), *PERIOD, CLOUD, SCENES)
def band_formula_tool(p, ctx: Ctx) -> Result:
    import ast
    sensor, src = sensor_of(p)
    expr = str(p.get("expression") or "").strip()
    if not expr or len(expr) > 1000:
        raise bad("Write a formula, e.g. (NIR - Red) / (NIR + Red).", "expression")
    try:
        names = {n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)}
    except SyntaxError:
        raise bad("The formula is not written right, e.g. (NIR - Red) / (NIR + Red).", "expression") from None
    have = band_names(sensor)
    use = [b for b in have if b in names]
    unknown = sorted(n for n in names - set(have) if n.lower() not in rt.FUNCS and n not in ("con", "isnull", "pi"))
    if unknown:
        raise bad(f"{unknown[0]} is not a band of {SENSORS[sensor]['name']}. Use: {', '.join(have)}.", "expression")
    if not use:
        raise bad("The formula uses none of the bands.", "expression")
    a, b = span(p)
    bbox = area_of(p)
    got: dict = {}

    def each(k, scenes, g):
        if scenes:
            got.update(composite(scenes, use))
    g = optical(ctx, sensor, src, [(a, b, "")], bbox, whole(p, "max_cloud", 0, 100, "Cloud", 50), whole(p, "scenes", 1, 30, "Scenes", 6), use, each)
    if not got:
        raise bad("Every scene found was cloudy over the area. Choose a longer period.")
    v = rt.evaluate(expr, {k: got[k].astype(np.float64) for k in use}).astype(np.float32)
    fin = v[np.isfinite(v)]
    lo, hi = (float(np.percentile(fin, 2)), float(np.percentile(fin, 98))) if fin.size else (0.0, 1.0)
    return Result([float_out(f"Formula {span_words(a, b)}", v, g, ramp_style("Viridis", lo, hi if hi > lo else lo + 1),
                             {"formula": expr, "mean": mean_of(v), "dates": span_words(a, b)})])


@etool("surface_temperature", "Land surface temperature", "Images and indices",
       "How hot the ground is (degrees Celsius) from Landsat 8 and 9's thermal band, clouds masked, scenes combined. "
       "Shows urban heat islands, dry land and water bodies.",
       AREA, *PERIOD, CLOUD, SCENES,
       _p("method", "Combine the scenes by", "choice", default="median", choices=[["median", "Median"], ["mean", "Mean"], ["max", "Maximum (hottest)"], ["min", "Minimum"]]))
def lst_tool(p, ctx: Ctx) -> Result:
    a, b = span(p)
    bbox = area_of(p)
    how = p.get("method") or "median"
    got: dict = {}

    def each(k, scenes, g):
        if scenes:
            got["ST"] = reduce(np.stack([s["ST"] for s in scenes]), how)
            got["_n"] = len(scenes)
    g = optical(ctx, "landsat", PC, [(a, b, "")], bbox, whole(p, "max_cloud", 0, 100, "Cloud", 50), whole(p, "scenes", 1, 30, "Scenes", 6), ["ST"], each)
    if not got:
        raise bad("Every Landsat scene found was cloudy over the area. Choose a longer period.")
    v = got["ST"]
    fin = v[np.isfinite(v)]
    lo, hi = (float(np.percentile(fin, 2)), float(np.percentile(fin, 98))) if fin.size else (20.0, 45.0)
    return Result([float_out(f"Surface temperature {span_words(a, b)}", v, g, ramp_style("Blue to red", lo, hi if hi > lo else lo + 1),
                             {"unit": "deg C", "mean": mean_of(v), "scenes": got["_n"], "dates": span_words(a, b)})])


@etool("rainfall", "Rainfall total", "Images and indices",
       "Rainfall (mm) over whole months from CHIRPS (satellite and rain gauges, about 5.5 km cells, 1981 to the last published month).",
       AREA, _p("start", "From month", "date", default={"ago": 400}), _p("end", "To month", "date", default={"ago": 45}))
def rainfall_tool(p, ctx: Ctx) -> Result:
    a, b = span(p, most=MAX_SERIES_DAYS)
    ms = months(a, b)
    if len(ms) > 72:
        raise bad("Choose six years or less.", "end")
    grid, data, missing = rainfall(ctx, area_of(p), ms)
    total = np.nansum(np.stack(list(data.values())), 0)
    total[np.all(np.isnan(np.stack(list(data.values()))), 0)] = np.nan
    first, last = month_words(min(data)), month_words(max(data))
    notes = [f"Not published yet, so left out: {', '.join(month_words(m) for m in missing)}."] if missing else []
    fin = total[np.isfinite(total)]
    return Result([float_out(f"Rainfall {first} to {last}", total, grid, ramp_style("Blues", float(fin.min()) if fin.size else 0, float(fin.max()) if fin.size else 1),
                             {"unit": "mm", "months": len(data), "mean": mean_of(total), "from": first, "to": last})], notes=notes)


@etool("terrain", "Terrain", "Images and indices",
       "Elevation (Copernicus 30 m), with slope, aspect and hillshade worked out from it.",
       AREA, _p("slope", "Slope (degrees)", "bool", default=True), _p("aspect", "Aspect", "bool", default=False),
       _p("hillshade", "Hillshade", "bool", default=True))
def terrain_tool(p, ctx: Ctx) -> Result:
    bbox = area_of(p)
    g = plan(bbox, 30, 4 * 6, "layers")
    ctx.say("Reading the elevation model", 0.3)
    z = elevation(g, bbox)
    G = grid_of(g, z)
    outs = [float_out("Elevation", z, g, ramp_style("Terrain", float(np.nanpercentile(z, 2)), float(np.nanpercentile(z, 98))),
                      {"unit": "m", "mean": mean_of(z), "source": "Copernicus DEM GLO-30"})]
    ctx.say("Working out the terrain", 0.8)
    gx, gy = rt.horn(G)
    if p.get("slope", True):
        s = np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)
        s[np.isnan(z)] = np.nan
        outs.append(float_out("Slope", s, g, ramp_style("Magma", 0, float(np.nanpercentile(s, 98)) or 1, invert=True), {"unit": "degrees", "mean": mean_of(s)}))
    if p.get("aspect"):
        asp = rt.aspect_of(gx, gy).astype(np.float32)
        asp[np.isnan(z)] = np.nan
        outs.append(float_out("Aspect", asp, g, ramp_style("Spectral", 0, 360), {"unit": "degrees from north"}))
    if p.get("hillshade", True):
        hs = rt.shade(G, 315, 45).astype(np.float32)
        hs[np.isnan(z)] = np.nan
        outs.append(float_out("Hillshade", hs, g, ramp_style("Gray", 0, 255)))
    return Result(outs)


WATER_TRANSITIONS = [(1, "Permanent", "#0000ff"), (2, "New permanent", "#22b14c"), (3, "Lost permanent", "#d1102d"), (4, "Seasonal", "#99d9ea"),
                     (5, "New seasonal", "#b5e61d"), (6, "Lost seasonal", "#e6a1aa"), (7, "Seasonal to permanent", "#ff7f27"),
                     (8, "Permanent to seasonal", "#ffc90e"), (9, "Ephemeral permanent", "#7f7f7f"), (10, "Ephemeral seasonal", "#c3c3c3")]


@etool("water_history", "Surface water history", "Images and indices",
       "Where water has been from 1984 to 2020 (JRC Global Surface Water, from every Landsat scene): how often, for how many "
       "months a year, or how it changed (new, lost, seasonal, permanent).",
       AREA, _p("what", "Show", "choice", default="occurrence", choices=[["occurrence", "Occurrence: % of the time with water"],
                                                                        ["seasonality", "Seasonality: months with water in 2020"],
                                                                        ["transitions", "Transitions: how the water changed"]]))
def water_history_tool(p, ctx: Ctx) -> Result:
    bbox = area_of(p)
    what = p.get("what") or "occurrence"
    if what not in ("occurrence", "seasonality", "transitions"):
        raise bad("Choose what to show.", "what")
    g = plan(bbox, 30, 4 * 3, "layers")
    ctx.say("Reading the JRC surface water layers", 0.4)
    v = surface_water(g, bbox, what)
    if what == "transitions":
        cls = np.nan_to_num(v, nan=0).astype(np.uint8)
        present = set(np.unique(cls)) - {0}
        return Result([class_out("Surface water transitions 1984-2020", cls, g, [t for t in WATER_TRANSITIONS if t[0] in present] or WATER_TRANSITIONS[:1])])
    v[v == 0] = np.nan
    name = "Surface water occurrence 1984-2020" if what == "occurrence" else "Months with water in 2020"
    return Result([float_out(name, v, g, ramp_style("Blues", 0, 100 if what == "occurrence" else 12), {"unit": "%" if what == "occurrence" else "months"})])


# =====================================================================================================================
# Time series and charts
# =====================================================================================================================
MEASURE = _p("measure", "Measure", "choice", default="index",
             choices=[["index", "A spectral index (NDVI, NDWI…)"], ["rainfall", "Rainfall (CHIRPS, mm a month)"],
                      ["temperature", "Land surface temperature (Landsat, deg C)"], ["radar_vv", "Radar backscatter VV (Sentinel-1, dB)"],
                      ["radar_vh", "Radar backscatter VH (Sentinel-1, dB)"]])
STEP = _p("step", "One value for each", "choice", default="month", choices=[["month", "Month"], ["year", "Year"], ["scene", "Scene (each date)"]])
ZONES = _p("zones", "Places (points or polygons; else the whole area)", "layer", geometry=["Point", "Polygon"], optional=True)
LABEL = _p("label", "Name the places by", "field", of="zones", optional=True)


def steps(a: dt.date, b: dt.date, step: str) -> list[tuple[dt.date, dt.date, str]]:
    out = []
    if step == "year":
        for y in range(a.year, b.year + 1):
            out.append((max(a, dt.date(y, 1, 1)), min(b, dt.date(y, 12, 31)), str(y)))
    else:
        for y, m in months(a, b):
            last = dt.date(y, m, calendar.monthrange(y, m)[1])
            out.append((max(a, dt.date(y, m, 1)), min(b, last), f"{y}-{m:02d}"))
    return out


@etool("time_series", "Time series chart", "Time series and change",
       "How a measure changed over time at your places (points or polygons, or the whole area): an index such as NDVI, rainfall, "
       "surface temperature or radar backscatter, as a chart, a table to download, and fields on the places.",
       AREA, MEASURE, _p("sensor", "Satellite", "choice", default="sentinel2", when={"measure": "index"}, choices=SENSOR["choices"]),
       {**SOURCE, "when": {"measure": "index", "sensor": "sentinel2"}}, {**INDEX, "when": {"measure": "index"}},
       _p("start", "From", "date", default={"ago": 730}), _p("end", "To", "date", default={"ago": 0}), STEP,
       {**CLOUD, "when": {"measure": ["index", "temperature"]}}, _p("scenes", "Scenes in each month or year (at most)", "number", min=1, default=3),
       ZONES, LABEL,
       _p("rasters", "Rasters", "choice", default="each", choices=[["each", "A raster layer for each interval"],
                                                                    ["stack", "One raster with a band for each interval"],
                                                                    ["none", "None (chart and table only)"]]))
def time_series_tool(p, ctx: Ctx) -> Result:
    measure = p.get("measure") or "index"
    a, b = span(p, most=MAX_SERIES_DAYS)
    step = p.get("step") or "month"
    if step not in ("month", "year", "scene"):
        raise bad("Choose a step.", "step")
    zs = zones_of(ctx, "zones", p.get("label"), area_of(p) if not ctx.inputs.layers.get("zones") else None)
    bbox = zones_bbox(zs)
    rasters_as = p.get("rasters") or "each"
    if rasters_as not in ("each", "stack", "none"):
        raise bad("Choose how to keep the rasters.", "rasters")
    if rasters_as != "none" and ctx.inputs.layers.get("zones", {}).get("geometry_type") == "Point":
        w, s_, e, n = area_of(p)                     # points: the rasters cover the area too, not only the points
        bw, bs, be, bn = bbox
        bbox = (min(w, bw), min(s_, bs), max(e, be), max(n, bn))
    per = whole(p, "scenes", 1, 30, "Scenes", 3)
    x: list[str] = []
    values: list[list] = [[] for _ in zs]
    notes: list[str] = []
    maps: list[tuple[str, np.ndarray, dict]] = []                # (interval, its raster, its grid) for the rasters
    keep = rasters_as != "none"

    if measure == "rainfall":
        if step == "scene":
            step = "month"
        ms = months(a, b)
        if len(ms) > 72:
            raise bad("Choose six years or less.", "end")
        grid, data, missing = rainfall(ctx, bbox, ms)
        masks = zone_masks(zs, grid)
        if step == "month":
            for ym in sorted(data):
                x.append(f"{ym[0]}-{ym[1]:02d}")
                for k, v in enumerate(zone_stats(data[ym], masks)):
                    values[k].append(v)
                if keep:
                    maps.append((x[-1], data[ym], grid))
        else:
            for y in sorted({ym[0] for ym in data}):
                got = np.stack([data[ym] for ym in data if ym[0] == y])
                x.append(str(y))
                tot = np.nansum(got, 0)
                tot[np.all(np.isnan(got), 0)] = np.nan
                for k, v in enumerate(zone_stats(tot, masks)):
                    values[k].append(v)
                if keep:
                    maps.append((x[-1], tot.astype(np.float32), grid))
            notes.append("A year's value is the total of its published months within the dates chosen.")
        if missing:
            notes.append(f"Not published yet, so left out: {', '.join(month_words(m) for m in missing)}.")
        unit, title = "mm", "Rainfall (CHIRPS)"

    elif measure in ("radar_vv", "radar_vh"):
        pol = measure[-2:]
        periods = steps(a, b, step) if step != "scene" else [(a, b, "")]
        found = in_parallel([lambda s=s, e=e: find_radar(bbox, s, e, "any", 60 if step == "scene" else per * 4) for s, e, _ in periods], ctx, "the catalogue", 0.02, 0.06)
        if step != "scene":
            found = [sorted(f, key=lambda i: i["properties"].get("datetime", ""))[-per * 4:] for f in found]
        total = sum(len(f) for f in found)
        if not total:
            raise bad("No Sentinel-1 radar scenes found for those dates. Choose a longer period.")
        if total > MAX_SCENES_READ:
            raise bad(f"That needs {total} radar scenes; the most for one run is {MAX_SCENES_READ}. Choose a shorter period or a step of a month.")
        g = plan(bbox, 10, total * 4 + 16 + (len(periods) * 4 if keep else 0))
        masks = zone_masks(zs, g)
        tokens: dict = {}
        warm(PC, "sentinel-1-rtc", tokens)
        flat = [(k, it) for k, f in enumerate(found) for it in f]
        got = in_parallel([lambda it=it: read_radar(it, pol, g, tokens) for _, it in flat], ctx, "radar scenes")
        if step == "scene":
            by_day: dict[str, list] = {}
            for (_, it), v in zip(flat, got):
                if v is not None:
                    by_day.setdefault(it["properties"].get("datetime", "")[:10], []).append(v)
            for d in sorted(by_day):
                x.append(d)
                st = reduce(np.stack(by_day[d]), "mean")
                for k, v in enumerate(zone_stats(st, masks)):
                    values[k].append(None if v is None else float(10 * np.log10(v)))
                if keep:
                    maps.append((d, db(st), g))
        else:
            for k, (s, e, label) in enumerate(periods):
                ims = [v for (j, _), v in zip(flat, got) if j == k and v is not None]
                x.append(label)
                st = reduce(np.stack(ims), "median") if ims else np.full((g["height"], g["width"]), np.nan)
                for z, v in enumerate(zone_stats(st, masks)):
                    values[z].append(None if v is None or v <= 0 else float(10 * np.log10(v)))
                if keep and ims:
                    maps.append((label, db(st), g))
        unit, title = "dB", f"Radar backscatter {pol.upper()} (Sentinel-1)"

    else:
        if measure == "temperature":
            sensor, src, bands, idx = "landsat", PC, ["ST"], None
        elif measure == "index":
            sensor, src = sensor_of(p)
            idx = p.get("index") or "ndvi"
            bands = check_index(sensor, idx)["bands"]
        else:
            raise bad("Choose a measure.", "measure")
        cloud = whole(p, "max_cloud", 0, 100, "Cloud", 50)
        masks_box: dict = {}

        def value_of(sc: dict) -> np.ndarray:
            return sc["ST"] if idx is None else index_of(idx, {k: sc[k] for k in bands})

        if step == "scene":
            ctx.say("Finding the scenes", 0.02)
            ds = DATASETS[SENSORS[sensor]["dataset"]]
            items = imagery._stac_post(src, imagery._query(src, ds, bbox, a, b, cloud, 100))
            items = sorted(items, key=lambda i: i["properties"].get("datetime", ""))
            if not items:
                raise bad("No scenes found for those dates. Choose a longer period or allow more cloud.")
            by_day: dict[str, list] = {}

            day_maps: dict[str, list] = {}

            def each(k, scenes, g):
                masks_box["m"] = zone_masks(zs, g)
                for sc in scenes:
                    v = value_of(sc)
                    by_day.setdefault(sc["_date"], []).append(zone_stats(v, masks_box["m"]))
                    if keep:
                        day_maps.setdefault(sc["_date"], []).append(v.astype(np.float32))
            g = plan(bbox, SENSORS[sensor]["res"], len(items) * len(bands) * 4 * 2 + (len(items) * 4 if keep else 0))
            tokens: dict = {}
            warm(src, DATASETS[SENSORS[sensor]["dataset"]]["stac"][src], tokens)
            got = in_parallel([lambda it=it: read_scene(sensor, src, it, bands, g, tokens) for it in items], ctx, f"{SENSORS[sensor]['name']} scenes")
            each(0, [s for s in got if s], g)
            for d in sorted(by_day):
                rows = by_day[d]
                x.append(d)
                for z in range(len(zs)):
                    vs = [r[z] for r in rows if r[z] is not None]
                    values[z].append(float(np.mean(vs)) if vs else None)
                if keep and day_maps.get(d):
                    maps.append((d, reduce(np.stack(day_maps[d]), "mean").astype(np.float32), g))   # tiles of one day joined
        else:
            periods = steps(a, b, step)
            series_vals: list = [None] * len(periods)

            def each(k, scenes, g):
                if "m" not in masks_box:
                    masks_box["m"] = zone_masks(zs, g)
                if scenes:
                    if idx is None:
                        v = reduce(np.stack([s["ST"] for s in scenes]), "median")
                    else:
                        v = index_of(idx, composite(scenes, bands))
                    series_vals[k] = zone_stats(v, masks_box["m"])
                    if keep:
                        period_maps[k] = (v.astype(np.float32), g)
            period_maps: dict[int, tuple] = {}
            optical(ctx, sensor, src, periods, bbox, cloud, per, bands, each, extra_layers=len(periods) if keep else 1, need_each=False)
            for k, (_, _, label) in enumerate(periods):
                x.append(label)
                for z in range(len(zs)):
                    values[z].append(series_vals[k][z] if series_vals[k] else None)
                if k in period_maps:
                    maps.append((label, *period_maps[k]))
        if idx is None:
            unit, title = "deg C", "Land surface temperature (Landsat)"
        else:
            unit, title = "", f"{INDICES[idx]['name'].split(':')[0]} ({SENSORS[sensor]['name']})"

    if not x or all(v is None for row in values for v in row):
        raise bad("No clear values were found for the places and dates. Choose a longer period or allow more cloud.")
    chart = {"title": title, "unit": unit, "x": x, "series": [{"name": z.label, "values": [None if v is None else round(v, 4) for v in row]}
                                                            for z, row in zip(zs, values)]}
    outs = [o for o in [series_layer(ctx, zs, x, values, f"{title} {span_words(a, b)}", unit)] if o]
    if keep and maps:
        outs += interval_rasters(maps, title, unit, rasters_as, measure, p.get("index") or "ndvi")
        missing = [lbl for lbl in x if lbl not in {m[0] for m in maps}]
        if missing:
            notes.append(f"No raster for {', '.join(missing)}: no clear scene then.")
    return Result(outs, chart=chart, notes=notes)


MAX_INTERVAL_LAYERS = 36


def interval_rasters(maps: list[tuple[str, np.ndarray, dict]], title: str, unit: str, how: str, measure: str, idx: str) -> list[rt.Out]:
    """The time series as rasters: one layer per interval, or one layer with a band per interval, all drawn on the same scale
    so the intervals can be compared by eye."""
    if measure == "index":
        spec = INDICES[idx]
        lo, hi, ramp = spec["range"][0], spec["range"][1], spec["ramp"]
    else:
        allv = np.concatenate([clip_array(a, g)[np.isfinite(clip_array(a, g))].ravel() for _, a, g in maps]) if maps else np.zeros(1)
        lo, hi = (float(np.percentile(allv, 2)), float(np.percentile(allv, 98))) if allv.size else (0.0, 1.0)
        ramp = {"rainfall": "Blues", "temperature": "Blue to red"}.get(measure, "Gray")
    style = ramp_style(ramp, lo, hi if hi > lo else lo + 1)
    if how == "each":
        if len(maps) > MAX_INTERVAL_LAYERS:
            raise bad(f"That makes {len(maps)} rasters; the most as separate layers is {MAX_INTERVAL_LAYERS}. Choose \"One raster with a band "
                      "for each interval\", a step of a year, or a shorter period.", "rasters")
        return [float_out(f"{title} {label}", a, g, style, {"interval": label, "unit": unit, "mean": 0}) for label, a, g in maps]
    g = maps[0][2]
    stack = np.stack([clip_array(a, g) for _, a, _ in maps]).astype(np.float32)
    return [rt.Out(name=f"{title} by interval", data=np.ma.masked_invalid(stack), crs=g["crs"], transform=g["transform"], dtype="float32",
                   names=[label for label, _, _ in maps], style=style,
                   summary={"intervals": len(maps), "first": maps[0][0], "last": maps[-1][0], "unit": unit, "bands": "one per interval"})]


def same_window_in(year: int, a: dt.date, b: dt.date) -> tuple[dt.date, dt.date]:
    def move(d: dt.date, y: int) -> dt.date:
        return dt.date(y, d.month, min(d.day, calendar.monthrange(y, d.month)[1]))
    shift = year - a.year
    return move(a, a.year + shift), move(b, b.year + shift)


@etool("trend", "Trend over the years", "Time series and change",
       "Where an index has been rising or falling year after year: a composite for the same season of each year, then the "
       "straight-line slope through them at every pixel (change per year). Greening, degradation, drying, urban growth.",
       AREA, SENSOR, SOURCE, INDEX, _p("first_year", "From year", "number", min=1985, default=dt.date.today().year - 6),
       _p("last_year", "To year", "number", min=1985, default=dt.date.today().year - 1),
       _p("from_month", "Season: from month (1-12)", "number", min=1, default=1), _p("to_month", "Season: to month (1-12)", "number", min=1, default=12),
       CLOUD, _p("scenes", "Scenes for each year (at most, for each tile)", "number", min=1, default=6))
def trend_tool(p, ctx: Ctx) -> Result:
    y0, y1 = whole(p, "first_year", 1985, 2100, "From year", 2019), whole(p, "last_year", 1985, 2100, "To year", 2025)
    if y1 - y0 < 2:
        raise bad("Choose at least three years.", "last_year")
    if y1 - y0 > 11:
        raise bad("Choose twelve years or fewer.", "first_year")
    m0, m1 = whole(p, "from_month", 1, 12, "From month", 1), whole(p, "to_month", 1, 12, "To month", 12)
    if y1 > dt.date.today().year:
        raise bad("The last year is in the future.", "last_year")
    periods = []
    for y in range(y0, y1 + 1):
        a = dt.date(y, m0, 1)
        b = dt.date(y if m1 >= m0 else y + 1, m1, calendar.monthrange(y if m1 >= m0 else y + 1, m1)[1])
        periods.append((a, min(b, dt.date.today()), str(y)))
    idx = p.get("index") or "ndvi"
    g, outs, counts = index_composites(ctx, p, periods, idx, area_of(p))
    ctx.say("Fitting the trend", 0.93)
    years = np.array([int(lbl) for _, _, lbl in periods], np.float64)
    st = np.stack(outs).astype(np.float64)
    ok = np.isfinite(st)
    n = ok.sum(0)
    t = np.where(ok, years[:, None, None], np.nan)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mt, my = np.nanmean(t, 0), np.nanmean(st, 0)
        slope = np.nansum((t - mt) * (st - my), 0) / np.nansum((t - mt) ** 2, 0)
    slope[n < 3] = np.nan
    slope = slope.astype(np.float32)
    fin = slope[np.isfinite(slope)]
    lim = float(np.percentile(np.abs(fin), 98)) if fin.size else 0.02
    short = INDICES[idx]["name"].split(":")[0]
    summary = {"index": short, "years": f"{y0} to {y1}", "season": f"months {m0} to {m1}", "unit": f"{short} per year", "mean slope": mean_of(slope),
               "rising %": round(100 * float((fin > 0).mean()), 1) if fin.size else None, "years without scenes": sum(1 for c in counts if not c)}
    return Result([float_out(f"{short} trend {y0}-{y1}", slope, g, ramp_style("Red to green", -lim or -0.01, lim or 0.01), summary)])


@etool("anomaly", "Anomaly against earlier years", "Time series and change",
       "How a period compares with the same dates in earlier years: an index (e.g. NDVI now minus its average) or rainfall "
       "(mm above or below normal, and % of normal). Drought, crop failure, unusual wet seasons.",
       AREA, _p("measure", "Measure", "choice", default="index", choices=[["index", "A spectral index"], ["rainfall", "Rainfall (CHIRPS)"]]),
       {**SENSOR, "when": {"measure": "index"}}, {**SOURCE, "when": {"measure": "index", "sensor": "sentinel2"}}, {**INDEX, "when": {"measure": "index"}},
       *dates("", "Period", 0, 90), _p("years", "Earlier years to compare with", "number", min=1, default=5),
       {**CLOUD, "when": {"measure": "index"}}, {**_p("scenes", "Scenes for each year (at most, for each tile)", "number", min=1, default=6), "when": {"measure": "index"}})
def anomaly_tool(p, ctx: Ctx) -> Result:
    a, b = span(p, label="Period")
    bbox = area_of(p)
    if (p.get("measure") or "index") == "rainfall":
        n = whole(p, "years", 1, 30, "Years", 10)
        ms = months(a, b)
        earlier = [(y - k, m) for k in range(1, n + 1) for y, m in ms]
        grid, data, missing = rainfall(ctx, bbox, ms + earlier)
        now = [data[m] for m in ms if m in data]
        if not now:
            raise bad("The months chosen are not published yet. Choose earlier months.")
        total = np.nansum(np.stack(now), 0)
        normals = []
        for k in range(1, n + 1):
            yr = [data.get((y - k, m)) for y, m in ms]
            if all(v is not None for v in yr):
                normals.append(np.nansum(np.stack(yr), 0))
        if not normals:
            raise bad("The earlier years could not be read. Try again later.")
        normal = np.mean(np.stack(normals), 0)
        diff = (total - normal).astype(np.float32)
        with np.errstate(all="ignore"):
            pct = (100 * total / normal).astype(np.float32)
        pct[~np.isfinite(pct)] = np.nan
        lim = float(np.nanmax(np.abs(diff))) or 1
        span_m = f"{month_words(ms[0])} to {month_words(ms[-1])}"
        notes = [f"Left out (not published): {', '.join(month_words(m) for m in missing)}."] if missing else []
        return Result([float_out(f"Rainfall anomaly {span_m}", diff, grid, ramp_style("Blue to red", -lim, lim, invert=True),
                                 {"unit": "mm", "mean": mean_of(diff), "compared with": f"{len(normals)} earlier years"}),
                       float_out(f"Rainfall % of normal {span_m}", pct, grid, ramp_style("Blue to red", 50, 150, invert=True),
                                 {"unit": "% of normal", "mean": mean_of(pct)})], notes=notes)
    n = whole(p, "years", 1, 10, "Years", 5)
    periods = [(a, b, "the period")] + [(*same_window_in(a.year - k, a, b), str(a.year - k)) for k in range(1, n + 1)]
    idx = p.get("index") or "ndvi"
    g, outs, counts = index_composites(ctx, p, periods, idx, bbox)
    if not counts[0]:
        raise bad("No clear scenes for the period chosen. Choose a longer period.")
    base = [o for o, c in zip(outs[1:], counts[1:]) if c]
    if not base:
        raise bad("No clear scenes in the earlier years. Choose fewer years or a longer period.")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(np.stack(base), 0)
    diff = (outs[0] - mean).astype(np.float32)
    short = INDICES[idx]["name"].split(":")[0]
    return Result([float_out(f"{short} anomaly {span_words(a, b)}", diff, g, ramp_style("Red to green", -0.3, 0.3),
                             {"index": short, "mean": mean_of(diff), "compared with": f"{len(base)} earlier years", "dates": span_words(a, b)})])


CHANGE_PRESETS = {"ndvi": ("Vegetation", "Vegetation loss", "Vegetation gain"), "ndbi": ("Built-up", "Less built-up", "More built-up"),
                  "mndwi": ("Water", "Less water", "More water"), "ndmi": ("Moisture", "Drier", "Wetter")}


@etool("change", "Change between two dates", "Time series and change",
       "Where an index rose or fell between a before and an after period (e.g. NDVI for deforestation or new farms, NDBI for "
       "urban growth), with the area of each kind of change.",
       AREA, SENSOR, SOURCE, INDEX, *BEFORE, *AFTER, _p("threshold", "Count as change when the index moves by more than", "number", min=0, default=0.15),
       CLOUD, SCENES)
def change_tool(p, ctx: Ctx) -> Result:
    a0, b0 = span(p, "before_", "Before")
    a1, b1 = span(p, "after_", "After")
    idx = p.get("index") or "ndvi"
    thr = rt.num(p, "threshold", 0, 2, "Threshold", 0.15)
    g, outs, counts = index_composites(ctx, p, [(a0, b0, "before"), (a1, b1, "after")], idx, area_of(p))
    diff = (outs[1] - outs[0]).astype(np.float32)
    ok = np.isfinite(diff)
    cls = np.zeros(diff.shape, np.uint8)
    cls[ok & (diff < -thr)] = 1
    cls[ok & (np.abs(diff) <= thr)] = 2
    cls[ok & (diff > thr)] = 3
    short = INDICES[idx]["name"].split(":")[0]
    what, down, up = CHANGE_PRESETS.get(idx, (short, f"{short} fell", f"{short} rose"))
    lim = max(thr * 2, 0.2)
    sm = {"index": short, "before": span_words(a0, b0), "after": span_words(a1, b1), "threshold": thr}
    return Result([float_out(f"{short} change", diff, g, ramp_style("Red to green", -lim, lim, invert=idx == "ndbi"), {**sm, "mean": mean_of(diff)}),
                   class_out(f"{what} change", cls, g, [(1, down, "#d7191c"), (2, "No change", "#e8e8e8"), (3, up, "#1a9641")], sm)])


BURN = [(1, "Enhanced regrowth, high", "#7a8737"), (2, "Enhanced regrowth, low", "#acbe4d"), (3, "Unburned", "#0ae042"),
        (4, "Low severity", "#fff70b"), (5, "Moderate-low severity", "#ffaf38"), (6, "Moderate-high severity", "#ff641b"), (7, "High severity", "#a41fd6")]
BURN_EDGES = [-0.251, -0.101, 0.1, 0.27, 0.44, 0.66]        # USGS dNBR classes (Key and Benson)


@etool("burn_severity", "Burn severity", "Time series and change",
       "How badly a fire burnt: dNBR (NBR before minus NBR after) in the USGS severity classes, with the burnt area.",
       AREA, SENSOR, SOURCE, *dates("before_", "Before the fire", 60, 60), *dates("after_", "After the fire", 0, 45), CLOUD, SCENES)
def burn_tool(p, ctx: Ctx) -> Result:
    a0, b0 = span(p, "before_", "Before")
    a1, b1 = span(p, "after_", "After")
    g, outs, _ = index_composites(ctx, p, [(a0, b0, "before"), (a1, b1, "after")], "nbr", area_of(p))
    dnbr = (outs[0] - outs[1]).astype(np.float32)
    ok = np.isfinite(dnbr)
    cls = np.where(ok, np.digitize(dnbr, BURN_EDGES) + 1, 0).astype(np.uint8)
    burnt = float((ok & (dnbr >= 0.1)).sum() * cell_ha(g))
    sm = {"before": span_words(a0, b0), "after": span_words(a1, b1), "burnt area (ha)": round(burnt, 2)}
    return Result([class_out("Burn severity", cls, g, BURN, sm),
                   float_out("dNBR", dnbr, g, ramp_style("Magma", -0.25, 0.8, invert=True), {"mean": mean_of(dnbr)})])


@etool("water_change", "Water change", "Time series and change",
       "Open water before and after (MNDWI above a threshold): water that stayed, appeared or went. Dams, rivers, lakes, "
       "seasonal flooding, mining pits.",
       AREA, SENSOR, SOURCE, *BEFORE, *AFTER, _p("threshold", "Water where MNDWI is above", "number", default=0), CLOUD, SCENES)
def water_change_tool(p, ctx: Ctx) -> Result:
    a0, b0 = span(p, "before_", "Before")
    a1, b1 = span(p, "after_", "After")
    thr = rt.num(p, "threshold", -1, 1, "Threshold", 0)
    g, outs, _ = index_composites(ctx, p, [(a0, b0, "before"), (a1, b1, "after")], "mndwi", area_of(p))
    ok = np.isfinite(outs[0]) & np.isfinite(outs[1])
    w0, w1 = outs[0] > thr, outs[1] > thr
    cls = np.zeros(w0.shape, np.uint8)
    cls[ok & w0 & w1] = 1
    cls[ok & ~w0 & w1] = 2
    cls[ok & w0 & ~w1] = 3
    return Result([class_out("Water change", cls, g, [(1, "Water both times", "#0064c8"), (2, "New water", "#00c8ff"), (3, "Water gone", "#d7191c")],
                             {"before": span_words(a0, b0), "after": span_words(a1, b1), "MNDWI threshold": thr})])


@etool("flood", "Flood map (radar)", "Time series and change",
       "Flooded land from Sentinel-1 radar, which sees through cloud: UN-SPIDER's method (after/before backscatter ratio above "
       "1.25, without permanent water, steep slopes and specks), with the flooded area.",
       AREA, *dates("before_", "Before the flood", 45, 30), *dates("after_", "During the flood", 0, 12),
       _p("polarisation", "Polarisation", "choice", default="vh", choices=[["vh", "VH (recommended)"], ["vv", "VV"]]),
       _p("orbit", "Satellite pass", "choice", default="auto", choices=[["auto", "Whichever has scenes both times (descending first)"],
                                                                        ["descending", "Descending"], ["ascending", "Ascending"]]),
       _p("threshold", "Flooded where the ratio is above", "number", min=1, default=1.25))
def flood_tool(p, ctx: Ctx) -> Result:
    a0, b0 = span(p, "before_", "Before")
    a1, b1 = span(p, "after_", "During")
    bbox = area_of(p)
    pol = p.get("polarisation") or "vh"
    thr = rt.num(p, "threshold", 1, 5, "Threshold", 1.25)
    ctx.say("Finding Sentinel-1 scenes", 0.02)
    orbit = p.get("orbit") or "auto"
    tries = ["descending", "ascending"] if orbit == "auto" else [orbit]
    before = after = []
    for o in tries:
        before, after = find_radar(bbox, a0, b0, o, 20), find_radar(bbox, a1, b1, o, 20)
        if before and after:
            orbit = o
            break
    if not before or not after:
        raise bad("No Sentinel-1 scenes from the same satellite pass were found both before and during the flood. Widen the dates.")
    g = plan(bbox, 10, (len(before) + len(after)) * 4 + 40, "radar scenes")
    tokens: dict = {}
    warm(PC, "sentinel-1-rtc", tokens)
    got = in_parallel([lambda it=it: read_radar(it, pol, g, tokens) for it in before + after], ctx, "radar scenes", 0.06, 0.7)
    pre = [v for v in got[:len(before)] if v is not None]
    post = [v for v in got[len(before):] if v is not None]
    if not pre or not post:
        raise bad("The radar scenes found hold no data over the area. Widen the dates.")
    ctx.say("Comparing before and during", 0.75)
    cell = g["cell"]
    r = max(1, round(50 / cell))

    def smooth(x):            # a 50 m circle's mean, ignoring empty cells
        fp = rt.footprint(r, True).astype(np.float64)
        ok = np.isfinite(x)
        s = ndimage.convolve(np.where(ok, x, 0.0), fp, mode="nearest")
        c = ndimage.convolve(ok.astype(np.float64), fp, mode="nearest")
        with np.errstate(all="ignore"):
            return np.where(ok, s / c, np.nan)
    bdb, adb = smooth(db(reduce(np.stack(pre), "median")).astype(np.float64)), smooth(db(reduce(np.stack(post), "median")).astype(np.float64))
    with np.errstate(all="ignore"):
        ratio = (adb / bdb).astype(np.float32)
    flooded = np.isfinite(ratio) & (ratio > thr) & (adb < bdb)
    notes = []
    ctx.say("Removing permanent water and steep slopes", 0.82)
    try:
        season = surface_water(g, bbox, "seasonality")
        flooded &= ~(season >= 10)
        water_note = "permanent water (10 or more months a year, JRC) left out"
    except (Exception,):  # noqa: BLE001 - fall back to WorldCover's permanent water class
        flooded &= land_cover(g, bbox) != 80
        water_note = "permanent water (ESA WorldCover) left out"
    z = elevation(g, bbox)
    gx, gy = rt.horn(grid_of(g, z))
    flooded &= ~(np.degrees(np.arctan(np.hypot(gx, gy))) >= 5)
    lab, k = ndimage.label(flooded, structure=np.ones((3, 3)))
    if k:
        sizes = ndimage.sum(flooded, lab, index=np.arange(1, k + 1))
        flooded &= np.isin(lab, np.flatnonzero(sizes >= 8) + 1)
    ha = float(flooded.sum() * cell_ha(g))
    notes.append(f"Pass: {orbit}; {len(pre)} scenes before, {len(post)} during; {water_note}.")
    sm = {"flooded area (ha)": round(ha, 2), "before": span_words(a0, b0), "during": span_words(a1, b1), "polarisation": pol.upper(),
          "pass": orbit, "threshold": thr}
    return Result([class_out("Flooded", flooded.astype(np.uint8), g, [(1, "Flooded", "#0050ff")], sm),
                   float_out("Backscatter ratio (during / before, dB)", ratio, g, ramp_style("Blues", 0.8, 1.6), {"threshold": thr})], notes=notes)


# =====================================================================================================================
# Classification
# =====================================================================================================================
EXTRA_INDICES = ["ndvi", "ndwi", "ndbi", "mndwi"]


def features_for(ctx: Ctx, p, bbox) -> tuple[dict, np.ndarray, list[str]]:
    """The composite's bands and a few indices (optionally elevation and slope) as the pixels' features: (rows, cols, k)."""
    sensor, src = sensor_of(p)
    a, b = span(p)
    bands = band_names(sensor)
    got: dict = {}

    def each(k, scenes, g):
        if scenes:
            got.update(composite(scenes, bands))
    g = optical(ctx, sensor, src, [(a, b, "")], bbox, whole(p, "max_cloud", 0, 100, "Cloud", 50), whole(p, "scenes", 1, 30, "Scenes", 6), bands, each,
                extra_layers=len(bands) + 8)
    if not got:
        raise bad("Every scene found was cloudy over the area. Choose a longer period.")
    names = list(bands)
    layers = [got[x] for x in bands]
    if p.get("indices", True):
        for i in EXTRA_INDICES:
            layers.append(index_of(i, got).astype(np.float32))
            names.append(i.upper())
    if p.get("terrain"):
        ctx.say("Adding elevation and slope", 0.9)
        z = elevation(g, bbox)
        gx, gy = rt.horn(grid_of(g, z))
        layers += [z, np.degrees(np.arctan(np.hypot(gx, gy))).astype(np.float32)]
        names += ["Elevation", "Slope"]
    return g, np.stack(layers, -1), names


METHODS = [["rf", "Random forest"], ["cart", "Decision tree (CART)"], ["svm", "Support vector machine"], ["nb", "Naive Bayes"],
           ["mindist", "Minimum distance"]]


@etool("classify", "Land cover classification", "Classification",
       "Every pixel given a class (e.g. water, forest, farmland, built-up) learnt from your training points or polygons, with a "
       "random forest, decision tree, SVM, naive Bayes or minimum distance; some training places are held back to report the "
       "accuracy (overall, kappa, and by class), and the area of each class.",
       AREA, SENSOR, SOURCE, *PERIOD, CLOUD, SCENES,
       _p("training", "Training places (points or polygons)", "layer", geometry=["Point", "Polygon"]),
       _p("field", "Class field", "field", of="training"),
       _p("method", "Classifier", "choice", default="rf", choices=METHODS),
       _p("trees", "Trees", "number", min=10, default=100, when={"method": "rf"}),
       _p("test", "Hold back for checking", "number", unit="%", min=0, default=30),
       _p("indices", "Add NDVI, NDWI, NDBI and MNDWI", "bool", default=True), _p("terrain", "Add elevation and slope", "bool", default=False))
def classify_tool(p, ctx: Ctx) -> Result:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.naive_bayes import GaussianNB
    from sklearn.neighbors import NearestCentroid
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import SVC
    from sklearn.tree import DecisionTreeClassifier

    fld = p.get("field")
    if not fld:
        raise bad("Choose the class field.", "field")
    feats = ctx.inputs.vector("training")
    labelled = [(f, f["props"].get(fld)) for f in feats if f["props"].get(fld) not in (None, "")]
    names = sorted({str(v) for _, v in labelled}, key=lambda s: (0, float(s)) if _num(s) else (1, s))
    if len(names) < 2:
        raise bad("The training places need at least two classes in the class field.", "field")
    if len(names) > 50:
        raise bad("The class field has more than 50 classes.", "field")
    code = {n: i + 1 for i, n in enumerate(names)}
    bbox = area_of(p)
    g, X, fnames = features_for(ctx, p, bbox)
    ctx.say("Taking the training pixels", 0.92)
    H, W, K = X.shape
    valid = np.isfinite(X).all(-1)
    shape = (H, W)
    rng = np.random.default_rng(1)
    samples: list[tuple[int, int, np.ndarray]] = []         # (class, place, pixel indices)
    for k, (idx, (f, v)) in enumerate(zip(place_pixels([f["geom"] for f, _ in labelled], g, valid), labelled)):
        if idx.size > 2000:
            idx = rng.choice(idx, 2000, replace=False)
        if idx.size:
            samples.append((code[str(v)], k, idx))
    unused = len(labelled) - len(samples)
    by_class = {c: [s for s in samples if s[0] == c] for c in code.values()}
    empty = [names[c - 1] for c, s in by_class.items() if not s]
    if empty:
        raise bad(f"No clear pixels under the training places of class {empty[0]}: check they are inside the area, or choose dates with less cloud.", "training")
    test_frac = rt.num(p, "test", 0, 90, "Hold back", 30) / 100
    train_i, train_y, test_i, test_y = [], [], [], []
    pixel_split = []
    for c, ss in by_class.items():
        order = rng.permutation(len(ss))
        n_test = int(round(len(ss) * test_frac)) if len(ss) >= 2 else 0
        if test_frac > 0 and len(ss) >= 2:
            n_test = min(max(1, n_test), len(ss) - 1)
        for j, o in enumerate(order):
            _, _, idx = ss[o]
            if test_frac > 0 and len(ss) < 2:              # one place only: split its pixels
                cut = rng.permutation(idx)
                t = int(round(len(cut) * test_frac))
                test_i.append(cut[:t]); test_y += [c] * t
                train_i.append(cut[t:]); train_y += [c] * (len(cut) - t)
                pixel_split.append(names[c - 1])
            elif j < n_test:
                test_i.append(idx); test_y += [c] * idx.size
            else:
                train_i.append(idx); train_y += [c] * idx.size
    flat = X.reshape(-1, K)
    tr = np.concatenate(train_i)
    Xtr, ytr = flat[tr], np.array(train_y)
    method = p.get("method") or "rf"
    if method == "svm" and len(ytr) > 6000:
        keep = rng.choice(len(ytr), 6000, replace=False)
        Xtr, ytr = Xtr[keep], ytr[keep]
    model = {"rf": lambda: RandomForestClassifier(n_estimators=whole(p, "trees", 10, 500, "Trees", 100), n_jobs=2, random_state=1),
             "cart": lambda: DecisionTreeClassifier(random_state=1),
             "svm": lambda: make_pipeline(StandardScaler(), SVC(kernel="rbf", gamma="scale", C=10)),
             "nb": lambda: GaussianNB(),
             "mindist": lambda: make_pipeline(StandardScaler(), NearestCentroid())}.get(method)
    if not model:
        raise bad("Choose a classifier.", "method")
    ctx.say(f"Training the {dict(METHODS)[method].lower()}", 0.94)
    clf = model().fit(Xtr, ytr)
    ctx.say("Classifying every pixel", 0.96)
    out = np.zeros(H * W, np.uint8)
    vi = np.flatnonzero(valid.ravel())
    for s in range(0, vi.size, 400_000):
        out[vi[s:s + 400_000]] = clf.predict(flat[vi[s:s + 400_000]])
    cls = out.reshape(shape)
    report = None
    summary: dict = {"classifier": dict(METHODS)[method], "training pixels": int(len(ytr)), "features": ", ".join(fnames)}
    if test_i:
        te = np.concatenate(test_i)
        yt, yp = np.array(test_y), clf.predict(flat[te])
        report = accuracy(yt, yp, names)
        if pixel_split:
            report["note"] = (f"{', '.join(pixel_split)} had a single training place, so its own pixels were split: the accuracy for it "
                              "is likely too high. Add more places for each class.")
        summary.update({"overall accuracy %": report["overall"], "kappa": report["kappa"], "checked pixels": int(len(yt))})
    if hasattr(clf, "feature_importances_"):
        imp = clf.feature_importances_
        summary["most useful"] = ", ".join(fnames[i] for i in np.argsort(imp)[::-1][:4])
    from .raster_style import CATEGORY
    classes = [(code[n], n, CATEGORY[(code[n] - 1) % len(CATEGORY)]) for n in names]
    o = class_out(f"Land cover ({dict(METHODS)[method].lower()})", cls, g, classes, summary)
    notes = [f"{unused} of the {len(labelled)} training places were outside the area or under cloud, so not used: choose an area that "
             "covers them all (e.g. the training layer's extent)."] if unused else []
    return Result([o], report=report, notes=notes)


def _num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def accuracy(yt: np.ndarray, yp: np.ndarray, names: list[str]) -> dict:
    k = len(names)
    m = np.zeros((k, k), int)
    for t, q in zip(yt, yp):
        m[t - 1, q - 1] += 1
    n = m.sum()
    po = np.trace(m) / n if n else 0.0
    pe = float((m.sum(0) * m.sum(1)).sum()) / (n * n) if n else 0.0
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    with np.errstate(all="ignore"):
        prod = np.diag(m) / m.sum(1)
        user = np.diag(m) / m.sum(0)
    pc = lambda v: None if not np.isfinite(v) else round(100 * float(v), 1)  # noqa: E731
    return {"classes": names, "matrix": m.tolist(), "overall": pc(po), "kappa": round(float(kappa), 3),
            "producers": [pc(v) for v in prod], "users": [pc(v) for v in user]}


@etool("cluster", "Unsupervised classification", "Classification",
       "The area's pixels grouped into classes of similar reflectance (k-means on the composite's bands and indices), no training "
       "needed: name the classes afterwards in the layer's properties.",
       AREA, SENSOR, SOURCE, *PERIOD, CLOUD, SCENES, _p("classes", "Classes", "number", min=2, default=8),
       _p("indices", "Add NDVI, NDWI, NDBI and MNDWI", "bool", default=True))
def cluster_tool(p, ctx: Ctx) -> Result:
    from scipy.cluster.vq import kmeans2, vq
    k = whole(p, "classes", 2, 30, "Classes", 8)
    g, X, fnames = features_for(ctx, p, area_of(p))
    ctx.say("Grouping the pixels", 0.93)
    H, W, K = X.shape
    flat = X.reshape(-1, K)
    ok = np.isfinite(flat).all(1)
    Z = flat[ok]
    if len(Z) < k:
        raise bad("Too few clear pixels for that many classes.")
    mu, sd = Z.mean(0), Z.std(0) + 1e-9
    Zs = (Z - mu) / sd
    rng = np.random.default_rng(1)
    sample = Zs[rng.choice(len(Zs), min(len(Zs), 200_000), replace=False)]
    cent, _ = kmeans2(sample, k, minit="++", seed=1, iter=30)
    lab, _ = vq(Zs, cent)
    order = np.argsort(cent[:, fnames.index("NDVI")] if "NDVI" in fnames else cent.mean(1))
    rank = np.empty(k, int)
    rank[order] = np.arange(1, k + 1)
    out = np.zeros(H * W, np.uint8)
    out[ok] = rank[lab]
    from .raster_style import CATEGORY
    return Result([class_out(f"{k} classes (k-means)", out.reshape(H, W), g, [(i, f"Class {i}", CATEGORY[(i - 1) % len(CATEGORY)]) for i in range(1, k + 1)],
                             {"features": ", ".join(fnames), "note": "numbered from least to most green" if "NDVI" in fnames else ""})])


# =====================================================================================================================
# Statistics for places
# =====================================================================================================================
@etool("zonal_index", "Index statistics by polygon", "Statistics",
       "For each polygon (farms, plots, wards…): the mean, minimum, maximum, spread and median of an index from a cloud-free "
       "composite, and how much of it was clear, as fields on a copy of the polygons.",
       _p("zones", "Polygons", "layer", geometry=["Polygon"]), SENSOR, SOURCE, INDEX, *PERIOD, CLOUD, SCENES)
def zonal_index_tool(p, ctx: Ctx) -> Result:
    feats = ctx.inputs.vector("zones")
    if not feats:
        raise bad("The layer has no polygons.", "zones")
    if len(feats) > 5000:
        raise bad("The layer has more than 5,000 polygons: select some of them first.", "zones")
    a, b = span(p)
    zs = [Zone(f["id"], "", f["geom"]) for f in feats]
    g, outs, counts = index_composites(ctx, p, [(a, b, "")], p.get("index") or "ndvi", zones_bbox(zs))
    v = outs[0]
    ctx.say("Summarising each polygon", 0.95)
    masks = zone_masks(zs, g)
    short = INDICES[p.get("index") or "ndvi"]["name"].split(":")[0].lower()
    rows = []
    for f, m in zip(feats, masks):
        vals = v[m]
        fin = vals[np.isfinite(vals)]
        st = {f"{short}_mean": None, f"{short}_min": None, f"{short}_max": None, f"{short}_std": None, f"{short}_median": None}
        if fin.size:
            st = {f"{short}_mean": round(float(fin.mean()), 4), f"{short}_min": round(float(fin.min()), 4), f"{short}_max": round(float(fin.max()), 4),
                  f"{short}_std": round(float(fin.std()), 4), f"{short}_median": round(float(np.median(fin)), 4)}
        st["clear_pct"] = round(100 * fin.size / vals.size, 1) if vals.size else None
        rows.append((f["geom"], {**f["props"], **st}))
    lay = ctx.inputs.layers["zones"]
    fields = list(lay["fields"]) + [{"name": k, "type": "number"} for k in rows[0][1] if k not in {x["name"] for x in lay["fields"]}]
    return Result([rt.Out(name=f"{lay['name']} {short.upper()} {span_words(a, b)}", kind="vector", geometry_type="Polygon", fields=fields, rows=rows,
                          summary={"index": short.upper(), "dates": span_words(a, b), "scenes": counts[0]})])


@etool("land_cover", "Land cover breakdown", "Statistics",
       "ESA WorldCover land cover (10 m, 2021) for the area in its official colours, with the hectares of each class; for "
       "polygons, the hectares and share of each class in every polygon.",
       AREA, _p("zones", "Polygons (optional)", "layer", geometry=["Polygon"], optional=True))
def land_cover_tool(p, ctx: Ctx) -> Result:
    if ctx.inputs.layers.get("zones"):
        feats = ctx.inputs.vector("zones")
        if not feats:
            raise bad("The layer has no polygons.", "zones")
        bbox = zones_bbox([Zone(f["id"], "", f["geom"]) for f in feats])
    else:
        feats, bbox = [], area_of(p)
    g = plan(bbox, 10, 3, "layers")
    ctx.say("Reading ESA WorldCover", 0.4)
    lc = land_cover(g, bbox)
    present = [v for v in sorted(imagery.WORLDCOVER) if (lc == v).any()]
    classes = [(v, imagery.WORLDCOVER[v][0], imagery.WORLDCOVER[v][1]) for v in present]
    outs = [class_out("Land cover (ESA WorldCover 2021)", lc, g, classes, {"source": "ESA WorldCover 10 m v200"})]
    if feats:
        ha = cell_ha(g)
        rows = []
        for f, m in zip(feats, zone_masks([Zone(f["id"], "", f["geom"]) for f in feats], g)):
            tot = int((m & (lc > 0)).sum())
            props = dict(f["props"])
            for v in present:
                n = int((m & (lc == v)).sum())
                props[f"{imagery.WORLDCOVER[v][0]} ha"] = round(n * ha, 2)
                props[f"{imagery.WORLDCOVER[v][0]} %"] = round(100 * n / tot, 1) if tot else None
            rows.append((f["geom"], props))
        lay = ctx.inputs.layers["zones"]
        have = {x["name"] for x in lay["fields"]}
        fields = list(lay["fields"]) + [{"name": k, "type": "number"} for k in rows[0][1] if k not in have]
        outs.append(rt.Out(name=f"{lay['name']} land cover", kind="vector", geometry_type="Polygon", fields=fields, rows=rows))
    return Result(outs)
