"""Scenarios and simulations: what-if models that are saved, run, varied and compared.

Four kinds, each turning a scenario's settings into one raster (a band per time step or level) and a summary of
figures to compare scenarios by:
- suitability: weighted overlay of criteria (rasters scored higher/lower/between, vector layers scored near/far), with
  areas excluded; the best places and how much land falls in each class.
- flood: water on an elevation model, as a set water level spreading from sources, a sea-level rise from the sea, or a
  river stage above the nearest stream (height above nearest drainage); depth per level, flooded area and what it reaches.
- land_change: land cover projected to future years (CA-Markov): how much of each class turns into each other class
  comes from two past maps (Markov chain); where it happens follows the neighbourhood (cellular automaton) and optional
  drivers, with protected areas left alone and growth of a class raised or lowered by policy.
- spread: something spreading from sources over a surface (fire, pollution, a disease front): arrival time from a
  speed and an optional friction raster, slowed or sped up by wind and stopped by barriers; extent at each time.
Layer references in the settings are layer ids; `Inputs` holds those layers under their ids."""
import math

import numpy as np
import shapely
from rasterio import features as rfeatures
from rasterio.enums import Resampling
from scipy import ndimage

from .config import settings
from .raster_tools import (Grid, Inputs, Out, accumulate, bad, burn, classes_style, flow, new_grid, ramp, to_crs)

KINDS = {
    "suitability": "Suitability (where is best?)",
    "flood": "Flood and sea-level rise",
    "land_change": "Land-use change and urban growth",
    "spread": "Spread over time",
}
CLASS_NAMES = ["Very low", "Low", "Moderate", "High", "Very high"]
CLASS_COLOURS = ["#d7191c", "#fdae61", "#ffffbf", "#a6d96a", "#1a9641"]


# =====================================================================================================================
# Shared helpers
# =====================================================================================================================
def _num(d: dict, key: str, lo: float, hi: float, label: str, default=None) -> float:
    v = d.get(key, default)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise bad(f"{label} must be a number.") from None
    if not (lo <= v <= hi) or not math.isfinite(v):
        raise bad(f"{label} must be between {lo:g} and {hi:g}.")
    return v


def _list(d: dict, key: str) -> list:
    v = d.get(key) or []
    return v if isinstance(v, list) else [v]


def _numbers(d: dict, key: str, label: str, lo: float, hi: float, most: int = 12) -> list[float]:
    raw = d.get(key)
    vals = raw if isinstance(raw, list) else [x for x in str(raw or "").replace(";", ",").split(",") if x.strip()]
    try:
        out = sorted({float(x) for x in vals})
    except (TypeError, ValueError):
        raise bad(f"{label}: give numbers separated by commas.") from None
    if not out:
        raise bad(f"{label}: give at least one.")
    if len(out) > most:
        raise bad(f"{label}: at most {most}.")
    if any(not (lo <= x <= hi) for x in out):
        raise bad(f"{label} must be between {lo:g} and {hi:g}.")
    return out


def _name(inp: Inputs, lid) -> str:
    return inp.layers[str(lid)]["name"]


def _is_raster(inp: Inputs, lid) -> bool:
    return inp.layers[str(lid)]["geometry_type"] == "Raster"


def _grid_like(g: Grid) -> Grid:
    return Grid(np.ma.masked_array(np.zeros(g.shape), mask=np.ma.getmaskarray(g.data)), g.crs, g.transform)


def _vector_grid(inp: Inputs, ids: list, cell: float, pad: float) -> Grid:
    feats = [f for i in ids for f in inp.vector(str(i))]
    crs, tr, w, h = new_grid(feats, cell, pad)
    return Grid(np.ma.masked_array(np.zeros((h, w)), mask=np.zeros((h, w), bool)), crs, tr)


def _mask_of(inp: Inputs, lid, g: Grid) -> np.ndarray:
    """Cells covered by a layer's features (or, for a raster, where it has a value other than 0)."""
    if _is_raster(inp, lid):
        a = np.ma.filled(inp.aligned(str(lid), g, resampling=Resampling.nearest), 0)
        return np.nan_to_num(a) != 0
    feats = inp.vector(str(lid))
    return burn(feats, to_crs([f["geom"] for f in feats], g.crs), g.shape, g.transform) > 0


def _distance(inp: Inputs, lid, g: Grid) -> np.ndarray:
    src = _mask_of(inp, lid, g)
    if not src.any():
        raise bad(f"{_name(inp, lid)} has no features inside the study area.")
    dx, dy = g.cell_m()
    return ndimage.distance_transform_edt(~src, sampling=(float(dy), float(np.mean(dx))))


def _ha(cells: int | float, g: Grid) -> float:
    return round(float(cells) * float(np.mean(g.cell_area())) / 10_000, 2)


def _impacts(inp: Inputs, assets: list, g: Grid, wet: np.ndarray) -> dict:
    """How many features of each asset layer the wet cells reach."""
    out = {}
    if not wet.any():
        return {_name(inp, a): 0 for a in assets}
    polys = [shapely.geometry.shape(s) for s, v in rfeatures.shapes(wet.astype("uint8"), mask=wet, transform=g.transform) if v]
    tree = shapely.STRtree(polys)
    for a in assets:
        feats = inp.vector(str(a))
        geoms = to_crs([f["geom"] for f in feats], g.crs)
        hit = set()
        if geoms:
            pairs = tree.query(geoms, predicate="intersects")
            hit = set(pairs[0].tolist())
        out[_name(inp, a)] = len(hit)
    return out


def _people(inp: Inputs, pop, g: Grid, wet: np.ndarray) -> float | None:
    if not pop:
        return None
    a = np.ma.filled(inp.aligned(str(pop), g), 0)
    return round(float(np.nansum(np.where(wet, a, 0))), 1)


def _bands_out(name, bands: list[np.ndarray], labels: list[str], g: Grid, style: dict, dtype="float32", categorical=False, summary=None) -> Out:
    a = np.stack(bands).astype("float64")
    o = Out(name=name, data=np.ma.masked_invalid(a), crs=g.crs, transform=g.transform, dtype=dtype, style=style, names=labels,
            categorical=categorical, nodata=0 if dtype == "uint8" else None)
    o.summary = summary or {}
    return o


# =====================================================================================================================
# Suitability
# =====================================================================================================================
RULES = {"higher": "Higher is better", "lower": "Lower is better", "between": "Best between two values", "near": "Closer is better",
         "far": "Further is better"}


def suitability(p: dict, inp: Inputs) -> Out:
    crit = _list(p, "criteria")
    if not crit:
        raise bad("Add at least one criterion.")
    if len(crit) > 20:
        raise bad("Use at most 20 criteria.")
    rasters = [c for c in crit if _is_raster(inp, c.get("layer"))]
    cell = _num(p, "cell", 0.5, 100_000, "Cell size", 30)
    if rasters:
        g = _grid_like(inp.grid(str(rasters[0]["layer"])))
    else:
        g = _vector_grid(inp, [p["area"]] if p.get("area") else [c["layer"] for c in crit], cell, 0 if p.get("area") else 1000)
    valid = ~np.ma.getmaskarray(g.data)
    total_w, score, used = 0.0, np.zeros(g.shape), []
    for i, c in enumerate(crit):
        lid = c.get("layer")
        if not lid:
            raise bad(f"Criterion {i + 1}: choose its layer.")
        w = _num(c, "weight", 0, 1000, f"Criterion {i + 1}: weight", 1)
        rule = c.get("rule") or ("higher" if _is_raster(inp, lid) else "near")
        if rule not in RULES:
            raise bad(f"Criterion {i + 1}: choose how it is scored.")
        if _is_raster(inp, lid):
            if rule in ("near", "far"):
                raise bad(f"{_name(inp, lid)} is a raster: score it higher, lower or between.")
            v = np.ma.filled(inp.aligned(str(lid), g, int(c.get("band") or 1)), np.nan)
            valid &= np.isfinite(v)
            if rule == "between":
                lo, hi = _num(c, "min", -1e12, 1e12, f"{_name(inp, lid)}: lowest", 0), _num(c, "max", -1e12, 1e12, f"{_name(inp, lid)}: highest", 1)
                tol = max(abs(hi - lo) * 0.5, 1e-9)
                s = np.where(v < lo, 1 - (lo - v) / tol, np.where(v > hi, 1 - (v - hi) / tol, 1.0))
            else:
                fin = v[np.isfinite(v)]
                lo, hi = (np.percentile(fin, 2), np.percentile(fin, 98)) if fin.size else (0, 1)
                s = (v - lo) / ((hi - lo) or 1)
                s = 1 - s if rule == "lower" else s
        else:
            if rule not in ("near", "far"):
                raise bad(f"{_name(inp, lid)} is a vector layer: score it by distance, closer or further.")
            d = _distance(inp, lid, g)
            reach = _num(c, "distance", 1, 1e7, f"{_name(inp, lid)}: distance that counts", 5000)
            s = d / reach
            s = 1 - s if rule == "near" else s
        s = np.clip(np.nan_to_num(s), 0, 1)
        score += w * s
        total_w += w
        used.append({"layer": _name(inp, lid), "rule": RULES[rule], "weight": w})
    if total_w <= 0:
        raise bad("At least one criterion needs a weight above 0.")
    suit = score / total_w * 100
    excluded = np.zeros(g.shape, bool)
    for e in _list(p, "exclude"):
        excluded |= _mask_of(inp, e, g)
    if p.get("area"):
        valid &= _mask_of(inp, p["area"], g)
    keep = valid & ~excluded
    suit = np.where(keep, suit, np.nan)
    v = suit[keep]
    if not v.size:
        raise bad("Nothing is left to score: the exclusions cover the whole study area.")
    cls = np.clip((suit // 20).astype("float64"), 0, 4)
    top = _num(p, "top_percent", 0.1, 100, "Best share", 10)
    cut = float(np.percentile(v, 100 - top))
    best = keep & (suit >= cut)
    lab, n = ndimage.label(best, np.ones((3, 3)))
    sizes = ndimage.sum(np.ones(g.shape), lab, range(1, n + 1)) if n else np.array([])
    summary = {
        "criteria": [{**u, "share": round(u["weight"] / total_w * 100, 1)} for u in used],
        "mean": round(float(v.mean()), 1), "max": round(float(v.max()), 1),
        "classes": [{"label": CLASS_NAMES[k], "area_ha": _ha(int(((cls == k) & keep).sum()), g)} for k in range(5)],
        "excluded_ha": _ha(int((valid & excluded).sum()), g), "scored_ha": _ha(int(keep.sum()), g),
        "best_percent": top, "best_threshold": round(cut, 1), "best_ha": _ha(int(best.sum()), g), "best_sites": int(n),
        "largest_site_ha": _ha(int(sizes.max()) if len(sizes) else 0, g),
    }
    summary["steps"] = [{"label": "Suitability", "mean": summary["mean"], "best_ha": summary["best_ha"]}]
    style = {"type": "raster", "mode": "ramp", "band": 1, "min": 0, "max": 100, "ramp": "Spectral", "invert": True, "opacity": 0.85}   # red poor, blue best
    return _bands_out("Suitability", [suit], ["Suitability (0–100)"], g, style, summary=summary)


# =====================================================================================================================
# Flood
# =====================================================================================================================
def _hand(dem: Grid, km2: float) -> np.ndarray:
    """Height above nearest drainage: each cell's height above the stream cell its water first reaches."""
    fl = flow(dem)
    acc = accumulate(fl, fl.grid.cell_area() * np.ones(fl.valid.shape) / 1e6)
    stream = ((acc >= km2) & fl.valid).ravel()
    if not stream.any():
        raise bad(f"No stream drains {km2:g} km² on this elevation model: use a smaller stream threshold.")
    F = fl.filled.ravel()
    base = F.copy()
    recv = fl.recv
    for i in reversed(fl.order.tolist()):           # downstream cells first, so each cell's receiver is settled
        if not stream[i] and recv[i] >= 0:
            base[i] = base[recv[i]]
    h = np.ma.filled(dem.data, np.nan).ravel() - base
    h = np.where(fl.valid.ravel(), np.maximum(h, 0), np.nan)
    return h.reshape(fl.valid.shape)


def flood(p: dict, inp: Inputs) -> Out:
    if not p.get("dem"):
        raise bad("Choose the elevation model.")
    dem = inp.grid(str(p["dem"]))
    z = np.ma.filled(dem.data, np.nan)
    valid = np.isfinite(z)
    method = p.get("method", "level")
    levels = _numbers(p, "levels", "Water levels" if method == "level" else "Heights", -500, 9000)
    seed = np.zeros(dem.shape, bool)
    if p.get("sources"):
        seed = _mask_of(inp, p["sources"], dem) & valid
        if not seed.any():
            raise bad(f"{_name(inp, p['sources'])} has nothing on the elevation model.")
    hand = None
    if method == "river":
        hand = _hand(dem, _num(p, "stream_km2", 0.001, 1e6, "Stream threshold", 1))
    elif method == "sea_rise":
        sea = valid & (z <= _num(p, "sea_level", -100, 100, "Today's sea level", 0))
        edge = np.zeros(dem.shape, bool)
        edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
        sea |= edge & ~valid                                       # open water beyond the model's edge counts as sea
        if not p.get("sources"):
            seed = sea
    elif method != "level":
        raise bad("Choose how the water rises.")
    surge = _num(p, "surge", 0, 50, "Storm surge", 0) if method == "sea_rise" else 0
    assets, pop = _list(p, "assets"), p.get("population")
    bands, labels, steps = [], [], []
    for lv in levels:
        if method == "river":
            wet = valid & (hand <= lv)
            depth = np.where(wet, lv - hand, np.nan)
        else:
            top = lv + surge
            low = (z < top) & valid
            if method == "sea_rise":
                low |= seed
            if seed.any():
                lab, _ = ndimage.label(low | seed, np.ones((3, 3)))
                keep = np.unique(lab[seed])
                wet = np.isin(lab, keep[keep > 0]) & valid
            else:
                wet = low                                              # no sources: every cell below the level
            depth = np.where(wet, top - z, np.nan)
        depth = np.where(depth > 0, depth, np.nan)
        wet = np.isfinite(depth)
        unit = "m above the stream" if method == "river" else ("m rise" if method == "sea_rise" else "m")
        label = f"{lv:g} {unit}"
        labels.append(label)
        bands.append(depth)
        steps.append({"label": label, "area_ha": _ha(int(wet.sum()), dem), "max_depth": round(float(np.nanmax(depth)), 2) if wet.any() else 0,
                      "mean_depth": round(float(np.nanmean(depth)), 2) if wet.any() else 0, "assets": _impacts(inp, assets, dem, wet),
                      **({"people": _people(inp, pop, dem, wet)} if pop else {})})
    deepest = float(max((s["max_depth"] for s in steps), default=1)) or 1
    style = {"type": "raster", "mode": "ramp", "band": len(bands), "min": 0, "max": round(deepest, 2), "ramp": "Blues", "opacity": 0.85}
    title = {"level": "Flood", "sea_rise": "Sea-level rise", "river": "River flood"}[method]
    return _bands_out(title, bands, labels, dem, style, summary={"method": method, "steps": steps, "surge": surge})


# =====================================================================================================================
# Land-use change (CA-Markov)
# =====================================================================================================================
def _matrix_power(P: np.ndarray, t: float) -> np.ndarray:
    """A transition matrix over t periods (t may be fractional): rows stay probabilities."""
    from scipy.linalg import fractional_matrix_power
    if abs(t - round(t)) < 1e-9:
        M = np.linalg.matrix_power(P, int(round(t)))
    else:
        M = np.real(fractional_matrix_power(P, t))
    M = np.clip(np.nan_to_num(M), 0, None)
    s = M.sum(1, keepdims=True)
    return np.where(s > 0, M / np.where(s > 0, s, 1), np.eye(len(P)))


def land_change(p: dict, inp: Inputs) -> Out:
    for k in ("before", "after"):
        if not p.get(k):
            raise bad("Choose the earlier and the later land-cover maps.")
    y0 = _num(p, "before_year", 1800, 2200, "Year of the earlier map")
    y1 = _num(p, "after_year", 1800, 2200, "Year of the later map")
    if y1 <= y0:
        raise bad("The later map's year must come after the earlier map's.")
    years = _numbers(p, "years", "Years to project", y1 + 1e-9, 2300, most=8)
    g = inp.grid(str(p["after"]))
    after = np.ma.filled(g.data, np.nan)
    before = np.ma.filled(inp.aligned(str(p["before"]), g, resampling=Resampling.nearest), np.nan)
    valid = np.isfinite(after) & np.isfinite(before)
    if valid.sum() < 10:
        raise bad("The two maps hardly overlap: they must cover the same place.")
    classes = sorted({int(x) for x in np.unique(after[valid])} | {int(x) for x in np.unique(before[valid])})
    if len(classes) > 40:
        raise bad(f"The maps have {len(classes)} different values: land-cover maps need classes (whole numbers), at most 40.")
    k = len(classes)
    idx = {c: i for i, c in enumerate(classes)}
    lut = np.full(max(classes) - min(classes) + 1, -1)
    for c, i in idx.items():
        lut[c - min(classes)] = i
    b_i = np.where(valid, lut[np.nan_to_num(before, nan=classes[0]).astype(int) - min(classes)], -1)
    a_i = np.where(valid, lut[np.nan_to_num(after, nan=classes[0]).astype(int) - min(classes)], -1)
    C = np.zeros((k, k))
    np.add.at(C, (b_i[valid], a_i[valid]), 1)
    P = np.where(C.sum(1, keepdims=True) > 0, C / np.maximum(C.sum(1, keepdims=True), 1), np.eye(k))
    period = y1 - y0
    # policy: growth of a class raised or lowered (gains into it scaled; what is left over stays put)
    demand = p.get("demand") or {}
    for c, m in (demand.items() if isinstance(demand, dict) else []):
        try:
            c, m = int(c), float(m)
        except (TypeError, ValueError):
            continue
        if c in idx and 0 <= m <= 10:
            j = idx[c]
            for i in range(k):
                if i != j:
                    P[i, j] *= m
            for i in range(k):
                over = P[i].sum() - 1
                if over > 0:
                    P[i, i] = max(P[i, i] - over, 0)
                P[i] /= P[i].sum()
    blocked = set()
    for pair in _list(p, "no_change"):           # transitions not allowed, e.g. "forest→built" as [from, to]
        try:
            f, t = int(pair[0]), int(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if f in idx and t in idx:
            blocked.add((idx[f], idx[t]))
    for f, t in blocked:
        P[f, f] += P[f, t]
        P[f, t] = 0
    protect = np.zeros(g.shape, bool)
    for e in _list(p, "protect"):
        protect |= _mask_of(inp, e, g)
    # drivers: a 0–1 score per cell that makes change more likely (near roads, flat land…)
    drive = np.ones(g.shape)
    drivers = _list(p, "drivers")
    if drivers:
        tw, acc = 0.0, np.zeros(g.shape)
        for i, c in enumerate(drivers):
            lid = c.get("layer")
            if not lid:
                continue
            w = _num(c, "weight", 0, 1000, f"Driver {i + 1}: weight", 1)
            rule = c.get("rule") or ("higher" if _is_raster(inp, lid) else "near")
            if _is_raster(inp, lid):
                v = np.ma.filled(inp.aligned(str(lid), g), np.nan)
                fin = v[np.isfinite(v)]
                lo, hi = (np.percentile(fin, 2), np.percentile(fin, 98)) if fin.size else (0, 1)
                s = np.clip((v - lo) / ((hi - lo) or 1), 0, 1)
                s = 1 - s if rule == "lower" else s
            else:
                d = _distance(inp, lid, g)
                s = np.clip(d / _num(c, "distance", 1, 1e7, "Driver distance", 5000), 0, 1)
                s = 1 - s if rule == "near" else s
            acc += w * np.nan_to_num(s)
            tw += w
        if tw > 0:
            drive = 0.25 + 0.75 * acc / tw                         # drivers shape, never fully forbid
    size = int(_num(p, "neighbourhood", 3, 25, "Neighbourhood", 5)) | 1
    rng = np.random.default_rng(int(_num(p, "seed", 0, 1e9, "Seed", 1)))
    jitter = rng.random(g.shape) * 1e-3
    cur = a_i.copy()
    bands, labels, steps = [], [], []
    yr_prev = y1
    area_of = lambda m: {str(classes[i]): _ha(int((m == i).sum()), g) for i in range(k)}  # noqa: E731
    base_areas = area_of(cur)
    for yr in years:
        Pt = _matrix_power(P, (yr - yr_prev) / period)
        counts = np.array([(cur == i).sum() for i in range(k)])
        changed = np.zeros(g.shape, bool)
        nxt = cur.copy()
        moves = sorted(((i, j, int(round(counts[i] * Pt[i, j]))) for i in range(k) for j in range(k) if i != j and Pt[i, j] > 0),
                       key=lambda x: -x[2])
        near = {j: ndimage.uniform_filter((cur == j).astype(float), size, mode="nearest") for j in {m[1] for m in moves}}
        for i, j, q in moves:
            if q <= 0:
                continue
            cand = (cur == i) & ~changed & ~protect & valid
            n = int(cand.sum())
            if not n:
                continue
            score = np.where(cand, near[j] * drive + jitter, -1)
            q = min(q, n)
            pick = np.argpartition(score.ravel(), -q)[-q:]
            flat = nxt.ravel()
            flat[pick] = j
            changed.ravel()[pick] = True
        cur = nxt
        yr_prev = yr
        band = np.where(valid, np.array(classes, dtype=float)[np.maximum(cur, 0)], np.nan)
        bands.append(band)
        labels.append(f"{yr:g}")
        steps.append({"label": f"{yr:g}", "areas_ha": area_of(cur), "changed_ha": _ha(int(changed.sum()), g)})
    annual = _matrix_power(P, 1 / period)
    style = (inp.layers[str(p["after"])].get("style") or {})
    if style.get("mode") != "classes":
        style = classes_style(classes)
    style = {**style, "band": len(bands)}
    summary = {"classes": [str(c) for c in classes], "labels": {str(c.get("value")): c.get("label") for c in style.get("categories", [])},
               "start": {"label": f"{y1:g}", "areas_ha": base_areas}, "steps": steps,
               "annual_rates": [[round(float(x), 5) for x in row] for row in annual], "period_years": period}
    dt = "uint8" if min(classes) >= 1 and max(classes) <= 255 else ("int16" if -32_000 < min(classes) and max(classes) < 32_000 else "int32")
    return _bands_out("Land cover projection", bands, labels, g, style, dtype=dt, categorical=True, summary=summary)


# =====================================================================================================================
# Spread over time
# =====================================================================================================================
OFF8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def spread(p: dict, inp: Inputs) -> Out:
    if not p.get("sources"):
        raise bad("Choose where it starts.")
    hours = _numbers(p, "hours", "Times (hours)", 0.001, 100_000)
    speed = _num(p, "speed", 1e-6, 1e6, "Speed (metres per hour)", 500)
    cell = _num(p, "cell", 0.5, 100_000, "Cell size", 30)
    if p.get("friction"):
        g = _grid_like(inp.grid(str(p["friction"])))
        fr = np.ma.filled(inp.aligned(str(p["friction"]), g), np.nan)
        fr = np.where(np.isfinite(fr) & (fr > 0), fr, np.nan)       # 1 = normal; 2 = twice as slow; no data or 0 = cannot pass
    else:
        reach = speed * max(hours) * 1.15
        g = _vector_grid(inp, [p["area"]] if p.get("area") else [p["sources"]], cell, 0 if p.get("area") else reach)
        fr = np.ones(g.shape)
    H, W = g.shape
    if H * W > settings.max_hydro_cells:
        raise bad(f"The area has {H * W:,} cells; spread works on up to {settings.max_hydro_cells:,}. Use larger cells or a smaller area.")
    for b in _list(p, "barriers"):
        fr = np.where(_mask_of(inp, b, g), np.nan, fr)
    if p.get("area"):
        fr = np.where(_mask_of(inp, p["area"], g), fr, np.nan)
    src = _mask_of(inp, p["sources"], g) & np.isfinite(fr)
    if not src.any():
        raise bad(f"{_name(inp, p['sources'])} has nothing inside the area it can spread over.")
    dxr, dy = g.cell_m()
    dx = float(np.mean(dxr))
    strength = _num(p, "wind_strength", 0, 0.95, "Wind strength", 0)
    towards = math.radians((_num(p, "wind_from", 0, 360, "Wind from (degrees)", 0) + 180) % 360)
    # Dijkstra over the 8-connected grid, each step's time from its length, the two cells' friction, and the wind
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra
    ok = np.isfinite(fr)
    ids = np.arange(H * W).reshape(H, W)
    rows, cols, wts = [], [], []
    for di, dj in OFF8:
        a = ok[max(0, -di):H - max(0, di), max(0, -dj):W - max(0, dj)]
        b = ok[max(0, di):H - max(0, -di), max(0, dj):W - max(0, -dj)]
        both = a & b
        ia = ids[max(0, -di):H - max(0, di), max(0, -dj):W - max(0, dj)][both]
        ib = ids[max(0, di):H - max(0, -di), max(0, dj):W - max(0, -dj)][both]
        fa = fr[max(0, -di):H - max(0, di), max(0, -dj):W - max(0, dj)][both]
        fb = fr[max(0, di):H - max(0, -di), max(0, dj):W - max(0, -dj)][both]
        length = math.hypot(di * dy, dj * dx)
        bearing = math.atan2(dj * dx, -di * dy)                      # 0 = north, clockwise
        boost = max(0.05, 1 + strength * math.cos(bearing - towards))
        rows.append(ia)
        cols.append(ib)
        wts.append(length * (fa + fb) / 2 / speed / boost)
    graph = coo_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))), shape=(H * W, H * W)).tocsr()
    t = dijkstra(graph, directed=True, indices=np.flatnonzero(src.ravel()), min_only=True).reshape(H, W)
    t = np.where(np.isfinite(t), t, np.nan)
    assets = _list(p, "assets")
    bands, labels, steps = [], [], []
    for hr in hours:
        reached = np.isfinite(t) & (t <= hr)
        bands.append(np.where(reached, t, np.nan))
        lab = f"{hr:g} h"
        labels.append(lab)
        steps.append({"label": lab, "area_ha": _ha(int(reached.sum()), g), "assets": _impacts(inp, assets, g, reached)})
    style = {"type": "raster", "mode": "ramp", "band": len(bands), "min": 0, "max": max(hours), "ramp": "Magma", "opacity": 0.85}
    return _bands_out("Spread", bands, labels, g, style,
                      summary={"steps": steps, "speed_m_per_h": speed, "wind_from": p.get("wind_from"), "wind_strength": strength})


RUN = {"suitability": suitability, "flood": flood, "land_change": land_change, "spread": spread}


def layer_refs(kind: str, p: dict) -> list[str]:
    """Every layer id a scenario's settings point to (so they can be checked and loaded)."""
    refs = []
    one = {"suitability": ["area"], "flood": ["dem", "sources", "population"], "land_change": ["before", "after"],
           "spread": ["sources", "friction", "area"]}[kind]
    many = {"suitability": ["exclude"], "flood": ["assets"], "land_change": ["protect"], "spread": ["barriers", "assets"]}[kind]
    for k in one:
        if p.get(k):
            refs.append(str(p[k]))
    for k in many:
        refs += [str(x) for x in _list(p, k) if x]
    for k in ("criteria", "drivers"):
        refs += [str(c["layer"]) for c in _list(p, k) if isinstance(c, dict) and c.get("layer")]
    return list(dict.fromkeys(refs))
