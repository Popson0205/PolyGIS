"""Imagery providers: tile services (XYZ, WMS) fetched through PolyGIS with the organization's key, STAC catalogues
searched and their scenes added, the built-in free catalogue, and who may manage them."""
import httpx
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.routers import providers
from test_rasters import pro  # noqa: F401  (fixture)
from test_scenarios import P  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\nfake"
seen: list[httpx.Request] = []


def answer(req: httpx.Request) -> httpx.Response:
    seen.append(req)
    if req.url.host == "tiles.vendor.test":
        if req.url.params.get("api_key") != "SECRET":
            return httpx.Response(401)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    if req.url.host == "wms.vendor.test":
        assert req.url.params["REQUEST"] == "GetMap" and req.url.params["LAYERS"] == "ortho"
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    if req.url.host == "stac.vendor.test":
        if req.headers.get("authorization") != "Bearer TOKEN":
            return httpx.Response(403)
        if req.url.path.endswith("/search"):
            import json
            q = json.loads(req.content)
            assert q["collections"] == ["wv03"] and q["query"]["eo:cloud_cover"]["lte"] == 20
            return httpx.Response(200, json={"features": [
                {"id": "S1", "collection": "wv03", "bbox": [7.4, 9.0, 7.5, 9.1], "geometry": {"type": "Point", "coordinates": [7.45, 9.05]},
                 "properties": {"datetime": "2026-11-01T10:00:00Z", "eo:cloud_cover": 3, "gsd": 0.3, "platform": "worldview-3"},
                 "assets": {"visual": {"href": "https://data.vendor.test/S1.tif"}, "thumbnail": {"href": "https://data.vendor.test/S1.jpg"}},
                 "links": [{"rel": "self", "href": "https://stac.vendor.test/collections/wv03/items/S1"}]}]})
        if req.url.path.endswith("/items/S1"):
            return httpx.Response(200, json={"id": "S1", "properties": {"datetime": "2026-11-01T10:00:00Z"},
                                             "assets": {"visual": {"href": "https://data.vendor.test/S1.tif"}}})
    if req.url.host == "data.vendor.test":
        return httpx.Response(200, content=b"jpegbytes", headers={"content-type": "image/jpeg"})
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def stand_in(monkeypatch):
    seen.clear()
    monkeypatch.setattr(providers, "TRANSPORT", httpx.MockTransport(answer))


@pytest.fixture
def admin(person):
    return person(role="owner", name="Org Admin", features=["maps", "editing", "export", "geoprocessing", "raster"])


def test_only_admins_manage_and_keys_stay_on_the_server(pro, admin):
    body = {"name": "Vendor imagery", "kind": "xyz", "url": "https://tiles.vendor.test/{z}/{x}/{y}.png",
            "config": {"attribution": "© Vendor", "max_zoom": 21}, "auth": {"mode": "query", "name": "api_key", "value": "SECRET"}}
    assert pro.post("/imagery-providers", json=body).status_code == 403
    r = admin.post("/imagery-providers", json=body)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["key_set"] and p["auth_mode"] == "query" and "SECRET" not in r.text and p["tiles"].endswith("/tiles/{z}/{x}/{y}")
    listed = pro.get("/imagery-providers").json()
    assert [x["name"] for x in listed][:1] == ["Earth Search · Sentinel-2 (free)"] and "SECRET" not in str(listed)
    # a tile, fetched with the key added here
    t = pro.get(f"/imagery-providers/{p['id']}/tiles/12/2133/1960")
    assert t.status_code == 200 and t.content == PNG and t.headers["content-type"] == "image/png"
    assert seen[-1].url.path == "/12/2133/1960.png"
    # changing other things keeps the key; a wrong one is reported plainly
    admin.patch(f"/imagery-providers/{p['id']}", json={"name": "Vendor HD", "auth": {"mode": "query", "name": "api_key"}})
    assert pro.get(f"/imagery-providers/{p['id']}/tiles/12/2133/1960").status_code == 200
    admin.patch(f"/imagery-providers/{p['id']}", json={"auth": {"mode": "query", "name": "api_key", "value": "WRONG"}})
    r = pro.get(f"/imagery-providers/{p['id']}/tiles/12/2133/1960")
    assert r.status_code == 502 and "key" in r.json()["detail"]
    assert admin.post("/imagery-providers", json={**body, "url": "http://tiles.vendor.test/{z}/{x}/{y}"}).status_code == 422
    assert admin.post("/imagery-providers", json={**body, "url": "https://tiles.vendor.test/tiles"}).status_code == 422
    assert admin.delete("/imagery-providers/builtin-earth-search").status_code == 422
    assert admin.delete(f"/imagery-providers/{p['id']}").status_code == 204


def test_wms(admin):
    p = admin.post("/imagery-providers", json={"name": "Ortho WMS", "kind": "wms", "url": "https://wms.vendor.test/service",
                                               "config": {"layers": "ortho"}}).json()
    r = admin.get(f"/imagery-providers/{p['id']}/tiles/10/530/490")
    assert r.status_code == 200, r.text
    assert seen[-1].url.params["CRS"] == "EPSG:3857" and seen[-1].url.params["WIDTH"] == "256"


def test_stac_search_thumbnail_and_add(pro, admin, P, monkeypatch, tmp_path):
    p = admin.post("/imagery-providers", json={"name": "Vendor archive", "kind": "stac", "url": "https://stac.vendor.test",
                                               "config": {"collections": ["wv03"], "asset": "visual", "hosts": ["data.vendor.test"]},
                                               "auth": {"mode": "header", "name": "Authorization", "value": "Bearer TOKEN"}}).json()
    r = pro.post(f"/imagery-providers/{p['id']}/search", json={"bbox": [7.4, 9.0, 7.5, 9.1], "start": "2026-10-01", "end": "2026-11-15", "max_cloud": 20})
    assert r.status_code == 200, r.text
    sc = r.json()["scenes"][0]
    assert sc["id"] == "S1" and sc["cloud"] == 3 and sc["has_image"] and sc["gsd"] == 0.3
    t = pro.get(f"/imagery-providers/{p['id']}/thumbnail", params={"href": sc["thumbnail"]})
    assert t.status_code == 200 and t.content == b"jpegbytes" and seen[-1].headers["authorization"] == "Bearer TOKEN"
    assert pro.get(f"/imagery-providers/{p['id']}/thumbnail", params={"href": "https://evil.test/x.jpg"}).status_code == 422
    got = {}

    def reader(href, bbox, headers, auth, out):
        got.update(href=href, headers=headers)
        with rasterio.open(out, "w", driver="GTiff", width=20, height=20, count=3, dtype="uint8", crs="EPSG:32632",
                           transform=from_origin(330000, 1004000, 0.5, 0.5)) as d:
            d.write(np.full((3, 20, 20), 120, "uint8"))
    monkeypatch.setattr(providers, "READ_SCENE", reader)
    r = pro.post(f"/projects/{P['id']}/imagery-providers/{p['id']}/add", json={"scene_id": "S1", "bbox": [7.44, 9.04, 7.46, 9.06]})
    assert r.status_code == 201, r.text
    lay = r.json()
    assert lay["geometry_type"] == "Raster" and lay["raster"]["count"] == 3 and "2026-11-01" in lay["name"]
    assert lay["source"]["provider"] == "Vendor archive" and got["href"] == "https://data.vendor.test/S1.tif"
    assert got["headers"]["Authorization"] == "Bearer TOKEN"
    assert pro.post(f"/projects/{P['id']}/imagery-providers/{p['id']}/add", json={"scene_id": "S1", "bbox": [0, 0, 10, 10]}).status_code == 422
