"""Military tools: coordinates and grids. They register in the toolbox like the other tools (group "Military")."""
import math

import shapely
from shapely.geometry import box

from . import milcoords as mc
from .errors import Problem
from .tools import _p
from .tools_more import tool

NOTATION_CHOICES = [["auto", "Work it out"], ["dd", "Decimal degrees"], ["ddm", "Degrees decimal minutes"], ["dms", "Degrees minutes seconds"],
                    ["utm", "UTM (zone, easting, northing)"], ["mgrs", "MGRS"], ["usng", "USNG"], ["gars", "GARS"], ["georef", "GEOREF"]]
ORDER = _p("order", "Plain numbers are", "choice", default="latlon", choices=[["latlon", "latitude, longitude"], ["lonlat", "longitude, latitude"]])
NOTE_FIELDS = [{"name": n, "type": "string"} for n in mc.NOTATIONS]
AREA = _p("area", "Over", "extent")
MAX_CELLS = 20_000
# a grid is drawn as outlines with its references on, like a map's grid
GRID_STYLE = {"type": "single", "color": "#1d3557", "stroke": "#1d3557", "width": 1.2, "size": 6, "opacity": 0}
GRID_LABELS = {"field": "label", "size": 11, "color": "#1d3557", "halo": "#ffffff", "halo_width": 1.6, "font": "bold"}


def _notes(lng: float, lat: float) -> dict:
    return mc.all_notations(lng, lat)


def _area(params: dict) -> tuple[float, float, float, float]:
    """The extent the grid covers: [west, south, east, north] in degrees (the map view, or a layer's)."""
    a = params.get("area")
    try:
        w, s, e, n = (float(x) for x in a)
    except (TypeError, ValueError):
        raise Problem(422, "Choose what the grid covers: the map view or a layer.", code="validation", fields={"params": ["area"]}) from None
    w, e = max(w, -180.0), min(e, 180.0)
    s, n = max(s, -80.0), min(n, 84.0)
    if not (w < e and s < n):
        raise Problem(422, "That area is outside the part of the world UTM and MGRS cover (80° S to 84° N).", code="validation")
    return w, s, e, n


@tool("convert_notation", "Convert coordinate notation", "Military",
      "Each feature's position (or the coordinates written in one of its fields) in decimal degrees, degrees and minutes, "
      "degrees minutes seconds, UTM, MGRS, USNG, GARS and GEOREF, as new fields, on a point at that position.",
      _p("input", "Input layer", "layer"),
      _p("source", "Take the position from", "choice", default="location", choices=[["location", "where each feature is"], ["field", "a field holding coordinates"]]),
      _p("field", "Coordinates field", "field", of="input", when={"source": "field"}),
      _p("notation", "Written as", "choice", default="auto", choices=NOTATION_CHOICES, when={"source": "field"}),
      {**ORDER, "when": {"source": "field"}})
def convert_notation(params, layers, args, a):
    from .tools import Built
    by_field = params.get("source") == "field"
    field = params.get("field")
    if by_field and not field:
        raise Problem(422, "Choose the field holding the coordinates.", code="validation", fields={"params": ["field"]})
    notation, order = params.get("notation") or "auto", params.get("order") or "latlon"
    taken = {f["name"] for f in a["fields"]}
    out_fields = [f for f in a["fields"]] + [f for f in NOTE_FIELDS if f["name"] not in taken] + \
        ([{"name": "coord_problem", "type": "string"}] if by_field else [])

    async def compute(conn):
        cur = await conn.execute("SELECT ST_AsBinary(ST_PointOnSurface(geom)) AS g, props FROM features WHERE layer_id = layer_data(%s) "
                                 "AND (%s::bigint[] IS NULL OR id = ANY(%s::bigint[])) ORDER BY id", (args["input"], args["input_ids"], args["input_ids"]))
        made = []
        for r in await cur.fetchall():
            props = dict(r["props"] or {})
            pt = shapely.from_wkb(bytes(r["g"])) if r["g"] else None
            if by_field:
                try:
                    lng, lat, _ = mc.parse(str(props.get(field) or ""), notation, order)
                    pt = shapely.Point(lng, lat)
                    props["coord_problem"] = None
                except mc.CoordError as e:
                    props["coord_problem"] = str(e)                 # kept where it was, with the reason
            if pt is None or pt.is_empty:
                continue
            props.update(_notes(pt.x, pt.y))
            made.append((pt, props))
        return made

    return Built(None, args, "Point", out_fields, f"{a['name']} coordinates", compute)


@tool("points_from_coordinates", "Coordinates to points", "Military",
      "Points from coordinates typed or pasted one per line, in any notation (MGRS, USNG, UTM, degrees, GARS, GEOREF), "
      "each with its position in every notation. A name can go before the coordinates, followed by a colon.",
      _p("coordinates", "Coordinates, one per line", "text", lines=True, default=""),
      _p("notation", "Written as", "choice", default="auto", choices=NOTATION_CHOICES), ORDER)
def points_from_coordinates(params, layers, args, a):
    from .tools import Built
    lines = [x.strip() for x in str(params.get("coordinates") or "").splitlines() if x.strip()]
    if not lines:
        raise Problem(422, "Type or paste at least one coordinate.", code="validation", fields={"params": ["coordinates"]})
    if len(lines) > 5000:
        raise Problem(422, "Up to 5,000 coordinates at a time.", code="validation", fields={"params": ["coordinates"]})
    made, bad = [], []
    how, order = params.get("notation") or "auto", params.get("order") or "latlon"
    for i, line in enumerate(lines, 1):
        name, text = None, line
        try:
            lng, lat, used = mc.parse(text, how, order)
        except mc.CoordError as e:
            head, _, tail = line.partition(":")            # "OP North: 32P LR 31452 03665"
            try:
                if not tail.strip():
                    raise e
                lng, lat, used = mc.parse(tail, how, order)
                name, text = head.strip()[:120], tail.strip()
            except mc.CoordError:
                bad.append(f"line {i} ({line[:40]}): {e}")
                continue
        made.append((shapely.Point(lng, lat), {"name": name, "input": text, "notation": mc.NAMES[used], **_notes(lng, lat)}))
    if not made:
        raise Problem(422, "None of the lines could be read. " + "; ".join(bad[:3]), code="validation", fields={"params": ["coordinates"]})
    if bad:
        raise Problem(422, f"{len(bad)} of {len(lines)} lines could not be read: " + "; ".join(bad[:3]) + ("…" if len(bad) > 3 else ""),
                      code="validation", fields={"params": ["coordinates"]})

    async def compute(conn):
        return made

    fields = [{"name": "name", "type": "string"}, {"name": "input", "type": "string"}, {"name": "notation", "type": "string"}] + NOTE_FIELDS
    return Built(None, args, "Point", fields, "Coordinates", compute,
                 labels={"field": "name" if any(p.get("name") for _, p in made) else "usng", "size": 11, "font": "bold"})


def _utm_ring(zone: int, south: bool, e0: float, n0: float, e1: float, n1: float, steps: int = 8):
    """A UTM square as a WGS 84 polygon, its edges densified (they curve on the map)."""
    inv = mc._utm(zone, south)[1]
    xs, ys = [], []
    for k in range(steps):
        xs.append(e0 + (e1 - e0) * k / steps); ys.append(n0)
    for k in range(steps):
        xs.append(e1); ys.append(n0 + (n1 - n0) * k / steps)
    for k in range(steps):
        xs.append(e1 - (e1 - e0) * k / steps); ys.append(n1)
    for k in range(steps):
        xs.append(e0); ys.append(n1 - (n1 - n0) * k / steps)
    lng, lat = inv.transform(xs, ys)
    return shapely.Polygon(list(zip(lng, lat)))


LEVELS = {"gzd": None, "100km": 100_000, "10km": 10_000, "1km": 1_000, "100m": 100}


@tool("mgrs_grid", "MGRS grid", "Military",
      "The Military Grid Reference System as polygons over an area: grid zones, 100 km squares, or finer squares "
      "(10 km, 1 km, 100 m), each with its MGRS reference to label it by.",
      AREA, _p("level", "Squares", "choice", default="100km",
               choices=[["gzd", "Grid zones (6° × 8°)"], ["100km", "100 km squares"], ["10km", "10 km"], ["1km", "1 km"], ["100m", "100 m"]]))
def mgrs_grid(params, layers, args, a):
    from .tools import Built
    w, s, e, n = _area(params)
    level = params.get("level") or "100km"
    if level not in LEVELS:
        raise Problem(422, "Choose the size of the squares.", code="validation", fields={"params": ["level"]})
    size = LEVELS[level]
    area = box(w, s, e, n)
    made = []
    for zone, bi, b, clip in mc.gzds_in(w, s, e, n):
        gzd = f"{zone}{mc.BANDS[bi]}"
        zone_box = box(*b)
        if size is None:
            made.append((zone_box, {"mgrs": gzd, "gzd": gzd, "label": gzd}))
            continue
        south = bi < 10
        fwd = mc._utm(zone, south)[0]
        # the zone's piece of the area, in UTM metres
        cw, cs, ce, cn = clip
        xs = [cw + (ce - cw) * k / 8 for k in range(9)] * 2 + [cw] * 9 + [ce] * 9
        ys = [cs] * 9 + [cn] * 9 + [cs + (cn - cs) * k / 8 for k in range(9)] * 2
        ex, ny = fwd.transform(xs, ys)
        e0, e1 = math.floor(min(ex) / size) * size, math.ceil(max(ex) / size) * size
        n0, n1 = math.floor(min(ny) / size) * size, math.ceil(max(ny) / size) * size
        count = ((e1 - e0) / size) * ((n1 - n0) / size)
        if count + len(made) > MAX_CELLS:
            raise Problem(422, f"That is more than {MAX_CELLS:,} squares: zoom in, or choose larger squares.", code="too_many")
        piece = zone_box                                  # whole squares, cut only where a grid zone ends
        digits = 5 - int(math.log10(size))
        ee = e0
        while ee < e1:
            nn = n0
            while nn < n1:
                sq = _utm_ring(zone, south, ee, nn, ee + size, nn + size, 4 if size < 10_000 else 8)
                if not sq.intersects(area):
                    nn += size
                    continue
                cut = sq.intersection(piece)
                if not cut.is_empty and cut.area > 0:
                    rp = cut.representative_point()             # inside the square and its grid zone: names it
                    try:
                        ref = mc.to_mgrs(rp.x, rp.y, digits, spaced=True)
                    except mc.CoordError:
                        ref = None
                    if ref:
                        made.append((cut, {"mgrs": ref, "gzd": gzd, "label": ref}))
                nn += size
            ee += size
    if not made:
        raise Problem(422, "No grid squares fall in that area.", code="validation")

    async def compute(conn):
        return made

    fields = [{"name": "mgrs", "type": "string"}, {"name": "gzd", "type": "string"}, {"name": "label", "type": "string"}]
    word = {"gzd": "grid zones", "100km": "100 km", "10km": "10 km", "1km": "1 km", "100m": "100 m"}[level]
    return Built(None, args, "Polygon", fields, f"MGRS grid {word}", compute, GRID_STYLE, GRID_LABELS)


@tool("gars_grid", "GARS grid", "Military",
      "The Global Area Reference System as polygons over an area: 30-minute cells, 15-minute quadrants or 5-minute "
      "keypad squares, each with its GARS reference.",
      AREA, _p("level", "Cells", "choice", default="30", choices=[["30", "30 minutes"], ["15", "15 minutes"], ["5", "5 minutes"]]))
def gars_grid(params, layers, args, a):
    from .tools import Built
    w, s, e, n = _area(params)
    lv = int(params.get("level") or 30)
    if lv not in (30, 15, 5):
        raise Problem(422, "Choose the size of the cells.", code="validation", fields={"params": ["level"]})
    step = lv / 60
    x0, x1 = math.floor((w + 180) / step), math.ceil((e + 180) / step)
    y0, y1 = math.floor((s + 90) / step), math.ceil((n + 90) / step)
    if (x1 - x0) * (y1 - y0) > MAX_CELLS:
        raise Problem(422, f"That is more than {MAX_CELLS:,} cells: zoom in, or choose larger cells.", code="too_many")
    made = []
    for i in range(x0, x1):
        for j in range(y0, y1):
            cw, cs = -180 + i * step, -90 + j * step
            ref = mc.to_gars(cw + step / 2, cs + step / 2, lv)
            made.append((box(cw, cs, cw + step, cs + step), {"gars": ref, "label": ref}))

    async def compute(conn):
        return made

    return Built(None, args, "Polygon", [{"name": "gars", "type": "string"}, {"name": "label", "type": "string"}], f"GARS grid {lv} minutes", compute,
                 GRID_STYLE, GRID_LABELS)


# ================= distance and direction =================
from pyproj import Geod  # noqa: E402

GEOD = Geod(ellps="WGS84")
UNITS = {"m": 1.0, "km": 1000.0, "mi": 1609.344, "nmi": 1852.0, "ft": 0.3048, "yd": 0.9144}
UNIT_CHOICES = [["m", "metres"], ["km", "kilometres"], ["mi", "miles"], ["nmi", "nautical miles"], ["ft", "feet"], ["yd", "yards"]]
ANGLES = {"deg": 1.0, "mil": 360 / 6400}            # NATO mils: 6400 to the circle
ANGLE_CHOICES = [["deg", "degrees"], ["mil", "mils (6400)"]]
MILS = 6400 / 360


def _origin_params(label="Origins"):
    return [
        _p("origin", f"{label} from", "choice", default="typed", choices=[["typed", "coordinates I type"], ["layer", "a point layer"]]),
        _p("origin_text", f"{label}, one per line (any notation; a name and a colon may go first)", "text", lines=True, default="",
           when={"origin": "typed"}),
        _p("input", f"{label} layer", "layer", geometry=["Point"], optional=True, when={"origin": "layer"}),
    ]


def read_lines(text: str, how: str = "auto", order: str = "latlon", what: str = "coordinates") -> list[tuple[str | None, float, float]]:
    """(name, longitude, latitude) for each line; any line that can't be read stops it, with the line number."""
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    out, bad = [], []
    for i, line in enumerate(lines, 1):
        try:
            lng, lat, _ = mc.parse(line, how, order)
            out.append((None, lng, lat))
        except mc.CoordError as e:
            head, _, tail = line.partition(":")
            try:
                if not tail.strip():
                    raise e
                lng, lat, _ = mc.parse(tail, how, order)
                out.append((head.strip()[:120], lng, lat))
            except mc.CoordError:
                bad.append(f"line {i} ({line[:40]}): {e}")
    if bad:
        raise Problem(422, f"{len(bad)} of the {what} could not be read: " + "; ".join(bad[:3]) + ("…" if len(bad) > 3 else ""), code="validation")
    if len(out) > 2000:
        raise Problem(422, f"Up to 2,000 {what} at a time.", code="validation")
    return out


def _origins(params, args, label="origins", text_key="origin_text", choice_key="origin", layer_key="input"):
    """async (conn) -> [(name, lng, lat, props)] from typed coordinates or a point layer."""
    if params.get(choice_key) == "layer":
        if not args.get(layer_key):
            raise Problem(422, f"Choose the {label} layer.", code="validation", fields={"params": [layer_key]})

        async def from_layer(conn):
            cur = await conn.execute(f"SELECT id, ST_X(ST_PointOnSurface(geom)) AS x, ST_Y(ST_PointOnSurface(geom)) AS y, props FROM features "
                                     f"WHERE layer_id = layer_data(%s) AND (%s::bigint[] IS NULL OR id = ANY(%s::bigint[])) ORDER BY id",
                                     (args[layer_key], args.get(f"{layer_key}_ids"), args.get(f"{layer_key}_ids")))
            return [(str(r["props"].get("name") or r["id"]) if r["props"] else str(r["id"]), r["x"], r["y"], r["props"] or {})
                    for r in await cur.fetchall()]
        return from_layer
    pts = read_lines(params.get(text_key), what=label)
    if not pts:
        raise Problem(422, f"Type the {label}: one position per line.", code="validation", fields={"params": [text_key]})

    async def typed(conn):
        return [(n or f"{label.rstrip('s').capitalize()} {i}", x, y, {}) for i, (n, x, y) in enumerate(pts, 1)]
    return typed


def _unwrap(lngs):
    """Longitudes made continuous across 180° (a ring around a point near the date line stays whole)."""
    out = [lngs[0]]
    for v in lngs[1:]:
        d = v - out[-1]
        out.append(out[-1] + d - 360 * round(d / 360))
    return out


def ring(lng, lat, r_m, n=180):
    az = [360 * k / n for k in range(n + 1)]
    xs, ys, _ = GEOD.fwd([lng] * len(az), [lat] * len(az), az, [r_m] * len(az))
    return shapely.LineString(list(zip(_unwrap(list(xs)), ys)))


def geo_line(lng, lat, az, dist_m, n=32):
    """A geodesic from a point along an azimuth, as a line with its curve kept."""
    d = [dist_m * k / n for k in range(n + 1)]
    xs, ys, _ = GEOD.fwd([lng] * len(d), [lat] * len(d), [az] * len(d), d)
    return shapely.LineString(list(zip(_unwrap(list(xs)), ys)))


def between(x1, y1, x2, y2, step_m=2000):
    """The geodesic between two points, densified."""
    _, _, dist = GEOD.inv(x1, y1, x2, y2)
    n = int(min(256, max(0, dist // step_m)))
    pts = [(x1, y1)] + [tuple(p) for p in GEOD.npts(x1, y1, x2, y2, n)] + [(x2, y2)] if n else [(x1, y1), (x2, y2)]
    xs = _unwrap([p[0] for p in pts])
    return shapely.LineString(list(zip(xs, [p[1] for p in pts])))


def _az(v):
    return round(v % 360, 4)


@tool("range_rings", "Range rings", "Military",
      "Rings at set ranges around each origin, measured on the ellipsoid, with radials (spokes) if wanted: weapon, sensor or "
      "travel ranges. Ranges are a list, or an interval and a number of rings.",
      *_origin_params(),
      _p("how", "Ranges", "choice", default="list", choices=[["list", "a list of ranges"], ["interval", "an interval and a number of rings"]]),
      _p("ranges", "Ranges (separated by commas)", "text", default="1, 2, 5", when={"how": "list"}),
      _p("interval", "Interval", "number", min=0.001, default=1, when={"how": "interval"}),
      _p("count", "Number of rings", "number", min=1, default=4, when={"how": "interval"}),
      _p("unit", "Unit", "choice", default="km", choices=UNIT_CHOICES),
      _p("radials", "Radials (0 for none)", "number", min=0, default=8))
def range_rings(params, layers, args, a):
    from .tools import Built
    from .tools_more import numbers
    from .tools import number
    unit = params.get("unit") or "km"
    if unit not in UNITS:
        raise Problem(422, "Choose the unit.", code="validation", fields={"params": ["unit"]})
    if params.get("how") == "interval":
        step = number(params, "interval", 0.001, 1e6, "Interval")
        k = int(number(params, "count", 1, 100, "Number of rings"))
        ranges = [step * i for i in range(1, k + 1)]
    else:
        ranges = numbers(params, "ranges", "Ranges", lo=0.001, hi=1e6, most=50)
    if max(ranges) * UNITS[unit] > 10_000_000:
        raise Problem(422, "Ranges go up to 10,000 km.", code="validation", fields={"params": ["ranges"]})
    radials = int(number(params, "radials", 0, 360, "Radials"))
    origins = _origins(params, args)

    async def compute(conn):
        made = []
        for name, x, y, _ in await origins(conn):
            for r in ranges:
                made.append((ring(x, y, r * UNITS[unit]), {"origin": name, "kind": "ring", "range": r, "unit": unit, "range_m": round(r * UNITS[unit], 3),
                                                            "bearing": None, "label": f"{r:g} {unit}"}))
            for i in range(radials):
                az = 360 * i / radials
                made.append((geo_line(x, y, az, max(ranges) * UNITS[unit]), {"origin": name, "kind": "radial", "range": max(ranges), "unit": unit,
                                                                               "range_m": round(max(ranges) * UNITS[unit], 3), "bearing": round(az, 3),
                                                                               "label": f"{az:g}°"}))
        if len(made) > 50_000:
            raise Problem(422, "That makes more than 50,000 rings and radials: use fewer origins or ranges.", code="too_many")
        return made

    fields = [{"name": "origin", "type": "string"}, {"name": "kind", "type": "string"}, {"name": "range", "type": "number"}, {"name": "unit", "type": "string"},
              {"name": "range_m", "type": "number"}, {"name": "bearing", "type": "number"}, {"name": "label", "type": "string"}]
    return Built(None, args, "LineString", fields, "Range rings", compute,
                 {"type": "single", "color": "#c0262d", "stroke": "#c0262d", "width": 1.6, "size": 6, "opacity": 0.95},
                 {"field": "label", "size": 11, "color": "#7a1218", "halo": "#ffffff", "halo_width": 1.6, "font": "bold", "line_placement": "along"})


@tool("bearing_lines", "Bearing lines", "Military",
      "Lines from each origin along bearings (true north, degrees or mils) for a distance, following the ellipsoid: "
      "lines of fire, sight lines, intercept bearings.",
      *_origin_params(),
      _p("bearings", "Bearings (separated by commas)", "text", default="0, 45, 90"),
      _p("angle", "Bearings in", "choice", default="deg", choices=ANGLE_CHOICES),
      _p("distance", "Length", "number", min=0.001, default=10),
      _p("unit", "Unit", "choice", default="km", choices=UNIT_CHOICES))
def bearing_lines(params, layers, args, a):
    from .tools import Built, number
    from .tools_more import numbers
    angle = params.get("angle") or "deg"
    if angle not in ANGLES:
        raise Problem(422, "Choose degrees or mils.", code="validation", fields={"params": ["angle"]})
    top = 6400 if angle == "mil" else 360
    bearings = numbers(params, "bearings", "Bearings", lo=-top, hi=top, most=100)
    unit = params.get("unit") or "km"
    if unit not in UNITS:
        raise Problem(422, "Choose the unit.", code="validation", fields={"params": ["unit"]})
    dist = number(params, "distance", 0.001, 1e7, "Length") * UNITS[unit]
    if dist > 20_000_000:
        raise Problem(422, "A bearing line is at most 20,000 km.", code="validation", fields={"params": ["distance"]})
    origins = _origins(params, args)

    async def compute(conn):
        made = []
        for name, x, y, _ in await origins(conn):
            for b in bearings:
                az = b * ANGLES[angle]
                line = geo_line(x, y, az, dist)
                end = line.coords[-1]
                back = GEOD.inv(end[0], end[1], x, y)[0]
                made.append((line, {"origin": name, "bearing": _az(az), "bearing_mils": round((az % 360) * MILS, 1), "back_bearing": _az(back),
                                    "distance": round(dist / UNITS[unit], 6), "unit": unit, "label": f"{_az(az):g}°"}))
        return made

    fields = [{"name": "origin", "type": "string"}, {"name": "bearing", "type": "number"}, {"name": "bearing_mils", "type": "number"},
              {"name": "back_bearing", "type": "number"}, {"name": "distance", "type": "number"}, {"name": "unit", "type": "string"},
              {"name": "label", "type": "string"}]
    return Built(None, args, "LineString", fields, "Bearing lines", compute,
                 {"type": "single", "color": "#1d3557", "stroke": "#1d3557", "width": 1.8, "size": 6, "opacity": 0.95})


@tool("distance_bearing", "Distance and bearing between points", "Military",
      "From each origin to each destination: the geodesic line, its length, the bearing out (true, degrees and mils) and "
      "the back bearing.",
      *_origin_params(),
      _p("dest", "Destinations from", "choice", default="typed", choices=[["typed", "coordinates I type"], ["layer", "a point layer"]]),
      _p("dest_text", "Destinations, one per line", "text", lines=True, default="", when={"dest": "typed"}),
      _p("targets", "Destinations layer", "layer", geometry=["Point"], optional=True, when={"dest": "layer"}),
      _p("unit", "Unit", "choice", default="km", choices=UNIT_CHOICES))
def distance_bearing(params, layers, args, a):
    from .tools import Built
    unit = params.get("unit") or "km"
    if unit not in UNITS:
        raise Problem(422, "Choose the unit.", code="validation", fields={"params": ["unit"]})
    origins = _origins(params, args)
    dests = _origins(params, args, "destinations", "dest_text", "dest", "targets")

    async def compute(conn):
        os_, ds = await origins(conn), await dests(conn)
        if len(os_) * len(ds) > 20_000:
            raise Problem(422, "That is more than 20,000 pairs: use fewer origins or destinations.", code="too_many")
        made = []
        for on, x1, y1, _ in os_:
            for dn, x2, y2, _ in ds:
                if (x1, y1) == (x2, y2):
                    continue
                fwd, back, dist = GEOD.inv(x1, y1, x2, y2)
                made.append((between(x1, y1, x2, y2), {"origin": on, "destination": dn, "distance": round(dist / UNITS[unit], 6), "unit": unit,
                                                       "distance_m": round(dist, 3), "bearing": _az(fwd), "bearing_mils": round((fwd % 360) * MILS, 1),
                                                       "back_bearing": _az(back), "label": f"{dist / UNITS[unit]:,.2f} {unit} · {_az(fwd):.1f}°"}))
        return made

    fields = [{"name": "origin", "type": "string"}, {"name": "destination", "type": "string"}, {"name": "distance", "type": "number"},
              {"name": "unit", "type": "string"}, {"name": "distance_m", "type": "number"}, {"name": "bearing", "type": "number"},
              {"name": "bearing_mils", "type": "number"}, {"name": "back_bearing", "type": "number"}, {"name": "label", "type": "string"}]
    return Built(None, args, "LineString", fields, "Distance and bearing", compute,
                 {"type": "single", "color": "#6b3fa0", "stroke": "#6b3fa0", "width": 2, "size": 6, "opacity": 0.95},
                 {"field": "label", "size": 11, "color": "#3b1f5e", "halo": "#ffffff", "halo_width": 1.6, "line_placement": "along"})


@tool("ellipse", "Ellipses", "Military",
      "An ellipse around each origin from its major and minor axes and the bearing of the major axis, on the ellipsoid: "
      "target areas, error ellipses, footprints.",
      *_origin_params("Centres"),
      _p("major", "Major axis", "number", min=0.001, default=4), _p("minor", "Minor axis", "number", min=0.001, default=2),
      _p("axes", "The axes are", "choice", default="full", choices=[["full", "full lengths (diameters)"], ["semi", "half lengths (radii)"]]),
      _p("orientation", "Bearing of the major axis (degrees)", "number", default=0),
      _p("unit", "Unit", "choice", default="km", choices=UNIT_CHOICES))
def ellipse(params, layers, args, a):
    from .tools import Built, number
    unit = params.get("unit") or "km"
    if unit not in UNITS:
        raise Problem(422, "Choose the unit.", code="validation", fields={"params": ["unit"]})
    half = 0.5 if (params.get("axes") or "full") == "full" else 1.0
    A = number(params, "major", 0.001, 1e7, "Major axis") * UNITS[unit] * half
    B = number(params, "minor", 0.001, 1e7, "Minor axis") * UNITS[unit] * half
    if B > A:
        A, B = B, A
    if A > 5_000_000:
        raise Problem(422, "An ellipse is at most 10,000 km across.", code="validation", fields={"params": ["major"]})
    theta = number(params, "orientation", -360, 360, "Bearing of the major axis")
    origins = _origins(params, args, "centres")

    async def compute(conn):
        made = []
        ts = [2 * math.pi * k / 180 for k in range(181)]
        az = [theta + math.degrees(math.atan2(B * math.sin(t), A * math.cos(t))) for t in ts]
        rr = [math.hypot(A * math.cos(t), B * math.sin(t)) for t in ts]
        for name, x, y, _ in await origins(conn):
            xs, ys, _ = GEOD.fwd([x] * len(ts), [y] * len(ts), az, rr)
            poly = shapely.Polygon(list(zip(_unwrap(list(xs)), ys)))
            made.append((poly, {"centre": name, "major": round(2 * A / UNITS[unit], 6), "minor": round(2 * B / UNITS[unit], 6), "unit": unit,
                                "orientation": _az(theta), "area_km2": round(math.pi * A * B / 1e6, 4)}))
        return made

    fields = [{"name": "centre", "type": "string"}, {"name": "major", "type": "number"}, {"name": "minor", "type": "number"},
              {"name": "unit", "type": "string"}, {"name": "orientation", "type": "number"}, {"name": "area_km2", "type": "number"}]
    return Built(None, args, "Polygon", fields, "Ellipses", compute,
                 {"type": "single", "color": "#e07a10", "stroke": "#b35c00", "width": 1.6, "size": 6, "opacity": 0.15})
