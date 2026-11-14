"""Georeferencing: placing an image that has no coordinates (a scanned map, a plan, an aerial photo, a drone frame), or
re-placing a raster that is in the wrong place, from control points: pixels on the image matched to places on the map.

The transformation from image to map is fitted to the points by least squares (shift, rotate and scale; affine;
polynomials of order 2 and 3) or as a thin plate spline that passes through every point (for rubber-sheeting old,
distorted maps). Each point's residual, how far the fitted transformation puts it from where it was placed, shows
which points are wrong. Warping fits the reverse transformation, map to image, and samples the image at every cell of
the new grid; cells whose reverse and forward mappings disagree (folds of a polynomial beyond its points) are left empty.
"""
import io
import math
from dataclasses import dataclass

import numpy as np
import rasterio
from pyproj import CRS as PCRS
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.transform import from_origin
from scipy import ndimage

METHODS = {  # id: (label, fewest points)
    "similarity": ("Shift, rotate and scale (Helmert)", 2),
    "affine": ("Affine (first-order polynomial)", 3),
    "poly2": ("Second-order polynomial", 6),
    "poly3": ("Third-order polynomial", 10),
    "tps": ("Thin plate spline (rubber sheet)", 3),
}


class GeorefError(ValueError):
    pass


# =====================================================================================================================
# Fitting
# =====================================================================================================================
def _terms(x: np.ndarray, y: np.ndarray, order: int) -> np.ndarray:
    return np.stack([x ** i * y ** j for n in range(order + 1) for j in range(n + 1) for i in [n - j]], axis=-1)


@dataclass
class Fit:
    """A transformation from one plane to another, fitted to point pairs."""
    method: str
    f: object

    def __call__(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.f(np.asarray(x, float), np.asarray(y, float))


def fit(src: np.ndarray, dst: np.ndarray, method: str) -> Fit:
    """src, dst: (n, 2). Coordinates are centred and scaled first so polynomials stay well conditioned."""
    if method not in METHODS:
        raise GeorefError("Choose a transformation from the list.")
    need = METHODS[method][1]
    if len(src) < need:
        raise GeorefError(f"{METHODS[method][0]} needs at least {need} points; there {'is' if len(src) == 1 else 'are'} {len(src)}.")
    s0, ss = src.mean(0), (src.std(0).max() or 1.0)
    d0, ds = dst.mean(0), (dst.std(0).max() or 1.0)
    S, D = (src - s0) / ss, (dst - d0) / ds
    if len(src) >= 3 and method != "similarity" and np.linalg.matrix_rank(np.c_[S, np.ones(len(S))], tol=1e-6) < 3:
        raise GeorefError("The points lie on one line: spread them around the image, ideally near its corners and edges.")
    if method == "similarity":
        # x' = a x − b y + c, y' = b x + a y + d
        A = np.zeros((2 * len(S), 4))
        A[0::2] = np.c_[S[:, 0], -S[:, 1], np.ones(len(S)), np.zeros(len(S))]
        A[1::2] = np.c_[S[:, 1], S[:, 0], np.zeros(len(S)), np.ones(len(S))]
        (a, b, c, d), *_ = np.linalg.lstsq(A, D.reshape(-1), rcond=None)

        def f(x, y):
            u, v = (x - s0[0]) / ss, (y - s0[1]) / ss
            return (a * u - b * v + c) * ds + d0[0], (b * u + a * v + d) * ds + d0[1]
        return Fit(method, f)
    if method == "tps":
        from scipy.interpolate import RBFInterpolator
        if len(np.unique(S.round(9), axis=0)) < len(S):
            raise GeorefError("Two points are on the same pixel: remove one of them.")
        rbf = RBFInterpolator(S, D, kernel="thin_plate_spline", degree=1)

        def f(x, y):
            shape = np.shape(x)
            P = np.c_[(np.ravel(x) - s0[0]) / ss, (np.ravel(y) - s0[1]) / ss]
            out = np.concatenate([rbf(P[i:i + 200_000]) for i in range(0, len(P), 200_000)]) if len(P) else np.zeros((0, 2))
            return (out[:, 0] * ds + d0[0]).reshape(shape), (out[:, 1] * ds + d0[1]).reshape(shape)
        return Fit(method, f)
    order = {"affine": 1, "poly2": 2, "poly3": 3}[method]
    T = _terms(S[:, 0], S[:, 1], order)
    coef, *_ = np.linalg.lstsq(T, D, rcond=None)

    def f(x, y):
        out = _terms((x - s0[0]) / ss, (y - s0[1]) / ss, order) @ coef
        return out[..., 0] * ds + d0[0], out[..., 1] * ds + d0[1]
    return Fit(method, f)


# =====================================================================================================================
# Points and where they are worked out
# =====================================================================================================================
def utm_for(lng: float, lat: float) -> CRS:
    zone = int((lng + 180) // 6) % 60 + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def active(points: list[dict]) -> list[dict]:
    good = []
    for p in points or []:
        if p.get("on", True) is False:
            continue
        try:
            vals = [float(p[k]) for k in ("col", "row", "lng", "lat")]
        except (KeyError, TypeError, ValueError):
            raise GeorefError("Each control point needs its image position and its map position.") from None
        if not all(map(math.isfinite, vals)) or not (-180 <= vals[2] <= 180 and -90 <= vals[3] <= 90):
            raise GeorefError("A control point's map position is not a longitude and latitude.")
        good.append(p)
    return good


def target_crs(points: list[dict], epsg: int | None) -> CRS:
    if epsg:
        try:
            return CRS.from_epsg(int(epsg))
        except Exception:  # noqa: BLE001
            raise GeorefError(f"EPSG:{epsg} is not a coordinate system PolyGIS knows.") from None
    lng = float(np.mean([p["lng"] for p in points])) if points else 0.0
    lat = float(np.mean([p["lat"] for p in points])) if points else 0.0
    return utm_for(lng, lat)


def _to(crs: CRS) -> Transformer:
    return Transformer.from_crs(4326, PCRS.from_wkt(crs.to_wkt()), always_xy=True)


def pairs(points: list[dict], crs: CRS) -> tuple[np.ndarray, np.ndarray]:
    pix = np.array([[float(p["col"]), float(p["row"])] for p in points])
    x, y = _to(crs).transform([float(p["lng"]) for p in points], [float(p["lat"]) for p in points])
    return pix, np.c_[x, y]


def _metres(crs: CRS) -> float:
    if crs.is_geographic:
        return 111_320.0
    f = crs.linear_units_factor[1] if crs.linear_units_factor else 1.0
    return float(f)


def residuals(points: list[dict], method: str, crs: CRS, size: tuple[int, int]) -> dict:
    """How far the fitted transformation puts each active point from where it was placed (metres on the map and pixels
    on the image), and the root mean square of those errors."""
    pts = active(points)
    pix, xy = pairs(pts, crs)
    fwd = fit(pix, xy, method)
    inv = fit(xy, pix, method)
    px, py = fwd(pix[:, 0], pix[:, 1])
    ic, ir = inv(xy[:, 0], xy[:, 1])
    k = _metres(crs)
    out = []
    for i, p in enumerate(pts):
        dx, dy = (px[i] - xy[i, 0]) * k, (py[i] - xy[i, 1]) * k
        out.append({"id": p.get("id"), "dx_m": round(float(dx), 3), "dy_m": round(float(dy), 3), "error_m": round(float(math.hypot(dx, dy)), 3),
                    "error_px": round(float(math.hypot(ic[i] - pix[i, 0], ir[i] - pix[i, 1])), 2)})
    e = np.array([r["error_m"] for r in out])
    cell = cell_size(fwd, *size)
    return {"method": method, "crs": crs.to_string(), "points": out, "rmse_m": round(float(np.sqrt((e ** 2).mean())), 3) if len(e) else None,
            "rmse_px": round(float(np.sqrt(np.mean([r["error_px"] ** 2 for r in out]))), 2) if out else None,
            "cell_m": round(cell * k, 4), "exact": method == "tps"}


def cell_size(fwd: Fit, w: int, h: int) -> float:
    """The ground size of one image pixel (in the target's units), from the transformation's local scale at the image centre."""
    cx, cy = w / 2, h / 2
    x0, y0 = fwd(np.array([cx, cx + 1, cx]), np.array([cy, cy, cy + 1]))
    sx = math.hypot(x0[1] - x0[0], y0[1] - y0[0])
    sy = math.hypot(x0[2] - x0[0], y0[2] - y0[0])
    s = math.sqrt(max(sx * sy, 1e-30))
    if not math.isfinite(s) or s <= 0:
        raise GeorefError("The points do not give a usable transformation: check that none is badly misplaced.")
    return float(f"{s:.3g}")


# =====================================================================================================================
# Warping
# =====================================================================================================================
def _border(w: int, h: int, n: int = 64):
    t = np.linspace(0, 1, n)
    xs = np.r_[t * w, np.full(n, w), (1 - t) * w, np.zeros(n)]
    ys = np.r_[np.zeros(n), t * h, np.full(n, h), (1 - t) * h]
    return xs, ys


def warp(src_path, points: list[dict], method: str, crs: CRS, cell: float | None = None, resampling: str = "bilinear",
         most: int = 400_000_000, out_px: int | None = None):
    """The image warped onto a north-up grid in `crs`. Returns (bands, rows, cols) as float with NaN for no data, the
    transform, and the cell size. `out_px` caps the larger side (for previews)."""
    pts = active(points)
    pix, xy = pairs(pts, crs)
    fwd = fit(pix, xy, method)
    inv = fit(xy, pix, method)
    with rasterio.open(src_path) as src:
        w, h, count = src.width, src.height, src.count
        bx, by = fwd(*_border(w, h))
        if not (np.isfinite(bx).all() and np.isfinite(by).all()):
            raise GeorefError("The transformation sends part of the image to infinity: check the points.")
        x0, x1, y0, y1 = float(bx.min()), float(bx.max()), float(by.min()), float(by.max())
        c = cell or cell_size(fwd, w, h)
        if out_px:
            c = max(c, max(x1 - x0, y1 - y0) / out_px)
        cols, rows = max(1, math.ceil((x1 - x0) / c)), max(1, math.ceil((y1 - y0) / c))
        if cols * rows * count > most:
            raise GeorefError(f"The result would have {cols * rows:,} cells: the points place the image far larger than it is "
                              "(check for a misplaced point), or use larger cells.")
        # read the image (shrunk for a preview, so a large scan still previews fast)
        k = 1.0
        if out_px:
            k = max(1.0, max(w, h) / (out_px * 2))
        a = src.read(out_shape=(count, max(1, int(h / k)), max(1, int(w / k))), masked=True).astype("float32")
        data = np.ma.filled(a, np.nan)
    transform = from_origin(x0, y1, c, c)
    out = np.full((count, rows, cols), np.nan, "float32")
    order = 0 if resampling == "nearest" else 1
    tol = 2.5 * c
    step = max(1, 2_000_000 // cols)
    for r0 in range(0, rows, step):
        rr = np.arange(r0, min(rows, r0 + step))
        X, Y = np.meshgrid(x0 + (np.arange(cols) + 0.5) * c, y1 - (rr + 0.5) * c)
        sc, sr = inv(X, Y)
        fx, fy = fwd(sc, sr)
        ok = (sc >= 0) & (sc < w) & (sr >= 0) & (sr < h) & (np.hypot(fx - X, fy - Y) < tol)
        sc_k, sr_k = sc / k - 0.5, sr / k - 0.5
        for b in range(count):
            band = data[b]
            filled = np.where(np.isfinite(band), band, 0)
            v = ndimage.map_coordinates(filled, [sr_k, sc_k], order=order, mode="nearest")
            hole = ndimage.map_coordinates(np.isfinite(band).astype("float32"), [sr_k, sc_k], order=order, mode="nearest") < 0.5
            out[b, rr] = np.where(ok & ~hole, v, np.nan)
    return out, transform, c


def preview_png(src_path, points, method, size: int = 1024) -> tuple[bytes, list[list[float]]]:
    """The warped image as a PNG in Web Mercator, and its corners in longitude and latitude (for a map image overlay)."""
    from PIL import Image
    merc = CRS.from_epsg(3857)
    a, t, c = warp(src_path, points, method, merc, out_px=size, most=size * size * 64)
    with rasterio.open(src_path) as s:
        rgba = to_rgba(a, is_bytes=s.dtypes[0] == "uint8")          # blended byte values are not whole numbers, but still bytes
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    back = Transformer.from_crs(3857, 4326, always_xy=True)
    h, w = a.shape[1:]
    xs, ys = [t.c, t.c + w * c, t.c + w * c, t.c], [t.f, t.f, t.f - h * c, t.f - h * c]
    lng, lat = back.transform(xs, ys)
    return buf.getvalue(), [[round(x, 8), round(y, 8)] for x, y in zip(lng, lat)]


def to_rgba(a: np.ndarray, is_bytes: bool | None = None) -> np.ndarray:
    """Bands as an 8-bit RGBA picture: the first three as colour (or one as grey), stretched unless they are already bytes."""
    bands = a[:3] if a.shape[0] >= 3 else np.repeat(a[:1], 3, 0)
    ok = np.isfinite(bands).all(0)
    out = np.zeros(bands.shape[1:] + (4,), "uint8")
    v = bands[:, ok]
    if v.size:
        if is_bytes is None:
            is_bytes = np.nanmin(v) >= 0 and np.nanmax(v) <= 255 and np.allclose(v, np.round(v))
        for i in range(3):
            b = bands[i]
            if is_bytes:
                s = b
            else:
                lo, hi = np.percentile(v[i], 2), np.percentile(v[i], 98)
                s = (b - lo) / ((hi - lo) or 1) * 255
            out[..., i] = np.clip(np.nan_to_num(s), 0, 255).astype("uint8")
    out[..., 3] = np.where(ok, 255, 0)
    return out


def source_png(src_path, most: int = 2048) -> tuple[bytes, float]:
    """The image itself (unplaced) as a PNG no larger than `most` on its longer side, and how much it was shrunk."""
    from PIL import Image
    with rasterio.open(src_path) as src:
        k = max(1.0, max(src.width, src.height) / most)
        a = src.read(out_shape=(src.count, max(1, round(src.height / k)), max(1, round(src.width / k))), masked=True).astype("float32")
    buf = io.BytesIO()
    Image.fromarray(to_rgba(np.ma.filled(a, np.nan)), "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue(), k
