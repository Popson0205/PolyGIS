"""Reading and writing GIS files with GDAL (through pyogrio, which brings its own GDAL).

Reading: GeoJSON, Shapefile (zipped), GeoPackage, KML/KMZ, CSV (longitude/latitude or WKT columns), DXF, GPX and
anything else GDAL opens. Every layer in the file is read; a layer that mixes points, lines and polygons is split into
one layer per kind. Geometries come back in WGS 84, two-dimensional, valid, with their attributes and inferred fields.

Writing: GeoJSON, GeoPackage, Shapefile (zipped), CSV with WKT, KML."""
import io
import math
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pyogrio
import pyogrio.raw
import shapely
from pyproj import CRS, Transformer

KINDS = {"Point": "Point", "MultiPoint": "Point", "LineString": "LineString", "MultiLineString": "LineString",
         "LinearRing": "LineString", "Polygon": "Polygon", "MultiPolygon": "Polygon"}
WORDS = {"Point": "points", "LineString": "lines", "Polygon": "polygons"}
FIELD_TYPES = ("string", "integer", "number", "boolean", "date")


class ReadError(Exception):
    """The file could not be read; the message says why, in words for the person who uploaded it."""


@dataclass
class ReadLayer:
    name: str
    geometry_type: str                        # Point | LineString | Polygon
    fields: list[dict]                        # [{name, type}]
    geoms: list = field(default_factory=list)  # shapely geometries in WGS 84
    props: list[dict] = field(default_factory=list)
    crs: str | None = None                    # the source's system, e.g. "EPSG:26332"
    dropped: int = 0                          # features without a usable geometry


def _crs_of(meta_crs: str | None, given_epsg: int | None) -> CRS | None:
    if given_epsg:
        return CRS.from_epsg(given_epsg)
    if meta_crs:
        try:
            c = CRS.from_user_input(meta_crs)
        except Exception:  # noqa: BLE001
            return None
        # a .prj in Esri's form drops the datum shift; the matching EPSG system keeps it, so use that when it is clear
        epsg = c.to_epsg(min_confidence=70)
        return CRS.from_epsg(epsg) if epsg else c
    return None


def _label(crs: CRS | None) -> str | None:
    if crs is None:
        return None
    epsg = crs.to_epsg(min_confidence=70)
    return f"EPSG:{epsg}" if epsg else crs.name


def _field_type(dtype) -> str:
    k = np.dtype(dtype).kind if not isinstance(dtype, str) else np.dtype(dtype).kind
    return {"i": "integer", "u": "integer", "f": "number", "b": "boolean", "M": "date"}.get(k, "string")


def _value(v, kind: str):
    """A plain JSON value from what GDAL gave."""
    if v is None:
        return None
    if isinstance(v, (np.floating, float)):
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return int(f) if kind == "integer" and f.is_integer() else f
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, np.datetime64):
        if np.isnat(v):
            return None
        return str(np.datetime_as_string(v, unit="D"))
    if isinstance(v, (datetime, date)):
        return v.isoformat()[:10]
    if isinstance(v, bytes):
        return v.hex()
    s = str(v)
    return s if s != "" else None


# every vector format GDAL reads that people keep spatial data in
EXTENSIONS = (".geojson", ".json", ".geojsonl", ".geojsons", ".ndjson", ".topojson", ".zip", ".shp", ".gpkg", ".kml", ".kmz", ".csv", ".txt",
              ".tsv", ".dxf", ".dgn", ".gpx", ".gml", ".xml", ".fgb", ".tab", ".mif", ".sqlite", ".spatialite", ".db", ".osm", ".pbf",
              ".e00", ".gdb", ".gmt", ".vrt", ".jml", ".georss", ".rss", ".sxf", ".s57", ".000", ".mvt", ".pmtiles", ".gtfs")
IN_ZIP = (".shp", ".gpkg", ".fgb", ".geojson", ".json", ".geojsonl", ".kml", ".gml", ".gpx", ".tab", ".mif", ".sqlite", ".dgn", ".csv", ".dxf",
          ".osm", ".pbf", ".e00", ".topojson")


def _open_path(path: Path) -> tuple[str, dict]:
    """The path GDAL should open, and open options. A .zip is opened inside (Shapefiles come zipped); CSV columns
    named like longitude/latitude or WKT become the geometry."""
    name = path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            inner = [n for n in z.namelist() if not n.startswith("__MACOSX") and not n.endswith("/")]
        gdb = next((n.split(".gdb/")[0] + ".gdb" for n in inner if ".gdb/" in n.lower()), None)
        if gdb:                                                    # an Esri file geodatabase is a folder: zipped whole
            return f"/vsizip/{path}/{gdb}", {}
        for ext in IN_ZIP:
            hit = next((n for n in inner if n.lower().endswith(ext)), None)
            if hit:
                return f"/vsizip/{path}/{hit}", ({} if ext != ".csv" else CSV_OPTIONS)
        raise ReadError("The zip holds no file PolyGIS can read (a Shapefile's .shp, .shx and .dbf, a file geodatabase (.gdb "
                        "folder), GeoPackage, FlatGeobuf, GeoJSON, KML, GPX, MapInfo, CSV or DXF).")
    if name.endswith(".kmz"):
        return f"/vsizip/{path}", {}
    if name.endswith((".csv", ".txt", ".tsv")):
        return str(path), CSV_OPTIONS
    if name.endswith((".mif", ".tab", ".shp")) or name.endswith(".gdb"):
        return str(path), {}                                       # its companion files sit beside it
    return str(path), {}


CSV_OPTIONS = {"X_POSSIBLE_NAMES": "lon,lng,long,longitude,x,easting,X,Lon,Longitude,LONGITUDE,Easting",
               "Y_POSSIBLE_NAMES": "lat,latitude,y,northing,Y,Lat,Latitude,LATITUDE,Northing",
               "GEOM_POSSIBLE_NAMES": "wkt,WKT,geometry,geom,the_geom", "KEEP_GEOM_COLUMNS": "NO", "AUTODETECT_TYPE": "YES"}


def read_file(path: Path, display_name: str, given_epsg: int | None = None, max_features: int = 500_000) -> list[ReadLayer]:
    """Every layer in the file, ready to store. `given_epsg` is the system to assume when the file does not say
    (a CSV, a Shapefile without its .prj) or to override it."""
    src, options = _open_path(path)
    try:
        names = [n for n, gt in pyogrio.list_layers(src) if gt is not None] or [n for n, _ in pyogrio.list_layers(src)]
    except Exception as e:  # noqa: BLE001
        raise ReadError(f"PolyGIS could not open {display_name}: {_gdal_words(e)}") from e
    base = Path(display_name).stem
    out: list[ReadLayer] = []
    total = 0
    for lname in names[:20]:
        try:
            meta, _fids, wkb, cols = pyogrio.raw.read(src, layer=lname, **options)
        except Exception as e:  # noqa: BLE001
            raise ReadError(f"PolyGIS could not read the layer {lname} in {display_name}: {_gdal_words(e)}") from e
        if wkb is None or len(wkb) == 0:
            continue
        total += len(wkb)
        if total > max_features:
            raise ReadError(f"{display_name} has more than {max_features:,} features, the most one upload can hold.")
        src_crs = _crs_of(meta.get("crs"), given_epsg)
        if src_crs is None:
            # no system given: GPS-style coordinates are taken as WGS 84; anything else needs to be said
            sample = shapely.from_wkb([g for g in wkb[:200] if g is not None])
            b = shapely.total_bounds(sample) if len(sample) else [0, 0, 0, 0]
            if not (-180 <= b[0] <= 180 and -90 <= b[1] <= 90 and -180 <= b[2] <= 180 and -90 <= b[3] <= 90):
                raise ReadError(f"{display_name} does not say which coordinate system it uses, and its coordinates are "
                                "not longitude and latitude. Choose the system (for example Minna / UTM zone 32N, "
                                "EPSG:26332) and upload it again.")
            src_crs = CRS.from_epsg(4326)
        to_wgs = None if src_crs.equals(CRS.from_epsg(4326)) else Transformer.from_crs(src_crs, 4326, always_xy=True)
        fields = [{"name": n, "type": _field_type(t)} for n, t in zip(meta["fields"], meta["dtypes"])]
        geoms = shapely.from_wkb(wkb)
        geoms = shapely.force_2d(geoms)
        if to_wgs is not None:
            geoms = shapely.transform(geoms, lambda xy: np.column_stack(to_wgs.transform(xy[:, 0], xy[:, 1])))
        by_kind: dict[str, ReadLayer] = {}
        dropped = 0
        for i, g in enumerate(geoms):
            g = clean(g)
            if g is None:
                dropped += 1
                continue
            parts = [g] if g.geom_type != "GeometryCollection" else [p for p in shapely.get_parts(g) if not p.is_empty]
            row = {f["name"]: _value(col[i], f["type"]) for f, col in zip(fields, cols)}
            for p in parts:
                kind = KINDS.get(p.geom_type)
                if not kind:
                    dropped += 1
                    continue
                lay = by_kind.get(kind)
                if lay is None:
                    lay = by_kind[kind] = ReadLayer(name="", geometry_type=kind, fields=fields, crs=_label(src_crs))
                lay.geoms.append(p)
                lay.props.append(row)
        title = base if len(names) == 1 else f"{base} · {lname}"
        for kind, lay in by_kind.items():
            lay.name = title if len(by_kind) == 1 else f"{title} ({WORDS[kind]})"
            lay.dropped = dropped
            out.append(lay)
    if not out:
        raise ReadError(f"{display_name} has no features with a location PolyGIS can draw.")
    return out


def clean(g):
    """Valid, non-empty, 2D; None when nothing usable is left."""
    if g is None or g.is_empty:
        return None
    if not g.is_valid:
        g = shapely.make_valid(g)
        if g.geom_type == "GeometryCollection":                 # make_valid can leave stray lines beside a polygon
            polys = [p for p in shapely.get_parts(g) if p.geom_type in ("Polygon", "MultiPolygon")]
            if polys:
                g = shapely.union_all(polys)
    return None if g.is_empty else g


def _gdal_words(e: Exception) -> str:
    s = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return s[:300]


# ---------- writing ----------
FORMATS = {
    "geojson": ("GeoJSON", ".geojson", "application/geo+json"),
    "gpkg": ("GPKG", ".gpkg", "application/geopackage+sqlite3"),
    "shp": ("ESRI Shapefile", ".zip", "application/zip"),
    "csv": ("CSV", ".csv", "text/csv"),
    "kml": ("KML", ".kml", "application/vnd.google-earth.kml+xml"),
}


def write_file(fmt: str, name: str, fields: list[dict], rows: list[tuple], epsg: int = 4326) -> tuple[bytes, str, str]:
    """Write features [(id, shapely geometry in WGS 84, props)] as a file; returns (bytes, filename, media type)."""
    driver, ext, media = FORMATS[fmt]
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip() or "layer"
    geoms = [g for _, g, _ in rows]
    if epsg != 4326 and fmt not in ("geojson", "kml"):
        t = Transformer.from_crs(4326, epsg, always_xy=True)
        geoms = [shapely.transform(g, lambda xy: np.column_stack(t.transform(xy[:, 0], xy[:, 1]))) for g in geoms]
    else:
        epsg = 4326
    names = ["polygis_id"] + [f["name"] for f in fields]
    arrays = [np.array([r[0] for r in rows], dtype=np.int64)]
    for f in fields:
        vals = [r[2].get(f["name"]) for r in rows]
        if f["type"] == "integer" and all(v is None or isinstance(v, (int, float)) for v in vals) and all(v is not None for v in vals):
            arrays.append(np.array(vals, dtype=np.int64))
        elif f["type"] == "number" and all(v is None or isinstance(v, (int, float)) for v in vals):
            arrays.append(np.array([np.nan if v is None else float(v) for v in vals], dtype=np.float64))
        else:
            arrays.append(np.array(["" if v is None else str(v) for v in vals], dtype=object))
    kinds = {g.geom_type for g in geoms}
    gtype = kinds.pop() if len(kinds) == 1 else ("Unknown" if fmt != "shp" else _shp_type(geoms))
    if fmt == "shp":
        geoms = [_promote(g) for g in geoms]
        gtype = geoms[0].geom_type if geoms else "Point"
    wkb = np.array(shapely.to_wkb(geoms), dtype=object) if geoms else np.array([], dtype=object)
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / f"{safe}{ext if fmt != 'shp' else '.shp'}"
        opts = {"layer_options": {"GEOMETRY": "AS_WKT"}} if fmt == "csv" else {}
        pyogrio.raw.write(str(target), wkb, arrays, names, driver=driver, geometry_type=gtype,
                          crs=f"EPSG:{epsg}", encoding="UTF-8", layer=safe, **opts)
        if fmt == "shp":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for f in Path(d).iterdir():
                    z.write(f, f.name)
            return buf.getvalue(), f"{safe}.zip", media
        return target.read_bytes(), f"{safe}{ext}", media


def _shp_type(geoms) -> str:
    return _promote(geoms[0]).geom_type if geoms else "Point"


def _promote(g):
    """A Shapefile holds one kind: single parts become multi-parts so points and multipoints can sit together."""
    return {"Point": shapely.MultiPoint, "LineString": shapely.MultiLineString, "Polygon": shapely.MultiPolygon}.get(
        g.geom_type, lambda x: x)([g]) if g.geom_type in ("Point", "LineString", "Polygon") else g


def save_upload(data: bytes, filename: str) -> Path:
    """Keep an upload in a temporary folder under its own name (GDAL picks the reader from the extension)."""
    d = Path(tempfile.mkdtemp(prefix="polygis-"))
    p = d / (Path(filename).name or "upload")
    p.write_bytes(data)
    return p


def remove_upload(p: Path) -> None:
    try:
        for f in p.parent.iterdir():
            f.unlink()
        os.rmdir(p.parent)
    except OSError:
        pass
