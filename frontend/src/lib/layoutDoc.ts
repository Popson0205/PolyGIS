/** A layout as ArcGIS Pro keeps one: a page with elements on it (map frames, legends, north arrows, scale bars, text
 *  and rectangles), each in its own box in millimetres, in drawing order (the first is drawn first, at the bottom).
 *  Every element turns into the same simple drawing operations as before (layout.ts), so the preview, the PNG and
 *  the PDF still draw them the same way. Pure: no DOM. */
import {
  DEFAULTS, PAGES, fitText, gridOps, growth, legendOps, northOps, pageSize, roughMeasure, sheetFrames, wrap,
  type LayoutContext, type LayoutSettings, type Op, type PageId, type Rect, type XY,
} from "./layout";

export type ElKind = "map" | "legend" | "north" | "scalebar" | "text" | "rect";
export interface Stroke { color: string; width: number }
interface Base { id: string; kind: ElKind; name: string; x: number; y: number; w: number; h: number; visible: boolean; locked: boolean }
export interface MapEl extends Base { kind: "map"; center: XY; scale: number; border: Stroke | null; grid: { on: boolean; interval: number | null; labels: boolean } }
export interface LegendEl extends Base { kind: "legend"; map: string | null; title: string; size: number; fill: string | null; border: Stroke | null }
export interface NorthEl extends Base { kind: "north"; color: string }
export interface ScaleBarEl extends Base { kind: "scalebar"; map: string | null; size: number; showScale: boolean; color: string }
export interface TextEl extends Base { kind: "text"; text: string; size: number; bold: boolean; color: string; align: "left" | "center" | "right"; fill: string | null; border: Stroke | null }
export interface RectEl extends Base { kind: "rect"; fill: string | null; border: Stroke | null }
export type El = MapEl | LegendEl | NorthEl | ScaleBarEl | TextEl | RectEl;

export interface LayoutDoc { v: 2; page: PageId; orientation: "portrait" | "landscape"; dpi: number; elements: El[] }

export const KIND_NAMES: Record<ElKind, string> = { map: "Map frame", legend: "Legend", north: "North arrow", scalebar: "Scale bar", text: "Text", rect: "Rectangle" };
/** Words dynamic text can carry, replaced when the page is drawn. */
export const TOKENS: [string, string][] = [
  ["{project}", "The project's name"], ["{date}", "Today's date"], ["{author}", "Your name"], ["{org}", "Your organization"],
  ["{scale}", "The scale of the first map frame"], ["{crs}", "The project's coordinate system"], ["{credits}", "The basemap's credits"],
];

let seq = 0;
export const newId = () => `e${Date.now().toString(36)}${(seq++).toString(36)}`;

export const docSize = (d: Pick<LayoutDoc, "page" | "orientation">) => pageSize(d);
export const mapsOf = (d: LayoutDoc) => d.elements.filter((e): e is MapEl => e.kind === "map");
/** The map frame an element follows: the one it names, or else the first. */
export const mapFor = (d: LayoutDoc, id: string | null | undefined) => mapsOf(d).find((m) => m.id === id) ?? mapsOf(d)[0] ?? null;

/** A name not yet used on the page: "Legend", "Legend 2"… */
export function freshName(d: LayoutDoc, kind: ElKind) {
  const base = KIND_NAMES[kind];
  const used = new Set(d.elements.map((e) => e.name));
  if (!used.has(base)) return base;
  let n = 2;
  while (used.has(`${base} ${n}`)) n++;
  return `${base} ${n}`;
}

/** A new element of this kind in `box`, set up the way it is most often wanted. */
export function makeElement(d: LayoutDoc, kind: ElKind, box: Rect, view?: { center: XY; scale: number }, text?: string): El {
  const [pw, ph] = docSize(d);
  const f = growth(pw, ph);
  const base = { id: newId(), name: freshName(d, kind), ...box, visible: true, locked: false };
  const firstMap = mapsOf(d)[0];
  switch (kind) {
    case "map": return { ...base, kind, center: view?.center ?? firstMap?.center ?? [0, 0], scale: view?.scale ?? firstMap?.scale ?? 10000,
      border: { color: "#1f2937", width: 0.3 * f }, grid: { on: false, interval: null, labels: true } };
    case "legend": return { ...base, kind, map: null, title: "Legend", size: 7 * f, fill: "#ffffff", border: null };
    case "north": return { ...base, kind, color: "#1f2937" };
    case "scalebar": return { ...base, kind, map: null, size: 6.5 * f, showScale: true, color: "#1f2937" };
    case "text": return { ...base, kind, text: text ?? "Text", size: 12 * f, bold: false, color: "#111827", align: "left", fill: null, border: null };
    case "rect": return { ...base, kind, fill: null, border: { color: "#1f2937", width: 0.35 * f } };
  }
}

/** The size an element gets when it is placed with a click rather than drawn as a box. */
export function defaultSize(d: LayoutDoc, kind: ElKind): [number, number] {
  const [pw, ph] = docSize(d);
  const f = growth(pw, ph);
  switch (kind) {
    case "map": return [Math.min(pw, ph) * 0.6, Math.min(pw, ph) * 0.45];
    case "legend": return [55 * f, 45 * f];
    case "north": return [10 * f, 17 * f];
    case "scalebar": return [60 * f, 10 * f];
    case "text": return [70 * f, 9 * f];
    case "rect": return [50 * f, 30 * f];
  }
}

const dynamic = (s: string, d: LayoutDoc, ctx: LayoutContext) => {
  const m = mapsOf(d)[0];
  const crs = ctx.crs ? `${ctx.crs.name}${ctx.crs.epsg ? ` (EPSG:${ctx.crs.epsg})` : ""}` : "WGS 84";
  const v: Record<string, string> = {
    "{project}": ctx.project ?? "", "{date}": ctx.date ?? "", "{author}": ctx.author ?? "", "{org}": ctx.org ?? "",
    "{scale}": m ? `1:${Math.round(m.scale).toLocaleString("en-GB")}` : "", "{crs}": crs, "{credits}": ctx.credits ?? "",
  };
  return s.replace(/\{(project|date|author|org|scale|crs|credits)\}/g, (t) => v[t] ?? t)
    .replace(/(\s*·\s*)+$/g, "").replace(/^(\s*·\s*)+/g, "").replace(/(·\s*){2,}/g, "· ");      // separators left by empty words
};

/** A scale bar filling its box: a round ground length in four parts, the scale above if wanted. */
function scaleBarIn(e: ScaleBarEl, scale: number): Op[] {
  const f = e.size / 6.5;
  const top = e.showScale ? 3.4 * f : 0;
  const bh = Math.max(0.8, Math.min(1.8 * f, e.h - top - 3.4 * f));
  const ground = (e.w * 0.92 * scale) / 1000;
  const p = 10 ** Math.floor(Math.log10(Math.max(ground, 1e-9)));
  const len = [1, 2, 2.5, 5, 10].map((n) => n * p).filter((v) => v <= ground).pop() ?? p;
  const mm = (len * 1000) / scale, seg = mm / 4;
  const unit = len >= 1000 ? "km" : "m";
  const show = (v: number) => Number((unit === "km" ? v / 1000 : v).toPrecision(4)).toString();
  const y = e.y + top, x = e.x, c = e.color;
  const ops: Op[] = [];
  if (e.showScale) ops.push({ k: "text", x, y: y - 1 * f, s: `Scale 1:${Math.round(scale).toLocaleString("en-GB")}`, pt: 7.5 * f, color: c, base: "bottom" });
  for (let i = 0; i < 4; i++) ops.push({ k: "rect", x: x + i * seg, y, w: seg, h: bh, fill: i % 2 ? "#ffffff" : c, stroke: c, lw: 0.2 * f });
  [0, 2, 4].forEach((i, n) => ops.push({ k: "text", x: x + i * seg, y: y + bh + 0.8 * f, s: show([0, len / 2, len][n]) + (n === 2 ? ` ${unit}` : ""), pt: e.size, color: c,
    align: n === 0 ? "left" : n === 2 ? "left" : "center", base: "top" }));
  return ops;
}

function frameOps(e: { x: number; y: number; w: number; h: number; fill: string | null; border: Stroke | null }): Op[] {
  if (!e.fill && !e.border) return [];
  return [{ k: "rect", x: e.x, y: e.y, w: e.w, h: e.h, fill: e.fill ?? undefined, stroke: e.border?.color, lw: e.border?.width }];
}

/** What one element draws. */
export function elementOps(e: El, d: LayoutDoc, ctx: LayoutContext): Op[] {
  const measure = ctx.measure ?? roughMeasure;
  const [pw, ph] = docSize(d);
  const f = growth(pw, ph);
  switch (e.kind) {
    case "map": {
      const ops: Op[] = [{ k: "map", id: e.id, x: e.x, y: e.y, w: e.w, h: e.h }];
      if (e.grid.on) ops.push(...gridOps(e, e.center, e.scale, e.grid, ctx, f));
      if (e.border) ops.push({ k: "rect", x: e.x, y: e.y, w: e.w, h: e.h, stroke: e.border.color, lw: e.border.width });
      return ops;
    }
    case "legend": {
      const k = e.size / 7, pad = 2.5 * k;
      return [...frameOps(e), ...legendOps(ctx.layers, { x: e.x + pad, y: e.y + pad, w: e.w - 2 * pad, h: e.h - 2 * pad }, k, measure, e.title.trim() || false).ops];
    }
    case "north": {
      const k = Math.min(e.h / 15.5, e.w / 8);
      return northOps(e.x + e.w / 2, e.y + (e.h - 15 * k) / 2, k).ops.map((o) =>
        (o.k === "poly" && o.fill === "#1f2937" ? { ...o, fill: e.color } : o.k === "poly" ? { ...o, stroke: e.color } : o.k === "text" ? { ...o, color: e.color } : o));
    }
    case "scalebar": {
      const m = mapFor(d, e.map);
      if (!m) return [{ k: "text", x: e.x, y: e.y, s: "Scale bar: add a map frame", pt: 7, color: "#9ca3af", base: "top" }];
      return scaleBarIn(e, m.scale);
    }
    case "text": {
      const pad = e.fill || e.border ? 1.5 * f : 0;
      const lh = e.size * 0.3528 * 1.22;
      const width = Math.max(1, e.w - 2 * pad);
      const lines = wrap(dynamic(e.text, d, ctx), e.size, width, (s, pt) => measure(s, pt, e.bold));
      const room = Math.max(1, Math.floor((e.h - 2 * pad + 0.01) / lh));
      const shown = lines.length > room ? [...lines.slice(0, room - 1), fitText(lines.slice(room - 1).join(" "), e.size, width, measure)] : lines;
      const x = e.align === "center" ? e.x + e.w / 2 : e.align === "right" ? e.x + e.w - pad : e.x + pad;
      return [...frameOps(e), ...shown.map((s, i): Op => ({ k: "text", x, y: e.y + pad + i * lh, s, pt: e.size, bold: e.bold, color: e.color, align: e.align, base: "top" }))];
    }
    case "rect": return frameOps(e);
  }
}

/** The whole page, bottom element first. */
export function composeDoc(d: LayoutDoc, ctx: LayoutContext): { w: number; h: number; ops: Op[] } {
  const [w, h] = docSize(d);
  const ops: Op[] = [{ k: "rect", x: 0, y: 0, w, h, fill: "#ffffff" }];
  for (const e of d.elements) if (e.visible) ops.push(...elementOps(e, d, ctx));
  return { w, h, ops };
}

/** The classic sheet (title, map, legend column, scale bar, north arrow, notes, footer) as elements to rearrange. */
export function templateDoc(s: LayoutSettings): LayoutDoc {
  const d: LayoutDoc = { v: 2, page: s.page, orientation: s.orientation, dpi: s.dpi, elements: [] };
  const { w, h, f, frame, panel, header, footer } = sheetFrames(s);
  const add = (kind: ElKind, box: Rect, p: Record<string, unknown> = {}) => { const e = { ...makeElement(d, kind, box, { center: s.center, scale: s.scale }), ...p } as El; d.elements.push(e); return e; };
  const m = 10 * f;
  add("rect", { x: m, y: m, w: w - 2 * m, h: h - 2 * m }, { name: "Neatline", border: { color: "#1f2937", width: 0.5 * f } });
  add("map", frame, { name: "Map frame", grid: { ...s.grid } });
  if (s.title) add("text", { x: header.x, y: header.y, w: header.w, h: 8 * f }, { name: "Title", text: s.title, size: 18 * f, bold: true });
  if (s.subtitle) add("text", { x: header.x, y: header.y + (s.title ? 8 * f : 0), w: header.w, h: 5 * f }, { name: "Subtitle", text: s.subtitle, size: 10 * f, color: "#4b5563" });
  const blocks = (x: number, bottom: number, width: number) => {
    if (s.north) add("north", { x, y: bottom - 18 * f, w: 7.5 * f, h: 16 * f });
    if (s.scalebar) add("scalebar", { x: x + (s.north ? 10 * f : 0), y: bottom - 11 * f, w: Math.min(60 * f, width - (s.north ? 12 * f : 0)), h: 10 * f });
    return s.north || s.scalebar ? bottom - 20 * f : bottom;
  };
  if (panel) {
    add("rect", panel, { name: "Legend panel", border: { color: "#1f2937", width: 0.25 * f } });
    const inner = { x: panel.x + 4 * f, y: panel.y + 4 * f, w: panel.w - 8 * f, h: panel.h - 8 * f };
    if (s.orientation === "landscape") {
      let bottom = blocks(inner.x, inner.y + inner.h, inner.w);
      if (s.notes.trim()) { const nh = Math.min(40 * f, inner.h * 0.25); add("text", { x: inner.x, y: bottom - nh - 2 * f, w: inner.w, h: nh }, { name: "Notes", text: s.notes.trim(), size: 7 * f, color: "#374151" }); bottom -= nh + 4 * f; }
      add("legend", { x: inner.x - 2.5 * f, y: inner.y - 2.5 * f, w: inner.w + 5 * f, h: bottom - inner.y }, { fill: null });
    } else {
      const sideW = Math.min(70 * f, inner.w * 0.38);
      const right = { x: inner.x + inner.w - sideW, y: inner.y, w: sideW, h: inner.h };
      const top = blocks(right.x, right.y + right.h, right.w);
      if (s.notes.trim()) add("text", { x: right.x, y: right.y, w: right.w, h: Math.max(4 * f, top - right.y - 2 * f) }, { name: "Notes", text: s.notes.trim(), size: 7 * f, color: "#374151" });
      add("legend", { x: inner.x - 2.5 * f, y: inner.y - 2.5 * f, w: inner.w - sideW - 4 * f, h: inner.h + 5 * f }, { fill: null });
    }
  } else {
    if (s.legend === "inside") add("legend", { x: frame.x + 4 * f, y: frame.y + frame.h * 0.5, w: Math.min(65 * f, frame.w * 0.4), h: frame.h * 0.5 - 4 * f }, { border: { color: "#9ca3af", width: 0.2 * f } });
    if (s.scalebar || s.north) {
      const bw = 58 * f, bh = 22 * f, bx = frame.x + frame.w - bw - 4 * f, by = frame.y + frame.h - bh - 4 * f;
      add("rect", { x: bx, y: by, w: bw, h: bh }, { name: "Scale box", fill: "#ffffff", border: { color: "#9ca3af", width: 0.2 * f } });
      blocks(bx + 3 * f, by + bh - 1 * f, bw - 6 * f);
    }
    if (s.notes.trim()) add("text", { x: frame.x + 4 * f, y: frame.y + 4 * f, w: frame.w * 0.45, h: 20 * f }, { name: "Notes", text: s.notes.trim(), size: 7 * f, color: "#374151", fill: "#ffffff", border: { color: "#9ca3af", width: 0.2 * f } });
  }
  if (footer) {
    const fw = footer.w / 2 - 2 * f;
    add("text", { x: footer.x, y: footer.y, w: fw, h: footer.h }, { name: "Footer", text: "Coordinate system: {crs} · Printed {date} · by {author}, {org}", size: 6 * f, color: "#4b5563" });
    add("text", { x: footer.x + footer.w - fw, y: footer.y, w: fw, h: footer.h }, { name: "Credits", text: "{credits} · Made with GeoEstate PolyGIS", size: 6 * f, color: "#4b5563", align: "right" });
  }
  return d;
}

/** A saved layout as a page of elements: new ones as they are, older ones (kept as template settings) laid out. */
export function docFrom(saved: unknown, fallback: { center: XY; scale: number; title: string }): LayoutDoc {
  const s = (saved ?? {}) as Partial<LayoutDoc> & Partial<LayoutSettings>;
  if (s.v === 2 && Array.isArray(s.elements)) {
    return { v: 2, page: s.page && PAGES[s.page] ? s.page : "A4", orientation: s.orientation === "portrait" ? "portrait" : "landscape", dpi: s.dpi ?? 300,
      elements: s.elements.filter((e) => e && typeof e === "object" && e.kind in KIND_NAMES) };
  }
  return templateDoc({ ...DEFAULTS, ...fallback, ...s, grid: { ...DEFAULTS.grid, ...(s.grid ?? {}) } } as LayoutSettings);
}

// ---------- moving things about ----------
export type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
const MIN = 3;

/** The box after dragging one of its handles by (dx, dy) mm; Shift keeps the shape. */
export function resizeBox(b: Rect, h: Handle, dx: number, dy: number, keepRatio = false): Rect {
  let { x, y, w, h: hh } = b;
  if (h.includes("w")) { x += dx; w -= dx; }
  if (h.includes("e")) w += dx;
  if (h.includes("n")) { y += dy; hh -= dy; }
  if (h.includes("s")) hh += dy;
  if (keepRatio && h.length === 2) {
    const r = b.w / b.h;
    if (Math.abs(w / b.w) > Math.abs(hh / b.h)) { const nh = w / r; if (h.includes("n")) y -= nh - hh; hh = nh; }
    else { const nw = hh * r; if (h.includes("w")) x -= nw - w; w = nw; }
  }
  if (w < MIN) { if (h.includes("w")) x -= MIN - w; w = MIN; }
  if (hh < MIN) { if (h.includes("n")) y -= MIN - hh; hh = MIN; }
  return { x, y, w, h: hh };
}

/** Lines things snap to: the page's edges and middle, and the edges and middles of the other elements. */
export function snapLines(d: LayoutDoc, skip: Set<string>): { xs: number[]; ys: number[] } {
  const [w, h] = docSize(d);
  const xs = [0, w / 2, w], ys = [0, h / 2, h];
  for (const e of d.elements) if (e.visible && !skip.has(e.id)) { xs.push(e.x, e.x + e.w / 2, e.x + e.w); ys.push(e.y, e.y + e.h / 2, e.y + e.h); }
  return { xs, ys };
}

/** The nudge that brings the nearest of `edges` onto a line within `tol` mm, and the line it lands on. */
export function snap(edges: number[], lines: number[], tol: number): { d: number; at: number | null } {
  let best = { d: 0, at: null as number | null, gap: tol + 1e-9 };
  for (const e of edges) for (const l of lines) { const g = Math.abs(l - e); if (g < best.gap) best = { d: l - e, at: l, gap: g }; }
  return { d: best.d, at: best.at };
}
