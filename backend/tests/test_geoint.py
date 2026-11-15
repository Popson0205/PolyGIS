"""Geospatial intelligence: activity change between periods, weapon and sensor ranges, and the area of interest brief."""
import numpy as np

from test_rasters import pro  # noqa: F401  (fixture)
from test_scenarios import N, P, ll, raster, vector  # noqa: F401


def _events(pro, P):
    """Two places: activity at A falls and stops; at B it starts. Dates as text, as they come in a CSV or GeoJSON."""
    fc = {"type": "FeatureCollection", "features": []}
    def add(x, y, d):
        fc["features"].append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]}, "properties": {"when": d, "what": "sighting"}})
    for k in range(6):
        add(7.400 + k * 1e-4, 9.000, f"2026-01-{10 + k:02d}")       # A, before
    add(7.400, 9.000, "2026-03-05T10:20:00")                         # A, after (a timestamp)
    for k in range(4):
        add(7.500 + k * 1e-4, 9.100, f"2026-03-{10 + k:02d}")       # B, after only
    add(7.45, 9.05, "not a date")
    import json
    r = pro.post(f"/projects/{P['id']}/layers/import", files={"file": ("events.geojson", json.dumps(fc).encode())})
    return r.json()[0]


def _rows(pro, lay):
    return [x["props"] for x in pro.post(f"/layers/{lay['id']}/rows", json={"limit": 1000}).json()["rows"]]


def test_activity_change(pro, P):
    ev = _events(pro, P)
    r = pro.post(f"/projects/{P['id']}/tools/activity_change", json={"params": {"input": ev["id"], "date_field": "when", "a_from": "2026-01-01",
                 "a_to": "2026-01-31", "b_from": "2026-03-01", "b_to": "2026-03-31", "size": 2000}})
    assert r.status_code == 201, r.text
    lay = r.json()["layer"]
    assert lay["style"]["field"] == "trend"
    got = {(x["before"], x["after"]): x["trend"] for x in _rows(pro, lay)}
    assert got == {(6, 1): "Decrease", (0, 4): "New"}
    r = pro.post(f"/projects/{P['id']}/tools/activity_change", json={"params": {"input": ev["id"], "date_field": "when", "a_from": "2026-02-01",
                 "a_to": "2026-01-01", "b_from": "2026-03-01", "b_to": "2026-03-31"}})
    assert r.status_code == 422
    r = pro.post(f"/projects/{P['id']}/tools/activity_change", json={"params": {"input": ev["id"], "date_field": "when", "a_from": "Jan",
                 "a_to": "2026-01-31", "b_from": "2026-03-01", "b_to": "2026-03-31"}})
    assert r.status_code == 422 and "2026-01-31" in r.json()["detail"]


def test_weapon_ranges_from_fields(pro, P):
    import json
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [7.4, 9.0]}, "properties": {"name": "SAM", "max_km": 20, "min_km": 3}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [7.6, 9.1]}, "properties": {"name": "Mortar", "max_km": 5.5, "min_km": None}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [7.7, 9.1]}, "properties": {"name": "No range", "max_km": None, "min_km": None}}]}
    lay = pro.post(f"/projects/{P['id']}/layers/import", files={"file": ("w.geojson", json.dumps(fc).encode())}).json()[0]
    r = pro.post(f"/projects/{P['id']}/tools/weapon_ranges", json={"params": {"input": lay["id"], "range_field": "max_km", "min_field": "min_km"}})
    assert r.status_code == 201, r.text
    out = r.json()["layer"]
    assert out["feature_count"] == 2
    rows = {x["name"]: x for x in _rows(pro, out)}
    assert rows["SAM"]["range_max"] == 20 and rows["SAM"]["range_min"] == 3 and rows["Mortar"]["range_min"] == 0
    areas = pro.post(f"/projects/{P['id']}/tools/calculate_geometry", json={"params": {"input": out["id"]}}).json()["layer"]
    a = {x["name"]: x["area_m2"] for x in _rows(pro, areas)}
    assert abs(a["SAM"] - np.pi * (20_000 ** 2 - 3_000 ** 2)) / a["SAM"] < 0.005
    assert abs(a["Mortar"] - np.pi * 5_500 ** 2) / a["Mortar"] < 0.005


def test_area_brief(pro, P):
    z = np.full((N, N), 200.0, "float32")
    z[10, 10] = 260.0
    dem = raster(pro, P, "dem", z)
    units = pro.post(f"/projects/{P['id']}/layers", json={"name": "ORBAT", "geometry_type": "Point", "fields": [
        {"name": "sidc", "type": "string"}, {"name": "designation", "type": "string"}]}).json()
    pro.patch(f"/layers/{units['id']}", json={"style": {"type": "milsymbol", "mil": {"sidc_field": "sidc", "designation_field": "designation", "sidc": "SUGP-----------"}}})
    for code, name, (c, r_) in (("SFGPUCI----D---", "A/1-14", (12, 12)), ("SHGPUCA----E---", "2 COY", (15, 15)), ("SHGPUCA----E---", "far", (38, 38))):
        pro.post(f"/layers/{units['id']}/features", json={"geometry": {"type": "Point", "coordinates": ll(c, r_)}, "properties": {"sidc": code, "designation": name}})
    roads = vector(pro, P, "roads", [{"type": "LineString", "coordinates": [ll(0, 15), ll(39, 15)]}])
    area = {"type": "Polygon", "coordinates": [[ll(5, 5), ll(25, 5), ll(25, 25), ll(5, 25), ll(5, 5)]]}
    r = pro.post(f"/projects/{P['id']}/area-brief", json={"geometry": area, "dem_id": dem["id"], "name": "Objective GOLD"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert abs(b["area_km2"] - (20 * 30) ** 2 / 1e6) < 0.01 and b["centre"]["mgrs"].startswith("32P")
    assert b["ground"]["max_m"] == 260 and b["ground"]["min_m"] == 200 and b["ground"]["highest"]["elevation_m"] == 260
    assert b["unit_counts"] == {"Friend": 1, "Hostile": 1}
    assert {u["designation"] for u in b["units"]} == {"A/1-14", "2 COY"}
    road = next(x for x in b["layers"] if x["name"] == "roads")
    assert road["unit"] == "km" and abs(road["measure"] - 20 * 30 / 1000) < 0.01
    # a polygon of a layer as the area
    aoi = vector(pro, P, "aoi", [area])
    r = pro.post(f"/projects/{P['id']}/area-brief", json={"layer_id": aoi["id"], "feature_id": pro.post(f"/layers/{aoi['id']}/rows", json={}).json()["rows"][0]["id"]})
    assert r.status_code == 200 and r.json()["unit_counts"] == {"Friend": 1, "Hostile": 1} and r.json()["ground"] is None
    assert all(x["name"] != "aoi" for x in r.json()["layers"])
    assert pro.post(f"/projects/{P['id']}/area-brief", json={"geometry": {"type": "Point", "coordinates": [7, 9]}}).status_code == 422
