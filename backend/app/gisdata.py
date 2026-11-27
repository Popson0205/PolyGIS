"""Reading and writing GIS files with GDAL (through pyogrio, which brings its own GDAL).

Reading: GeoJSON, Shapefile (zipped), GeoPackage, KML/KMZ, CSV (longitude/latitude or WKT columns), DXF, GPX and
anything else GDAL opens. Every layer in the file is read; a layer that mixes points, lines and polygons is split into
one layer per kind. Geometries come back in WGS 84, two-dimensional, valid, with their attributes and inferred fields.

Writing: GeoJSON, GeoPackage, Shapefile (zipped), CSV with WKT, KML."""
import io
import json
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

# Shapefile DBF date fields are a common source of bad values (e.g. 0000-00-00 in official
# boundary data); read them as text so GDAL does not choke on an out-of-range year.
try:
    pyogrio.set_gdal_config_options({"OGR_SHAPE_DBF_DATE_AS_STRING": "YES"})
except Exception:  # noqa: BLE001
    pass

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
    try:
        k = np.dtype(dtype).kind
    except TypeError:                     # GDAL's list types ("list(str)", "list(int64)"…) are kept as text
        return "string"
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
    if isinstance(v, (list, tuple, dict, np.ndarray)):           # lists and nested objects: kept as their JSON text
        return json.dumps(v.tolist() if isinstance(v, np.ndarray) else v, default=str, ensure_ascii=False)
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


def _raw_read(src: str, layer, options: dict):
    """pyogrio.raw.read, but survive a bad date field. A Shapefile's dates are read as text
    (see OGR_SHAPE_DBF_DATE_AS_STRING above); for any other format, a feature whose date
    cannot be parsed (year 0 etc.) would otherwise fail the whole import, so retry without the
    date field(s) rather than lose the layer."""
    try:
        return pyogrio.raw.read(src, layer=layer, **options)
    except ValueError:
        info = pyogrio.read_info(src, layer=layer) if layer else pyogrio.read_info(src)
        fa, da = info.get("fields"), info.get("dtypes")
        names = list(fa) if fa is not None else []
        kinds = [np.dtype(d).kind for d in (list(da) if da is not None else [])]
        drop = {n for n, k in zip(names, kinds) if k == "M"}
        if not drop:
            raise
        keep = [n for n in names if n not in drop]
        opts = {k: v for k, v in options.items() if k != "columns"}
        return pyogrio.raw.read(src, layer=layer, columns=keep, **opts)


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
            meta, _fids, wkb, cols = _raw_read(src, lname, options)
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


def _feature_arrays(fields: list[dict], rows: list[tuple]) -> tuple[list[str], list[np.ndarray]]:
    """Columns for pyogrio: the layer's polygis_id plus each field, typed from `fields`."""
    names = ["polygis_id"] + [f["name"] for f in fields]
    arrays: list[np.ndarray] = [np.array([r[0] for r in rows], dtype=np.int64)]
    for f in fields:
        vals = [r[2].get(f["name"]) for r in rows]
        if f["type"] == "integer" and all(isinstance(v, (int, float)) for v in vals):
            arrays.append(np.array(vals, dtype=np.int64))
        elif f["type"] == "number" and all(v is None or isinstance(v, (int, float)) for v in vals):
            arrays.append(np.array([np.nan if v is None else float(v) for v in vals], dtype=np.float64))
        else:
            arrays.append(np.array(["" if v is None else str(v) for v in vals], dtype=object))
    return names, arrays


def _write_gpkg(target: Path, items: list[tuple], epsg: int) -> bool:
    """Write items = [(gpkg_layer_name, fields, rows)] into one GeoPackage at `target`. Layer names are used as given
    (the caller makes them unique). Empty layers are skipped. Returns whether anything was written."""
    transformer = Transformer.from_crs(4326, epsg, always_xy=True) if epsg != 4326 else None
    wrote = False
    for lname, fields, rows in items:
        if not rows:
            continue
        geoms = [g for _, g, _ in rows]
        if transformer is not None:
            geoms = [shapely.transform(g, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1]))) for g in geoms]
        kinds = {g.geom_type for g in geoms}
        gtype = kinds.pop() if len(kinds) == 1 else "Unknown"
        names, arrays = _feature_arrays(fields, rows)
        wkb = np.array(shapely.to_wkb(geoms), dtype=object)
        pyogrio.raw.write(str(target), wkb, arrays, names, driver="GPKG", geometry_type=gtype,
                          crs=f"EPSG:{epsg}", encoding="UTF-8", layer=lname, append=wrote, promote_to_multi=False)
        wrote = True
    return wrote


def write_geopackage(db_name: str, layers: list[tuple], epsg: int = 4326) -> tuple[bytes, str, str]:
    """Write a whole geodatabase to one GeoPackage. `layers` is [(name, fields, rows)] with
    rows = [(id, shapely geometry in WGS 84, props)]; empty layers are skipped. Returns (bytes, filename, media)."""
    safe_db = "".join(c if c.isalnum() or c in "-_ " else "_" for c in db_name).strip() or "geodatabase"
    used: set[str] = set()
    items: list[tuple] = []
    for name, fields, rows in layers:
        safe_l = "".join(c if c.isalnum() or c in "-_ " else "_" for c in (name or "layer")).strip() or "layer"
        base, i = safe_l, 2
        while safe_l.lower() in used:
            safe_l = f"{base}_{i}"; i += 1
        used.add(safe_l.lower())
        items.append((safe_l, fields, rows))
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / f"{safe_db}.gpkg"
        if not _write_gpkg(target, items, epsg):   # an empty geodatabase still makes a valid, empty GeoPackage
            pyogrio.raw.write(str(target), np.array([], dtype=object), [np.array([], dtype=np.int64)], ["polygis_id"],
                              driver="GPKG", geometry_type="Point", crs="EPSG:4326", encoding="UTF-8", layer="empty")
        return target.read_bytes(), f"{safe_db}.gpkg", "application/geopackage+sqlite3"


def pack_geopackage(items: list[tuple], epsg: int = 4326) -> bytes:
    """Write items = [(key, fields, rows)] to a GeoPackage and return its bytes; `key` is used verbatim as the layer
    name, so an archive can read each layer back by the same key. Always returns a valid file (empty if no rows)."""
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "gdb.gpkg"
        if not _write_gpkg(target, items, epsg):
            pyogrio.raw.write(str(target), np.array([], dtype=object), [np.array([], dtype=np.int64)], ["polygis_id"],
                              driver="GPKG", geometry_type="Point", crs="EPSG:4326", encoding="UTF-8", layer="empty")
        return target.read_bytes()


def read_gpkg_layer(path: Path, key: str, fields: list[dict]) -> list[tuple]:
    """Read one layer of a GeoPackage back as rows [(geom in WGS 84, props)], mapping columns to `fields` by name."""
    meta, _fids, wkb, cols = pyogrio.raw.read(str(path), layer=key)
    if wkb is None or len(wkb) == 0:
        return []
    geoms = shapely.from_wkb(wkb)
    col_by = {n: cols[i] for i, n in enumerate(meta["fields"])}
    out: list[tuple] = []
    for i, g in enumerate(geoms):
        props = {f["name"]: _value(col_by[f["name"]][i], f["type"]) for f in fields if f["name"] in col_by}
        out.append((g, props))
    return out


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


# ---- streaming ingest of large (gigabyte / national) datasets ---------------------------------
# read_file() loads a whole file into memory; these read it in batches so a file far larger than
# memory can be streamed straight into PostGIS. Geometry stays in its SOURCE srid (WKB); PostGIS
# does the reprojection to 4326 in bulk, which is far faster than per-feature Python.

def large_meta(path: Path, layer: str | None = None, given_epsg: int | None = None) -> dict:
    """Header of a large source: normalized geometry type, fields, source EPSG srid and count.
    Raises ReadError if the file has no single geometry type or no resolvable EPSG."""
    info = pyogrio.read_info(str(path), layer=layer) if layer else pyogrio.read_info(str(path))
    gt = info.get("geometry_type")
    kind = KINDS.get((gt or "").replace("3D ", "").strip())
    if kind is None:
        raise ReadError(f"PolyGIS cannot stream a layer of mixed or unknown geometry type ({gt!r}). "
                        "Export a single geometry type (points, lines or polygons) and try again.")
    crs = _crs_of(info.get("crs"), given_epsg)
    epsg = crs.to_epsg() if crs else None
    if not epsg:
        raise ReadError("The file's coordinate system is not a standard EPSG code. "
                        "Reproject it to a known EPSG, or give the source EPSG when starting the import.")
    fa, da = info.get("fields"), info.get("dtypes")
    names = list(fa) if fa is not None else []
    dtypes = list(da) if da is not None else []
    fields = [{"name": str(n), "type": _field_type(d)} for n, d in zip(names, dtypes)]
    return {"geometry_type": kind, "fields": fields, "srid": int(epsg),
            "crs": f"EPSG:{epsg}", "total": int(info.get("features") or 0), "layer": layer}


def stream_batches(path: Path, layer: str | None = None, batch: int = 50_000):
    """Yield (wkb_bytes, props_dict) for every feature with a geometry, reading `batch` at a time.
    WKB is in the file's own srid; the caller reprojects in PostGIS."""
    fields = large_meta(path, layer)["fields"]
    types = {f["name"]: f["type"] for f in fields}
    skip = 0
    while True:
        meta, _fids, geoms, cols = _raw_read(
            str(path), layer, {"max_features": batch, "skip_features": skip})
        n = len(geoms)
        if n == 0:
            break
        mf = meta.get("fields")
        names = [str(x) for x in (mf if mf is not None else [])]
        for i in range(n):
            g = geoms[i]
            if g is None:
                continue
            props = {names[j]: _value(cols[j][i], types.get(names[j], "string")) for j in range(len(names))}
            yield g, props
        skip += n
        if n < batch:
            break
