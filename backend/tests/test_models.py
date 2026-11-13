"""Model builder: a chain of tools saved for the organization, checked when saved, and run on a project's layers,
keeping the final result (or every step's), and stopping plainly at a step that fails."""
import json

import pytest

PTS = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": f"BH{i}", "zone": "A" if i < 2 else "B"},
                                                  "geometry": {"type": "Point", "coordinates": [7.46 + i * 0.002, 9.07]}} for i in range(4)]}
AREA = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"site": "Estate"},
                                                   "geometry": {"type": "Polygon", "coordinates": [[[7.459, 9.069], [7.4635, 9.069], [7.4635, 9.072], [7.459, 9.072], [7.459, 9.069]]]}}]}
GP = ["maps", "editing", "export", "geoprocessing"]
MODEL = {"name": "Borehole protection zones", "description": "Buffer the boreholes, clip to the site, dissolve.",
         "inputs": [{"name": "boreholes", "label": "Boreholes", "kinds": ["Point"]}, {"name": "site", "label": "Site boundary", "kinds": ["Polygon"]}],
         "steps": [{"id": "s1", "tool": "buffer", "params": {"input": "input:boreholes", "distance": 50}, "output_name": "Zones"},
                   {"id": "s2", "tool": "clip", "params": {"input": "step:s1", "clip": "input:site"}},
                   {"id": "s3", "tool": "dissolve", "params": {"input": "step:s2", "fields": []}, "output_name": "Protection zones"}]}


@pytest.fixture
def gis(person):
    return person(role="member", name="Model Maker", features=GP)


def _project(p):
    pr = p.post("/projects", json={"name": "Models"}).json()
    bh = p.post(f"/projects/{pr['id']}/layers/import", files={"file": ("boreholes.geojson", json.dumps(PTS))}).json()[0]
    site = p.post(f"/projects/{pr['id']}/layers/import", files={"file": ("site.geojson", json.dumps(AREA))}).json()[0]
    return pr, bh, site


def test_a_model_is_saved_and_run_on_layers(gis):
    r = gis.post("/models", json=MODEL)
    assert r.status_code == 201, r.text
    m = r.json()
    pr, bh, site = _project(gis)
    before = len(gis.get(f"/projects/{pr['id']}").json()["layers"])
    r = gis.post(f"/projects/{pr['id']}/models/{m['id']}/run", json={"inputs": {"boreholes": bh["id"], "site": site["id"]}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "done" and [s["tool"] for s in out["steps"]] == ["buffer", "clip", "dissolve"]
    assert [x["name"] for x in out["layers"]] == ["Protection zones"] and out["layers"][0]["geometry_type"] == "Polygon"
    layers = gis.get(f"/projects/{pr['id']}").json()["layers"]
    assert len(layers) == before + 1                                      # intermediate results were not kept
    assert len(gis.get(f"/projects/{pr['id']}/runs").json()) == 3          # each step is in the history
    # every step's result, when asked
    out = gis.post(f"/projects/{pr['id']}/models/{m['id']}/run", json={"inputs": {"boreholes": bh["id"], "site": site["id"]}, "keep": "all"}).json()
    assert [x["name"] for x in out["layers"]] == ["Zones", "Borehole protection zones: Clip", "Protection zones"]
    assert gis.get("/models").json()[0]["runs"] == 2


def test_models_are_checked_when_saved_and_when_run(person, gis):
    bad_ref = {**MODEL, "steps": [{**MODEL["steps"][0], "params": {"input": "step:s2", "distance": 50}}, *MODEL["steps"][1:]]}
    r = gis.post("/models", json=bad_ref)
    assert r.status_code == 422 and "later step" in r.json()["detail"]
    assert gis.post("/models", json={**MODEL, "steps": [{"id": "s1", "tool": "teleport", "params": {}}]}).status_code == 422
    assert gis.post("/models", json={**MODEL, "inputs": [{"name": "Bore Holes", "kinds": ["Point"]}]}).status_code == 422
    missing = {**MODEL, "steps": [{"id": "s1", "tool": "buffer", "params": {"distance": 50}}]}
    assert "choose its input" in gis.post("/models", json=missing).json()["detail"]

    m = gis.post("/models", json=MODEL).json()
    pr, bh, site = _project(gis)
    # inputs of the wrong kind, or missing
    r = gis.post(f"/projects/{pr['id']}/models/{m['id']}/run", json={"inputs": {"boreholes": site["id"], "site": site["id"]}})
    assert r.status_code == 422 and "takes Point" in r.json()["detail"]
    assert gis.post(f"/projects/{pr['id']}/models/{m['id']}/run", json={"inputs": {"boreholes": bh["id"]}}).status_code == 422
    # a step that fails stops the model and says which; nothing half-made is left
    broken = gis.patch(f"/models/{m['id']}", json={"steps": [MODEL["steps"][0], {"id": "s2", "tool": "dissolve", "params": {"input": "step:s1", "fields": ["nope"]}}]})
    assert broken.status_code == 200
    n = len(gis.get(f"/projects/{pr['id']}").json()["layers"])
    out = gis.post(f"/projects/{pr['id']}/models/{m['id']}/run", json={"inputs": {"boreholes": bh["id"], "site": site["id"]}}).json()
    assert out["status"] == "failed" and out["failed_step"] == "s2" and out["message"].startswith("Step 2 (Dissolve)")
    assert len(gis.get(f"/projects/{pr['id']}").json()["layers"]) == n
    # only editors on a plan with geoprocessing make and run models; everyone in the organization sees them
    std = person(role="member", name="Std Plan")
    assert std.post("/models", json=MODEL).status_code == 403
    viewer = person(role="viewer", name="View Only", features=GP)
    assert any(x["id"] == m["id"] for x in viewer.get("/models").json())
    assert viewer.post(f"/projects/{pr['id']}/models/{m['id']}/run", json={"inputs": {}}).status_code == 403
    assert gis.delete(f"/models/{m['id']}").status_code == 204
