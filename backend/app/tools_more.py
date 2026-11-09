"""More geoprocessing tools, in a registry: each tool declares its parameters and a builder. Most build one PostGIS
query; spatial statistics and network analysis compute in Python (numpy, networkx) from the features they read.

Measurements are true metres: on the ellipsoid (geography) where PostGIS offers it, otherwise in the UTM zone of the
input layer's centre (moving, rotating, offsetting, snapping, clustering by distance, statistics on distances)."""
import json
import math
import re
from collections.abc import Callable

import numpy as np
import shapely
from pyproj import Transformer

from .errors import Problem
from .tools import Built, _p, field_add, need_kind, norm, number, renamed, renamed_props, src

TOOLS: dict[str, dict] = {}
BUILDERS: dict[str, Callable] = {}


def tool(tid: str, name: str, group: str, about: str, *params):
    def reg(fn):
        TOOLS[tid] = {"name": name, "group": group, "about": about, "params": list(params)}
        BUILDERS[tid] = fn
        return fn
    return reg


IN = _p("input", "Input layer", "layer")
IN_PTS = _p("input", "Input points", "layer", geometry=["Point"])
IN_LINES = _p("input", "Input lines", "layer", geometry=["LineString"])
IN_POLY = _p("input", "Input polygons", "layer", geometry=["Polygon"])
IN_LP = _p("input", "Input layer", "layer", geometry=["LineString", "Polygon"])
EACH = _p("each", "One for each feature (else one for all)", "bool", default=True)


# ---------- helpers ----------
def utm_srid(layer: dict) -> int:
    """The UTM zone (WGS 84) of the layer's centre, for metric work."""
    b = layer.get("bbox") or [0, 0, 0, 0]
    lng, lat = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    zone = min(60, max(1, int((lng + 180) // 6) + 1))
    return (32600 if lat >= 0 else 32700) + zone


def in_utm(expr: str) -> str:
    """`expr` (an operation on X, where X is the geometry in UTM) brought back to WGS 84."""
    return f"ST_Transform({re.sub(r'\bX\b', 'ST_Transform(geom, %(utm)s)', expr)}, 4326)"


def numbers(params: dict, name: str, label: str, lo=0.0, hi=1_000_000.0, most=20) -> list[float]:
    raw = params.get(name)
    vals = raw if isinstance(raw, list) else str(raw or "").replace(";", ",").split(",")
    try:
        out = sorted({float(str(v).strip()) for v in vals if str(v).strip()})
    except ValueError:
        raise Problem(422, f"{label}: give numbers separated by commas, e.g. 100, 250, 500.", code="validation",
                      fields={"params": [name]}) from None
    if not out or len(out) > most or out[0] < lo or out[-1] > hi:
        raise Problem(422, f"{label}: give 1 to {most} numbers between {lo:g} and {hi:g}.", code="validation", fields={"params": [name]})
    return out


def field_of(layer: dict, name: str | None, label: str, numeric=False, optional=False) -> str | None:
    if not name:
        if optional:
            return None
        raise Problem(422, f"Choose the {label.lower()}.", code="validation", fields={"params": [label]})
    f = next((x for x in layer["fields"] if x["name"] == name), None)
    if not f:
        raise Problem(422, f"{layer['name']} has no field called {name}.", code="validation", fields={"params": [label]})
    if numeric and f["type"] not in ("integer", "number"):
        raise Problem(422, f"{name} must be a number field.", code="validation", fields={"params": [label]})
    return name


NUM = "(CASE WHEN jsonb_typeof(props->%({k})s) = 'number' THEN (props->>%({k})s)::float8 END)"


def num(key: str, alias: str = "") -> str:
    return NUM.replace("props", f"{alias}props" if alias else "props").replace("{k}", key)


async def rows_of(conn, key: str, args: dict, extra: str = "") -> list[dict]:
    cur = await conn.execute(f"SELECT id, ST_AsBinary(geom) AS wkb, props{extra} FROM {src(key)} s ORDER BY id", args)
    return await cur.fetchall()


def to_utm(srid: int):
    t = Transformer.from_crs(4326, srid, always_xy=True)
    back = Transformer.from_crs(srid, 4326, always_xy=True)
    return t, back


def pval(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


# =====================================================================================================================
# Proximity
# =====================================================================================================================
@tool("multi_buffer", "Multiple-ring buffer", "Proximity", "Rings at several distances around each feature.",
      IN, _p("distances", "Distances (metres, separated by commas)", "text", default="100, 250, 500"))
def _(params, layers, args, a):
    args["ds"] = numbers(params, "distances", "Distances", 0.01)
    sql = (f"SELECT CASE WHEN dd.prev IS NULL THEN ST_Buffer(s.geom::geography, dd.d)::geometry "
           f"ELSE ST_Difference(ST_Buffer(s.geom::geography, dd.d)::geometry, ST_Buffer(s.geom::geography, dd.prev)::geometry) END AS g, "
           f"s.props || jsonb_build_object('distance_m', dd.d, 'from_m', COALESCE(dd.prev, 0)) AS p FROM {src('input')} s "
           f"CROSS JOIN LATERAL (SELECT d, lag(d) OVER (ORDER BY d) AS prev FROM unnest(%(ds)s::float8[]) d) dd")
    return Built(sql, args, "Polygon", field_add(a["fields"], {"name": "distance_m", "type": "number"}, {"name": "from_m", "type": "number"}),
                 f"{a['name']} rings")


@tool("buffer_by_field", "Buffer by field", "Proximity", "Each feature buffered by the distance in one of its fields.",
      IN, _p("field", "Distance field (metres)", "field", of="input", numeric=True))
def _(params, layers, args, a):
    args["f"] = field_of(a, params.get("field"), "Distance field", numeric=True)
    sql = (f"SELECT ST_Buffer(geom::geography, {num('f')})::geometry AS g, props AS p FROM {src('input')} s "
           f"WHERE {num('f')} > 0")
    return Built(sql, args, "Polygon", a["fields"], f"{a['name']} buffer by {args['f']}")


@tool("connect_nearest", "Connect to nearest", "Proximity", "A line from each feature to the nearest feature of another layer, with its length.",
      IN, _p("near", "Nearest of", "layer"))
def _(params, layers, args, a):
    same = layers["near"]["id"] == a["id"]
    sql = (f"SELECT ST_ShortestLine(s.geom, n.geom) AS g, s.props || jsonb_build_object('near_id', n.id, "
           f"'distance_m', round(ST_Distance(s.geom::geography, n.geom::geography)::numeric, 3)) AS p FROM {src('input')} s "
           f"CROSS JOIN LATERAL (SELECT c.id, c.geom FROM (SELECT b.id, b.geom FROM {src('near')} b {'WHERE b.id <> s.id' if same else ''} "
           f"ORDER BY s.geom <-> b.geom LIMIT 8) c ORDER BY ST_Distance(s.geom::geography, c.geom::geography) LIMIT 1) n")
    return Built(sql, args, "LineString", field_add(a["fields"], {"name": "near_id", "type": "integer"}, {"name": "distance_m", "type": "number"}),
                 f"{a['name']} to nearest")


@tool("k_nearest", "K nearest neighbours", "Proximity", "Lines from each feature to its k nearest features in another (or the same) layer.",
      IN, _p("near", "Neighbours from", "layer"), _p("k", "How many", "number", min=1, default=3))
def _(params, layers, args, a):
    args["k"] = int(number(params, "k", 1, 50, "How many"))
    same = layers["near"]["id"] == a["id"]
    sql = (f"SELECT ST_ShortestLine(s.geom, n.geom) AS g, jsonb_build_object('from_id', s.id, 'to_id', n.id, 'rank', n.r, "
           f"'distance_m', round(n.d::numeric, 3)) AS p FROM {src('input')} s CROSS JOIN LATERAL (SELECT c.id, c.geom, c.d, "
           f"row_number() OVER (ORDER BY c.d) AS r FROM (SELECT b.id, b.geom, ST_Distance(s.geom::geography, b.geom::geography) AS d "
           f"FROM {src('near')} b {'WHERE b.id <> s.id' if same else ''} ORDER BY s.geom <-> b.geom LIMIT %(k)s * 3 + 5) c "
           f"ORDER BY c.d LIMIT %(k)s) n")
    return Built(sql, args, "LineString", [{"name": "from_id", "type": "integer"}, {"name": "to_id", "type": "integer"},
                                           {"name": "rank", "type": "integer"}, {"name": "distance_m", "type": "number"}], f"{a['name']} {args['k']} nearest")


@tool("point_distance", "Point distance", "Proximity", "Lines between every pair of points (of two layers) within a distance, with the distance.",
      IN_PTS, _p("near", "To points", "layer", geometry=["Point"]), _p("distance", "Within", "number", unit="m", min=0.01, default=1000))
def _(params, layers, args, a):
    args["d"] = number(params, "distance", 0.01, 1_000_000, "Within")
    same = layers["near"]["id"] == a["id"]
    sql = (f"SELECT ST_MakeLine(s.geom, b.geom) AS g, jsonb_build_object('from_id', s.id, 'to_id', b.id, 'distance_m', "
           f"round(ST_Distance(s.geom::geography, b.geom::geography)::numeric, 3)) AS p FROM {src('input')} s JOIN {src('near')} b "
           f"ON ST_DWithin(s.geom::geography, b.geom::geography, %(d)s) {'AND b.id > s.id' if same else ''}")
    return Built(sql, args, "LineString", [{"name": "from_id", "type": "integer"}, {"name": "to_id", "type": "integer"},
                                           {"name": "distance_m", "type": "number"}], f"{a['name']} point distances")


@tool("points_along_lines", "Points along lines", "Proximity", "Points at a set interval along each line, with their distance from the start.",
      IN_LINES, _p("interval", "Every", "number", unit="m", min=0.01, default=100), _p("ends", "Include the end of each line", "bool", default=True))
def _(params, layers, args, a):
    args["iv"] = number(params, "interval", 0.01, 1_000_000, "Every")
    sql = (f"SELECT ST_LineInterpolatePoint(part.g, LEAST(1, k * %(iv)s / part.len)) AS g, s.props || jsonb_build_object('distance_m', "
           f"round(LEAST(part.len, k * %(iv)s)::numeric, 3)) AS p FROM {src('input')} s CROSS JOIN LATERAL (SELECT d.geom AS g, "
           f"ST_Length(d.geom::geography) AS len FROM ST_Dump(ST_LineMerge(s.geom)) d) part CROSS JOIN LATERAL "
           f"generate_series(0, floor(part.len / %(iv)s)::int + (CASE WHEN %(ends)s AND mod(part.len::numeric, %(iv)s::numeric) > 0.001 THEN 1 ELSE 0 END)) k "
           f"WHERE part.len > 0 AND k <= 100000")
    args["ends"] = bool(params.get("ends", True))
    return Built(sql, args, "Point", field_add(a["fields"], {"name": "distance_m", "type": "number"}), f"{a['name']} every {args['iv']:g} m")


@tool("extract_by_location", "Extract by location", "Proximity", "The features that intersect, are within, contain, or are near the features of another layer.",
      IN, _p("other", "Of the layer", "layer"), _p("relation", "That", "choice", default="intersects",
          choices=[["intersects", "intersect"], ["within", "are within"], ["contains", "contain"], ["distance", "are within a distance of"], ["disjoint", "do not touch"]]),
      _p("distance", "Distance", "number", unit="m", min=0, default=100, when={"relation": "distance"}))
def _(params, layers, args, a):
    rel = params.get("relation") or "intersects"
    test = {"intersects": "ST_Intersects(s.geom, o.geom)", "within": "ST_Within(s.geom, o.geom)", "contains": "ST_Contains(s.geom, o.geom)",
            "distance": "ST_DWithin(s.geom::geography, o.geom::geography, %(d)s)", "disjoint": "ST_Intersects(s.geom, o.geom)"}[rel]
    args["d"] = number(params, "distance", 0, 1_000_000, "Distance") if rel == "distance" else 0
    neg = "NOT " if rel == "disjoint" else ""
    sql = f"SELECT s.geom AS g, s.props AS p FROM {src('input')} s WHERE {neg}EXISTS (SELECT 1 FROM {src('other')} o WHERE {test})"
    return Built(sql, args, a["geometry_type"], a["fields"], f"{a['name']} extracted")


# =====================================================================================================================
# Overlay
# =====================================================================================================================
OPS = [["=", "is"], ["!=", "is not"], ["<", "less than"], ["<=", "at most"], [">", "more than"], [">=", "at least"],
       ["contains", "contains"], ["empty", "is empty"], ["not_empty", "is not empty"]]


@tool("extract_by_attribute", "Extract by attributes", "Overlay", "The features whose field meets a condition, as a new layer.",
      IN, _p("field", "Field", "field", of="input"), _p("op", "Test", "choice", default="=", choices=OPS), _p("value", "Value", "text", optional=True))
def _(params, layers, args, a):
    from .routers.layers import Filter, where_of
    field_of(a, params.get("field"), "Field")
    try:
        f = Filter(field=params["field"], op=params.get("op") or "=", value=params.get("value"))
    except Exception:  # noqa: BLE001
        raise Problem(422, "That condition is not right.", code="validation") from None
    where, vals = where_of(a, [f])
    for i, v in enumerate(vals):
        args[f"w{i}"] = v
    k = iter(range(len(vals)))
    where = re.sub(r"%s", lambda _m: f"%(w{next(k)})s", where)
    sql = f"SELECT geom AS g, props AS p FROM {src('input')} s WHERE {where}"
    return Built(sql, args, a["geometry_type"], a["fields"], f"{a['name']} where {params['field']}")


def _faces(args, a, b, keep: str) -> tuple[str, list[dict]]:
    """Polygon overlay: every face made by the boundaries of both layers, with the attributes of the polygons it is in."""
    bf, ren = renamed(a["fields"], b["fields"])
    args["ren"] = json.dumps(ren)
    sql = (f"WITH a AS {src('input')}, b AS {src('overlay')}, "
           f"faces AS (SELECT (ST_Dump(ST_Polygonize(l))).geom AS f FROM (SELECT ST_Union(ST_Boundary(geom)) AS l FROM "
           f"(SELECT geom FROM a UNION ALL SELECT geom FROM b) x) y) "
           f"SELECT faces.f AS g, COALESCE(pa.props, '{{}}'::jsonb) || COALESCE(pb.rp, '{{}}'::jsonb) AS p FROM faces "
           f"LEFT JOIN LATERAL (SELECT a.props FROM a WHERE ST_Contains(a.geom, ST_PointOnSurface(faces.f)) ORDER BY a.id LIMIT 1) pa ON true "
           f"LEFT JOIN LATERAL (SELECT {renamed_props('b', 'ren')} AS rp FROM b WHERE ST_Contains(b.geom, ST_PointOnSurface(faces.f)) "
           f"ORDER BY b.id LIMIT 1) pb ON true WHERE {keep}")
    return sql, a["fields"] + bf


for _tid, _name, _about, _keep in (
        ("union", "Union", "Both polygon layers overlaid: every piece, with the attributes of whichever polygons it lies in.",
         "pa.props IS NOT NULL OR pb.rp IS NOT NULL"),
        ("identity", "Identity", "The input polygons cut by the overlay, each piece with the overlay's attributes where it lies in one.",
         "pa.props IS NOT NULL"),
        ("symmetrical_difference", "Symmetrical difference", "The parts that lie in one layer or the other, but not both.",
         "(pa.props IS NULL) <> (pb.rp IS NULL)")):
    def _mk(keep, tid):
        def build(params, layers, args, a):
            need_kind(a, ["Polygon"], "The input")
            need_kind(layers["overlay"], ["Polygon"], "The overlay")
            sql, fields = _faces(args, a, layers["overlay"], keep)
            return Built(sql, args, "Polygon", fields, f"{a['name']} {tid.replace('_', ' ')}")
        return build
    tool(_tid, _name, "Overlay", _about, IN_POLY, _p("overlay", "Overlay polygons", "layer", geometry=["Polygon"]))(_mk(_keep, _tid))


@tool("update", "Update", "Overlay", "The input polygons with the update polygons put in their place where they overlap.",
      IN_POLY, _p("overlay", "Update polygons", "layer", geometry=["Polygon"]))
def _(params, layers, args, a):
    bf, ren = renamed(a["fields"], layers["overlay"]["fields"])
    args["ren"] = json.dumps(ren)
    sql = (f"WITH u AS (SELECT ST_Union(geom) AS u FROM {src('overlay')} o) "
           f"SELECT {norm('CASE WHEN u.u IS NOT NULL AND ST_Intersects(s.geom, u.u) THEN ST_Difference(s.geom, u.u) ELSE s.geom END', 'Polygon')} AS g, "
           f"s.props AS p FROM {src('input')} s, u UNION ALL SELECT o.geom, {renamed_props('o', 'ren')} FROM {src('overlay')} o")
    return Built(sql, args, "Polygon", a["fields"] + bf, f"{a['name']} updated")


@tool("split_by_lines", "Split by lines", "Overlay", "Polygons or lines cut where lines of another layer cross them.",
      IN_LP, _p("blade", "Cut with lines", "layer", geometry=["LineString", "Polygon"]))
def _(params, layers, args, a):
    sql = (f"WITH bl AS (SELECT ST_Union(CASE WHEN GeometryType(geom) LIKE '%%POLYGON' THEN ST_Boundary(geom) ELSE geom END) AS b "
           f"FROM {src('blade')} k) SELECT (ST_Dump(CASE WHEN bl.b IS NOT NULL AND ST_Intersects(s.geom, bl.b) "
           f"THEN ST_CollectionExtract(ST_Split(s.geom, bl.b), {3 if a['geometry_type'] == 'Polygon' else 2}) ELSE s.geom END)).geom AS g, "
           f"s.props AS p FROM {src('input')} s, bl")
    return Built(sql, args, a["geometry_type"], a["fields"], f"{a['name']} split")


# =====================================================================================================================
# Generalization
# =====================================================================================================================
@tool("smooth", "Smooth", "Generalization", "Rounded corners on lines and polygons (Chaikin's method).",
      IN_LP, _p("iterations", "Passes", "number", min=1, default=3))
def _(params, layers, args, a):
    args["n"] = int(number(params, "iterations", 1, 5, "Passes"))
    return Built(f"SELECT {norm('ST_ChaikinSmoothing(geom, %(n)s)', a['geometry_type'])} AS g, props AS p FROM {src('input')} s",
                 args, a["geometry_type"], a["fields"], f"{a['name']} smoothed")


@tool("densify", "Densify", "Generalization", "Extra vertices so that no segment is longer than a distance.",
      IN_LP, _p("distance", "Longest segment", "number", unit="m", min=0.1, default=50))
def _(params, layers, args, a):
    args["d"] = number(params, "distance", 0.1, 1_000_000, "Longest segment")
    return Built(f"SELECT ST_Segmentize(geom::geography, %(d)s)::geometry AS g, props AS p FROM {src('input')} s",
                 args, a["geometry_type"], a["fields"], f"{a['name']} densified")


@tool("concave_hull", "Concave hull", "Generalization", "A tighter outline than the convex hull, around all the features (or each).",
      IN, _p("ratio", "Tightness (0 = tightest, 1 = convex)", "number", min=0, default=0.3), _p("each", "One for each feature (else one for all)", "bool", default=False))
def _(params, layers, args, a):
    args["r"] = number(params, "ratio", 0, 1, "Tightness")
    if params.get("each"):
        return Built(f"SELECT {norm('ST_ConcaveHull(geom, %(r)s)', 'Polygon')} AS g, props AS p FROM {src('input')} s", args, "Polygon",
                     a["fields"], f"{a['name']} concave hulls")
    return Built(f"SELECT {norm('ST_ConcaveHull(ST_Collect(geom), %(r)s)', 'Polygon')} AS g, jsonb_build_object('count', count(*)) AS p "
                 f"FROM {src('input')} s", args, "Polygon", [{"name": "count", "type": "integer"}], f"{a['name']} concave hull")


for _tid, _name, _fn, _about in (("min_circle", "Minimum bounding circle", "ST_MinimumBoundingCircle", "The smallest circle around the features."),
                                 ("oriented_box", "Minimum rotated rectangle", "ST_OrientedEnvelope", "The smallest rectangle, at any angle, around the features.")):
    def _mk(fn, tid, name):
        def build(params, layers, args, a):
            if params.get("each", True):
                return Built(f"SELECT {norm(f'{fn}(geom)', 'Polygon')} AS g, props AS p FROM {src('input')} s", args, "Polygon", a["fields"],
                             f"{a['name']} {name.lower()}")
            return Built(f"SELECT {norm(f'{fn}(ST_Collect(geom))', 'Polygon')} AS g, jsonb_build_object('count', count(*)) AS p FROM {src('input')} s",
                         args, "Polygon", [{"name": "count", "type": "integer"}], f"{a['name']} {name.lower()}")
        return build
    tool(_tid, _name, "Generalization", _about, IN, EACH)(_mk(_fn, _tid, _name))


@tool("eliminate", "Eliminate", "Generalization", "Small polygons merged into the neighbour they share the longest border with.",
      IN_POLY, _p("area", "Smaller than", "number", unit="m²", min=0.01, default=100))
def _(params, layers, args, a):
    args["min"] = number(params, "area", 0.01, 1e12, "Smaller than")
    sql = (f"WITH s AS {src('input')}, sm AS (SELECT id, geom FROM s WHERE ST_Area(geom::geography) < %(min)s), "
           f"big AS (SELECT id, geom, props FROM s WHERE ST_Area(geom::geography) >= %(min)s), "
           f"pick AS (SELECT DISTINCT ON (sm.id) sm.id AS sid, b.id AS bid FROM sm JOIN big b ON ST_Intersects(sm.geom, b.geom) "
           f"ORDER BY sm.id, ST_Length(ST_CollectionExtract(ST_Intersection(sm.geom, b.geom), 2)::geography) DESC) "
           f"SELECT {norm('ST_Union(ARRAY[b.geom] || ARRAY(SELECT sm.geom FROM sm JOIN pick ON pick.sid = sm.id WHERE pick.bid = b.id))', 'Polygon')} AS g, "
           f"b.props AS p FROM big b UNION ALL SELECT s.geom, s.props FROM s JOIN sm ON sm.id = s.id WHERE NOT EXISTS (SELECT 1 FROM pick WHERE pick.sid = s.id)")
    return Built(sql, args, "Polygon", a["fields"], f"{a['name']} eliminated")


@tool("remove_small", "Remove small polygons", "Generalization", "Polygons smaller than an area left out.",
      IN_POLY, _p("area", "Smaller than", "number", unit="m²", min=0.01, default=100))
def _(params, layers, args, a):
    args["min"] = number(params, "area", 0.01, 1e12, "Smaller than")
    return Built(f"SELECT geom AS g, props AS p FROM {src('input')} s WHERE ST_Area(geom::geography) >= %(min)s", args, "Polygon", a["fields"],
                 f"{a['name']} without small polygons")


@tool("fill_holes", "Fill holes", "Generalization", "Holes in polygons filled (all of them, or those smaller than an area).",
      IN_POLY, _p("area", "Only holes smaller than (0 = all)", "number", unit="m²", min=0, default=0))
def _(params, layers, args, a):
    m = number(params, "area", 0, 1e13, "Only holes smaller than")
    args["min"] = m if m > 0 else 1e18
    sql = (f"SELECT (SELECT ST_Collect(ST_MakePolygon(ST_ExteriorRing(d.geom), ARRAY(SELECT ST_InteriorRingN(d.geom, i) "
           f"FROM generate_series(1, ST_NumInteriorRings(d.geom)) i WHERE ST_Area(ST_MakePolygon(ST_InteriorRingN(d.geom, i))::geography) >= %(min)s))) "
           f"FROM ST_Dump(s.geom) d) AS g, s.props AS p FROM {src('input')} s")
    return Built(sql, args, "Polygon", a["fields"], f"{a['name']} filled")


@tool("aggregate_polygons", "Aggregate polygons", "Generalization", "Polygons closer than a distance joined into larger areas.",
      IN_POLY, _p("distance", "Join gaps up to", "number", unit="m", min=0.1, default=50))
def _(params, layers, args, a):
    args["d"], args["utm"] = number(params, "distance", 0.1, 100_000, "Join gaps up to") / 2, utm_srid(a)
    sql = (f"SELECT (ST_Dump({in_utm('ST_Buffer(ST_Buffer(ST_Union(X), %(d)s, \'join=mitre\'), -%(d)s, \'join=mitre\')')})).geom AS g, "
           f"'{{}}'::jsonb AS p FROM {src('input')} s")
    return Built(sql, args, "Polygon", [], f"{a['name']} aggregated")


@tool("snap", "Snap", "Generalization", "Vertices moved onto the nearest vertices of another layer within a distance.",
      IN, _p("target", "Snap to", "layer"), _p("tolerance", "Within", "number", unit="m", min=0.001, default=1))
def _(params, layers, args, a):
    args["t"], args["utm"] = number(params, "tolerance", 0.001, 10_000, "Within"), utm_srid(a)
    sql = (f"WITH t AS (SELECT ST_Transform(ST_Collect(geom), %(utm)s) AS u FROM {src('target')} k) "
           f"SELECT {norm(in_utm('ST_Snap(X, t.u, %(t)s)'), a['geometry_type'])} AS g, s.props AS p FROM {src('input')} s, t")
    return Built(sql, args, a["geometry_type"], a["fields"], f"{a['name']} snapped")


# =====================================================================================================================
# Conversion and editing
# =====================================================================================================================
@tool("vertices", "Feature vertices to points", "Conversion", "A point at every vertex, with its order.", IN_LP)
def _(params, layers, args, a):
    sql = (f"SELECT d.geom AS g, s.props || jsonb_build_object('vertex', d.path[array_length(d.path, 1)], 'feature_id', s.id) AS p "
           f"FROM {src('input')} s, ST_DumpPoints(s.geom) d")
    return Built(sql, args, "Point", field_add(a["fields"], {"name": "vertex", "type": "integer"}, {"name": "feature_id", "type": "integer"}),
                 f"{a['name']} vertices")


@tool("endpoints", "Line end points", "Conversion", "The start and end point of each line.", IN_LINES)
def _(params, layers, args, a):
    sql = (f"SELECT e.g, s.props || jsonb_build_object('end', e.w) AS p FROM {src('input')} s, ST_Dump(s.geom) d, "
           f"LATERAL (VALUES (ST_StartPoint(d.geom), 'start'), (ST_EndPoint(d.geom), 'end')) e(g, w)")
    return Built(sql, args, "Point", field_add(a["fields"], {"name": "end", "type": "string"}), f"{a['name']} end points")


@tool("midpoints", "Line midpoints", "Conversion", "The point halfway along each line.", IN_LINES)
def _(params, layers, args, a):
    return Built(f"SELECT ST_LineInterpolatePoint(d.geom, 0.5) AS g, s.props AS p FROM {src('input')} s, ST_Dump(s.geom) d",
                 args, "Point", a["fields"], f"{a['name']} midpoints")


@tool("line_intersections", "Line intersections", "Conversion", "Points where lines of two layers (or of one layer) cross.",
      IN_LINES, _p("other", "With lines", "layer", geometry=["LineString", "Polygon"]))
def _(params, layers, args, a):
    b = layers["other"]
    bf, ren = renamed(a["fields"], b["fields"])
    args["ren"] = json.dumps(ren)
    same = b["id"] == a["id"]
    sql = (f"SELECT (ST_Dump(ST_CollectionExtract(ST_Intersection(s.geom, CASE WHEN GeometryType(o.geom) LIKE '%%POLYGON' THEN ST_Boundary(o.geom) ELSE o.geom END), 1))).geom AS g, "
           f"s.props || {renamed_props('o', 'ren')} AS p FROM {src('input')} s JOIN {src('other')} o ON ST_Intersects(s.geom, o.geom) "
           f"{'AND o.id > s.id' if same else ''}")
    return Built(sql, args, "Point", a["fields"] + bf, f"{a['name']} crossings")


@tool("split_at_vertices", "Split lines at vertices", "Conversion", "Each segment between two vertices as its own line.", IN_LP)
def _(params, layers, args, a):
    sql = (f"SELECT ST_MakeLine(q.p1, q.p2) AS g, q.props || jsonb_build_object('feature_id', q.id) AS p FROM (SELECT s.id, s.props, d.geom AS p1, "
           f"lead(d.geom) OVER (PARTITION BY s.id, d.path[1:array_length(d.path, 1) - 1] ORDER BY d.path[array_length(d.path, 1)]) AS p2 "
           f"FROM {src('input')} s, ST_DumpPoints(CASE WHEN GeometryType(s.geom) LIKE '%%POLYGON' THEN ST_Boundary(s.geom) ELSE s.geom END) d) q "
           f"WHERE q.p2 IS NOT NULL AND NOT ST_Equals(q.p1, q.p2)")
    return Built(sql, args, "LineString", field_add(a["fields"], {"name": "feature_id", "type": "integer"}), f"{a['name']} segments")


@tool("split_lines_at_points", "Split lines at points", "Conversion", "Lines cut at points of another layer that lie on (or near) them.",
      IN_LINES, _p("points", "At points", "layer", geometry=["Point"]), _p("tolerance", "Points within", "number", unit="m", min=0, default=1))
def _(params, layers, args, a):
    args["t"] = number(params, "tolerance", 0, 10_000, "Points within")
    sql = (f"SELECT (ST_Dump(COALESCE(ST_CollectionExtract(ST_Split(ST_Snap(s.geom, c.mp, 1e-9), c.mp), 2), s.geom))).geom AS g, s.props AS p "
           f"FROM {src('input')} s LEFT JOIN LATERAL (SELECT ST_Collect(ST_ClosestPoint(s.geom, pt.geom)) AS mp FROM {src('points')} pt "
           f"WHERE ST_DWithin(s.geom::geography, pt.geom::geography, %(t)s)) c ON true")
    return Built(sql, args, "LineString", a["fields"], f"{a['name']} split at points")


@tool("lines_to_polygons", "Lines to polygons", "Conversion", "Closed lines (rings) turned into polygons.", IN_LINES)
def _(params, layers, args, a):
    return Built(f"SELECT {norm('ST_BuildArea(geom)', 'Polygon')} AS g, props AS p FROM {src('input')} s", args, "Polygon", a["fields"],
                 f"{a['name']} polygons")


@tool("polygonize", "Polygonize", "Conversion", "Every area enclosed by the lines (all lines noded together).", IN_LP)
def _(params, layers, args, a):
    sql = (f"SELECT (ST_Dump(ST_Polygonize(l))).geom AS g, '{{}}'::jsonb AS p FROM (SELECT ST_Union(CASE WHEN GeometryType(geom) LIKE '%%POLYGON' "
           f"THEN ST_Boundary(geom) ELSE geom END) AS l FROM {src('input')} s) x")
    return Built(sql, args, "Polygon", [], f"{a['name']} polygonized")


@tool("points_to_line", "Points to line", "Conversion", "Points joined into lines, in order, one line per group.",
      IN_PTS, _p("order", "Order by", "field", of="input", optional=True), _p("group", "One line per", "field", of="input", optional=True))
def _(params, layers, args, a):
    o, g = field_of(a, params.get("order"), "Order by", optional=True), field_of(a, params.get("group"), "One line per", optional=True)
    args["o"], args["g"] = o or "", g or ""
    order = "props->%(o)s, id" if o else "id"
    sql = (f"SELECT ST_MakeLine(geom ORDER BY {order}) AS g, " + ("jsonb_build_object(%(g)s::text, props->%(g)s, 'points', count(*))" if g else
           "jsonb_build_object('points', count(*))") + f" AS p FROM {src('input')} s" + (" GROUP BY props->%(g)s" if g else "") + " HAVING count(*) > 1")
    fields = ([{"name": g, "type": next(f["type"] for f in a["fields"] if f["name"] == g)}] if g else []) + [{"name": "points", "type": "integer"}]
    return Built(sql, args, "LineString", fields, f"{a['name']} line")


@tool("reverse_lines", "Flip line direction", "Conversion", "Lines with their direction reversed.", IN_LINES)
def _(params, layers, args, a):
    return Built(f"SELECT ST_Reverse(geom) AS g, props AS p FROM {src('input')} s", args, "LineString", a["fields"], f"{a['name']} flipped")


@tool("offset_lines", "Offset lines", "Conversion", "Parallel copies of lines at a distance (positive to the left, negative to the right).",
      IN_LINES, _p("distance", "Offset", "number", unit="m", default=10))
def _(params, layers, args, a):
    args["d"], args["utm"] = number(params, "distance", -100_000, 100_000, "Offset"), utm_srid(a)
    sql = f"SELECT {norm(in_utm('ST_OffsetCurve(ST_LineMerge(X), %(d)s)'), 'LineString')} AS g, props AS p FROM {src('input')} s"
    return Built(sql, args, "LineString", a["fields"], f"{a['name']} offset {args['d']:g} m")


@tool("delaunay", "Delaunay triangulation", "Conversion", "Triangles joining the points (or vertices) of a layer.", IN)
def _(params, layers, args, a):
    return Built(f"SELECT (ST_Dump(ST_DelaunayTriangles(ST_Collect(geom)))).geom AS g, '{{}}'::jsonb AS p FROM {src('input')} s",
                 args, "Polygon", [], f"{a['name']} triangles")


@tool("divide_lines", "Divide lines", "Conversion", "Each line cut into a number of equal parts.", IN_LINES,
      _p("parts", "Parts", "number", min=2, default=4))
def _(params, layers, args, a):
    args["n"] = int(number(params, "parts", 2, 1000, "Parts"))
    sql = (f"SELECT ST_LineSubstring(d.geom, k::float8 / %(n)s, (k + 1)::float8 / %(n)s) AS g, s.props || jsonb_build_object('part', k + 1) AS p "
           f"FROM {src('input')} s, ST_Dump(ST_LineMerge(s.geom)) d, generate_series(0, %(n)s - 1) k")
    return Built(sql, args, "LineString", field_add(a["fields"], {"name": "part", "type": "integer"}), f"{a['name']} in {args['n']} parts")


@tool("move", "Move", "Conversion", "Features moved east and north by a distance.", IN,
      _p("dx", "East", "number", unit="m", default=0), _p("dy", "North", "number", unit="m", default=0))
def _(params, layers, args, a):
    args["dx"], args["dy"], args["utm"] = number(params, "dx", -1e7, 1e7, "East"), number(params, "dy", -1e7, 1e7, "North"), utm_srid(a)
    return Built(f"SELECT {in_utm('ST_Translate(X, %(dx)s, %(dy)s)')} AS g, props AS p FROM {src('input')} s", args, a["geometry_type"], a["fields"],
                 f"{a['name']} moved")


@tool("rotate", "Rotate", "Conversion", "Features turned by an angle (clockwise) around their own centres, or around the layer's centre.", IN,
      _p("angle", "Angle (degrees, clockwise)", "number", default=90), _p("each", "Each around its own centre", "bool", default=True))
def _(params, layers, args, a):
    args["ang"], args["utm"] = math.radians(-number(params, "angle", -360, 360, "Angle")), utm_srid(a)
    if params.get("each", True):
        expr = in_utm("ST_Rotate(X, %(ang)s, ST_Centroid(X))")
        return Built(f"SELECT {expr} AS g, props AS p FROM {src('input')} s", args, a["geometry_type"], a["fields"], f"{a['name']} rotated")
    sql = (f"WITH c AS (SELECT ST_Centroid(ST_Transform(ST_Collect(geom), %(utm)s)) AS c FROM {src('input')} k) "
           f"SELECT {in_utm('ST_Rotate(X, %(ang)s, c.c)')} AS g, s.props AS p FROM {src('input')} s, c")
    return Built(sql, args, a["geometry_type"], a["fields"], f"{a['name']} rotated")


@tool("scale", "Scale", "Conversion", "Features enlarged or shrunk around their own centres.", IN_LP,
      _p("factor", "Factor (2 = twice as large)", "number", min=0.001, default=2))
def _(params, layers, args, a):
    args["f"], args["utm"] = number(params, "factor", 0.001, 1000, "Factor"), utm_srid(a)
    expr = in_utm("ST_Translate(ST_Scale(ST_Translate(X, -ST_X(ST_Centroid(X)), -ST_Y(ST_Centroid(X))), %(f)s, %(f)s), ST_X(ST_Centroid(X)), ST_Y(ST_Centroid(X)))")
    return Built(f"SELECT {norm(expr, a['geometry_type'])} AS g, props AS p FROM {src('input')} s", args, a["geometry_type"], a["fields"],
                 f"{a['name']} scaled")


# =====================================================================================================================
# Topology and quality
# =====================================================================================================================
@tool("repair_geometry", "Repair geometry", "Topology", "Invalid shapes (self-crossing polygons, bow-ties) made valid.", IN)
def _(params, layers, args, a):
    return Built(f"SELECT {norm('ST_MakeValid(geom)', a['geometry_type'])} AS g, props AS p FROM {src('input')} s", args, a["geometry_type"],
                 a["fields"], f"{a['name']} repaired")


@tool("check_geometry", "Check geometry", "Topology", "A point at each problem: invalid shapes, empty or tiny parts, repeated vertices.", IN)
def _(params, layers, args, a):
    sql = (f"SELECT COALESCE(v.location, ST_PointOnSurface(s.geom)) AS g, jsonb_build_object('feature_id', s.id, 'problem', v.reason) AS p "
           f"FROM {src('input')} s, ST_IsValidDetail(s.geom) v WHERE NOT v.valid "
           f"UNION ALL SELECT ST_PointOnSurface(s.geom), jsonb_build_object('feature_id', s.id, 'problem', 'Repeated vertices') "
           f"FROM {src('input')} s WHERE ST_NPoints(ST_RemoveRepeatedPoints(s.geom)) < ST_NPoints(s.geom)")
    return Built(sql, args, "Point", [{"name": "feature_id", "type": "integer"}, {"name": "problem", "type": "string"}], f"{a['name']} problems")


@tool("find_overlaps", "Find overlaps", "Topology", "Where polygons of the same layer overlap, with both ids and the overlap's area.", IN_POLY)
def _(params, layers, args, a):
    sql = (f"WITH s AS {src('input')} SELECT {norm('ST_Intersection(x.geom, y.geom)', 'Polygon')} AS g, jsonb_build_object('id_1', x.id, "
           f"'id_2', y.id, 'area_m2', round(ST_Area(ST_Intersection(x.geom, y.geom)::geography)::numeric, 2)) AS p FROM s x JOIN s y "
           f"ON x.id < y.id AND ST_Intersects(x.geom, y.geom) AND NOT ST_Touches(x.geom, y.geom)")
    return Built(sql, args, "Polygon", [{"name": "id_1", "type": "integer"}, {"name": "id_2", "type": "integer"}, {"name": "area_m2", "type": "number"}],
                 f"{a['name']} overlaps")


@tool("find_gaps", "Find gaps", "Topology", "Holes between neighbouring polygons (empty spaces enclosed by the layer).",
      IN_POLY, _p("area", "Only gaps smaller than (0 = all)", "number", unit="m²", min=0, default=0))
def _(params, layers, args, a):
    m = number(params, "area", 0, 1e13, "Only gaps smaller than")
    args["max"] = m if m > 0 else 1e18
    sql = (f"SELECT q.g, jsonb_build_object('area_m2', round(ST_Area(q.g::geography)::numeric, 2)) AS p FROM (SELECT ST_MakePolygon(ST_InteriorRingN(d.geom, i)) AS g "
           f"FROM (SELECT (ST_Dump(ST_Union(geom))).geom FROM {src('input')} s) d, generate_series(1, ST_NumInteriorRings(d.geom)) i) q "
           f"WHERE ST_Area(q.g::geography) < %(max)s")
    return Built(sql, args, "Polygon", [{"name": "area_m2", "type": "number"}], f"{a['name']} gaps")


@tool("find_identical", "Find identical", "Topology", "Features with exactly the same shape, grouped, with how many share it.", IN)
def _(params, layers, args, a):
    sql = (f"SELECT q.geom AS g, q.props || jsonb_build_object('duplicate_group', q.grp, 'copies', q.n) AS p FROM (SELECT s.geom, s.props, "
           f"dense_rank() OVER (ORDER BY ST_AsBinary(ST_Normalize(s.geom))) AS grp, count(*) OVER (PARTITION BY ST_AsBinary(ST_Normalize(s.geom))) AS n "
           f"FROM {src('input')} s) q WHERE q.n > 1")
    return Built(sql, args, a["geometry_type"], field_add(a["fields"], {"name": "duplicate_group", "type": "integer"}, {"name": "copies", "type": "integer"}),
                 f"{a['name']} identical")


@tool("delete_duplicates", "Delete identical", "Topology", "One of each group of features with exactly the same shape.", IN)
def _(params, layers, args, a):
    sql = f"SELECT DISTINCT ON (ST_AsBinary(ST_Normalize(geom))) geom AS g, props AS p FROM {src('input')} s ORDER BY ST_AsBinary(ST_Normalize(geom)), id"
    return Built(sql, args, a["geometry_type"], a["fields"], f"{a['name']} without duplicates")


@tool("find_slivers", "Find slivers", "Topology", "Long thin polygons (by thinness: 4πA/P², where 1 is a circle).",
      IN_POLY, _p("thinness", "Thinner than", "number", min=0.001, default=0.1))
def _(params, layers, args, a):
    args["t"] = number(params, "thinness", 0.0001, 1, "Thinner than")
    sql = (f"SELECT geom AS g, props || jsonb_build_object('thinness', round(t::numeric, 4)) AS p FROM (SELECT geom, props, "
           f"4 * pi() * ST_Area(geom::geography) / NULLIF(ST_Perimeter(geom::geography) ^ 2, 0) AS t FROM {src('input')} s) q WHERE t < %(t)s")
    return Built(sql, args, "Polygon", field_add(a["fields"], {"name": "thinness", "type": "number"}), f"{a['name']} slivers")


# =====================================================================================================================
# Sampling
# =====================================================================================================================
@tool("random_points", "Random points in polygons", "Sampling", "A number of random points inside each polygon.",
      IN_POLY, _p("count", "Points per polygon", "number", min=1, default=10), _p("seed", "Seed (same seed, same points)", "number", default=1))
def _(params, layers, args, a):
    args["n"], args["seed"] = int(number(params, "count", 1, 10_000, "Points per polygon")), int(number(params, "seed", 0, 1e9, "Seed"))
    sql = (f"SELECT (ST_Dump(ST_GeneratePoints(geom, %(n)s, %(seed)s))).geom AS g, props || jsonb_build_object('polygon_id', id) AS p "
           f"FROM {src('input')} s")
    return Built(sql, args, "Point", field_add(a["fields"], {"name": "polygon_id", "type": "integer"}), f"{a['name']} random points")


@tool("random_points_extent", "Random points in extent", "Sampling", "Random points anywhere in the layer's bounding box.",
      IN, _p("count", "Points", "number", min=1, default=100), _p("seed", "Seed (same seed, same points)", "number", default=1))
def _(params, layers, args, a):
    args["n"], args["seed"] = int(number(params, "count", 1, 100_000, "Points")), int(number(params, "seed", 0, 1e9, "Seed"))
    sql = (f"SELECT (ST_Dump(ST_GeneratePoints(ST_Envelope(ST_Collect(geom)), %(n)s, %(seed)s))).geom AS g, '{{}}'::jsonb AS p FROM {src('input')} s")
    return Built(sql, args, "Point", [], f"{a['name']} random points")


@tool("regular_points", "Regular points", "Sampling", "Points on a regular grid (true metres) inside the polygons.",
      IN_POLY, _p("spacing", "Spacing", "number", unit="m", min=1, default=100))
def _(params, layers, args, a):
    args["sp"] = number(params, "spacing", 1, 1_000_000, "Spacing")
    _cells_ok(a, args["sp"])
    sql = (f"WITH s AS {src('input')}, e AS (SELECT ST_Transform(ST_Envelope(ST_Collect(geom)), 3857) AS env, "
           f"cos(radians(ST_Y(ST_Centroid(ST_Envelope(ST_Collect(geom)))))) AS k FROM s) "
           f"SELECT ST_Transform(ST_Centroid(c.geom), 4326) AS g, '{{}}'::jsonb AS p FROM e, ST_SquareGrid(%(sp)s / e.k, e.env) c "
           f"WHERE EXISTS (SELECT 1 FROM s WHERE ST_Contains(s.geom, ST_Transform(ST_Centroid(c.geom), 4326)))")
    return Built(sql, args, "Point", [], f"{a['name']} points every {args['sp']:g} m")


def _cells_ok(a: dict, size: float, most: int = 200_000) -> None:
    if a.get("bbox"):
        x1, y1, x2, y2 = a["bbox"]
        w = (x2 - x1) * 111_320 * math.cos(math.radians((y1 + y2) / 2))
        n = (w + size) * ((y2 - y1) * 111_320 + size) / size ** 2
        if n > most:
            raise Problem(422, f"That would make about {n:,.0f} cells; the most is {most:,}. Use a larger size.", code="validation")


@tool("bin_points", "Aggregate into bins", "Sampling", "Features counted in square or hexagon bins (only bins with features), with an optional sum.",
      IN, _p("size", "Bin size", "number", unit="m", min=1, default=500),
      _p("shape", "Bins", "choice", default="hexagon", choices=[["hexagon", "Hexagons"], ["square", "Squares"]]),
      _p("sum_field", "Add up", "field", of="input", numeric=True, optional=True))
def _(params, layers, args, a):
    args["size"] = number(params, "size", 1, 1_000_000, "Bin size")
    _cells_ok(a, args["size"])
    fn = "ST_SquareGrid" if params.get("shape") == "square" else "ST_HexagonGrid"
    sf = field_of(a, params.get("sum_field"), "Add up", numeric=True, optional=True)
    args["sf"] = sf or ""
    sum_ = f", 'sum_' || %(sf)s, COALESCE(sum({num('sf', 's.')}), 0)" if sf else ""
    sql = (f"WITH s AS {src('input')}, e AS (SELECT ST_Transform(ST_Envelope(ST_Collect(geom)), 3857) AS env, "
           f"cos(radians(ST_Y(ST_Centroid(ST_Envelope(ST_Collect(geom)))))) AS k FROM s) "
           f"SELECT ST_Transform(c.geom, 4326) AS g, jsonb_build_object('count', count(s.id){sum_}) AS p FROM e, {fn}(%(size)s / e.k, e.env) c "
           f"JOIN s ON ST_Intersects(s.geom, ST_Transform(c.geom, 4326)) GROUP BY c.geom")
    return Built(sql, args, "Polygon", [{"name": "count", "type": "integer"}] + ([{"name": f"sum_{sf}", "type": "number"}] if sf else []),
                 f"{a['name']} in {args['size']:g} m bins")


# =====================================================================================================================
# Spatial statistics (computed with numpy on true metres, in the layer's UTM zone)
# =====================================================================================================================
def _point_on(g) -> tuple[float, float]:
    p = g if g.geom_type == "Point" else g.representative_point() if g.geom_type in ("Polygon", "MultiPolygon") else g.interpolate(0.5, normalized=True)
    return p.x, p.y


async def _xy(conn, key, args, a, field=None, srid=None):
    """The features' representative points in UTM metres, their ids, props, and (optionally) a numeric field."""
    rows = await rows_of(conn, key, args)
    if not rows:
        raise Problem(422, f"{a['name']} has no features to analyse.", code="validation")
    t, back = to_utm(srid or utm_srid(a))
    ll = np.array([_point_on(shapely.from_wkb(bytes(r["wkb"]))) for r in rows])
    x, y = t.transform(ll[:, 0], ll[:, 1])
    vals = None
    if field:
        vals = np.array([r["props"].get(field) if isinstance(r["props"].get(field), (int, float)) and not isinstance(r["props"].get(field), bool)
                         else np.nan for r in rows], dtype=float)
    return rows, np.column_stack([x, y]), vals, back


def _pt(back, x, y):
    lng, lat = back.transform(x, y)
    return shapely.Point(lng, lat)


def _ring(back, xs, ys):
    lng, lat = back.transform(np.asarray(xs), np.asarray(ys))
    return shapely.Polygon(np.column_stack([lng, lat]))


@tool("mean_center", "Mean centre", "Statistics", "The average location of the features (optionally weighted, and per group).",
      IN, _p("weight", "Weight by", "field", of="input", numeric=True, optional=True), _p("group", "One per", "field", of="input", optional=True))
def _(params, layers, args, a):
    w = field_of(a, params.get("weight"), "Weight by", numeric=True, optional=True)
    g = field_of(a, params.get("group"), "One per", optional=True)

    async def compute(conn):
        rows, xy, vals, back = await _xy(conn, "input", args, a, w)
        wt = np.nan_to_num(vals, nan=0.0) if w else np.ones(len(rows))
        groups = {}
        for i, r in enumerate(rows):
            groups.setdefault(r["props"].get(g) if g else None, []).append(i)
        out = []
        for key, idx in groups.items():
            ww = wt[idx]
            if ww.sum() <= 0:
                continue
            cx, cy = (xy[idx] * ww[:, None]).sum(0) / ww.sum()
            out.append((_pt(back, cx, cy), {**({g: key} if g else {}), "count": len(idx)}))
        return out
    fields = ([{"name": g, "type": "string"}] if g else []) + [{"name": "count", "type": "integer"}]
    return Built(None, args, "Point", fields, f"{a['name']} mean centre", compute)


@tool("median_center", "Median centre", "Statistics", "The location that is the least total distance from all features.", IN)
def _(params, layers, args, a):
    args["utm"] = utm_srid(a)
    sql = (f"SELECT ST_Transform(ST_GeometricMedian(ST_Transform(ST_Collect(ST_PointOnSurface(geom)), %(utm)s)), 4326) AS g, "
           f"jsonb_build_object('count', count(*)) AS p FROM {src('input')} s")
    return Built(sql, args, "Point", [{"name": "count", "type": "integer"}], f"{a['name']} median centre")


@tool("central_feature", "Central feature", "Statistics", "The feature with the smallest total distance to all the others.", IN)
def _(params, layers, args, a):
    async def compute(conn):
        rows, xy, _, back = await _xy(conn, "input", args, a)
        if len(rows) > 20_000:
            raise Problem(422, "Central feature works on up to 20,000 features: use a selection.", code="validation")
        best, total = 0, math.inf
        for i in range(0, len(xy), 2000):
            d = np.sqrt(((xy[i:i + 2000, None, :] - xy[None, :, :]) ** 2).sum(-1)).sum(1)
            j = int(np.argmin(d))
            if d[j] < total:
                best, total = i + j, float(d[j])
        r = rows[best]
        return [(shapely.from_wkb(bytes(r["wkb"])), {**r["props"], "total_distance_m": round(total, 2)})]
    return Built(None, args, a["geometry_type"], field_add(a["fields"], {"name": "total_distance_m", "type": "number"}), f"{a['name']} central feature", compute)


@tool("standard_distance", "Standard distance", "Statistics", "A circle showing how spread out the features are around their mean centre.",
      IN, _p("deviations", "Standard deviations", "choice", default="1", choices=[["1", "1"], ["2", "2"], ["3", "3"]]))
def _(params, layers, args, a):
    k = float(params.get("deviations") or 1)

    async def compute(conn):
        rows, xy, _, back = await _xy(conn, "input", args, a)
        c = xy.mean(0)
        sd = math.sqrt(((xy - c) ** 2).sum(1).mean()) * k
        ang = np.linspace(0, 2 * math.pi, 73)
        return [(_ring(back, c[0] + sd * np.cos(ang), c[1] + sd * np.sin(ang)), {"radius_m": round(sd, 2), "count": len(rows), "deviations": k})]
    return Built(None, args, "Polygon", [{"name": "radius_m", "type": "number"}, {"name": "count", "type": "integer"}, {"name": "deviations", "type": "number"}],
                 f"{a['name']} standard distance", compute)


@tool("deviational_ellipse", "Directional distribution", "Statistics", "The standard deviational ellipse: the spread and the direction of the features.",
      IN, _p("deviations", "Standard deviations", "choice", default="1", choices=[["1", "1"], ["2", "2"], ["3", "3"]]))
def _(params, layers, args, a):
    k = float(params.get("deviations") or 1)

    async def compute(conn):
        rows, xy, _, back = await _xy(conn, "input", args, a)
        if len(rows) < 3:
            raise Problem(422, "Directional distribution needs at least 3 features.", code="validation")
        c = xy.mean(0)
        dx, dy = (xy - c).T
        cov = np.cov(np.vstack([dx, dy]), bias=True)
        vals, vecs = np.linalg.eigh(cov)
        order = vals.argsort()[::-1]
        vals, vecs = vals[order], vecs[:, order]
        sx, sy = math.sqrt(max(vals[0], 0)) * k, math.sqrt(max(vals[1], 0)) * k
        theta = math.atan2(vecs[1, 0], vecs[0, 0])
        t = np.linspace(0, 2 * math.pi, 73)
        ex = c[0] + sx * np.cos(t) * math.cos(theta) - sy * np.sin(t) * math.sin(theta)
        ey = c[1] + sx * np.cos(t) * math.sin(theta) + sy * np.sin(t) * math.cos(theta)
        bearing = (90 - math.degrees(theta)) % 180
        return [(_ring(back, ex, ey), {"long_axis_m": round(sx, 2), "short_axis_m": round(sy, 2), "bearing_deg": round(bearing, 2), "count": len(rows)})]
    return Built(None, args, "Polygon", [{"name": "long_axis_m", "type": "number"}, {"name": "short_axis_m", "type": "number"},
                                         {"name": "bearing_deg", "type": "number"}, {"name": "count", "type": "integer"}],
                 f"{a['name']} ellipse", compute)


@tool("kmeans", "K-means clustering", "Statistics", "Features split into a number of clusters by location.",
      IN, _p("k", "Clusters", "number", min=2, default=5))
def _(params, layers, args, a):
    args["k"] = int(number(params, "k", 2, 500, "Clusters"))
    sql = f"SELECT geom AS g, props || jsonb_build_object('cluster', ST_ClusterKMeans(geom, %(k)s) OVER () + 1) AS p FROM {src('input')} s"
    return Built(sql, args, a["geometry_type"], field_add(a["fields"], {"name": "cluster", "type": "integer"}), f"{a['name']} {args['k']} clusters")


@tool("dbscan", "Density-based clustering", "Statistics", "Clusters of features close together (DBSCAN); scattered ones are marked as noise (0).",
      IN, _p("distance", "Within", "number", unit="m", min=0.01, default=200), _p("min_points", "At least", "number", min=2, default=4))
def _(params, layers, args, a):
    args["d"], args["m"], args["utm"] = number(params, "distance", 0.01, 1e6, "Within"), int(number(params, "min_points", 2, 10_000, "At least")), utm_srid(a)
    sql = (f"SELECT geom AS g, props || jsonb_build_object('cluster', COALESCE(ST_ClusterDBSCAN(ST_Transform(geom, %(utm)s), %(d)s, %(m)s) OVER () + 1, 0)) AS p "
           f"FROM {src('input')} s")
    return Built(sql, args, a["geometry_type"], field_add(a["fields"], {"name": "cluster", "type": "integer"}), f"{a['name']} clusters")


@tool("nearest_neighbor_index", "Average nearest neighbour", "Statistics",
      "Whether the features are clustered, random or dispersed (nearest-neighbour ratio, z-score and p-value).", IN)
def _(params, layers, args, a):
    async def compute(conn):
        rows, xy, _, back = await _xy(conn, "input", args, a)
        n = len(rows)
        if n < 3:
            raise Problem(422, "Average nearest neighbour needs at least 3 features.", code="validation")
        nn = np.empty(n)
        for i in range(0, n, 2000):
            d = np.sqrt(((xy[i:i + 2000, None, :] - xy[None, :, :]) ** 2).sum(-1))
            d[np.arange(d.shape[0]), np.arange(i, i + d.shape[0])] = np.inf
            nn[i:i + d.shape[0]] = d.min(1)
        area = float((xy[:, 0].max() - xy[:, 0].min()) * (xy[:, 1].max() - xy[:, 1].min())) or 1.0
        do, de = nn.mean(), 0.5 / math.sqrt(n / area)
        se = 0.26136 / math.sqrt(n * n / area)
        z = (do - de) / se
        ratio = do / de
        pattern = "Clustered" if z < -1.96 else "Dispersed" if z > 1.96 else "Random"
        c = xy.mean(0)
        return [(_pt(back, *c), {"observed_m": round(do, 2), "expected_m": round(de, 2), "ratio": round(ratio, 4), "z_score": round(z, 3),
                                 "p_value": round(pval(z), 5), "pattern": pattern, "count": n})]
    fields = [{"name": k, "type": t} for k, t in (("observed_m", "number"), ("expected_m", "number"), ("ratio", "number"), ("z_score", "number"),
                                                    ("p_value", "number"), ("pattern", "string"), ("count", "integer"))]
    return Built(None, args, "Point", fields, f"{a['name']} nearest neighbour", compute)


def _weights(xy: np.ndarray, mode: str, d: float, k: int, self_in=False) -> list[np.ndarray]:
    """Neighbours of each feature: within a distance band, or the k nearest. Returns index arrays."""
    n = len(xy)
    out = []
    for i in range(0, n, 1000):
        dist = np.sqrt(((xy[i:i + 1000, None, :] - xy[None, :, :]) ** 2).sum(-1))
        for r in range(dist.shape[0]):
            row = dist[r]
            me = i + r
            if mode == "knn":
                row = row.copy()
                row[me] = np.inf
                idx = np.argpartition(row, min(k, n - 2))[:k]
            else:
                idx = np.nonzero(row <= d)[0]
                idx = idx[idx != me]
            if self_in:
                idx = np.append(idx, me)
            out.append(idx)
    return out


WEIGHTS = [_p("neighbours", "Neighbours", "choice", default="distance", choices=[["distance", "Within a distance"], ["knn", "The nearest"]]),
           _p("distance", "Distance band", "number", unit="m", min=0.01, default=1000, when={"neighbours": "distance"}),
           _p("k", "How many nearest", "number", min=1, default=8, when={"neighbours": "knn"})]


def _wparams(params):
    mode = params.get("neighbours") or "distance"
    return mode, (number(params, "distance", 0.01, 1e7, "Distance band") if mode == "distance" else 0), (int(number(params, "k", 1, 100, "How many nearest")) if mode == "knn" else 0)


async def _field_xy(conn, args, a, field):
    rows, xy, vals, back = await _xy(conn, "input", args, a, field)
    ok = ~np.isnan(vals)
    if ok.sum() < 4:
        raise Problem(422, f"{field} needs numbers on at least 4 features.", code="validation")
    return [r for r, o in zip(rows, ok) if o], xy[ok], vals[ok], back


@tool("morans_i", "Spatial autocorrelation (Global Moran's I)", "Statistics",
      "Whether similar values sit near each other across the whole layer (Moran's I, z-score, p-value).",
      IN, _p("field", "Field", "field", of="input", numeric=True), *WEIGHTS)
def _(params, layers, args, a):
    f = field_of(a, params.get("field"), "Field", numeric=True)
    mode, d, k = _wparams(params)

    async def compute(conn):
        rows, xy, x, back = await _field_xy(conn, args, a, f)
        n = len(x)
        nb = _weights(xy, mode, d, k)
        if any(len(i) == 0 for i in nb):
            raise Problem(422, f"{sum(len(i) == 0 for i in nb)} feature(s) have no neighbours within {d:g} m: use a larger distance band.", code="validation")
        z = x - x.mean()
        # row-standardised weights
        w = [np.full(len(i), 1.0 / len(i)) for i in nb]
        s0 = float(n)
        num_ = sum(z[i] * (w[i] * z[nb[i]]).sum() for i in range(n))
        moran = (n / s0) * num_ / (z ** 2).sum()
        e = -1.0 / (n - 1)
        # variance under randomisation
        wm = {}
        for i in range(n):
            for j, v in zip(nb[i], w[i]):
                wm[(i, int(j))] = v
        s1 = 0.5 * sum((v + wm.get((j, i), 0.0)) ** 2 for (i, j), v in wm.items()) + 0.5 * sum(v ** 2 for (i, j), v in wm.items() if (j, i) not in wm)
        col = np.zeros(n)
        for (i, j), v in wm.items():
            col[j] += v
        s2 = float(((1.0 + col) ** 2).sum())
        b2 = n * (z ** 4).sum() / ((z ** 2).sum() ** 2)
        var = ((n * ((n * n - 3 * n + 3) * s1 - n * s2 + 3 * s0 * s0) - b2 * ((n * n - n) * s1 - 2 * n * s2 + 6 * s0 * s0))
               / ((n - 1) * (n - 2) * (n - 3) * s0 * s0)) - e * e
        zs = (moran - e) / math.sqrt(var) if var > 0 else 0.0
        pattern = "Clustered" if zs > 1.96 else "Dispersed" if zs < -1.96 else "Random"
        c = xy.mean(0)
        return [(_pt(back, *c), {"morans_i": round(moran, 6), "expected": round(e, 6), "variance": round(var, 8), "z_score": round(zs, 3),
                                 "p_value": round(pval(zs), 5), "pattern": pattern, "count": n})]
    fields = [{"name": k_, "type": t} for k_, t in (("morans_i", "number"), ("expected", "number"), ("variance", "number"), ("z_score", "number"),
                                                     ("p_value", "number"), ("pattern", "string"), ("count", "integer"))]
    return Built(None, args, "Point", fields, f"{a['name']} Moran's I of {f}", compute)


@tool("local_morans_i", "Cluster and outlier analysis (Local Moran's I)", "Statistics",
      "For each feature: part of a high or low cluster (HH, LL), an outlier (HL, LH), or not significant.",
      IN, _p("field", "Field", "field", of="input", numeric=True), *WEIGHTS)
def _(params, layers, args, a):
    f = field_of(a, params.get("field"), "Field", numeric=True)
    mode, d, k = _wparams(params)

    async def compute(conn):
        rows, xy, x, back = await _field_xy(conn, args, a, f)
        n = len(x)
        nb = _weights(xy, mode, d, k)
        z = x - x.mean()
        m2 = (z ** 2).sum() / n
        b2 = ((z ** 4).sum() / n) / (m2 ** 2)
        out = []
        for i, r in enumerate(rows):
            if not len(nb[i]):
                out.append((shapely.from_wkb(bytes(r["wkb"])), {**r["props"], "lisa_i": None, "lisa_z": None, "lisa_p": None, "cluster": "No neighbours"}))
                continue
            wi = np.full(len(nb[i]), 1.0 / len(nb[i]))
            ii = z[i] / m2 * (wi * z[nb[i]]).sum()
            e = -wi.sum() / (n - 1)
            w2 = (wi ** 2).sum()
            wkh = wi.sum() ** 2 - w2
            var = w2 * (n - b2) / (n - 1) + wkh * (2 * b2 - n) / ((n - 1) * (n - 2)) - e * e
            zs = (ii - e) / math.sqrt(var) if var > 0 else 0.0
            p = pval(zs)
            lag = (wi * z[nb[i]]).sum()
            kind = "Not significant" if p >= 0.05 else ("HH" if z[i] > 0 and lag > 0 else "LL" if z[i] < 0 and lag < 0 else "HL" if z[i] > 0 else "LH")
            out.append((shapely.from_wkb(bytes(r["wkb"])), {**r["props"], "lisa_i": round(ii, 6), "lisa_z": round(zs, 3), "lisa_p": round(p, 5), "cluster": kind}))
        return out
    return Built(None, args, a["geometry_type"], field_add(a["fields"], {"name": "lisa_i", "type": "number"}, {"name": "lisa_z", "type": "number"},
                                                           {"name": "lisa_p", "type": "number"}, {"name": "cluster", "type": "string"}),
                 f"{a['name']} clusters of {f}", compute)


@tool("hotspots", "Hot spot analysis (Getis-Ord Gi*)", "Statistics",
      "Where high values (hot spots) and low values (cold spots) cluster, with 90, 95 and 99 % confidence.",
      IN, _p("field", "Field", "field", of="input", numeric=True), *WEIGHTS)
def _(params, layers, args, a):
    f = field_of(a, params.get("field"), "Field", numeric=True)
    mode, d, k = _wparams(params)

    async def compute(conn):
        rows, xy, x, back = await _field_xy(conn, args, a, f)
        n = len(x)
        nb = _weights(xy, mode, d, k, self_in=True)
        xbar, s = x.mean(), math.sqrt((x ** 2).mean() - x.mean() ** 2)
        out = []
        for i, r in enumerate(rows):
            wsum = float(len(nb[i]))
            denom = s * math.sqrt((n * wsum - wsum ** 2) / (n - 1)) if n > 1 else 0
            zs = (x[nb[i]].sum() - xbar * wsum) / denom if denom > 0 else 0.0
            p = pval(zs)
            conf = 99 if p < 0.01 else 95 if p < 0.05 else 90 if p < 0.10 else 0
            kind = "Not significant" if not conf else f"{'Hot' if zs > 0 else 'Cold'} spot ({conf} %)"
            out.append((shapely.from_wkb(bytes(r["wkb"])), {**r["props"], "gi_z": round(zs, 3), "gi_p": round(p, 5), "gi_bin": (conf // 1 if zs > 0 else -conf) // 1 if conf else 0,
                                                            "hotspot": kind}))
        return out
    return Built(None, args, a["geometry_type"], field_add(a["fields"], {"name": "gi_z", "type": "number"}, {"name": "gi_p", "type": "number"},
                                                           {"name": "gi_bin", "type": "integer"}, {"name": "hotspot", "type": "string"}),
                 f"{a['name']} hot spots of {f}", compute)


@tool("summary_statistics", "Summary statistics", "Statistics", "Count, sum, mean, minimum, maximum and standard deviation of a field, overall or per group.",
      IN, _p("field", "Field", "field", of="input", numeric=True), _p("group", "Per", "field", of="input", optional=True))
def _(params, layers, args, a):
    args["f"] = field_of(a, params.get("field"), "Field", numeric=True)
    g = field_of(a, params.get("group"), "Per", optional=True)
    args["g"] = g or ""
    v = num("f")
    stats = (f"jsonb_build_object('count', count(*), 'sum', sum({v}), 'mean', round(avg({v})::numeric, 6), 'min', min({v}), 'max', max({v}), "
             f"'std', round(stddev_pop({v})::numeric, 6))")
    sql = (f"SELECT {norm('ST_Union(geom)', a['geometry_type'])} AS g, " + ("jsonb_build_object(%(g)s::text, props->%(g)s) || " if g else "") +
           f"{stats} AS p FROM {src('input')} s" + (" GROUP BY props->%(g)s" if g else ""))
    fields = ([{"name": g, "type": next(x["type"] for x in a["fields"] if x["name"] == g)}] if g else []) + \
        [{"name": n_, "type": "integer" if n_ == "count" else "number"} for n_ in ("count", "sum", "mean", "min", "max", "std")]
    return Built(sql, args, a["geometry_type"], fields, f"{a['name']} statistics of {args['f']}")


# =====================================================================================================================
# Network analysis (networkx on the lines of a layer, lengths on the ellipsoid)
# =====================================================================================================================
NET = _p("network", "Road or path network (lines)", "layer", geometry=["LineString"])


async def _graph(conn, args):
    import networkx as nx
    cur = await conn.execute(f"SELECT ST_AsBinary((ST_Dump(ST_Node(ST_Collect(geom)))).geom) AS wkb FROM {src('network')} s", args)
    g = nx.Graph()
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    for r in await cur.fetchall():
        line = shapely.from_wkb(bytes(r["wkb"]))
        cs = list(line.coords)
        if len(cs) < 2:
            continue
        a_, b_ = tuple(round(v, 9) for v in cs[0]), tuple(round(v, 9) for v in cs[-1])
        length = geod.line_length([c[0] for c in cs], [c[1] for c in cs])
        if not g.has_edge(a_, b_) or g[a_][b_]["w"] > length:
            g.add_edge(a_, b_, w=length, coords=cs)
    if not g.number_of_nodes():
        raise Problem(422, "The network layer has no lines.", code="validation")
    nodes = np.array(list(g.nodes))
    return g, nodes


def _nearest_node(nodes, lng, lat):
    d = (nodes[:, 0] - lng) ** 2 * math.cos(math.radians(lat)) ** 2 + (nodes[:, 1] - lat) ** 2
    i = int(np.argmin(d))
    return tuple(nodes[i]), math.sqrt(d[i]) * 111_320


def _path_line(g, path, start=None, end=None):
    """The line along the path's edges, from `start` (a point off the network, joined to it straight) to `end`."""
    cs = [tuple(start)] if start is not None else []
    cs.append(tuple(float(v) for v in path[0]))
    for u, v in zip(path, path[1:]):
        seg = g[u][v]["coords"]
        seg = seg if tuple(round(x, 9) for x in seg[0]) == u else seg[::-1]
        cs.extend(tuple(c) for c in seg)
    if end is not None:
        cs.append(tuple(end))
    cs = [c for i, c in enumerate(cs) if i == 0 or c != cs[i - 1]]
    return shapely.LineString(cs) if len(cs) > 1 else None


def _access(p, node) -> float:
    """Metres on the ellipsoid from a point to the network node where it joins the network."""
    from pyproj import Geod
    return Geod(ellps="WGS84").inv(p[0], p[1], float(node[0]), float(node[1]))[2]


async def _points(conn, key, args):
    rows = await rows_of(conn, key, args)
    return [(r, _point_on(shapely.from_wkb(bytes(r["wkb"])))) for r in rows]


@tool("route", "Route between stops", "Network", "The shortest route along the network visiting the stops in order.",
      NET, _p("stops", "Stops (points)", "layer", geometry=["Point"]), _p("order", "Visit in the order of", "field", of="stops", optional=True))
def _(params, layers, args, a):
    import networkx as nx
    st = layers["stops"]
    o = field_of(st, params.get("order"), "Visit in the order of", optional=True)

    async def compute(conn):
        g, nodes = await _graph(conn, args)
        pts = await _points(conn, "stops", args)
        if o:
            pts.sort(key=lambda t: (t[0]["props"].get(o) is None, t[0]["props"].get(o)))
        if len(pts) < 2:
            raise Problem(422, "A route needs at least two stops.", code="validation")
        path, total = [], 0.0
        for (r1, p1), (r2, p2) in zip(pts, pts[1:]):
            n1, _ = _nearest_node(nodes, *p1)
            n2, _ = _nearest_node(nodes, *p2)
            try:
                seg = nx.shortest_path(g, n1, n2, weight="w")
            except nx.NetworkXNoPath:
                raise Problem(422, f"There is no way along the network between stops {r1['id']} and {r2['id']}.", code="no_path") from None
            total += nx.path_weight(g, seg, "w")
            path += seg if not path else seg[1:]
        first, last = pts[0][1], pts[-1][1]
        line = _path_line(g, path, first, last)
        reach = _access(first, path[0]) + _access(last, path[-1])
        return [(line, {"stops": len(pts), "network_m": round(total, 2), "length_m": round(total + reach, 2)})] if line else []
    return Built(None, args, "LineString", [{"name": "stops", "type": "integer"}, {"name": "network_m", "type": "number"},
                                            {"name": "length_m", "type": "number"}], f"Route through {st['name']}", compute)


@tool("closest_facility", "Closest facility", "Network", "For each incident, the route along the network to its nearest facility.",
      NET, _p("incidents", "Incidents (points)", "layer", geometry=["Point"]), _p("facilities", "Facilities (points)", "layer", geometry=["Point"]))
def _(params, layers, args, a):
    import networkx as nx
    fac = layers["facilities"]
    ff, ren = renamed([{"name": n_} for n_ in ("incident_id", "facility_id", "network_m", "length_m")], fac["fields"])

    async def compute(conn):
        g, nodes = await _graph(conn, args)
        fpts = await _points(conn, "facilities", args)
        fnode = {}
        for r, p in fpts:
            fnode.setdefault(_nearest_node(nodes, *p)[0], (r, p))
        dist, paths = nx.multi_source_dijkstra(g, set(fnode), weight="w")
        out = []
        for r, p in await _points(conn, "incidents", args):
            n, _ = _nearest_node(nodes, *p)
            if n not in paths:
                continue
            path = paths[n][::-1]
            fr, fp = fnode[path[-1]]
            line = _path_line(g, path, p, fp)
            if line:
                total = dist[n] + _access(p, path[0]) + _access(fp, path[-1])
                out.append((line, {"incident_id": r["id"], "facility_id": fr["id"], "network_m": round(dist[n], 2), "length_m": round(total, 2),
                                   **{ren.get(k_, k_): v for k_, v in fr["props"].items()}}))
        return out
    return Built(None, args, "LineString", [{"name": "incident_id", "type": "integer"}, {"name": "facility_id", "type": "integer"},
                                            {"name": "network_m", "type": "number"}, {"name": "length_m", "type": "number"}] + ff, f"Closest {fac['name']}", compute)


def _reach(g, nodes, pts, limit):
    import networkx as nx
    out = []
    for r, p in pts:
        n, _ = _nearest_node(nodes, *p)
        dist = nx.single_source_dijkstra_path_length(g, n, cutoff=limit, weight="w")
        out.append((r, dist))
    return out


@tool("service_area", "Service area (lines)", "Network", "The parts of the network reachable from each facility within distances.",
      NET, _p("facilities", "Facilities (points)", "layer", geometry=["Point"]),
      _p("distances", "Distances (metres, separated by commas)", "text", default="500, 1000"))
def _(params, layers, args, a):
    ds = numbers(params, "distances", "Distances", 1, 1e7, 5)

    async def compute(conn):
        g, nodes = await _graph(conn, args)
        out = []
        for r, dist in _reach(g, nodes, await _points(conn, "facilities", args), ds[-1]):
            for lim in ds:
                segs = [shapely.LineString(g[u][v]["coords"]) for u, v in g.edges() if dist.get(u, math.inf) + g[u][v]["w"] <= lim
                        or dist.get(v, math.inf) + g[u][v]["w"] <= lim]
                if segs:
                    out.append((shapely.MultiLineString(segs), {"facility_id": r["id"], "within_m": lim}))
        return out
    return Built(None, args, "LineString", [{"name": "facility_id", "type": "integer"}, {"name": "within_m", "type": "number"}], "Service area lines", compute)


@tool("service_area_polygons", "Service area (polygons)", "Network", "The area reachable along the network from each facility within distances.",
      NET, _p("facilities", "Facilities (points)", "layer", geometry=["Point"]),
      _p("distances", "Distances (metres, separated by commas)", "text", default="500, 1000"))
def _(params, layers, args, a):
    ds = numbers(params, "distances", "Distances", 1, 1e7, 5)

    async def compute(conn):
        g, nodes = await _graph(conn, args)
        out = []
        for r, dist in _reach(g, nodes, await _points(conn, "facilities", args), ds[-1]):
            for lim in ds:
                pts = [n for n, d_ in dist.items() if d_ <= lim]
                if len(pts) >= 3:
                    hull = shapely.concave_hull(shapely.MultiPoint(pts), ratio=0.3)
                    if hull.geom_type in ("Polygon", "MultiPolygon"):
                        out.append((hull, {"facility_id": r["id"], "within_m": lim}))
        return out
    return Built(None, args, "Polygon", [{"name": "facility_id", "type": "integer"}, {"name": "within_m", "type": "number"}], "Service areas", compute)


@tool("od_matrix", "Origin-destination matrix", "Network", "The network distance from every origin to every destination (straight lines carry the distance).",
      NET, _p("origins", "Origins (points)", "layer", geometry=["Point"]), _p("destinations", "Destinations (points)", "layer", geometry=["Point"]))
def _(params, layers, args, a):
    import networkx as nx

    async def compute(conn):
        g, nodes = await _graph(conn, args)
        org, dst = await _points(conn, "origins", args), await _points(conn, "destinations", args)
        if len(org) * len(dst) > 50_000:
            raise Problem(422, "That is more than 50,000 pairs: use selections.", code="validation")
        dnodes = [(r, p, _nearest_node(nodes, *p)[0]) for r, p in dst]
        out = []
        for r, p in org:
            n, _ = _nearest_node(nodes, *p)
            dist = nx.single_source_dijkstra_path_length(g, n, weight="w")
            for r2, p2, n2 in dnodes:
                if n2 in dist:
                    out.append((shapely.LineString([p, p2]), {"origin_id": r["id"], "destination_id": r2["id"], "network_m": round(dist[n2], 2)}))
        return out
    return Built(None, args, "LineString", [{"name": "origin_id", "type": "integer"}, {"name": "destination_id", "type": "integer"},
                                            {"name": "network_m", "type": "number"}], "OD matrix", compute)



# =====================================================================================================================
# More: attributes, shapes, lines and networks
# =====================================================================================================================
@tool("join_field", "Join field", "Data management", "Attributes of another layer joined on a matching key field (like a table join).",
      IN, _p("key", "Key field", "field", of="input"), _p("other", "Join from layer", "layer"), _p("other_key", "Its key field", "field", of="other"),
      _p("keep_all", "Keep features with no match", "bool", default=True))
def _(params, layers, args, a):
    b = layers["other"]
    args["k"], args["ok"] = field_of(a, params.get("key"), "Key field"), field_of(b, params.get("other_key"), "Its key field")
    bf, ren = renamed(a["fields"], [f for f in b["fields"] if f["name"] != args["ok"]])
    args["ren"] = json.dumps(ren)
    join = "LEFT JOIN" if params.get("keep_all", True) else "JOIN"
    sql = (f"SELECT s.geom AS g, s.props || COALESCE(o.rp, '{{}}'::jsonb) AS p FROM {src('input')} s {join} LATERAL (SELECT "
           f"{renamed_props('b', 'ren')} - %(ok)s AS rp FROM {src('other')} b WHERE lower(b.props->>%(ok)s) = lower(s.props->>%(k)s) "
           f"ORDER BY b.id LIMIT 1) o ON true")
    return Built(sql, args, a["geometry_type"], a["fields"] + bf, f"{a['name']} joined to {b['name']}")


@tool("delete_fields", "Keep fields", "Data management", "A copy of the layer with only the fields chosen.",
      IN, _p("fields", "Fields to keep", "fields", of="input"))
def _(params, layers, args, a):
    keep = [f for f in params.get("fields") or [] if any(x["name"] == f for x in a["fields"])]
    args["keep"] = keep
    sql = f"SELECT geom AS g, (SELECT COALESCE(jsonb_object_agg(key, value), '{{}}'::jsonb) FROM jsonb_each(props) WHERE key = ANY(%(keep)s::text[])) AS p FROM {src('input')} s"
    return Built(sql, args, a["geometry_type"], [f for f in a["fields"] if f["name"] in keep], f"{a['name']} (fields kept)")


@tool("sort", "Sort", "Data management", "A copy of the layer in the order of a field (the new ids follow that order).",
      IN, _p("field", "Sort by", "field", of="input"), _p("descending", "Largest first", "bool", default=False))
def _(params, layers, args, a):
    f = field_of(a, params.get("field"), "Sort by")
    args["f"] = f
    numeric = next(x["type"] for x in a["fields"] if x["name"] == f) in ("integer", "number")
    key = num("f") if numeric else "props->>%(f)s"
    sql = f"SELECT geom AS g, props AS p FROM {src('input')} s ORDER BY {key} {'DESC' if params.get('descending') else 'ASC'} NULLS LAST, id"
    return Built(sql, args, a["geometry_type"], a["fields"], f"{a['name']} sorted by {f}")


@tool("add_xy", "Add XY coordinates", "Data management", "Each feature's position (its point inside) in a coordinate system of your choice, as X and Y fields.",
      IN, _p("epsg", "Coordinate system (EPSG code; e.g. 26332 for Minna / Nigeria Mid Belt)", "text", default="4326"))
def _(params, layers, args, a):
    try:
        args["srid"] = int(str(params.get("epsg") or "4326").upper().replace("EPSG:", "").strip())
    except ValueError:
        raise Problem(422, "The coordinate system must be an EPSG code, such as 4326 or 26332.", code="validation", fields={"params": ["epsg"]}) from None
    sql = (f"SELECT s.geom AS g, s.props || jsonb_build_object('x', round(ST_X(q.pt)::numeric, 6), 'y', round(ST_Y(q.pt)::numeric, 6), 'epsg', %(srid)s) AS p "
           f"FROM {src('input')} s, LATERAL (SELECT ST_Transform(ST_PointOnSurface(s.geom), %(srid)s) AS pt) q")
    return Built(sql, args, a["geometry_type"], field_add(a["fields"], {"name": "x", "type": "number"}, {"name": "y", "type": "number"},
                                                          {"name": "epsg", "type": "integer"}), f"{a['name']} with XY")


@tool("polygon_neighbors", "Polygon neighbours", "Topology", "The border each pair of polygons shares, as a line with both ids and its length.", IN_POLY)
def _(params, layers, args, a):
    sql = (f"WITH s AS {src('input')} SELECT {norm('ST_LineMerge(ST_CollectionExtract(ST_Intersection(ST_Boundary(x.geom), ST_Boundary(y.geom)), 2))', 'LineString')} AS g, "
           f"jsonb_build_object('id_1', x.id, 'id_2', y.id, 'shared_m', round(ST_Length(ST_CollectionExtract(ST_Intersection(ST_Boundary(x.geom), "
           f"ST_Boundary(y.geom)), 2)::geography)::numeric, 3)) AS p FROM s x JOIN s y ON x.id < y.id AND ST_Intersects(x.geom, y.geom)")
    return Built(sql, args, "LineString", [{"name": "id_1", "type": "integer"}, {"name": "id_2", "type": "integer"}, {"name": "shared_m", "type": "number"}],
                 f"{a['name']} neighbours")


@tool("tabulate_intersection", "Tabulate intersection", "Overlay", "How much of each zone each class covers: area and percentage of the zone.",
      _p("input", "Zones (polygons)", "layer", geometry=["Polygon"]), _p("zone_field", "Zone field", "field", of="input"),
      _p("classes", "Classes (polygons)", "layer", geometry=["Polygon"]), _p("class_field", "Class field", "field", of="classes"))
def _(params, layers, args, a):
    c = layers["classes"]
    args["zf"], args["cf"] = field_of(a, params.get("zone_field"), "Zone field"), field_of(c, params.get("class_field"), "Class field")
    sql = (f"SELECT {norm('ST_Union(ST_Intersection(z.geom, k.geom))', 'Polygon')} AS g, jsonb_build_object('zone', z.props->%(zf)s, 'class', k.props->%(cf)s, "
           f"'area_m2', round(sum(ST_Area(ST_Intersection(z.geom, k.geom)::geography))::numeric, 2), 'percent', "
           f"round((100 * sum(ST_Area(ST_Intersection(z.geom, k.geom)::geography)) / NULLIF(max(ST_Area(z.geom::geography)), 0))::numeric, 3)) AS p "
           f"FROM {src('input')} z JOIN {src('classes')} k ON ST_Intersects(z.geom, k.geom) GROUP BY z.id, z.props->%(zf)s, k.props->%(cf)s")
    return Built(sql, args, "Polygon", [{"name": "zone", "type": "string"}, {"name": "class", "type": "string"}, {"name": "area_m2", "type": "number"},
                                        {"name": "percent", "type": "number"}], f"{a['name']} by {c['name']}")


@tool("count_overlaps", "Count overlapping features", "Overlay", "Every piece where polygons overlap, with how many overlap there.",
      IN_POLY, _p("at_least", "Keep pieces covered at least", "number", min=1, default=2))
def _(params, layers, args, a):
    args["min"] = int(number(params, "at_least", 1, 10_000, "At least"))
    sql = (f"WITH s AS {src('input')}, faces AS (SELECT (ST_Dump(ST_Polygonize(l))).geom AS f FROM (SELECT ST_Union(ST_Boundary(geom)) AS l FROM s) x) "
           f"SELECT faces.f AS g, jsonb_build_object('count', c.n, 'ids', c.ids) AS p FROM faces CROSS JOIN LATERAL (SELECT count(*) AS n, "
           f"array_agg(s.id ORDER BY s.id) AS ids FROM s WHERE ST_Contains(s.geom, ST_PointOnSurface(faces.f))) c WHERE c.n >= %(min)s")
    return Built(sql, args, "Polygon", [{"name": "count", "type": "integer"}, {"name": "ids", "type": "string"}], f"{a['name']} overlap count")


@tool("remove_overlaps", "Remove overlaps", "Topology", "Overlapping polygons trimmed so that each piece of ground belongs to one polygon only "
      "(the earlier feature, or the larger, keeps it).", IN_POLY,
      _p("keep", "Who keeps an overlap", "choice", default="first", choices=[["first", "The earlier feature"], ["larger", "The larger polygon"]]))
def _(params, layers, args, a):
    order = "ST_Area(geom::geography) DESC, id" if params.get("keep") == "larger" else "id"
    sql = (f"WITH s AS (SELECT geom, props, row_number() OVER (ORDER BY {order}) AS r FROM {src('input')} k) "
           f"SELECT {norm('COALESCE(ST_Difference(x.geom, (SELECT ST_Union(y.geom) FROM s y WHERE y.r < x.r AND ST_Intersects(x.geom, y.geom))), x.geom)', 'Polygon')} AS g, "
           f"x.props AS p FROM s x")
    return Built(sql, args, "Polygon", a["fields"], f"{a['name']} without overlaps")


@tool("shape_metrics", "Polygon shape metrics", "Statistics", "Area, perimeter, compactness (1 = circle), elongation and fractal dimension of each polygon.", IN_POLY)
def _(params, layers, args, a):
    sql = (f"SELECT geom AS g, props || jsonb_build_object('area_m2', round(ar::numeric, 2), 'perimeter_m', round(pe::numeric, 2), "
           f"'compactness', round((4 * pi() * ar / NULLIF(pe * pe, 0))::numeric, 4), "
           f"'elongation', round((1 - LEAST(bw, bh) / NULLIF(GREATEST(bw, bh), 0))::numeric, 4), "
           f"'fractal_dim', CASE WHEN ar > 1 AND pe > 1 THEN round((2 * ln(pe / 4) / ln(ar))::numeric, 4) END) AS p FROM (SELECT geom, props, "
           f"ST_Area(geom::geography) AS ar, ST_Perimeter(geom::geography) AS pe, ST_Distance(ST_PointN(ST_ExteriorRing(b), 1)::geography, ST_PointN(ST_ExteriorRing(b), 2)::geography) AS bw, "
           f"ST_Distance(ST_PointN(ST_ExteriorRing(b), 2)::geography, ST_PointN(ST_ExteriorRing(b), 3)::geography) AS bh "
           f"FROM (SELECT geom, props, ST_OrientedEnvelope(geom) AS b FROM {src('input')} s) q) w")
    return Built(sql, args, "Polygon", field_add(a["fields"], *({"name": n_, "type": "number"} for n_ in ("area_m2", "perimeter_m", "compactness", "elongation", "fractal_dim"))),
                 f"{a['name']} shape metrics")


@tool("line_direction", "Line direction and sinuosity", "Statistics", "Each line's length, the bearing from its start to its end, and how winding it is.", IN_LINES)
def _(params, layers, args, a):
    sql = (f"SELECT geom AS g, props || jsonb_build_object('length_m', round(len::numeric, 3), 'bearing_deg', round(degrees(ST_Azimuth(ST_StartPoint(l)::geography, "
           f"ST_EndPoint(l)::geography))::numeric, 2), 'straight_m', round(st::numeric, 3), 'sinuosity', round((len / NULLIF(st, 0))::numeric, 4)) AS p "
           f"FROM (SELECT geom, props, ST_GeometryN(ST_LineMerge(geom), 1) AS l, ST_Length(geom::geography) AS len, "
           f"ST_Distance(ST_StartPoint(ST_GeometryN(ST_LineMerge(geom), 1))::geography, ST_EndPoint(ST_GeometryN(ST_LineMerge(geom), 1))::geography) AS st "
           f"FROM {src('input')} s) q")
    return Built(sql, args, "LineString", field_add(a["fields"], *({"name": n_, "type": "number"} for n_ in ("length_m", "bearing_deg", "straight_m", "sinuosity"))),
                 f"{a['name']} directions")


@tool("split_by_length", "Split lines by length", "Conversion", "Lines cut into pieces no longer than a length (the last piece takes what is left).",
      IN_LINES, _p("length", "Piece length", "number", unit="m", min=0.1, default=100))
def _(params, layers, args, a):
    args["len"] = number(params, "length", 0.1, 1e7, "Piece length")
    sql = (f"SELECT ST_LineSubstring(d.geom, k * %(len)s / q.len, LEAST(1, (k + 1) * %(len)s / q.len)) AS g, s.props || jsonb_build_object('piece', k + 1) AS p "
           f"FROM {src('input')} s, ST_Dump(ST_LineMerge(s.geom)) d, LATERAL (SELECT ST_Length(d.geom::geography) AS len) q, "
           f"generate_series(0, ceil(q.len / %(len)s)::int - 1) k WHERE q.len > 0 AND k < 100000")
    return Built(sql, args, "LineString", field_add(a["fields"], {"name": "piece", "type": "integer"}), f"{a['name']} in {args['len']:g} m pieces")


@tool("transects", "Transects along lines", "Conversion", "Lines across each line, at right angles, at a set interval (for cross-sections and sampling).",
      IN_LINES, _p("interval", "Every", "number", unit="m", min=0.1, default=100), _p("length", "Transect length", "number", unit="m", min=0.1, default=50))
def _(params, layers, args, a):
    args["iv"], args["half"], args["utm"] = number(params, "interval", 0.1, 1e6, "Every"), number(params, "length", 0.1, 1e6, "Transect length") / 2, utm_srid(a)
    sql = (f"SELECT ST_Transform(ST_MakeLine(ST_Translate(q.p, -q.h * sin(q.az + pi() / 2), -q.h * cos(q.az + pi() / 2)), "
           f"ST_Translate(q.p, q.h * sin(q.az + pi() / 2), q.h * cos(q.az + pi() / 2))), 4326) AS g, q.props || jsonb_build_object('distance_m', round(q.at::numeric, 3)) AS p "
           f"FROM (SELECT s.props, %(half)s::float8 AS h, k * %(iv)s AS at, ST_LineInterpolatePoint(u.l, LEAST(1, k * %(iv)s / u.len)) AS p, "
           f"ST_Azimuth(ST_LineInterpolatePoint(u.l, GREATEST(0, LEAST(1, k * %(iv)s / u.len) - 0.0005)), ST_LineInterpolatePoint(u.l, LEAST(1, LEAST(1, k * %(iv)s / u.len) + 0.0005))) AS az "
           f"FROM {src('input')} s, ST_Dump(ST_LineMerge(ST_Transform(s.geom, %(utm)s))) d, LATERAL (SELECT d.geom AS l, ST_Length(d.geom) AS len) u, "
           f"generate_series(0, floor(u.len / %(iv)s)::int) k WHERE u.len > 0 AND k < 100000) q")
    return Built(sql, args, "LineString", field_add(a["fields"], {"name": "distance_m", "type": "number"}), f"{a['name']} transects")


@tool("bearing_distance", "Bearing distance to line", "Conversion", "From each point, a line along the bearing and distance in its fields (on the ellipsoid).",
      IN_PTS, _p("bearing", "Bearing field (degrees from north)", "field", of="input", numeric=True),
      _p("distance", "Distance field (metres)", "field", of="input", numeric=True))
def _(params, layers, args, a):
    args["b"], args["d"] = field_of(a, params.get("bearing"), "Bearing field", numeric=True), field_of(a, params.get("distance"), "Distance field", numeric=True)
    sql = (f"SELECT ST_MakeLine(geom, ST_Project(geom::geography, {num('d')}, radians({num('b')}))::geometry) AS g, props AS p "
           f"FROM {src('input')} s WHERE {num('d')} > 0 AND {num('b')} IS NOT NULL")
    return Built(sql, args, "LineString", a["fields"], f"{a['name']} bearing lines")


@tool("wedges", "Wedge buffer", "Proximity", "A pie-slice from each point: a direction, an opening angle and a radius (views, antenna and sensor coverage).",
      IN_PTS, _p("radius", "Radius", "number", unit="m", min=0.1, default=500), _p("direction", "Facing (degrees from north)", "number", default=0),
      _p("width", "Opening angle (degrees)", "number", min=1, default=60))
def _(params, layers, args, a):
    args["r"], args["dir"], args["w"] = number(params, "radius", 0.1, 1e6, "Radius"), number(params, "direction", -360, 360, "Facing"), number(params, "width", 1, 360, "Opening angle")
    sql = (f"SELECT ST_MakePolygon(ST_MakeLine(ARRAY[s.geom] || ARRAY(SELECT ST_Project(s.geom::geography, %(r)s, radians(%(dir)s - %(w)s / 2 + %(w)s * i / 36.0))::geometry "
           f"FROM generate_series(0, 36) i) || ARRAY[s.geom])) AS g, s.props AS p FROM {src('input')} s")
    return Built(sql, args, "Polygon", a["fields"], f"{a['name']} wedges")


@tool("snap_to_lines", "Snap points to lines", "Proximity", "Points moved onto the nearest line within a distance, with how far they moved.",
      IN_PTS, _p("lines", "Lines", "layer", geometry=["LineString", "Polygon"]), _p("tolerance", "Within", "number", unit="m", min=0.01, default=50))
def _(params, layers, args, a):
    args["t"] = number(params, "tolerance", 0.01, 1e6, "Within")
    sql = (f"SELECT COALESCE(n.p, s.geom) AS g, s.props || jsonb_build_object('moved_m', round(COALESCE(n.d, 0)::numeric, 3), 'line_id', n.id) AS p "
           f"FROM {src('input')} s LEFT JOIN LATERAL (SELECT l.id, ST_ClosestPoint(CASE WHEN GeometryType(l.geom) LIKE '%%POLYGON' THEN ST_Boundary(l.geom) ELSE l.geom END, s.geom) AS p, "
           f"ST_Distance(s.geom::geography, l.geom::geography) AS d FROM {src('lines')} l WHERE ST_DWithin(s.geom::geography, l.geom::geography, %(t)s) "
           f"ORDER BY 3 LIMIT 1) n ON true")
    return Built(sql, args, "Point", field_add(a["fields"], {"name": "moved_m", "type": "number"}, {"name": "line_id", "type": "integer"}), f"{a['name']} snapped")


@tool("thin_points", "Thin points", "Sampling", "Points closer together than a distance reduced to one each (the first), for even coverage.",
      IN_PTS, _p("distance", "Keep points at least", "number", unit="m", min=0.01, default=100))
def _(params, layers, args, a):
    d = number(params, "distance", 0.01, 1e6, "Keep points at least")
    srid = utm_srid(a)

    async def compute(conn):
        rows, xy, _, back = await _xy(conn, "input", args, a, srid=srid)
        cell = d
        grid: dict = {}
        out = []
        for r, (x, y) in zip(rows, xy):
            gx, gy = int(x // cell), int(y // cell)
            near = [q for i in (-1, 0, 1) for j in (-1, 0, 1) for q in grid.get((gx + i, gy + j), [])]
            if all((x - qx) ** 2 + (y - qy) ** 2 >= d * d for qx, qy in near):
                grid.setdefault((gx, gy), []).append((x, y))
                out.append((shapely.from_wkb(bytes(r["wkb"])), r["props"]))
        return out
    return Built(None, args, "Point", a["fields"], f"{a['name']} thinned to {d:g} m", compute)


@tool("unsplit_lines", "Unsplit lines", "Generalization", "Lines that meet end to end joined into one (all, or those with the same value in a field).",
      IN_LINES, _p("field", "Only where equal in", "field", of="input", optional=True))
def _(params, layers, args, a):
    f = field_of(a, params.get("field"), "Only where equal in", optional=True)
    args["f"] = f or ""
    sql = ("SELECT (ST_Dump(ST_LineMerge(ST_Union(geom)))).geom AS g, " + ("jsonb_build_object(%(f)s::text, props->%(f)s)" if f else "'{}'::jsonb") +
           f" AS p FROM {src('input')} s" + (" GROUP BY props->%(f)s" if f else ""))
    return Built(sql, args, "LineString", [x for x in a["fields"] if x["name"] == f], f"{a['name']} unsplit")


@tool("find_dangles", "Find dangles", "Topology", "Line ends that touch no other line (gaps and overshoots in a network), and junctions with how many lines meet.",
      IN_LINES, _p("what", "Find", "choice", default="dangles", choices=[["dangles", "Dangling ends"], ["junctions", "Junctions"]]))
def _(params, layers, args, a):
    having = "count(*) = 1" if params.get("what", "dangles") == "dangles" else "count(*) >= 3"
    sql = (f"WITH e AS (SELECT ST_StartPoint(d.geom) AS p FROM (SELECT (ST_Dump(ST_Node(ST_Collect(geom)))).geom FROM {src('input')} s) d "
           f"UNION ALL SELECT ST_EndPoint(d.geom) FROM (SELECT (ST_Dump(ST_Node(ST_Collect(geom)))).geom FROM {src('input')} s) d) "
           f"SELECT ST_SetSRID(ST_GeomFromWKB(w), 4326) AS g, jsonb_build_object('lines', count(*)) AS p FROM (SELECT ST_AsBinary(ST_SnapToGrid(p, 1e-9)) AS w FROM e) q "
           f"GROUP BY w HAVING {having}")
    return Built(sql, args, "Point", [{"name": "lines", "type": "integer"}], f"{a['name']} {params.get('what', 'dangles')}")


@tool("spanning_tree", "Minimum spanning tree", "Network", "The shortest set of straight links joining all the points (for pipes, cables or paths).", IN_PTS)
def _(params, layers, args, a):
    import networkx as nx
    from scipy.spatial import Delaunay

    async def compute(conn):
        rows, xy, _, back = await _xy(conn, "input", args, a)
        if len(rows) < 2:
            raise Problem(422, "A spanning tree needs at least two points.", code="validation")
        g = nx.Graph()
        if len(rows) >= 4:
            for s_ in Delaunay(xy).simplices:          # the minimum spanning tree is always within the Delaunay edges
                for i, j in ((s_[0], s_[1]), (s_[1], s_[2]), (s_[0], s_[2])):
                    g.add_edge(int(i), int(j), w=float(np.hypot(*(xy[i] - xy[j]))))
        else:
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    g.add_edge(i, j, w=float(np.hypot(*(xy[i] - xy[j]))))
        geo = [shapely.from_wkb(bytes(r["wkb"])) for r in rows]
        from pyproj import Geod
        geod = Geod(ellps="WGS84")
        out = []
        for i, j in nx.minimum_spanning_tree(g, weight="w").edges():
            p1, p2 = _point_on(geo[i]), _point_on(geo[j])
            out.append((shapely.LineString([p1, p2]), {"from_id": rows[i]["id"], "to_id": rows[j]["id"], "length_m": round(geod.inv(*p1, *p2)[2], 3)}))
        return out
    return Built(None, args, "LineString", [{"name": "from_id", "type": "integer"}, {"name": "to_id", "type": "integer"}, {"name": "length_m", "type": "number"}],
                 f"{a['name']} spanning tree", compute)


@tool("optimal_route", "Best order of stops", "Network", "A short route along the network that visits every stop once, in the best order found "
      "(travelling salesman), starting from the first stop.",
      NET, _p("stops", "Stops (points)", "layer", geometry=["Point"]), _p("back", "Return to the start", "bool", default=True))
def _(params, layers, args, a):
    import networkx as nx
    st = layers["stops"]

    async def compute(conn):
        g, nodes = await _graph(conn, args)
        pts = await _points(conn, "stops", args)
        if not 2 <= len(pts) <= 200:
            raise Problem(422, "The best order works on 2 to 200 stops.", code="validation")
        ns = [_nearest_node(nodes, *p)[0] for _, p in pts]
        dist, paths = {}, {}
        for i, n in enumerate(ns):
            d_, p_ = nx.single_source_dijkstra(g, n, weight="w")
            for j, m in enumerate(ns):
                if m not in d_:
                    raise Problem(422, f"Stops {pts[i][0]['id']} and {pts[j][0]['id']} are not joined by the network.", code="no_path")
                dist[i, j], paths[i, j] = d_[m], p_[m]
        k = nx.complete_graph(len(pts))
        for i, j in k.edges():
            k[i][j]["w"] = dist[i, j]
        cycle = params.get("back", True)
        order = nx.approximation.traveling_salesman_problem(k, weight="w", cycle=cycle, method=nx.approximation.christofides if cycle else None)
        if order[0] != 0:                                       # start at the first stop
            if cycle:
                order = order[:-1]
                at = order.index(0)
                order = order[at:] + order[:at] + [0]
        path, total = [], 0.0
        for i, j in zip(order, order[1:]):
            seg = paths[i, j]
            total += dist[i, j]
            path += seg if not path else seg[1:]
        line = _path_line(g, path) if len(path) > 1 else None
        return [(line, {"stops": len(pts), "network_m": round(total, 2), "order": ", ".join(str(pts[i][0]["id"]) for i in order)})] if line else []
    return Built(None, args, "LineString", [{"name": "stops", "type": "integer"}, {"name": "network_m", "type": "number"}, {"name": "order", "type": "string"}],
                 f"Best route through {st['name']}", compute)


@tool("allocate", "Allocate to facilities", "Network", "Each demand point assigned to the facility nearest along the network, with the distance.",
      NET, _p("incidents", "Demand points", "layer", geometry=["Point"]), _p("facilities", "Facilities (points)", "layer", geometry=["Point"]))
def _(params, layers, args, a):
    import networkx as nx
    inc = layers["incidents"]

    async def compute(conn):
        g, nodes = await _graph(conn, args)
        fnode = {}
        for r, p in await _points(conn, "facilities", args):
            fnode.setdefault(_nearest_node(nodes, *p)[0], r)
        dist, paths = nx.multi_source_dijkstra(g, set(fnode), weight="w")
        out = []
        for r, p in await _points(conn, "incidents", args):
            n, _ = _nearest_node(nodes, *p)
            f_ = fnode[paths[n][0]] if n in paths else None
            out.append((shapely.from_wkb(bytes(r["wkb"])), {**r["props"], "facility_id": f_["id"] if f_ else None,
                                                             "network_m": round(dist[n], 2) if f_ else None}))
        return out
    return Built(None, args, "Point", field_add(inc["fields"], {"name": "facility_id", "type": "integer"}, {"name": "network_m", "type": "number"}),
                 f"{inc['name']} allocated", compute)
