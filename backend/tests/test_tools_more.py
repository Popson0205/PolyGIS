"""The wider toolbox: every tool runs on real layers, and the numbers that matter are checked against independent
calculations (pyproj's Geod for metres, plain numpy for the statistics, hand-counted networks)."""
import json
import math

import numpy as np
import pytest
from pyproj import Geod, Transformer

from tests.test_tools import area, geoms, rows, run

GEOD = Geod(ellps="WGS84")


def fc(*feats):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": p, "geometry": g} for g, p in feats]}


def pt(x, y):
    return {"type": "Point", "coordinates": [x, y]}


def sq(x, y, s):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]]}


def line(*cs):
    return {"type": "LineString", "coordinates": [list(c) for c in cs]}


# twenty points: a tight cluster of high values in the south-west, low values scattered to the north-east
rng = np.random.default_rng(7)
PTS = [(7.40 + rng.random() * 0.01, 9.00 + rng.random() * 0.01, round(100 + rng.random() * 10, 3)) for _ in range(10)] + \
      [(7.45 + rng.random() * 0.05, 9.05 + rng.random() * 0.05, round(rng.random() * 10, 3)) for _ in range(10)]
POINTS = fc(*[(pt(x, y), {"n": i, "value": round(v, 3), "grp": "a" if i < 10 else "b"}) for i, (x, y, v) in enumerate(PTS)])
# two overlapping squares and a small one; a 3 x 3 street grid (0.01° spacing) for the network tools
POLYS = fc((sq(7.40, 9.00, 0.02), {"zone": "Z1", "rate": 5}), (sq(7.41, 9.01, 0.02), {"zone": "Z2", "rate": 7}),
           (sq(7.45, 9.00, 0.0005), {"zone": "tiny", "rate": 1}))
STREETS = fc(*[(line((7.40, 9.00 + i * 0.01), (7.42, 9.00 + i * 0.01)), {"street": f"H{i}"}) for i in range(3)],
             *[(line((7.40 + i * 0.01, 9.00), (7.40 + i * 0.01, 9.02)), {"street": f"V{i}"}) for i in range(3)])
STOPS = fc((pt(7.40, 9.00), {"seq": 1, "name": "start"}), (pt(7.42, 9.02), {"seq": 2, "name": "end"}))
FAC = fc((pt(7.40, 9.00), {"fac": "F-SW"}), (pt(7.42, 9.02), {"fac": "F-NE"}))
TILES = fc((sq(7.40, 9.00, 0.01), {"t": 1}), (sq(7.41, 9.00, 0.01), {"t": 2}), (sq(7.40, 9.01, 0.01), {"t": 3}))
OTHER = fc((sq(7.41, 9.00, 0.02), {"zone": "O1", "soil": "loam"}))
INC = fc((pt(7.401, 9.001), {"inc": 1}), (pt(7.419, 9.019), {"inc": 2}))


@pytest.fixture
def pro(person):
    return person(role="member", name="Ada Analyst", features=["maps", "editing", "export", "geoprocessing"])


@pytest.fixture
def L(pro):
    p = pro.post("/projects", json={"name": "Toolbox"}).json()

    def add(name, data):
        r = pro.post(f"/projects/{p['id']}/layers/import", files={"file": (f"{name}.geojson", json.dumps(data).encode())})
        assert r.status_code == 201, r.text
        return r.json()[0]
    return {"p": p, "pts": add("pts", POINTS), "polys": add("polys", POLYS), "streets": add("streets", STREETS),
            "stops": add("stops", STOPS), "fac": add("fac", FAC), "inc": add("inc", INC),
            "other": add("other", OTHER), "tiles": add("tiles", TILES)}


def go(pro, L, tool, **params):
    params = {k: (L[v]["id"] if isinstance(v, str) and v in L and k not in ("field", "group", "order", "weight", "key", "other_key", "zone_field", "class_field", "bearing", "distance") else v) for k, v in params.items()}
    return run(pro, L["p"], tool, **params)


def net_area(g):
    """Area with the holes taken out."""
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    return sum(abs(GEOD.polygon_area_perimeter(*zip(*r))[0]) * (1 if i == 0 else -1) for poly in polys for i, r in enumerate(poly))


# one run of every tool with sensible settings: each must make a layer of the right kind
EVERY = {
    "multi_buffer": dict(input="pts", distances="50, 100"), "buffer_by_field": dict(input="pts", field="value"),
    "connect_nearest": dict(input="pts", near="polys"), "k_nearest": dict(input="pts", near="pts", k=2),
    "point_distance": dict(input="pts", near="pts", distance=300), "points_along_lines": dict(input="streets", interval=500, ends=True),
    "extract_by_location": dict(input="pts", other="polys", relation="within"),
    "extract_by_attribute": dict(input="pts", field="grp", op="=", value="a"),
    "union": dict(input="polys", overlay="other"), "identity": dict(input="polys", overlay="other"),
    "symmetrical_difference": dict(input="polys", overlay="other"), "update": dict(input="polys", overlay="other"),
    "split_by_lines": dict(input="polys", blade="streets"), "smooth": dict(input="polys", iterations=2),
    "densify": dict(input="streets", distance=100), "concave_hull": dict(input="pts", ratio=0.5),
    "min_circle": dict(input="polys"), "oriented_box": dict(input="pts", each=False), "eliminate": dict(input="polys", area=1000),
    "remove_small": dict(input="polys", area=1000), "fill_holes": dict(input="polys"), "aggregate_polygons": dict(input="polys", distance=100),
    "snap": dict(input="pts", target="streets", tolerance=50), "vertices": dict(input="polys"), "endpoints": dict(input="streets"),
    "midpoints": dict(input="streets"), "line_intersections": dict(input="streets", other="streets"),
    "split_at_vertices": dict(input="polys"), "split_lines_at_points": dict(input="streets", points="stops", tolerance=1),
    "lines_to_polygons": dict(input="streets"), "polygonize": dict(input="streets"), "points_to_line": dict(input="pts", order="n", group="grp"),
    "reverse_lines": dict(input="streets"), "offset_lines": dict(input="streets", distance=20), "delaunay": dict(input="pts"),
    "divide_lines": dict(input="streets", parts=4), "move": dict(input="pts", dx=100, dy=-50), "rotate": dict(input="polys", angle=45),
    "scale": dict(input="polys", factor=2), "repair_geometry": dict(input="polys"), "check_geometry": dict(input="polys"),
    "find_overlaps": dict(input="polys"), "find_gaps": dict(input="polys"), "find_identical": dict(input="pts"),
    "delete_duplicates": dict(input="pts"), "find_slivers": dict(input="polys", thinness=0.9),
    "random_points": dict(input="polys", count=5, seed=3), "random_points_extent": dict(input="pts", count=50, seed=3),
    "regular_points": dict(input="polys", spacing=500), "bin_points": dict(input="pts", size=1000, sum_field="value"),
    "mean_center": dict(input="pts", group="grp"), "median_center": dict(input="pts"), "central_feature": dict(input="pts"),
    "standard_distance": dict(input="pts"), "deviational_ellipse": dict(input="pts"), "kmeans": dict(input="pts", k=2),
    "dbscan": dict(input="pts", distance=1500, min_points=3), "nearest_neighbor_index": dict(input="pts"),
    "morans_i": dict(input="pts", field="value", neighbours="knn", k=4), "local_morans_i": dict(input="pts", field="value", neighbours="knn", k=4),
    "hotspots": dict(input="pts", field="value", neighbours="distance", distance=2000), "summary_statistics": dict(input="pts", field="value", group="grp"),
    "route": dict(network="streets", stops="stops", order="seq"), "closest_facility": dict(network="streets", incidents="inc", facilities="fac"),
    "service_area": dict(network="streets", facilities="fac", distances="1200"), "service_area_polygons": dict(network="streets", facilities="fac", distances="2300"),
    "od_matrix": dict(network="streets", origins="inc", destinations="fac"),
    "join_field": dict(input="polys", key="zone", other="other", other_key="zone"), "delete_fields": dict(input="pts", fields=["n"]),
    "sort": dict(input="pts", field="value", descending=True), "add_xy": dict(input="pts", epsg="26332"),
    "polygon_neighbors": dict(input="tiles"), "tabulate_intersection": dict(input="polys", zone_field="zone", classes="other", class_field="soil"),
    "count_overlaps": dict(input="polys"), "remove_overlaps": dict(input="polys", keep="larger"), "shape_metrics": dict(input="polys"),
    "line_direction": dict(input="streets"), "split_by_length": dict(input="streets", length=300), "transects": dict(input="streets", interval=500, length=40),
    "bearing_distance": dict(input="pts", bearing="value", distance="value"), "wedges": dict(input="pts", radius=200, direction=45, width=90),
    "snap_to_lines": dict(input="pts", lines="streets", tolerance=2000), "thin_points": dict(input="pts", distance=1000),
    "unsplit_lines": dict(input="streets"), "find_dangles": dict(input="streets", what="junctions"), "spanning_tree": dict(input="pts"),
    "optimal_route": dict(network="streets", stops="inc"), "allocate": dict(network="streets", incidents="inc", facilities="fac"),
}


@pytest.mark.parametrize("tool", sorted(EVERY))
def test_every_tool_runs(pro, L, tool):
    out = go(pro, L, tool, **EVERY[tool])
    # the quality checks find nothing wrong in clean data, which is the right answer
    may_be_empty = {"check_geometry", "find_gaps", "find_identical", "lines_to_polygons"}
    assert out["run"]["status"] == "done" and (out["layer"]["feature_count"] > 0 or tool in may_be_empty), (tool, out["layer"])


def test_every_registered_tool_is_covered(pro):
    ids = {t["id"] for t in pro.get("/tools").json()}
    from app.tools import tools_more
    assert set(tools_more.TOOLS) <= set(EVERY), set(tools_more.TOOLS) - set(EVERY)
    assert len(ids) >= 100


def test_multi_ring_areas(pro, L):
    out = go(pro, L, "multi_buffer", input="pts", distances="100, 200")
    rs = rows(pro, out["layer"])
    gs = geoms(pro, out["layer"])
    inner = [area(g) for g, r in zip(gs, rs) if r["distance_m"] == 100]
    outer = [net_area(g) for g, r in zip(gs, rs) if r["distance_m"] == 200]
    assert len(inner) == len(outer) == 20
    # a ring from 100 to 200 m is π(200² − 100²) ≈ 94,248 m²; the buffer's polygons lose < 1 %
    assert all(abs(a / (math.pi * 100 ** 2) - 1) < 0.01 for a in inner)
    assert all(abs(a / (math.pi * (200 ** 2 - 100 ** 2)) - 1) < 0.01 for a in outer)


def test_points_along_and_densify(pro, L):
    out = go(pro, L, "points_along_lines", input="streets", interval=500, ends=True)
    # each street: 0.02° long; work out its true length and the points a 500 m step makes, plus the end
    want = 0
    for f in STREETS["features"]:
        (x1, y1), (x2, y2) = f["geometry"]["coordinates"]
        length = GEOD.line_length([x1, x2], [y1, y2])
        want += math.floor(length / 500) + 1 + (1 if length % 500 > 0.001 else 0)
    assert out["layer"]["feature_count"] == want
    d = go(pro, L, "densify", input="streets", distance=100)
    for g in geoms(pro, d["layer"]):
        xs, ys = zip(*g["coordinates"])
        assert max(GEOD.line_lengths(xs, ys)) <= 100.001


def test_statistics_match_numpy(pro, L):
    t = Transformer.from_crs(4326, 32632, always_xy=True)
    xy = np.array([t.transform(x, y) for x, y, _ in PTS])
    # standard distance: sqrt(mean squared distance to the mean centre)
    sd = go(pro, L, "standard_distance", input="pts")
    want = math.sqrt(((xy - xy.mean(0)) ** 2).sum(1).mean())
    assert abs(rows(pro, sd["layer"])[0]["radius_m"] - want) < 0.05
    # mean centre per group
    mc = go(pro, L, "mean_center", input="pts", group="grp")
    got = {r["grp"]: g["coordinates"] for r, g in zip(rows(pro, mc["layer"]), geoms(pro, mc["layer"]))}
    a = np.array([(x, y) for x, y, _ in PTS[:10]])
    assert GEOD.inv(*got["a"], *a.mean(0))[2] < 1  # within a metre of the plain average
    # Moran's I by hand: k=4 nearest, row-standardised
    v = np.array([p[2] for p in PTS])
    z = v - v.mean()
    d = np.sqrt(((xy[:, None] - xy[None]) ** 2).sum(-1))
    np.fill_diagonal(d, np.inf)
    nb = np.argsort(d, 1)[:, :4]
    want_i = (z * z[nb].mean(1)).sum() / (z ** 2).sum()
    mi = rows(pro, go(pro, L, "morans_i", input="pts", field="value", neighbours="knn", k=4)["layer"])[0]
    assert abs(mi["morans_i"] - want_i) < 1e-5 and mi["pattern"] == "Clustered" and mi["p_value"] < 0.01
    # hot spots: the high cluster is hot, the low one is not
    hs = rows(pro, go(pro, L, "hotspots", input="pts", field="value", neighbours="knn", k=5)["layer"])
    assert all(r["gi_z"] > 1.96 for r in hs if r["grp"] == "a") and all(r["gi_z"] < 0 for r in hs if r["grp"] == "b")
    lm = rows(pro, go(pro, L, "local_morans_i", input="pts", field="value", neighbours="knn", k=5)["layer"])
    assert {r["cluster"] for r in lm if r["grp"] == "a"} <= {"HH", "Not significant"} and any(r["cluster"] == "HH" for r in lm)
    st = {r["grp"]: r for r in rows(pro, go(pro, L, "summary_statistics", input="pts", field="value", group="grp")["layer"])}
    assert abs(st["a"]["mean"] - v[:10].mean()) < 1e-4 and st["b"]["count"] == 10 and abs(st["a"]["std"] - v[:10].std()) < 1e-4
    nn = rows(pro, go(pro, L, "nearest_neighbor_index", input="pts")["layer"])[0]
    assert abs(nn["observed_m"] - d.min(1).mean()) < 0.05


def test_network(pro, L):
    # from one corner of the grid to the other: 0.02° east plus 0.02° north along the streets
    r = rows(pro, go(pro, L, "route", network="streets", stops="stops", order="seq")["layer"])[0]
    east = GEOD.line_length([7.40, 7.42], [9.00, 9.00])
    north = GEOD.line_length([7.40, 7.40], [9.00, 9.02])
    assert abs(r["length_m"] - (east + north)) < 1
    cf = {x["incident_id"]: x for x in rows(pro, go(pro, L, "closest_facility", network="streets", incidents="inc", facilities="fac")["layer"])}
    assert sorted(x["fac"] for x in cf.values()) == ["F-NE", "F-SW"]
    od = rows(pro, go(pro, L, "od_matrix", network="streets", origins="inc", destinations="fac")["layer"])
    assert len(od) == 4 and max(x["network_m"] for x in od) == pytest.approx(east + north, abs=1)
    # within ~1.2 km of a corner: two street segments of ~1.1 km each touch it
    sa = go(pro, L, "service_area", network="streets", facilities="fac", distances="1200")
    assert sa["layer"]["feature_count"] == 2


def test_overlay_areas_add_up(pro, L):
    # union of the two overlapping squares (with themselves as overlay) covers what they cover: total = sum of faces
    u = go(pro, L, "union", input="polys", overlay="polys")
    total = sum(area(g) for g in geoms(pro, u["layer"]))
    want = area(POLYS["features"][0]["geometry"]) * 2 - area(sq(7.41, 9.01, 0.01)) + area(POLYS["features"][2]["geometry"])
    assert abs(total / want - 1) < 1e-3
    ov = rows(pro, go(pro, L, "find_overlaps", input="polys")["layer"])
    assert len(ov) == 1 and abs(ov[0]["area_m2"] / area(sq(7.41, 9.01, 0.01)) - 1) < 1e-3
    e = go(pro, L, "remove_small", input="polys", area=5000)
    assert e["layer"]["feature_count"] == 2


def test_bad_settings_say_so(pro, L):
    r = pro.post(f"/projects/{L['p']['id']}/tools/multi_buffer", json={"params": {"input": L["pts"]["id"], "distances": "ten"}})
    assert r.status_code == 422 and "numbers" in r.json()["detail"]
    r = pro.post(f"/projects/{L['p']['id']}/tools/route", json={"params": {"network": L["pts"]["id"], "stops": L["stops"]["id"]}})
    assert r.status_code == 422 and "lines" in r.json()["detail"]


def test_neighbours_junctions_and_shapes(pro, L):
    nb = rows(pro, go(pro, L, "polygon_neighbors", input="tiles")["layer"])
    # tile 1 touches 2 (a 0.01° meridian edge) and 3 (a 0.01° parallel edge); 2 and 3 meet only at a corner
    want = {(1, 2): GEOD.line_length([7.41, 7.41], [9.00, 9.01]), (1, 3): GEOD.line_length([7.40, 7.41], [9.01, 9.01])}
    ids = {r["id"]: r["props"]["t"] for r in pro.post(f"/layers/{L['tiles']['id']}/rows", json={"limit": 10}).json()["rows"]}
    got = {tuple(sorted((ids[r["id_1"]], ids[r["id_2"]]))): r["shared_m"] for r in nb if r["shared_m"] > 0}
    assert got.keys() == want.keys() and all(abs(got[k] - want[k]) < 0.01 for k in want)
    # the 3 x 3 grid: four T-junctions on the edges and one crossing in the middle
    j = rows(pro, go(pro, L, "find_dangles", input="streets", what="junctions")["layer"])
    assert sorted(r["lines"] for r in j) == [3, 3, 3, 3, 4]
    assert go(pro, L, "find_dangles", input="streets")["layer"]["feature_count"] == 0
    m = {r["zone"]: r for r in rows(pro, go(pro, L, "shape_metrics", input="polys")["layer"])}
    assert abs(m["Z1"]["compactness"] - math.pi / 4) < 0.01       # a square: 4πA/P² = π/4
    tab = rows(pro, go(pro, L, "tabulate_intersection", input="polys", zone_field="zone", classes="other", class_field="soil")["layer"])
    z1 = next(r for r in tab if r["zone"] == "Z1")
    assert abs(z1["percent"] - 50) < 0.1                            # the other square covers the east half of Z1
    mst = rows(pro, go(pro, L, "spanning_tree", input="pts")["layer"])
    assert len(mst) == 19
    # bearing lines: the line's geodesic length is the distance field
    bl = go(pro, L, "bearing_distance", input="pts", bearing="value", distance="value")
    for r, g in zip(rows(pro, bl["layer"]), geoms(pro, bl["layer"])):
        (x1, y1), (x2, y2) = g["coordinates"]
        az, _, d = GEOD.inv(x1, y1, x2, y2)
        assert abs(d - r["value"]) < 0.02
        if r["value"] > 50:                                         # bearings of very short lines suffer from the coordinates' rounding
            assert abs((az - r["value"] + 180) % 360 - 180) < 0.01
