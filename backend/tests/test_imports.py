"""Large-dataset server-side ingest: streaming helpers, path safety, and an end-to-end import."""
import json
import time
from pathlib import Path

import pytest

from app import gisdata
from app.config import settings
from app.errors import Problem
from app.routers import imports as I


def _tiny(path: Path, n: int = 2):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[[3.4 + i * 0.2, 6.5], [3.5 + i * 0.2, 6.5],
                                                          [3.5 + i * 0.2, 6.6], [3.4 + i * 0.2, 6.6], [3.4 + i * 0.2, 6.5]]]},
         "properties": {"name": chr(65 + i), "pop": (i + 1) * 10}} for i in range(n)]}
    path.write_text(json.dumps(fc))


# ---- streaming helpers (no DB) -----------------------------------------------------
def test_large_meta(tmp_path):
    f = tmp_path / "d.geojson"; _tiny(f)
    m = gisdata.large_meta(f)
    assert m["geometry_type"] == "Polygon" and m["srid"] == 4326 and m["total"] == 2
    assert {x["name"] for x in m["fields"]} == {"name", "pop"}


def test_stream_batches_covers_all_features(tmp_path):
    f = tmp_path / "d.geojson"; _tiny(f, n=5)
    rows = list(gisdata.stream_batches(f, batch=2))     # batch smaller than total -> exercises paging
    assert len(rows) == 5
    assert isinstance(rows[0][0], (bytes, bytearray)) and rows[0][1]["name"] == "A"
    assert {r[1]["name"] for r in rows} == set("ABCDE")


# ---- path safety (no DB) -----------------------------------------------------------
def test_source_path_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ingest_dir", str(tmp_path))
    (tmp_path / "ok.geojson").write_text("{}")
    assert I._source_path("ok.geojson").name == "ok.geojson"
    for bad in ("../x", "../../etc/passwd", "sub/x", "nope.geojson"):
        with pytest.raises(Problem):
            I._source_path(bad)


def test_sources_needs_ingest_dir(monkeypatch):
    monkeypatch.setattr(settings, "ingest_dir", "")
    with pytest.raises(Problem):
        I._root()


# ---- end to end --------------------------------------------------------------------
def test_ingest_small_file_end_to_end(person, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ingest_dir", str(tmp_path))
    _tiny(tmp_path / "national.geojson", n=3)
    admin = person(role="owner", name="Admin")
    pid = admin.post("/projects", json={"name": "Big data"}).json()["id"]

    r = admin.post(f"/projects/{pid}/imports", json={"source": "national.geojson", "name": "National"})
    assert r.status_code == 200, r.text
    imp = r.json(); lid = imp["layer_id"]
    assert imp["status"] in ("queued", "running")

    s = {}
    for _ in range(150):
        s = admin.get(f"/imports/{imp['id']}").json()
        if s["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert s["status"] == "done", s
    assert s["features"] == 3

    # the layer is marked big, and renders as a tile
    proj = admin.get(f"/projects/{pid}").json()
    mine = next(x for x in proj["layers"] if x["id"] == lid)
    assert mine["big"] is True and mine["feature_count"] == 3
    assert admin.get(f"/layers/{lid}/tiles/0/0/0.pbf").status_code == 200

    # table is guarded without a filter, allowed with one
    assert admin.post(f"/layers/{lid}/rows", json={"filters": []}).status_code == 409
    got = admin.post(f"/layers/{lid}/rows", json={"filters": [{"field": "name", "op": "=", "value": "A"}]})
    assert got.status_code == 200 and got.json()["total"] == 1

    # export is guarded without a selection
    assert admin.get(f"/layers/{lid}/export").status_code == 409
    assert admin.get(f"/layers/{lid}/export", params={"ids": "1"}).status_code == 200


def test_start_rejects_missing_source(person, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ingest_dir", str(tmp_path))
    admin = person(role="owner")
    pid = admin.post("/projects", json={"name": "P"}).json()["id"]
    assert admin.post(f"/projects/{pid}/imports", json={"source": "ghost.geojson", "name": "X"}).status_code == 404


def test_start_needs_admin(person, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ingest_dir", str(tmp_path))
    _tiny(tmp_path / "x.geojson")
    member = person(role="member")
    pid = member.post("/projects", json={"name": "P"}).json()["id"]
    assert member.post(f"/projects/{pid}/imports", json={"source": "x.geojson", "name": "X"}).status_code == 403
