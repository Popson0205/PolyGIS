import type { Map as MapLibre } from "maplibre-gl";
import { Download, FileDown, Image as ImageIcon, LocateFixed, Minus, Plus, Printer, Save, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, type SavedLayout } from "../lib/api";
import { fromLngLat } from "../lib/coords";
import {
  DEFAULTS, PAGES, SCALES, composeSheet, frameProjection, mapPixels, pageSize, roundScale, sheetFrames, type LayoutContext, type LayoutSettings, type PageId,
} from "../lib/layout";
import { canvasMeasure, drawCanvas, drawPdf } from "../lib/layoutRender";
import { printableStyle, renderMap } from "../lib/printMap";
import type { CrsDef, Layer, Me, Project } from "../lib/types";
import { Button, ErrorText, IconButton, useToast } from "./ui";

interface Picture { canvas: HTMLCanvasElement; center: [number, number]; scale: number; frameW: number; frameH: number }

/** The scale that shows what the live map shows now across the layout's map frame, rounded up to a printed scale. */
export function fittingScale(map: MapLibre, s: LayoutSettings) {
  const b = map.getBounds();
  const lat = map.getCenter().lat;
  const groundW = ((b.getEast() - b.getWest()) * Math.PI / 180) * 6378137 * Math.cos((lat * Math.PI) / 180);
  const frame = sheetFrames({ ...s, scale: 1, center: [0, 0] }).frame;
  const exact = (groundW / frame.w) * 1000;
  return SCALES.find((x) => x >= exact * 0.98) ?? roundScale(exact);
}

/** Print layout view: the sheet as it will print, beside its settings. Drag the map to move it, scroll to change the
 *  scale; export a PDF (vector text and lines, the map as a picture) or a PNG; save layouts with the project. */
export default function LayoutView({ project, layers, crs, me, map, canSave, onClose }: {
  project: Project; layers: Layer[]; crs?: CrsDef; me: Me; map: MapLibre; canSave: boolean; onClose: () => void;
}) {
  const toast = useToast();
  const [s, setS] = useState<LayoutSettings>(() => {
    const c = map.getCenter();
    const base = { ...DEFAULTS, title: project.name, center: [c.lng, c.lat] as [number, number], scale: 1 };
    return { ...base, scale: fittingScale(map, base) };
  });
  const set = (p: Partial<LayoutSettings>) => setS((x) => ({ ...x, ...p }));
  const [saved, setSaved] = useState<SavedLayout[]>([]);
  const [current, setCurrent] = useState<SavedLayout | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [error, setError] = useState<unknown>(null);
  const [pic, setPic] = useState<Picture | null>(null);
  const [drag, setDrag] = useState<{ x: number; y: number; dx: number; dy: number } | null>(null);
  const [box, setBox] = useState({ w: 800, h: 600 });
  const stage = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const measure = useMemo(canvasMeasure, []);

  useEffect(() => { api.layouts(project.id).then(setSaved, () => undefined); }, [project.id]);
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !(e.target as HTMLElement)?.closest?.("input, textarea, select")) onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const ctx: LayoutContext = useMemo(() => {
    const style = map.getStyle();
    const credits = Array.from(new Set(Object.values(style.sources).map((x) => ("attribution" in x ? String(x.attribution ?? "") : ""))
      .map((a) => a.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim()).filter(Boolean))).join("; ");
    return {
      layers, crs, measure, author: me.name, org: me.org_name ?? undefined, credits: credits || undefined,
      date: new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }),
      toCrs: crs && crs.epsg !== 4326 ? (lng: number, lat: number) => fromLngLat(crs.epsg, lng, lat) : undefined,
    };
  }, [layers, crs, me, map, measure]);

  const [pw, ph] = pageSize(s);
  const pad = 24;
  const pxPerMm = Math.max(0.5, Math.min((box.w - pad * 2) / pw, (box.h - pad * 2) / ph));
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const composed = useMemo(() => composeSheet(s, ctx), [s, ctx]);
  const frame = composed.sheet.frame;

  // the preview's map picture: drawn again a moment after the map's centre, scale or frame changes
  const frameKey = `${frame.w.toFixed(1)}x${frame.h.toFixed(1)}`;
  useEffect(() => {
    if (drag) return;
    const ac = new AbortController();
    const t = setTimeout(() => {
      const ratio = Math.max(0.35, Math.min(2, (pxPerMm * dpr * 25.4) / 96));
      const px = mapPixels(frame, 96 * ratio);
      renderMap({ style: printableStyle(map.getStyle()), center: s.center, scale: s.scale, cssW: px.cssW, cssH: px.cssH, ratio: px.ratio }, ac.signal)
        .then((r) => setPic({ canvas: r.canvas, center: s.center, scale: s.scale, frameW: frame.w, frameH: frame.h }), () => undefined);
    }, 350);
    return () => { clearTimeout(t); ac.abort(); };
  }, [s.center[0], s.center[1], s.scale, frameKey, pxPerMm, layers, drag === null]);   // eslint-disable-line react-hooks/exhaustive-deps

  // draw the sheet
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    c.width = Math.round(pw * pxPerMm * dpr); c.height = Math.round(ph * pxPerMm * dpr);
    c.style.width = `${pw * pxPerMm}px`; c.style.height = `${ph * pxPerMm}px`;
    const g = c.getContext("2d")!;
    let dx = 0, dy = 0, zoom = 1;
    if (pic) {
      // where the picture's centre is now, and how much bigger its scale makes it
      const proj = frameProjection(frame, s.center, s.scale);
      const [px, py] = proj.toPage(...pic.center);
      dx = px - (frame.x + frame.w / 2); dy = py - (frame.y + frame.h / 2);
      zoom = (pic.scale / s.scale) * (pic.frameW / frame.w);
    }
    if (drag) { dx += drag.dx; dy += drag.dy; }
    drawCanvas(g, composed.ops, pxPerMm * dpr, { image: pic?.canvas ?? null, dx, dy, zoom });
  }, [composed, pic, drag, pxPerMm, dpr, pw, ph, frame, s.center, s.scale]);

  // drag the map inside its frame; scroll to change the scale
  const inFrame = (e: React.PointerEvent | React.WheelEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    const x = (e.clientX - r.left) / pxPerMm, y = (e.clientY - r.top) / pxPerMm;
    return x >= frame.x && x <= frame.x + frame.w && y >= frame.y && y <= frame.y + frame.h;
  };
  const onDown = (e: React.PointerEvent) => {
    if (!inFrame(e)) return;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ x: e.clientX, y: e.clientY, dx: 0, dy: 0 });
  };
  const onMove = (e: React.PointerEvent) => { if (drag) setDrag({ ...drag, dx: (e.clientX - drag.x) / pxPerMm, dy: (e.clientY - drag.y) / pxPerMm }); };
  const onUp = () => {
    if (!drag) return;
    const proj = frameProjection(frame, s.center, s.scale);
    const c = proj.toLngLat(frame.x + frame.w / 2 - drag.dx, frame.y + frame.h / 2 - drag.dy);
    setDrag(null);
    if (Math.abs(drag.dx) + Math.abs(drag.dy) > 0.2) set({ center: [Number(c[0].toFixed(7)), Number(c[1].toFixed(7))] });
  };
  const step = useCallback((dir: 1 | -1) => setS((x) => {
    const i = SCALES.findIndex((v) => v >= x.scale - 1e-6);
    const next = dir > 0 ? SCALES[Math.min(SCALES.length - 1, (SCALES[i] === x.scale ? i : i - 1) + 1)] : SCALES[Math.max(0, (i < 0 ? SCALES.length : i) - 1)];
    return { ...x, scale: next ?? x.scale };
  }), []);
  const wheelAt = useRef(0);
  const onWheel = (e: React.WheelEvent) => {
    if (!inFrame(e) || Date.now() - wheelAt.current < 180) return;
    wheelAt.current = Date.now();
    step(e.deltaY > 0 ? 1 : -1);
  };

  // ---------- export ----------
  const fileName = (ext: string) => `${(s.title || project.name).replace(/[\\/:*?"<>|]+/g, " ").trim() || "map"} (1-${s.scale}).${ext}`;
  async function bigMap(dpi: number) {
    const px = mapPixels(frame, dpi);
    setBusy(`Drawing the map at ${px.dpi} dpi…`);
    const r = await renderMap({ style: printableStyle(map.getStyle()), center: s.center, scale: s.scale, cssW: px.cssW, cssH: px.cssH, ratio: px.ratio });
    if (!r.complete) toast("Some of the map was still loading after a minute; it is printed as far as it got.", "info");
    if (px.dpi < dpi) toast(`The map is printed at ${px.dpi} dpi: ${dpi} dpi on this page is more than a browser can draw.`, "info");
    return r;
  }
  async function exportPdf() {
    setError(null);
    try {
      const r = await bigMap(s.dpi);
      setBusy("Writing the PDF…");
      const { jsPDF } = await import("jspdf");
      const doc = new jsPDF({ unit: "mm", format: [pw, ph], orientation: pw > ph ? "landscape" : "portrait", compress: true });
      doc.setProperties({ title: s.title || project.name, subject: `Scale 1:${s.scale}`, creator: "GeoEstate PolyGIS", author: me.name });
      drawPdf(doc, composed.ops, r.jpeg());
      doc.save(fileName("pdf"));
      toast("PDF ready.", "success");
    } catch (e) { setError(e instanceof Error ? e : new Error("Could not make the PDF.")); }
    finally { setBusy(""); }
  }
  async function exportPng() {
    setError(null);
    try {
      // the whole page at the resolution chosen, within what a browser can hold in one picture
      let dpi = s.dpi;
      while (dpi > 72 && ((pw / 25.4) * dpi * (ph / 25.4) * dpi > 60e6 || Math.max(pw, ph) / 25.4 * dpi > 12000)) dpi = Math.floor(dpi * 0.9);
      const r = await bigMap(dpi);
      setBusy("Drawing the page…");
      const c = document.createElement("canvas");
      c.width = Math.round((pw / 25.4) * dpi); c.height = Math.round((ph / 25.4) * dpi);
      drawCanvas(c.getContext("2d")!, composed.ops, dpi / 25.4, { image: r.canvas });
      const blob = await new Promise<Blob | null>((ok) => c.toBlob(ok, "image/png"));
      if (!blob) throw new Error("The browser could not make a picture that large: choose a smaller page or a lower resolution.");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = fileName("png"); a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      toast(dpi < s.dpi ? `PNG ready, at ${dpi} dpi (the most a browser can draw for this page).` : "PNG ready.", "success");
    } catch (e) { setError(e instanceof Error ? e : new Error("Could not make the picture.")); }
    finally { setBusy(""); }
  }

  // ---------- saved layouts ----------
  async function save(asNew: boolean) {
    setError(null);
    try {
      if (current && !asNew) {
        const u = await api.updateLayout(current.id, { settings: s });
        setCurrent(u); setSaved((x) => [u, ...x.filter((y) => y.id !== u.id)]);
        toast(`${u.name} saved.`, "success");
      } else {
        const name = window.prompt("Name for this layout", current ? `${current.name} (copy)` : `${s.title || project.name}, ${s.page} ${s.orientation}`);
        if (!name?.trim()) return;
        const c = await api.createLayout(project.id, name.trim(), s);
        setCurrent(c); setSaved((x) => [c, ...x]);
        toast(`${c.name} saved with the project.`, "success");
      }
    } catch (e) { setError(e instanceof ApiError ? e : new Error("Could not save the layout.")); }
  }
  async function remove() {
    if (!current || !window.confirm(`Delete the layout ${current.name}? The project and its layers are not touched.`)) return;
    try { await api.deleteLayout(current.id); setSaved((x) => x.filter((y) => y.id !== current.id)); setCurrent(null); }
    catch (e) { setError(e); }
  }
  const open = (lid: string) => {
    const l = saved.find((x) => x.id === lid);
    if (!l) { setCurrent(null); return; }
    setCurrent(l); setS({ ...DEFAULTS, ...l.settings, grid: { ...DEFAULTS.grid, ...(l.settings.grid ?? {}) } } as LayoutSettings);
  };

  const scaleText = `1:${s.scale.toLocaleString("en-GB")}`;
  const px = mapPixels(frame, s.dpi);
  return (
    <div className="lo" role="dialog" aria-modal="true" aria-label="Print layout">
      <header className="lo-bar">
        <Printer size={18} aria-hidden="true" />
        <strong>Print layout</strong>
        <select className="lo-pick" value={current?.id ?? ""} onChange={(e) => open(e.target.value)} aria-label="Saved layouts">
          <option value="">{saved.length ? "New layout" : "New layout (none saved yet)"}</option>
          {saved.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
        {canSave && <>
          <Button small onClick={() => void save(false)}><Save size={14} aria-hidden="true" />{current ? "Save" : "Save layout"}</Button>
          {current && <Button small variant="ghost" onClick={() => void save(true)}>Save as new</Button>}
          {current && <IconButton label="Delete this layout" onClick={() => void remove()}><Trash2 size={16} /></IconButton>}
        </>}
        <span className="ge-spacer" />
        {busy && <span className="lo-busy" role="status">{busy}</span>}
        <Button small onClick={() => void exportPng()} disabled={!!busy}><ImageIcon size={14} aria-hidden="true" />PNG</Button>
        <Button small variant="primary" onClick={() => void exportPdf()} disabled={!!busy}><FileDown size={14} aria-hidden="true" />Export PDF</Button>
        <IconButton label="Close the layout" onClick={onClose}><X size={18} /></IconButton>
      </header>
      <div className="lo-body">
        <div className="lo-stage" ref={stage} onWheel={onWheel}>
          <canvas ref={canvas} className={`lo-page${drag ? " is-dragging" : ""}`} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
            aria-label={`Preview of the ${s.page} ${s.orientation} sheet at ${scaleText}. Drag the map to move it; scroll over it to change the scale.`} />
          <div className="lo-zoom">
            <IconButton label="Larger scale (closer)" onClick={() => step(-1)}><Plus size={16} /></IconButton>
            <span>{scaleText}</span>
            <IconButton label="Smaller scale (further out)" onClick={() => step(1)}><Minus size={16} /></IconButton>
          </div>
        </div>
        <aside className="lo-side ws-scroll" aria-label="Layout settings">
          <ErrorText error={error} />
          <fieldset className="lo-set"><legend>Page</legend>
            <div className="ws-row">
              <label className="ws-f">Size<select value={s.page} onChange={(e) => set({ page: e.target.value as PageId })}>
                {Object.keys(PAGES).map((p) => <option key={p}>{p}</option>)}</select></label>
              <label className="ws-f">Orientation<select value={s.orientation} onChange={(e) => set({ orientation: e.target.value as LayoutSettings["orientation"] })}>
                <option value="landscape">Landscape</option><option value="portrait">Portrait</option></select></label>
            </div>
            <label className="ws-f">Resolution<select value={s.dpi} onChange={(e) => set({ dpi: Number(e.target.value) })}>
              <option value={150}>150 dpi (draft)</option><option value={200}>200 dpi</option><option value={300}>300 dpi (print)</option></select>
              {px.dpi < s.dpi && <span className="ws-help">On a {s.page} sheet the map is drawn at {px.dpi} dpi, the most a browser can draw.</span>}</label>
          </fieldset>
          <fieldset className="lo-set"><legend>Map</legend>
            <label className="ws-f">Scale
              <span className="lo-scale">1 :
                <input type="number" min={100} step={100} value={s.scale} onChange={(e) => Number(e.target.value) >= 50 && set({ scale: Math.round(Number(e.target.value)) })} aria-label="Scale denominator" />
                <select value={SCALES.includes(s.scale) ? s.scale : ""} onChange={(e) => e.target.value && set({ scale: Number(e.target.value) })} aria-label="A standard scale">
                  <option value="">Standard…</option>{SCALES.map((v) => <option key={v} value={v}>1:{v.toLocaleString("en-GB")}</option>)}</select>
              </span></label>
            <Button small variant="ghost" onClick={() => { const c = map.getCenter(); set({ center: [c.lng, c.lat], scale: fittingScale(map, s) }); }}>
              <LocateFixed size={14} aria-hidden="true" />Use the map's current view</Button>
            <p className="ws-muted">Drag the map on the sheet to move it, and scroll over it to change the scale. Layers print as they are shown now.</p>
          </fieldset>
          <fieldset className="lo-set"><legend>Text</legend>
            <label className="ws-f">Title<input value={s.title} maxLength={120} onChange={(e) => set({ title: e.target.value })} /></label>
            <label className="ws-f">Subtitle<input value={s.subtitle} maxLength={160} onChange={(e) => set({ subtitle: e.target.value })} placeholder="e.g. Layout plan, sheet 1 of 2" /></label>
            <label className="ws-f">Notes<textarea rows={3} value={s.notes} maxLength={1200} onChange={(e) => set({ notes: e.target.value })} placeholder="Source, surveyor, purpose, disclaimers…" /></label>
          </fieldset>
          <fieldset className="lo-set"><legend>Map elements</legend>
            <label className="ws-f">Legend<select value={s.legend} onChange={(e) => set({ legend: e.target.value as LayoutSettings["legend"] })}>
              <option value="side">{s.orientation === "landscape" ? "In a column at the side" : "In a band below the map"}</option>
              <option value="inside">Inside the map</option><option value="none">None</option></select></label>
            <label className="lo-check"><input type="checkbox" checked={s.scalebar} onChange={(e) => set({ scalebar: e.target.checked })} />Scale bar</label>
            <label className="lo-check"><input type="checkbox" checked={s.north} onChange={(e) => set({ north: e.target.checked })} />North arrow</label>
            <label className="lo-check"><input type="checkbox" checked={s.grid.on} onChange={(e) => set({ grid: { ...s.grid, on: e.target.checked } })} />
              Grid in {crs ? crs.name : "WGS 84"}</label>
            {s.grid.on && <div className="ws-row lo-indent">
              <label className="ws-f">Interval ({crs && !crs.geographic ? "m" : "°"})
                <input type="number" min={0} step="any" value={s.grid.interval ?? ""} placeholder="Automatic"
                  onChange={(e) => set({ grid: { ...s.grid, interval: e.target.value === "" ? null : Number(e.target.value) } })} /></label>
              <label className="lo-check"><input type="checkbox" checked={s.grid.labels} onChange={(e) => set({ grid: { ...s.grid, labels: e.target.checked } })} />Coordinates</label>
            </div>}
            <label className="lo-check"><input type="checkbox" checked={s.footer} onChange={(e) => set({ footer: e.target.checked })} />Footer: coordinate system, date, author and credits</label>
          </fieldset>
          <p className="ws-muted"><Download size={12} aria-hidden="true" /> The PDF keeps text and lines sharp at any size; the map itself is a picture at the resolution above.</p>
        </aside>
      </div>
    </div>
  );
}
