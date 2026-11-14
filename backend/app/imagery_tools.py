"""Image correction and enhancement: turning raw satellite, aerial and drone images into clean, comparable,
good-looking ones. Registered into the raster toolbox (so they also appear in the Model builder).

Correction: digital numbers to reflectance, atmospheric correction (dark object subtraction), cloud and shadow masking,
topographic (illumination) correction, pan-sharpening and filling gaps.
Enhancement: brightness, contrast and gamma; sharpening (unsharp mask); local contrast (CLAHE); haze removal (dark
channel prior); matching colours to another image; and noise removal (median, Lee speckle filter, total variation)."""
import numpy as np
from rasterio.enums import Resampling
from rasterio.fill import fillnodata
from scipy import ndimage

from .config import settings
from .raster_tools import Grid, Out, aspect_of, bad, classes_style, horn, num, rtool
from .tools import _p

CORR, ENH = "Image correction", "Image enhancement"
IMG = _p("input", "Image", "layer", geometry=["Raster"])


# =====================================================================================================================
# Helpers
# =====================================================================================================================
def _name(inp, key="input") -> str:
    return inp.layers[key]["name"]


def _names(inp, key="input") -> list[str] | None:
    bands = (inp.meta(key) or {}).get("bands") or []
    return [b.get("name") or f"Band {b['band']}" for b in bands] or None


def _valid(a: np.ndarray) -> np.ndarray:
    """Cells with a value in every band."""
    return np.isfinite(a).all(0)


def _pct(b: np.ndarray, *q):
    v = b[np.isfinite(b)]
    return [float(np.percentile(v, x)) for x in q] if v.size else [0.0 for _ in q]


def _style(a: np.ndarray, lo=None, hi=None) -> dict:
    """RGB for three or more bands, grey otherwise, stretched between percentiles unless told."""
    if a.shape[0] >= 3:
        lims = [(_pct(a[i], 2, 98) if lo is None else (lo, hi)) for i in range(3)]
        return {"type": "raster", "mode": "rgb", "bands": [1, 2, 3], "mins": [float(f"{x[0]:.6g}") for x in lims],
                "maxs": [float(f"{x[1]:.6g}") if x[1] > x[0] else x[0] + 1 for x in lims], "opacity": 1}
    l0, h0 = _pct(a[0], 2, 98) if lo is None else (lo, hi)
    return {"type": "raster", "mode": "ramp", "band": 1, "min": float(f"{l0:.6g}"), "max": float(f"{h0:.6g}") if h0 > l0 else l0 + 1,
            "ramp": "Gray", "opacity": 1}


def _out(name, a: np.ndarray, g: Grid, names=None, dtype="float32", style=None, summary=None) -> Out:
    """A multi-band result; NaN cells are no data. Byte images keep 0 for no data and 1–255 for values."""
    a = np.where(np.isfinite(a), a, np.nan)
    nodata = None
    if dtype == "uint8":
        a = np.where(np.isfinite(a), np.clip(np.round(a), 1, 255), np.nan)
        nodata = 0
    o = Out(name=name, data=np.ma.masked_invalid(a), crs=g.crs, transform=g.transform, dtype=dtype,
            style=style or (_style(a, 0, 255) if dtype == "uint8" else _style(a)), names=names, nodata=nodata)
    o.summary = summary or {}
    return o


def _image(inp, key="input") -> tuple[np.ndarray, Grid]:
    g = inp.bands(key)
    return np.ma.filled(g.data, np.nan), g


def _band_no(p, name, label, count, default) -> int:
    b = int(num(p, name, 1, 10_000, label, default))
    if b > count:
        raise bad(f"{label}: the image has {count} band{'s' if count > 1 else ''}, so choose 1 to {count}.", name)
    return b - 1


def _unit(a: np.ndarray) -> np.ndarray:
    """Each band onto 0–1 between its 0.5th and 99.5th percentiles (for methods that work on display values)."""
    out = np.empty_like(a)
    for i, b in enumerate(a):
        lo, hi = _pct(b, 0.5, 99.5)
        out[i] = np.clip((b - lo) / ((hi - lo) or 1), 0, 1)
    return out


def _reflectance_scale(a: np.ndarray) -> float:
    """How much a value is per unit of reflectance: 1 for reflectance already, 10 000 for scaled integers."""
    v = a[np.isfinite(a)]
    return 1.0 if not v.size or np.percentile(v, 99) <= 2 else 10_000.0


def _filled(b: np.ndarray) -> np.ndarray:
    """A band with its gaps filled from the nearest values, so neighbourhood filters do not bleed no-data."""
    m = ~np.isfinite(b)
    if m.any() and (~m).any():
        idx = ndimage.distance_transform_edt(m, return_distances=False, return_indices=True)
        return b[tuple(idx)]
    return np.nan_to_num(b)


# =====================================================================================================================
# Correction
# =====================================================================================================================
PRESETS = [["s2", "Sentinel-2 L2A (value ÷ 10 000)"], ["s2_offset", "Sentinel-2 L2A from 2022 (value − 1 000, ÷ 10 000)"],
           ["landsat", "Landsat Collection 2 Level-2 (× 0.0000275 − 0.2)"], ["custom", "My own gain and offset"]]
GAINS = {"s2": (0.0001, 0.0), "s2_offset": (0.0001, -0.1), "landsat": (0.0000275, -0.2)}


@rtool("to_reflectance", "Digital numbers to reflectance", CORR,
       "Stored values (digital numbers) turned into surface reflectance from 0 to 1, so images from different sensors and dates compare.",
       IMG, _p("preset", "Sensor", "choice", default="s2", choices=PRESETS),
       _p("gain", "Gain", "number", default=1, when={"preset": "custom"}), _p("offset", "Offset", "number", default=0, when={"preset": "custom"}))
def to_reflectance_tool(p, inp):
    a, g = _image(inp)
    pre = p.get("preset", "s2")
    if pre not in (*GAINS, "custom"):
        raise bad("Choose a sensor from the list.", "preset")
    gain, off = GAINS.get(pre) or (num(p, "gain", -1e9, 1e9, "Gain", 1), num(p, "offset", -1e9, 1e9, "Offset", 0))
    out = a * gain + off
    if pre != "custom":
        out = np.clip(out, 0, 1.6)                      # bright snow and glints go just above 1; nothing is below 0
    return _out(f"{_name(inp)} reflectance", out, g, _names(inp), summary={"gain": gain, "offset": off})


@rtool("dark_object_subtraction", "Atmospheric correction (dark object subtraction)", CORR,
       "Haze from the atmosphere removed: each band lowered by the value of its darkest objects (deep water, shadow), which should be near zero.",
       IMG, _p("percentile", "Darkest objects are the lowest (percent of pixels)", "number", min=0, default=0.01),
       _p("dos1", "Leave the darkest objects at 1 % reflectance (DOS1)", "bool", default=True))
def dos_tool(p, inp):
    a, g = _image(inp)
    q = num(p, "percentile", 0, 10, "Percentile", 0.01)
    scale = _reflectance_scale(a)
    keep = 0.01 * scale if p.get("dos1", True) in (True, "true", 1) else 0.0
    out, dark = np.empty_like(a), []
    for i, b in enumerate(a):
        d = _pct(b, q)[0]
        dark.append(round(d, 6))
        out[i] = np.where(np.isfinite(b), np.maximum(b - d + keep, 0), np.nan)
    return _out(f"{_name(inp)} corrected", out, g, _names(inp), summary={"dark_values": dark, "reflectance_scale": scale})


SP = {"method": "spectral"}
SCL_BAD = {"shadow": [3], "cloud": [8, 9, 10], "snow": [11], "saturated": [1]}   # Sentinel-2 scene classification


@rtool("cloud_mask", "Cloud and shadow mask", CORR,
       "Clouds and their shadows found and removed (made no data), from a Sentinel-2 scene classification band, a Landsat QA band, or the colours.",
       IMG, _p("method", "Find clouds from", "choice", default="spectral",
               choices=[["spectral", "The image's colours (brightness and whiteness)"], ["scl", "A Sentinel-2 scene classification (SCL) raster"],
                        ["qa", "A Landsat QA_PIXEL raster"]]),
       _p("mask", "SCL or QA raster", "layer", geometry=["Raster"], optional=True, when={"method": ["scl", "qa"]}),
       _p("blue", "Blue band", "number", min=1, default=3, when=SP), _p("green", "Green band", "number", min=1, default=2, when=SP),
       _p("red", "Red band", "number", min=1, default=1, when=SP), _p("nir", "Near-infrared band", "number", min=1, default=4, when=SP),
       _p("brightness", "Clouds are brighter than (reflectance)", "number", min=0, default=0.2, when=SP),
       _p("shadow_distance", "Shadows lie within this distance of a cloud (m)", "number", min=0, default=2000, when=SP),
       _p("buffer", "Grow clouds and shadows by (cells)", "number", min=0, default=2),
       _p("output", "Result", "choice", default="image", choices=[["image", "The image with clouds removed"], ["mask", "A cloud and shadow map"]]))
def cloud_mask_tool(p, inp):
    a, g = _image(inp)
    ok = _valid(a)
    method = p.get("method", "spectral")
    cloud = np.zeros(a.shape[1:], bool)
    shadow = np.zeros(a.shape[1:], bool)
    if method in ("scl", "qa"):
        if not inp.layers.get("mask"):
            raise bad("Choose the SCL or QA raster that comes with the image.", "mask")
        m = np.ma.filled(inp.aligned("mask", g, 1, resampling=Resampling.nearest), np.nan)
        if method == "scl":
            cloud = np.isin(m, SCL_BAD["cloud"])
            shadow = np.isin(m, SCL_BAD["shadow"])
        else:
            q = np.nan_to_num(m).astype(np.int64)
            cloud = ((q >> 3) & 1).astype(bool) | ((q >> 1) & 1).astype(bool) | ((q >> 2) & 1).astype(bool)   # cloud, dilated cloud, cirrus
            shadow = ((q >> 4) & 1).astype(bool)
    elif method == "spectral":
        n = a.shape[0]
        bl, gr, rd, ni = (_band_no(p, k, lbl, n, d) for k, lbl, d in (("blue", "Blue band", 3), ("green", "Green band", 2), ("red", "Red band", 1),
                                                                      ("nir", "Near-infrared band", 4)))
        s = _reflectance_scale(a)
        vis = a[[bl, gr, rd]] / s
        mean = vis.mean(0)
        white = np.abs(vis - mean).sum(0) / np.where(mean > 0, mean, 1)          # 0 for pure grey/white, larger for colour
        t = num(p, "brightness", 0, 1, "Brightness", 0.2)
        cloud = ok & (vis[0] > t) & (mean > t) & (white < 0.7)
        # shaded ground is dark but still brighter in near-infrared than in green; water is darker in near-infrared
        dark = ok & (a[ni] / s < 0.12) & (mean < 0.08) & (a[ni] >= a[gr])
        dx, dy = g.cell_m()
        reach = num(p, "shadow_distance", 0, 50_000, "Shadow distance", 2000)
        near = ndimage.distance_transform_edt(~cloud, sampling=(float(dy), float(np.mean(dx)))) <= reach if cloud.any() else cloud
        shadow = dark & near & ~cloud
    else:
        raise bad("Choose how to find the clouds.", "method")
    k = int(num(p, "buffer", 0, 50, "Buffer", 2))
    if k:
        sq = np.ones((3, 3), bool)                                                  # grow in all eight directions
        cloud = ndimage.binary_dilation(cloud, sq, iterations=k)
        shadow = ndimage.binary_dilation(shadow, sq, iterations=k) & ~cloud
    total = max(int(ok.sum()), 1)
    summary = {"cloud_percent": round(100 * float((cloud & ok).sum()) / total, 2), "shadow_percent": round(100 * float((shadow & ok).sum()) / total, 2)}
    if p.get("output", "image") == "mask":
        cls = np.where(ok, np.where(cloud, 2, np.where(shadow, 3, 1)), np.nan)
        o = Out(name=f"{_name(inp)} clouds", data=np.ma.masked_invalid(cls[None]), crs=g.crs, transform=g.transform, dtype="uint8", categorical=True,
                style=classes_style([1, 2, 3], {1: "Clear", 2: "Cloud", 3: "Shadow"}, {1: "#7fbf7b", 2: "#f7f7f7", 3: "#4d4d4d"}), nodata=0)
        o.summary = summary
        return o
    out = np.where((cloud | shadow)[None], np.nan, a)
    return _out(f"{_name(inp)} cloud-free", out, g, _names(inp), summary=summary)


@rtool("topographic_correction", "Topographic (illumination) correction", CORR,
       "Slopes facing away from the sun brightened and those facing it darkened, so the same land cover looks the same on every slope.",
       IMG, _p("dem", "Elevation model (DEM)", "layer", geometry=["Raster"]),
       _p("azimuth", "Sun azimuth (degrees from north)", "number", min=0, default=135),
       _p("elevation", "Sun elevation (degrees above the horizon)", "number", min=1, default=55),
       _p("method", "Method", "choice", default="c", choices=[["c", "C-correction (recommended)"], ["cosine", "Cosine"]]))
def topo_tool(p, inp):
    a, g = _image(inp)
    dem = Grid(inp.aligned("dem", g), g.crs, g.transform)
    gx, gy = horn(dem)
    slope = np.arctan(np.hypot(gx, gy))
    asp = np.radians(np.maximum(aspect_of(gx, gy), 0))                          # flat cells (-1) face north; their slope is 0 anyway
    az = np.radians(num(p, "azimuth", 0, 360, "Sun azimuth", 135))
    zen = np.radians(90 - num(p, "elevation", 1, 90, "Sun elevation", 55))
    cos_i = np.cos(zen) * np.cos(slope) + np.sin(zen) * np.sin(slope) * np.cos(az - asp)
    cos_i = np.where(np.isfinite(np.ma.filled(dem.data, np.nan)), cos_i, np.nan)
    out, cs = np.empty_like(a), []
    for i, b in enumerate(a):
        ok = np.isfinite(b) & np.isfinite(cos_i)
        if p.get("method", "c") == "c" and ok.sum() > 10 and np.ptp(cos_i[ok]) > 1e-6:
            slope_b, icpt = np.polyfit(cos_i[ok], b[ok], 1)
            c = icpt / slope_b if abs(slope_b) > 1e-12 else 0.0
            c = c if np.isfinite(c) and c > -0.95 else 0.0
        else:
            c = 0.0
        cs.append(round(float(c), 4))
        out[i] = b * (np.cos(zen) + c) / np.maximum(cos_i + c, 0.05)
    return _out(f"{_name(inp)} illumination-corrected", np.where(np.isfinite(cos_i)[None], out, np.nan), g, _names(inp), summary={"c": cs})


@rtool("pan_sharpen", "Pan-sharpening", CORR,
       "Colour bands sharpened with a finer panchromatic band (e.g. Landsat 30 m colour with its 15 m pan): the result has the pan's detail.",
       _p("input", "Colour (multispectral) image", "layer", geometry=["Raster"]), _p("pan", "Panchromatic raster", "layer", geometry=["Raster"]),
       _p("method", "Method", "choice", default="brovey", choices=[["brovey", "Brovey (ratio)"], ["ihs", "Intensity substitution (keeps colours closer)"]]))
def pan_tool(p, inp):
    pan = inp.grid("pan")
    count = inp.meta("input")["count"]
    if pan.shape[0] * pan.shape[1] * count > settings.max_raster_cells:
        raise bad("The sharpened image would be too large: clip the images to a smaller area first.")
    ms = np.stack([np.ma.filled(inp.aligned("input", pan, b + 1), np.nan) for b in range(count)])
    P = np.ma.filled(pan.data, np.nan)
    inten = ms.mean(0)
    # the pan is scaled to the colour bands' average brightness (keeping all its detail), so the result keeps their levels
    ok = np.isfinite(P) & np.isfinite(inten)
    if ok.sum() and abs(P[ok].mean()) > 1e-12:
        P = P * (inten[ok].mean() / P[ok].mean())
    if p.get("method", "brovey") == "ihs":
        out = ms + (P - inten)[None]
    else:
        out = ms * (P / np.where(np.abs(inten) > 1e-9, inten, np.nan))[None]
    return _out(f"{_name(inp)} pan-sharpened", out, pan, _names(inp))


@rtool("fill_nodata", "Fill gaps", CORR,
       "Holes of no data (removed clouds, scan lines, small gaps) filled from the values around them.",
       IMG, _p("distance", "Look this far for values (cells)", "number", min=1, default=50),
       _p("smoothing", "Smoothing passes", "number", min=0, default=0))
def fill_tool(p, inp):
    a, g = _image(inp)
    d = int(num(p, "distance", 1, 2000, "Distance", 50))
    s = int(num(p, "smoothing", 0, 20, "Smoothing", 0))
    out = np.empty_like(a)
    for i, b in enumerate(a):
        mask = np.isfinite(b).astype("uint8")
        if not mask.any() or mask.all():
            out[i] = b
            continue
        filled = fillnodata(np.nan_to_num(b).astype("float32"), mask=mask, max_search_distance=d, smoothing_iterations=s)
        reached = (mask == 1) | (ndimage.distance_transform_edt(mask == 0) <= d)      # gaps further from any value stay empty
        out[i] = np.where(reached, filled, np.nan)
    before = int((~_valid(a)).sum())
    after = int((~_valid(out)).sum())
    return _out(f"{_name(inp)} gaps filled", out, g, _names(inp), summary={"cells_filled": before - after})


# =====================================================================================================================
# Enhancement
# =====================================================================================================================
@rtool("brightness_contrast", "Brightness, contrast and gamma", ENH,
       "An image made easier to see: stretched, then brightened or darkened, with more or less contrast, and gamma for the mid-tones.",
       IMG, _p("brightness", "Brightness (−100 to 100)", "number", default=0), _p("contrast", "Contrast (−100 to 100)", "number", default=0),
       _p("gamma", "Gamma (above 1 lifts the shadows)", "number", min=0.1, default=1))
def bc_tool(p, inp):
    a, g = _image(inp)
    br = num(p, "brightness", -100, 100, "Brightness", 0) / 100
    ct = num(p, "contrast", -100, 100, "Contrast", 0)
    gm = num(p, "gamma", 0.1, 10, "Gamma", 1)
    u = _unit(a) ** (1 / gm)
    f = (259 * (ct * 2.55 + 255)) / (255 * (259 - ct * 2.55))                 # the usual contrast curve, around mid-grey
    u = np.clip((u - 0.5) * f + 0.5 + br, 0, 1)
    return _out(f"{_name(inp)} enhanced", np.where(np.isfinite(a), u * 254 + 1, np.nan), g, _names(inp), dtype="uint8")


@rtool("unsharp_mask", "Sharpen (unsharp mask)", ENH,
       "Edges and fine detail made crisper on every band, as in photo editors, without amplifying flat areas.",
       IMG, _p("radius", "Radius (cells)", "number", min=0.3, default=1.5), _p("amount", "Amount (percent)", "number", min=0, default=100),
       _p("threshold", "Ignore changes smaller than (percent of the range)", "number", min=0, default=0))
def unsharp_tool(p, inp):
    a, g = _image(inp)
    r = num(p, "radius", 0.3, 50, "Radius", 1.5)
    k = num(p, "amount", 0, 1000, "Amount", 100) / 100
    t = num(p, "threshold", 0, 100, "Threshold", 0) / 100
    out = np.empty_like(a)
    for i, b in enumerate(a):
        f = _filled(b)
        detail = f - ndimage.gaussian_filter(f, r, mode="nearest")
        lo, hi = _pct(b, 0.5, 99.5)
        detail = np.where(np.abs(detail) >= t * (hi - lo), detail, 0)
        out[i] = np.where(np.isfinite(b), b + k * detail, np.nan)
    return _out(f"{_name(inp)} sharpened", out, g, _names(inp))


@rtool("clahe", "Local contrast (CLAHE)", ENH,
       "Contrast raised region by region (adaptive histogram equalization), bringing out detail in both dark and bright parts of an image.",
       IMG, _p("tile", "Region size (cells)", "number", min=8, default=64), _p("clip", "Strength (0.001 to 0.1)", "number", min=0.001, default=0.01))
def clahe_tool(p, inp):
    from skimage.exposure import equalize_adapthist
    a, g = _image(inp)
    tile = int(num(p, "tile", 8, 4096, "Region size", 64))
    clip = num(p, "clip", 0.001, 1, "Strength", 0.01)
    u = _unit(a)
    out = np.stack([equalize_adapthist(np.nan_to_num(b), kernel_size=min(tile, max(b.shape)), clip_limit=clip) for b in u])
    return _out(f"{_name(inp)} local contrast", np.where(np.isfinite(a), out * 254 + 1, np.nan), g, _names(inp), dtype="uint8")


def _box(x: np.ndarray, r: int) -> np.ndarray:
    return ndimage.uniform_filter(x, 2 * r + 1, mode="nearest")


def _guided(I: np.ndarray, p_: np.ndarray, r: int, eps: float) -> np.ndarray:
    """He et al.'s guided filter: p_ smoothed while following the edges of I."""
    mI, mp = _box(I, r), _box(p_, r)
    a = (_box(I * p_, r) - mI * mp) / (_box(I * I, r) - mI * mI + eps)
    b = mp - a * mI
    return _box(a, r) * I + _box(b, r)


@rtool("dehaze", "Haze removal", ENH,
       "Haze, smoke and thin mist removed from a colour image (dark channel prior), restoring contrast and colour.",
       IMG, _p("red", "Red band", "number", min=1, default=1), _p("green", "Green band", "number", min=1, default=2),
       _p("blue", "Blue band", "number", min=1, default=3), _p("strength", "Strength (0 to 1)", "number", min=0, default=0.9),
       _p("patch", "Patch size (cells)", "number", min=3, default=15))
def dehaze_tool(p, inp):
    a, g = _image(inp)
    n = a.shape[0]
    if n < 3:
        raise bad("Haze removal needs a colour image with at least three bands.", "input")
    idx = [_band_no(p, k, lbl, n, d) for k, lbl, d in (("red", "Red band", 1), ("green", "Green band", 2), ("blue", "Blue band", 3))]
    ok = _valid(a[idx])
    I = np.stack([np.where(np.isfinite(b), b, 0) for b in _unit(a[idx])])
    w = num(p, "strength", 0, 1, "Strength", 0.9)
    patch = int(num(p, "patch", 3, 101, "Patch size", 15))
    dark = ndimage.minimum_filter(np.where(ok, I.min(0), 1), patch, mode="nearest")
    top = dark[ok] >= np.quantile(dark[ok], 0.999) if ok.any() else np.zeros(0, bool)
    A = np.array([np.clip(I[c][ok][top].max() if top.any() else 1.0, 0.3, 1.0) for c in range(3)])
    t = 1 - w * ndimage.minimum_filter(np.where(ok, (I / A[:, None, None]).min(0), 1), patch, mode="nearest")
    t = np.clip(_guided(I.mean(0), t, max(patch * 2, 8), 1e-3), 0.1, 1)
    J = np.clip((I - A[:, None, None]) / t[None] + A[:, None, None], 0, 1)
    J = _unit(np.where(ok[None], J, np.nan))
    return _out(f"{_name(inp)} dehazed", np.where(ok[None], J * 254 + 1, np.nan), g, [_names(inp)[i] for i in idx] if _names(inp) else None,
                dtype="uint8", summary={"haze_percent": round(100 * float(1 - np.nanmean(np.where(ok, t, np.nan))), 1), "airlight": [round(float(x), 3) for x in A]})


@rtool("histogram_match", "Match colours to another image", ENH,
       "An image's colours matched to a reference image's (band by band), so neighbouring scenes or dates blend in a mosaic.",
       IMG, _p("reference", "Reference image", "layer", geometry=["Raster"]))
def match_tool(p, inp):
    a, g = _image(inp)
    ref, _ = _image(inp, "reference")
    n = min(a.shape[0], ref.shape[0])
    out = a.copy()
    for i in range(n):
        src, rv = a[i], ref[i][np.isfinite(ref[i])]
        sv = src[np.isfinite(src)]
        if not sv.size or not rv.size:
            continue
        qs = np.linspace(0, 100, 1001)
        out[i] = np.where(np.isfinite(src), np.interp(src, np.percentile(sv, qs), np.percentile(rv, qs)), np.nan)
    return _out(f"{_name(inp)} matched", out, g, _names(inp), summary={"bands_matched": n})


@rtool("denoise", "Noise removal", ENH,
       "Noise smoothed away while keeping edges: a median filter for specks, a Lee filter for radar speckle, or total variation for grain.",
       IMG, _p("method", "Method", "choice", default="median",
               choices=[["median", "Median (salt-and-pepper specks)"], ["lee", "Lee filter (radar speckle)"], ["tv", "Total variation (grainy images)"]]),
       _p("size", "Window size (cells)", "number", min=3, default=3, when={"method": ["median", "lee"]}),
       _p("weight", "Strength", "number", min=0, default=0.1, when={"method": "tv"}))
def denoise_tool(p, inp):
    a, g = _image(inp)
    size = int(num(p, "size", 3, 51, "Window size", 3)) | 1
    m = p.get("method", "median")
    out = np.empty_like(a)
    for i, b in enumerate(a):
        f = _filled(b)
        if m == "median":
            r = ndimage.median_filter(f, size, mode="nearest")
        elif m == "lee":
            mean = ndimage.uniform_filter(f, size, mode="nearest")
            var = np.maximum(ndimage.uniform_filter(f * f, size, mode="nearest") - mean ** 2, 0)
            noise = np.median(var[np.isfinite(b)]) if np.isfinite(b).any() else 0
            k = var / np.maximum(var + noise, 1e-12)
            r = mean + k * (f - mean)
        elif m == "tv":
            from skimage.restoration import denoise_tv_chambolle
            lo, hi = _pct(b, 0.5, 99.5)
            span = (hi - lo) or 1
            r = denoise_tv_chambolle((f - lo) / span, weight=num(p, "weight", 0, 10, "Strength", 0.1)) * span + lo
        else:
            raise bad("Choose a method from the list.", "method")
        out[i] = np.where(np.isfinite(b), r, np.nan)
    return _out(f"{_name(inp)} denoised", out, g, _names(inp))

