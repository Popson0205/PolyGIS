"""Military visibility on an elevation model: line of sight (with its terrain profile), radial line of sight, a viewshed
limited to a sector and a band of ranges, and the highest points (candidate observation posts).

Heights are allowed for the earth's curvature and normal refraction (k = 0.13) when asked. Observers and targets are
typed (any coordinate notation) or taken from point layers."""
import math

import numpy as np
import shapely
from pyproj import Geod
from rasterio.transform import rowcol
from scipy import ndimage

from .raster_tools import PTS, Grid, Out, bad, classes_style, num, rtool, to_crs, to_wgs84
from .tools import _p
from .tools_military import read_lines

GEOD = Geod(ellps="WGS84")
K_REFRACTION = 0.13
EARTH_R = 6_371_000.0
DEM = _p("input", "Elevation model (DEM)", "layer", geometry=["Raster"])
VISIBLE, HIDDEN = "#1a9850", "#d73027"
LOS_STYLE = {"type": "categorized", "field": "visible", "color": VISIBLE, "stroke": VISIBLE, "width": 3, "size": 6, "opacity": 0.95,
             "categories": [{"value": "Visible", "color": VISIBLE, "label": "Visible"}, {"value": "Hidden", "color": HIDDEN, "label": "Hidden"}]}


def _who(prefix: str, label: str, layer_label: str, default="typed"):
    return [
        _p(prefix, f"{label} from", "choice", default=default, choices=[["typed", "coordinates I type"], ["layer", "a point layer"]]),
        _p(f"{prefix}_text", f"{label}, one per line (any notation; a name and a colon may go first)", "text", lines=True, default="",
           when={prefix: "typed"}),
        _p(f"{prefix}_layer", layer_label, "layer", geometry=PTS, optional=True, when={prefix: "layer"}),
    ]


def points(p, inp, prefix: str, what: str, most: int) -> list[tuple[str, float, float]]:
    """(name, longitude, latitude) of the observers or targets, typed or from a layer."""
    if p.get(prefix) == "layer":
        key = f"{prefix}_layer"
        if key not in inp.layers:
            raise bad(f"Choose the {what} layer.", key)
        out = []
        for i, f in enumerate(inp.vector(key), 1):
            c = f["geom"] if f["geom"].geom_type == "Point" else f["geom"].representative_point()
            props = f.get("props") or {}
            out.append((str(props.get("name") or props.get("label") or f.get("id") or i), c.x, c.y))
    else:
        try:
            out = [(n or f"{what.rstrip('s').capitalize()} {i}", x, y) for i, (n, x, y) in enumerate(read_lines(p.get(f"{prefix}_text"), what=what), 1)]
        except Exception as e:                             # the toolbox's Problem: keep its words, on this parameter
            raise bad(getattr(e, "detail", str(e)), f"{prefix}_text") from None
    if not out:
        raise bad(f"Give at least one of the {what}.", f"{prefix}_text")
    if len(out) > most:
        raise bad(f"Up to {most} {what} at a time.", f"{prefix}_text")
    return out


# ---------- the profile between two points ----------
def _sample(z: np.ndarray, g: Grid, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Heights at points (in the DEM's system), bilinear between cell centres; NaN outside or on no-data."""
    inv = ~g.transform
    cols, rows = inv * (xs, ys)
    return ndimage.map_coordinates(z, [np.asarray(rows) - 0.5, np.asarray(cols) - 0.5], order=1, mode="constant", cval=np.nan, prefilter=False)


def profile(g: Grid, z: np.ndarray, a: tuple[float, float], b: tuple[float, float], oh: float, th: float, curvature: bool) -> dict:
    """The terrain from a (longitude, latitude) to b, and what an observer `oh` metres above a sees of it and of a
    target `th` metres above b."""
    total = GEOD.inv(a[0], a[1], b[0], b[1])[2]
    if total <= 0:
        raise bad("The observer and the target are the same point.")
    pa, pb = to_crs([shapely.Point(a), shapely.Point(b)], g.crs)
    dx, dy = g.cell_m()
    step_m = max(1.0, min(float(dx.min()), float(dy)) / 2)
    n = int(min(4000, max(2, math.ceil(total / step_m)))) + 1
    t = np.linspace(0, 1, n)
    xs, ys = pa.x + (pb.x - pa.x) * t, pa.y + (pb.y - pa.y) * t
    zz = _sample(z, g, xs, ys)
    d = t * total
    if not np.isfinite(zz[0]):
        raise bad("The observer is off the elevation model (or on a cell without a height).")
    drop = d ** 2 * (1 - K_REFRACTION) / (2 * EARTH_R) if curvature else np.zeros_like(d)
    eff = zz - drop                                         # the ground as the observer sees it
    z0 = zz[0] + oh
    with np.errstate(invalid="ignore", divide="ignore"):
        ang = np.where(d > 0, (eff - z0) / np.where(d > 0, d, 1), -np.inf)
    ang = np.where(np.isfinite(ang), ang, -np.inf)
    horizon = np.maximum.accumulate(np.concatenate([[-np.inf], ang[:-1]]))
    seen = (ang >= horizon) & np.isfinite(zz)
    seen[0] = True
    target_ok = bool(np.isfinite(zz[-1]))
    target_angle = (eff[-1] + th - z0) / total
    target_seen = bool(target_ok and target_angle >= horizon[-1])
    # where the sight line to the target first passes under the ground
    sight = z0 + (eff[-1] + th - z0) * d / total
    under = np.where((eff > sight + 1e-6) & (d > 0) & (d < total))[0]
    blocked = int(under[0]) if len(under) and not target_seen else None
    return {"t": t, "x": xs, "y": ys, "d": d, "z": zz, "seen": seen, "sight": sight, "eff": eff, "z0": z0,
            "target_seen": target_seen, "blocked": blocked, "total": total, "target_ok": target_ok}


def segments(g: Grid, pr: dict) -> list[tuple[shapely.LineString, bool, float, float]]:
    """The profile cut into runs of visible and hidden ground, as lines in WGS 84 with where each starts and ends."""
    d = pr["d"]
    state = np.where(np.isfinite(pr["z"]), pr["seen"].astype(int), -1)     # -1: no height there (off the model): no line
    seen = state
    out, start = [], 0
    for i in range(1, len(seen) + 1):
        if i == len(seen) or seen[i] != seen[start]:
            j = min(i, len(seen) - 1)                       # share the end point with the next run, so the lines join
            if seen[start] >= 0 and j < len(seen) and seen[j] < 0:
                j -= 1                                      # don't run onto ground without a height
            if j > start and seen[start] >= 0:
                line = to_wgs84([shapely.LineString(list(zip(pr["x"][start:j + 1], pr["y"][start:j + 1])))], g.crs)[0]
                out.append((line, bool(seen[start]), float(d[start]), float(d[j])))
            start = i
    return out


@rtool("line_of_sight", "Line of sight", "Military",
       "Whether each target can be seen from each observer over the terrain, and which stretches of ground between them are "
       "visible (green) or hidden (red). Heights above ground for the observer and target; curvature and refraction allowed for.",
       DEM, *_who("observers", "Observers", "Observer points"), *_who("targets", "Targets", "Target points"),
       _p("height", "Observer height above ground", "number", unit="m", min=0, default=2),
       _p("target_height", "Target height above ground", "number", unit="m", min=0, default=0),
       _p("curvature", "Allow for the earth's curvature and refraction", "bool", default=True))
def line_of_sight(p, inp):
    g = inp.grid("input")
    z = np.ma.filled(g.data, np.nan)
    obs = points(p, inp, "observers", "observers", 50)
    tgs = points(p, inp, "targets", "targets", 200)
    if len(obs) * len(tgs) > 2000:
        raise bad("That is more than 2,000 sight lines: use fewer observers or targets.")
    oh = num(p, "height", 0, 10000, "Observer height", 2)
    th = num(p, "target_height", 0, 10000, "Target height", 0)
    curv = bool(p.get("curvature", True))
    rows, seen_n = [], 0
    for on, ox, oy in obs:
        for tn, tx, ty in tgs:
            if (ox, oy) == (tx, ty):
                continue
            pr = profile(g, z, (ox, oy), (tx, ty), oh, th, curv)
            seen_n += pr["target_seen"]
            for line, vis, d0, d1 in segments(g, pr):
                rows.append((line, {"observer": on, "target": tn, "visible": "Visible" if vis else "Hidden",
                                    "target_visible": pr["target_seen"], "from_m": round(d0, 1), "to_m": round(d1, 1),
                                    "distance_m": round(pr["total"], 1)}))
    if not rows:
        raise bad("No sight lines: the observers and targets are the same points.")
    fields = [{"name": "observer", "type": "string"}, {"name": "target", "type": "string"}, {"name": "visible", "type": "string"},
              {"name": "target_visible", "type": "boolean"}, {"name": "from_m", "type": "number"}, {"name": "to_m", "type": "number"},
              {"name": "distance_m", "type": "number"}]
    pairs = len(obs) * len(tgs)
    return Out(name="Line of sight", kind="vector", geometry_type="LineString", fields=fields, rows=rows, style=LOS_STYLE,
               summary={"sight_lines": pairs, "targets_seen": int(seen_n)})


@rtool("radial_line_of_sight", "Radial line of sight", "Military",
       "Spokes from each observer out to a range, each cut into the ground that can be seen (green) and that is hidden (red): "
       "a quick picture of what an observation post covers, within a sector if wanted.",
       DEM, *_who("observers", "Observers", "Observer points"),
       _p("radius", "Out to", "number", unit="m", min=10, default=3000),
       _p("radials", "Number of spokes", "number", min=4, default=72),
       _p("az_from", "Sector from (degrees)", "number", default=0), _p("az_to", "Sector to (degrees)", "number", default=360),
       _p("height", "Observer height above ground", "number", unit="m", min=0, default=2),
       _p("target_height", "Target height above ground", "number", unit="m", min=0, default=0),
       _p("curvature", "Allow for the earth's curvature and refraction", "bool", default=True))
def radial_line_of_sight(p, inp):
    g = inp.grid("input")
    z = np.ma.filled(g.data, np.nan)
    obs = points(p, inp, "observers", "observers", 20)
    radius = num(p, "radius", 10, 100_000, "Out to", 3000)
    k = int(num(p, "radials", 4, 720, "Number of spokes", 72))
    a0, a1 = num(p, "az_from", -360, 720, "Sector from", 0), num(p, "az_to", -360, 720, "Sector to", 360)
    span = (a1 - a0) % 360 or 360
    oh, th = num(p, "height", 0, 10000, "Observer height", 2), num(p, "target_height", 0, 10000, "Target height", 0)
    curv = bool(p.get("curvature", True))
    rows, seen_m, all_m = [], 0.0, 0.0
    for on, ox, oy in obs:
        for i in range(k if span == 360 else k + 1):
            az = (a0 + span * i / k) % 360
            ex, ey, _ = GEOD.fwd(ox, oy, az, radius)
            pr = profile(g, z, (ox, oy), (ex, ey), oh, th, curv)
            for line, vis, d0, d1 in segments(g, pr):
                rows.append((line, {"observer": on, "bearing": round(az, 2), "visible": "Visible" if vis else "Hidden",
                                    "from_m": round(d0, 1), "to_m": round(d1, 1)}))
                all_m += d1 - d0
                seen_m += (d1 - d0) if vis else 0
    fields = [{"name": "observer", "type": "string"}, {"name": "bearing", "type": "number"}, {"name": "visible", "type": "string"},
              {"name": "from_m", "type": "number"}, {"name": "to_m", "type": "number"}]
    return Out(name="Radial line of sight", kind="vector", geometry_type="LineString", fields=fields, rows=rows,
               style={**LOS_STYLE, "width": 2}, summary={"visible_share": round(seen_m / all_m, 4) if all_m else 0})


def _in_sector(az: np.ndarray, a0: float, a1: float) -> np.ndarray:
    span = (a1 - a0) % 360 or 360
    return ((az - a0) % 360) <= span


@rtool("sector_viewshed", "Viewshed (sector and ranges)", "Military",
       "The ground seen from observers within a sector of bearings and between a nearest and a farthest range (as an "
       "observation post or a weapon covers it), and from how many of them.",
       DEM, *_who("observers", "Observers", "Observer points"),
       _p("inner", "From (nearest range)", "number", unit="m", min=0, default=0),
       _p("radius", "Out to (farthest range)", "number", unit="m", min=10, default=5000),
       _p("az_from", "Sector from (degrees)", "number", default=0), _p("az_to", "Sector to (degrees)", "number", default=360),
       _p("height", "Observer height above ground", "number", unit="m", min=0, default=2),
       _p("target_height", "Target height above ground", "number", unit="m", min=0, default=0),
       _p("curvature", "Allow for the earth's curvature and refraction", "bool", default=True))
def sector_viewshed(p, inp):
    g = inp.grid("input")
    obs = points(p, inp, "observers", "observers", 50)
    oh, th = num(p, "height", 0, 10000, "Observer height", 2), num(p, "target_height", 0, 10000, "Target height", 0)
    inner, radius = num(p, "inner", 0, 200_000, "From", 0), num(p, "radius", 10, 200_000, "Out to", 5000)
    if inner >= radius:
        raise bad("The nearest range must be less than the farthest.", "inner")
    a0, a1 = num(p, "az_from", -360, 720, "Sector from", 0), num(p, "az_to", -360, 720, "Sector to", 360)
    curv = bool(p.get("curvature", True))
    z = np.ma.filled(g.data, np.nan)
    H, W = z.shape
    dxr, dy = g.cell_m()
    count = np.zeros((H, W), np.int32)
    xs, ys = g.centres()
    pts = to_crs([shapely.Point(x, y) for _, x, y in obs], g.crs)
    used = 0
    for (_, lng, lat), pt in zip(obs, pts):
        r0, c0 = rowcol(g.transform, pt.x, pt.y)
        if not (0 <= r0 < H and 0 <= c0 < W) or not np.isfinite(z[r0, c0]):
            continue
        used += 1
        dx = float(dxr[r0, 0])
        R = int(min(1500, math.ceil(radius / min(dx, dy))))
        z0 = z[r0, c0] + oh
        per = [(-R, j) for j in range(-R, R + 1)] + [(R, j) for j in range(-R, R + 1)] + \
              [(i, -R) for i in range(-R + 1, R)] + [(i, R) for i in range(-R + 1, R)]
        per = np.array(per, float)
        vis = np.zeros((H, W), bool)
        steps = np.arange(1, R + 1, dtype=float)
        for chunk in np.array_split(per, max(1, len(per) // 512)):
            nn = np.abs(chunk).max(1)[:, None]
            rr = np.rint(r0 + chunk[:, :1] * steps / nn).astype(int)
            cc = np.rint(c0 + chunk[:, 1:] * steps / nn).astype(int)
            inside = (rr >= 0) & (rr < H) & (cc >= 0) & (cc < W)
            rrc, ccc = rr.clip(0, H - 1), cc.clip(0, W - 1)
            dist = np.hypot((rrc - r0) * dy, (ccc - c0) * dx)
            zz = z[rrc, ccc]
            if curv:
                zz = zz - dist ** 2 * (1 - K_REFRACTION) / (2 * EARTH_R)
            ok = inside & np.isfinite(zz) & (dist <= radius) & (dist > 0)
            ang = np.where(ok, (zz - z0) / np.where(dist > 0, dist, 1), -np.inf)
            horizon = np.maximum.accumulate(np.concatenate([np.full((len(chunk), 1), -np.inf), ang[:, :-1]], 1), axis=1)
            seen = ok & ((zz + th - z0) / np.where(dist > 0, dist, 1) >= horizon)
            vis[rr[seen], cc[seen]] = True
        # only the sector and the band of ranges count
        az = (np.degrees(np.arctan2((np.arange(W)[None, :] - c0) * dx, -(np.arange(H)[:, None] - r0) * dy)) + 360) % 360
        dist_all = np.hypot((np.arange(H)[:, None] - r0) * dy, (np.arange(W)[None, :] - c0) * dx)
        vis &= _in_sector(az, a0, a1) & (dist_all >= inner) & (dist_all <= radius)
        count += vis
    if not used:
        raise bad("None of the observers are on the elevation model.")
    out = np.ma.masked_array(count, mask=np.ma.getmaskarray(g.data))
    vals = sorted({int(v) for v in np.unique(out.compressed())})
    st = classes_style(vals, {0: "Not seen", **{v: f"Seen from {v}" for v in vals if v}}, {0: "#4d4d4d", 1: "#a6d96a"})
    del xs, ys
    return Out(name=f"{inp.layers['input']['name']} sector viewshed", data=out, crs=g.crs, transform=g.transform, dtype="int32",
               categorical=True, style=st, summary={"visible_m2": float((g.cell_area() * (count > 0)).sum()), "observers": used})


@rtool("highest_points", "Highest points", "Military",
       "The highest hilltops of an elevation model (local peaks), kept apart by a distance: candidate observation posts, "
       "relay or radar sites. Within polygons if given.",
       DEM, _p("count", "How many", "number", min=1, default=10),
       _p("spacing", "At least this far apart", "number", unit="m", min=0, default=500),
       _p("within", "Only inside (optional)", "layer", geometry=["Polygon"], optional=True))
def highest_points(p, inp):
    g = inp.grid("input")
    n = int(num(p, "count", 1, 1000, "How many", 10))
    spacing = num(p, "spacing", 0, 1_000_000, "Apart", 500)
    z = np.ma.filled(g.data, np.nan)
    finite = np.isfinite(z)
    if not finite.any():
        raise bad("The elevation model has no heights.")
    zf = np.where(finite, z, -np.inf)
    peak = (zf == ndimage.maximum_filter(zf, size=3, mode="nearest")) & finite
    if "within" in inp.layers:
        from rasterio import features as rfeatures
        polys = to_crs([f["geom"] for f in inp.vector("within")], g.crs)
        if not polys:
            raise bad("The polygons layer has no features.", "within")
        peak &= rfeatures.geometry_mask(polys, out_shape=z.shape, transform=g.transform, invert=True, all_touched=True)
    rr, cc = np.nonzero(peak)
    if not len(rr):
        raise bad("No peaks there.")
    order = np.argsort(-z[rr, cc])
    dx, dy = g.cell_m()
    xs, ys = g.transform * (cc + 0.5, rr + 0.5)
    chosen: list[int] = []
    for i in order:
        if len(chosen) >= n:
            break
        ok = True
        for j in chosen:
            if math.hypot((cc[i] - cc[j]) * float(dx[rr[i], 0]), (rr[i] - rr[j]) * dy) < spacing:
                ok = False
                break
        if ok:
            chosen.append(int(i))
    pts = to_wgs84([shapely.Point(xs[i], ys[i]) for i in chosen], g.crs)
    from . import milcoords as mc
    rows = [(pt, {"rank": k + 1, "elevation": round(float(z[rr[i], cc[i]]), 2), "mgrs": mc.write("usng", pt.x, pt.y), "label": f"{k + 1}: {z[rr[i], cc[i]]:.0f} m"})
            for k, (pt, i) in enumerate(zip(pts, chosen))]
    return Out(name=f"{inp.layers['input']['name']} highest points", kind="vector", geometry_type="Point", rows=rows,
               fields=[{"name": "rank", "type": "integer"}, {"name": "elevation", "type": "number"}, {"name": "mgrs", "type": "string"},
                       {"name": "label", "type": "string"}],
               style={"type": "single", "color": "#7b3294", "stroke": "#ffffff", "width": 1, "size": 9, "opacity": 0.95},
               labels={"field": "label", "size": 11, "font": "bold", "color": "#40164f", "halo": "#ffffff"})
