"""Project files: save a project to a .polygis file and open it again, round-tripping maps, geodatabases, layers,
styles and features."""
import io
import json
import zipfile

from test_scenarios import P, vector  # noqa: F401
from test_rasters import pro  # noqa: F401

PT = {"type": "Point", "coordinates": [7.45, 9.05]}
LINE = {"type": "LineString", "coordinates": [[7.4, 9.0], [7.5, 9.1]]}


def test_export_makes_a_polygis_zip_with_project_json(pro, P):
    vector(pro, P, "wells", [PT, PT])
    r = pro.get(f"/projects/{P['id']}/file")
    assert r.status_code == 200
    assert "zip" in r.headers["content-type"]
    assert ".polygis" in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    man = json.loads(zf.read("project.json"))
    assert man["polygis"] == 1
    assert man["project"]["name"] == "What if"
    assert any(l["name"] == "wells" for l in man["layers"])
    assert any(n.startswith("data/") and n.endswith(".gpkg") for n in zf.namelist())


def test_open_a_file_recreates_the_project(pro, P):
    a = vector(pro, P, "wells", [PT, PT, PT])
    vector(pro, P, "roads", [LINE])
    # a second geodatabase with a layer, to check structure round-trips
    g2 = pro.post(f"/projects/{P['id']}/geodatabases", json={"name": "Survey"}).json()
    pro.patch(f"/geodatabases/{g2['id']}", json={"is_default": True})
    vector(pro, P, "plots", [PT])

    blob = pro.get(f"/projects/{P['id']}/file").content

    r = pro.post("/projects/import-file", files={"file": ("What if.polygis", blob, "application/zip")})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["name"] == "What if" and out["layers"] == 3 and out["rasters_skipped"] == 0

    proj = pro.get(f"/projects/{out['id']}").json()
    assert proj["id"] != P["id"]                      # a new project
    names = sorted(l["name"] for l in proj["layers"])
    assert names == ["plots", "roads", "wells"]
    wells = next(l for l in proj["layers"] if l["name"] == "wells")
    assert wells["feature_count"] == 3 and wells["geometry_type"] == "Point"
    # geodatabases came across, with the right default
    gdbs = pro.get(f"/projects/{out['id']}/geodatabases").json()
    assert {g["name"] for g in gdbs} == {"Default", "Survey"}
    assert next(g for g in gdbs if g["is_default"])["name"] == "Survey"
    # the imported "plots" layer sits in the Survey geodatabase
    survey = next(g for g in gdbs if g["name"] == "Survey")
    assert next(l for l in proj["layers"] if l["name"] == "plots")["geodatabase_id"] == survey["id"]


def test_open_rejects_a_non_polygis_file(pro):
    r = pro.post("/projects/import-file", files={"file": ("x.polygis", b"not a zip", "application/zip")})
    assert r.status_code == 422
