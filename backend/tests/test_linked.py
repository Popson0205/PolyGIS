"""Linked layers: Land Registry's parcels, FarmScan's farms and a Geohub form's features as layers that follow those
apps, read as the person (Geohub's token exchange for the other apps), refreshed, and not edited here."""
import json

import httpx
import pytest

from app.routers import linked
from conftest import HUB

SQ = {"type": "Polygon", "coordinates": [[[7.46, 9.07], [7.47, 9.07], [7.47, 9.08], [7.46, 9.08], [7.46, 9.07]]]}


@pytest.fixture
def apps(monkeypatch):
    seen = {"parcels": [{"type": "Feature", "geometry": SQ, "properties": {"pin": "KLA-000001", "land_use": "residential", "area_sqm": 1000,
                                                                           "title_no": "KLA/CO/1", "locality": "Kabusa", "secret": "x"}}]}

    def answer(req: httpx.Request) -> httpx.Response:
        tok = req.headers.get("authorization", "")
        if req.url.host == "land.test" and req.url.path == "/api/parcels.geojson":
            assert tok.startswith("Bearer land-token-for-")
            return httpx.Response(200, json={"type": "FeatureCollection", "features": seen["parcels"]})
        if req.url.host == "farm.test" and req.url.path == "/api/boundaries.geojson":
            assert tok.startswith("Bearer farm-token-for-")
            return httpx.Response(200, json={"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": SQ, "properties": {"name": "North block", "ndvi": 0.61, "area_ha": 1.2}}]})
        return httpx.Response(404, json={"detail": "Not found"})
    monkeypatch.setattr(linked, "TRANSPORT", httpx.MockTransport(answer))
    HUB.products = {"land"}
    HUB.hub_projects = [{"id": "p1", "name": "Kuje survey", "forms": [{"id": "11111111-1111-4111-8111-111111111111", "name": "Boreholes", "geometry_type": "point",
                                                                       "fields": [{"id": "depth_m", "label": "Depth", "type": "number"},
                                                                                  {"id": "works", "label": "Works?", "type": "boolean"}],
                                                                       "features": 3, "approved": 2}]}]
    HUB.hub_features = {"11111111-1111-4111-8111-111111111111": [
        {"id": f"f{i}", "geometry": {"type": "Point", "coordinates": [7.2 + i / 100, 8.8]}, "status": "approved" if i < 2 else "submitted",
         "properties": {"depth_m": 40 + i, "works": True}, "created_by_name": "Field Team"} for i in range(3)]}
    yield seen
    HUB.products = set()


def test_land_registry_parcels_follow_the_register(person, apps):
    ed = person(role="member", name="Link Maker")
    p = ed.post("/projects", json={"name": "Linked"}).json()
    src = ed.get(f"/projects/{p['id']}/linked/sources").json()
    land = next(s for s in src if s["app"] == "land")
    farm = next(s for s in src if s["app"] == "farm")
    assert land["available"] and not farm["available"] and "does not have FarmScan" in farm["why"]
    assert any(s["app"] == "hub" and s["label"] == "Kuje survey › Boreholes" and s["geometry_type"] == "Point" for s in src)

    r = ed.post(f"/projects/{p['id']}/layers/linked", json={"app": "land"})
    assert r.status_code == 201, r.text
    lay = r.json()
    assert lay["name"] == "Parcel fabric" and lay["feature_count"] == 1 and lay["source"]["linked"]["app"] == "land" and lay["labels"]["field"] == "pin"
    assert lay["style"]["type"] == "categorized" and lay["style"]["field"] == "land_use" and lay["style"]["categories"][0]["value"] == "residential"
    row = ed.post(f"/layers/{lay['id']}/rows", json={}).json()["rows"][0]["props"]
    assert row["pin"] == "KLA-000001" and row["title_no"] == "KLA/CO/1" and "secret" not in row

    # changed in Land Registry, refreshed here
    apps["parcels"].append({"type": "Feature", "geometry": SQ, "properties": {"pin": "KLA-000002", "land_use": "commercial"}})
    r = ed.post(f"/layers/{lay['id']}/refresh")
    assert r.status_code == 200 and r.json()["feature_count"] == 2 and r.json()["source"]["linked"]["refreshed_at"]

    # not edited here
    for call in (lambda: ed.post(f"/layers/{lay['id']}/features", json={"geometry": SQ, "properties": {}}),
                 lambda: ed.post(f"/layers/{lay['id']}/fields", json={"op": "add", "name": "x", "type": "string"}),
                 lambda: ed.post(f"/layers/{lay['id']}/features/delete", json={"ids": [1]})):
        res = call()
        assert res.status_code == 409 and "follows Land Registry" in res.json()["detail"]
    # styling it is fine
    assert ed.patch(f"/layers/{lay['id']}", json={"style": {**lay["style"], "color": "#ff0000"}}).status_code == 200

    # FarmScan only once the organization has it
    assert ed.post(f"/projects/{p['id']}/layers/linked", json={"app": "farm"}).status_code == 403
    HUB.products = {"land", "farm"}
    f = ed.post(f"/projects/{p['id']}/layers/linked", json={"app": "farm"}).json()
    assert f["name"] == "Farms" and f["feature_count"] == 1 and f["style"]["type"] == "graduated" and f["style"]["field"] == "ndvi"


def test_a_geohub_form_as_a_layer(person, apps):
    ed = person(role="member", name="Form Linker")
    p = ed.post("/projects", json={"name": "Boreholes"}).json()
    r = ed.post(f"/projects/{p['id']}/layers/linked", json={"app": "hub", "form_id": "11111111-1111-4111-8111-111111111111"})
    assert r.status_code == 201, r.text
    lay = r.json()
    assert lay["geometry_type"] == "Point" and lay["feature_count"] == 2 and {f["name"] for f in lay["fields"]} >= {"depth_m", "works", "status", "collected_by"}
    allf = ed.post(f"/projects/{p['id']}/layers/linked", json={"app": "hub", "form_id": "11111111-1111-4111-8111-111111111111", "status": "all"}).json()
    assert allf["feature_count"] == 3
    assert ed.post(f"/projects/{p['id']}/layers/linked", json={"app": "hub", "form_id": "22222222-2222-4222-8222-222222222222"}).status_code == 404
    viewer = person(role="viewer", name="Look Only")
    assert viewer.post(f"/layers/{lay['id']}/refresh").status_code == 403
