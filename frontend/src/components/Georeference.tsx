import maplibregl, { type Map as MapLibre, type Marker } from "maplibre-gl";
import { Crosshair, FileImage, MapPin, Plus, RotateCcw, Trash2, Upload, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { ApiError, api, georefImageUrl } from "../lib/api";
import { suggest } from "../lib/georef";
import { BASEMAPS, basemapStyle } from "../lib/mapstyle";
import type { GeorefFit, GeorefMethod, GeorefPoint, GeorefSource, Layer } from "../lib/types";
import { Button, ErrorText, IconButton, useToast } from "./ui";

type Pending = { col: number; row: number; lng?: undefined } | { lng: number; lat: number; col?: undefined } | null;
const MOST = 2048;
const shrink = (g: GeorefSource) => Math.max(1, Math.max(g.width, g.height) / MOST);
const newId = () => Math.random().toString(36).slice(2, 10);

/** Georeferencing: place an image without coordinates (a scanned map, a plan, a photo), or a raster in the wrong place,
 *  by matching points on the image to the same places on the map. */
export default function Georeference({ projectId, layers, canEdit, view, file, onClose, onDone }: {
  projectId: string; layers: Layer[]; canEdit: boolean; view: { center: [number, number]; zoom: number } | null; file?: File | null;
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

  useEffect(() => { void api.georefList(projectId).then(setEarlier, () => setEarlier([])); }, [projectId]);
  const startedWith = useRef<File | null>(null);
  useEffect(() => {                                              // sent here from Add data with a picture that has no coordinates
    if (file && startedWith.current !== file && canEdit) { startedWith.current = file; void start(file); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || (e.target as HTMLElement)?.closest?.("input, textarea, select")) return;
      if (pending) setPending(null); else onClose();
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose, pending]);

  function open(g: GeorefSource) {
    setSrc(g); setPts(g.points.map((p) => ({ ...p, on: p.on !== false }))); setMethod(g.method); setName(g.layer_id ? `${g.name} (re-placed)` : `${g.name} (georeferenced)`);
    setPending(null); setSel(null); setFit(null); setFitError(null); setError(null);
  }
  async function start(file?: File, layerId?: string) {
    setBusy(true); setError(null);
    try { open(file ? await api.georefStart(projectId, file) : await api.georefFromLayer(projectId, layerId!)); }
    catch (e) { setError(e instanceof ApiError ? e : new Error("Could not open the image.")); }
    finally { setBusy(false); }
  }

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
        setFit(await api.georefFit(src.id, { points: pts, method, epsg: epsg ? Number(epsg) : null, preview }));
        setFitError(null);
      } catch (e) { setFit(null); setFitError(e); }
    }, 450);
    return () => window.clearTimeout(t);
  }, [src, pts, method, preview, epsg, canEdit, onCount, need]);

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

  async function warp() {
    if (!src) return;
    setBusy(true); setError(null);
    try {
      const r = await api.georefWarp(src.id, { points: pts, method, epsg: epsg ? Number(epsg) : null, name: name.trim() || undefined, resampling });
      toast(`${r.layer.name} added to the map (RMS error ${r.fit.exact ? "0, the spline passes through every point" : `${r.fit.rmse_m} m`}).`, "success");
      onDone(r.layer);
    } catch (e) { setError(e instanceof ApiError ? e : new Error("Could not save the georeferenced image.")); }
    finally { setBusy(false); }
  }
  async function discard() {
    if (!src || !window.confirm(`Discard the georeferencing of ${src.name} and its ${pts.length} points? Layers already saved from it stay.`)) return;
    try { await api.georefDiscard(src.id); setEarlier((x) => (x ?? []).filter((g) => g.id !== src.id)); setSrc(null); } catch (e) { setError(e); }
  }

  const step = !src ? "" : pending?.col !== undefined
    ? guess ? "Now click the same place on the map, or use the suggested spot." : "Now click the same place on the map."
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
        {src && <span className="gr-step" role="status"><Crosshair size={14} aria-hidden="true" />{step}</span>}
        <IconButton label="Close georeferencing" onClick={onClose}><X size={18} /></IconButton>
      </div>
      {!src ? (
        <StartScreen layers={layers} earlier={earlier} busy={busy} canEdit={canEdit} error={error} onFile={(f) => void start(f)}
          onLayer={(id) => void start(undefined, id)} onOpen={open} />
      ) : (
        <div className="gr-body">
          <ImagePane src={src} pts={pts} pending={pending} sel={sel} err={err} onPlace={placeImage} onMove={(id, col, row) => move(id, { col, row })} onSelect={setSel} />
          <MapPane view={view} pts={pts} pending={pending} guess={guess} sel={sel} preview={preview && fit?.preview ? fit.preview : null} opacity={opacity}
            onPlace={placeMap} onMove={(id, lng, lat) => move(id, { lng, lat })} onSelect={setSel} />
          <aside className="lo-side ws-scroll gr-side" aria-label="Control points and result">
            {guess && <div className="gr-guess"><span>Suggested map position from the points so far.</span>
              <Button small variant="primary" onClick={() => placeMap(guess[0], guess[1])}><MapPin size={14} aria-hidden="true" />Use it</Button></div>}
            <label className="ws-f">Transformation
              <select value={method} onChange={(e) => setMethod(e.target.value as GeorefMethod)}>
                {methods.map((m) => <option key={m.id} value={m.id}>{m.label}{onCount < m.min_points ? ` (needs ${m.min_points} points)` : ""}</option>)}
              </select>
              <span className="ws-help">{method === "tps" ? "Bends the image to pass through every point: for old or warped maps. Check each point well, as there is no residual to catch a wrong one."
                : method === "similarity" ? "Keeps the image's shape: for photos or plans that only need moving, turning and resizing."
                : method === "affine" ? "Also allows skew and different scales across and down: right for most scans."
                : "Bends the image smoothly: for distorted scans, with points spread over the whole image."}</span>
            </label>
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
              <thead><tr><th>#</th><th>Image</th><th>Map</th><th>Error</th><th aria-label="Use"></th><th aria-label="Remove"></th></tr></thead>
              <tbody>
                {pts.map((p, i) => {
                  const e = err.get(p.id);
                  const worst = suspect.has(p.id);
                  return (
                    <tr key={p.id} className={`${sel === p.id ? "is-sel" : ""}${p.on ? "" : " is-off"}`} onClick={() => setSel(p.id)}>
                      <td>{i + 1}</td><td>{Math.round(p.col)}, {Math.round(p.row)}</td><td>{p.lat.toFixed(5)}, {p.lng.toFixed(5)}</td>
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
            {pending && <Button small variant="ghost" onClick={() => setPending(null)}><RotateCcw size={14} aria-hidden="true" />Cancel this point (Esc)</Button>}
            <fieldset className="lo-set"><legend>Preview</legend>
              <label className="lo-check"><input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} />Show the placed image on the map</label>
              <label className="ws-f">Transparency<input type="range" min={0.1} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} /></label>
            </fieldset>
            <fieldset className="lo-set"><legend>Result</legend>
              <label className="ws-f">Layer name<input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} /></label>
              <label className="ws-f">Coordinate system (EPSG)<input value={epsg} inputMode="numeric" placeholder={fit ? `${fit.crs} (UTM, chosen for you)` : "UTM, chosen for you"}
                onChange={(e) => setEpsg(e.target.value.replace(/\D/g, "").slice(0, 6))} /></label>
              <label className="ws-f">Resampling<select value={resampling} onChange={(e) => setResampling(e.target.value as "bilinear" | "nearest")}>
                <option value="bilinear">Smooth (scans, photos)</option><option value="nearest">Exact values (classified or data rasters)</option></select></label>
              <ErrorText error={error} />
              <Button variant="primary" busy={busy} disabled={!canEdit || onCount < need || !fit} onClick={() => void warp()}>
                <Plus size={14} aria-hidden="true" />Add to the map</Button>
              {canEdit && <Button small variant="ghost" onClick={() => void discard()}><Trash2 size={14} aria-hidden="true" />Discard this work</Button>}
            </fieldset>
          </aside>
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
function ImagePane({ src, pts, pending, sel, err, onPlace, onMove, onSelect }: {
  src: GeorefSource; pts: GeorefPoint[]; pending: Pending; sel: string | null; err: Map<string, { error_m: number }>;
  onPlace: (col: number, row: number) => void; onMove: (id: string, col: number, row: number) => void; onSelect: (id: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const k = shrink(src);
  const dw = Math.max(1, Math.round(src.width / k)), dh = Math.max(1, Math.round(src.height / k));
  const [v, setV] = useState({ z: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean; point?: string } | null>(null);
  const [live, setLive] = useState<{ id: string; col: number; row: number } | null>(null);
  const fitView = useCallback(() => {
    const r = box.current?.getBoundingClientRect();
    if (!r) return;
    const z = Math.min(r.width / dw, r.height / dh) * 0.94;
    setV({ z, x: (r.width - dw * z) / 2, y: (r.height - dh * z) / 2 });
  }, [dw, dh]);
  useEffect(() => { fitView(); }, [fitView, src.id]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
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
    else setV((o) => ({ ...o, x: d.vx + e.clientX - d.x, y: d.vy + e.clientY - d.y }));
  };
  const up = (e: RPointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.point) { if (d.moved && live) onMove(live.id, live.col, live.row); setLive(null); return; }
    if (!d.moved) { const [col, row] = toImage(e); onPlace(col, row); }
  };
  const at = (col: number, row: number) => ({ left: v.x + (col / k) * v.z, top: v.y + (row / k) * v.z });
  return (
    <div className="gr-pane gr-image" ref={box} onPointerDown={(e) => down(e)} onPointerMove={moveP} onPointerUp={up}
      aria-label="The image: click to place a point, drag to pan, scroll to zoom">
      <span className="gr-pane-label">Image</span>
      <img src={georefImageUrl(src, MOST)} alt={src.name} draggable={false} width={dw} height={dh}
        style={{ transform: `translate(${v.x}px, ${v.y}px) scale(${v.z})` }} />
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
function MapPane({ view, pts, pending, guess, sel, preview, opacity, onPlace, onMove, onSelect }: {
  view: { center: [number, number]; zoom: number } | null; pts: GeorefPoint[]; pending: Pending; guess: [number, number] | null; sel: string | null;
  preview: GeorefFit["preview"] | null; opacity: number;
  onPlace: (lng: number, lat: number) => void; onMove: (id: string, lng: number, lat: number) => void; onSelect: (id: string) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibre | null>(null);
  const marks = useRef(new Map<string, Marker>());
  const extra = useRef<Marker[]>([]);
  const [basemap, setBasemap] = useState("satellite");
  const [ready, setReady] = useState(0);
  const cb = useRef({ onPlace, onMove, onSelect });
  cb.current = { onPlace, onMove, onSelect };

  useEffect(() => {
    const m = new maplibregl.Map({ container: el.current!, style: basemapStyle("satellite"), center: view?.center ?? [8.68, 9.08], zoom: view?.zoom ?? 5.5,
      attributionControl: { compact: true } });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.on("click", (e) => cb.current.onPlace(e.lngLat.lng, e.lngLat.lat));
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
