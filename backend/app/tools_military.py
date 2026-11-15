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
