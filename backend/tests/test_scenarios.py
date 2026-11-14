"""Scenarios: each kind of simulation checked on small surfaces whose answers are known, plus making, varying,
re-running and comparing scenarios."""
import io
import json

import numpy as np
import pytest
import rasterio

from test_rasters import CELL, TO_LL, X0, Y0, pro, tif  # noqa: F401  (fixture)

N = 40


def ll(c, r):
    return list(TO_LL.transform(X0 + (c + 0.5) * CELL, Y0 - (r + 0.5) * CELL))


@pytest.fixture
def P(pro):
    return pro.post("/projects", json={"name": "What if"}).json()


def raster(c, P, name, arr, **kw):
    r = c.post(f"/projects/{P['id']}/layers/import", files={"file": (f"{name}.tif", tif(arr, **kw))})
    assert r.status_code == 201, r.text
    return r.json()[0]


def vector(c, P, name, geoms):
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": g, "properties": {"n": i}} for i, g in enumerate(geoms)]}
    r = c.post(f"/projects/{P['id']}/layers/import", files={"file": (f"{name}.geojson", json.dumps(fc).encode())})
    assert r.status_code == 201, r.text
    return r.json()[0]


def box(c0, r0, c1, r1):
    a, b = ll(c0 - 0.5, r0 - 0.5), ll(c1 - 0.5, r1 - 0.5)
    return {"type": "Polygon", "coordinates": [[a, [b[0], a[1]], b, [a[0], b[1]], a]]}


def scenario(c, P, kind, name, **params):
    r = c.post(f"/projects/{P['id']}/scenarios", json={"kind": kind, "name": name, "params": params})
    assert r.status_code == 201, r.text
    return r.json()


def run(c, s):
    r = c.post(f"/scenarios/{s['id']}/run")
    assert r.status_code == 200, r.text
    return r.json()


def bands(layer):
    from app import rasters
    with rasterio.open(rasters.local(layer["raster"]["key"])) as s:
        return s.read(masked=True).astype(float).filled(np.nan), list(s.descriptions)


def test_suitability_scores_and_excludes(pro, P):
    slope = np.tile(np.linspace(0, 30, N, dtype="float32"), (N, 1))          # flat in the west, steep in the east
    sl = raster(pro, P, "slope", slope)
    road = vector(pro, P, "road", [{"type": "LineString", "coordinates": [ll(0, 5), ll(N - 1, 5)]}])   # along the north
    park = vector(pro, P, "park", [box(0, 30, 10, 40)])                        # a protected corner in the south-west
    s = scenario(pro, P, "suitability", "Housing", criteria=[{"layer": sl["id"], "rule": "lower", "weight": 2},
                                                              {"layer": road["id"], "rule": "near", "weight": 1, "distance": 1000}],
                 exclude=[park["id"]], top_percent=10)
    out = run(pro, s)
    a, names = bands(out["layer"])
    assert names == ["Suitability (0–100)"] and a.shape == (1, N, N)
    assert a[0, 5, 1] > 95 and a[0, 35, 38] < 20                               # flat by the road; steep and far
    assert np.isnan(a[0, 35, 5])                                                # excluded
    sm = out["scenario"]["summary"]
    assert [c["share"] for c in sm["criteria"]] == [66.7, 33.3]
    assert sm["excluded_ha"] == pytest.approx(110 * 0.09, abs=1)   # cells the park touches and sm["best_sites"] >= 1
    assert sum(c["area_ha"] for c in sm["classes"]) == pytest.approx(sm["scored_ha"], abs=0.1)
    assert out["scenario"]["status"] == "ready" and out["layer"]["style"]["ramp"] == "Spectral"
    # a raster scored by distance is refused plainly
    bad = scenario(pro, P, "suitability", "Bad", criteria=[{"layer": sl["id"], "rule": "near"}])
    r = pro.post(f"/scenarios/{bad['id']}/run")
    assert r.status_code == 422 and "raster" in r.json()["detail"]
    assert pro.get(f"/projects/{P['id']}/scenarios").json()[-1]["status"] == "failed"


def test_flood_by_level_spreads_only_from_its_source(pro, P):
    z = np.full((N, N), 10.0, "float32")
    z[5:15, 5:15] = 2.0                                                          # the river's basin
    z[25:35, 25:35] = 1.0                                                        # a separate hollow, lower still but cut off
    dem = raster(pro, P, "dem", z)
    river = vector(pro, P, "river", [{"type": "Point", "coordinates": ll(10, 10)}])
    houses = vector(pro, P, "houses", [{"type": "Point", "coordinates": ll(7, 7)}, {"type": "Point", "coordinates": ll(20, 20)},
                                         {"type": "Point", "coordinates": ll(30, 30)}])
    s = scenario(pro, P, "flood", "River at 3 and 4 m", dem=dem["id"], method="level", levels="3, 4", sources=river["id"], assets=[houses["id"]])
    out = run(pro, s)
    a, names = bands(out["layer"])
    assert names == ["3 m", "4 m"]
    assert a[0, 10, 10] == pytest.approx(1.0) and a[1, 10, 10] == pytest.approx(2.0)   # depth = level − ground
    assert np.isnan(a[0, 30, 30])                                                       # the cut-off hollow stays dry
    st = out["scenario"]["summary"]["steps"]
    assert st[0]["area_ha"] == pytest.approx(100 * 0.09, abs=0.01) and st[0]["assets"] == {"houses": 1}
    # with no source every low place floods
    s2 = scenario(pro, P, "flood", "Anywhere", dem=dem["id"], method="level", levels=[3])
    a2, _ = bands(run(pro, s2)["layer"])
    assert a2[0, 30, 30] == pytest.approx(2.0)


def test_sea_level_rise_and_river_stage(pro, P):
    z = np.tile(np.linspace(-2, 8, N, dtype="float32"), (N, 1))                # sea in the west, rising inland
    z[15:25, 30:35] = 0.5                                                       # a low pocket inland, behind higher ground
    dem = raster(pro, P, "coast", z)
    s = scenario(pro, P, "flood", "Rise", dem=dem["id"], method="sea_rise", levels=[1], surge=0.5)
    a, names = bands(run(pro, s)["layer"])
    assert names == ["1 m rise"]
    edge = int(np.argmax(z[0] >= 1.5))
    assert np.isfinite(a[0, 0, edge - 1]) and np.isnan(a[0, 0, edge + 1])       # the sea reaches 1 m + 0.5 m surge
    assert np.isnan(a[0, 20, 32])                                                # the pocket is not connected to the sea
    # a river valley: the stage above the stream floods the valley floor first
    cols = np.abs(np.arange(N) - 20)[None, :].repeat(N, 0).astype("float32")
    valley = 50 + cols * 1.0 - np.arange(N)[:, None] * 0.2                       # V-shaped, sloping south
    v = raster(pro, P, "valley", valley.astype("float32"))
    s = scenario(pro, P, "flood", "Stage", dem=v["id"], method="river", levels=[2, 5], stream_km2=0.05)
    a, names = bands(run(pro, s)["layer"])
    assert names == ["2 m above the stream", "5 m above the stream"]
    row = a[:, 30]
    wide2, wide5 = np.isfinite(row[0]).sum(), np.isfinite(row[1]).sum()
    assert 3 <= wide2 <= 7 and wide5 > wide2 and np.isfinite(row[0, 20]) and np.isnan(row[0, 2])


def test_land_change_projects_growth_with_policies(pro, P):
    before = np.full((N, N), 1, "uint8")                                         # 1 forest, 2 built, 3 farmland
    before[:, 25:] = 3
    before[18:22, 18:22] = 2
    after = before.copy()
    after[16:24, 16:24] = 2                                                      # the town grew: 64 cells from 16
    b, a = raster(pro, P, "lc2010", before), raster(pro, P, "lc2020", after)
    s = scenario(pro, P, "land_change", "Trend", before=b["id"], after=a["id"], before_year=2010, after_year=2020, years="2030, 2040")
    out = run(pro, s)
    x, names = bands(out["layer"])
    assert names == ["2030", "2040"]
    built = [(x[i] == 2).sum() for i in range(2)]
    assert 64 < built[0] < built[1]                                              # keeps growing
    grown = (x[0] == 2) & (after != 2)
    assert grown.any() and np.all(np.abs(np.argwhere(grown) - 20).max(1) <= 10)  # next to the town, not scattered
    sm = out["scenario"]["summary"]
    assert sm["start"]["areas_ha"]["2"] == pytest.approx(64 * 0.09, abs=0.01) and len(sm["steps"]) == 2
    assert sm["steps"][0]["areas_ha"]["2"] == pytest.approx(built[0] * 0.09, abs=0.01)
    # policy: forest protected around the town, and growth doubled
    ring = vector(pro, P, "reserve", [box(0, 0, 25, 40)])
    s2 = scenario(pro, P, "land_change", "Protect the forest", before=b["id"], after=a["id"], before_year=2010, after_year=2020, years=[2030],
                  protect=[ring["id"]])
    y, _ = bands(run(pro, s2)["layer"])
    assert ((y[0] == 2) & (after != 2))[:, :25].sum() == 0                      # nothing new inside the reserve
    s3 = scenario(pro, P, "land_change", "Fast growth", before=b["id"], after=a["id"], before_year=2010, after_year=2020, years=[2030],
                  demand={"2": 2})
    z, _ = bands(run(pro, s3)["layer"])
    assert (z[0] == 2).sum() > built[0] * 1.3


def test_spread_with_wind_and_barriers(pro, P):
    src = vector(pro, P, "ignition", [{"type": "Point", "coordinates": ll(20, 20)}])
    s = scenario(pro, P, "spread", "Calm", sources=src["id"], speed=300, hours="1, 2", cell=30)
    out = run(pro, s)
    a, names = bands(out["layer"])
    assert names == ["1 h", "2 h"]
    from app.raster_style import RAMPS
    assert out["layer"]["style"]["ramp"] in RAMPS                               # a ramp the map knows
    st = out["scenario"]["summary"]["steps"]
    import math
    assert st[0]["area_ha"] == pytest.approx(math.pi * 300 ** 2 / 1e4, rel=0.15)    # a circle 300 m across in an hour
    assert st[1]["area_ha"] == pytest.approx(4 * st[0]["area_ha"], rel=0.15)
    windy = scenario(pro, P, "spread", "West wind", sources=src["id"], speed=300, hours=[1], cell=30, wind_from=270, wind_strength=0.6)
    w, _ = bands(run(pro, windy)["layer"])
    mid = w.shape[1] // 2
    row = np.isfinite(w[0, mid])
    cols = np.flatnonzero(row)
    assert cols.max() - mid > mid - cols.min()                                   # pushed east, downwind
    # a barrier (a river) stops it
    wall = vector(pro, P, "river", [{"type": "LineString", "coordinates": [ll(23, 0), ll(23, 40)]}])
    s3 = scenario(pro, P, "spread", "River", sources=src["id"], speed=300, hours=[2], cell=30, barriers=[wall["id"]],
                  area=vector(pro, P, "area", [box(5, 5, 35, 35)])["id"])
    b3, _ = bands(run(pro, s3)["layer"])
    assert np.isfinite(b3[0]).any() and not np.isfinite(b3[0][:, 24:]).any()


def test_varying_rerunning_and_comparing(pro, P):
    z = np.tile(np.linspace(0, 10, N, dtype="float32"), (N, 1))
    dem = raster(pro, P, "terrain", z)
    s = scenario(pro, P, "flood", "Low", dem=dem["id"], method="level", levels=[2])
    first = run(pro, s)
    v = pro.post(f"/scenarios/{s['id']}/duplicate")
    assert v.status_code == 201 and v.json()["name"] == "Low (variant)" and v.json()["status"] == "draft"
    v = pro.patch(f"/scenarios/{v.json()['id']}", json={"name": "High", "params": {**s["params"], "levels": [6]}}).json()
    second = run(pro, v)
    assert second["scenario"]["summary"]["steps"][0]["area_ha"] > first["scenario"]["summary"]["steps"][0]["area_ha"]
    # changing settings marks it as needing a run; running again redraws the same layer
    s2 = pro.patch(f"/scenarios/{s['id']}", json={"params": {**s["params"], "levels": [3]}}).json()
    assert s2["status"] == "draft"
    again = run(pro, s2)
    assert again["layer"]["id"] == first["layer"]["id"] and again["scenario"]["summary"]["steps"][0]["label"] == "3 m"
    listed = pro.get(f"/projects/{P['id']}/scenarios").json()
    assert [x["name"] for x in listed] == ["Low", "High"]
    assert pro.delete(f"/scenarios/{s['id']}").status_code == 204
    assert first["layer"]["id"] in {x["id"] for x in pro.get(f"/projects/{P['id']}").json()["layers"]}   # its layer stays
    assert [k["id"] for k in pro.get("/scenario-kinds").json()] == ["suitability", "flood", "land_change", "spread"]
