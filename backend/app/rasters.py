"""Raster layers: storing them as Cloud-Optimized GeoTIFFs, describing them, drawing map tiles, and reading values.

A raster keeps its own coordinate system; tiles are warped to Web Mercator as they are drawn, from the overview that
matches the zoom, so a large elevation model or image stays quick to view. The layer's `raster` column describes the
file (size, cells, coordinate system, bands and their statistics); its style says how to colour it."""
import io
import logging
import math
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

import numpy as np
import rasterio
import rasterio.shutil
import rasterio.windows
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform as warp_points
from rasterio.warp import transform_bounds

from .config import settings

log = logging.getLogger("rasters")
EXTENSIONS = (".tif", ".tiff", ".geotiff", ".img", ".asc", ".jp2", ".hgt", ".dem", ".bil", ".nc", ".vrt", ".ecw", ".sid", ".ter", ".xyz")
MERC = CRS.from_epsg(3857)
HALF = 20037508.342789244
TILE = 256


class RasterError(Exception):
    """The raster cannot be used, in words for the person who supplied it."""


# =====================================================================================================================
# Storage: a local folder, mirrored to an S3-compatible bucket when one is set
# =====================================================================================================================
_s3 = None
_lock = threading.Lock()


def _bucket():
    global _s3
    if not settings.s3_endpoint:
        return None
    if _s3 is None:
        import boto3
        region = settings.s3_region or ("auto" if "r2.cloudflarestorage.com" in settings.s3_endpoint else "us-east-1")
        _s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint, aws_access_key_id=settings.s3_access_key,
                           aws_secret_access_key=settings.s3_secret_key, region_name=region)
    return _s3


def _dir() -> Path:
    d = Path(settings.raster_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def local(key: str) -> Path:
    """The raster's file on this machine, fetched from the bucket first when only the bucket has it."""
    if not key or "/" in key or ".." in key:
        raise RasterError("That raster's file name is not valid.")
    p = _dir() / key
    if not p.exists():
        b = _bucket()
        if not b:
            raise RasterError("The raster's file is missing from storage (it was kept on the server's disk, which a redeploy replaces). "
                              "Add the raster again; the administrator should set up a storage bucket or volume so it stays.")
        with _lock:
            if not p.exists():
                tmp = p.with_suffix(".part")
                b.download_file(settings.s3_bucket, settings.s3_prefix + key, str(tmp))
                tmp.replace(p)
    return p


def stored(key: str | None) -> bool:
    """Whether the raster's file can be had: on this machine, or (when a bucket is set) presumed in the bucket."""
    if not key or "/" in key or ".." in key:
        return False
    return bool(settings.s3_endpoint) or (_dir() / key).exists()


def warn_if_not_kept() -> None:
    """Without a bucket, rasters live only in RASTER_DIR: on a host whose disk is replaced at each deploy (Railway, Render,
    Fly without a volume) they are lost then. Say so in the log at start."""
    if not settings.s3_endpoint:
        log.warning("Rasters are kept only in %s (no S3_ENDPOINT). Unless that folder is on a persistent volume, every raster "
                    "is lost when the service is redeployed. Set S3_* (e.g. the Cloudflare R2 bucket) or mount a volume there.",
                    Path(settings.raster_dir).resolve())


def _keep(path: Path) -> str:
    """Move a finished COG into storage; returns its key."""
    key = f"{uuid.uuid4().hex}.tif"
    dst = _dir() / key
    shutil.move(str(path), dst)
    b = _bucket()
    if b:
        b.upload_file(str(dst), settings.s3_bucket, settings.s3_prefix + key, ExtraArgs={"ContentType": "image/tiff"})
    return key


def remove(key: str | None) -> None:
    if not key:
        return
    try:
        (_dir() / key).unlink(missing_ok=True)
        b = _bucket()
        if b:
            b.delete_object(Bucket=settings.s3_bucket, Key=settings.s3_prefix + key)
    except Exception as e:  # noqa: BLE001  (a file left behind is no reason to fail removing the layer)
        log.warning("could not remove raster %s: %s", key, e)


def workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="gisr-", dir=_dir()))


# =====================================================================================================================
# Describing a raster
# =====================================================================================================================
def _epsg(crs: CRS) -> int | None:
    try:
        return crs.to_epsg(confidence_threshold=70)
    except Exception:  # noqa: BLE001
        return None


def cell_metres(crs: CRS, transform, height: int, bounds) -> tuple[float, float]:
    """The size of a cell on the ground, in metres (at the raster's centre for a geographic raster)."""
    rx, ry = abs(transform.a), abs(transform.e)
    if crs.is_geographic:
        lat = (bounds[1] + bounds[3]) / 2
        return rx * 111_320 * math.cos(math.radians(lat)), ry * 110_574
    f = crs.linear_units_factor[1] if crs.linear_units_factor else 1.0
    return rx * f, ry * f


def _sample(src, band: int, most: int = 1024) -> np.ma.MaskedArray:
    """A band read at reduced size (from its overviews when it has them), with no-data masked."""
    k = max(1, math.ceil(max(src.width, src.height) / most))
    shape = (max(1, src.height // k), max(1, src.width // k))
    a = src.read(band, out_shape=shape, masked=True, resampling=Resampling.nearest)
    if np.issubdtype(a.dtype, np.floating):
        a = np.ma.masked_invalid(a)
    return a


def _crs_name(crs: CRS) -> str:
    try:
        import pyproj
        return pyproj.CRS.from_wkt(crs.to_wkt()).name
    except Exception:  # noqa: BLE001
        return crs.to_string()


def band_stats(src) -> list[dict]:
    out = []
    for b in range(1, src.count + 1):
        a = _sample(src, b)
        v = a.compressed().astype("float64")
        d: dict = {"band": b, "name": (src.descriptions[b - 1] if src.descriptions and src.descriptions[b - 1] else f"Band {b}")}
        if v.size:
            d.update(min=float(v.min()), max=float(v.max()), mean=float(v.mean()), std=float(v.std()),
                     p2=float(np.percentile(v, 2)), p98=float(np.percentile(v, 98)))
            if np.issubdtype(a.dtype, np.integer) or np.all(v == np.round(v)):
                u = np.unique(v)
                if u.size <= 64:
                    d["values"] = [int(x) for x in u]
        out.append(d)
    return out


def describe(path: Path, key: str) -> dict:
    with rasterio.open(path) as src:
        b4326 = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
        rm = cell_metres(src.crs, src.transform, src.height, b4326)
        return {"key": key, "width": src.width, "height": src.height, "count": src.count, "dtype": src.dtypes[0],
                "nodata": None if src.nodata is None or (isinstance(src.nodata, float) and math.isnan(src.nodata)) else src.nodata,
                "crs": src.crs.to_string(), "epsg": _epsg(src.crs), "geographic": bool(src.crs.is_geographic),
                "crs_name": _crs_name(src.crs),
                "transform": list(src.transform)[:6], "res": [abs(src.transform.a), abs(src.transform.e)], "res_m": [round(rm[0], 4), round(rm[1], 4)],
                "bounds": list(src.bounds), "bbox": [round(x, 8) for x in b4326], "bands": band_stats(src), "size_bytes": path.stat().st_size}


def _r(v: float | None) -> float | None:
    return None if v is None else float(f"{v:.6g}")


def default_style(meta: dict) -> dict:
    b = meta["bands"]
    if meta["count"] >= 3 and meta["dtype"] in ("uint8", "uint16", "int16"):
        return {"type": "raster", "mode": "rgb", "bands": [1, 2, 3], "mins": [_r(b[i].get("p2", 0)) for i in range(3)],
                "maxs": [_r(b[i].get("p98", 255)) for i in range(3)], "opacity": 1}
    b1 = b[0]
    if "values" in b1 and len(b1["values"]) <= 20 and meta["dtype"].startswith(("uint", "int")):
        from .raster_style import CATEGORY
        return {"type": "raster", "mode": "classes", "band": 1, "opacity": 1,
                "categories": [{"value": v, "color": CATEGORY[i % len(CATEGORY)], "label": str(v)} for i, v in enumerate(b1["values"])]}
    return {"type": "raster", "mode": "ramp", "band": 1, "min": _r(b1.get("p2", 0)), "max": _r(b1.get("p98", 1)), "ramp": "Terrain", "opacity": 1}


# =====================================================================================================================
# Making COGs
# =====================================================================================================================
def _cog(src_path: Path, dst: Path, resampling: str) -> None:
    rasterio.shutil.copy(str(src_path), str(dst), driver="COG", compress="DEFLATE", predictor="YES", bigtiff="IF_SAFER",
                         overview_resampling=resampling, num_threads="ALL_CPUS")


def _overview_kind(src) -> str:
    if np.issubdtype(np.dtype(src.dtypes[0]), np.floating) or src.count >= 3:
        return "AVERAGE"
    st = band_stats(src)[0]
    return "NEAREST" if "values" in st else "AVERAGE"


def ingest(path: Path, epsg: int | None = None) -> dict:
    """A raster file brought in: checked, made into a COG in storage, and described."""
    try:
        src = rasterio.open(path)
    except rasterio.errors.RasterioIOError:
        raise RasterError("PolyGIS could not read that raster. GeoTIFF works best; ASCII grids, Erdas IMG, JPEG 2000 and "
                          "SRTM .hgt tiles are read too.") from None
    work = workdir()
    try:
        with src:
            if src.count > 64:
                raise RasterError(f"The raster has {src.count} bands; the most is 64.")
            if src.width * src.height > 2_000_000_000:
                raise RasterError("The raster has more than 2 billion cells: clip it first.")
            crs = src.crs or (CRS.from_epsg(epsg) if epsg else None)
            if epsg and src.crs and _epsg(src.crs) != epsg:
                crs = CRS.from_epsg(epsg)                 # the person says the file's own system is wrong
            if crs is None:
                raise RasterError("The raster does not say its coordinate system: choose it under “Its coordinate system”.")
            if src.transform.is_identity:
                raise RasterError("The raster has no georeferencing (no world file or coordinates): it cannot be placed on the map.")
            resampling = _overview_kind(src)
            source = path
            if crs != src.crs:
                source = work / "with-crs.tif"
                rasterio.shutil.copy(str(path), str(source), driver="GTiff")
                with rasterio.open(source, "r+") as d:
                    d.crs = crs
        out = work / "cog.tif"
        _cog(source, out, resampling)
        key = _keep(out)
        return describe(_dir() / key, key)
    except rasterio.errors.RasterioError as e:
        raise RasterError(f"PolyGIS could not read that raster: {e}") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def save(data: np.ndarray | np.ma.MaskedArray, crs, transform, nodata=None, dtype: str | None = None,
         names: list[str] | None = None, categorical: bool = False) -> dict:
    """An array (bands, rows, cols) or (rows, cols) written as a COG in storage; masked or NaN cells become no-data."""
    a = data if data.ndim == 3 else data[np.newaxis]
    mask = np.ma.getmaskarray(a) if np.ma.isMaskedArray(a) else np.zeros(a.shape, bool)
    arr = np.ma.getdata(a)
    dtype = dtype or ("float32" if np.issubdtype(arr.dtype, np.floating) else str(arr.dtype))
    if np.issubdtype(np.dtype(dtype), np.floating):
        mask = mask | ~np.isfinite(arr)
        nodata = -3.4028235e38 if nodata is None else nodata
    elif nodata is None and mask.any():
        info = np.iinfo(np.dtype(dtype))
        nodata = info.min if info.min < 0 else info.max
    arr = np.where(mask, nodata if nodata is not None else 0, arr).astype(dtype)
    work = workdir()
    try:
        tmp = work / "out.tif"
        with rasterio.open(tmp, "w", driver="GTiff", width=arr.shape[2], height=arr.shape[1], count=arr.shape[0], dtype=dtype,
                           crs=crs, transform=transform, nodata=nodata, tiled=True, compress="DEFLATE") as d:
            d.write(arr)
            for i, n in enumerate(names or []):
                d.set_band_description(i + 1, n)
        out = work / "cog.tif"
        _cog(tmp, out, "NEAREST" if categorical else "AVERAGE")
        key = _keep(out)
        return describe(_dir() / key, key)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# =====================================================================================================================
# Map tiles and values
# =====================================================================================================================
def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    size = 2 * HALF / 2 ** z
    return -HALF + x * size, HALF - (y + 1) * size, -HALF + (x + 1) * size, HALF - y * size


def _tile_lonlat(z, x, y):
    n = 2 ** z
    lon = lambda i: i / n * 360 - 180  # noqa: E731
    lat = lambda j: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * j / n))))  # noqa: E731
    return lon(x), lat(y + 1), lon(x + 1), lat(y)


def _overview_level(src, tile_res: float) -> int | None:
    """The coarsest overview still at least as fine as the tile's cells (None: full resolution)."""
    facs = src.overviews(1)
    if not facs:
        return None
    try:
        b = transform_bounds(src.crs, MERC, *src.bounds)
    except Exception:  # noqa: BLE001
        return None
    res = (b[2] - b[0]) / src.width
    best = None
    for i, f in enumerate(facs):
        if res * f <= tile_res:
            best = i
    return best


def _covered(vrt, src) -> np.ndarray:
    """Where the warped tile has the raster's cells. For a raster without a nodata value the warp adds an alpha band;
    the VRT's own dataset mask ignores it (and reports the whole tile), so the alpha band is read itself."""
    if vrt.count > src.count:
        return vrt.read(vrt.count) > 0
    return vrt.dataset_mask() > 0


def render(meta: dict, style: dict, z: int, x: int, y: int) -> bytes | None:
    """The tile as a PNG, or None when the raster does not reach it."""
    from .raster_style import colourise
    w, s, e, n = _tile_lonlat(z, x, y)
    bw, bs, be, bn = meta["bbox"]
    if e < bw or w > be or n < bs or s > bn:
        return None
    path = local(meta["key"])
    mode = style.get("mode", "ramp")
    bands = [int(b) for b in style.get("bands", [1, 2, 3])] if mode == "rgb" else [int(style.get("band", 1))]
    bands = [min(max(1, b), meta["count"]) for b in bands]
    mb = tile_bounds(z, x, y)
    tile_res = (mb[2] - mb[0]) / TILE
    with rasterio.Env(GDAL_CACHEMAX=64):
        with rasterio.open(path) as src0:
            level = _overview_level(src0, tile_res)
        with rasterio.open(path, overview_level=level) if level is not None else rasterio.open(path) as src:
            rs = Resampling.nearest if mode == "classes" else Resampling.bilinear
            with WarpedVRT(src, crs=MERC, transform=from_bounds(*mb, TILE, TILE), width=TILE, height=TILE, resampling=rs,
                           src_nodata=src.nodata, nodata=src.nodata, add_alpha=src.nodata is None) as vrt:
                data = vrt.read(bands).astype("float64")
                valid = _covered(vrt, src)
    for b in data:
        valid &= np.isfinite(b)
        if meta.get("nodata") is not None:
            valid &= b != meta["nodata"]
    if not valid.any():
        return None
    rgba = colourise(data, valid, style, meta)
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", compress_level=1)
    return buf.getvalue()


def terrain(meta: dict, z: int, x: int, y: int, band: int = 1) -> bytes | None:
    """The tile's heights as a Terrarium-encoded PNG (MapLibre's 3D terrain), or None when the raster does not reach
    it. Cells without data take the lowest height of the tile, so the edge of an elevation model does not drop away."""
    w, s, e, n = _tile_lonlat(z, x, y)
    bw, bs, be, bn = meta["bbox"]
    if e < bw or w > be or n < bs or s > bn:
        return None
    band = min(max(1, band), meta["count"])
    mb = tile_bounds(z, x, y)
    path = local(meta["key"])
    with rasterio.Env(GDAL_CACHEMAX=64):
        with rasterio.open(path) as src0:
            level = _overview_level(src0, (mb[2] - mb[0]) / TILE)
        with rasterio.open(path, overview_level=level) if level is not None else rasterio.open(path) as src:
            with WarpedVRT(src, crs=MERC, transform=from_bounds(*mb, TILE, TILE), width=TILE, height=TILE, resampling=Resampling.bilinear,
                           src_nodata=src.nodata, nodata=src.nodata, add_alpha=src.nodata is None) as vrt:
                h = vrt.read(band).astype("float64")
                valid = _covered(vrt, src) & np.isfinite(h)
    if meta.get("nodata") is not None:
        valid &= h != meta["nodata"]
    if not valid.any():
        return None
    h = np.where(valid, h, h[valid].min())
    v = np.clip(h + 32768.0, 0, 65535.99)
    rgb = np.stack([np.floor(v / 256), np.floor(v) % 256, np.floor((v - np.floor(v)) * 256)], axis=-1).astype("uint8")
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, "PNG", compress_level=1)
    return buf.getvalue()


def values_at(meta: dict, lng: float, lat: float) -> list[float | None] | None:
    """Each band's value at a point (None where there is no data); None when the point is off the raster."""
    path = local(meta["key"])
    with rasterio.open(path) as src:
        xs, ys = warp_points("EPSG:4326", src.crs, [lng], [lat])
        r, c = src.index(xs[0], ys[0])
        if not (0 <= r < src.height and 0 <= c < src.width):
            return None
        win = rasterio.windows.Window(c, r, 1, 1)
        v = src.read(window=win, masked=True)[:, 0, 0]
        out = []
        for x in v:
            out.append(None if np.ma.is_masked(x) or not np.isfinite(float(x)) else (int(x) if float(x).is_integer() else float(x)))
        return out


def preview(meta: dict, style: dict, most: int = 320) -> tuple[bytes, tuple[float, float, float, float]] | None:
    """A small picture of the raster in Web Mercator, coloured by its style, and where it lies (for thumbnails)."""
    from PIL import Image

    from .raster_style import colourise
    try:
        path = local(meta["key"])
    except RasterError:
        return None
    mode = style.get("mode", "ramp")
    bands = [int(b) for b in style.get("bands", [1, 2, 3])] if mode == "rgb" else [int(style.get("band", 1))]
    bands = [min(max(1, b), meta["count"]) for b in bands]
    with rasterio.open(path) as src0:
        b = transform_bounds(src0.crs, MERC, *src0.bounds, densify_pts=21)
        k = max(b[2] - b[0], b[3] - b[1]) / most
        w, h = max(1, round((b[2] - b[0]) / k)), max(1, round((b[3] - b[1]) / k))
        level = _overview_level(src0, k)
    with rasterio.open(path, overview_level=level) if level is not None else rasterio.open(path) as src:
        with WarpedVRT(src, crs=MERC, transform=from_bounds(*b, w, h), width=w, height=h, src_nodata=src.nodata, nodata=src.nodata,
                       add_alpha=src.nodata is None, resampling=Resampling.nearest if mode == "classes" else Resampling.bilinear) as vrt:
            data = vrt.read(bands).astype("float64")
            valid = vrt.dataset_mask() > 0
    for x in data:
        valid &= np.isfinite(x)
        if meta.get("nodata") is not None:
            valid &= x != meta["nodata"]
    buf = io.BytesIO()
    Image.fromarray(colourise(data, valid, style, meta), "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue(), b
