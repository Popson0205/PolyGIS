"""Raster geoprocessing: terrain, hydrology, map algebra, neighbourhood and zonal statistics, distance, density and
interpolation, conversion, and image analysis.

Each tool is a plain function of its settings and its inputs (rasters as files, vector layers as their features) and
returns a new raster or a new vector layer. Cell sizes are worked out in metres on the ground, also for rasters in
longitude and latitude (where a cell's width shrinks towards the poles), so slopes, distances and areas are true."""
import ast
import math
from dataclasses import dataclass, field
from collections.abc import Callable

import numpy as np
import rasterio
import shapely
import shapely.geometry
from pyproj import CRS as PCRS
from pyproj import Transformer
from rasterio import features as rfeatures
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin, rowcol
from rasterio.warp import reproject
from scipy import ndimage

from . import rasters
from .config import settings
from .errors import Problem
from .tools import _p

RTOOLS: dict[str, dict] = {}
RBUILD: dict[str, Callable] = {}


def rtool(tid: str, name: str, group: str, about: str, *params):
    def reg(fn):
        RTOOLS[tid] = {"name": name, "group": group, "about": about, "params": list(params), "kind": "raster"}
        RBUILD[tid] = fn
        return fn
    return reg


def bad(msg: str, param: str | None = None):
    return Problem(422, msg, code="validation", fields={"params": [param]} if param else None)


R_IN = _p("input", "Input raster", "layer", geometry=["Raster"])
DEM = _p("input", "Elevation model (DEM)", "layer", geometry=["Raster"])
BAND = _p("band", "Band", "number", min=1, default=1)
PTS = ["Point"]
POLY = ["Polygon"]


def num(params: dict, name: str, lo: float, hi: float, label: str, default=None) -> float:
    v = params.get(name, default)
    if v in (None, ""):
        v = default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise bad(f"{label} must be a number.", name) from None
    if not lo <= v <= hi:
        raise bad(f"{label} must be between {lo:g} and {hi:g}.", name)
    return v


# =====================================================================================================================
# Inputs and outputs
# =====================================================================================================================
@dataclass
class Grid:
    """A raster's cells: data (masked float64), and where they lie."""
    data: np.ma.MaskedArray
    crs: CRS
    transform: rasterio.Affine

    @property
    def shape(self):
        return self.data.shape[-2:]

    def cell_m(self) -> tuple[np.ndarray, float]:
        """Cell width in metres for each row (a column vector) and cell height in metres."""
        h = self.shape[0]
        rx, ry = abs(self.transform.a), abs(self.transform.e)
        if self.crs.is_geographic:
            lat = self.transform.f + self.transform.e * (np.arange(h) + 0.5)
            return (rx * 111_320 * np.cos(np.radians(lat)))[:, None].clip(1e-6), ry * 110_574
        f = self.crs.linear_units_factor[1] if self.crs.linear_units_factor else 1.0
        return np.full((h, 1), rx * f), ry * f

    def cell_area(self) -> np.ndarray:
        dx, dy = self.cell_m()
        return dx * dy

    def centres(self):
        h, w = self.shape
        cols, rows = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
        x, y = self.transform * (cols, rows)
        return x, y


@dataclass
class Out:
    name: str
    kind: str = "raster"                          # raster | vector
    data: np.ndarray | None = None                # raster: (rows, cols) or (bands, rows, cols), masked
    crs: CRS | None = None
    transform: object = None
    dtype: str | None = None
    categorical: bool = False
    style: dict | None = None
    names: list[str] | None = None
    geometry_type: str = ""                       # vector
    fields: list[dict] = field(default_factory=list)
    rows: list = field(default_factory=list)      # [(shapely geometry in WGS 84, props)]
    summary: dict = field(default_factory=dict)   # figures worth keeping with the layer (volumes, accuracy…)


class Inputs:
    """What a tool reads: rasters (their layer rows) and vector layers (their features in WGS 84)."""

    def __init__(self, layers: dict, features: dict):
        self.layers, self.features = layers, features

    def meta(self, key: str) -> dict:
        return self.layers[key]["raster"]

    def grid(self, key: str, band: int = 1, limit: int | None = None) -> Grid:
        m = self.meta(key)
        band = int(band)
        if not 1 <= band <= m["count"]:
            raise bad(f"{self.layers[key]['name']} has {m['count']} band{'s' if m['count'] > 1 else ''}: choose 1 to {m['count']}.", "band")
        cells = m["width"] * m["height"]
        if cells > (limit or settings.max_raster_cells):
            raise bad(f"{self.layers[key]['name']} has {cells:,} cells; this tool works on up to {(limit or settings.max_raster_cells):,}. "
                      "Clip or resample it first.")
        with rasterio.open(rasters.local(m["key"])) as src:
            a = src.read(band, masked=True).astype("float64")
            a = np.ma.masked_invalid(a)
            return Grid(a, src.crs, src.transform)

    def bands(self, key: str) -> Grid:
        m = self.meta(key)
        if m["width"] * m["height"] * m["count"] > settings.max_raster_cells:
            raise bad(f"{self.layers[key]['name']} is too large for this tool: clip or resample it first.")
        with rasterio.open(rasters.local(m["key"])) as src:
            a = np.ma.masked_invalid(src.read(masked=True).astype("float64"))
            return Grid(a, src.crs, src.transform)

    def aligned(self, key: str, to: Grid, band: int = 1, resampling=Resampling.bilinear) -> np.ma.MaskedArray:
        """Another raster's band on the cells of `to` (warped when its grid differs)."""
        m = self.meta(key)
        with rasterio.open(rasters.local(m["key"])) as src:
            if src.crs == to.crs and src.transform == to.transform and (src.height, src.width) == to.shape:
                return np.ma.masked_invalid(src.read(band, masked=True).astype("float64"))
            out = np.full(to.shape, np.nan)
            reproject(rasterio.band(src, band), out, dst_transform=to.transform, dst_crs=to.crs, dst_nodata=np.nan, resampling=resampling,
                      src_nodata=src.nodata)
            return np.ma.masked_invalid(out)

    def vector(self, key: str) -> list[dict]:
        return self.features[key]


def to_crs(geoms, crs) -> list:
    t = Transformer.from_crs(4326, PCRS.from_wkt(CRS.from_user_input(crs).to_wkt()), always_xy=True)
    return [shapely.transform(g, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1]))) for g in geoms]


def to_wgs84(geoms, crs) -> list:
    t = Transformer.from_crs(PCRS.from_wkt(CRS.from_user_input(crs).to_wkt()), 4326, always_xy=True)
    return [shapely.transform(g, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1]))) for g in geoms]


def utm_for(bbox) -> CRS:
    lng, lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + min(60, max(1, int((lng + 180) // 6) + 1)))


def new_grid(feats: list[dict], cell: float, pad: float = 0.0) -> tuple[CRS, object, int, int]:
    """A grid in the UTM zone of the features, covering them (plus a margin), with square cells of `cell` metres."""
    if not feats:
        raise bad("The layer has no features.")
    b = shapely.total_bounds([f["geom"] for f in feats])
    crs = utm_for(b)
    gs = to_crs([f["geom"] for f in feats], crs)
    x1, y1, x2, y2 = shapely.total_bounds(gs)
    x1, y1, x2, y2 = x1 - pad, y1 - pad, x2 + pad, y2 + pad
    w, h = max(1, math.ceil((x2 - x1) / cell)), max(1, math.ceil((y2 - y1) / cell))
    if w * h > settings.max_raster_cells:
        raise bad(f"That makes {w * h:,} cells; the most is {settings.max_raster_cells:,}. Use larger cells.", "cell")
    return crs, from_origin(x1, y2, cell, cell), w, h


def window(a: np.ndarray, r: int = 1):
    """The (2r+1)² neighbours of every cell as views of a padded copy (edges repeat)."""
    p = np.pad(a, r, mode="edge")
    h, w = a.shape
    return [[p[i:i + h, j:j + w] for j in range(2 * r + 1)] for i in range(2 * r + 1)]


def filled_for_neighbours(g: Grid) -> np.ndarray:
    """The data with no-data cells set to their nearest valid value, so edges of no-data do not spread."""
    a = np.ma.filled(g.data, np.nan)
    m = np.isnan(a)
    if m.any() and (~m).any():
        idx = ndimage.distance_transform_edt(m, return_distances=False, return_indices=True)
        a = a[tuple(idx)]
    return a


def raster_out(name, data, g: Grid, mask=None, **kw) -> Out:
    d = np.ma.masked_array(data, mask=(np.ma.getmaskarray(g.data) if mask is None else mask) | ~np.isfinite(np.ma.getdata(data)))
    return Out(name=name, data=d, crs=g.crs, transform=g.transform, **kw)


def ramp(name: str, data, invert=False, lo=None, hi=None) -> dict:
    v = np.ma.compressed(data) if np.ma.isMaskedArray(data) else data[np.isfinite(data)]
    v = v[np.isfinite(v)] if v.size else v
    lo = float(np.percentile(v, 2)) if lo is None and v.size else (lo or 0)
    hi = float(np.percentile(v, 98)) if hi is None and v.size else (hi if hi is not None else 1)
    lo, hi = float(f"{lo:.6g}"), float(f"{hi:.6g}")
    return {"type": "raster", "mode": "ramp", "band": 1, "min": lo, "max": hi if hi > lo else lo + 1, "ramp": name, "invert": invert, "opacity": 1}


def classes_style(values, labels: dict | None = None, colours: dict | None = None) -> dict:
    from .raster_style import CATEGORY
    return {"type": "raster", "mode": "classes", "band": 1, "opacity": 1,
            "categories": [{"value": int(v), "label": (labels or {}).get(int(v), str(int(v))),
                            "color": (colours or {}).get(int(v), CATEGORY[i % len(CATEGORY)])} for i, v in enumerate(values)]}


# =====================================================================================================================
# Terrain
# =====================================================================================================================
def horn(g: Grid):
    """East and south gradients (dz/dx, dz/dy with y down the rows) by Horn's method, in metres per metre."""
    z = filled_for_neighbours(g)
    (a, b, c), (d, _, f), (gg, h, i) = window(z)
    dx, dy = g.cell_m()
    gx = ((c + 2 * f + i) - (a + 2 * d + gg)) / (8 * dx)
    gy = ((gg + 2 * h + i) - (a + 2 * b + c)) / (8 * dy)
    return gx, gy


Z = _p("z_factor", "Z factor (vertical units to metres)", "number", min=0.000001, default=1)


@rtool("slope", "Slope", "Terrain", "The steepness of each cell, in degrees or percent.", DEM,
       _p("unit", "In", "choice", default="degrees", choices=[["degrees", "Degrees"], ["percent", "Percent rise"]]), Z)
def slope_tool(p, inp):
    g = inp.grid("input")
    gx, gy = horn(g)
    k = num(p, "z_factor", 1e-6, 1e6, "Z factor", 1)
    s = np.hypot(gx, gy) * k
    out = np.degrees(np.arctan(s)) if p.get("unit", "degrees") == "degrees" else s * 100
    return raster_out(f"{inp.layers['input']['name']} slope", out, g, style=ramp("Magma", out, invert=True, lo=0))


def aspect_of(gx, gy):
    asp = (np.degrees(np.arctan2(-gx, gy)) + 360) % 360    # the downhill direction, clockwise from north
    return np.where((gx == 0) & (gy == 0), -1, asp)


@rtool("aspect", "Aspect", "Terrain", "The direction each slope faces, in degrees clockwise from north (-1 where flat).", DEM)
def aspect_tool(p, inp):
    g = inp.grid("input")
    out = aspect_of(*horn(g))
    st = {"type": "raster", "mode": "ramp", "band": 1, "min": 0, "max": 360, "ramp": "Spectral", "opacity": 1}
    return raster_out(f"{inp.layers['input']['name']} aspect", out, g, style=st)


def shade(g: Grid, az: float, alt: float, k: float = 1.0) -> np.ndarray:
    """Esri's hillshade: 0 (shadow) to 255 (full light), from a light at azimuth `az` and altitude `alt`."""
    gx, gy = horn(g)
    gx, gy = gx * k, gy * k
    zen = math.radians(90 - alt)
    az_m = math.radians((360 - az + 90) % 360)
    slope = np.arctan(np.hypot(gx, gy))
    asp = np.arctan2(gy, -gx)
    hs = 255 * (math.cos(zen) * np.cos(slope) + math.sin(zen) * np.sin(slope) * np.cos(az_m - asp))
    return np.clip(hs, 0, 255)


@rtool("hillshade", "Hillshade", "Terrain", "Shaded relief: the terrain lit from one direction.", DEM,
       _p("azimuth", "Light from (degrees clockwise from north)", "number", default=315), _p("altitude", "Light altitude (degrees)", "number", min=0, default=45), Z)
def hillshade_tool(p, inp):
    g = inp.grid("input")
    hs = shade(g, num(p, "azimuth", -360, 720, "Light from", 315), num(p, "altitude", 0, 90, "Light altitude", 45), num(p, "z_factor", 1e-6, 1e6, "Z factor", 1))
    return raster_out(f"{inp.layers['input']['name']} hillshade", hs.round(), g, dtype="uint8",
                      style={"type": "raster", "mode": "ramp", "band": 1, "min": 0, "max": 255, "ramp": "Gray", "opacity": 1})


@rtool("multi_hillshade", "Multidirectional hillshade", "Terrain", "Shaded relief lit from six directions at once, so no slope is lost in shadow.", DEM, Z)
def multi_hillshade_tool(p, inp):
    g = inp.grid("input")
    k = num(p, "z_factor", 1e-6, 1e6, "Z factor", 1)
    hs = np.mean([shade(g, az, 45, k) for az in (0, 60, 120, 180, 240, 300)], axis=0)
    hs = 255 * (hs - hs.min()) / max(1e-9, hs.max() - hs.min())
    return raster_out(f"{inp.layers['input']['name']} multidirectional hillshade", hs.round(), g, dtype="uint8",
                      style={"type": "raster", "mode": "ramp", "band": 1, "min": 0, "max": 255, "ramp": "Gray", "opacity": 1})


@rtool("curvature", "Curvature", "Terrain", "How the surface bends (1/100 m): positive is convex (ridges), negative concave (valleys).", DEM,
       _p("kind", "Curvature", "choice", default="total", choices=[["total", "Total"], ["profile", "Profile (along the slope)"], ["plan", "Plan (across the slope)"]]))
def curvature_tool(p, inp):
    g = inp.grid("input")
    z = filled_for_neighbours(g)
    (a, b, c), (d, e, f), (gg, h, i) = window(z)
    lx, ly = g.cell_m()
    D = ((d + f) / 2 - e) / lx ** 2
    E = ((b + h) / 2 - e) / ly ** 2
    F = (-a + c + gg - i) / (4 * lx * ly)
    G = (-d + f) / (2 * lx)
    H = (b - h) / (2 * ly)
    kind = p.get("kind", "total")
    with np.errstate(invalid="ignore", divide="ignore"):
        if kind == "profile":
            out = np.where(G ** 2 + H ** 2 > 0, -2 * (D * G ** 2 + E * H ** 2 + F * G * H) / (G ** 2 + H ** 2), 0) * 100
        elif kind == "plan":
            out = np.where(G ** 2 + H ** 2 > 0, 2 * (D * H ** 2 + E * G ** 2 - F * G * H) / (G ** 2 + H ** 2), 0) * 100
        else:
            out = -2 * (D + E) * 100
    v = np.abs(out[np.isfinite(out)])
    lim = float(np.percentile(v, 98)) if v.size else 1
    return raster_out(f"{inp.layers['input']['name']} {kind} curvature", out, g, style=ramp("Blue to red", out, invert=True, lo=-lim, hi=lim))


def focal_mean(a: np.ndarray, r: int, circle=True, exclude_centre=False) -> np.ndarray:
    fp = footprint(r, circle)
    if exclude_centre:
        fp[r, r] = False
    ok = np.isfinite(a)
    s = ndimage.correlate(np.where(ok, a, 0), fp.astype(float), mode="nearest")
    n = ndimage.correlate(ok.astype(float), fp.astype(float), mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        return s / n


def footprint(r: int, circle=True) -> np.ndarray:
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y <= r * r + 0.5) if circle else np.ones((2 * r + 1, 2 * r + 1), bool)


@rtool("tpi", "Topographic position index", "Terrain", "Each cell's height above (+) or below (-) the average of its surroundings: ridges and valleys.",
       DEM, _p("radius", "Neighbourhood radius (cells)", "number", min=1, default=3))
def tpi_tool(p, inp):
    g = inp.grid("input")
    r = int(num(p, "radius", 1, 100, "Radius", 3))
    z = np.ma.filled(g.data, np.nan)
    out = z - focal_mean(z, r, exclude_centre=True)
    v = np.abs(out[np.isfinite(out)])
    lim = float(np.percentile(v, 98)) if v.size else 1
    return raster_out(f"{inp.layers['input']['name']} TPI", out, g, style=ramp("Blue to red", out, lo=-lim, hi=lim))


@rtool("tri", "Terrain ruggedness index", "Terrain", "How much each cell's height differs from its eight neighbours (Riley's index, metres).", DEM)
def tri_tool(p, inp):
    g = inp.grid("input")
    z = filled_for_neighbours(g)
    w = window(z)
    out = np.sqrt(sum((w[i][j] - z) ** 2 for i in range(3) for j in range(3)))
    return raster_out(f"{inp.layers['input']['name']} TRI", out, g, style=ramp("Magma", out, invert=True, lo=0))


@rtool("roughness", "Roughness", "Terrain", "The range of heights (highest minus lowest) around each cell.", DEM,
       _p("radius", "Neighbourhood radius (cells)", "number", min=1, default=1))
def roughness_tool(p, inp):
    g = inp.grid("input")
    r = int(num(p, "radius", 1, 100, "Radius", 1))
    z = filled_for_neighbours(g)
    fp = footprint(r, circle=False)
    out = ndimage.maximum_filter(z, footprint=fp, mode="nearest") - ndimage.minimum_filter(z, footprint=fp, mode="nearest")
    return raster_out(f"{inp.layers['input']['name']} roughness", out, g, style=ramp("Magma", out, invert=True, lo=0))


@rtool("vrm", "Vector ruggedness measure", "Terrain", "Ruggedness independent of slope (Sappington et al.): 0 is smooth, 1 is most rugged.",
       DEM, _p("radius", "Neighbourhood radius (cells)", "number", min=1, default=1))
def vrm_tool(p, inp):
    g = inp.grid("input")
    r = int(num(p, "radius", 1, 50, "Radius", 1))
    gx, gy = horn(g)
    s = np.arctan(np.hypot(gx, gy))
    a = np.arctan2(-gx, gy)
    x, y, zc = np.sin(s) * np.sin(a), np.sin(s) * np.cos(a), np.cos(s)
    fp = footprint(r, circle=False).astype(float)
    n = fp.sum()
    sx, sy, sz = (ndimage.correlate(v, fp, mode="nearest") for v in (x, y, zc))
    out = 1 - np.sqrt(sx ** 2 + sy ** 2 + sz ** 2) / n
    return raster_out(f"{inp.layers['input']['name']} VRM", out, g, style=ramp("Magma", out, invert=True, lo=0))


@rtool("contours", "Contours", "Terrain", "Lines of equal height (or of any value) at an interval.", R_IN,
       _p("interval", "Interval", "number", min=0.000001, default=10), _p("base", "Starting from", "number", default=0), BAND)
def contours_tool(p, inp):
    import contourpy
    g = inp.grid("input", p.get("band", 1))
    iv, base = num(p, "interval", 1e-9, 1e9, "Interval", 10), num(p, "base", -1e12, 1e12, "Starting from", 0)
    v = g.data.compressed()
    if not v.size:
        raise bad("The raster has no values.")
    lo, hi = math.ceil((v.min() - base) / iv), math.floor((v.max() - base) / iv)
    if hi - lo > 2000:
        raise bad(f"That makes {hi - lo + 1:,} contour levels: use a larger interval.", "interval")
    x, y = g.centres()
    cg = contourpy.contour_generator(x[0], y[:, 0], g.data, line_type="Separate")
    lines, levels = [], []
    for k in range(lo, hi + 1):
        lev = base + k * iv
        for seg in cg.lines(lev):
            if len(seg) >= 2:
                lines.append(shapely.LineString(seg))
                levels.append(lev)
    geo = to_wgs84(lines, g.crs)
    rows = [(gg, {"value": round(lev, 6), "index": bool(round((lev - base) / iv) % 5 == 0)}) for gg, lev in zip(geo, levels)]
    return Out(name=f"{inp.layers['input']['name']} contours {iv:g}", kind="vector", geometry_type="LineString",
               fields=[{"name": "value", "type": "number"}, {"name": "index", "type": "boolean"}], rows=rows)


@rtool("viewshed", "Viewshed", "Terrain", "The ground that can be seen from observer points, and from how many of them.", DEM,
       _p("observers", "Observers (points)", "layer", geometry=PTS), _p("height", "Observer height above ground", "number", unit="m", min=0, default=1.7),
       _p("target", "Target height above ground", "number", unit="m", min=0, default=0),
       _p("radius", "Up to", "number", unit="m", min=1, default=5000), _p("curvature", "Allow for the earth's curvature and refraction", "bool", default=True))
def viewshed_tool(p, inp):
    g = inp.grid("input")
    obs = inp.vector("observers")
    if not 1 <= len(obs) <= 100:
        raise bad("Use 1 to 100 observer points.", "observers")
    oh, th = num(p, "height", 0, 10000, "Observer height", 1.7), num(p, "target", 0, 10000, "Target height", 0)
    radius = num(p, "radius", 1, 200_000, "Up to", 5000)
    z = np.ma.filled(g.data, np.nan)
    H, W = z.shape
    dxr, dy = g.cell_m()
    count = np.zeros((H, W), np.int32)
    pts = to_crs([o["geom"] for o in obs], g.crs)
    curv = bool(p.get("curvature", True))
    for pt in pts:
        r0, c0 = rowcol(g.transform, pt.x, pt.y)
        if not (0 <= r0 < H and 0 <= c0 < W) or not np.isfinite(z[r0, c0]):
            continue
        dx = float(dxr[r0, 0])
        R = int(min(1500, math.ceil(radius / min(dx, dy))))
        z0 = z[r0, c0] + oh
        per = [(-R, j) for j in range(-R, R + 1)] + [(R, j) for j in range(-R, R + 1)] + \
              [(i, -R) for i in range(-R + 1, R)] + [(i, R) for i in range(-R + 1, R)]
        per = np.array(per, float)
        vis = np.zeros((H, W), bool)
        vis[r0, c0] = True
        steps = np.arange(1, R + 1, dtype=float)
        for chunk in np.array_split(per, max(1, len(per) // 512)):
            n = np.abs(chunk).max(1)[:, None]                         # sample once per cell along the ray
            rr = np.rint(r0 + chunk[:, :1] * steps / n).astype(int)
            cc = np.rint(c0 + chunk[:, 1:] * steps / n).astype(int)
            inside = (rr >= 0) & (rr < H) & (cc >= 0) & (cc < W)
            rrc, ccc = rr.clip(0, H - 1), cc.clip(0, W - 1)
            dist = np.hypot((rrc - r0) * dy, (ccc - c0) * dx)
            zz = z[rrc, ccc]
            if curv:
                zz = zz - dist ** 2 * (1 - 0.13) / (2 * 6_371_000)
            ok = inside & np.isfinite(zz) & (dist <= radius) & (dist > 0)
            ang = np.where(ok, (zz - z0) / np.where(dist > 0, dist, 1), -np.inf)
            horizon = np.maximum.accumulate(np.concatenate([np.full((len(chunk), 1), -np.inf), ang[:, :-1]], 1), axis=1)
            seen = ok & ((zz + th - z0) / np.where(dist > 0, dist, 1) >= horizon)
            vis[rr[seen], cc[seen]] = True
        count += vis
    out = np.ma.masked_array(count, mask=np.ma.getmaskarray(g.data))
    vals = sorted({int(v) for v in np.unique(out.compressed())})
    st = classes_style(vals, {0: "Not seen", **{v: f"Seen from {v}" for v in vals if v}}, {0: "#4d4d4d", 1: "#a6d96a"})
    return Out(name=f"{inp.layers['input']['name']} viewshed", data=out, crs=g.crs, transform=g.transform, dtype="int32", categorical=True, style=st,
               summary={"visible_m2": float((g.cell_area() * (count > 0)).sum()), "observers": len(pts)})


@rtool("cut_fill", "Cut and fill", "Terrain", "The change in height between a surface before and after, with the volumes cut and filled.",
       _p("input", "Before (elevation)", "layer", geometry=["Raster"]), _p("after", "After (elevation)", "layer", geometry=["Raster"]))
def cut_fill_tool(p, inp):
    g = inp.grid("input")
    after = inp.aligned("after", g)
    diff = after - g.data
    area = g.cell_area()
    d = np.ma.filled(diff, 0)
    cut = float(-(np.minimum(d, 0) * area).sum())
    fill = float((np.maximum(d, 0) * area).sum())
    v = np.abs(diff.compressed())
    lim = float(np.percentile(v, 98)) if v.size else 1
    out = raster_out(f"{inp.layers['input']['name']} cut and fill", np.ma.getdata(diff), g, mask=np.ma.getmaskarray(diff),
                     style=ramp("Blue to red", diff, invert=True, lo=-lim, hi=lim))
    out.summary = {"cut_m3": round(cut, 2), "fill_m3": round(fill, 2), "net_m3": round(fill - cut, 2),
                   "changed_m2": round(float((area * (np.abs(d) > 1e-9)).sum()), 2)}
    return out


@rtool("profile", "Elevation profile", "Terrain", "Points along lines at an interval, with the height there and the distance along the line.",
       _p("lines", "Lines", "layer", geometry=["LineString"]), DEM, _p("interval", "Every", "number", unit="m", min=0.01, default=10))
def profile_tool(p, inp):
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    iv = num(p, "interval", 0.01, 1e6, "Every", 10)
    meta = inp.meta("input")
    rows = []
    with rasterio.open(rasters.local(meta["key"])) as src:
        t = Transformer.from_crs(4326, PCRS.from_wkt(src.crs.to_wkt()), always_xy=True)
        for f in inp.vector("lines"):
            for part in getattr(f["geom"], "geoms", [f["geom"]]):
                cs = np.array(part.coords)
                seg = np.array([geod.inv(*cs[k], *cs[k + 1])[2] for k in range(len(cs) - 1)])
                cum = np.concatenate([[0], np.cumsum(seg)])
                total = cum[-1]
                ds = np.append(np.arange(0, total, iv), total)
                if len(ds) > 200_000:
                    raise bad("That makes more than 200,000 points: use a larger interval.", "interval")
                lng = np.interp(ds, cum, cs[:, 0])
                lat = np.interp(ds, cum, cs[:, 1])
                xs, ys = t.transform(lng, lat)
                vals = [v[0] for v in src.sample(zip(xs, ys), indexes=1, masked=True)]
                for d_, x_, y_, v in zip(ds, lng, lat, vals):
                    rows.append((shapely.Point(x_, y_), {"line_id": f["id"], "distance_m": round(float(d_), 3),
                                                         "elevation": None if np.ma.is_masked(v) else round(float(v), 3)}))
    return Out(name=f"{inp.layers['lines']['name']} profile", kind="vector", geometry_type="Point", rows=rows,
               fields=[{"name": "line_id", "type": "integer"}, {"name": "distance_m", "type": "number"}, {"name": "elevation", "type": "number"}])


# =====================================================================================================================
# Hydrology (D8): sinks filled, flats drained towards their outlets, every cell's downstream neighbour found once
# =====================================================================================================================
OFFS = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)]      # E SE S SW W NW N NE
ESRI = [1, 2, 4, 8, 16, 32, 64, 128]
LOW = -1e30


def fill_sinks(z: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Depressions raised to their spill height (morphological reconstruction; the raster's edge and no-data drain)."""
    from skimage.morphology import reconstruction
    mask = np.where(valid, z, LOW)
    seed = np.full_like(mask, mask[valid].max() if valid.any() else 0)
    seed[0, :], seed[-1, :], seed[:, 0], seed[:, -1] = mask[0, :], mask[-1, :], mask[:, 0], mask[:, -1]
    seed[~valid] = LOW
    return reconstruction(seed, mask, method="erosion", footprint=np.ones((3, 3)))


@dataclass
class Flow:
    filled: np.ndarray
    valid: np.ndarray
    recv: np.ndarray          # flat index of the downstream cell, -1 where water leaves the raster
    direction: np.ndarray     # 0..7 into OFFS, -1 for an outlet
    order: np.ndarray         # flat indices of valid cells, upstream before downstream
    step: np.ndarray          # metres to the downstream cell
    grid: Grid


def flow(g: Grid) -> Flow:
    H, W = g.shape
    if H * W > settings.max_hydro_cells:
        raise bad(f"The elevation model has {H * W:,} cells; hydrology tools work on up to {settings.max_hydro_cells:,}. Clip or resample it first.")
    valid = ~np.ma.getmaskarray(g.data)
    if not valid.any():
        raise bad("The elevation model has no values.")
    F = fill_sinks(np.ma.filled(g.data, 0).astype("float64"), valid)
    dxr, dy = g.cell_m()
    dx = np.broadcast_to(dxr, (H, W))
    P = np.pad(np.where(valid, F, np.inf), 1, constant_values=np.inf)
    best = np.zeros((H, W))
    dirn = np.full((H, W), -1, np.int64)
    lower_or_equal_nb = np.zeros((H, W), bool)
    for k, (di, dj) in enumerate(OFFS):
        nb = P[1 + di:1 + di + H, 1 + dj:1 + dj + W]
        dist = np.hypot(di * dy, dj * dx)
        drop = (F - nb) / dist
        better = (drop > best) & np.isfinite(nb)
        best = np.where(better, drop, best)
        dirn = np.where(better, k, dirn)
    edge = np.zeros((H, W), bool)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    near_nodata = ndimage.binary_dilation(~valid, structure=np.ones((3, 3))) & valid
    drains = valid & ((dirn >= 0) | edge | near_nodata)
    flat = valid & ~drains
    dist = np.zeros((H, W))
    if flat.any():
        from skimage.graph import MCP_Geometric
        # outlets of each flat: draining cells at the same height next to it
        start = np.zeros((H, W), bool)
        Pf = np.pad(np.where(flat, F, np.nan), 1, constant_values=np.nan)
        for di, dj in OFFS:
            start |= drains & (Pf[1 + di:1 + di + H, 1 + dj:1 + dj + W] == F)
        costs = np.where(flat | start, 1.0, -1.0)
        m = MCP_Geometric(costs, fully_connected=True)
        cum, _ = m.find_costs(list(zip(*np.nonzero(start))))
        dist = np.where(flat | start, cum, 0)
        dist = np.where(np.isfinite(dist), dist, 0)
        Pd = np.pad(np.where(flat | start, dist, np.inf), 1, constant_values=np.inf)
        Pe = np.pad(np.where(valid, F, np.nan), 1, constant_values=np.nan)
        bestd = np.where(flat, dist, np.inf)
        for k, (di, dj) in enumerate(OFFS):
            nd = Pd[1 + di:1 + di + H, 1 + dj:1 + dj + W]
            same = Pe[1 + di:1 + di + H, 1 + dj:1 + dj + W] == F
            better = flat & same & (nd < bestd)
            bestd = np.where(better, nd, bestd)
            dirn = np.where(better, k, dirn)
    del lower_or_equal_nb
    di = np.array([o[0] for o in OFFS] + [0])[dirn]
    dj = np.array([o[1] for o in OFFS] + [0])[dirn]
    rows, cols = np.mgrid[0:H, 0:W]
    recv = np.where(dirn >= 0, (rows + di) * W + (cols + dj), -1).ravel()
    recv[~valid.ravel()] = -1
    step = np.where(dirn >= 0, np.hypot(di * dy, dj * dx), 0)
    idx = np.flatnonzero(valid.ravel())
    o = np.lexsort((-dist.ravel()[idx], -F.ravel()[idx]))
    return Flow(F, valid, recv, np.where(valid, dirn, -1), idx[o], step, g)


def accumulate(fl: Flow, weight: np.ndarray | None = None) -> np.ndarray:
    acc = (np.ones(fl.recv.size) if weight is None else np.nan_to_num(weight.ravel().astype(float))).tolist()
    recv = fl.recv.tolist()
    for i in fl.order.tolist():
        r = recv[i]
        if r >= 0:
            acc[r] += acc[i]
    return np.array(acc).reshape(fl.valid.shape)


def propagate(fl: Flow, labels: np.ndarray) -> np.ndarray:
    """Labels carried upstream: each unlabelled cell takes the label of the cell it drains to."""
    lab = labels.ravel().tolist()
    recv = fl.recv.tolist()
    for i in reversed(fl.order.tolist()):
        if lab[i] == 0:
            r = recv[i]
            if r >= 0:
                lab[i] = lab[r]
    return np.array(lab, dtype=np.int64).reshape(fl.valid.shape)


@rtool("fill", "Fill sinks", "Hydrology", "Pits and depressions filled to the height where water would spill out of them.", DEM)
def fill_tool(p, inp):
    g = inp.grid("input", limit=settings.max_hydro_cells)
    valid = ~np.ma.getmaskarray(g.data)
    F = fill_sinks(np.ma.filled(g.data, 0), valid)
    m = inp.layers["input"]["style"]
    return raster_out(f"{inp.layers['input']['name']} filled", F, g, style=m if m.get("mode") == "ramp" else ramp("Terrain", g.data))


@rtool("sinks", "Sink depth", "Hydrology", "How deep each depression is: the filled surface minus the original (0 outside depressions).", DEM)
def sinks_tool(p, inp):
    g = inp.grid("input", limit=settings.max_hydro_cells)
    valid = ~np.ma.getmaskarray(g.data)
    d = fill_sinks(np.ma.filled(g.data, 0), valid) - np.ma.filled(g.data, 0)
    out = raster_out(f"{inp.layers['input']['name']} sink depth", d, g, style=ramp("Blues", d, lo=0))
    out.summary = {"sink_cells": int((d > 1e-9).sum()), "sink_volume_m3": round(float((d * g.cell_area()).sum()), 2)}
    return out


@rtool("flow_direction", "Flow direction", "Hydrology", "The neighbour each cell drains to (D8, coded 1 east, 2 south-east, 4 south … 128 north-east; 0 where water leaves).", DEM)
def flow_direction_tool(p, inp):
    fl = flow(inp.grid("input"))
    codes = np.where(fl.direction >= 0, np.array(ESRI + [0])[fl.direction], 0)
    labels = {0: "Leaves", 1: "E", 2: "SE", 4: "S", 8: "SW", 16: "W", 32: "NW", 64: "N", 128: "NE"}
    return raster_out(f"{inp.layers['input']['name']} flow direction", codes, fl.grid, mask=~fl.valid, dtype="uint8", categorical=True,
                      style=classes_style(sorted(set(np.unique(codes[fl.valid]).tolist())), labels))


@rtool("flow_accumulation", "Flow accumulation", "Hydrology", "How many cells (or how much area) drain through each cell.", DEM,
       _p("unit", "As", "choice", default="cells", choices=[["cells", "Number of cells"], ["km2", "Upstream area (km²)"]]))
def flow_accumulation_tool(p, inp):
    fl = flow(inp.grid("input"))
    if p.get("unit") == "km2":
        acc = accumulate(fl, fl.grid.cell_area() * np.ones(fl.valid.shape) / 1e6)
    else:
        acc = accumulate(fl)
    lo, hi = float(acc[fl.valid].min()), float(np.percentile(acc[fl.valid], 99.5))
    return raster_out(f"{inp.layers['input']['name']} flow accumulation", acc, fl.grid, mask=~fl.valid, style=ramp("Blues", acc, lo=lo, hi=hi))


THRESH = _p("threshold", "A stream starts where it drains", "number", unit="km²", min=0.000001, default=1)


def _streams(p, fl: Flow):
    t = num(p, "threshold", 1e-9, 1e7, "Stream threshold", 1)
    acc_km2 = accumulate(fl, fl.grid.cell_area() * np.ones(fl.valid.shape) / 1e6)
    st = (acc_km2 >= t) & fl.valid
    if not st.any():
        raise bad(f"No cell drains {t:g} km²: use a smaller threshold.", "threshold")
    return st, acc_km2


def strahler(fl: Flow, st: np.ndarray) -> np.ndarray:
    n = st.size
    s = st.ravel().tolist()
    mx, cnt, order = [0] * n, [0] * n, [0] * n
    recv = fl.recv.tolist()
    for i in fl.order.tolist():
        if not s[i]:
            continue
        o = 1 if mx[i] == 0 else (mx[i] + 1 if cnt[i] >= 2 else mx[i])
        order[i] = o
        r = recv[i]
        if r >= 0 and s[r]:
            if o > mx[r]:
                mx[r], cnt[r] = o, 1
            elif o == mx[r]:
                cnt[r] += 1
    return np.array(order).reshape(st.shape)


@rtool("stream_order", "Stream order", "Hydrology", "Streams (where enough area drains) numbered by Strahler order: 1 at the sources, rising where two of the same order meet.",
       DEM, THRESH)
def stream_order_tool(p, inp):
    fl = flow(inp.grid("input"))
    st, _ = _streams(p, fl)
    o = strahler(fl, st)
    return raster_out(f"{inp.layers['input']['name']} stream order", o, fl.grid, mask=~st, dtype="uint8", categorical=True,
                      style=classes_style(sorted(np.unique(o[st]).tolist()), colours={1: "#9ecae1", 2: "#6baed6", 3: "#3182bd", 4: "#08519c", 5: "#08306b"}))


@rtool("stream_network", "Stream network", "Hydrology", "Streams as lines, one per link between junctions, with Strahler order, length and upstream area.", DEM, THRESH)
def stream_network_tool(p, inp):
    fl = flow(inp.grid("input"))
    st, acc = _streams(p, fl)
    o = strahler(fl, st).ravel()
    H, W = st.shape
    s = st.ravel()
    recv = fl.recv
    ndon = np.zeros(s.size, np.int64)
    src = np.flatnonzero(s & (recv >= 0))
    src = src[s[recv[src]]]
    np.add.at(ndon, recv[src], 1)
    x, y = fl.grid.centres()
    x, y = x.ravel(), y.ravel()
    lines, props = [], []
    starts = np.flatnonzero(s & (ndon != 1))
    accr = acc.ravel()
    for c in starts.tolist():
        path = [c]
        cur = c
        while True:
            r = int(recv[cur])
            if r < 0 or not s[r]:
                break
            path.append(r)
            if ndon[r] != 1:
                break
            cur = r
        if len(path) < 2:
            continue
        line = shapely.LineString(np.column_stack([x[path], y[path]]))
        lines.append(line)
        props.append({"order": int(o[c]), "upstream_km2": round(float(accr[path[-2]]), 4)})
    geo = to_wgs84(lines, fl.grid.crs)
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    rows = [(gg, {**pp, "length_m": round(geod.geometry_length(gg), 2)}) for gg, pp in zip(geo, props)]
    return Out(name=f"{inp.layers['input']['name']} streams", kind="vector", geometry_type="LineString", rows=rows,
               fields=[{"name": "order", "type": "integer"}, {"name": "upstream_km2", "type": "number"}, {"name": "length_m", "type": "number"}])


def _snap(fl: Flow, acc: np.ndarray, pts: list[dict], snap_m: float) -> list[tuple[int, dict]]:
    g = fl.grid
    H, W = fl.valid.shape
    dxr, dy = g.cell_m()
    out = []
    for f, pt in zip(pts, to_crs([f["geom"] for f in pts], g.crs)):
        r, c = rowcol(g.transform, pt.x, pt.y)
        if not (0 <= r < H and 0 <= c < W):
            continue
        rr, rc = int(math.ceil(snap_m / dy)), int(math.ceil(snap_m / float(dxr[r, 0])))
        r1, r2, c1, c2 = max(0, r - rr), min(H, r + rr + 1), max(0, c - rc), min(W, c + rc + 1)
        win = np.where(fl.valid[r1:r2, c1:c2], acc[r1:r2, c1:c2], -1)
        yy, xx = np.mgrid[r1:r2, c1:c2]
        win = np.where(np.hypot((yy - r) * dy, (xx - c) * float(dxr[r, 0])) <= max(snap_m, 0.5 * dy), win, -1)
        k = np.unravel_index(int(np.argmax(win)), win.shape)
        if win[k] < 0:
            continue
        out.append(((r1 + k[0]) * W + (c1 + k[1]), f))
    return out


SNAP = _p("snap", "Move each point to the largest flow within", "number", unit="m", min=0, default=60)


@rtool("snap_pour_points", "Snap pour points", "Hydrology", "Outlet points moved onto the cell of largest flow nearby, so watersheds start on the stream.",
       _p("points", "Pour points", "layer", geometry=PTS), DEM, SNAP)
def snap_pour_points_tool(p, inp):
    fl = flow(inp.grid("input"))
    acc = accumulate(fl)
    x, y = fl.grid.centres()
    rows = []
    for cell, f in _snap(fl, acc, inp.vector("points"), num(p, "snap", 0, 1e5, "Snap distance", 60)):
        pt = to_wgs84([shapely.Point(x.ravel()[cell], y.ravel()[cell])], fl.grid.crs)[0]
        rows.append((pt, {**f["props"], "upstream_cells": int(acc.ravel()[cell])}))
    lay = inp.layers["points"]
    return Out(name=f"{lay['name']} snapped", kind="vector", geometry_type="Point", rows=rows,
               fields=lay["fields"] + [{"name": "upstream_cells", "type": "integer"}])


def _polygons(labels: np.ndarray, g: Grid, mask: np.ndarray) -> dict[int, shapely.Geometry]:
    shapes: dict[int, list] = {}
    for geom, v in rfeatures.shapes(labels.astype("int32"), mask=mask, transform=g.transform, connectivity=8):
        shapes.setdefault(int(v), []).append(shapely.geometry.shape(geom))
    return {k: shapely.union_all(v) for k, v in shapes.items()}


@rtool("watershed", "Watershed", "Hydrology", "The area draining to each pour point, as polygons with their area.",
       _p("points", "Pour points", "layer", geometry=PTS), DEM, SNAP)
def watershed_tool(p, inp):
    fl = flow(inp.grid("input"))
    acc = accumulate(fl)
    seeds = np.zeros(fl.valid.size, np.int64)
    snapped = _snap(fl, acc, inp.vector("points"), num(p, "snap", 0, 1e5, "Snap distance", 60))
    if not snapped:
        raise bad("None of the pour points is on the elevation model.", "points")
    for k, (cell, _) in enumerate(snapped, start=1):
        seeds[cell] = k
    lab = propagate(fl, seeds.reshape(fl.valid.shape))
    polys = _polygons(lab, fl.grid, lab > 0)
    area = fl.grid.cell_area() * np.ones(fl.valid.shape)
    geo = to_wgs84([polys[k] for k in sorted(polys)], fl.grid.crs)
    rows = []
    for gg, k in zip(geo, sorted(polys)):
        f = snapped[k - 1][1]
        rows.append((gg, {**f["props"], "point_id": f["id"], "area_km2": round(float(area[lab == k].sum()) / 1e6, 6)}))
    lay = inp.layers["points"]
    return Out(name=f"{lay['name']} watersheds", kind="vector", geometry_type="Polygon", rows=rows,
               fields=lay["fields"] + [{"name": "point_id", "type": "integer"}, {"name": "area_km2", "type": "number"}])


@rtool("basins", "Basins", "Hydrology", "Every drainage basin of the elevation model: all cells that drain to the same outlet share a number.", DEM)
def basins_tool(p, inp):
    fl = flow(inp.grid("input"))
    seeds = np.zeros(fl.valid.size, np.int64)
    outl = np.flatnonzero(fl.valid.ravel() & (fl.recv < 0))
    seeds[outl] = np.arange(1, outl.size + 1)
    lab = propagate(fl, seeds.reshape(fl.valid.shape))
    # number basins from the largest down
    ids, counts = np.unique(lab[fl.valid], return_counts=True)
    rank = np.zeros(int(ids.max()) + 1, np.int64)
    rank[ids[np.argsort(-counts)]] = np.arange(1, ids.size + 1)
    out = rank[lab]
    st = classes_style(list(range(1, min(20, ids.size) + 1)))
    return raster_out(f"{inp.layers['input']['name']} basins", out, fl.grid, mask=~fl.valid, dtype="int32", categorical=True, style=st,
                      summary={"basins": int(ids.size)})


@rtool("twi", "Topographic wetness index", "Hydrology", "Where water tends to collect: ln(upslope area per metre of contour / tan slope).", DEM)
def twi_tool(p, inp):
    fl = flow(inp.grid("input"))
    acc = accumulate(fl)
    dx, dy = fl.grid.cell_m()
    sca = acc * dx * dy / np.sqrt(dx * dy)
    gx, gy = horn(fl.grid)
    tanb = np.maximum(np.hypot(gx, gy), 0.001)
    out = np.log(sca / tanb)
    return raster_out(f"{inp.layers['input']['name']} wetness index", out, fl.grid, mask=~fl.valid, style=ramp("Blues", out))


@rtool("flow_length", "Flow length", "Hydrology", "The distance water travels from each cell to where it leaves the elevation model (downstream), in metres.", DEM)
def flow_length_tool(p, inp):
    fl = flow(inp.grid("input"))
    L = [0.0] * fl.recv.size
    recv = fl.recv.tolist()
    step = fl.step.ravel().tolist()
    for i in reversed(fl.order.tolist()):
        r = recv[i]
        if r >= 0:
            L[i] = L[r] + step[i]
    out = np.array(L).reshape(fl.valid.shape)
    return raster_out(f"{inp.layers['input']['name']} flow length", out, fl.grid, mask=~fl.valid, style=ramp("Viridis", out, lo=0))


# =====================================================================================================================
# Map algebra
# =====================================================================================================================
FUNCS = {"abs": np.abs, "sqrt": np.sqrt, "log": np.log, "ln": np.log, "log10": np.log10, "exp": np.exp, "sin": np.sin, "cos": np.cos,
         "tan": np.tan, "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan, "atan2": np.arctan2, "floor": np.floor, "ceil": np.ceil,
         "round": np.round, "min": np.minimum, "max": np.maximum, "degrees": np.degrees, "radians": np.radians}


def evaluate(expr: str, env: dict) -> np.ndarray:
    """A formula over rasters, read safely: numbers, the raster names, + - * / ** %, comparisons, and, or, not,
    the functions in FUNCS, con(test, a, b) and isnull(x). Nothing else is allowed."""
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError:
        raise bad("The formula is not written right, e.g. (A - B) / (A + B) or con(A > 100, 1, 0).", "expression") from None

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id in env:
                return env[n.id]
            if n.id == "pi":
                return math.pi
            raise bad(f"The formula names {n.id}, which is not one of the rasters ({', '.join(env)}).", "expression")
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            ops = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide, ast.Pow: np.power,
                   ast.Mod: np.mod, ast.FloorDiv: np.floor_divide}
            if type(n.op) in ops:
                return ops[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            if isinstance(n.op, ast.USub):
                return -v
            if isinstance(n.op, ast.UAdd):
                return v
            if isinstance(n.op, ast.Not):
                return np.logical_not(v).astype(float)
        if isinstance(n, ast.Compare):
            left, out = ev(n.left), None
            ops = {ast.Gt: np.greater, ast.GtE: np.greater_equal, ast.Lt: np.less, ast.LtE: np.less_equal, ast.Eq: np.equal, ast.NotEq: np.not_equal}
            for op, c in zip(n.ops, n.comparators):
                if type(op) not in ops:
                    break
                right = ev(c)
                r = ops[type(op)](left, right)
                out = r if out is None else np.logical_and(out, r)
                left = right
            else:
                return out.astype(float)
        if isinstance(n, ast.BoolOp):
            vals = [ev(v) for v in n.values]
            f = np.logical_and if isinstance(n.op, ast.And) else np.logical_or
            out = vals[0]
            for v in vals[1:]:
                out = f(out, v)
            return np.asarray(out, float)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
            args = [ev(a) for a in n.args]
            name = n.func.id.lower()
            if name == "con" and len(args) in (2, 3):
                return np.where(np.asarray(args[0]) != 0, args[1], args[2] if len(args) == 3 else np.nan)
            if name == "isnull" and len(args) == 1:
                return np.isnan(args[0]).astype(float)
            if name in FUNCS:
                return FUNCS[name](*args)
            raise bad(f"{n.func.id} is not a function PolyGIS knows. Use: {', '.join(sorted(FUNCS))}, con, isnull.", "expression")
        raise bad("The formula uses something PolyGIS does not allow: numbers, raster names, arithmetic, comparisons and functions only.", "expression")

    with np.errstate(all="ignore"):
        out = ev(tree)
    return np.broadcast_to(np.asarray(out, float), next(iter(env.values())).shape).copy()


def _env(inp, p, keys=("A", "B", "C", "D")) -> tuple[Grid, dict]:
    g = inp.grid("A", p.get("band_a", 1))
    env = {"A": np.ma.filled(g.data, np.nan)}
    for k in keys[1:]:
        if inp.layers.get(k):
            env[k] = np.ma.filled(inp.aligned(k, g, int(p.get(f"band_{k.lower()}", 1) or 1)), np.nan)
    return g, env


@rtool("calculator", "Raster calculator", "Map algebra", "A new raster from a formula of up to four rasters, e.g. (A - B) / (A + B), con(A > 300, 1, 0), sqrt(A).",
       _p("A", "Raster A", "layer", geometry=["Raster"]), _p("B", "Raster B", "layer", geometry=["Raster"], optional=True),
       _p("C", "Raster C", "layer", geometry=["Raster"], optional=True), _p("D", "Raster D", "layer", geometry=["Raster"], optional=True),
       _p("expression", "Formula", "text", default="A * 1"))
def calculator_tool(p, inp):
    expr = str(p.get("expression") or "").strip()
    if not expr or len(expr) > 1000:
        raise bad("Write a formula, e.g. (A - B) / (A + B).", "expression")
    g, env = _env(inp, p)
    out = evaluate(expr, env)
    return raster_out(f"{inp.layers['A']['name']} calculated", out, g, mask=~np.isfinite(out), style=ramp("Viridis", out))


def _two_bands(inp, p) -> tuple[Grid, np.ndarray, np.ndarray]:
    g = inp.grid("input", p.get("band_1", 1))
    other = "second" if inp.layers.get("second") else "input"
    b = inp.aligned(other, g, int(p.get("band_2", 2) or 2))
    return g, np.ma.filled(g.data, np.nan), np.ma.filled(b, np.nan)


ND_PARAMS = [_p("input", "Image (or the first band's raster)", "layer", geometry=["Raster"]),
             _p("band_1", "First band (e.g. near-infrared)", "number", min=1, default=4),
             _p("second", "Second band's raster (if another file)", "layer", geometry=["Raster"], optional=True),
             _p("band_2", "Second band (e.g. red)", "number", min=1, default=3)]


@rtool("normalized_difference", "Normalized difference (NDVI, NDWI…)", "Map algebra",
       "(first − second) / (first + second) of two bands: NDVI with near-infrared and red, NDWI with green and near-infrared.", *ND_PARAMS)
def nd_tool(p, inp):
    g, a, b = _two_bands(inp, p)
    with np.errstate(all="ignore"):
        out = (a - b) / (a + b)
    return raster_out(f"{inp.layers['input']['name']} normalized difference", out, g, mask=~np.isfinite(out),
                      style={"type": "raster", "mode": "ramp", "band": 1, "min": -0.2, "max": 0.8, "ramp": "Red to green", "opacity": 1})


@rtool("savi", "Soil-adjusted vegetation index (SAVI)", "Map algebra", "Like NDVI but less affected by bare soil: (1 + L)(NIR − red)/(NIR + red + L).",
       *ND_PARAMS, _p("L", "Soil brightness factor L", "number", min=0, default=0.5))
def savi_tool(p, inp):
    g, a, b = _two_bands(inp, p)
    L = num(p, "L", 0, 1, "L", 0.5)
    with np.errstate(all="ignore"):
        out = (1 + L) * (a - b) / (a + b + L)
    return raster_out(f"{inp.layers['input']['name']} SAVI", out, g, mask=~np.isfinite(out),
                      style={"type": "raster", "mode": "ramp", "band": 1, "min": -0.2, "max": 0.8, "ramp": "Red to green", "opacity": 1})


@rtool("evi", "Enhanced vegetation index (EVI)", "Map algebra", "2.5 (NIR − red) / (NIR + 6 red − 7.5 blue + 1), from reflectance (0–1) bands of one image.",
       _p("input", "Image", "layer", geometry=["Raster"]), _p("nir", "Near-infrared band", "number", min=1, default=4),
       _p("red", "Red band", "number", min=1, default=3), _p("blue", "Blue band", "number", min=1, default=1),
       _p("scale", "Divide the values by (10000 for Sentinel-2 L2A)", "number", min=0.000001, default=1))
def evi_tool(p, inp):
    g = inp.grid("input", p.get("nir", 4))
    k = num(p, "scale", 1e-9, 1e9, "Scale", 1)
    nir = np.ma.filled(g.data, np.nan) / k
    red = np.ma.filled(inp.aligned("input", g, int(p.get("red", 3))), np.nan) / k
    blue = np.ma.filled(inp.aligned("input", g, int(p.get("blue", 1))), np.nan) / k
    with np.errstate(all="ignore"):
        out = 2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1)
    return raster_out(f"{inp.layers['input']['name']} EVI", out, g, mask=~np.isfinite(out),
                      style={"type": "raster", "mode": "ramp", "band": 1, "min": -0.2, "max": 0.8, "ramp": "Red to green", "opacity": 1})


def parse_table(text: str) -> list[tuple[float, float, float]]:
    """Reclassify rules, one per line or separated by ';': "from - to: new" for ranges, "value: new" for single values."""
    out = []
    for part in [x.strip() for x in str(text or "").replace("\n", ";").split(";") if x.strip()]:
        if ":" not in part:
            raise bad(f"“{part}” needs a colon: e.g. 0 - 100: 1, or 5: 2.", "table")
        left, new = part.rsplit(":", 1)
        left = left.strip()
        try:
            import re
            m = re.fullmatch(r"(-?[\d.eE+]+|-inf)\s*(?:-|to|–)\s*(-?[\d.eE+]+|inf)", left)
            lo, hi = (float(m.group(1)), float(m.group(2))) if m else (float(left), float(left))
            out.append((lo, hi, float(new) if new.strip().lower() not in ("nodata", "null", "") else np.nan))
        except (ValueError, AttributeError):
            raise bad(f"“{part}” is not a rule PolyGIS can read: e.g. 0 - 100: 1, or 5: 2.", "table") from None
    if not out:
        raise bad("Write at least one rule, e.g. 0 - 100: 1; 100 - 500: 2.", "table")
    return out


@rtool("reclassify", "Reclassify", "Map algebra", "Values (or ranges of values) changed to new ones, e.g. slopes 0–5: 1, 5–15: 2, 15–90: 3.",
       R_IN, BAND, _p("table", "Rules (from - to: new; one per line or separated by ;)", "text", default="0 - 5: 1; 5 - 15: 2; 15 - 90: 3"),
       _p("others", "Values no rule covers", "choice", default="nodata", choices=[["nodata", "Become no data"], ["keep", "Keep their value"]]))
def reclassify_tool(p, inp):
    g = inp.grid("input", p.get("band", 1))
    a = np.ma.filled(g.data, np.nan)
    rules = parse_table(p.get("table"))
    out = np.full(a.shape, np.nan) if p.get("others", "nodata") == "nodata" else a.copy()
    done = np.zeros(a.shape, bool)
    for lo, hi, new in rules:
        hit = ((a == lo) if lo == hi else (a >= lo) & (a < hi)) & ~done
        out[hit] = new
        done |= hit
    # a range's top value is included in the last rule, so 15 - 90 covers 90
    lo, hi, new = rules[-1]
    out[(a == hi) & ~done] = new
    vals = np.unique(out[np.isfinite(out)])
    whole = np.all(vals == np.round(vals))
    return raster_out(f"{inp.layers['input']['name']} reclassified", out, g, mask=~np.isfinite(out), dtype="int32" if whole and vals.size else "float32",
                      categorical=bool(whole and vals.size <= 50),
                      style=classes_style(vals.astype(int).tolist()) if whole and vals.size <= 50 else ramp("Viridis", out))


@rtool("set_null", "Set no data", "Map algebra", "Cells inside (or outside) a range of values made no data; the rest keep their value.",
       R_IN, BAND, _p("min", "From", "number", default=0), _p("max", "To", "number", default=0),
       _p("where", "Make no data the cells", "choice", default="inside", choices=[["inside", "Inside the range"], ["outside", "Outside the range"]]))
def set_null_tool(p, inp):
    g = inp.grid("input", p.get("band", 1))
    lo, hi = num(p, "min", -1e300, 1e300, "From", 0), num(p, "max", -1e300, 1e300, "To", 0)
    a = np.ma.filled(g.data, np.nan)
    inside = (a >= min(lo, hi)) & (a <= max(lo, hi))
    kill = inside if p.get("where", "inside") == "inside" else ~inside
    out = np.where(kill, np.nan, a)
    return raster_out(f"{inp.layers['input']['name']} with no data set", out, g, mask=~np.isfinite(out), style=inp.layers["input"]["style"])


@rtool("rescale", "Rescale", "Map algebra", "Values stretched in a straight line onto a new range (e.g. 0 to 1 for a suitability model).",
       R_IN, BAND, _p("low", "New lowest", "number", default=0), _p("high", "New highest", "number", default=1),
       _p("invert", "Highest values become lowest", "bool", default=False))
def rescale_tool(p, inp):
    g = inp.grid("input", p.get("band", 1))
    a = np.ma.filled(g.data, np.nan)
    lo, hi = num(p, "low", -1e300, 1e300, "New lowest", 0), num(p, "high", -1e300, 1e300, "New highest", 1)
    mn, mx = np.nanmin(a), np.nanmax(a)
    t = (a - mn) / ((mx - mn) or 1)
    if p.get("invert"):
        t = 1 - t
    out = lo + t * (hi - lo)
    return raster_out(f"{inp.layers['input']['name']} rescaled", out, g, style=ramp("Viridis", out, lo=min(lo, hi), hi=max(lo, hi)))


MATH = [["abs", "Absolute value"], ["sqrt", "Square root"], ["square", "Square"], ["log", "Natural log"], ["log10", "Log base 10"],
        ["exp", "Exponential"], ["int", "Whole number (truncate)"], ["round", "Round"], ["negate", "Negate"], ["sin", "Sine (radians)"],
        ["cos", "Cosine (radians)"], ["degrees", "Radians to degrees"], ["radians", "Degrees to radians"]]


@rtool("math", "Math", "Map algebra", "One mathematical function applied to every cell.", R_IN, BAND,
       _p("function", "Function", "choice", default="abs", choices=MATH))
def math_tool(p, inp):
    g = inp.grid("input", p.get("band", 1))
    a = np.ma.filled(g.data, np.nan)
    fn = p.get("function", "abs")
    f = {"square": np.square, "int": np.trunc, "negate": np.negative}.get(fn) or FUNCS.get(fn)
    if f is None:
        raise bad("Choose a function.", "function")
    with np.errstate(all="ignore"):
        out = f(a)
    return raster_out(f"{inp.layers['input']['name']} {fn}", out, g, mask=~np.isfinite(out), style=ramp("Viridis", out))


STATS = [["mean", "Mean"], ["min", "Minimum"], ["max", "Maximum"], ["sum", "Sum"], ["std", "Standard deviation"], ["range", "Range"],
         ["median", "Median"], ["majority", "Majority"], ["minority", "Minority"], ["variety", "Variety (number of values)"]]


def stack_stat(stack: np.ndarray, stat: str, axis=0) -> np.ndarray:
    """A statistic across one axis, ignoring no-data (NaN)."""
    with np.errstate(all="ignore"), __import__("warnings").catch_warnings():
        __import__("warnings").simplefilter("ignore")
        if stat == "mean":
            return np.nanmean(stack, axis)
        if stat == "min":
            return np.nanmin(stack, axis)
        if stat == "max":
            return np.nanmax(stack, axis)
        if stat == "sum":
            return np.where(np.isfinite(stack).any(axis), np.nansum(stack, axis), np.nan)
        if stat == "std":
            return np.nanstd(stack, axis)
        if stat == "range":
            return np.nanmax(stack, axis) - np.nanmin(stack, axis)
        if stat == "median":
            return np.nanmedian(stack, axis)
        s = np.sort(np.moveaxis(stack, axis, -1), axis=-1)
        fin = np.isfinite(s)
        new = np.concatenate([fin[..., :1], (s[..., 1:] != s[..., :-1]) & fin[..., 1:]], -1)
        if stat == "variety":
            return np.where(fin.any(-1), new.sum(-1), np.nan)
        # majority / minority: the longest (shortest) run of equal values in the sorted list
        run_id = np.cumsum(new, -1)
        n = s.shape[-1]
        counts = np.zeros(s.shape[:-1] + (n + 1,))
        np.put_along_axis(counts, run_id, 0, -1)
        idx = np.indices(run_id.shape)
        np.add.at(counts, tuple(idx[:-1]) + (run_id,), fin.astype(float))
        counts[..., 0] = 0
        if stat == "majority":
            best = counts.argmax(-1)
        else:
            c = np.where(counts > 0, counts, np.inf)
            best = c.argmin(-1)
        first = np.argmax(run_id == best[..., None], -1)
        out = np.take_along_axis(s, first[..., None], -1)[..., 0]
        return np.where(fin.any(-1), out, np.nan)


@rtool("cell_statistics", "Cell statistics", "Map algebra", "A statistic of several rasters cell by cell (e.g. the mean of twelve monthly rainfall rasters).",
       _p("rasters", "Rasters", "layers", geometry=["Raster"], min=2), _p("stat", "Statistic", "choice", default="mean", choices=STATS))
def cell_statistics_tool(p, inp):
    ls = inp.layers["rasters"]
    first = Inputs({"x": ls[0]}, {}).grid("x")
    stack = [np.ma.filled(first.data, np.nan)] + [np.ma.filled(Inputs({"x": lay}, {}).aligned("x", first), np.nan) for lay in ls[1:]]
    out = stack_stat(np.array(stack), p.get("stat", "mean"))
    return raster_out(f"{p.get('stat', 'mean')} of {len(ls)} rasters", out, first, mask=~np.isfinite(out), style=ramp("Viridis", out))


@rtool("slice", "Slice", "Map algebra", "Values divided into a number of classes, by equal intervals or with equal numbers of cells.",
       R_IN, BAND, _p("classes", "Classes", "number", min=2, default=5),
       _p("method", "By", "choice", default="equal", choices=[["equal", "Equal intervals"], ["quantile", "Equal areas (quantiles)"]]))
def slice_tool(p, inp):
    g = inp.grid("input", p.get("band", 1))
    n = int(num(p, "classes", 2, 100, "Classes", 5))
    v = g.data.compressed()
    if not v.size:
        raise bad("The raster has no values.")
    edges = np.quantile(v, np.linspace(0, 1, n + 1)) if p.get("method") == "quantile" else np.linspace(v.min(), v.max(), n + 1)
    out = np.clip(np.searchsorted(edges[1:-1], np.ma.filled(g.data, np.nan), side="right") + 1, 1, n).astype(float)
    labels = {k + 1: f"{edges[k]:.4g} – {edges[k + 1]:.4g}" for k in range(n)}
    from .raster_style import lut
    cols = {k + 1: "#%02x%02x%02x" % tuple(lut("Viridis")[int(255 * k / max(1, n - 1))]) for k in range(n)}
    return raster_out(f"{inp.layers['input']['name']} in {n} classes", out, g, dtype="uint8", categorical=True,
                      style=classes_style(list(range(1, n + 1)), labels, cols))


# =====================================================================================================================
# Neighbourhood, zonal and generalization
# =====================================================================================================================
@rtool("focal_statistics", "Focal statistics", "Statistics", "A statistic of the cells around each cell, in a circle or square (smoothing, local range, majority…).",
       R_IN, BAND, _p("stat", "Statistic", "choice", default="mean", choices=STATS), _p("radius", "Radius (cells)", "number", min=1, default=1),
       _p("shape", "Neighbourhood", "choice", default="square", choices=[["square", "Square"], ["circle", "Circle"]]))
def focal_statistics_tool(p, inp):
    from numpy.lib.stride_tricks import sliding_window_view
    g = inp.grid("input", p.get("band", 1))
    r = int(num(p, "radius", 1, 25, "Radius", 1))
    stat = p.get("stat", "mean")
    a = np.ma.filled(g.data, np.nan)
    fp = footprint(r, p.get("shape") == "circle")
    if stat == "mean":
        out = focal_mean(a, r, p.get("shape") == "circle")
    else:
        pad = np.pad(a, r, constant_values=np.nan)
        out = np.empty(a.shape)
        rows = max(1, 2_000_000 // (a.shape[1] * fp.sum()))
        for i in range(0, a.shape[0], rows):
            win = sliding_window_view(pad[i:i + rows + 2 * r], fp.shape)[..., fp]
            out[i:i + rows] = stack_stat(win, stat, axis=-1)
    return raster_out(f"{inp.layers['input']['name']} focal {stat}", out, g, style=ramp("Viridis", out))


def zones_of(inp, key: str, g: Grid):
    feats = inp.vector(key)
    geoms = to_crs([f["geom"] for f in feats], g.crs)
    return feats, geoms


ZSTATS = ["count", "area_m2", "min", "max", "mean", "std", "sum", "median"]


@rtool("zonal_statistics", "Zonal statistics", "Statistics", "For each polygon, the statistics of the raster cells inside it (count, area, min, max, mean, std, sum, median).",
       _p("zones", "Zones (polygons)", "layer", geometry=POLY), R_IN, BAND)
def zonal_statistics_tool(p, inp):
    g = inp.grid("input", p.get("band", 1))
    feats, geoms = zones_of(inp, "zones", g)
    area = g.cell_area() * np.ones(g.shape)
    a = np.ma.filled(g.data, np.nan)
    rows = []
    for f, geom in zip(feats, geoms):
        inside = rfeatures.geometry_mask([geom], out_shape=g.shape, transform=g.transform, invert=True, all_touched=False)
        if not inside.any():                    # a polygon smaller than a cell: the cell at its centre
            c = geom.representative_point()
            r_, c_ = rowcol(g.transform, c.x, c.y)
            if 0 <= r_ < g.shape[0] and 0 <= c_ < g.shape[1]:
                inside[r_, c_] = True
        v = a[inside]
        ok = np.isfinite(v)
        vv = v[ok]
        st = {"count": int(ok.sum()), "area_m2": round(float(area[inside][ok].sum()), 2)}
        if vv.size:
            st.update(min=float(vv.min()), max=float(vv.max()), mean=round(float(vv.mean()), 6), std=round(float(vv.std()), 6),
                      sum=round(float(vv.sum()), 6), median=float(np.median(vv)))
        rows.append((f["geom"], {**f["props"], **st}))
    lay = inp.layers["zones"]
    have = {x["name"] for x in lay["fields"]}
    return Out(name=f"{lay['name']} zonal statistics", kind="vector", geometry_type="Polygon", rows=rows,
               fields=lay["fields"] + [{"name": k, "type": "integer" if k == "count" else "number"} for k in ZSTATS if k not in have])


@rtool("tabulate_area", "Tabulate area", "Statistics", "For each polygon, the area of each class of a class raster (land cover, soil…) inside it.",
       _p("zones", "Zones (polygons)", "layer", geometry=POLY), _p("input", "Class raster", "layer", geometry=["Raster"]))
def tabulate_area_tool(p, inp):
    g = inp.grid("input")
    feats, geoms = zones_of(inp, "zones", g)
    area = g.cell_area() * np.ones(g.shape)
    a = np.ma.filled(g.data, np.nan)
    allv = np.unique(a[np.isfinite(a)])
    if allv.size > 100:
        raise bad("The raster has more than 100 different values: use a class raster (or reclassify it first).", "input")
    names = {v: f"class_{int(v) if float(v).is_integer() else v}_m2" for v in allv}
    rows = []
    for f, geom in zip(feats, geoms):
        inside = rfeatures.geometry_mask([geom], out_shape=g.shape, transform=g.transform, invert=True)
        v, ar = a[inside], area[inside]
        rows.append((f["geom"], {**f["props"], **{names[k]: round(float(ar[v == k].sum()), 2) for k in allv}}))
    lay = inp.layers["zones"]
    return Out(name=f"{lay['name']} areas by class", kind="vector", geometry_type="Polygon", rows=rows,
               fields=lay["fields"] + [{"name": names[k], "type": "number"} for k in allv])


@rtool("extract_values", "Extract values to points", "Statistics", "The raster's value (every band) at each point, added as fields.",
       _p("points", "Points", "layer", geometry=PTS), R_IN,
       _p("method", "Value", "choice", default="nearest", choices=[["nearest", "Of the cell the point is in"], ["bilinear", "Interpolated from the four nearest cells"]]))
def extract_values_tool(p, inp):
    meta = inp.meta("input")
    feats = inp.vector("points")
    names = [f"{(b.get('name') or f'Band {b['band']}').lower().replace(' ', '_')}" for b in meta["bands"]]
    names = ["value"] if len(names) == 1 else names
    rows = []
    with rasterio.open(rasters.local(meta["key"])) as src:
        geoms = to_crs([f["geom"] for f in feats], src.crs)
        if p.get("method") == "bilinear":
            a = src.read(masked=True).astype(float).filled(np.nan)
            inv = ~src.transform
            for f, g_ in zip(feats, geoms):
                c, r = inv * (g_.x, g_.y)
                c, r = c - 0.5, r - 0.5
                c0, r0 = int(math.floor(c)), int(math.floor(r))
                vals = []
                for b in range(src.count):
                    if 0 <= r0 < src.height - 1 and 0 <= c0 < src.width - 1:
                        q = a[b, r0:r0 + 2, c0:c0 + 2]
                        tx, ty = c - c0, r - r0
                        v = q[0, 0] * (1 - tx) * (1 - ty) + q[0, 1] * tx * (1 - ty) + q[1, 0] * (1 - tx) * ty + q[1, 1] * tx * ty
                        vals.append(None if not np.isfinite(v) else round(float(v), 6))
                    else:
                        vals.append(None)
                rows.append((f["geom"], {**f["props"], **dict(zip(names, vals))}))
        else:
            for f, v in zip(feats, src.sample([(g_.x, g_.y) for g_ in geoms], masked=True)):
                rows.append((f["geom"], {**f["props"], **{n: (None if np.ma.is_masked(x) else float(x)) for n, x in zip(names, v)}}))
    lay = inp.layers["points"]
    return Out(name=f"{lay['name']} with {inp.layers['input']['name']}", kind="vector", geometry_type="Point", rows=rows,
               fields=lay["fields"] + [{"name": n, "type": "number"} for n in names if n not in {x["name"] for x in lay["fields"]}])


@rtool("region_group", "Region group", "Generalization", "Each connected patch of cells with the same value given its own number, with patch sizes.", R_IN,
       _p("diagonal", "Cells touching at corners are connected", "bool", default=True))
def region_group_tool(p, inp):
    g = inp.grid("input")
    a = np.ma.filled(g.data, np.nan)
    st = np.ones((3, 3)) if p.get("diagonal", True) else None
    out = np.zeros(a.shape, np.int64)
    n = 0
    vals = np.unique(a[np.isfinite(a)])
    if vals.size > 1000:
        raise bad("The raster has more than 1,000 different values: region group works on classes (reclassify it first).", "input")
    for v in vals:
        lab, k = ndimage.label(a == v, structure=st)
        out[lab > 0] = lab[lab > 0] + n
        n += k
    if n > 2_000_000_000:
        raise bad("Too many regions.")
    return raster_out(f"{inp.layers['input']['name']} regions", out, g, dtype="int32", categorical=n <= 20,
                      style=classes_style(list(range(1, min(n, 20) + 1))) if n <= 20 else ramp("Spectral", out.astype(float)),
                      summary={"regions": int(n)})


@rtool("majority_filter", "Majority filter", "Generalization", "Each cell takes the most common value of its neighbours: removes speckle from a classification.",
       R_IN, _p("radius", "Radius (cells)", "number", min=1, default=1))
def majority_filter_tool(p, inp):
    p = {**p, "stat": "majority", "shape": "square"}
    out = focal_statistics_tool(p, inp)
    out.name = f"{inp.layers['input']['name']} majority filtered"
    out.categorical, out.dtype, out.style = True, inp.meta("input")["dtype"], inp.layers["input"]["style"]
    return out


@rtool("sieve", "Remove small regions (sieve)", "Generalization", "Patches smaller than a number of cells merged into their largest neighbour.",
       R_IN, _p("cells", "Smaller than (cells)", "number", min=2, default=10), _p("diagonal", "Cells touching at corners are connected", "bool", default=True))
def sieve_tool(p, inp):
    meta = inp.meta("input")
    g = inp.grid("input")
    n = int(num(p, "cells", 2, 1e8, "Smaller than", 10))
    with rasterio.open(rasters.local(meta["key"])) as src:
        a = src.read(1)
        if np.issubdtype(a.dtype, np.floating):
            raise bad("Sieve works on whole-number (class) rasters: reclassify it first.", "input")
        mask = src.read_masks(1) > 0
        out = rfeatures.sieve(a.astype("int32"), size=n, mask=mask, connectivity=8 if p.get("diagonal", True) else 4)
    return raster_out(f"{inp.layers['input']['name']} sieved", out.astype(float), g, dtype="int32", categorical=True, style=inp.layers["input"]["style"])


@rtool("expand", "Expand", "Generalization", "Zones of chosen values grown outward by a number of cells, over other values.",
       R_IN, _p("values", "Values to grow (separated by commas)", "text", default="1"), _p("cells", "By (cells)", "number", min=1, default=1))
def expand_tool(p, inp):
    g = inp.grid("input")
    a = np.ma.filled(g.data, np.nan)
    vals = _values(p)
    k = int(num(p, "cells", 1, 1000, "By", 1))
    zone = np.isin(a, vals)
    dist, (ri, ci) = ndimage.distance_transform_edt(~zone, return_indices=True)
    out = np.where((dist <= k + 1e-9) & ~zone & np.isfinite(a), a[ri, ci], a)
    return raster_out(f"{inp.layers['input']['name']} expanded", out, g, dtype=inp.meta("input")["dtype"], categorical=True, style=inp.layers["input"]["style"])


@rtool("shrink", "Shrink", "Generalization", "Zones of chosen values pulled in by a number of cells; the cells given up take the nearest other value.",
       R_IN, _p("values", "Values to shrink (separated by commas)", "text", default="1"), _p("cells", "By (cells)", "number", min=1, default=1))
def shrink_tool(p, inp):
    g = inp.grid("input")
    a = np.ma.filled(g.data, np.nan)
    vals = _values(p)
    k = int(num(p, "cells", 1, 1000, "By", 1))
    zone = np.isin(a, vals)
    other = ~zone & np.isfinite(a)
    if not other.any():
        raise bad("There are no other values to fill the cells given up.")
    dist, (ri, ci) = ndimage.distance_transform_edt(~other, return_indices=True)
    out = np.where(zone & (dist <= k + 1e-9), a[ri, ci], a)
    return raster_out(f"{inp.layers['input']['name']} shrunk", out, g, dtype=inp.meta("input")["dtype"], categorical=True, style=inp.layers["input"]["style"])


def _values(p) -> list[float]:
    try:
        v = [float(x) for x in str(p.get("values") or "").replace(";", ",").split(",") if x.strip()]
    except ValueError:
        raise bad("Give the values as numbers separated by commas.", "values") from None
    if not v:
        raise bad("Give at least one value.", "values")
    return v


# =====================================================================================================================
# Distance, density and interpolation (grids in the UTM zone of the input, in metres)
# =====================================================================================================================
CELL = _p("cell", "Cell size", "number", unit="m", min=0.01, default=30)


def burn(feats, geoms, shape, transform, values=None, all_touched=True) -> np.ndarray:
    shapes = [(gg, (v if values is not None else 1)) for gg, v in zip(geoms, values if values is not None else [1] * len(geoms)) if not gg.is_empty]
    if not shapes:
        return np.zeros(shape, "float64")
    return rfeatures.rasterize(shapes, out_shape=shape, transform=transform, fill=0, all_touched=all_touched, dtype="float64")


def _sources_grid(inp, key, p, pad_default=0.0):
    feats = inp.vector(key)
    cell = num(p, "cell", 0.01, 1e6, "Cell size", 30)
    pad = num(p, "max_distance", 0, 1e7, "Up to", pad_default) if "max_distance" in p else pad_default
    crs, tr, w, h = new_grid(feats, cell, pad)
    geoms = to_crs([f["geom"] for f in feats], crs)
    return feats, geoms, crs, tr, w, h, cell


@rtool("euclidean_distance", "Euclidean distance", "Distance", "The straight-line distance (metres) from every cell to the nearest feature.",
       _p("sources", "From features", "layer"), CELL, _p("max_distance", "Area around them", "number", unit="m", min=0, default=1000))
def euclidean_distance_tool(p, inp):
    feats, geoms, crs, tr, w, h, cell = _sources_grid(inp, "sources", p, 1000)
    src = burn(feats, geoms, (h, w), tr) > 0
    d = ndimage.distance_transform_edt(~src, sampling=cell)
    g = Grid(np.ma.masked_array(d, mask=np.zeros(d.shape, bool)), crs, tr)
    lim = num(p, "max_distance", 0, 1e7, "Up to", 1000)
    out = np.where(d <= lim, d, np.nan) if lim > 0 else d
    return raster_out(f"Distance to {inp.layers['sources']['name']}", out, g, mask=~np.isfinite(out), style=ramp("Viridis", out, lo=0))


@rtool("euclidean_allocation", "Euclidean allocation", "Distance", "Every cell given the id (or a field's value) of its nearest feature: zones of nearest service.",
       _p("sources", "Features", "layer"), _p("field", "Value from field (else the feature id)", "field", of="sources", numeric=True, optional=True), CELL,
       _p("max_distance", "Area around them", "number", unit="m", min=0, default=1000))
def euclidean_allocation_tool(p, inp):
    feats, geoms, crs, tr, w, h, cell = _sources_grid(inp, "sources", p, 1000)
    f = p.get("field")
    vals = [(x["props"].get(f) if f else x["id"]) for x in feats]
    vals = [float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else np.nan for v in vals]
    idx = burn(feats, geoms, (h, w), tr, values=list(range(1, len(feats) + 1)))
    d, (ri, ci) = ndimage.distance_transform_edt(idx == 0, sampling=cell, return_indices=True)
    which = idx[ri, ci].astype(int)
    lut = np.array([np.nan] + vals)
    out = lut[which]
    lim = num(p, "max_distance", 0, 1e7, "Up to", 1000)
    if lim > 0:
        out = np.where(d <= lim, out, np.nan)
    g = Grid(np.ma.masked_array(out, mask=~np.isfinite(out)), crs, tr)
    u = np.unique(out[np.isfinite(out)])
    whole = np.all(u == np.round(u))
    return raster_out(f"Nearest {inp.layers['sources']['name']}", out, g, dtype="int32" if whole else "float32", categorical=bool(whole and u.size <= 50),
                      style=classes_style(u.astype(int).tolist()) if whole and u.size <= 50 else ramp("Spectral", out))


@rtool("cost_distance", "Cost distance", "Distance", "The least accumulated cost of travelling from the nearest source to every cell, over a cost surface "
       "(cost per metre: e.g. 1 on roads, 5 in bush, no data where impassable).",
       _p("sources", "Sources (features)", "layer"), _p("input", "Cost surface", "layer", geometry=["Raster"]))
def cost_distance_tool(p, inp):
    from skimage.graph import MCP_Geometric
    g = inp.grid("input")
    cost = np.ma.filled(g.data, -1.0)
    cost = np.where(np.isfinite(cost) & (cost >= 0), cost, -1.0)
    dxr, dy = g.cell_m()
    if g.crs.is_geographic and float(dxr.max() / dxr.min()) > 1.05:
        raise bad("Cost distance needs cells of the same size across the raster: reproject the cost surface to a projected system (e.g. UTM) first.", "input")
    dx = float(dxr.mean())
    feats = inp.vector("sources")
    geoms = to_crs([f["geom"] for f in feats], g.crs)
    srcmask = burn(feats, geoms, g.shape, g.transform) > 0
    starts = list(zip(*np.nonzero(srcmask & (cost >= 0))))
    if not starts:
        raise bad("None of the sources is on the cost surface.", "sources")
    m = MCP_Geometric(cost, fully_connected=True, sampling=(dy, dx))
    acc, _ = m.find_costs(starts)
    acc = np.where(np.isfinite(acc) & (cost >= 0), acc, np.nan)
    return raster_out(f"Cost distance from {inp.layers['sources']['name']}", acc, g, mask=~np.isfinite(acc), style=ramp("Viridis", acc, lo=0))


@rtool("cost_path", "Least-cost path", "Distance", "The cheapest route over a cost surface from the sources to each destination, as lines with their cost.",
       _p("sources", "From (features)", "layer"), _p("destinations", "To (points)", "layer", geometry=PTS), _p("input", "Cost surface", "layer", geometry=["Raster"]))
def cost_path_tool(p, inp):
    from skimage.graph import MCP_Geometric
    g = inp.grid("input")
    cost = np.ma.filled(g.data, -1.0)
    cost = np.where(np.isfinite(cost) & (cost >= 0), cost, -1.0)
    dxr, dy = g.cell_m()
    dx = float(dxr.mean())
    feats = inp.vector("sources")
    srcmask = burn(feats, to_crs([f["geom"] for f in feats], g.crs), g.shape, g.transform) > 0
    starts = list(zip(*np.nonzero(srcmask & (cost >= 0))))
    if not starts:
        raise bad("None of the sources is on the cost surface.", "sources")
    m = MCP_Geometric(cost, fully_connected=True, sampling=(dy, dx))
    acc, _ = m.find_costs(starts)
    x, y = g.centres()
    rows = []
    dests = inp.vector("destinations")
    for f, pt in zip(dests, to_crs([d["geom"] for d in dests], g.crs)):
        r, c = rowcol(g.transform, pt.x, pt.y)
        if not (0 <= r < g.shape[0] and 0 <= c < g.shape[1]) or not np.isfinite(acc[r, c]):
            continue
        path = m.traceback((r, c))
        if len(path) < 2:
            continue
        rr, cc = np.array(path).T
        line = to_wgs84([shapely.LineString(np.column_stack([x[rr, cc], y[rr, cc]]))], g.crs)[0]
        rows.append((line, {"destination_id": f["id"], "cost": round(float(acc[r, c]), 4)}))
    return Out(name=f"Least-cost paths to {inp.layers['destinations']['name']}", kind="vector", geometry_type="LineString", rows=rows,
               fields=[{"name": "destination_id", "type": "integer"}, {"name": "cost", "type": "number"}])


def _points_xy(inp, key, crs, field=None):
    feats = inp.vector(key)
    pts = to_crs([shapely.Point(f["geom"].representative_point()) if f["geom"].geom_type != "Point" else f["geom"] for f in feats], crs)
    xy = np.array([(q.x, q.y) for q in pts]) if pts else np.zeros((0, 2))
    if field:
        w = np.array([f["props"].get(field) if isinstance(f["props"].get(field), (int, float)) and not isinstance(f["props"].get(field), bool)
                      else np.nan for f in feats], float)
    else:
        w = np.ones(len(feats))
    return feats, xy, w


@rtool("kernel_density", "Kernel density", "Density", "A smooth surface of how densely points lie (per km²), each point spread over a radius (quartic kernel).",
       _p("points", "Points", "layer", geometry=PTS), _p("weight", "Count each point as (field)", "field", of="points", numeric=True, optional=True),
       _p("radius", "Search radius", "number", unit="m", min=1, default=1000), CELL)
def kernel_density_tool(p, inp):
    radius = num(p, "radius", 1, 1e6, "Search radius", 1000)
    feats0 = inp.vector("points")
    cell = num(p, "cell", 0.01, 1e6, "Cell size", 30)
    crs, tr, w, h = new_grid(feats0, cell, radius)
    feats, xy, wt = _points_xy(inp, "points", crs, p.get("weight"))
    ok = np.isfinite(wt)
    xy, wt = xy[ok], wt[ok]
    out = np.zeros((h, w))
    k = int(math.ceil(radius / cell))
    for (px, py), ww in zip(xy, wt):
        c, r = ~tr * (px, py)
        c0, r0 = int(c), int(r)
        r1, r2, c1, c2 = max(0, r0 - k), min(h, r0 + k + 1), max(0, c0 - k), min(w, c0 + k + 1)
        if r1 >= r2 or c1 >= c2:
            continue
        yy, xx = np.mgrid[r1:r2, c1:c2]
        cx, cy = tr * (xx + 0.5, yy + 0.5)
        d2 = ((cx - px) ** 2 + (cy - py) ** 2) / radius ** 2
        out[r1:r2, c1:c2] += np.where(d2 < 1, ww * 3 / (math.pi * radius ** 2) * (1 - d2) ** 2, 0)
    out *= 1e6                                    # per square kilometre
    g = Grid(np.ma.masked_array(out, mask=np.zeros(out.shape, bool)), crs, tr)
    o = raster_out(f"{inp.layers['points']['name']} density", out, g, style=ramp("Magma", out, lo=0))
    o.summary = {"points": int(len(xy)), "total": round(float(out.sum() * cell * cell / 1e6), 4)}
    return o


@rtool("point_density", "Point density", "Density", "How many points (or how much of a field) lie within a radius of each cell, per km².",
       _p("points", "Points", "layer", geometry=PTS), _p("weight", "Count each point as (field)", "field", of="points", numeric=True, optional=True),
       _p("radius", "Within", "number", unit="m", min=1, default=500), CELL)
def point_density_tool(p, inp):
    radius = num(p, "radius", 1, 1e6, "Within", 500)
    cell = num(p, "cell", 0.01, 1e6, "Cell size", 30)
    crs, tr, w, h = new_grid(inp.vector("points"), cell, radius)
    feats, xy, wt = _points_xy(inp, "points", crs, p.get("weight"))
    counts = np.zeros((h, w))
    ok = np.isfinite(wt)
    for (px, py), ww in zip(xy[ok], wt[ok]):
        c, r = ~tr * (px, py)
        if 0 <= int(r) < h and 0 <= int(c) < w:
            counts[int(r), int(c)] += ww
    k = int(math.ceil(radius / cell))
    yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
    disk = (np.hypot(yy, xx) * cell <= radius).astype(float)
    out = ndimage.convolve(counts, disk, mode="constant") / (disk.sum() * cell * cell / 1e6)
    g = Grid(np.ma.masked_array(out, mask=np.zeros(out.shape, bool)), crs, tr)
    return raster_out(f"{inp.layers['points']['name']} point density", out, g, style=ramp("Magma", out, lo=0))


INTERP = [_p("points", "Sample points", "layer", geometry=PTS), _p("field", "Value field", "field", of="points", numeric=True), CELL]


def _interp_grid(inp, p):
    cell = num(p, "cell", 0.01, 1e6, "Cell size", 30)
    crs, tr, w, h = new_grid(inp.vector("points"), cell, cell)
    feats, xy, z = _points_xy(inp, "points", crs, p.get("field"))
    ok = np.isfinite(z)
    if ok.sum() < 3:
        raise bad("Interpolation needs at least 3 points with a number in the value field.", "field")
    cx, cy = tr * (np.arange(w) + 0.5, np.zeros(w))
    _, ry = tr * (np.zeros(h), np.arange(h) + 0.5)
    X, Y = np.meshgrid(cx, ry)
    return xy[ok], z[ok], X, Y, crs, tr


def _grid_out(name, out, crs, tr):
    g = Grid(np.ma.masked_array(out, mask=~np.isfinite(out)), crs, tr)
    return raster_out(name, out, g, mask=~np.isfinite(out), style=ramp("Spectral", out, invert=True))


@rtool("idw", "IDW interpolation", "Interpolation", "A surface from sample points, each cell a distance-weighted average of the nearest samples.",
       *INTERP, _p("power", "Power", "number", min=0.1, default=2), _p("neighbours", "Nearest samples used", "number", min=1, default=12))
def idw_tool(p, inp):
    from scipy.spatial import cKDTree
    xy, z, X, Y, crs, tr = _interp_grid(inp, p)
    pw = num(p, "power", 0.1, 10, "Power", 2)
    k = int(min(num(p, "neighbours", 1, 100, "Nearest samples", 12), len(z)))
    tree = cKDTree(xy)
    q = np.column_stack([X.ravel(), Y.ravel()])
    out = np.empty(len(q))
    for i in range(0, len(q), 500_000):
        d, j = tree.query(q[i:i + 500_000], k=k)
        d, j = (d[:, None], j[:, None]) if k == 1 else (d, j)
        wgt = 1 / np.maximum(d, 1e-12) ** pw
        exact = d[:, 0] < 1e-9
        v = (wgt * z[j]).sum(1) / wgt.sum(1)
        v[exact] = z[j[exact, 0]]
        out[i:i + 500_000] = v
    return _grid_out(f"{inp.layers['points']['name']} {p.get('field')} (IDW)", out.reshape(X.shape), crs, tr)


@rtool("nearest_interpolation", "Nearest neighbour (Thiessen) surface", "Interpolation", "Each cell takes the value of the nearest sample point.", *INTERP)
def nearest_interp_tool(p, inp):
    from scipy.spatial import cKDTree
    xy, z, X, Y, crs, tr = _interp_grid(inp, p)
    _, j = cKDTree(xy).query(np.column_stack([X.ravel(), Y.ravel()]))
    return _grid_out(f"{inp.layers['points']['name']} {p.get('field')} (nearest)", z[j].reshape(X.shape), crs, tr)


@rtool("tin_interpolation", "Linear (TIN) interpolation", "Interpolation", "A surface of flat triangles between the sample points (no values outside them).", *INTERP)
def tin_interp_tool(p, inp):
    from scipy.interpolate import LinearNDInterpolator
    xy, z, X, Y, crs, tr = _interp_grid(inp, p)
    f = LinearNDInterpolator(xy, z)
    return _grid_out(f"{inp.layers['points']['name']} {p.get('field')} (TIN)", f(X, Y), crs, tr)


@rtool("spline", "Spline interpolation", "Interpolation", "A smooth surface that passes through the sample points (thin-plate spline).",
       *INTERP, _p("smoothing", "Smoothing (0 = through every point)", "number", min=0, default=0))
def spline_tool(p, inp):
    from scipy.interpolate import RBFInterpolator
    xy, z, X, Y, crs, tr = _interp_grid(inp, p)
    if len(z) > 20_000:
        raise bad("Spline works on up to 20,000 points: use IDW for more.", "points")
    sm = num(p, "smoothing", 0, 1e9, "Smoothing", 0)
    sc = xy.mean(0)
    f = RBFInterpolator(xy - sc, z, kernel="thin_plate_spline", smoothing=sm, neighbors=min(len(z), 64) if len(z) > 500 else None)
    q = np.column_stack([X.ravel(), Y.ravel()]) - sc
    out = np.concatenate([f(q[i:i + 200_000]) for i in range(0, len(q), 200_000)])
    return _grid_out(f"{inp.layers['points']['name']} {p.get('field')} (spline)", out.reshape(X.shape), crs, tr)


# =====================================================================================================================
# Conversion and management
# =====================================================================================================================
@rtool("raster_to_polygons", "Raster to polygons", "Conversion", "Patches of equal value as polygons, with their value and area (for class rasters).",
       R_IN, _p("simplify", "Smooth the cell steps", "bool", default=False))
def raster_to_polygons_tool(p, inp):
    g = inp.grid("input")
    a = np.ma.filled(g.data, np.nan)
    if not np.all(a[np.isfinite(a)] == np.round(a[np.isfinite(a)])):
        raise bad("Raster to polygons works on whole-number (class) rasters: reclassify or slice it first.", "input")
    rows_g, vals = [], []
    for geom, v in rfeatures.shapes(np.nan_to_num(a, nan=0).astype("int32"), mask=np.isfinite(a), transform=g.transform, connectivity=8):
        s = shapely.geometry.shape(geom)
        if p.get("simplify"):
            s = s.simplify(abs(g.transform.a) * 0.5, preserve_topology=True)
        rows_g.append(s)
        vals.append(int(v))
        if len(rows_g) > settings.max_features:
            raise bad(f"That makes more than {settings.max_features:,} polygons: sieve or generalize the raster first.")
    geo = to_wgs84(rows_g, g.crs)
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    rows = [(gg, {"value": v, "area_m2": round(abs(geod.geometry_area_perimeter(gg)[0]), 2)}) for gg, v in zip(geo, vals)]
    return Out(name=f"{inp.layers['input']['name']} polygons", kind="vector", geometry_type="Polygon", rows=rows,
               fields=[{"name": "value", "type": "integer"}, {"name": "area_m2", "type": "number"}])


@rtool("raster_to_points", "Raster to points", "Conversion", "A point at the centre of every cell with a value (every band's value as a field).", R_IN)
def raster_to_points_tool(p, inp):
    g = inp.bands("input")
    a = np.ma.filled(g.data, np.nan)
    ok = np.isfinite(a).any(0)
    if ok.sum() > settings.max_features:
        raise bad(f"That makes {int(ok.sum()):,} points; the most is {settings.max_features:,}. Resample the raster to larger cells first.")
    x, y = Grid(g.data[0], g.crs, g.transform).centres()
    t = Transformer.from_crs(PCRS.from_wkt(g.crs.to_wkt()), 4326, always_xy=True)
    lng, lat = t.transform(x[ok], y[ok])
    names = ["value"] if a.shape[0] == 1 else [f"band_{i + 1}" for i in range(a.shape[0])]
    vals = a[:, ok].T
    rows = [(shapely.Point(xx, yy), {n: (None if not np.isfinite(v) else float(v)) for n, v in zip(names, vv)}) for xx, yy, vv in zip(lng, lat, vals)]
    return Out(name=f"{inp.layers['input']['name']} points", kind="vector", geometry_type="Point", rows=rows,
               fields=[{"name": n, "type": "number"} for n in names])


@rtool("features_to_raster", "Features to raster", "Conversion", "Points, lines or polygons burned into a raster: a field's value (or 1) where a feature lies.",
       _p("features", "Features", "layer"), _p("field", "Value from field (else 1)", "field", of="features", numeric=True, optional=True), CELL,
       _p("all_touched", "Every cell a feature touches (else cells whose centre it covers)", "bool", default=False))
def features_to_raster_tool(p, inp):
    feats = inp.vector("features")
    cell = num(p, "cell", 0.01, 1e6, "Cell size", 30)
    crs, tr, w, h = new_grid(feats, cell, cell)
    geoms = to_crs([f["geom"] for f in feats], crs)
    f = p.get("field")
    vals = [x["props"].get(f) if f else 1 for x in feats]
    shapes = [(gg, float(v)) for gg, v in zip(geoms, vals) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not shapes:
        raise bad("No feature has a number in that field.", "field")
    out = rfeatures.rasterize(shapes, out_shape=(h, w), transform=tr, fill=np.nan, all_touched=bool(p.get("all_touched")), dtype="float64")
    g = Grid(np.ma.masked_invalid(out), crs, tr)
    u = np.unique(out[np.isfinite(out)])
    whole = bool(np.all(u == np.round(u)))
    return raster_out(f"{inp.layers['features']['name']} raster", out, g, dtype="int32" if whole else "float32", categorical=whole and u.size <= 50,
                      style=classes_style(u.astype(int).tolist()) if whole and u.size <= 50 else ramp("Viridis", out))


RESAMPLE = [["nearest", "Nearest (classes)"], ["bilinear", "Bilinear (continuous)"], ["cubic", "Cubic"], ["average", "Average"], ["mode", "Majority"]]


def _warp(inp, key, dst_crs, res=None, method="bilinear", bounds=None) -> Out:
    from rasterio.warp import calculate_default_transform
    meta = inp.meta(key)
    with rasterio.open(rasters.local(meta["key"])) as src:
        left, bottom, right, top = bounds or src.bounds
        tr, w, h = calculate_default_transform(src.crs, dst_crs, src.width, src.height, left, bottom, right, top, resolution=res)
        if w * h > settings.max_raster_cells:
            raise bad(f"That makes {w * h:,} cells; the most is {settings.max_raster_cells:,}. Use larger cells.", "cell")
        out = np.full((src.count, h, w), np.nan)
        for b in range(1, src.count + 1):
            reproject(rasterio.band(src, b), out[b - 1], dst_transform=tr, dst_crs=dst_crs, dst_nodata=np.nan, src_nodata=src.nodata,
                      resampling=getattr(Resampling, method))
        cat = meta["bands"][0].get("values") is not None and method in ("nearest", "mode")
        return Out(name="", data=np.ma.masked_invalid(out), crs=dst_crs, transform=tr, dtype=meta["dtype"] if cat or method == "nearest" else "float32",
                   categorical=cat, style=inp.layers[key]["style"], names=[b.get("name") for b in meta["bands"]])


@rtool("reproject_raster", "Project raster", "Conversion", "The raster in another coordinate system (e.g. a UTM zone, so cells are square metres).",
       R_IN, _p("epsg", "To (EPSG code; e.g. 32632 for UTM 32N, 26332 for Minna / Nigeria Mid Belt)", "text", default="32632"),
       _p("cell", "Cell size (0 = keep about the same)", "number", min=0, default=0), _p("method", "Resampling", "choice", default="bilinear", choices=RESAMPLE))
def reproject_tool(p, inp):
    try:
        crs = CRS.from_epsg(int(str(p.get("epsg") or "").upper().replace("EPSG:", "").strip()))
    except Exception:  # noqa: BLE001
        raise bad("Give an EPSG code, such as 32632 or 26332.", "epsg") from None
    c = num(p, "cell", 0, 1e7, "Cell size", 0)
    out = _warp(inp, "input", crs, c or None, p.get("method", "bilinear"))
    out.name = f"{inp.layers['input']['name']} EPSG {crs.to_epsg()}"
    return out


@rtool("resample", "Resample", "Conversion", "The raster with larger (or smaller) cells.", R_IN,
       _p("cell", "New cell size (in the raster's units: metres, or degrees for longitude/latitude)", "number", min=0.0000001, default=30),
       _p("method", "Resampling", "choice", default="average", choices=RESAMPLE))
def resample_tool(p, inp):
    meta = inp.meta("input")
    c = num(p, "cell", 1e-9, 1e9, "Cell size", 30)
    out = _warp(inp, "input", CRS.from_user_input(meta["crs"]), c, p.get("method", "average"))
    out.name = f"{inp.layers['input']['name']} {c:g} cells"
    return out


@rtool("clip_raster", "Clip raster", "Conversion", "The part of the raster inside polygons (cells outside become no data), cut to their extent.",
       R_IN, _p("clip", "Clip to (polygons)", "layer", geometry=POLY), _p("crop", "Cut to the polygons' extent", "bool", default=True))
def clip_raster_tool(p, inp):
    from rasterio.mask import mask as rmask
    meta = inp.meta("input")
    with rasterio.open(rasters.local(meta["key"])) as src:
        geoms = to_crs([f["geom"] for f in inp.vector("clip")], src.crs)
        try:
            data, tr = rmask(src, geoms, crop=bool(p.get("crop", True)), filled=False)
        except ValueError:
            raise bad("The polygons do not overlap the raster.", "clip") from None
        data = np.ma.masked_invalid(data.astype("float64"))
        return Out(name=f"{inp.layers['input']['name']} clipped", data=data, crs=src.crs, transform=tr, dtype=meta["dtype"],
                   categorical="values" in meta["bands"][0], style=inp.layers["input"]["style"], names=[b.get("name") for b in meta["bands"]])


@rtool("mosaic", "Mosaic", "Conversion", "Several rasters joined into one (tiles of a survey, scenes of an area); where they overlap, by the rule chosen.",
       _p("rasters", "Rasters", "layers", geometry=["Raster"], min=2),
       _p("overlap", "Where they overlap", "choice", default="first", choices=[["first", "The first raster"], ["last", "The last raster"], ["mean", "Mean"], ["min", "Minimum"], ["max", "Maximum"]]))
def mosaic_tool(p, inp):
    from rasterio.merge import merge
    ls = inp.layers["rasters"]
    srcs = [rasterio.open(rasters.local(lay["raster"]["key"])) for lay in ls]
    try:
        crs = srcs[0].crs
        if any(s.crs != crs for s in srcs[1:]):
            raise bad("The rasters are in different coordinate systems: project them to one first.", "rasters")
        if any(s.count != srcs[0].count for s in srcs[1:]):
            raise bad("The rasters have different numbers of bands.", "rasters")
        method = {"first": "first", "last": "last", "min": "min", "max": "max", "mean": "sum"}[p.get("overlap", "first")]
        data, tr = merge(srcs, method=method, nodata=np.nan, dtype="float64")
        if p.get("overlap") == "mean":
            ones, _ = merge(srcs, method="count")
            data = data / np.where(ones > 0, ones, np.nan)
        if data.shape[1] * data.shape[2] > settings.max_raster_cells:
            raise bad("The mosaic would be too large: resample the rasters first.")
        meta = ls[0]["raster"]
        return Out(name=f"Mosaic of {len(ls)} rasters", data=np.ma.masked_invalid(data), crs=crs, transform=tr, dtype=meta["dtype"],
                   categorical="values" in meta["bands"][0], style=ls[0]["style"])
    finally:
        for s in srcs:
            s.close()


@rtool("composite_bands", "Composite bands", "Conversion", "Single-band rasters stacked into one multi-band raster (e.g. separate Landsat bands into one image).",
       _p("rasters", "Rasters, in band order", "layers", geometry=["Raster"], min=2))
def composite_bands_tool(p, inp):
    ls = inp.layers["rasters"]
    first = Inputs({"x": ls[0]}, {}).grid("x")
    stack = [first.data] + [Inputs({"x": lay}, {}).aligned("x", first) for lay in ls[1:]]
    data = np.ma.stack(stack)
    names = [lay["name"] for lay in ls]
    st = {"type": "raster", "mode": "rgb", "bands": [1, 2, 3] if len(ls) >= 3 else [1, 2, 2],
          "mins": [float(np.percentile(s.compressed(), 2)) if s.count() else 0 for s in stack[:3]] + [0] * (3 - min(3, len(ls))),
          "maxs": [float(np.percentile(s.compressed(), 98)) if s.count() else 1 for s in stack[:3]] + [1] * (3 - min(3, len(ls))), "opacity": 1}
    return Out(name=f"Composite of {len(ls)} bands", data=data, crs=first.crs, transform=first.transform, dtype=ls[0]["raster"]["dtype"], style=st, names=names)


@rtool("extract_bands", "Extract bands", "Conversion", "Some bands of a multi-band raster, as a new raster (e.g. 4, 3, 2 for a false-colour image).",
       R_IN, _p("bands", "Bands (separated by commas)", "text", default="1"))
def extract_bands_tool(p, inp):
    meta = inp.meta("input")
    try:
        bs = [int(x) for x in str(p.get("bands") or "").replace(";", ",").split(",") if x.strip()]
    except ValueError:
        raise bad("Give band numbers separated by commas, e.g. 4, 3, 2.", "bands") from None
    if not bs or any(not 1 <= b <= meta["count"] for b in bs):
        raise bad(f"Choose bands from 1 to {meta['count']}.", "bands")
    g = inp.bands("input")
    data = g.data[[b - 1 for b in bs]]
    if len(bs) >= 3:
        st = {"type": "raster", "mode": "rgb", "bands": [1, 2, 3], "mins": [meta["bands"][b - 1].get("p2", 0) for b in bs[:3]],
              "maxs": [meta["bands"][b - 1].get("p98", 255) for b in bs[:3]], "opacity": 1}
    else:
        st = ramp("Gray", data[0])
    return Out(name=f"{inp.layers['input']['name']} bands {', '.join(map(str, bs))}", data=data, crs=g.crs, transform=g.transform, dtype=meta["dtype"],
               style=st, names=[meta["bands"][b - 1].get("name") for b in bs])


@rtool("copy_raster", "Copy raster", "Conversion", "A copy of the raster, optionally as another number type or with a no-data value set.",
       R_IN, _p("dtype", "Number type", "choice", default="same", choices=[["same", "As it is"], ["uint8", "Whole 0–255"], ["int16", "Whole ±32,767"],
                                                                      ["int32", "Whole (large)"], ["float32", "Decimal"]]),
       _p("nodata", "Also treat this value as no data", "text", optional=True))
def copy_raster_tool(p, inp):
    meta = inp.meta("input")
    g = inp.bands("input")
    data = g.data
    nd = str(p.get("nodata") or "").strip()
    if nd:
        try:
            data = np.ma.masked_where(np.ma.filled(data, np.nan) == float(nd), data)
        except ValueError:
            raise bad("The no-data value must be a number.", "nodata") from None
    dt = meta["dtype"] if p.get("dtype", "same") == "same" else p["dtype"]
    if dt != "float32" and not dt.startswith("float"):
        info = np.iinfo(np.dtype(dt))
        data = np.ma.masked_where((np.ma.filled(data, 0) < info.min) | (np.ma.filled(data, 0) > info.max), np.ma.round(data))
    return Out(name=f"{inp.layers['input']['name']} copy", data=data, crs=g.crs, transform=g.transform, dtype=dt,
               style=inp.layers["input"]["style"], categorical="values" in meta["bands"][0], names=[b.get("name") for b in meta["bands"]])


# =====================================================================================================================
# Imagery
# =====================================================================================================================
def _pixels(g: Grid):
    a = np.ma.filled(g.data, np.nan)
    ok = np.isfinite(a).all(0)
    return a, ok, a[:, ok].T


@rtool("unsupervised_classification", "Unsupervised classification", "Imagery", "The image's pixels grouped into classes of similar colour (k-means on all bands).",
       _p("input", "Image", "layer", geometry=["Raster"]), _p("classes", "Classes", "number", min=2, default=6), _p("seed", "Seed", "number", default=1))
def unsupervised_tool(p, inp):
    from scipy.cluster.vq import kmeans2, vq
    g = inp.bands("input")
    a, ok, X = _pixels(g)
    k = int(num(p, "classes", 2, 50, "Classes", 6))
    if len(X) < k:
        raise bad("The image has too few cells for that many classes.")
    mu, sd = X.mean(0), X.std(0) + 1e-12
    Xs = (X - mu) / sd
    rng = np.random.default_rng(int(num(p, "seed", 0, 1e9, "Seed", 1)))
    sample = Xs[rng.choice(len(Xs), min(len(Xs), 200_000), replace=False)]
    cent, _ = kmeans2(sample, k, minit="++", seed=int(num(p, "seed", 0, 1e9, "Seed", 1)), iter=30)
    lab, _ = vq(Xs, cent)
    order = np.argsort(cent.mean(1))                      # number classes from dark to bright
    rank = np.empty(k, int)
    rank[order] = np.arange(1, k + 1)
    out = np.full(a.shape[1:], np.nan)
    out[ok] = rank[lab]
    g1 = Grid(np.ma.masked_invalid(out), g.crs, g.transform)
    return raster_out(f"{inp.layers['input']['name']} {k} classes", out, g1, dtype="uint8", categorical=True, style=classes_style(list(range(1, k + 1))))


@rtool("supervised_classification", "Supervised classification", "Imagery", "Every pixel given the class it looks most like, learnt from training polygons "
       "(maximum likelihood or minimum distance), with the accuracy on the training pixels.",
       _p("input", "Image", "layer", geometry=["Raster"]), _p("training", "Training polygons", "layer", geometry=POLY),
       _p("field", "Class field (whole numbers)", "field", of="training"),
       _p("method", "Method", "choice", default="ml", choices=[["ml", "Maximum likelihood"], ["md", "Minimum distance"]]))
def supervised_tool(p, inp):
    g = inp.bands("input")
    a, ok, _ = _pixels(g)
    feats = inp.vector("training")
    geoms = to_crs([f["geom"] for f in feats], g.crs)
    fld = p.get("field")
    labels = {}
    shapes = []
    for f, gg in zip(feats, geoms):
        v = f["props"].get(fld)
        try:
            c = int(v)
        except (TypeError, ValueError):
            continue
        labels.setdefault(c, str(v))
        shapes.append((gg, c))
    if len(labels) < 2:
        raise bad("The training polygons need at least two classes (whole numbers) in the class field.", "field")
    train = rfeatures.rasterize(shapes, out_shape=a.shape[1:], transform=g.transform, fill=0, dtype="int32")
    classes = sorted(labels)
    X = a[:, ok].T
    stats = {}
    for c in classes:
        T = a[:, (train == c) & ok].T
        if len(T) < a.shape[0] + 1:
            raise bad(f"Class {c} has only {len(T)} training pixels: draw larger training polygons.", "training")
        cov = np.cov(T.T) + np.eye(a.shape[0]) * 1e-6
        stats[c] = (T.mean(0), np.linalg.inv(cov), np.linalg.slogdet(cov)[1])

    def classify(Z):
        best = np.full(len(Z), -np.inf)
        lab = np.zeros(len(Z), int)
        for c in classes:
            m, ic, ld = stats[c]
            d = Z - m
            s = -(0.5 * ld + 0.5 * np.einsum("ij,jk,ik->i", d, ic, d)) if p.get("method", "ml") == "ml" else -np.einsum("ij,ij->i", d, d)
            better = s > best
            best, lab = np.where(better, s, best), np.where(better, c, lab)
        return lab
    out = np.full(a.shape[1:], np.nan)
    flat = np.concatenate([classify(X[i:i + 500_000]) for i in range(0, len(X), 500_000)]) if len(X) else np.zeros(0)
    out[ok] = flat
    tmask = (train > 0) & ok
    acc = float((out[tmask] == train[tmask]).mean()) if tmask.any() else None
    g1 = Grid(np.ma.masked_invalid(out), g.crs, g.transform)
    o = raster_out(f"{inp.layers['input']['name']} classified", out, g1, dtype="int32", categorical=True, style=classes_style(classes, labels))
    o.summary = {"training_accuracy": round(acc, 4) if acc is not None else None, "classes": len(classes)}
    return o


@rtool("principal_components", "Principal components", "Imagery", "The bands turned into uncorrelated components, the first holding the most variation.",
       _p("input", "Image", "layer", geometry=["Raster"]), _p("components", "Components to keep", "number", min=1, default=3))
def pca_tool(p, inp):
    g = inp.bands("input")
    a, ok, X = _pixels(g)
    k = int(min(num(p, "components", 1, 64, "Components", 3), a.shape[0]))
    mu = X.mean(0)
    vals, vecs = np.linalg.eigh(np.cov((X - mu).T).reshape(a.shape[0], a.shape[0]))
    order = np.argsort(vals)[::-1][:k]
    comps = (X - mu) @ vecs[:, order]
    out = np.full((k,) + a.shape[1:], np.nan)
    out[:, ok] = comps.T
    share = vals[order] / vals.sum()
    st = {"type": "raster", "mode": "rgb", "bands": [1, 2, 3], "mins": [float(np.percentile(c, 2)) for c in comps.T[:3]] + [0] * (3 - min(3, k)),
          "maxs": [float(np.percentile(c, 98)) for c in comps.T[:3]] + [1] * (3 - min(3, k)), "opacity": 1} if k >= 3 else ramp("Gray", out[0])
    o = Out(name=f"{inp.layers['input']['name']} components", data=np.ma.masked_invalid(out), crs=g.crs, transform=g.transform, style=st,
            names=[f"PC{i + 1} ({s:.1%})" for i, s in enumerate(share)])
    o.summary = {"variance_explained": [round(float(s), 4) for s in share]}
    return o


FILTERS = [["low", "Smooth (low pass)"], ["gauss", "Gaussian blur"], ["high", "Edges (high pass)"], ["sharpen", "Sharpen"], ["sobel", "Edge strength (Sobel)"],
           ["laplace", "Laplacian"]]


@rtool("filter", "Filter", "Imagery", "Smoothing, sharpening or edge-finding filters on one band.", R_IN, BAND,
       _p("kind", "Filter", "choice", default="low", choices=FILTERS), _p("size", "Strength (cells)", "number", min=1, default=1))
def filter_tool(p, inp):
    g = inp.grid("input", p.get("band", 1))
    a = filled_for_neighbours(g)
    k = int(num(p, "size", 1, 25, "Strength", 1))
    kind = p.get("kind", "low")
    if kind == "low":
        out = ndimage.uniform_filter(a, 2 * k + 1, mode="nearest")
    elif kind == "gauss":
        out = ndimage.gaussian_filter(a, k, mode="nearest")
    elif kind == "high":
        out = a - ndimage.uniform_filter(a, 2 * k + 1, mode="nearest")
    elif kind == "sharpen":
        out = a + (a - ndimage.uniform_filter(a, 2 * k + 1, mode="nearest"))
    elif kind == "sobel":
        out = np.hypot(ndimage.sobel(a, 0, mode="nearest"), ndimage.sobel(a, 1, mode="nearest"))
    else:
        out = ndimage.laplace(a, mode="nearest")
    return raster_out(f"{inp.layers['input']['name']} {kind} filter", out, g, style=ramp("Gray", out))


@rtool("stretch", "Contrast stretch", "Imagery", "Bands stretched to 0–255 for display or export: between percentiles, or histogram-equalized.",
       R_IN, _p("method", "Stretch", "choice", default="percent", choices=[["percent", "Between the 2nd and 98th percentiles"], ["minmax", "Minimum to maximum"],
                                                                        ["equalize", "Histogram equalization"]]))
def stretch_tool(p, inp):
    g = inp.bands("input")
    a = np.ma.filled(g.data, np.nan)
    out = np.full(a.shape, np.nan)
    for i, b in enumerate(a):
        v = b[np.isfinite(b)]
        if not v.size:
            continue
        m = p.get("method", "percent")
        if m == "equalize":
            srt = np.sort(v)
            out[i] = np.searchsorted(srt, b, side="right") / srt.size * 255
        else:
            lo, hi = (np.percentile(v, 2), np.percentile(v, 98)) if m == "percent" else (v.min(), v.max())
            out[i] = np.clip((b - lo) / ((hi - lo) or 1), 0, 1) * 255
    out = np.where(np.isfinite(a), np.round(out), np.nan)
    st = {"type": "raster", "mode": "rgb", "bands": [1, 2, 3], "mins": [0, 0, 0], "maxs": [255, 255, 255], "opacity": 1} if a.shape[0] >= 3 else \
        {"type": "raster", "mode": "ramp", "band": 1, "min": 0, "max": 255, "ramp": "Gray", "opacity": 1}
    return Out(name=f"{inp.layers['input']['name']} stretched", data=np.ma.masked_invalid(out), crs=g.crs, transform=g.transform, dtype="uint8", style=st)


@rtool("change_detection", "Change detection", "Imagery", "Where a value rose or fell between two dates by more than a threshold (e.g. NDVI before and after clearing).",
       _p("input", "Before", "layer", geometry=["Raster"]), _p("after", "After", "layer", geometry=["Raster"]), BAND,
       _p("threshold", "Change of at least", "number", min=0, default=0.1))
def change_detection_tool(p, inp):
    g = inp.grid("input", p.get("band", 1))
    after = inp.aligned("after", g, int(p.get("band", 1) or 1))
    t = num(p, "threshold", 0, 1e300, "Threshold", 0.1)
    d = np.ma.filled(after - g.data, np.nan)
    out = np.where(np.isfinite(d), np.where(d >= t, 3, np.where(d <= -t, 1, 2)), np.nan)
    area = g.cell_area() * np.ones(g.shape)
    o = raster_out(f"{inp.layers['input']['name']} change", out, g, mask=~np.isfinite(out), dtype="uint8", categorical=True,
                   style=classes_style([1, 2, 3], {1: "Decrease", 2: "No change", 3: "Increase"}, {1: "#d7191c", 2: "#e0e0e0", 3: "#1a9641"}))
    o.summary = {"decrease_m2": round(float(area[out == 1].sum()), 2), "increase_m2": round(float(area[out == 3].sum()), 2)}
    return o


def catalog() -> list[dict]:
    return [{"id": k, **v} for k, v in RTOOLS.items()]
