"""The geoprocessing tools, checked against numbers worked out independently: areas and distances on the ellipsoid
(pyproj's Geod), counts, attributes carried over, selections, the plan, and failed runs kept in the history."""
import json
import math

import pytest
from pyproj import Geod

GEOD = Geod(ellps="WGS84")

POINTS = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"name": "A", "kind": "school", "pupils": 300}, "geometry": {"type": "Point", "coordinates": [7.4600, 9.0700]}},
    {"type": "Feature", "properties": {"name": "B", "kind": "school", "pupils": 500}, "geometry": {"type": "Point", "coordinates": [7.4650, 9.0720]}},
    {"type": "Feature", "properties": {"name": "C", "kind": "clinic", "pupils": 0}, "geometry": {"type": "Point", "coordinates": [7.4900, 9.0900]}},
]}
# two plots: one around A and B, one far away; a road across the first
PLOTS = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"plot": "P1", "use": "farm"},
     "geometry": {"type": "Polygon", "coordinates": [[[7.455, 9.065], [7.470, 9.065], [7.470, 9.075], [7.455, 9.075], [7.455, 9.065]]]}},
    {"type": "Feature", "properties": {"plot": "P2", "use": "farm"},
     "geometry": {"type": "Polygon", "coordinates": [[[7.480, 9.080], [7.485, 9.080], [7.485, 9.085], [7.480, 9.085], [7.480, 9.080]]]}},
]}
ROAD = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"road": "R1"}, "geometry": {"type": "LineString", "coordinates": [[7.450, 9.070], [7.475, 9.070]]}}]}


@pytest.fixture
def pro(person):
    return person(role="member", name="Tunde Analyst", features=["maps", "editing", "export", "geoprocessing"])


@pytest.fixture
def setup(pro):
    p = pro.post("/projects", json={"name": "Analysis"}).json()

    def add(name, fc):
        r = pro.post(f"/projects/{p['id']}/layers/import", files={"file": (name, json.dumps(fc).encode())})
        assert r.status_code == 201, r.text
        return r.json()[0]
    return p, add("schools.geojson", POINTS), add("plots.geojson", PLOTS), add("road.geojson", ROAD)


def run(c, project, tool, **params):
    r = c.post(f"/projects/{project['id']}/tools/{tool}", json={"params": params})
    assert r.status_code == 201, (tool, r.text)
    return r.json()


def rows(c, layer):
    return [r["props"] for r in c.post(f"/layers/{layer['id']}/rows", json={"limit": 1000}).json()["rows"]]


def geoms(c, layer):
    ids = [r["id"] for r in c.post(f"/layers/{layer['id']}/rows", json={"limit": 1000}).json()["rows"]]
    return [f["geometry"] for f in c.post(f"/layers/{layer['id']}/features/geojson", json=ids).json()["features"]]


def area(g):
    rings = g["coordinates"] if g["type"] == "Polygon" else [r for poly in g["coordinates"] for r in poly]
    return abs(GEOD.polygon_area_perimeter(*zip(*rings[0]))[0])


def test_the_plan_and_the_catalog(person, setup):
    p, pts, *_ = setup
    tools = person(role="member").get("/tools").json()
    assert len(tools) >= 18 and {"buffer", "clip", "intersect", "dissolve", "spatial_join"} <= {t["id"] for t in tools}
    std = person(role="member", features=["maps", "editing", "export"])
    r = std.post(f"/projects/{p['id']}/tools/buffer", json={"params": {"input": pts["id"], "distance": 10}})
    assert r.status_code == 403 and "PolyGIS Advanced" in r.json()["detail"]
    viewer = person(role="viewer", features=["geoprocessing"])
    assert viewer.post(f"/projects/{p['id']}/tools/buffer", json={"params": {"input": pts["id"], "distance": 10}}).status_code == 403


def test_buffer_is_true_metres(pro, setup):
    p, pts, *_ = setup
    out = run(pro, p, "buffer", input=pts["id"], distance=100)
    assert out["layer"]["geometry_type"] == "Polygon" and out["layer"]["feature_count"] == 3 and out["run"]["status"] == "done"
    assert {r["name"] for r in rows(pro, out["layer"])} == {"A", "B", "C"}
    # a 100 m circle is π·100² ≈ 31,416 m² on the ground (32 segments lose about 0.6 %)
    for g in geoms(pro, out["layer"]):
        assert area(g) == pytest.approx(math.pi * 100 ** 2, rel=0.01)
    # dissolved: A and B (about 590 m apart) overlap with 400 m buffers, C does not: one multi-part feature
    d = run(pro, p, "buffer", input=pts["id"], distance=400, dissolve=True)
    assert d["layer"]["feature_count"] == 1 and d["layer"]["fields"] == []
    # only the selected features
    ids = [r["id"] for r in pro.post(f"/layers/{pts['id']}/rows", json={"filters": [{"field": "kind", "op": "=", "value": "clinic"}]}).json()["rows"]]
    one = run(pro, p, "buffer", input=pts["id"], input_ids=ids, distance=50)
    assert one["layer"]["feature_count"] == 1 and rows(pro, one["layer"])[0]["name"] == "C"


def test_overlay_tools(pro, setup):
    p, pts, plots, road = setup
    clip = run(pro, p, "clip", input=road["id"], clip=plots["id"])
    g = geoms(pro, clip["layer"])[0]
    # the road enters plot 1 at its west edge (7.455) and leaves at its east edge (7.470)
    xs = sorted(x for x, _ in g["coordinates"])
    assert xs[0] == pytest.approx(7.455) and xs[-1] == pytest.approx(7.470)
    erase = run(pro, p, "erase", input=road["id"], erase=plots["id"])
    assert erase["layer"]["geometry_type"] == "LineString" and erase["layer"]["feature_count"] == 1
    inter = run(pro, p, "intersect", input=pts["id"], overlay=plots["id"])
    assert inter["layer"]["geometry_type"] == "Point"
    got = sorted((r["name"], r["plot"]) for r in rows(pro, inter["layer"]))
    assert got == [("A", "P1"), ("B", "P1")]
    # clashing field names get a suffix
    both = run(pro, p, "intersect", input=plots["id"], overlay=plots["id"])
    assert {"plot", "plot_2", "use", "use_2"} <= {f["name"] for f in both["layer"]["fields"]}
    m = pro.post(f"/projects/{p['id']}/tools/merge", json={"params": {"layers": [plots["id"], road["id"]]}})
    assert m.status_code == 422 and "same kind" in m.json()["detail"]
    b = run(pro, p, "buffer", input=pts["id"], distance=10)
    mm = run(pro, p, "merge", layers=[plots["id"], b["layer"]["id"]])
    assert mm["layer"]["feature_count"] == 5 and {"source_layer", "plot", "name"} <= {f["name"] for f in mm["layer"]["fields"]}


def test_proximity_and_statistics(pro, setup):
    p, pts, plots, road = setup
    near = run(pro, p, "near", input=pts["id"], near=road["id"])
    d = {r["name"]: r["near_dist_m"] for r in rows(pro, near["layer"])}
    # A sits on the road; B is 0.002° north of it; C is beyond the road's east end
    # A is on the road's line of latitude; the road between its ends follows the ellipsoid and bows a few cm north
    assert d["A"] == pytest.approx(0, abs=0.05)
    assert d["B"] == pytest.approx(GEOD.inv(7.465, 9.070, 7.465, 9.072)[2], rel=0.002)
    assert d["C"] == pytest.approx(GEOD.inv(7.475, 9.070, 7.490, 9.090)[2], rel=0.01)
    sj = run(pro, p, "spatial_join", input=plots["id"], join=pts["id"])
    by = {r["plot"]: r for r in rows(pro, sj["layer"])}
    assert by["P1"]["join_count"] == 2 and by["P1"]["name"] in ("A", "B") and by["P2"]["join_count"] == 0
    sw = run(pro, p, "summarize_within", input=plots["id"], summarize=pts["id"], sum_field="pupils")
    by = {r["plot"]: r for r in rows(pro, sw["layer"])}
    assert by["P1"]["point_count"] == 2 and by["P1"]["sum_pupils"] == 800 and by["P2"]["point_count"] == 0
    cg = run(pro, p, "calculate_geometry", input=plots["id"])
    p1 = next(r for r in rows(pro, cg["layer"]) if r["plot"] == "P1")
    want = abs(GEOD.polygon_area_perimeter([7.455, 7.470, 7.470, 7.455], [9.065, 9.065, 9.075, 9.075])[0])
    assert p1["area_m2"] == pytest.approx(want, rel=1e-4) and p1["area_ha"] == pytest.approx(want / 10000, rel=1e-3)
    ln = run(pro, p, "calculate_geometry", input=road["id"])
    assert rows(pro, ln["layer"])[0]["length_m"] == pytest.approx(GEOD.inv(7.450, 9.070, 7.475, 9.070)[2], rel=1e-4)


def test_generalization_and_conversion(pro, setup):
    p, pts, plots, road = setup
    dis = run(pro, p, "dissolve", input=plots["id"], fields=["use"])
    assert dis["layer"]["feature_count"] == 1 and rows(pro, dis["layer"])[0] == {"use": "farm", "count": 2}
    assert run(pro, p, "dissolve", input=pts["id"])["layer"]["feature_count"] == 1
    hull = run(pro, p, "convex_hull", input=pts["id"])
    assert hull["layer"]["feature_count"] == 1 and rows(pro, hull["layer"])[0]["count"] == 3
    env = run(pro, p, "envelope", input=plots["id"], each=True)
    assert env["layer"]["feature_count"] == 2
    cen = run(pro, p, "centroids", input=plots["id"])
    assert cen["layer"]["geometry_type"] == "Point" and geoms(pro, cen["layer"])[0]["type"] == "Point"
    out = run(pro, p, "boundary", input=plots["id"])
    assert out["layer"]["geometry_type"] == "LineString" and out["layer"]["feature_count"] == 2
    ex = run(pro, p, "explode", input=run(pro, p, "dissolve", input=plots["id"])["layer"]["id"])
    assert ex["layer"]["feature_count"] == 2
    vor = run(pro, p, "voronoi", input=pts["id"])
    assert vor["layer"]["feature_count"] == 3 and {r["name"] for r in rows(pro, vor["layer"])} == {"A", "B", "C"}
    grid = run(pro, p, "grid", input=plots["id"], size=500, touching=True)
    cells = geoms(pro, grid["layer"])
    assert grid["layer"]["feature_count"] >= 2
    # cells are 500 m on the ground at this latitude, not 500 Web Mercator units
    assert area(cells[0]) == pytest.approx(500 * 500, rel=0.01)
    simp = run(pro, p, "simplify", input=plots["id"], tolerance=5)
    assert simp["layer"]["feature_count"] == 2
    with_bad = pro.post(f"/projects/{p['id']}/tools/simplify", json={"params": {"input": pts["id"], "tolerance": 5}})
    assert with_bad.status_code == 422 and "lines or polygons" in with_bad.json()["detail"]
    cp = run(pro, p, "copy", input=pts["id"])
    assert cp["layer"]["feature_count"] == 3


def test_history_keeps_failures(pro, setup, monkeypatch):
    from app.config import settings
    p, pts, plots, road = setup
    run(pro, p, "copy", input=pts["id"])
    # wrong settings are refused before anything runs, with what is wrong
    bad = pro.post(f"/projects/{p['id']}/tools/buffer", json={"params": {"input": pts["id"], "distance": "far"}})
    assert bad.status_code == 422 and "Distance must be a number" in bad.json()["detail"]
    huge = pro.post(f"/projects/{p['id']}/tools/grid", json={"params": {"input": plots["id"], "size": 1, "touching": False}})
    assert huge.status_code == 422 and "cells" in huge.json()["detail"]
    # a tool that fails while running leaves no half-made layer, and the failure is kept with the reason
    monkeypatch.setattr(settings, "tool_timeout_s", 0.001)
    slow = pro.post(f"/projects/{p['id']}/tools/grid", json={"params": {"input": plots["id"], "size": 20, "touching": True}})
    assert slow.status_code == 422 and "longer than" in slow.json()["detail"]
    names = [l["name"] for l in pro.get(f"/projects/{p['id']}").json()["layers"]]
    assert not any("grid" in n for n in names)
    h = pro.get(f"/projects/{p['id']}/runs").json()
    assert [(x["tool"], x["status"]) for x in h] == [("grid", "failed"), ("copy", "done")]
    assert h[1]["feature_count"] == 3 and h[1]["actor_name"] == "Tunde Analyst" and h[1]["output_layer_id"]
    # a layer from another project cannot be used
    other = pro.post("/projects", json={"name": "Other"}).json()
    r = pro.post(f"/projects/{other['id']}/tools/copy", json={"params": {"input": pts["id"]}})
    assert r.status_code == 422 and "another project" in r.json()["detail"]
