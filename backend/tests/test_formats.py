"""Add data takes every common spatial format: several files chosen together (a Shapefile's parts, a picture and its
world file), FlatGeobuf, GeoJSON lines, MapInfo, file geodatabases, ENVI, images placed by world files or by control
points inside them; and a picture with no coordinates is sent to georeferencing."""
import io
import json
import zipfile

import numpy as np
import pyogrio
import pytest
import rasterio
import shapely
from rasterio.control import GroundControlPoint
from rasterio.transform import from_origin

from test_rasters import pro  # noqa: F401  (fixture)

POINTS = [shapely.Point(3.38 + i * 0.01, 6.52) for i in range(3)]


@pytest.fixture
def P(pro):
    return pro.post("/projects", json={"name": "Formats"}).json()


def send(c, P, *files, epsg=None):
    """files: (name, bytes); the first is `file`, the rest `extra`."""
    up = [("file", files[0])] + [("extra", f) for f in files[1:]]
    return c.post(f"/projects/{P['id']}/layers/import", files=up, data={"epsg": str(epsg)} if epsg else None)


def vector(tmp_path, name, driver, **kw):
    path = tmp_path / name
    pyogrio.raw.write(str(path), shapely.to_wkb(POINTS), [np.array(["a", "b", "c"], dtype=object)], ["name"], driver=driver,
                      geometry_type="Point", crs="EPSG:4326", **kw)
    return path


def test_a_shapefile_as_its_separate_files(pro, P, tmp_path):
    vector(tmp_path, "wells.shp", "ESRI Shapefile")
    parts = [(f"wells{e}", (tmp_path / f"wells{e}").read_bytes()) for e in (".dbf", ".shp", ".prj", ".shx")]   # in any order
    r = send(pro, P, *parts)
    assert r.status_code == 201, r.text
    lay = r.json()[0]
    assert lay["name"] == "wells" and lay["feature_count"] == 3 and lay["geometry_type"] == "Point"


@pytest.mark.parametrize("name,driver", [("towns.fgb", "FlatGeobuf"), ("towns.geojsonl", "GeoJSONSeq"), ("towns.gpkg", "GPKG"),
                                         ("towns.gml", "GML"), ("towns.kml", "KML"), ("towns.gpx", "GPX"), ("towns.sqlite", "SQLite")])
def test_single_file_vector_formats(pro, P, tmp_path, name, driver):
    kw = {"GPX_USE_EXTENSIONS": "YES"} if driver == "GPX" else {}
    path = vector(tmp_path, name, driver, **kw)
    r = send(pro, P, (name, path.read_bytes()))
    assert r.status_code == 201, (name, r.text)
    assert sum(x["feature_count"] for x in r.json()) == 3


def test_mapinfo_and_a_zipped_file_geodatabase(pro, P, tmp_path):
    vector(tmp_path, "roads.tab", "MapInfo File")
    parts = [(p.name, p.read_bytes()) for p in sorted(tmp_path.glob("roads.*"))]
    r = send(pro, P, *parts)
    assert r.status_code == 201 and r.json()[0]["feature_count"] == 3, r.text
    gdb = tmp_path / "survey.gdb"
    pyogrio.raw.write(str(gdb), shapely.to_wkb(POINTS), [np.array(["a", "b", "c"], dtype=object)], ["name"], driver="OpenFileGDB",
                      geometry_type="Point", crs="EPSG:4326", layer="beacons")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for f in gdb.iterdir():
            z.write(f, f"survey.gdb/{f.name}")
    r = send(pro, P, ("survey.gdb.zip", b.getvalue()))
    assert r.status_code == 201, r.text
    assert r.json()[0]["feature_count"] == 3


def picture(fmt="JPEG"):
    b = io.BytesIO()
    from PIL import Image
    a = np.zeros((40, 60, 3), "uint8")
    a[:, 30:] = [200, 30, 30]
    Image.fromarray(a).save(b, fmt)
    return b.getvalue()


def test_a_picture_with_its_world_file_lands_where_it_says(pro, P):
    jgw = "10\n0\n0\n-10\n660005\n829995\n"                                     # 10 m pixels, top-left corner at 660000, 830000
    prj = 'PROJCS["WGS 84 / UTM zone 31N",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",3],PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],PARAMETER["false_northing",0],UNIT["metre",1]]'
    r = send(pro, P, ("plan.jpg", picture()), ("plan.jgw", jgw.encode()), ("plan.prj", prj.encode()))
    assert r.status_code == 201, r.text
    m = r.json()[0]["raster"]
    assert (m["width"], m["height"], m["count"], m["epsg"]) == (60, 40, 3, 32631) and m["bounds"][0] == pytest.approx(660000)
    # in a zip too, with only a PNG world file and the system chosen
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("site.png", picture("PNG"))
        z.writestr("site.pgw", jgw)
    r = send(pro, P, ("site.zip", b.getvalue()), epsg=32631)
    assert r.status_code == 201 and r.json()[0]["raster"]["epsg"] == 32631, r.text


def test_a_picture_without_coordinates_goes_to_georeferencing(pro, P):
    r = send(pro, P, ("photo.jpg", picture()))
    assert r.status_code == 422 and r.json()["code"] == "no_georeference" and "Georeference" in r.json()["detail"]


def test_an_image_placed_by_its_own_control_points(pro, P, tmp_path):
    path = tmp_path / "scan.tif"
    gcps = [GroundControlPoint(row=0, col=0, x=660000, y=830000), GroundControlPoint(row=0, col=60, x=660600, y=830000),
            GroundControlPoint(row=40, col=0, x=660000, y=829600), GroundControlPoint(row=40, col=60, x=660600, y=829600)]
    with rasterio.open(path, "w", driver="GTiff", width=60, height=40, count=1, dtype="uint8") as d:
        d.write(np.full((1, 40, 60), 9, "uint8"))
        d.gcps = (gcps, rasterio.crs.CRS.from_epsg(32631))
    r = send(pro, P, ("scan.tif", path.read_bytes()))
    assert r.status_code == 201, r.text
    m = r.json()[0]["raster"]
    assert m["epsg"] == 32631 and m["res_m"][0] == pytest.approx(10, rel=0.05) and m["bounds"][0] == pytest.approx(660000, abs=15)


def test_envi_and_other_rasters(pro, P, tmp_path):
    path = tmp_path / "dem.dat"
    with rasterio.open(path, "w", driver="ENVI", width=20, height=10, count=1, dtype="float32", crs="EPSG:32631",
                       transform=from_origin(660000, 830000, 30, 30)) as d:
        d.write(np.full((1, 10, 20), 312.5, "float32"))
    parts = [(p.name, p.read_bytes()) for p in tmp_path.glob("dem.*")]
    r = send(pro, P, *parts)
    assert r.status_code == 201, r.text
    assert r.json()[0]["raster"]["width"] == 20
    grd = tmp_path / "surface.grd"
    with rasterio.open(grd, "w", driver="GSBG", width=20, height=10, count=1, dtype="float32",
                       transform=from_origin(3.3, 6.6, 0.001, 0.001)) as d:   # Surfer grid: no system inside, so it is chosen
        d.write(np.full((1, 10, 20), 5, "float32"))
    r = send(pro, P, ("surface.grd", grd.read_bytes()), epsg=4326)
    assert r.status_code == 201, r.text
