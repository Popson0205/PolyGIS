"""The geoprocessing toolbox. Each tool reads one or more layers (all of their features, or only the selected ones)
and writes its result as a new layer. Everything runs inside PostGIS; distances, areas and lengths are worked out on
the ellipsoid (geography), so they are true metres and square metres anywhere.

A tool is described by PARAMS (which the app turns into a form) and built by a function that returns:
  sql     a SELECT yielding (g geometry, p jsonb) for the new layer's features
  args    its named parameters
  kind    the new layer's geometry: Point | LineString | Polygon
  fields  the new layer's fields
"""
import json
import math
from dataclasses import dataclass

from .errors import Problem

TYPE_NO = {"Point": 1, "LineString": 2, "Polygon": 3}
DIM = {"Point": 0, "LineString": 1, "Polygon": 2}
MAX_CELLS = 200_000
M_PER_DEG = 111_320.0          # metres in a degree of latitude, for the one tool that needs degrees (simplify)


@dataclass
class Built:
    sql: str
    args: dict
    kind: str
    fields: list[dict]
    name: str


def _p(name, label, type, **kw):
    return {"name": name, "label": label, "type": type, **kw}


INPUT = _p("input", "Input layer", "layer")
SELECTED = "Only the selected features of a layer are used when its selection is passed with it."

TOOLS: dict[str, dict] = {
    # ---- proximity
    "buffer": {"name": "Buffer", "group": "Proximity", "about": "Areas within a distance of each feature.",
               "params": [INPUT, _p("distance", "Distance", "number", unit="m", min=0.01, default=100),
                          _p("dissolve", "Merge overlapping buffers into one", "bool", default=False)]},
    "near": {"name": "Near", "group": "Proximity", "about": "The distance from each feature to the nearest feature of another layer.",
             "params": [INPUT, _p("near", "Near features", "layer")]},
    "spatial_join": {"name": "Spatial join", "group": "Proximity",
                     "about": "Each feature gets the attributes of the feature it touches in another layer (the nearest when "
                              "a distance is used), and how many it touches.",
                     "params": [INPUT, _p("join", "Join features", "layer"),
                                _p("relation", "Match when they", "choice", default="intersects",
                                   choices=[["intersects", "intersect"], ["distance", "are within a distance"]]),
                                _p("distance", "Distance", "number", unit="m", min=0, default=100, when={"relation": "distance"})]},
    # ---- overlay
    "clip": {"name": "Clip", "group": "Overlay", "about": "The parts of the input inside the clip polygons.",
             "params": [INPUT, _p("clip", "Clip polygons", "layer", geometry=["Polygon"])]},
    "erase": {"name": "Erase", "group": "Overlay", "about": "The parts of the input outside the erase polygons.",
              "params": [INPUT, _p("erase", "Erase polygons", "layer", geometry=["Polygon"])]},
    "intersect": {"name": "Intersect", "group": "Overlay",
                  "about": "Where features of two layers overlap, with the attributes of both.",
                  "params": [INPUT, _p("overlay", "Overlay layer", "layer")]},
    "merge": {"name": "Merge", "group": "Overlay", "about": "Several layers of the same kind put together in one.",
              "params": [_p("layers", "Layers to merge", "layers", min=2)]},
    # ---- generalization
    "dissolve": {"name": "Dissolve", "group": "Generalization",
                 "about": "Features with the same values merged into one (all of them when no field is chosen), with a count.",
                 "params": [INPUT, _p("fields", "Dissolve by", "fields", of="input", optional=True)]},
    "simplify": {"name": "Simplify", "group": "Generalization", "about": "Fewer vertices, keeping the shape within a tolerance.",
                 "params": [_p("input", "Input layer", "layer", geometry=["LineString", "Polygon"]),
                            _p("tolerance", "Tolerance", "number", unit="m", min=0.01, default=5)]},
    "convex_hull": {"name": "Convex hull", "group": "Generalization", "about": "The smallest convex polygon around the features.",
                    "params": [INPUT, _p("each", "One for each feature (else one for all)", "bool", default=False)]},
    "envelope": {"name": "Bounding box", "group": "Generalization", "about": "The rectangle around the features.",
                 "params": [INPUT, _p("each", "One for each feature (else one for all)", "bool", default=True)]},
    # ---- conversion
    "centroids": {"name": "Feature to point", "group": "Conversion", "about": "A point for each feature.",
                  "params": [INPUT, _p("inside", "Keep the point inside the shape", "bool", default=True)]},
    "boundary": {"name": "Polygon to line", "group": "Conversion", "about": "The outline of each polygon as a line.",
                 "params": [_p("input", "Input polygons", "layer", geometry=["Polygon"])]},
    "explode": {"name": "Multipart to singlepart", "group": "Conversion", "about": "Each part of a multi-part feature on its own.",
                "params": [INPUT]},
    "voronoi": {"name": "Thiessen polygons", "group": "Conversion",
                "about": "The area closest to each point, with the point's attributes.",
                "params": [_p("input", "Input points", "layer", geometry=["Point"])]},
    "grid": {"name": "Grid", "group": "Conversion", "about": "Square or hexagon cells of a set size over a layer.",
             "params": [_p("input", "Over the layer", "layer"), _p("size", "Cell size", "number", unit="m", min=1, default=100),
                        _p("shape", "Cells", "choice", default="square", choices=[["square", "Squares"], ["hexagon", "Hexagons"]]),
                        _p("touching", "Only cells that touch its features", "bool", default=True)]},
    # ---- statistics
    "summarize_within": {"name": "Summarize within", "group": "Statistics",
                         "about": "For each polygon, how many features of another layer fall in it, and the sum of one of their fields.",
                         "params": [_p("input", "Polygons", "layer", geometry=["Polygon"]), _p("summarize", "Features to count", "layer"),
                                    _p("sum_field", "Add up", "field", of="summarize", numeric=True, optional=True)]},
    "calculate_geometry": {"name": "Calculate geometry", "group": "Statistics",
                           "about": "Area and perimeter, length, or longitude and latitude, as fields.", "params": [INPUT]},
    # ---- data management
    "copy": {"name": "Copy features", "group": "Data management", "about": "The features (or the selection) as a new layer.",
             "params": [INPUT]},
}


def catalog() -> list[dict]:
    return [{"id": k, **v} for k, v in TOOLS.items()]


# ---------- helpers ----------
def src(key: str) -> str:
    """The features of the layer passed as `key`, or only its selected ones when `key_ids` is given."""
    return (f"(SELECT id, geom, props FROM features WHERE layer_id = %({key})s "
            f"AND (%({key}_ids)s::bigint[] IS NULL OR id = ANY(%({key}_ids)s::bigint[])))")


def norm(g: str, kind: str) -> str:
    """A valid geometry of the output kind (multi-parts allowed); anything else becomes empty and is dropped."""
    return f"ST_CollectionExtract(ST_MakeValid({g}), {TYPE_NO[kind]})"


def renamed(base: list[dict], other: list[dict], suffix: str = "_2") -> tuple[list[dict], dict]:
    """The other layer's fields, renamed where they clash with the base's; and the map of renames."""
    taken = {f["name"].lower() for f in base}
    out, ren = [], {}
    for f in other:
        new = f["name"]
        while new.lower() in taken:
            new += suffix
        taken.add(new.lower())
        if new != f["name"]:
            ren[f["name"]] = new
        out.append({**f, "name": new})
    return out, ren


def renamed_props(alias: str, key: str) -> str:
    """`alias.props` with keys renamed by the json map passed as `key`."""
    return (f"(SELECT COALESCE(jsonb_object_agg(COALESCE(m.new, e.key), e.value), '{{}}'::jsonb) FROM jsonb_each({alias}.props) e "
            f"LEFT JOIN jsonb_each_text(%({key})s::jsonb) m(old, new) ON m.old = e.key)")


def field_add(fields: list[dict], *new: dict) -> list[dict]:
    names = {f["name"].lower() for f in fields}
    return fields + [f for f in new if f["name"].lower() not in names]


def need_kind(layer: dict, kinds: list[str], label: str) -> None:
    if layer["geometry_type"] not in kinds:
        words = {"Point": "points", "LineString": "lines", "Polygon": "polygons"}
        raise Problem(422, f"{label} must be {' or '.join(words[k] for k in kinds)}; {layer['name']} holds "
                           f"{words[layer['geometry_type']]}.", code="validation", fields={"params": [label]})


def number(params: dict, name: str, lo: float, hi: float, label: str) -> float:
    try:
        v = float(params.get(name))
    except (TypeError, ValueError):
        raise Problem(422, f"{label} must be a number.", code="validation", fields={"params": [name]}) from None
    if not lo <= v <= hi:
        raise Problem(422, f"{label} must be between {lo:g} and {hi:g}.", code="validation", fields={"params": [name]})
    return v


# ---------- the tools ----------
def build(tool: str, params: dict, layers: dict[str, dict]) -> Built:
    """`layers` maps each layer parameter to its layer row (and `layers` to a list for merge)."""
    a = layers.get("input")
    args: dict = {}
    for key, lay in layers.items():
        if key == "layers":
            continue
        args[key] = lay["id"]
        ids = params.get(f"{key}_ids")
        args[f"{key}_ids"] = [int(x) for x in ids] if ids else None
    name = lambda what: f"{a['name']} {what}"  # noqa: E731

    if tool == "buffer":
        d = number(params, "distance", 0.01, 1_000_000, "Distance")
        args["d"] = d
        if params.get("dissolve"):
            sql = f"SELECT ST_Union(ST_Buffer(geom::geography, %(d)s, 'quad_segs=8')::geometry) AS g, '{{}}'::jsonb AS p FROM {src('input')} s"
            return Built(sql, args, "Polygon", [], name(f"buffer {d:g} m"))
        sql = f"SELECT ST_Buffer(geom::geography, %(d)s, 'quad_segs=8')::geometry AS g, props AS p FROM {src('input')} s"
        return Built(sql, args, "Polygon", a["fields"], name(f"buffer {d:g} m"))

    if tool == "near":
        b = layers["near"]
        sql = (f"SELECT a.geom AS g, a.props || jsonb_build_object('near_id', n.id, 'near_dist_m', round(n.d::numeric, 3)) AS p "
               f"FROM {src('input')} a LEFT JOIN LATERAL (SELECT c.id, ST_Distance(a.geom::geography, c.geom::geography) AS d FROM "
               f"(SELECT b.id, b.geom FROM {src('near')} b ORDER BY a.geom <-> b.geom LIMIT 8) c ORDER BY 2 LIMIT 1) n ON true")
        if b["id"] == a["id"]:
            sql = sql.replace("ORDER BY a.geom <-> b.geom", "WHERE b.id <> a.id ORDER BY a.geom <-> b.geom")
        return Built(sql, args, a["geometry_type"],
                     field_add(a["fields"], {"name": "near_id", "type": "integer"}, {"name": "near_dist_m", "type": "number"}), name("near"))

    if tool == "spatial_join":
        b = layers["join"]
        bf, ren = renamed(a["fields"] + [{"name": "join_count"}], b["fields"])
        args["ren"] = json.dumps(ren)
        if params.get("relation") == "distance":
            args["d"] = number(params, "distance", 0, 1_000_000, "Distance")
            match, order = "ST_DWithin(t.geom::geography, b.geom::geography, %(d)s)", "ST_Distance(t.geom::geography, b.geom::geography)"
        else:
            match, order = "ST_Intersects(t.geom, b.geom)", "b.id"
        sql = (f"SELECT t.geom AS g, t.props || jsonb_build_object('join_count', COALESCE(j.n, 0)) || COALESCE(j.jp, '{{}}'::jsonb) AS p "
               f"FROM {src('input')} t LEFT JOIN LATERAL (SELECT count(*) OVER () AS n, {renamed_props('b', 'ren')} AS jp "
               f"FROM {src('join')} b WHERE {match} ORDER BY {order} LIMIT 1) j ON true")
        return Built(sql, args, a["geometry_type"], field_add(a["fields"], {"name": "join_count", "type": "integer"}, *bf), name("joined"))

    if tool in ("clip", "erase"):
        need_kind(layers[tool], ["Polygon"], "The clip layer" if tool == "clip" else "The erase layer")
        u = f"(SELECT ST_Union(geom) AS u FROM {src(tool)} c) c"
        if tool == "clip":
            sql = f"SELECT {norm('ST_Intersection(f.geom, c.u)', a['geometry_type'])} AS g, f.props AS p FROM {src('input')} f, {u} WHERE ST_Intersects(f.geom, c.u)"
        else:
            sql = (f"SELECT {norm('CASE WHEN c.u IS NOT NULL AND ST_Intersects(f.geom, c.u) THEN ST_Difference(f.geom, c.u) ELSE f.geom END', a['geometry_type'])} "
                   f"AS g, f.props AS p FROM {src('input')} f, {u}")
        return Built(sql, args, a["geometry_type"], a["fields"], name("clipped" if tool == "clip" else "erased"))

    if tool == "intersect":
        b = layers["overlay"]
        kind = min((a["geometry_type"], b["geometry_type"]), key=lambda k: DIM[k])
        bf, ren = renamed(a["fields"], b["fields"])
        args["ren"] = json.dumps(ren)
        sql = (f"SELECT {norm('ST_Intersection(x.geom, y.geom)', kind)} AS g, x.props || {renamed_props('y', 'ren')} AS p "
               f"FROM {src('input')} x JOIN {src('overlay')} y ON ST_Intersects(x.geom, y.geom)")
        return Built(sql, args, kind, a["fields"] + bf, f"{a['name']} × {b['name']}")

    if tool == "merge":
        ls = layers["layers"]
        kinds = {l["geometry_type"] for l in ls}
        if len(ls) < 2:
            raise Problem(422, "Choose at least two layers to merge.", code="validation", fields={"params": ["layers"]})
        if len(kinds) > 1:
            raise Problem(422, "Layers to merge must hold the same kind of features (all points, all lines or all polygons).",
                          code="validation", fields={"params": ["layers"]})
        fields: list[dict] = []
        for l in ls:
            for f in l["fields"]:
                have = next((x for x in fields if x["name"] == f["name"]), None)
                if have is None:
                    fields.append(dict(f))
                elif have["type"] != f["type"]:
                    have["type"] = "string"
        parts = []
        for i, l in enumerate(ls):
            args[f"m{i}"], args[f"mn{i}"] = l["id"], l["name"]
            ids = (params.get("layers_ids") or {}).get(str(l["id"])) if isinstance(params.get("layers_ids"), dict) else None
            args[f"m{i}_ids"] = [int(x) for x in ids] if ids else None
            parts.append(f"SELECT geom AS g, props || jsonb_build_object('source_layer', %(mn{i})s::text) AS p FROM {src(f'm{i}')} s{i}")
        return Built(" UNION ALL ".join(parts), args, kinds.pop(), field_add(fields, {"name": "source_layer", "type": "string"}),
                     "Merged " + ", ".join(l["name"] for l in ls)[:150])

    if tool == "dissolve":
        by = [f for f in (params.get("fields") or []) if f]
        known = {f["name"] for f in a["fields"]}
        for f in by:
            if f not in known:
                raise Problem(422, f"{a['name']} has no field called {f}.", code="validation", fields={"params": ["fields"]})
        for i, f in enumerate(by):
            args[f"f{i}"] = f
        keys = ", ".join(f"props->%(f{i})s" for i in range(len(by)))
        obj = " || ".join([f"jsonb_build_object(%(f{i})s::text, props->%(f{i})s)" for i in range(len(by))] + ["jsonb_build_object('count', count(*))"])
        sql = f"SELECT {norm('ST_Union(geom)', a['geometry_type'])} AS g, {obj} AS p FROM {src('input')} s" + (f" GROUP BY {keys}" if by else "")
        return Built(sql, args, a["geometry_type"], [f for f in a["fields"] if f["name"] in by] + [{"name": "count", "type": "integer"}],
                     name("dissolved"))

    if tool == "simplify":
        need_kind(a, ["LineString", "Polygon"], "The input")
        t = number(params, "tolerance", 0.01, 100_000, "Tolerance")
        args["t"] = t / M_PER_DEG
        sql = f"SELECT {norm('ST_SimplifyPreserveTopology(geom, %(t)s)', a['geometry_type'])} AS g, props AS p FROM {src('input')} s"
        return Built(sql, args, a["geometry_type"], a["fields"], name(f"simplified {t:g} m"))

    if tool in ("convex_hull", "envelope"):
        fn = "ST_ConvexHull" if tool == "convex_hull" else "ST_Envelope"
        word = "hull" if tool == "convex_hull" else "bounding box"
        if params.get("each"):
            sql = f"SELECT {norm(f'{fn}(geom)', 'Polygon')} AS g, props AS p FROM {src('input')} s"
            return Built(sql, args, "Polygon", a["fields"], name(word))
        sql = f"SELECT {norm(f'{fn}(ST_Collect(geom))', 'Polygon')} AS g, jsonb_build_object('count', count(*)) AS p FROM {src('input')} s"
        return Built(sql, args, "Polygon", [{"name": "count", "type": "integer"}], name(word))

    if tool == "centroids":
        fn = "ST_PointOnSurface" if params.get("inside", True) else "ST_Centroid"
        sql = f"SELECT {fn}(geom) AS g, props AS p FROM {src('input')} s"
        return Built(sql, args, "Point", a["fields"], name("points"))

    if tool == "boundary":
        need_kind(a, ["Polygon"], "The input")
        sql = f"SELECT {norm('ST_Boundary(geom)', 'LineString')} AS g, props AS p FROM {src('input')} s"
        return Built(sql, args, "LineString", a["fields"], name("outlines"))

    if tool == "explode":
        sql = f"SELECT (ST_Dump(geom)).geom AS g, props AS p FROM {src('input')} s"
        return Built(sql, args, a["geometry_type"], a["fields"], name("single parts"))

    if tool == "voronoi":
        need_kind(a, ["Point"], "The input")
        sql = (f"WITH pts AS {src('input')}, env AS (SELECT ST_Expand(ST_Envelope(ST_Collect(geom)), "
               f"GREATEST(ST_XMax(ST_Collect(geom)) - ST_XMin(ST_Collect(geom)), ST_YMax(ST_Collect(geom)) - ST_YMin(ST_Collect(geom)), 0.001) * 0.1) AS e FROM pts), "
               f"cells AS (SELECT (ST_Dump(ST_VoronoiPolygons(ST_Collect(geom), 0, (SELECT e FROM env)))).geom AS c FROM pts) "
               f"SELECT {norm('ST_Intersection(cells.c, env.e)', 'Polygon')} AS g, COALESCE(pt.props, '{{}}'::jsonb) AS p FROM cells, env "
               f"LEFT JOIN LATERAL (SELECT props FROM pts WHERE ST_Intersects(cells.c, pts.geom) ORDER BY id LIMIT 1) pt ON true")
        return Built(sql, args, "Polygon", a["fields"], name("Thiessen polygons"))

    if tool == "grid":
        size = number(params, "size", 1, 1_000_000, "Cell size")
        args["size"] = size
        if a.get("bbox"):
            x1, y1, x2, y2 = a["bbox"]
            w = (x2 - x1) * M_PER_DEG * math.cos(math.radians((y1 + y2) / 2))
            cells = (w + size) * ((y2 - y1) * M_PER_DEG + size) / size ** 2
            if cells > MAX_CELLS:
                raise Problem(422, f"That would make about {cells:,.0f} cells; the most is {MAX_CELLS:,}. Use a larger cell size "
                                   f"(at least {math.ceil(math.sqrt(w * (y2 - y1) * M_PER_DEG / MAX_CELLS)):,} m here).", code="validation",
                              fields={"params": ["size"]})
        fn = "ST_HexagonGrid" if params.get("shape") == "hexagon" else "ST_SquareGrid"
        keep = f"AND EXISTS (SELECT 1 FROM {src('input')} f WHERE ST_Intersects(f.geom, ST_Transform(c.geom, 4326)))" if params.get("touching", True) else ""
        # Web Mercator stretches by 1/cos(latitude): a cell of `size` metres on the ground is size/cos(lat) in its units
        sql = (f"WITH e AS (SELECT ST_Transform(ST_Envelope(ST_Collect(geom)), 3857) AS env, "
               f"cos(radians(ST_Y(ST_Centroid(ST_Envelope(ST_Collect(geom)))))) AS k FROM {src('input')} s) "
               f"SELECT ST_Transform(c.geom, 4326) AS g, jsonb_build_object('cell_id', row_number() OVER ()) AS p "
               f"FROM e, {fn}(%(size)s / e.k, e.env) c WHERE true {keep}")
        return Built(sql, args, "Polygon", [{"name": "cell_id", "type": "integer"}], name(f"grid {size:g} m"))

    if tool == "summarize_within":
        need_kind(a, ["Polygon"], "The input")
        b = layers["summarize"]
        sf = params.get("sum_field") or None
        newf = [{"name": "point_count" if b["geometry_type"] == "Point" else "count", "type": "integer"}]
        args["cname"] = newf[0]["name"]
        agg = "jsonb_build_object(%(cname)s::text, count(b.id))"
        if sf:
            ftype = next((f["type"] for f in b["fields"] if f["name"] == sf), None)
            if ftype not in ("integer", "number"):
                raise Problem(422, f"{sf} must be a number field of {b['name']}.", code="validation", fields={"params": ["sum_field"]})
            args["sf"], args["sname"] = sf, f"sum_{sf}"
            agg = ("jsonb_build_object(%(cname)s::text, count(b.id), %(sname)s::text, "
                   "COALESCE(sum(CASE WHEN jsonb_typeof(b.props->%(sf)s) = 'number' THEN (b.props->>%(sf)s)::numeric END), 0))")
            newf.append({"name": f"sum_{sf}", "type": "number"})
        sql = (f"SELECT poly.geom AS g, poly.props || s.v AS p FROM {src('input')} poly LEFT JOIN LATERAL "
               f"(SELECT {agg} AS v FROM {src('summarize')} b WHERE ST_Intersects(poly.geom, b.geom)) s ON true")
        return Built(sql, args, "Polygon", field_add(a["fields"], *newf), name("summary"))

    if tool == "calculate_geometry":
        k = a["geometry_type"]
        if k == "Polygon":
            add = ("jsonb_build_object('area_m2', round(ST_Area(geom::geography)::numeric, 2), 'area_ha', round((ST_Area(geom::geography) / 10000)::numeric, 4), "
                   "'perimeter_m', round(ST_Perimeter(geom::geography)::numeric, 2))")
            nf = [{"name": "area_m2", "type": "number"}, {"name": "area_ha", "type": "number"}, {"name": "perimeter_m", "type": "number"}]
        elif k == "LineString":
            add = "jsonb_build_object('length_m', round(ST_Length(geom::geography)::numeric, 2))"
            nf = [{"name": "length_m", "type": "number"}]
        else:
            add = "jsonb_build_object('longitude', round(ST_X(ST_PointOnSurface(geom))::numeric, 8), 'latitude', round(ST_Y(ST_PointOnSurface(geom))::numeric, 8))"
            nf = [{"name": "longitude", "type": "number"}, {"name": "latitude", "type": "number"}]
        sql = f"SELECT geom AS g, props || {add} AS p FROM {src('input')} s"
        return Built(sql, args, k, field_add(a["fields"], *nf), name("with geometry"))

    if tool == "copy":
        sql = f"SELECT geom AS g, props AS p FROM {src('input')} s"
        return Built(sql, args, a["geometry_type"], a["fields"], name("copy"))

    raise Problem(404, f"No tool called {tool}.", code="not_found")
