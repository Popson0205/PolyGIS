"""Project geodatabases: every project has a default geodatabase, new layers and analysis land in it, and the whole
geodatabase downloads as one GeoPackage (ArcGIS-style default gdb)."""
import io
import json
import tempfile

import pyogrio
from test_scenarios import P, vector  # noqa: F401
from test_rasters import pro  # noqa: F401

PT = {"type": "Point", "coordinates": [7.45, 9.05]}


def gdbs(c, P):
    return c.get(f"/projects/{P['id']}/geodatabases").json()


def test_a_project_starts_with_one_default_geodatabase(pro, P):
    gs = gdbs(pro, P)
    assert len(gs) == 1
    assert gs[0]["name"] == "Default" and gs[0]["is_default"] is True
    assert gs[0]["layer_count"] == 0


def test_new_layers_land_in_the_default_geodatabase(pro, P):
    default = gdbs(pro, P)[0]
    lay = vector(pro, P, "wells", [PT, PT])
    assert lay["geodatabase_id"] == default["id"]
    g = gdbs(pro, P)[0]
    assert g["layer_count"] == 1 and g["feature_count"] == 2


def test_outputs_follow_the_default_after_switching(pro, P):
    g2 = pro.post(f"/projects/{P['id']}/geodatabases", json={"name": "Scratch"}).json()
    assert g2["is_default"] is False
    # make the new one default; a fresh layer should land there, not in "Default"
    assert pro.patch(f"/geodatabases/{g2['id']}", json={"is_default": True}).status_code == 200
    lay = vector(pro, P, "roads", [PT])
    assert lay["geodatabase_id"] == g2["id"]
    by_id = {g["id"]: g for g in gdbs(pro, P)}
    assert by_id[g2["id"]]["is_default"] is True
    assert sum(g["is_default"] for g in by_id.values()) == 1  # exactly one default


def test_rename_a_geodatabase(pro, P):
    g2 = pro.post(f"/projects/{P['id']}/geodatabases", json={"name": "Temp"}).json()
    r = pro.patch(f"/geodatabases/{g2['id']}", json={"name": "Working set"})
    assert r.status_code == 200 and r.json()["name"] == "Working set"


def test_cannot_delete_the_default_or_the_last_geodatabase(pro, P):
    default = gdbs(pro, P)[0]
    # only one geodatabase -> cannot delete it
    assert pro.delete(f"/geodatabases/{default['id']}").status_code == 409
    g2 = pro.post(f"/projects/{P['id']}/geodatabases", json={"name": "Second"}).json()
    # default is still default -> cannot delete default while a non-default exists
    assert pro.delete(f"/geodatabases/{default['id']}").status_code == 409
    # a non-default one can be deleted
    assert pro.delete(f"/geodatabases/{g2['id']}").status_code == 204
    assert len(gdbs(pro, P)) == 1


def test_deleting_a_geodatabase_removes_its_layers(pro, P):
    g2 = pro.post(f"/projects/{P['id']}/geodatabases", json={"name": "Box"}).json()
    pro.patch(f"/geodatabases/{g2['id']}", json={"is_default": True})
    vector(pro, P, "temp_points", [PT])
    assert any(l["geodatabase_id"] == g2["id"] for l in pro.get(f"/projects/{P['id']}").json()["layers"])
    # must hand the default back before deleting
    default = next(g for g in gdbs(pro, P) if g["name"] == "Default")
    pro.patch(f"/geodatabases/{default['id']}", json={"is_default": True})
    assert pro.delete(f"/geodatabases/{g2['id']}").status_code == 204
    assert not any(l["geodatabase_id"] == g2["id"] for l in pro.get(f"/projects/{P['id']}").json()["layers"])


def test_download_a_geodatabase_as_geopackage(pro, P):
    vector(pro, P, "sites", [PT, PT])
    vector(pro, P, "more_sites", [PT])
    default = gdbs(pro, P)[0]
    r = pro.get(f"/geodatabases/{default['id']}/download")
    assert r.status_code == 200
    assert "geopackage" in r.headers["content-type"]
    assert r.content[:16].startswith(b"SQLite format 3")
    with tempfile.NamedTemporaryFile(suffix=".gpkg") as f:
        f.write(r.content); f.flush()
        names = [n for n, _ in pyogrio.list_layers(f.name)]
    assert "sites" in names and "more_sites" in names


# ---- archive / restore ----
class FakeS3:
    """An in-memory stand-in for the S3 bucket client used by archiving."""
    store: dict = {}
    def put_object(self, Bucket, Key, Body, **kw): FakeS3.store[(Bucket, Key)] = Body
    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(FakeS3.store[(Bucket, Key)])}
    def delete_object(self, Bucket, Key): FakeS3.store.pop((Bucket, Key), None)


def _use_fake_bucket(monkeypatch):
    from app.config import settings
    from app import rasters
    FakeS3.store = {}
    monkeypatch.setattr(settings, "s3_endpoint", "https://example.supabase.co/storage/v1/s3")
    monkeypatch.setattr(settings, "s3_bucket", "polygis")
    monkeypatch.setattr(rasters, "_bucket", lambda: FakeS3())


def test_archive_needs_a_bucket(pro, P, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "s3_endpoint", "")
    g2 = pro.post(f"/projects/{P['id']}/geodatabases", json={"name": "Box"}).json()
    assert pro.post(f"/geodatabases/{g2['id']}/archive").status_code == 409


def test_cannot_archive_the_default(pro, P, monkeypatch):
    _use_fake_bucket(monkeypatch)
    default = gdbs(pro, P)[0]
    assert pro.post(f"/geodatabases/{default['id']}/archive").status_code == 409


def test_archive_then_restore_round_trips(pro, P, monkeypatch):
    _use_fake_bucket(monkeypatch)
    # put a layer with features into a second geodatabase
    a = pro.post(f"/projects/{P['id']}/geodatabases", json={"name": "Old season"}).json()
    pro.patch(f"/geodatabases/{a['id']}", json={"is_default": True})
    vector(pro, P, "wells", [PT, PT, PT])
    default = next(g for g in gdbs(pro, P) if g["name"] == "Default")
    pro.patch(f"/geodatabases/{default['id']}", json={"is_default": True})   # hand default back so A can be archived

    r = pro.post(f"/geodatabases/{a['id']}/archive")
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["storage"] == "archived" and g["archived_at"]
    assert g["layer_count"] == 1 and g["feature_count"] == 3     # counts preserved from the manifest
    # its layers are gone from the live project, and it can't be downloaded while archived
    assert not any(l["geodatabase_id"] == a["id"] for l in pro.get(f"/projects/{P['id']}").json()["layers"])
    assert pro.get(f"/geodatabases/{a['id']}/download").status_code == 409
    assert FakeS3.store   # the GeoPackage is in the bucket

    r = pro.post(f"/geodatabases/{a['id']}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["storage"] == "postgis"
    back = [l for l in pro.get(f"/projects/{P['id']}").json()["layers"] if l["geodatabase_id"] == a["id"]]
    assert len(back) == 1 and back[0]["name"] == "wells" and back[0]["feature_count"] == 3
    assert not FakeS3.store   # the archived copy was removed on restore
