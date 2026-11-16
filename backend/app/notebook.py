"""Run a user's Python against a layer, server-side, in a locked-down subprocess.

The analyst writes Python in the Notebook pane. It runs here with the chosen layer already loaded as a GeoPandas
``gdf`` (geopandas, pandas, numpy, shapely are available). Whatever they leave in ``result`` comes back: a GeoDataFrame
becomes a new layer on the map, a DataFrame becomes a table, anything else is shown as text, along with whatever they
printed.

Isolation (first version): the code runs in a separate process with `python -I`, a scrubbed environment that carries no
database URL, S3 keys, proxy or other secrets, CPU/memory/output limits, and a wall-clock timeout. It is NOT yet network-
isolated — that needs a sandboxed worker (nsjail/gVisor or a separate container) and is the next hardening step; until
then run the API with no outbound secrets in its environment, or disable the pane with NOTEBOOK_ENABLED=false.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_S = 25
MEM_MB = 768
OUTPUT_CHARS = 20_000

# Only these environment variables reach the subprocess: enough for GDAL/PROJ to work, nothing that is a secret.
_KEEP_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "PROJ_LIB", "PROJ_DATA", "GDAL_DATA", "LD_LIBRARY_PATH",
             "DYLD_LIBRARY_PATH", "GDAL_DRIVER_PATH", "PYTHONPATH")

_RUNNER = r'''
import sys, json, io, os, contextlib, traceback
import geopandas as gpd, pandas as pd, numpy as np

inp, work, code_path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    gdf = gpd.read_file(inp) if os.path.getsize(inp) > 2 else gpd.GeoDataFrame(geometry=[])
except Exception:
    gdf = gpd.GeoDataFrame(geometry=[])

ns = {"__name__": "notebook", "gdf": gdf, "gpd": gpd, "pd": pd, "np": np}
res = {"kind": "none", "error": None, "stdout": ""}
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        exec(compile(open(code_path).read(), "<notebook>", "exec"), ns)
    out = ns.get("result", None)
    if isinstance(out, gpd.GeoDataFrame) and len(out) and out.geometry.notna().any():
        g = out.set_crs(4326, allow_override=True) if out.crs is None else out.to_crs(4326)
        g.to_file(os.path.join(work, "result.geojson"), driver="GeoJSON")
        res["kind"] = "layer"; res["rows"] = int(len(g))
    elif isinstance(out, (pd.DataFrame, gpd.GeoDataFrame)):
        df = pd.DataFrame(out.drop(columns=[c for c in ["geometry"] if c in out]).head(2000))
        open(os.path.join(work, "table.json"), "w").write(df.to_json(orient="split", default_handler=str))
        res["kind"] = "table"; res["rows"] = int(len(out))
    elif isinstance(out, pd.Series):
        open(os.path.join(work, "table.json"), "w").write(out.head(2000).to_frame().to_json(orient="split", default_handler=str))
        res["kind"] = "table"; res["rows"] = int(len(out))
    elif out is not None:
        res["kind"] = "text"; res["value"] = str(out)[:OUT]
except Exception as e:
    res["error"] = "".join(traceback.format_exception_only(type(e), e)).strip()[:4000]
res["stdout"] = buf.getvalue()[-OUT:]
open(os.path.join(work, "meta.json"), "w").write(json.dumps(res))
'''.replace("OUT", str(OUTPUT_CHARS))


def _limits() -> None:  # pragma: no cover  (runs in the child, after fork)
    import resource
    cpu = TIMEOUT_S + 5
    for what, soft in ((resource.RLIMIT_CPU, cpu), (resource.RLIMIT_AS, MEM_MB * 1024 * 1024),
                       (resource.RLIMIT_FSIZE, 128 * 1024 * 1024)):
        try:
            resource.setrlimit(what, (soft, soft))
        except (ValueError, OSError):
            pass
    try:
        os.setsid()
    except OSError:
        pass


def enabled() -> bool:
    return os.environ.get("NOTEBOOK_ENABLED", "true").lower() != "false"


def run(code: str, feature_collection: dict) -> dict:
    """Run `code` with the features loaded as `gdf`. Returns {kind, stdout, error, value?/rows?, geojson?/table?}."""
    with tempfile.TemporaryDirectory(prefix="nb-") as work:
        w = Path(work)
        (w / "input.geojson").write_text(json.dumps(feature_collection))
        (w / "runner.py").write_text(_RUNNER)
        (w / "code.py").write_text(code)
        env = {k: os.environ[k] for k in _KEEP_ENV if k in os.environ}
        env["HOME"] = work; env["TMPDIR"] = work; env["MPLBACKEND"] = "Agg"
        env["OPENBLAS_NUM_THREADS"] = env["OMP_NUM_THREADS"] = "2"
        try:
            subprocess.run([sys.executable, "-I", str(w / "runner.py"), str(w / "input.geojson"), work, str(w / "code.py")],
                           cwd=work, env=env, capture_output=True, timeout=TIMEOUT_S, preexec_fn=_limits, check=False)
        except subprocess.TimeoutExpired:
            return {"kind": "none", "error": f"Stopped: the code ran longer than {TIMEOUT_S} seconds.", "stdout": ""}
        meta_path = w / "meta.json"
        if not meta_path.exists():
            return {"kind": "none", "error": "The code could not be run (it may have used too much memory or crashed).", "stdout": ""}
        res = json.loads(meta_path.read_text())
        if res.get("kind") == "layer" and (w / "result.geojson").exists():
            res["geojson"] = (w / "result.geojson").read_text()
        if res.get("kind") == "table" and (w / "table.json").exists():
            res["table"] = json.loads((w / "table.json").read_text())
        return res
