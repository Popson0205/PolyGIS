"""Military visibility: line of sight with its profile, radial line of sight, sector viewsheds and highest points, on a
small surface whose answers are known: flat ground at 100 m with a 60 m high ridge across the middle (column 20)."""
import numpy as np
import pytest

from test_rasters import CELL, pro  # noqa: F401  (fixture)
from test_scenarios import N, P, ll, raster, vector  # noqa: F401

from app import milcoords as mc


@pytest.fixture
def dem(pro, P):
    z = np.full((N, N), 100.0, "float32")
    z[:, 20] = 160.0
    z[30, 33] = 140.0                       # a hill beyond the ridge
    return raster(pro, P, "dem", z)


def dd(c, r):
    x, y = ll(c, r)
    return f"{y:.7f}, {x:.7f}"


def rows(pro, lay):
    return [x["props"] for x in pro.post(f"/layers/{lay['id']}/rows", json={"limit": 1000}).json()["rows"]]


def test_the_ridge_hides_what_is_behind_it(pro, P, dem):
    body = {"input": dem["id"], "observers_text": f"OP: {dd(5, 10)}", "targets_text": f"Near: {dd(15, 10)}\nFar: {dd(35, 10)}",
            "height": 2, "target_height": 0}
    r = pro.post(f"/projects/{P['id']}/raster-tools/line_of_sight", json={"params": body})
    assert r.status_code == 201, r.text
    lay = r.json()["layer"]
    assert lay["style"]["type"] == "categorized" and lay["style"]["field"] == "visible"
    got = rows(pro, lay)
    near = [x for x in got if x["target"] == "Near"]
    far = [x for x in got if x["target"] == "Far"]
    assert all(x["target_visible"] for x in near) and {x["visible"] for x in near} == {"Visible"}
    assert not any(x["target_visible"] for x in far)
    hidden = [x for x in far if x["visible"] == "Hidden"]
    # seen up to the ridge (15 cells away), hidden behind it
    assert hidden and min(x["from_m"] for x in hidden) == pytest.approx(15.5 * CELL, abs=CELL)
    # a mast tall enough sees over the ridge
    r = pro.post(f"/projects/{P['id']}/raster-tools/line_of_sight", json={"params": {**body, "targets_text": dd(35, 10), "target_height": 200}})
    assert all(x["target_visible"] for x in rows(pro, r.json()["layer"]))


def test_the_profile_for_the_map(pro, P, dem):
    (x1, y1), (x2, y2) = ll(5, 10), ll(35, 10)
    r = pro.post(f"/layers/{dem['id']}/line-of-sight", json={"observer": [x1, y1], "target": [x2, y2], "height": 2})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["target_visible"] is False and out["blocked_at_m"] == pytest.approx(15 * CELL, abs=1.5 * CELL)
    assert out["observer_ground"] == pytest.approx(100) and out["observer_eye"] == pytest.approx(102)
    assert max(p["z"] for p in out["profile"]) == pytest.approx(160, abs=1)
    assert {f["properties"]["visible"] for f in out["segments"]["features"]} == {"Visible", "Hidden"}
    assert pro.post(f"/layers/{dem['id']}/line-of-sight", json={"observer": [x1, y1], "target": [x1, y1]}).status_code == 422


def test_radial_line_of_sight_and_a_sector(pro, P, dem):
    r = pro.post(f"/projects/{P['id']}/raster-tools/radial_line_of_sight", json={"params": {
        "input": dem["id"], "observers_text": dd(10, 20), "radius": 15 * CELL, "radials": 8}})
    assert r.status_code == 201, r.text
    got = rows(pro, r.json()["layer"])
    assert sorted({x["bearing"] for x in got}) == [0, 45, 90, 135, 180, 225, 270, 315]
    east = [x for x in got if x["bearing"] == 90]
    west = [x for x in got if x["bearing"] == 270]
    assert {x["visible"] for x in east} == {"Visible", "Hidden"} and {x["visible"] for x in west} == {"Visible"}
    r = pro.post(f"/projects/{P['id']}/raster-tools/radial_line_of_sight", json={"params": {
        "input": dem["id"], "observers_text": dd(10, 20), "radius": 10 * CELL, "radials": 4, "az_from": 45, "az_to": 135}})
    assert sorted({x["bearing"] for x in rows(pro, r.json()["layer"])}) == [45, 67.5, 90, 112.5, 135]


def test_sector_viewshed_counts_only_the_sector_and_ranges(pro, P, dem):
    from test_rasters import pro as _  # noqa: F401
    import rasterio
    from app import rasters
    base = {"input": dem["id"], "observers_text": dd(10, 20), "radius": 30 * CELL, "height": 2}
    full = pro.post(f"/projects/{P['id']}/raster-tools/sector_viewshed", json={"params": base})
    east = pro.post(f"/projects/{P['id']}/raster-tools/sector_viewshed", json={"params": {**base, "az_from": 45, "az_to": 135, "inner": 3 * CELL}})
    assert full.status_code == 201 and east.status_code == 201, east.text

    def read(lay):
        with rasterio.open(rasters.local(lay["raster"]["key"])) as s:
            return s.read(1)
    a, b = read(full.json()["layer"]), read(east.json()["layer"])
    assert a[20, 2] == 1 and b[20, 2] == 0                   # west: seen, but outside the sector
    assert b[20, 15] == 1 and b[20, 11] == 0                 # east, before the ridge; and nearer than the nearest range
    assert a[20, 30] == 0                                    # behind the ridge
    assert b.sum() < a.sum()


def test_highest_points_kept_apart(pro, P, dem):
    r = pro.post(f"/projects/{P['id']}/raster-tools/highest_points", json={"params": {"input": dem["id"], "count": 3, "spacing": 8 * CELL}})
    assert r.status_code == 201, r.text
    got = sorted(rows(pro, r.json()["layer"]), key=lambda x: x["rank"])
    assert [x["rank"] for x in got] == [1, 2, 3] and got[0]["elevation"] == pytest.approx(160)
    assert mc.parse(got[0]["mgrs"])
    area = vector(pro, P, "area", [{"type": "Polygon", "coordinates": [[ll(28, 25), ll(38, 25), ll(38, 36), ll(28, 36), ll(28, 25)]]}])
    r = pro.post(f"/projects/{P['id']}/raster-tools/highest_points", json={"params": {"input": dem["id"], "count": 1, "within": area["id"]}})
    assert rows(pro, r.json()["layer"])[0]["elevation"] == pytest.approx(140)


def test_observers_from_a_layer_and_errors(pro, P, dem):
    obs = vector(pro, P, "posts", [{"type": "Point", "coordinates": ll(5, 10)}])
    r = pro.post(f"/projects/{P['id']}/raster-tools/line_of_sight", json={"params": {"input": dem["id"], "observers": "layer", "observers_layer": obs["id"],
                                                                                      "targets_text": dd(15, 10)}})
    assert r.status_code == 201, r.text
    r = pro.post(f"/projects/{P['id']}/raster-tools/line_of_sight", json={"params": {"input": dem["id"], "observers_text": "nowhere", "targets_text": dd(15, 10)}})
    assert r.status_code == 422
    far = "0.0, 0.0"
    r = pro.post(f"/projects/{P['id']}/raster-tools/line_of_sight", json={"params": {"input": dem["id"], "observers_text": far, "targets_text": dd(15, 10)}})
    assert r.status_code == 422 and "off the elevation model" in r.json()["detail"]
