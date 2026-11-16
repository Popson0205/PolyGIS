import maplibregl, { type Map as MapLibre, type Marker } from "maplibre-gl";
import { Crosshair, Download, FileImage, FileText, FileUp, Focus, Globe2, Keyboard, ListChecks, MapPin, MapPinPlus, Maximize, Plus, RotateCcw, Save, SaveAll,
  Sparkles, Trash2, Upload, X, XCircle, ZoomIn } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { ApiError, api, georefImageUrl } from "../lib/api";
import { loadCrs, toLngLat, fromLngLat } from "../lib/coords";
import { fromDms, parsePoints, pointsText, suggest, toDms } from "../lib/georef";
import { BASEMAPS, basemapStyle } from "../lib/mapstyle";
import type { CrsDef, GeorefFit, GeorefMethod, GeorefPoint, GeorefSource, Layer } from "../lib/types";
import { CrsPicker, Dialog } from "./Dialogs";
import { Group, Tb, RibbonBody } from "./Ribbon";
import { Button, ErrorText, IconButton, useStored, useToast } from "./ui";

type Pending = { col: number; row: number; lng?: undefined } | { lng: number; lat: number; col?: undefined } | null;
const MOST = 2048;
const shrink = (g: GeorefSource) => Math.max(1, Math.max(g.width, g.height) / MOST);
const newId = () => Math.random().toString(36).slice(2, 10);

/** Georeferencing: place an image without coordinates (a scanned map, a plan, a photo), or a raster in the wrong place,
 *  by matching points on the image to the same places on the map. */
export default function Georeference({ projectId, layers, canEdit, view, file, layerId, onClose, onDone }: {
  projectId: string; layers: Layer[]; canEdit: boolean; view: { center: [number, number]; zoom: number } | null; file?: File | null; layerId?: string | null;
  onClose: () => void; onDone: (l: Layer) => void;
}) {
  const toast = useToast();
  const [src, setSrc] = useState<GeorefSource | null>(null);
  const [earlier, setEarlier] = useState<GeorefSource[] | null>(null);
  const [pts, setPts] = useState<GeorefPoint[]>([]);
  const [method, setMethod] = useState<GeorefMethod>("affine");
  const [pending, setPending] = useState<Pending>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [fit, setFit] = useState<GeorefFit | null>(null);
  const [fitError, setFitError] = useState<unknown>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(true);
  const [opacity, setOpacity] = useState(0.7);
  const [epsg, setEpsg] = useState("");
  const [name, setName] = useState("");
  const [resampling, setResampling] = useState<"bilinear" | "nearest">("bilinear");
  const [adding, setAdding] = useState(true);                    // Add control points: clicks place points (off: they only pan and pick)
  const [tableOpen, setTableOpen] = useState(true);
  const [typing, setTyping] = useState(false);                   // the Target coordinates dialog
  const [srsOpen, setSrsOpen] = useState(false);
  const [srs, setSrs] = useState<CrsDef>({ epsg: 4326, name: "WGS 84", proj4: "", geographic: true, unit: "degree" });
  const [fitSignal, setFitSignal] = useState(0);
  const [focus, setFocus] = useState<{ id: string; n: number } | null>(null);
  const importRef = useRef<HTMLInputElement>(null);

  useEffect(() => { void api.georefList(projectId).then(setEarlier, () => setEarlier([])); }, [projectId]);
  const startedWith = useRef<File | null>(null);
  useEffect(() => {                                              // sent here from Add data with a picture that has no coordinates
    if (file && startedWith.current !== file && canEdit) { startedWith.current = file; void start(file); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file]);
  const startedLayer = useRef<string | null>(null);
  useEffect(() => {                                              // sent here to place a picture added without coordinates
    if (!layerId || startedLayer.current === layerId || !canEdit || earlier == null) return;
    startedLayer.current = layerId;
    const going = earlier.find((g) => g.layer_id === layerId);    // carry on with its earlier points, if any
    if (going) open(going); else void start(undefined, layerId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layerId, earlier]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || (e.target as HTMLElement)?.closest?.("input, textarea, select")) return;
      if (pending) setPending(null); else onClose();
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose, pending]);

  function open(g: GeorefSource) {
    setSrc(g); setPts(g.points.map((p) => ({ ...p, on: p.on !== false }))); setMethod(g.method); const unplaced = !!layers.find((l) => l.id === g.layer_id)?.raster?.unplaced;
    setName(unplaced ? g.name : g.layer_id ? `${g.name} (re-placed)` : `${g.name} (georeferenced)`);
    setPending(null); setSel(null); setFit(null); setFitError(null); setError(null);
  }
  async function start(file?: File, layerId?: string) {
    setBusy(true); setError(null);
    try { open(file ? await api.georefStart(projectId, file) : await api.georefFromLayer(projectId, layerId!)); }
    catch (e) { setError(e instanceof ApiError ? e : new Error("Could not open the image.")); }
    finally { setBusy(false); }
  }

  // the result goes into the chosen system when it is projected (else the UTM zone of the points); typed and listed
  // coordinates use it too
  const outEpsg = epsg ? Number(epsg) : srs.geographic ? null : srs.epsg;
  const methods = src?.methods ?? [];
  const onCount = pts.filter((p) => p.on).length;
  const need = methods.find((m) => m.id === method)?.min_points ?? 3;

  // save the work and refit shortly after each change
  useEffect(() => {
    if (!src) return;
    const t = window.setTimeout(async () => {
      if (canEdit) void api.georefSave(src.id, { points: pts, method }).catch(() => undefined);
      if (onCount < need) { setFit(null); setFitError(null); return; }
      try {
        setFit(await api.georefFit(src.id, { points: pts, method, epsg: outEpsg, preview }));
        setFitError(null);
      } catch (e) { setFit(null); setFitError(e); }
    }, 450);
    return () => window.clearTimeout(t);
  }, [src, pts, method, preview, outEpsg, canEdit, onCount, need]);

  const add = useCallback((p: Omit<GeorefPoint, "id" | "on">) => {
    const id = newId();
    setPts((x) => [...x, { ...p, id, on: true }]); setSel(id); setPending(null);
  }, []);
  const placeImage = (col: number, row: number) => {
    if (pending && pending.lng !== undefined) add({ col, row, lng: pending.lng, lat: pending.lat });
    else setPending({ col, row });
  };
  const placeMap = (lng: number, lat: number) => {
    if (pending && pending.col !== undefined) add({ col: pending.col, row: pending.row, lng, lat });
    else setPending({ lng, lat });
  };
  const guess = pending && pending.col !== undefined ? suggest(pts, pending.col, pending.row) : null;
  const move = (id: string, x: Partial<GeorefPoint>) => setPts((all) => all.map((p) => (p.id === id ? { ...p, ...x } : p)));
  const err = useMemo(() => new Map((fit?.points ?? []).map((p) => [p.id, p])), [fit]);
  // a point stands out when it is well off compared with the typical point (the median, which one bad point does not inflate)
  const suspect = useMemo(() => {
    if (!fit || fit.exact || fit.points.length < 4) return new Set<string>();
    const e = fit.points.map((p) => p.error_m).sort((a, b) => a - b);
    const mid = e.length % 2 ? e[(e.length - 1) / 2] : (e[e.length / 2 - 1] + e[e.length / 2]) / 2;
    return new Set(fit.points.filter((p) => p.error_m > Math.max(2.5 * mid, 2 * fit.cell_m)).map((p) => p.id));
  }, [fit]);

  async function warp(replace?: boolean) {
    if (!src) return;
    if (replace && src.layer_id && !layers.find((l) => l.id === src.layer_id)?.raster?.unplaced
      && !window.confirm(`Save over ${layers.find((l) => l.id === src.layer_id)?.name ?? "the layer"}? It moves to its new place; use Save as new to keep it as it is.`)) return;
    setBusy(true); setError(null);
    try {
      const r = await api.georefWarp(src.id, { points: pts, method, epsg: outEpsg, name: name.trim() || undefined, resampling, replace });
      const moved = !!src.layer_id && r.layer.id === src.layer_id;
      toast(`${r.layer.name} ${moved ? "moved into place" : "added to the map"} (RMS error ${r.fit.exact ? "0, the spline passes through every point" : `${r.fit.rmse_m} m`}).`, "success");
      onDone(r.layer);
    } catch (e) { setError(e instanceof ApiError ? e : new Error("Could not save the georeferenced image.")); }
    finally { setBusy(false); }
  }
  async function discard() {
    if (!src || !window.confirm(`Discard the georeferencing of ${src.name} and its ${pts.length} points? Layers already saved from it stay.`)) return;
    try { await api.georefDiscard(src.id); setEarlier((x) => (x ?? []).filter((g) => g.id !== src.id)); setSrc(null); } catch (e) { setError(e); }
  }

  const selected = pts.find((p) => p.id === sel) ?? null;
  async function chooseSrs(code: number | null) {
    try { setSrs(await loadCrs(code ?? 4326)); setSrsOpen(false); } catch (e) { setError(e); }
  }
  function typed(lng: number, lat: number) {
    setTyping(false);
    let id = selected?.id;
    if (pending?.col !== undefined) { id = newId(); setPts((x) => [...x, { id: id!, col: pending.col, row: pending.row, lng, lat, on: true }]); setSel(id); setPending(null); }
    else if (selected) move(selected.id, { lng, lat });
    if (id) setFocus({ id, n: Date.now() });                     // the map goes to the typed place
  }
  async function importPoints(f: File) {
    try {
      const rows = parsePoints(await f.text());
      if (srs.geographic && rows.some((r) => Math.abs(r.x) > 180 || Math.abs(r.y) > 90))
        throw new Error("These look like eastings and northings: choose their coordinate system with Set SRS first.");
      if (srs.epsg !== 4326) await loadCrs(srs.epsg);
      const added = rows.map((r) => { const [lng, lat] = toLngLat(srs.epsg, r.x, r.y); return { id: newId(), col: r.col, row: r.row, lng, lat, on: true }; });
      setPts((x) => [...x, ...added]);
      toast(`${added.length} control point${added.length === 1 ? "" : "s"} imported (map coordinates read as ${srs.name}).`, "success");
    } catch (e) { setError(e instanceof Error ? e : new Error("The file could not be read.")); }
  }
  function download(name_: string, text: string, type: string) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type }));
    a.download = name_;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  function exportPoints() {
    if (!src) return;
    const rows = pts.filter((p) => p.on).map((p) => { const [x, y] = fromLngLat(srs.epsg, p.lng, p.lat); return { col: p.col, row: p.row, x, y }; });
    download(`${src.name} control points (EPSG ${srs.epsg}).txt`, pointsText(rows), "text/plain");
  }
  function report() {
    if (!src) return;
    const esc = (t: string) => t.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));
    const f = (v: number, d = 3) => v.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
    const rows = pts.map((p, i) => {
      const [x, y] = fromLngLat(srs.epsg, p.lng, p.lat);
      const e = err.get(p.id);
      return `<tr${suspect.has(p.id) ? ' class="bad"' : ""}><td>${i + 1}</td><td>${f(p.col, 2)}</td><td>${f(p.row, 2)}</td><td>${f(x, srs.geographic ? 7 : 3)}</td>
        <td>${f(y, srs.geographic ? 7 : 3)}</td><td>${p.on ? (e ? f(e.dx_m) : "–") : "off"}</td><td>${p.on ? (e ? f(e.dy_m) : "–") : "off"}</td><td>${p.on ? (e ? f(e.error_m) : "–") : "off"}</td></tr>`;
    }).join("");
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>Georeferencing report: ${esc(src.name)}</title><style>
      body{font:13px system-ui,sans-serif;color:#172033;margin:32px}h1{font-size:20px;margin:0 0 4px}p{margin:2px 0;color:#5b6474}
      dl{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;margin:16px 0}dt{color:#5b6474}dd{margin:0;font-weight:600}
      table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{padding:5px 8px;border-bottom:1px solid #dde2ea;text-align:right}
      th{background:#f3f5f9}tr.bad td{color:#c0392b;font-weight:700}@media print{button{display:none}}</style></head><body>
      <button onclick="print()" style="float:right">Print</button><h1>Georeferencing report</h1><p>${esc(src.name)} · ${src.width} × ${src.height} px · ${new Date().toLocaleString()}</p>
      <dl><dt>Transformation</dt><dd>${esc(methods.find((m) => m.id === method)?.label ?? method)}</dd>
      <dt>Control points</dt><dd>${onCount} in use of ${pts.length}</dd>
      <dt>RMS error</dt><dd>${fit ? (fit.exact ? "0 (passes through every point)" : `${f(fit.rmse_m ?? 0)} m · ${f(fit.rmse_px ?? 0, 2)} px`) : "–"}</dd>
      <dt>Pixel size</dt><dd>${fit ? `${fit.cell_m} m` : "–"}</dd><dt>Result's coordinate system</dt><dd>${esc(fit?.crs ?? "–")}</dd>
      <dt>Coordinates below</dt><dd>EPSG:${srs.epsg} · ${esc(srs.name)}</dd></dl>
      <table><thead><tr><th>#</th><th>Image column</th><th>Image row</th><th>${srs.geographic ? "Longitude" : "Easting (X)"}</th><th>${srs.geographic ? "Latitude" : "Northing (Y)"}</th>
      <th>Residual X (m)</th><th>Residual Y (m)</th><th>Error (m)</th></tr></thead><tbody>${rows}</tbody></table></body></html>`;
    const w = window.open("", "_blank");
    if (w) { w.document.write(html); w.document.close(); } else download(`${src.name} georeferencing report.html`, html, "text/html");
  }

  const step = !src ? "" : !adding ? "Add control points is off: click a point to select it, drag to pan." : pending?.col !== undefined
    ? guess ? "Now click the same place on the map, use the suggested spot, or Type coordinates." : "Now click the same place on the map, or Type coordinates."
    : pending?.lng !== undefined ? "Now click the same place on the image."
    : pts.length < 3 ? "Click a feature you can recognise on the image (a road junction, a building corner, a bridge), then the same place on the map."
    : "Add more points spread across the image; drag a point to adjust it.";

  return (
    <div className="lo gr" role="dialog" aria-modal="true" aria-label="Georeference">
      <div className="lo-bar">
        <strong>Georeference</strong>
        {src && <span className="ws-muted">{src.name} · {src.width.toLocaleString()} × {src.height.toLocaleString()} px</span>}
        {src && <Button small variant="ghost" onClick={() => { setSrc(null); setFit(null); }}>Another image</Button>}
        <span className="ge-spacer" />
        <IconButton label="Close georeferencing" onClick={onClose}><X size={18} /></IconButton>
      </div>
      {src && <div className="ws-ribbon gr-ribbon"><RibbonBody label="Georeference">
        <Group name="Prepare">
          <Tb icon={<Maximize />} label="Fit to display" onClick={() => setFitSignal((n) => n + 1)} title="Show the whole image, and the map at the points" />
          <Tb icon={<Globe2 />} label="Set SRS" onClick={() => setSrsOpen(true)} title={`Coordinate system: EPSG:${srs.epsg} ${srs.name}`} />
        </Group>
        <Group name="Adjust">
          <Tb icon={<MapPinPlus />} label="Add control points" on={adding} onClick={() => { setAdding((a) => !a); setPending(null); }} disabled={!canEdit} />
          <Tb icon={<Keyboard />} label="Type coordinates" onClick={() => setTyping(true)} disabled={!canEdit || !(pending?.col !== undefined || selected)}
            title="Type the map position of the point placed on the image (or of the selected point)" />
          <Tb icon={<FileUp />} label="Import control points" onClick={() => importRef.current?.click()} disabled={!canEdit} />
          <label className="ws-tb gr-tb-select"><span className="ws-tb-icon" aria-hidden="true"><Sparkles /></span>
            <select value={method} onChange={(e) => setMethod(e.target.value as GeorefMethod)} aria-label="Transformation">
              {methods.map((m) => <option key={m.id} value={m.id}>{m.label}{onCount < m.min_points ? ` (needs ${m.min_points})` : ""}</option>)}
            </select>Transformation</label>
          <Tb icon={<Focus />} label="Auto apply" on={preview} onClick={() => setPreview((x) => !x)} title="Show the placed image on the map as points change" />
        </Group>
        <Group name="Review">
          <Tb icon={<ListChecks />} label="Control point table" on={tableOpen} onClick={() => setTableOpen((x) => !x)} />
          <Tb icon={<ZoomIn />} label="Zoom to" onClick={() => selected && setFocus({ id: selected.id, n: Date.now() })} disabled={!selected} />
          <Tb icon={<XCircle />} label="Delete" onClick={() => { if (selected) { setPts((x) => x.filter((y) => y.id !== selected.id)); setSel(null); } }} disabled={!selected || !canEdit} />
          <Tb icon={<Trash2 />} label="Delete all" onClick={() => { if (pts.length && window.confirm(`Delete all ${pts.length} control points?`)) { setPts([]); setSel(null); } }} disabled={!pts.length || !canEdit} />
        </Group>
        <Group name="Save">
          <Tb icon={<Save />} label="Save" onClick={() => void warp(src.layer_id ? true : undefined)} disabled={!canEdit || busy || onCount < need || !fit}
            title={src.layer_id ? "Move the layer itself to its place" : "Add the placed image to the map"} />
          {src.layer_id && <Tb icon={<SaveAll />} label="Save as new" onClick={() => void warp(false)} disabled={!canEdit || busy || onCount < need || !fit} />}
          <Tb icon={<Download />} label="Export control points" onClick={exportPoints} disabled={!pts.length} />
          <Tb icon={<FileText />} label="Generate report" onClick={report} disabled={!pts.length} />
        </Group>
        <Group name="Close">
          <Tb icon={<X />} label="Close Georeference" onClick={onClose} />
        </Group>
        <input ref={importRef} type="file" className="sr-only" accept=".txt,.csv,.tsv,.pts" onChange={(e) => { const f = e.target.files?.[0]; if (f) void importPoints(f); e.target.value = ""; }} />
      </RibbonBody></div>}
      {src && <div className="gr-stepbar" role="status"><Crosshair size={14} aria-hidden="true" />{step}
        {pending?.col !== undefined && canEdit && <Button small variant="ghost" onClick={() => setTyping(true)}><Keyboard size={14} aria-hidden="true" />Type coordinates</Button>}
        {pending && <Button small variant="ghost" onClick={() => setPending(null)}><RotateCcw size={14} aria-hidden="true" />Cancel this point (Esc)</Button>}
        <span className="ge-spacer" /><span className="ws-muted">SRS: EPSG:{srs.epsg} · {srs.name}</span></div>}
      {typing && <TargetDialog srs={srs} start={selected && pending?.col === undefined ? [selected.lng, selected.lat] : guess} editing={!!selected && pending?.col === undefined}
        onCancel={() => setTyping(false)} onOk={typed} />}
      {srsOpen && <Dialog title="Set SRS: the coordinate system for typed, listed and exported coordinates and the result" onClose={() => setSrsOpen(false)}>
        <CrsPicker value={srs.epsg} onChange={(v) => void chooseSrs(v)} label="Coordinate system" />
        <p className="ws-muted">WGS 84 (EPSG:4326) types and lists longitude and latitude, and the result goes into the UTM zone of the points. A projected system
          (e.g. Minna / Nigeria West Belt, EPSG:26391, or UTM 31N, EPSG:32631) types eastings and northings, and the result is made in it.</p>
      </Dialog>}
      {!src ? (
        <StartScreen layers={layers} earlier={earlier} busy={busy} canEdit={canEdit} error={error} onFile={(f) => void start(f)}
          onLayer={(id) => void start(undefined, id)} onOpen={open} />
      ) : (
        <div className={`gr-body${tableOpen ? "" : " no-side"}`}>
          <ImagePane src={src} pts={pts} pending={pending} sel={sel} err={err} adding={adding && canEdit} fitSignal={fitSignal} focus={focus}
            onPlace={placeImage} onMove={(id, col, row) => move(id, { col, row })} onSelect={setSel} />
          <MapPane view={view} pts={pts} pending={pending} guess={guess} sel={sel} preview={preview && fit?.preview ? fit.preview : null} opacity={opacity}
            adding={adding && canEdit} fitSignal={fitSignal} focus={focus} onPlace={placeMap} onMove={(id, lng, lat) => move(id, { lng, lat })} onSelect={setSel} />
          {tableOpen && <aside className="lo-side ws-scroll gr-side" aria-label="Control points and result">
            {guess && <div className="gr-guess"><span>Suggested map position from the points so far.</span>
              <Button small variant="primary" onClick={() => placeMap(guess[0], guess[1])}><MapPin size={14} aria-hidden="true" />Use it</Button></div>}
            <div className="ws-f"><span>Transformation: <strong>{methods.find((m) => m.id === method)?.label}</strong></span>
              <span className="ws-help">{method === "tps" ? "Bends the image to pass through every point: for old or warped maps. Check each point well, as there is no residual to catch a wrong one."
                : method === "similarity" ? "Keeps the image's shape: for photos or plans that only need moving, turning and resizing."
                : method === "affine" ? "Also allows skew and different scales across and down: right for most scans."
                : "Bends the image smoothly: for distorted scans, with points spread over the whole image."}</span>
            </div>
            {suspect.size > 0 && <p className="gr-warn">Point{suspect.size > 1 ? "s" : ""} {pts.map((p, i) => (suspect.has(p.id) ? i + 1 : null)).filter(Boolean).join(", ")} {suspect.size > 1 ? "are" : "is"} much further off
              than the rest. Drag {suspect.size > 1 ? "them" : "it"} to the right place, or untick {suspect.size > 1 ? "them" : "it"} to leave {suspect.size > 1 ? "them" : "it"} out.</p>}
            <div className="gr-fit" aria-live="polite">
              {onCount < need ? <p className="ws-muted">{onCount} of the {need} points this transformation needs.</p>
                : fit ? <dl>
                  <dt>RMS error</dt><dd className={fit.rmse_m != null && fit.rmse_m > 3 * fit.cell_m ? "is-bad" : ""}>{fit.exact ? "0 (passes through every point)" : `${fit.rmse_m} m · ${fit.rmse_px} px`}</dd>
                  <dt>Pixel size</dt><dd>{fit.cell_m} m</dd><dt>Coordinates</dt><dd>{fit.crs}</dd></dl>
                : <ErrorText error={fitError} />}
            </div>
            <table className="gr-table">
              <thead><tr><th>#</th><th>Image (col, row)</th><th>{srs.geographic ? "Map (lat, lng)" : "Map (E, N)"}</th><th>Error</th><th aria-label="Use"></th><th aria-label="Remove"></th></tr></thead>
              <tbody>
                {pts.map((p, i) => {
                  const e = err.get(p.id);
                  const worst = suspect.has(p.id);
                  return (
                    <tr key={p.id} className={`${sel === p.id ? "is-sel" : ""}${p.on ? "" : " is-off"}`} onClick={() => setSel(p.id)}
                      onDoubleClick={() => { if (canEdit) { setSel(p.id); setPending(null); setTyping(true); } }} title="Double-click to type its map coordinates">
                      <td>{i + 1}</td><td>{Math.round(p.col)}, {Math.round(p.row)}</td>
                      <td>{srs.geographic ? `${p.lat.toFixed(6)}, ${p.lng.toFixed(6)}` : fromLngLat(srs.epsg, p.lng, p.lat).map((v) => v.toFixed(2)).join(", ")}</td>
                      <td className={worst ? "is-bad" : ""} title={worst ? "Much further off than the other points: check where it is on the image and the map" : undefined}>
                        {p.on ? (e ? `${e.error_m} m` : "–") : "off"}</td>
                      <td><input type="checkbox" checked={p.on} aria-label={`Use point ${i + 1}`} onChange={(ev) => move(p.id, { on: ev.target.checked })} onClick={(ev) => ev.stopPropagation()} /></td>
                      <td><IconButton label={`Remove point ${i + 1}`} onClick={(ev) => { ev.stopPropagation(); setPts((x) => x.filter((y) => y.id !== p.id)); }}><Trash2 size={14} /></IconButton></td>
                    </tr>
                  );
                })}
                {!pts.length && <tr><td colSpan={6} className="ws-muted">No points yet.</td></tr>}
              </tbody>
            </table>
            <fieldset className="lo-set"><legend>Preview</legend>
              <label className="lo-check"><input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} />Show the placed image on the map (Auto apply)</label>
              <label className="ws-f">Transparency<input type="range" min={0.1} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} /></label>
            </fieldset>
            <fieldset className="lo-set"><legend>Result</legend>
              <label className="ws-f">Layer name<input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} /></label>
              <label className="ws-f">Coordinate system (EPSG)<input value={epsg} inputMode="numeric"
                placeholder={srs.geographic ? (fit ? `${fit.crs} (UTM, chosen for you)` : "UTM, chosen for you") : `EPSG:${srs.epsg} (the SRS)`}
                onChange={(e) => setEpsg(e.target.value.replace(/\D/g, "").slice(0, 6))} /></label>
              <label className="ws-f">Resampling<select value={resampling} onChange={(e) => setResampling(e.target.value as "bilinear" | "nearest")}>
                <option value="bilinear">Smooth (scans, photos)</option><option value="nearest">Exact values (classified or data rasters)</option></select></label>
              <ErrorText error={error} />
              <Button variant="primary" busy={busy} disabled={!canEdit || onCount < need || !fit} onClick={() => void warp(src.layer_id ? true : undefined)}>
                <Plus size={14} aria-hidden="true" />{src.layer_id ? "Save" : "Add to the map"}</Button>
              {canEdit && <Button small variant="ghost" onClick={() => void discard()}><Trash2 size={14} aria-hidden="true" />Discard this work</Button>}
            </fieldset>
          </aside>}
        </div>
      )}
    </div>
  );
}

function StartScreen({ layers, earlier, busy, canEdit, error, onFile, onLayer, onOpen }: {
  layers: Layer[]; earlier: GeorefSource[] | null; busy: boolean; canEdit: boolean; error: unknown;
  onFile: (f: File) => void; onLayer: (id: string) => void; onOpen: (g: GeorefSource) => void;
}) {
  const rasters = layers.filter((l) => l.geometry_type === "Raster");
  const [layerId, setLayerId] = useState("");
  return (
    <div className="gr-start ws-scroll">
      <section className="gr-card">
        <h2><FileImage size={18} aria-hidden="true" />Place an image on the map</h2>
        <p>A scanned map, a site plan, an aerial or drone photo: anything without coordinates. You will match points on it to the same places on the map.</p>
        <label className={`ge-btn ge-btn-primary${!canEdit || busy ? " is-disabled" : ""}`}>
          <Upload size={14} aria-hidden="true" />{busy ? "Opening…" : "Choose an image…"}
          <input type="file" className="sr-only" disabled={!canEdit || busy} accept=".jpg,.jpeg,.png,.tif,.tiff,.bmp,.webp,.gif,.jp2,.img"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
        </label>
        <ErrorText error={error} />
      </section>
      <section className="gr-card">
        <h2><MapPin size={18} aria-hidden="true" />Re-place a raster in this project</h2>
        <p>For a raster that is in the wrong place or slightly off. The original stays as it is; the result is a new layer.</p>
        <div className="ws-row">
          <select value={layerId} onChange={(e) => setLayerId(e.target.value)} aria-label="Raster to re-place">
            <option value="">{rasters.length ? "Choose a raster…" : "No rasters in this project"}</option>
            {rasters.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
          <Button small disabled={!layerId || !canEdit || busy} onClick={() => onLayer(layerId)}>Start</Button>
        </div>
      </section>
      {!!earlier?.length && <section className="gr-card">
        <h2>Carry on</h2>
        <ul className="gr-earlier">
          {earlier.map((g) => <li key={g.id}><button type="button" onClick={() => onOpen(g)}><strong>{g.name}</strong>
            <span>{g.points.length} point{g.points.length === 1 ? "" : "s"} · {new Date(g.updated_at).toLocaleString()}</span></button></li>)}
        </ul>
      </section>}
    </div>
  );
}

/** The image itself: wheel to zoom, drag to pan, click to place a point, drag a point to move it. */
function ImagePane({ src, pts, pending, sel, err, adding, fitSignal, focus, onPlace, onMove, onSelect }: {
  src: GeorefSource; pts: GeorefPoint[]; pending: Pending; sel: string | null; err: Map<string, { error_m: number }>; adding: boolean; fitSignal: number;
  focus: { id: string; n: number } | null;
  onPlace: (col: number, row: number) => void; onMove: (id: string, col: number, row: number) => void; onSelect: (id: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const k = shrink(src);
  const dw = Math.max(1, Math.round(src.width / k)), dh = Math.max(1, Math.round(src.height / k));
  const [v, setV] = useState({ z: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean; point?: string } | null>(null);
  const [live, setLive] = useState<{ id: string; col: number; row: number } | null>(null);
  const [state, setState] = useState<"loading" | "ready" | string>("loading");
  const touched = useRef(false);                                 // once the person zooms or pans, the view is theirs
  const fitView = useCallback(() => {
    const r = box.current?.getBoundingClientRect();
    if (!r || r.width < 10 || r.height < 10) return false;       // not laid out yet: try again when it is
    const z = Math.min(r.width / dw, r.height / dh) * 0.94;
    setV({ z, x: (r.width - dw * z) / 2, y: (r.height - dh * z) / 2 });
    return true;
  }, [dw, dh]);
  useEffect(() => { touched.current = false; setState("loading"); fitView(); }, [fitView, src.id]);
  useEffect(() => { if (fitSignal) { touched.current = false; fitView(); } }, [fitSignal, fitView]);
  // fitted again whenever the pane changes size (the window, the table shown or hidden), until the person moves the view
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => { if (!touched.current) fitView(); });
    ro.observe(el);
    return () => ro.disconnect();
  }, [fitView]);
  useEffect(() => {
    const p = focus && pts.find((x) => x.id === focus.id);
    const r = box.current?.getBoundingClientRect();
    if (!p || !r) return;
    touched.current = true;
    setV((o) => ({ ...o, x: r.width / 2 - (p.col / k) * o.z, y: r.height / 2 - (p.row / k) * o.z }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      touched.current = true;
      setV((o) => {
        const z = Math.min(Math.max(o.z * (e.deltaY < 0 ? 1.2 : 1 / 1.2), 0.02), 64);
        return { z, x: mx - ((mx - o.x) / o.z) * z, y: my - ((my - o.y) / o.z) * z };
      });
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, []);
  const toImage = (e: { clientX: number; clientY: number }) => {
    const r = box.current!.getBoundingClientRect();
    const col = ((e.clientX - r.left - v.x) / v.z) * k, row = ((e.clientY - r.top - v.y) / v.z) * k;
    return [Math.min(Math.max(col, 0), src.width), Math.min(Math.max(row, 0), src.height)] as const;
  };
  const down = (e: RPointerEvent, point?: string) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, vx: v.x, vy: v.y, moved: false, point };
    if (point) onSelect(point);
  };
  const moveP = (e: RPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > 3) d.moved = true;
    if (!d.moved) return;
    if (d.point) { const [col, row] = toImage(e); setLive({ id: d.point, col, row }); }
    else { touched.current = true; setV((o) => ({ ...o, x: d.vx + e.clientX - d.x, y: d.vy + e.clientY - d.y })); }
  };
  const up = (e: RPointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.point) { if (d.moved && live) onMove(live.id, live.col, live.row); setLive(null); return; }
    if (!d.moved && adding) { const [col, row] = toImage(e); onPlace(col, row); }
  };
  const at = (col: number, row: number) => ({ left: v.x + (col / k) * v.z, top: v.y + (row / k) * v.z });
  return (
    <div className={`gr-pane gr-image${adding ? "" : " is-panning"}`} ref={box} onPointerDown={(e) => down(e)} onPointerMove={moveP} onPointerUp={up}
      aria-label="The image: click to place a point, drag to pan, scroll to zoom">
      <span className="gr-pane-label">Image</span>
      <img src={georefImageUrl(src, MOST)} alt={src.name} draggable={false} width={dw} height={dh}
        style={{ width: dw, height: dh, transform: `translate(${v.x}px, ${v.y}px) scale(${v.z})` }}
        onLoad={() => { setState("ready"); if (!touched.current) fitView(); }}
        onError={() => {
          void fetch(georefImageUrl(src, MOST), { credentials: "same-origin" }).then(async (r) => {
            const b = await r.json().catch(() => null) as { detail?: string } | null;
            setState(b?.detail ?? `The image could not be shown (the server answered ${r.status}).`);
          }, () => setState("The image could not be loaded: check your connection."));
        }} />
      {state !== "ready" && <div className="gr-img-state" role="status">{state === "loading" ? "Loading the image…" : state}</div>}
      {pts.map((p, i) => {
        const pos = live?.id === p.id ? at(live.col, live.row) : at(p.col, p.row);
        return <button type="button" key={p.id} className={`gr-mark${sel === p.id ? " is-sel" : ""}${p.on ? "" : " is-off"}`} style={pos}
          title={`Point ${i + 1}${err.get(p.id) ? `: ${err.get(p.id)!.error_m} m off` : ""}`} onPointerDown={(e) => down(e, p.id)}>{i + 1}</button>;
      })}
      {pending?.col !== undefined && <span className="gr-mark is-pending" style={at(pending.col, pending.row)}>+</span>}
      <div className="gr-zoom">
        <IconButton label="Zoom in" onClick={() => setV((o) => ({ ...o, z: o.z * 1.4 }))} onPointerDown={(e) => e.stopPropagation()}><Plus size={14} /></IconButton>
        <IconButton label="Whole image" onClick={fitView} onPointerDown={(e) => e.stopPropagation()}><RotateCcw size={14} /></IconButton>
      </div>
    </div>
  );
}

/** The map: click to place a point, drag a marker to move it; the placed image is drawn over it as a preview. */
function MapPane({ view, pts, pending, guess, sel, preview, opacity, adding, fitSignal, focus, onPlace, onMove, onSelect }: {
  view: { center: [number, number]; zoom: number } | null; pts: GeorefPoint[]; pending: Pending; guess: [number, number] | null; sel: string | null;
  preview: GeorefFit["preview"] | null; opacity: number; adding: boolean; fitSignal: number; focus: { id: string; n: number } | null;
  onPlace: (lng: number, lat: number) => void; onMove: (id: string, lng: number, lat: number) => void; onSelect: (id: string) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibre | null>(null);
  const marks = useRef(new Map<string, Marker>());
  const extra = useRef<Marker[]>([]);
  const [basemap, setBasemap] = useState("satellite");
  const [ready, setReady] = useState(0);
  const cb = useRef({ onPlace, onMove, onSelect, adding });
  cb.current = { onPlace, onMove, onSelect, adding };

  useEffect(() => {
    const m = new maplibregl.Map({ container: el.current!, style: basemapStyle("satellite"), center: view?.center ?? [8.68, 9.08], zoom: view?.zoom ?? 5.5,
      attributionControl: { compact: true } });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.on("click", (e) => { if (cb.current.adding) cb.current.onPlace(e.lngLat.lng, e.lngLat.lat); });
    m.on("style.load", () => setReady((n) => n + 1));
    m.getCanvas().style.cursor = "crosshair";
    if (pts.length) {                                           // carrying on: start where the points are
      const b = new maplibregl.LngLatBounds();
      pts.forEach((p) => b.extend([p.lng, p.lat]));
      m.fitBounds(b, { padding: 70, maxZoom: 17, duration: 0 });
    }
    map.current = m;
    const all = marks.current;
    return () => { all.forEach((x) => x.remove()); all.clear(); m.remove(); map.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { map.current?.setStyle(basemapStyle(basemap)); }, [basemap]);
  useEffect(() => { const c = map.current?.getCanvas(); if (c) c.style.cursor = adding ? "crosshair" : ""; }, [adding]);
  useEffect(() => {                                              // Fit to display: the map at the points (or the preview)
    const m = map.current;
    if (!m || !fitSignal) return;
    const b = new maplibregl.LngLatBounds();
    pts.forEach((p) => b.extend([p.lng, p.lat]));
    preview?.coordinates.forEach((c) => b.extend(c));
    if (!b.isEmpty()) m.fitBounds(b, { padding: 60, maxZoom: 17 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitSignal]);
  useEffect(() => {
    const p = focus && pts.find((x) => x.id === focus.id);
    if (p && map.current) map.current.easeTo({ center: [p.lng, p.lat], zoom: Math.max(map.current.getZoom(), 15) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);

  // markers follow the points
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const seen = new Set<string>();
    pts.forEach((p, i) => {
      seen.add(p.id);
      let mk = marks.current.get(p.id);
      if (!mk) {
        const node = document.createElement("button");
        node.type = "button";
        mk = new maplibregl.Marker({ element: node, draggable: true }).setLngLat([p.lng, p.lat]).addTo(m);
        node.addEventListener("click", (e) => { e.stopPropagation(); cb.current.onSelect(p.id); });
        mk.on("dragend", () => { const ll = mk!.getLngLat(); cb.current.onMove(p.id, ll.lng, ll.lat); });
        marks.current.set(p.id, mk);
      }
      mk.setLngLat([p.lng, p.lat]);
      const node = mk.getElement();
      node.className = `gr-mark${sel === p.id ? " is-sel" : ""}${p.on ? "" : " is-off"}`;
      node.textContent = String(i + 1);
      node.title = `Point ${i + 1}`;
    });
    for (const [id, mk] of marks.current) if (!seen.has(id)) { mk.remove(); marks.current.delete(id); }
  }, [pts, sel]);
  useEffect(() => {
    const m = map.current;
    extra.current.forEach((x) => x.remove());
    extra.current = [];
    if (!m) return;
    const mark = (ll: [number, number], cls: string, text: string) => {
      const node = document.createElement("span");
      node.className = cls; node.textContent = text;
      extra.current.push(new maplibregl.Marker({ element: node }).setLngLat(ll).addTo(m));
    };
    if (pending?.lng !== undefined) mark([pending.lng, pending.lat], "gr-mark is-pending", "+");
    if (guess) { mark(guess, "gr-mark is-guess", "?"); if (!m.getBounds().contains(guess)) m.easeTo({ center: guess }); }
  }, [pending, guess]);
  // the selected point comes into view
  useEffect(() => {
    const p = pts.find((x) => x.id === sel);
    const m = map.current;
    if (p && m && !m.getBounds().contains([p.lng, p.lat])) m.easeTo({ center: [p.lng, p.lat] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel]);
  // the preview image
  useEffect(() => {
    const m = map.current;
    if (!m || !m.isStyleLoaded()) return;
    if (m.getLayer("gr-preview")) m.removeLayer("gr-preview");
    if (m.getSource("gr-preview")) m.removeSource("gr-preview");
    if (!preview) return;
    const c = preview.coordinates as [[number, number], [number, number], [number, number], [number, number]];
    m.addSource("gr-preview", { type: "image", url: preview.url, coordinates: c });
    m.addLayer({ id: "gr-preview", type: "raster", source: "gr-preview", paint: { "raster-opacity": opacity, "raster-fade-duration": 0 } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preview, ready]);
  useEffect(() => { const m = map.current; if (m?.getLayer("gr-preview")) m.setPaintProperty("gr-preview", "raster-opacity", opacity); }, [opacity]);
  return (
    <div className="gr-pane gr-map">
      <span className="gr-pane-label">Map</span>
      <div ref={el} className="gr-map-el" aria-label="The map: click the same place as on the image" />
      <select className="gr-basemap" value={basemap} onChange={(e) => setBasemap(e.target.value)} aria-label="Basemap">
        {BASEMAPS.filter((b) => b.id !== "none").map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
      </select>
    </div>
  );
}

/** Target coordinates: a point's map position typed in, as degrees, minutes and seconds, as decimal degrees, or as an
 *  easting and northing in the SRS (or any coordinate system). */
function TargetDialog({ srs, start, editing, onCancel, onOk }: {
  srs: CrsDef; start: [number, number] | null; editing: boolean; onCancel: () => void; onOk: (lng: number, lat: number) => void;
}) {
  const [mode, setMode] = useState<"dms" | "dd" | "en">(srs.geographic ? "dms" : "en");
  const [lastEn, setLastEn] = useStored<number>("georef-en-epsg", 26391);     // the last system eastings were typed in
  const [enEpsg, setEnEpsgRaw] = useState<number | null>(srs.geographic ? lastEn : srs.epsg);
  const setEnEpsg = (v: number | null) => { setEnEpsgRaw(v); if (v) setLastEn(v); };
  const [enName, setEnName] = useState("");
  useEffect(() => { if (enEpsg) void loadCrs(enEpsg).then((d) => setEnName(d.name), () => setEnName("")); }, [enEpsg]);
  const lng0 = start?.[0], lat0 = start?.[1];
  const d0 = { lng: lng0 != null ? toDms(lng0, "lng") : null, lat: lat0 != null ? toDms(lat0, "lat") : null };
  const [dms, setDms] = useState({
    lngD: d0.lng ? String(d0.lng.deg) : "", lngM: d0.lng ? String(d0.lng.min) : "", lngS: d0.lng ? String(d0.lng.sec) : "", lngH: (d0.lng?.hemi ?? "E") as "E" | "W",
    latD: d0.lat ? String(d0.lat.deg) : "", latM: d0.lat ? String(d0.lat.min) : "", latS: d0.lat ? String(d0.lat.sec) : "", latH: (d0.lat?.hemi ?? "N") as "N" | "S",
  });
  const [dd, setDd] = useState({ lng: lng0 != null ? lng0.toFixed(7) : "", lat: lat0 != null ? lat0.toFixed(7) : "" });
  const [en, setEn] = useState({ x: "", y: "" });
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {                                              // the starting position in the chosen system
    if (mode !== "en" || !enEpsg || lng0 == null || lat0 == null) return;
    void loadCrs(enEpsg).then(() => { const [x, y] = fromLngLat(enEpsg, lng0, lat0); setEn({ x: x.toFixed(3), y: y.toFixed(3) }); }, () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, enEpsg]);
  async function ok() {
    setError(null);
    try {
      let lng: number, lat: number;
      if (mode === "dms") {
        const n = (v: string, what: string) => { if (v.trim() === "") return 0; const x = Number(v); if (!Number.isFinite(x)) throw new Error(`${what} must be a number.`); return x; };
        if (dms.lngD.trim() === "" || dms.latD.trim() === "") throw new Error("Type the degrees of both the longitude and the latitude.");
        lng = fromDms(n(dms.lngD, "Longitude degrees"), n(dms.lngM, "Longitude minutes"), n(dms.lngS, "Longitude seconds"), dms.lngH);
        lat = fromDms(n(dms.latD, "Latitude degrees"), n(dms.latM, "Latitude minutes"), n(dms.latS, "Latitude seconds"), dms.latH);
      } else if (mode === "dd") {
        lng = Number(dd.lng); lat = Number(dd.lat);
        if (!Number.isFinite(lng) || !Number.isFinite(lat) || dd.lng.trim() === "" || dd.lat.trim() === "") throw new Error("Type the longitude and latitude as numbers.");
      } else {
        if (!enEpsg) throw new Error("Choose the coordinate system of the easting and northing.");
        const x = Number(en.x), y = Number(en.y);
        if (!Number.isFinite(x) || !Number.isFinite(y) || en.x.trim() === "" || en.y.trim() === "") throw new Error("Type the easting (X) and northing (Y) as numbers.");
        const d = await loadCrs(enEpsg);
        [lng, lat] = d.geographic ? [x, y] : toLngLat(enEpsg, x, y);
      }
      if (!(Math.abs(lng) <= 180 && Math.abs(lat) <= 90)) throw new Error("That is not a place on Earth: check the numbers and the coordinate system.");
      onOk(lng, lat);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  const box = (v: string, set: (t: string) => void, label: string, width = 64) =>
    <input className="gr-num" style={{ width }} inputMode="decimal" value={v} aria-label={label} onChange={(e) => set(e.target.value)} />;
  return (
    <Dialog title={editing ? "Target coordinates: change the selected point" : "Target coordinates"} onClose={onCancel}>
      <form className="ws-form gr-target" onSubmit={(e) => { e.preventDefault(); void ok(); }}>
        <div className="gr-modes" role="radiogroup" aria-label="Type them as">
          {([["dms", "Degrees, minutes, seconds"], ["dd", "Decimal degrees"], ["en", "Easting and northing"]] as const).map(([k, t]) =>
            <label key={k} className={`gr-mode${mode === k ? " is-on" : ""}`}><input type="radio" name="mode" checked={mode === k} onChange={() => setMode(k)} />{t}</label>)}
        </div>
        {mode === "dms" && <div className="gr-dms">
          <span>Longitude</span>
          {box(dms.lngD, (t) => setDms({ ...dms, lngD: t }), "Longitude degrees")}<i>°</i>
          {box(dms.lngM, (t) => setDms({ ...dms, lngM: t }), "Longitude minutes")}<i>′</i>
          {box(dms.lngS, (t) => setDms({ ...dms, lngS: t }), "Longitude seconds", 88)}<i>″</i>
          <select value={dms.lngH} onChange={(e) => setDms({ ...dms, lngH: e.target.value as "E" | "W" })} aria-label="East or west"><option>E</option><option>W</option></select>
          <span>Latitude</span>
          {box(dms.latD, (t) => setDms({ ...dms, latD: t }), "Latitude degrees")}<i>°</i>
          {box(dms.latM, (t) => setDms({ ...dms, latM: t }), "Latitude minutes")}<i>′</i>
          {box(dms.latS, (t) => setDms({ ...dms, latS: t }), "Latitude seconds", 88)}<i>″</i>
          <select value={dms.latH} onChange={(e) => setDms({ ...dms, latH: e.target.value as "N" | "S" })} aria-label="North or south"><option>N</option><option>S</option></select>
        </div>}
        {mode === "dd" && <div className="gr-xy">
          <label className="ws-f">Longitude (X){box(dd.lng, (t) => setDd({ ...dd, lng: t }), "Longitude", 180)}</label>
          <label className="ws-f">Latitude (Y){box(dd.lat, (t) => setDd({ ...dd, lat: t }), "Latitude", 180)}</label>
        </div>}
        {mode === "en" && <>
          <div className="gr-xy">
            <label className="ws-f">Easting (X){box(en.x, (t) => setEn({ ...en, x: t }), "Easting", 180)}</label>
            <label className="ws-f">Northing (Y){box(en.y, (t) => setEn({ ...en, y: t }), "Northing", 180)}</label>
          </div>
          <p className="gr-chosen">In <strong>{enEpsg ? `EPSG:${enEpsg}${enName ? ` · ${enName}` : ""}` : "no coordinate system yet"}</strong></p>
          <CrsPicker value={enEpsg} onChange={setEnEpsg} label="Change the coordinate system" />
        </>}
        {start && !editing && <p className="ws-muted">Filled in with the position suggested by the points so far: change it to the true one.</p>}
        {error && <p className="ws-error" role="alert">{error}</p>}
        <div className="ws-row"><Button type="submit" variant="primary">OK</Button><Button variant="ghost" onClick={onCancel}>Cancel</Button></div>
      </form>
    </Dialog>
  );
}
