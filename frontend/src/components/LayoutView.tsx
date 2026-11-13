import type { Map as MapLibre } from "maplibre-gl";
import {
  BringToFront, Braces, ChevronDown, ChevronUp, Compass, Copy, Eye, EyeOff, FileDown, Grid3x3, Hand, Image as ImageIcon, LayoutTemplate, LocateFixed,
  Lock, LockOpen, Map as MapIcon, Printer, Redo2, Ruler, Save, SendToBack, Square, Trash2, Type, Undo2, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, type SavedLayout } from "../lib/api";
import { fromLngLat } from "../lib/coords";
import { DEFAULTS, PAGES, SCALES, frameProjection, mapPixels, roundScale, type LayoutContext, type LayoutSettings, type PageId, type Rect, type XY } from "../lib/layout";
import {
  KIND_NAMES, TOKENS, composeDoc, defaultSize, docFrom, docSize, makeElement, mapFor, mapsOf, newId, resizeBox, snap, snapLines, templateDoc,
  type El, type ElKind, type Handle, type LayoutDoc, type MapEl, type Stroke,
} from "../lib/layoutDoc";
import { canvasMeasure, drawCanvas, drawPdf, type MapPictures } from "../lib/layoutRender";
import { printableStyle, renderMap } from "../lib/printMap";
import type { CrsDef, Layer, Me, Project } from "../lib/types";
import { Group, Tb } from "./Ribbon";
import { Button, ErrorText, IconButton, Menu, useToast } from "./ui";

interface Picture { canvas: HTMLCanvasElement; key: string; center: XY; scale: number; w: number; h: number }
type Drag =
  | { kind: "move"; ids: string[]; start: XY; boxes: Record<string, Rect>; before: LayoutDoc; moved: boolean }
  | { kind: "resize"; id: string; handle: Handle; start: XY; box: Rect; before: LayoutDoc }
  | { kind: "draw"; tool: ElKind; start: XY; now: XY; text?: string }
  | { kind: "pan"; id: string; start: XY; d: XY };
const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
function KindIcon({ kind }: { kind: ElKind }) {
  const p = { size: 15, "aria-hidden": true, className: "lv-ico" } as const;
  return kind === "map" ? <MapIcon {...p} /> : kind === "legend" ? <Rows size={15} className="lv-ico" /> : kind === "north" ? <Compass {...p} />
    : kind === "scalebar" ? <Ruler {...p} /> : kind === "text" ? <Type {...p} /> : <Square {...p} />;
}
function Rows(props: React.SVGProps<SVGSVGElement> & { size?: number }) {          // a legend: symbols beside lines of text
  const s = props.size ?? 24;
  return <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden="true" {...props}>
    <rect x="3" y="4" width="5" height="4" rx="1" /><rect x="3" y="10" width="5" height="4" rx="1" /><rect x="3" y="16" width="5" height="4" rx="1" />
    <path d="M11 6h10M11 12h10M11 18h7" /></svg>;
}
const ZOOMS = [25, 50, 75, 100, 150, 200, 300, 400];
const PX_PER_MM_100 = 96 / 25.4;                       // 100%: the page at its real size on a 96 dpi screen
const r1 = (v: number) => Math.round(v * 10) / 10;

/** The scale that shows what the live map shows now across a frame `frameW` mm wide, rounded up to a printed scale. */
export function scaleToFit(map: MapLibre, frameW: number) {
  const b = map.getBounds();
  const lat = map.getCenter().lat;
  const groundW = ((b.getEast() - b.getWest()) * Math.PI / 180) * 6378137 * Math.cos((lat * Math.PI) / 180);
  const exact = (groundW / frameW) * 1000;
  return SCALES.find((x) => x >= exact * 0.98) ?? roundScale(exact);
}
const firstLayout = (map: MapLibre, title: string, t: Partial<LayoutSettings> = {}): LayoutDoc => {
  const c = map.getCenter();
  const s = { ...DEFAULTS, ...t, title, center: [c.lng, c.lat] as XY, scale: 10000 };
  const d = templateDoc(s);
  const m = mapsOf(d)[0];
  return m ? { ...d, elements: d.elements.map((e) => (e.id === m.id ? { ...m, scale: scaleToFit(map, m.w) } : e)) } : d;
};

/** Layout view, as ArcGIS Pro has it: the page with its elements, which are inserted from the ribbon, moved and resized
 *  on the page, listed in drawing order in Contents and set in the Element pane. Double-click a map frame to move and
 *  scale the map inside it. Export the page as a PDF (vector text and lines, maps as pictures) or a PNG; save
 *  layouts with the project. */
export default function LayoutView({ project, layers, crs, me, map, canSave, onClose }: {
  project: Project; layers: Layer[]; crs?: CrsDef; me: Me; map: MapLibre; canSave: boolean; onClose: () => void;
}) {
  const toast = useToast();
  const [doc, setDocState] = useState<LayoutDoc>(() => firstLayout(map, project.name));
  const undo = useRef<LayoutDoc[]>([]);
  const redo = useRef<LayoutDoc[]>([]);
  const [, bump] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState<SavedLayout[]>([]);
  const [current, setCurrent] = useState<SavedLayout | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [tool, setTool] = useState<{ kind: ElKind; text?: string } | null>(null);
  const [active, setActive] = useState<string | null>(null);           // the map frame being navigated
  const [drag, setDrag] = useState<Drag | null>(null);
  const [guides, setGuides] = useState<{ x: number | null; y: number | null }>({ x: null, y: null });
  const [zoom, setZoom] = useState<"fit" | number>("fit");
  const [cursor, setCursor] = useState<XY | null>(null);
  const [pics, setPics] = useState<Record<string, Picture>>({});
  const [drawing, setDrawing] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [box, setBox] = useState({ w: 800, h: 600 });
  const stage = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const measure = useMemo(canvasMeasure, []);

  // ---------- the document, with undo ----------
  const setDoc = useCallback((next: LayoutDoc | ((d: LayoutDoc) => LayoutDoc), before?: LayoutDoc) => {
    setDocState((d) => {
      const n = typeof next === "function" ? next(d) : next;
      if (n === d) return d;
      undo.current.push(before ?? d); if (undo.current.length > 100) undo.current.shift();
      redo.current = [];
      return n;
    });
    setDirty(true); bump((x) => x + 1);
  }, []);
  const live = (next: (d: LayoutDoc) => LayoutDoc) => setDocState(next);                // while dragging: kept on release
  const back = () => { const p = undo.current.pop(); if (!p) return; redo.current.push(doc); setDocState(p); setDirty(true); bump((x) => x + 1); };
  const fwd = () => { const n = redo.current.pop(); if (!n) return; undo.current.push(doc); setDocState(n); setDirty(true); bump((x) => x + 1); };
  const change = (id: string, p: Partial<El>) => setDoc((d) => ({ ...d, elements: d.elements.map((e) => (e.id === id ? ({ ...e, ...p } as El) : e)) }));
  const byId = (id: string | null | undefined) => doc.elements.find((e) => e.id === id) ?? null;
  const one = sel.length === 1 ? byId(sel[0]) : null;

  useEffect(() => { api.layouts(project.id).then(setSaved, () => undefined); }, [project.id]);
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const ctx: LayoutContext = useMemo(() => {
    const style = map.getStyle();
    const credits = Array.from(new Set(Object.values(style.sources).map((x) => ("attribution" in x ? String(x.attribution ?? "") : ""))
      .map((a) => a.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim()).filter(Boolean))).join("; ");
    return {
      layers, crs, measure, author: me.name, org: me.org_name ?? undefined, credits: credits || undefined, project: project.name,
      date: new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }),
      toCrs: crs && crs.epsg !== 4326 ? (lng: number, lat: number) => fromLngLat(crs.epsg, lng, lat) : undefined,
    };
  }, [layers, crs, me, map, measure, project.name]);

  // ---------- the page on screen ----------
  const [pw, ph] = docSize(doc);
  const RULER = 18, PAD = 28;
  const fit = Math.max(0.4, Math.min((box.w - RULER - PAD * 2) / pw, (box.h - RULER - PAD * 2) / ph));
  const k = zoom === "fit" ? fit : (zoom / 100) * PX_PER_MM_100;                       // screen pixels per millimetre
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const [scroll, setScroll] = useState<XY>([0, 0]);
  const pageLeft = Math.max(PAD, (box.w - RULER - pw * k) / 2), pageTop = Math.max(PAD, (box.h - RULER - ph * k) / 2);
  const composed = useMemo(() => composeDoc(doc, ctx), [doc, ctx]);
  const maps = mapsOf(doc);

  // each map frame's picture, drawn again a moment after its centre, scale or size changes
  const layersKey = useMemo(() => layers.map((l) => `${l.id}:${l.version}:${l.visible}:${l.position}:${JSON.stringify(l.style).length}:${l.labels ? 1 : 0}`).join("|"), [layers]);
  const ratio = Math.max(0.35, Math.min(2, (k * dpr * 25.4) / 96));
  const keyOf = (m: MapEl) => `${m.center[0].toFixed(7)},${m.center[1].toFixed(7)},${m.scale},${m.w.toFixed(1)}x${m.h.toFixed(1)},${ratio.toFixed(2)},${layersKey}`;
  const wanted = maps.filter((m) => m.visible && pics[m.id]?.key !== keyOf(m)).map((m) => m.id).join(",");
  useEffect(() => {
    if (!wanted || drag) { setDrawing(false); return; }
    const ac = new AbortController();
    const t = setTimeout(async () => {
      setDrawing(true);
      for (const id of wanted.split(",")) {
        const m = mapsOf(doc).find((x) => x.id === id);
        if (!m || ac.signal.aborted) continue;
        const px = mapPixels(m, 96 * ratio);
        try {
          const r = await renderMap({ style: printableStyle(map.getStyle()), center: m.center, scale: m.scale, cssW: px.cssW, cssH: px.cssH, ratio: px.ratio, wait: 10000 }, ac.signal);     // a preview need not wait for every tile
          if (!ac.signal.aborted) setPics((p) => ({ ...p, [m.id]: { canvas: r.canvas, key: keyOf(m), center: m.center, scale: m.scale, w: m.w, h: m.h } }));
        } catch { /* stopped, or the map could not be drawn: the frame stays grey */ }
      }
      if (!ac.signal.aborted) setDrawing(false);
    }, 350);
    return () => { clearTimeout(t); ac.abort(); };
  }, [wanted, drag === null]);   // eslint-disable-line react-hooks/exhaustive-deps

  // draw the page
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    c.width = Math.round(pw * k * dpr); c.height = Math.round(ph * k * dpr);
    c.style.width = `${pw * k}px`; c.style.height = `${ph * k}px`;
    const g = c.getContext("2d")!;
    const pictures: MapPictures = {};
    for (const m of maps) {
      const pic = pics[m.id];
      if (!pic) continue;
      // where the picture's centre is now, and how much bigger its scale makes it
      const [px, py] = frameProjection(m, m.center, m.scale).toPage(...pic.center);
      let dx = px - (m.x + m.w / 2), dy = py - (m.y + m.h / 2);
      if (drag?.kind === "pan" && drag.id === m.id) { dx += drag.d[0]; dy += drag.d[1]; }
      pictures[m.id] = { image: pic.canvas, dx, dy, zoom: (pic.scale / m.scale) * (pic.w / m.w) };
    }
    drawCanvas(g, composed.ops, k * dpr, pictures);
  }, [composed, pics, drag, k, dpr, pw, ph]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---------- pointer on the page ----------
  const at = (e: { clientX: number; clientY: number }): XY => {
    const r = canvas.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / k, (e.clientY - r.top) / k];
  };
  const hit = (p: XY) => [...doc.elements].reverse().find((e) => e.visible && !e.locked && p[0] >= e.x && p[0] <= e.x + e.w && p[1] >= e.y && p[1] <= e.y + e.h) ?? null;
  const tol = 6 / k;
  const snapMove = (boxes: Rect[], dx: number, dy: number, skip: Set<string>, off: boolean): XY => {
    if (off || !boxes.length) { setGuides({ x: null, y: null }); return [dx, dy]; }
    const u = { x: Math.min(...boxes.map((b) => b.x)) + dx, y: Math.min(...boxes.map((b) => b.y)) + dy };
    const r = { x: Math.max(...boxes.map((b) => b.x + b.w)) + dx, y: Math.max(...boxes.map((b) => b.y + b.h)) + dy };
    const lines = snapLines(doc, skip);
    const sx = snap([u.x, (u.x + r.x) / 2, r.x], lines.xs, tol), sy = snap([u.y, (u.y + r.y) / 2, r.y], lines.ys, tol);
    setGuides({ x: sx.at, y: sy.at });
    return [dx + sx.d, dy + sy.d];
  };

  const onDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const p = at(e);
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    if (tool) { setDrag({ kind: "draw", tool: tool.kind, start: p, now: p, text: tool.text }); return; }
    const h = hit(p);
    if (active) {
      if (h?.id === active) { setDrag({ kind: "pan", id: active, start: p, d: [0, 0] }); return; }
      setActive(null);
    }
    if (!h) { setSel([]); return; }
    const ids = e.shiftKey ? (sel.includes(h.id) ? sel.filter((x) => x !== h.id) : [...sel, h.id]) : sel.includes(h.id) ? sel : [h.id];
    setSel(ids);
    const boxes: Record<string, Rect> = {};
    for (const x of doc.elements) if (ids.includes(x.id) && !x.locked) boxes[x.id] = { x: x.x, y: x.y, w: x.w, h: x.h };
    setDrag({ kind: "move", ids: Object.keys(boxes), start: p, boxes, before: doc, moved: false });
  };
  const onHandle = (e: React.PointerEvent, el: El, handle: Handle) => {
    e.stopPropagation();
    if (e.button !== 0 || el.locked) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ kind: "resize", id: el.id, handle, start: at(e), box: { x: el.x, y: el.y, w: el.w, h: el.h }, before: doc });
  };
  const onMove = (e: React.PointerEvent) => {
    const p = at(e);
    setCursor(p);
    if (!drag) return;
    const dx = p[0] - drag.start[0], dy = p[1] - drag.start[1];
    if (drag.kind === "draw") setDrag({ ...drag, now: p });
    else if (drag.kind === "pan") setDrag({ ...drag, d: [dx, dy] });
    else if (drag.kind === "move") {
      if (!drag.moved && Math.hypot(dx, dy) * k < 3) return;
      const [sx, sy] = snapMove(Object.values(drag.boxes), dx, dy, new Set(drag.ids), e.altKey);
      live((d) => ({ ...d, elements: d.elements.map((x) => (drag.boxes[x.id] ? ({ ...x, x: r1(drag.boxes[x.id].x + sx), y: r1(drag.boxes[x.id].y + sy) } as El) : x)) }));
      if (!drag.moved) setDrag({ ...drag, moved: true });
    } else if (drag.kind === "resize") {
      let b = resizeBox(drag.box, drag.handle, dx, dy, e.shiftKey);
      if (!e.altKey) {
        const lines = snapLines(doc, new Set([drag.id]));
        let gx: number | null = null, gy: number | null = null;
        if (drag.handle.includes("e")) { const s = snap([b.x + b.w], lines.xs, tol); b = { ...b, w: b.w + s.d }; gx = s.at; }
        if (drag.handle.includes("w")) { const s = snap([b.x], lines.xs, tol); b = { ...b, x: b.x + s.d, w: b.w - s.d }; gx = s.at; }
        if (drag.handle.includes("s")) { const s = snap([b.y + b.h], lines.ys, tol); b = { ...b, h: b.h + s.d }; gy = s.at; }
        if (drag.handle.includes("n")) { const s = snap([b.y], lines.ys, tol); b = { ...b, y: b.y + s.d, h: b.h - s.d }; gy = s.at; }
        setGuides({ x: gx, y: gy });
      }
      live((d) => ({ ...d, elements: d.elements.map((x) => (x.id === drag.id ? ({ ...x, x: r1(b.x), y: r1(b.y), w: r1(b.w), h: r1(b.h) } as El) : x)) }));
    }
  };
  const onUp = () => {
    if (!drag) return;
    setGuides({ x: null, y: null });
    if (drag.kind === "draw") {
      const [a, b] = [drag.start, drag.now];
      let r: Rect = { x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]), w: Math.abs(b[0] - a[0]), h: Math.abs(b[1] - a[1]) };
      if (r.w * k < 8 || r.h * k < 8) { const [w, h] = defaultSize(doc, drag.tool); r = { x: a[0] - w / 2, y: a[1] - h / 2, w, h }; }       // a click: the usual size
      r = { x: r1(Math.max(0, Math.min(pw - r.w, r.x))), y: r1(Math.max(0, Math.min(ph - r.h, r.y))), w: r1(Math.min(r.w, pw)), h: r1(Math.min(r.h, ph)) };
      const c = map.getCenter();
      const el = makeElement(doc, drag.tool, r, drag.tool === "map" ? { center: [c.lng, c.lat], scale: scaleToFit(map, r.w) } : undefined, drag.text);
      setDoc((d) => ({ ...d, elements: [...d.elements, el] }));
      setSel([el.id]); setTool(null);
    } else if (drag.kind === "pan") {
      const m = byId(drag.id) as MapEl | null;
      if (m && Math.hypot(...drag.d) > 0.2) {
        const c = frameProjection(m, m.center, m.scale).toLngLat(m.x + m.w / 2 - drag.d[0], m.y + m.h / 2 - drag.d[1]);
        change(m.id, { center: [Number(c[0].toFixed(7)), Number(c[1].toFixed(7))] });
      }
    } else if ((drag.kind === "move" && drag.moved) || drag.kind === "resize") {
      setDocState((d) => { undo.current.push(drag.before); redo.current = []; return d; });
      setDirty(true); bump((x) => x + 1);
    }
    setDrag(null);
  };
  const onDouble = (e: React.MouseEvent) => {
    const h = hit(at(e));
    if (h?.kind === "map") { setActive(h.id); setSel([h.id]); }
  };

  // the scale of a map frame, through the printed scales
  const stepScale = (id: string, dir: 1 | -1) => {
    const m = byId(id) as MapEl | null;
    if (!m) return;
    const i = SCALES.findIndex((v) => v >= m.scale - 1e-6);
    const next = dir > 0 ? SCALES[Math.min(SCALES.length - 1, (SCALES[i] === m.scale ? i : i - 1) + 1)] : SCALES[Math.max(0, (i < 0 ? SCALES.length : i) - 1)];
    if (next && next !== m.scale) change(id, { scale: next });
  };
  const wheelAt = useRef(0);
  const onWheel = (e: React.WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {                                // the page's zoom
      e.preventDefault();
      const pct = Math.round((k / PX_PER_MM_100) * 100);
      setZoom(e.deltaY < 0 ? ZOOMS.find((z) => z > pct + 1) ?? 400 : [...ZOOMS].reverse().find((z) => z < pct - 1) ?? 25);
      return;
    }
    if (!active || hit(at(e))?.id !== active || Date.now() - wheelAt.current < 180) return;
    wheelAt.current = Date.now();
    stepScale(active, e.deltaY > 0 ? 1 : -1);
  };

  // ---------- commands ----------
  const removeSel = () => { if (!sel.length) return; setDoc((d) => ({ ...d, elements: d.elements.filter((e) => !sel.includes(e.id)) })); setSel([]); if (active && sel.includes(active)) setActive(null); };
  const duplicate = () => {
    if (!sel.length) return;
    const copies = doc.elements.filter((e) => sel.includes(e.id)).map((e) => ({ ...e, id: newId(), name: `${e.name} copy`, x: r1(e.x + 5), y: r1(e.y + 5), locked: false }));
    setDoc((d) => ({ ...d, elements: [...d.elements, ...copies] }));
    setSel(copies.map((c) => c.id));
  };
  const order = (how: "up" | "down" | "front" | "back") => {
    if (!sel.length) return;
    setDoc((d) => {
      const moving = d.elements.filter((e) => sel.includes(e.id)), rest = d.elements.filter((e) => !sel.includes(e.id));
      if (how === "front") return { ...d, elements: [...rest, ...moving] };
      if (how === "back") return { ...d, elements: [...moving, ...rest] };
      const els = [...d.elements];
      const idx = els.map((e, i) => (sel.includes(e.id) ? i : -1)).filter((i) => i >= 0);
      for (const i of how === "up" ? idx.reverse() : idx) {
        const j = how === "up" ? i + 1 : i - 1;
        if (j < 0 || j >= els.length || sel.includes(els[j].id)) continue;
        [els[i], els[j]] = [els[j], els[i]];
      }
      return { ...d, elements: els };
    });
  };
  const nudge = (dx: number, dy: number) => setDoc((d) => ({ ...d, elements: d.elements.map((e) => (sel.includes(e.id) && !e.locked ? ({ ...e, x: r1(e.x + dx), y: r1(e.y + dy) } as El) : e)) }));
  const setPage = (p: Partial<Pick<LayoutDoc, "page" | "orientation">>, rescale: boolean) => setDoc((d) => {
    const [ow, oh] = docSize(d), n = { ...d, ...p }, [nw, nh] = docSize(n);
    if (!rescale) return n;
    const sx = nw / ow, sy = nh / oh;
    return { ...n, elements: n.elements.map((e) => ({ ...e, x: r1(e.x * sx), y: r1(e.y * sy), w: r1(e.w * sx), h: r1(e.h * sy) }) as El) };
  });
  const fromTemplate = (t: Partial<LayoutSettings>) => {
    if (doc.elements.length && !window.confirm("Replace everything on the page with this template? Undo brings it back.")) return;
    const m = mapsOf(doc)[0];
    const d = templateDoc({ ...DEFAULTS, page: doc.page, orientation: doc.orientation, dpi: doc.dpi, title: project.name, center: m?.center ?? [map.getCenter().lng, map.getCenter().lat],
      scale: m?.scale ?? 10000, ...t });
    setDoc(d); setSel([]); setActive(null);
  };
  const useView = (id: string) => { const m = byId(id) as MapEl | null; if (!m) return; const c = map.getCenter(); change(id, { center: [c.lng, c.lat], scale: scaleToFit(map, m.w) }); };

  // keys: undo, delete, nudge, Escape
  useEffect(() => {
    const keys = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t?.closest?.("input, textarea, select, [role=dialog] [role=dialog]")) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); if (e.shiftKey) fwd(); else back(); }
      else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); fwd(); }
      else if (mod && e.key.toLowerCase() === "d") { e.preventDefault(); duplicate(); }
      else if (e.key === "Delete" || e.key === "Backspace") { if (sel.length) { e.preventDefault(); removeSel(); } }
      else if (e.key.startsWith("Arrow") && sel.length) {
        e.preventDefault();
        const s = e.shiftKey ? 5 : 1;
        nudge(e.key === "ArrowLeft" ? -s : e.key === "ArrowRight" ? s : 0, e.key === "ArrowUp" ? -s : e.key === "ArrowDown" ? s : 0);
      } else if (e.key === "Escape") {
        if (tool) setTool(null); else if (active) setActive(null); else if (sel.length) setSel([]);
      }
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  });

  // ---------- export ----------
  const firstMap = maps[0];
  const fileName = (ext: string) => `${(current?.name || project.name).replace(/[\\/:*?"<>|]+/g, " ").trim() || "map"}${firstMap ? ` (1-${firstMap.scale})` : ""}.${ext}`;
  async function bigMaps(dpi: number) {
    const out: Record<string, { canvas: HTMLCanvasElement; jpeg: () => string }> = {};
    let low = dpi, partial = false;
    const shown = maps.filter((m) => m.visible);
    for (const [i, m] of shown.entries()) {
      const px = mapPixels(m, dpi);
      low = Math.min(low, px.dpi);
      setBusy(`Drawing ${shown.length > 1 ? `map ${i + 1} of ${shown.length}` : "the map"} at ${px.dpi} dpi…`);
      const r = await renderMap({ style: printableStyle(map.getStyle()), center: m.center, scale: m.scale, cssW: px.cssW, cssH: px.cssH, ratio: px.ratio });
      partial ||= !r.complete;
      out[m.id] = r;
    }
    if (partial) toast("Some of the map was still loading after a minute; it is printed as far as it got.", "info");
    if (low < dpi) toast(`The map is printed at ${low} dpi: ${dpi} dpi for a frame this large is more than a browser can draw.`, "info");
    return out;
  }
  async function exportPdf() {
    setError(null);
    try {
      const r = await bigMaps(doc.dpi);
      setBusy("Writing the PDF…");
      const { jsPDF } = await import("jspdf");
      const pdf = new jsPDF({ unit: "mm", format: [pw, ph], orientation: pw > ph ? "landscape" : "portrait", compress: true });
      pdf.setProperties({ title: current?.name || project.name, subject: firstMap ? `Scale 1:${firstMap.scale}` : "", creator: "GeoEstate PolyGIS", author: me.name });
      drawPdf(pdf, composed.ops, Object.fromEntries(Object.entries(r).map(([id, x]) => [id, x.jpeg()])));
      pdf.save(fileName("pdf"));
      toast("PDF ready.", "success");
    } catch (e) { setError(e instanceof Error ? e : new Error("Could not make the PDF.")); }
    finally { setBusy(""); }
  }
  async function exportPng() {
    setError(null);
    try {
      let dpi = doc.dpi;
      while (dpi > 72 && ((pw / 25.4) * dpi * (ph / 25.4) * dpi > 60e6 || Math.max(pw, ph) / 25.4 * dpi > 12000)) dpi = Math.floor(dpi * 0.9);
      const r = await bigMaps(dpi);
      setBusy("Drawing the page…");
      const c = document.createElement("canvas");
      c.width = Math.round((pw / 25.4) * dpi); c.height = Math.round((ph / 25.4) * dpi);
      drawCanvas(c.getContext("2d")!, composed.ops, dpi / 25.4, Object.fromEntries(Object.entries(r).map(([id, x]) => [id, { image: x.canvas }])));
      const blob = await new Promise<Blob | null>((ok) => c.toBlob(ok, "image/png"));
      if (!blob) throw new Error("The browser could not make a picture that large: choose a smaller page or a lower resolution.");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = fileName("png"); a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      toast(dpi < doc.dpi ? `PNG ready, at ${dpi} dpi (the most a browser can draw for this page).` : "PNG ready.", "success");
    } catch (e) { setError(e instanceof Error ? e : new Error("Could not make the picture.")); }
    finally { setBusy(""); }
  }

  // ---------- saved layouts ----------
  async function save(asNew: boolean) {
    setError(null);
    try {
      if (current && !asNew) {
        const u = await api.updateLayout(current.id, { settings: doc });
        setCurrent(u); setSaved((x) => [u, ...x.filter((y) => y.id !== u.id)]);
        toast(`${u.name} saved.`, "success");
      } else {
        const name = window.prompt("Name for this layout", current ? `${current.name} (copy)` : `${project.name}, ${doc.page} ${doc.orientation}`);
        if (!name?.trim()) return;
        const c = await api.createLayout(project.id, name.trim(), doc);
        setCurrent(c); setSaved((x) => [c, ...x]);
        toast(`${c.name} saved with the project.`, "success");
      }
      setDirty(false);
    } catch (e) { setError(e instanceof ApiError ? e : new Error("Could not save the layout.")); }
  }
  async function remove() {
    if (!current || !window.confirm(`Delete the layout ${current.name}? The project and its layers are not touched.`)) return;
    try { await api.deleteLayout(current.id); setSaved((x) => x.filter((y) => y.id !== current.id)); setCurrent(null); }
    catch (e) { setError(e); }
  }
  const open = (lid: string) => {
    if (dirty && !window.confirm("Leave this layout without saving it?")) return;
    const l = saved.find((x) => x.id === lid) ?? null;
    const c = map.getCenter();
    setCurrent(l);
    setDocState(l ? docFrom(l.settings, { center: [c.lng, c.lat], scale: 10000, title: project.name }) : firstLayout(map, project.name));
    undo.current = []; redo.current = []; setSel([]); setActive(null); setDirty(false); setPics({});
  };
  const close = () => { if (dirty && canSave && !window.confirm("Close the layout without saving it?")) return; onClose(); };

  // ---------- what is drawn over the page ----------
  const toPx = (r: Rect) => ({ left: r.x * k, top: r.y * k, width: r.w * k, height: r.h * k });
  const drawBox = drag?.kind === "draw" ? { x: Math.min(drag.start[0], drag.now[0]), y: Math.min(drag.start[1], drag.now[1]), w: Math.abs(drag.now[0] - drag.start[0]), h: Math.abs(drag.now[1] - drag.start[1]) } : null;
  const shownMap = (active ? byId(active) : one?.kind === "map" ? one : one && "map" in one ? mapFor(doc, (one as { map: string | null }).map) : firstMap) as MapEl | null;
  const pct = Math.round((k / PX_PER_MM_100) * 100);
  const insert = (kind: ElKind, text?: string) => { setActive(null); setTool(tool?.kind === kind && tool.text === text ? null : { kind, text }); };

  return (
    <div className="lo lv" role="dialog" aria-modal="true" aria-label="Layout">
      <div className="lo-bar">
        <Printer size={18} aria-hidden="true" />
        <strong>Layout</strong>
        <select className="lo-pick" value={current?.id ?? ""} onChange={(e) => open(e.target.value)} aria-label="Saved layouts">
          <option value="">{saved.length ? "New layout" : "New layout (none saved yet)"}</option>
          {saved.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
        {canSave && <>
          <Button small onClick={() => void save(false)}><Save size={14} aria-hidden="true" />{current ? (dirty ? "Save" : "Saved") : "Save layout"}</Button>
          {current && <Button small variant="ghost" onClick={() => void save(true)}>Save as new</Button>}
          {current && <IconButton label="Delete this layout" onClick={() => void remove()}><Trash2 size={16} /></IconButton>}
        </>}
        <span className="ge-spacer" />
        {busy ? <span className="lo-busy" role="status">{busy}</span> : drawing && <span className="lo-busy" role="status">Drawing the map…</span>}
        <Button small onClick={() => void exportPng()} disabled={!!busy}><ImageIcon size={14} aria-hidden="true" />PNG</Button>
        <Button small variant="primary" onClick={() => void exportPdf()} disabled={!!busy}><FileDown size={14} aria-hidden="true" />Export PDF</Button>
        <IconButton label="Close the layout" onClick={close}><X size={18} /></IconButton>
      </div>

      <div className="ws-ribbon-body lv-ribbon" role="toolbar" aria-label="Layout ribbon">
        <Group name="Insert">
          <Tb icon={<MapIcon />} label="Map frame" on={tool?.kind === "map"} onClick={() => insert("map")} title="Draw a map frame on the page" />
          <Tb icon={<Rows />} label="Legend" on={tool?.kind === "legend"} onClick={() => insert("legend")} />
          <Tb icon={<Compass />} label="North arrow" on={tool?.kind === "north"} onClick={() => insert("north")} />
          <Tb icon={<Ruler />} label="Scale bar" on={tool?.kind === "scalebar"} onClick={() => insert("scalebar")} />
          <Tb icon={<Type />} label="Text" on={tool?.kind === "text" && !tool.text} onClick={() => insert("text")} />
          <div className="lv-tb-menu">
            <Menu label="Dynamic text" align="start" trigger={<span className="ws-tb"><span aria-hidden="true" className="ws-tb-icon"><Braces /></span>Dynamic text<ChevronDown size={12} aria-hidden="true" /></span>}
              items={[
                { label: "Title (project name)", onSelect: () => insert("text", "{project}") },
                { label: "Date", onSelect: () => insert("text", "Printed {date}") },
                { label: "Author", onSelect: () => insert("text", "{author}, {org}") },
                { label: "Map scale", onSelect: () => insert("text", "Scale {scale}") },
                { label: "Coordinate system", onSelect: () => insert("text", "Coordinate system: {crs}") },
                { label: "Credits", onSelect: () => insert("text", "{credits}") },
              ]} />
          </div>
          <Tb icon={<Square />} label="Rectangle" on={tool?.kind === "rect"} onClick={() => insert("rect")} />
        </Group>
        <Group name="Map frame">
          <Tb icon={<Hand />} label={active ? "Done" : "Navigate"} on={!!active} disabled={!shownMap}
            onClick={() => (active ? setActive(null) : shownMap && (setActive(shownMap.id), setSel([shownMap.id])))} title="Move and scale the map inside its frame (or double-click the frame)" />
          <Tb icon={<Grid3x3 />} label="Grid" on={!!shownMap?.grid.on} disabled={!shownMap} onClick={() => shownMap && change(shownMap.id, { grid: { ...shownMap.grid, on: !shownMap.grid.on } })} />
          <Tb icon={<LocateFixed />} label="Map's view" disabled={!shownMap} onClick={() => shownMap && useView(shownMap.id)} title="Show what the map shows now" />
        </Group>
        <Group name="Arrange">
          <div className="ws-tb-stack">
            <button type="button" className="ws-tb-small" disabled={!sel.length} onClick={() => order("front")}><BringToFront size={15} aria-hidden="true" />Bring to front</button>
            <button type="button" className="ws-tb-small" disabled={!sel.length} onClick={() => order("back")}><SendToBack size={15} aria-hidden="true" />Send to back</button>
          </div>
          <div className="ws-tb-stack">
            <button type="button" className="ws-tb-small" disabled={!sel.length} onClick={() => order("up")}><ChevronUp size={15} aria-hidden="true" />Bring forward</button>
            <button type="button" className="ws-tb-small" disabled={!sel.length} onClick={() => order("down")}><ChevronDown size={15} aria-hidden="true" />Send backward</button>
          </div>
          <div className="ws-tb-stack">
            <button type="button" className="ws-tb-small" disabled={!sel.length} onClick={duplicate}><Copy size={15} aria-hidden="true" />Duplicate</button>
            <button type="button" className="ws-tb-small" disabled={!sel.length} onClick={removeSel}><Trash2 size={15} aria-hidden="true" />Delete</button>
          </div>
        </Group>
        <Group name="Page">
          <label className="ws-tb-select">Size<select value={doc.page} onChange={(e) => setPage({ page: e.target.value as PageId }, true)}>{Object.keys(PAGES).map((p) => <option key={p}>{p}</option>)}</select></label>
          <label className="ws-tb-select">Orientation<select value={doc.orientation} onChange={(e) => setPage({ orientation: e.target.value as LayoutDoc["orientation"] }, true)}>
            <option value="landscape">Landscape</option><option value="portrait">Portrait</option></select></label>
          <div className="lv-tb-menu">
            <Menu label="Templates" align="start" trigger={<span className="ws-tb"><span aria-hidden="true" className="ws-tb-icon"><LayoutTemplate /></span>Template<ChevronDown size={12} aria-hidden="true" /></span>}
              items={[
                { label: "Legend at the side", onSelect: () => fromTemplate({ legend: "side" }) },
                { label: "Legend inside the map", onSelect: () => fromTemplate({ legend: "inside" }) },
                { label: "Map with grid", onSelect: () => fromTemplate({ legend: "side", grid: { on: true, interval: null, labels: true } }) },
                { label: "Map only", onSelect: () => fromTemplate({ legend: "none", footer: false, north: true, scalebar: true }) },
                { divider: true, label: "-", onSelect: () => undefined },
                { label: "Blank page", onSelect: () => { if (!doc.elements.length || window.confirm("Clear the page? Undo brings it back.")) { setDoc((d) => ({ ...d, elements: [] })); setSel([]); } } },
              ]} />
          </div>
        </Group>
        <Group name="Edit">
          <Tb icon={<Undo2 />} label="Undo" disabled={!undo.current.length} onClick={back} />
          <Tb icon={<Redo2 />} label="Redo" disabled={!redo.current.length} onClick={fwd} />
        </Group>
      </div>

      <div className="lv-body">
        <aside className="lv-contents ws-scroll" aria-label="Contents">
          <div className="ws-pane-head"><h2 className="ws-pane-title">Contents</h2></div>
          <p className="lv-sub">Drawing order</p>
          <ul className="lv-list" role="listbox" aria-label="Elements on the page, top first" aria-multiselectable="true">
            {[...doc.elements].reverse().map((e) => {
              const on = sel.includes(e.id);
              return (
                <li key={e.id} role="option" aria-selected={on} className={`${on ? "is-on" : ""}${e.visible ? "" : " is-hidden"}`}
                  onClick={(ev) => setSel(ev.shiftKey ? (on ? sel.filter((x) => x !== e.id) : [...sel, e.id]) : [e.id])}
                  onDoubleClick={() => e.kind === "map" && setActive(e.id)} tabIndex={0}
                  onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); setSel([e.id]); } }}>
                  <IconButton label={e.visible ? `Hide ${e.name}` : `Show ${e.name}`} onClick={(ev) => { ev.stopPropagation(); change(e.id, { visible: !e.visible }); }}>
                    {e.visible ? <Eye size={14} /> : <EyeOff size={14} />}</IconButton>
                  <IconButton label={e.locked ? `Unlock ${e.name}` : `Lock ${e.name}`} on={e.locked} onClick={(ev) => { ev.stopPropagation(); change(e.id, { locked: !e.locked }); }}>
                    {e.locked ? <Lock size={13} /> : <LockOpen size={13} />}</IconButton>
                  <KindIcon kind={e.kind} />
                  <span className="lv-name">{e.name}</span>
                </li>
              );
            })}
          </ul>
          {!doc.elements.length && <p className="ws-muted lv-sub">The page is empty: insert a map frame from the ribbon, or start from a template.</p>}
        </aside>

        <div className="lv-stage" ref={stage}>
          <RulerAxis axis="x" k={k} len={pw} origin={RULER + pageLeft - scroll[0]} size={RULER} at={cursor?.[0] ?? null} />
          <RulerAxis axis="y" k={k} len={ph} origin={RULER + pageTop - scroll[1]} size={RULER} at={cursor?.[1] ?? null} />
          <div className="lv-ruler-corner" style={{ width: RULER, height: RULER }} aria-hidden="true" />
          <div className="lv-view" style={{ left: RULER, top: RULER }} onWheel={onWheel} onScroll={(e) => setScroll([e.currentTarget.scrollLeft, e.currentTarget.scrollTop])}>
          <div className="lv-scroll" style={{ width: Math.max(box.w - RULER, pw * k + PAD * 2), height: Math.max(box.h - RULER, ph * k + PAD * 2) }}>
            <div className="lv-page-wrap" style={{ left: pageLeft, top: pageTop }}>
              <canvas ref={canvas} className="lo-page" aria-label={`The ${doc.page} ${doc.orientation} page with ${doc.elements.length} elements`} />
              <div className={`lv-over${tool ? " is-insert" : active ? " is-nav" : ""}`} style={{ width: pw * k, height: ph * k }}
                onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp} onDoubleClick={onDouble} onPointerLeave={() => setCursor(null)}>
                {doc.elements.filter((e) => sel.includes(e.id)).map((e) => (
                  <div key={e.id} className={`lv-sel${e.locked ? " is-locked" : ""}`} style={toPx(e)}>
                    {sel.length === 1 && !e.locked && HANDLES.map((h) => <span key={h} className={`lv-h lv-h-${h}`} onPointerDown={(ev) => onHandle(ev, e, h)} />)}
                  </div>
                ))}
                {active && byId(active) && <div className="lv-active" style={toPx(byId(active)!)}><span>Navigating: drag to move the map, scroll to change the scale · Esc when done</span></div>}
                {drawBox && <div className="lv-drawbox" style={toPx(drawBox)} />}
                {guides.x != null && <div className="lv-guide is-v" style={{ left: guides.x * k }} />}
                {guides.y != null && <div className="lv-guide is-h" style={{ top: guides.y * k }} />}
              </div>
            </div>
          </div>
          </div>
        </div>

        <aside className="lv-props ws-scroll" aria-label="Element">
          <div className="ws-pane-head"><h2 className="ws-pane-title">{one ? one.name : sel.length > 1 ? `${sel.length} elements` : "Page"}</h2></div>
          <div className="lv-props-body">
            <ErrorText error={error} />
            {tool && <p className="lv-tip">Draw a box on the page for the {KIND_NAMES[tool.kind].toLowerCase()}, or click to place it at its usual size. Esc cancels.</p>}
            {one ? <ElementForm el={one} doc={doc} onChange={(p) => change(one.id, p)} onView={() => useView(one.id)} onNavigate={() => setActive(one.id)} />
              : sel.length > 1 ? <p className="ws-muted">Drag them together, or use the arrow keys (Shift for 5 mm). Arrange changes their order.</p>
              : <PageForm doc={doc} onPage={setPage} onDpi={(dpi) => setDoc((d) => ({ ...d, dpi }))} />}
          </div>
        </aside>
      </div>

      <div className="lv-status">
        {shownMap ? <label className="lv-scale">{shownMap.name}
          <select value={SCALES.includes(shownMap.scale) ? shownMap.scale : ""} onChange={(e) => e.target.value && change(shownMap.id, { scale: Number(e.target.value) })} aria-label={`Scale of ${shownMap.name}`}>
            {!SCALES.includes(shownMap.scale) && <option value="">1:{shownMap.scale.toLocaleString("en-GB")}</option>}
            {SCALES.map((v) => <option key={v} value={v}>1:{v.toLocaleString("en-GB")}</option>)}</select></label> : <span className="ws-muted">No map frame</span>}
        <label className="lv-zoom">Zoom<select value={zoom === "fit" ? "fit" : String(zoom)} onChange={(e) => setZoom(e.target.value === "fit" ? "fit" : Number(e.target.value))}>
          <option value="fit">Whole page ({pct}%)</option>{ZOOMS.map((z) => <option key={z} value={z}>{z}%</option>)}</select></label>
        <span className="lv-pos" aria-live="off">{cursor ? `${cursor[0].toFixed(1)}, ${cursor[1].toFixed(1)} mm` : ""}</span>
        <span className="ge-spacer" />
        <span className="ws-muted">{doc.page} {doc.orientation} · {pw} × {ph} mm · {doc.dpi} dpi</span>
      </div>
    </div>
  );
}

/** A ruler along the top or left of the view, in millimetres from the page's corner, marking where the pointer is. */
function RulerAxis({ axis, k, len, origin, size, at }: { axis: "x" | "y"; k: number; len: number; origin: number; size: number; at: number | null }) {
  const step = k >= 4 ? 1 : k >= 1.6 ? 5 : 10;
  const label = k >= 2.5 ? 10 : k >= 1 ? 50 : 100;
  const ticks = Array.from({ length: Math.floor(len / step) + 1 }, (_, i) => i * step);
  return (
    <div className={`lv-ruler ${axis === "x" ? "is-top" : "is-left"}`} aria-hidden="true" style={axis === "x" ? { height: size, left: 0, right: 0, top: 0 } : { width: size, top: 0, bottom: 0, left: 0 }}>
      <svg width="100%" height="100%">
        {ticks.map((t) => {
          const p = origin + t * k, big = t % label === 0, mid = !big && t % (label / 2) === 0;
          const l = big ? size * 0.62 : mid ? size * 0.42 : size * 0.24;
          return axis === "x"
            ? <g key={t}><line x1={p} x2={p} y1={size} y2={size - l} />{big && <text x={p + 2} y={9}>{t}</text>}</g>
            : <g key={t}><line y1={p} y2={p} x1={size} x2={size - l} />{big && <text x={9} y={p - 2} transform={`rotate(-90 9 ${p - 2})`}>{t}</text>}</g>;
        })}
        {at != null && (axis === "x" ? <line className="lv-ruler-at" x1={origin + at * k} x2={origin + at * k} y1={0} y2={size} />
          : <line className="lv-ruler-at" y1={origin + at * k} y2={origin + at * k} x1={0} x2={size} />)}
      </svg>
    </div>
  );
}

// ---------- the Element pane ----------
function Num({ label, value, onChange, min, step = 0.1, unit = "mm" }: { label: string; value: number; onChange: (v: number) => void; min?: number; step?: number; unit?: string }) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  const done = () => { const n = Number(v); if (v.trim() !== "" && isFinite(n) && (min == null || n >= min)) { if (n !== value) onChange(n); } else setV(String(value)); };
  return <label className="ws-f lv-num">{label}<span><input type="number" step={step} min={min} value={v} onChange={(e) => setV(e.target.value)} onBlur={done}
    onKeyDown={(e) => { if (e.key === "Enter") done(); }} /><small>{unit}</small></span></label>;
}
function StrokeField({ label, value, onChange, width = 0.3 }: { label: string; value: Stroke | null; onChange: (s: Stroke | null) => void; width?: number }) {
  return (
    <div className="lv-stroke">
      <label className="lo-check"><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked ? { color: "#1f2937", width } : null)} />{label}</label>
      {value && <span className="ws-row">
        <label className="ws-f">Colour<input type="color" value={value.color} onChange={(e) => onChange({ ...value, color: e.target.value })} /></label>
        <Num label="Width" value={value.width} min={0.05} step={0.05} onChange={(w) => onChange({ ...value, width: w })} />
      </span>}
    </div>
  );
}
function FillField({ label, value, onChange }: { label: string; value: string | null; onChange: (s: string | null) => void }) {
  return (
    <div className="lv-stroke">
      <label className="lo-check"><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked ? "#ffffff" : null)} />{label}</label>
      {value && <label className="ws-f">Colour<input type="color" value={value} onChange={(e) => onChange(e.target.value)} /></label>}
    </div>
  );
}

function ElementForm({ el, doc, onChange, onView, onNavigate }: { el: El; doc: LayoutDoc; onChange: (p: Partial<El>) => void; onView: () => void; onNavigate: () => void }) {
  const maps = mapsOf(doc);
  const mapPick = (value: string | null, set: (v: string | null) => void) => maps.length > 1 && (
    <label className="ws-f">Map frame<select value={value ?? ""} onChange={(e) => set(e.target.value || null)}>
      <option value="">The first ({maps[0].name})</option>{maps.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>);
  const text = useRef<HTMLTextAreaElement>(null);
  return (
    <div className="ws-form">
      <label className="ws-f">Name<input value={el.name} maxLength={80} onChange={(e) => onChange({ name: e.target.value })} /></label>
      <fieldset className="lo-set"><legend>Position and size</legend>
        <div className="lv-grid4">
          <Num label="X" value={el.x} onChange={(x) => onChange({ x })} />
          <Num label="Y" value={el.y} onChange={(y) => onChange({ y })} />
          <Num label="Width" value={el.w} min={3} onChange={(w) => onChange({ w })} />
          <Num label="Height" value={el.h} min={3} onChange={(h) => onChange({ h })} />
        </div>
        <div className="ws-row">
          <label className="lo-check"><input type="checkbox" checked={el.visible} onChange={(e) => onChange({ visible: e.target.checked })} />Shown</label>
          <label className="lo-check"><input type="checkbox" checked={el.locked} onChange={(e) => onChange({ locked: e.target.checked })} />Locked</label>
        </div>
      </fieldset>
      {el.kind === "map" && <fieldset className="lo-set"><legend>Map</legend>
        <label className="ws-f">Scale<span className="lo-scale">1 :
          <input type="number" min={100} step={100} value={el.scale} onChange={(e) => Number(e.target.value) >= 50 && onChange({ scale: Math.round(Number(e.target.value)) })} aria-label="Scale denominator" />
          <select value={SCALES.includes(el.scale) ? el.scale : ""} onChange={(e) => e.target.value && onChange({ scale: Number(e.target.value) })} aria-label="A standard scale">
            <option value="">Standard…</option>{SCALES.map((v) => <option key={v} value={v}>1:{v.toLocaleString("en-GB")}</option>)}</select></span></label>
        <div className="ws-row">
          <Button small variant="ghost" onClick={onView}><LocateFixed size={14} aria-hidden="true" />Use the map's view</Button>
          <Button small variant="ghost" onClick={onNavigate}><Hand size={14} aria-hidden="true" />Navigate</Button>
        </div>
        <StrokeField label="Border" value={el.border} onChange={(border) => onChange({ border })} />
        <label className="lo-check"><input type="checkbox" checked={el.grid.on} onChange={(e) => onChange({ grid: { ...el.grid, on: e.target.checked } })} />Grid</label>
        {el.grid.on && <div className="ws-row lo-indent">
          <label className="ws-f">Interval<input type="number" min={0} step="any" value={el.grid.interval ?? ""} placeholder="Automatic"
            onChange={(e) => onChange({ grid: { ...el.grid, interval: e.target.value === "" ? null : Number(e.target.value) } })} /></label>
          <label className="lo-check"><input type="checkbox" checked={el.grid.labels} onChange={(e) => onChange({ grid: { ...el.grid, labels: e.target.checked } })} />Coordinates</label>
        </div>}
        <p className="ws-muted">Layers print as they are shown in the map now.</p>
      </fieldset>}
      {el.kind === "legend" && <fieldset className="lo-set"><legend>Legend</legend>
        <label className="ws-f">Title<input value={el.title} maxLength={80} onChange={(e) => onChange({ title: e.target.value })} placeholder="No title" /></label>
        <Num label="Text size" unit="pt" value={r1(el.size)} min={4} step={0.5} onChange={(size) => onChange({ size })} />
        {mapPick(el.map, (map) => onChange({ map }))}
        <FillField label="Background" value={el.fill} onChange={(fill) => onChange({ fill })} />
        <StrokeField label="Border" value={el.border} width={0.2} onChange={(border) => onChange({ border })} />
        <p className="ws-muted">Lists the layers shown in the map, top first. Make the box larger for more columns.</p>
      </fieldset>}
      {el.kind === "north" && <fieldset className="lo-set"><legend>North arrow</legend>
        <label className="ws-f">Colour<input type="color" value={el.color} onChange={(e) => onChange({ color: e.target.value })} /></label></fieldset>}
      {el.kind === "scalebar" && <fieldset className="lo-set"><legend>Scale bar</legend>
        {mapPick(el.map, (map) => onChange({ map }))}
        <Num label="Text size" unit="pt" value={r1(el.size)} min={4} step={0.5} onChange={(size) => onChange({ size })} />
        <label className="lo-check"><input type="checkbox" checked={el.showScale} onChange={(e) => onChange({ showScale: e.target.checked })} />Show the scale (1:…)</label>
        <label className="ws-f">Colour<input type="color" value={el.color} onChange={(e) => onChange({ color: e.target.value })} /></label>
        <p className="ws-muted">The bar is a round length that fits its box: make the box wider for a longer bar.</p>
      </fieldset>}
      {el.kind === "text" && <fieldset className="lo-set"><legend>Text</legend>
        <label className="ws-f">Text<textarea ref={text} rows={3} value={el.text} maxLength={2000} onChange={(e) => onChange({ text: e.target.value })} /></label>
        <div className="lv-tokens" aria-label="Dynamic text">{TOKENS.map(([t, about]) => (
          <button key={t} type="button" title={about} onClick={() => {
            const ta = text.current, a = ta?.selectionStart ?? el.text.length, b = ta?.selectionEnd ?? el.text.length;
            onChange({ text: el.text.slice(0, a) + t + el.text.slice(b) });
          }}>{t}</button>))}</div>
        <div className="ws-row">
          <Num label="Size" unit="pt" value={r1(el.size)} min={3} step={0.5} onChange={(size) => onChange({ size })} />
          <label className="ws-f">Colour<input type="color" value={el.color} onChange={(e) => onChange({ color: e.target.value })} /></label>
        </div>
        <div className="ws-row">
          <label className="ws-f">Align<select value={el.align} onChange={(e) => onChange({ align: e.target.value as "left" })}>
            <option value="left">Left</option><option value="center">Centre</option><option value="right">Right</option></select></label>
          <label className="lo-check"><input type="checkbox" checked={el.bold} onChange={(e) => onChange({ bold: e.target.checked })} />Bold</label>
        </div>
        <FillField label="Background" value={el.fill} onChange={(fill) => onChange({ fill })} />
        <StrokeField label="Border" value={el.border} width={0.2} onChange={(border) => onChange({ border })} />
      </fieldset>}
      {el.kind === "rect" && <fieldset className="lo-set"><legend>Rectangle</legend>
        <FillField label="Fill" value={el.fill} onChange={(fill) => onChange({ fill })} />
        <StrokeField label="Outline" value={el.border} width={0.35} onChange={(border) => onChange({ border })} />
      </fieldset>}
    </div>
  );
}

function PageForm({ doc, onPage, onDpi }: { doc: LayoutDoc; onPage: (p: Partial<Pick<LayoutDoc, "page" | "orientation">>, rescale: boolean) => void; onDpi: (d: number) => void }) {
  const [rescale, setRescale] = useState(true);
  const px = mapsOf(doc)[0] ? mapPixels(mapsOf(doc)[0], doc.dpi) : null;
  return (
    <div className="ws-form">
      <fieldset className="lo-set"><legend>Page</legend>
        <div className="ws-row">
          <label className="ws-f">Size<select value={doc.page} onChange={(e) => onPage({ page: e.target.value as PageId }, rescale)}>{Object.keys(PAGES).map((p) => <option key={p}>{p}</option>)}</select></label>
          <label className="ws-f">Orientation<select value={doc.orientation} onChange={(e) => onPage({ orientation: e.target.value as LayoutDoc["orientation"] }, rescale)}>
            <option value="landscape">Landscape</option><option value="portrait">Portrait</option></select></label>
        </div>
        <label className="lo-check"><input type="checkbox" checked={rescale} onChange={(e) => setRescale(e.target.checked)} />Move and resize the elements with the page</label>
        <label className="ws-f">Resolution for export<select value={doc.dpi} onChange={(e) => onDpi(Number(e.target.value))}>
          <option value={150}>150 dpi (draft)</option><option value={200}>200 dpi</option><option value={300}>300 dpi (print)</option></select>
          {px && px.dpi < doc.dpi && <span className="ws-help">The largest map frame is drawn at {px.dpi} dpi, the most a browser can draw.</span>}</label>
      </fieldset>
      <div className="lv-help">
        <p><strong>Insert</strong> an element from the ribbon, then draw its box on the page (or click for its usual size).</p>
        <p><strong>Select</strong> by clicking it on the page or in Contents (Shift adds to the selection); drag to move, drag a handle to resize (Shift keeps the shape, Alt stops snapping).</p>
        <p><strong>Navigate</strong> a map frame: double-click it, then drag to move the map and scroll to change the scale.</p>
        <p>Arrow keys nudge by 1 mm (Shift: 5 mm) · Delete removes · Ctrl+D duplicates · Ctrl+Z undoes.</p>
      </div>
    </div>
  );
}
