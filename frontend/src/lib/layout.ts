/** Print layouts: a map sheet worked out in millimetres on the page, as a list of simple drawing operations that two
 *  renderers draw the same way: a canvas (the preview and PNG) and jsPDF (vector PDF, with the map as an image).
 *  Everything here is pure: the map picture itself is made elsewhere (printMap.ts). */
import { RASTER_RAMPS } from "../components/RasterPanel";
import type { CrsDef, Layer, RasterStyle } from "./types";

export type PageId = "A4" | "A3" | "A2" | "A1" | "A0" | "Letter" | "Legal" | "Tabloid";
export const PAGES: Record<PageId, [number, number]> = {
  A4: [210, 297], A3: [297, 420], A2: [420, 594], A1: [594, 841], A0: [841, 1189], Letter: [215.9, 279.4], Legal: [215.9, 355.6], Tabloid: [279.4, 431.8],
};
export const SCALES = [500, 1000, 1250, 2000, 2500, 5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000, 2000000, 5000000];

export interface LayoutSettings {
  page: PageId; orientation: "portrait" | "landscape"; dpi: number;
  center: [number, number]; scale: number;
  title: string; subtitle: string; notes: string;
  legend: "side" | "inside" | "none"; scalebar: boolean; north: boolean;
  grid: { on: boolean; interval: number | null; labels: boolean };
  footer: boolean;
}
export const DEFAULTS: Omit<LayoutSettings, "center" | "scale" | "title"> = {
  page: "A4", orientation: "landscape", dpi: 300, subtitle: "", notes: "", legend: "side", scalebar: true, north: true,
  grid: { on: false, interval: null, labels: true }, footer: true,
};

export type XY = [number, number];
export interface Rect { x: number; y: number; w: number; h: number }
export type Op =
  | ({ k: "rect"; fill?: string; stroke?: string; lw?: number } & Rect)
  | { k: "line"; pts: XY[]; stroke: string; lw: number; clip?: Rect }
  | { k: "poly"; pts: XY[]; fill: string; stroke?: string; lw?: number }
  | { k: "circle"; x: number; y: number; r: number; fill?: string; stroke?: string; lw?: number }
  | { k: "text"; x: number; y: number; s: string; pt: number; color: string; bold?: boolean; align?: "left" | "center" | "right"; angle?: number; base?: "top" | "middle" | "bottom" }
  | ({ k: "gradient"; colors: string[]; vertical: boolean } & Rect)
  | ({ k: "map" } & Rect);

/** Width of a line of text in millimetres (Helvetica). The canvas measures it; tests use an average. */
export type Measure = (s: string, pt: number, bold?: boolean) => number;
export const roughMeasure: Measure = (s, pt, bold) => s.length * pt * 0.3528 * (bold ? 0.56 : 0.52);
const PT = 0.3528;                       // millimetres in a point

export interface LayoutContext {
  layers: Layer[];                       // the project's layers; visible ones are drawn and listed
  crs?: CrsDef;                          // the project's system, for the grid and the footer
  toCrs?: (lng: number, lat: number) => XY;
  author?: string; org?: string; date?: string; credits?: string;
  measure?: Measure;
}

// ---------- Web Mercator: the map on the page ----------
const R = 6378137;
const mx = (lng: number) => (R * lng * Math.PI) / 180;
const my = (lat: number) => R * Math.log(Math.tan(Math.PI / 4 + (Math.max(-85.05, Math.min(85.05, lat)) * Math.PI) / 360));
const lngOf = (x: number) => (x / R) * (180 / Math.PI);
const latOf = (y: number) => (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * (180 / Math.PI);

/** Mercator metres per millimetre of paper: the scale holds at the map's centre. */
export const mercPerMm = (scale: number, lat: number) => scale / 1000 / Math.cos((lat * Math.PI) / 180);

/** Turns longitude and latitude into millimetres on the page, and back, for a map frame. */
export function frameProjection(frame: Rect, center: XY, scale: number) {
  const k = mercPerMm(scale, center[1]);
  const cx = mx(center[0]), cy = my(center[1]);
  return {
    k,
    toPage: (lng: number, lat: number): XY => [frame.x + frame.w / 2 + (mx(lng) - cx) / k, frame.y + frame.h / 2 - (my(lat) - cy) / k],
    toLngLat: (x: number, y: number): XY => [lngOf(cx + (x - frame.x - frame.w / 2) * k), latOf(cy - (y - frame.y - frame.h / 2) * k)],
  };
}

/** The MapLibre zoom that draws `scale` when one CSS pixel is 1/96 inch. */
export function zoomForScale(scale: number, lat: number) {
  const mercPerCssPx = mercPerMm(scale, lat) * (25.4 / 96);
  return Math.log2((2 * Math.PI * R) / (512 * mercPerCssPx));
}
/** The scale a map shows at this zoom and latitude, on a 96 dpi screen. */
export function scaleForZoom(zoom: number, lat: number) {
  return ((2 * Math.PI * R * Math.cos((lat * Math.PI) / 180)) / (512 * 2 ** zoom)) * (96 / 0.0254);
}
/** A round scale near this one, as maps are printed (1:2,500, 1:10,000…). */
export function roundScale(s: number) {
  if (!isFinite(s) || s <= 0) return 10000;
  const p = 10 ** Math.floor(Math.log10(s));
  const n = [1, 1.25, 2, 2.5, 5, 10].reduce((best, m) => (Math.abs(m * p - s) < Math.abs(best * p - s) ? m : best), 1);
  return Math.round(n * p);
}

/** How many pixels the map picture needs, keeping within what a browser can draw (a long side of 8,192 and 48 million
 *  pixels); the resolution actually used is returned so the sheet can say so. */
export function mapPixels(frame: Rect, dpi: number) {
  let d = dpi;
  const px = (mm: number) => Math.round((mm / 25.4) * d);
  while (d > 72 && (Math.max(px(frame.w), px(frame.h)) > 8192 || px(frame.w) * px(frame.h) > 48e6)) d = Math.floor(d * 0.9);
  return { dpi: d, w: px(frame.w), h: px(frame.h), cssW: Math.round((frame.w / 25.4) * 96), cssH: Math.round((frame.h / 25.4) * 96), ratio: d / 96 };
}

// ---------- the sheet ----------
export function pageSize(s: Pick<LayoutSettings, "page" | "orientation">): [number, number] {
  const [a, b] = PAGES[s.page] ?? PAGES.A4;
  return s.orientation === "landscape" ? [b, a] : [a, b];
}
/** Text and elements grow with the sheet, a little less than the sheet does. */
export const growth = (w: number, h: number) => Math.max(1, (Math.min(w, h) / 210) ** 0.7);

export interface Sheet { w: number; h: number; f: number; frame: Rect; panel: Rect | null; header: Rect; footer: Rect | null }

/** Where the title, the map, the side panel and the footer go. */
export function sheetFrames(s: LayoutSettings): Sheet {
  const [w, h] = pageSize(s);
  const f = growth(w, h);
  const m = 10 * f;                                       // margin to the neatline
  const pad = 3 * f;
  const titleH = (s.title ? 7.5 : 0) * f + (s.subtitle ? 5 : 0) * f + (s.title || s.subtitle ? 3 * f : 0);
  const header = { x: m + pad, y: m + pad, w: w - 2 * (m + pad), h: titleH };
  const footH = s.footer ? 6 * f : 0;
  const footer = s.footer ? { x: m + pad, y: h - m - pad - footH + 1.5 * f, w: w - 2 * (m + pad), h: footH } : null;
  const gutter = s.grid.on && s.grid.labels ? 5 * f : 0;    // room for the grid's coordinates round the map
  const top = m + pad + titleH + gutter, bottom = h - m - pad - footH - gutter;
  const left = m + pad + gutter, right = w - m - pad - gutter;
  let panel: Rect | null = null;
  let frame: Rect = { x: left, y: top, w: right - left, h: bottom - top };
  if (s.legend === "side") {
    if (s.orientation === "landscape") {
      const pw = Math.min(70 * f, (right - left) * 0.28);
      frame = { ...frame, w: frame.w - pw - 4 * f - gutter };
      panel = { x: w - m - pad - pw, y: top - gutter, w: pw, h: bottom - top + 2 * gutter };
    } else {
      const ph = Math.min(62 * f, (bottom - top) * 0.3);
      frame = { ...frame, h: frame.h - ph - 4 * f - gutter };
      panel = { x: left - gutter, y: h - m - pad - footH - ph, w: right - left + 2 * gutter, h: ph };
    }
  }
  return { w, h, f, frame, panel, header, footer };
}

// ---------- scale bar, north arrow ----------
/** A scale bar about `want` mm long: a round ground length in four parts. */
export function scaleBar(scale: number, wantMm: number) {
  const ground = (wantMm * scale) / 1000;                 // metres the wanted length covers
  const p = 10 ** Math.floor(Math.log10(ground));
  const len = [1, 2, 2.5, 5, 10].map((n) => n * p).filter((v) => v <= ground).pop() ?? p;
  const mm = (len * 1000) / scale;
  const unit = len >= 1000 ? "km" : "m";
  const show = (v: number) => (unit === "km" ? Number((v / 1000).toPrecision(4)).toString() : Number(v.toPrecision(4)).toString());
  return { metres: len, mm, unit, labels: [0, len / 2, len].map(show) };
}

function scaleBarOps(x: number, y: number, maxW: number, scale: number, f: number): { ops: Op[]; h: number } {
  const b = scaleBar(scale, maxW * 0.8);
  const seg = b.mm / 4, bh = 1.6 * f;
  const ops: Op[] = [];
  for (let i = 0; i < 4; i++) ops.push({ k: "rect", x: x + i * seg, y, w: seg, h: bh, fill: i % 2 ? "#ffffff" : "#1f2937", stroke: "#1f2937", lw: 0.2 * f });
  [0, 2, 4].forEach((i, n) => ops.push({ k: "text", x: x + i * seg, y: y + bh + 1 * f, s: b.labels[n] + (n === 2 ? ` ${b.unit}` : ""), pt: 6.5 * f, color: "#1f2937", align: n === 0 ? "left" : n === 2 ? "left" : "center", base: "top" }));
  ops.push({ k: "text", x, y: y - 1.2 * f, s: `Scale 1:${Math.round(scale).toLocaleString("en-GB")}`, pt: 7.5 * f, color: "#1f2937", base: "bottom" });
  return { ops, h: bh + 4.5 * f };
}

function northOps(cx: number, top: number, f: number): { ops: Op[]; h: number } {
  const h = 11 * f, w = 3.6 * f, y = top + 4 * f;
  return {
    h: h + 4 * f,
    ops: [
      { k: "text", x: cx, y: top + 3.4 * f, s: "N", pt: 9 * f, bold: true, color: "#1f2937", align: "center", base: "bottom" },
      { k: "poly", pts: [[cx, y], [cx - w, y + h], [cx, y + h * 0.72]], fill: "#1f2937" },
      { k: "poly", pts: [[cx, y], [cx + w, y + h], [cx, y + h * 0.72]], fill: "#ffffff", stroke: "#1f2937", lw: 0.25 * f },
    ],
  };
}

// ---------- the grid ----------
/** A round grid interval giving about five lines across `span`. */
export function niceInterval(span: number) {
  const raw = span / 5;
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((n) => n * p).reduce((a, b) => (Math.abs(b - raw) < Math.abs(a - raw) ? b : a));
}
const dms = (v: number, pos: string, neg: string) => {
  const a = Math.abs(v), d = Math.floor(a + 1e-9), mFloat = (a - d) * 60, m = Math.floor(mFloat + 1e-9), sec = Math.round((mFloat - m) * 60);
  return `${d}°${String(m).padStart(2, "0")}'${sec ? `${String(sec).padStart(2, "0")}"` : ""}${v >= 0 ? pos : neg}`;
};
const metresLabel = (v: number) => Math.round(v).toLocaleString("en-GB").replace(/,/g, " ");

/** Grid lines of the project's system across the map frame, with their coordinates where they leave the frame. */
export function gridOps(frame: Rect, center: XY, scale: number, s: LayoutSettings, ctx: LayoutContext, f: number): Op[] {
  const geographic = !ctx.crs || ctx.crs.geographic;
  const to = ctx.toCrs ?? ((lng: number, lat: number): XY => [lng, lat]);
  const proj = frameProjection(frame, center, scale);
  // the frame's extent in the project's system, from points round its edge
  const edge: XY[] = [];
  for (let i = 0; i <= 20; i++) {
    const t = i / 20;
    edge.push([frame.x + t * frame.w, frame.y], [frame.x + t * frame.w, frame.y + frame.h], [frame.x, frame.y + t * frame.h], [frame.x + frame.w, frame.y + t * frame.h]);
  }
  const xy = edge.map(([x, y]) => to(...proj.toLngLat(x, y)));
  const [x0, x1] = [Math.min(...xy.map((p) => p[0])), Math.max(...xy.map((p) => p[0]))];
  const [y0, y1] = [Math.min(...xy.map((p) => p[1])), Math.max(...xy.map((p) => p[1]))];
  const step = s.grid.interval && s.grid.interval > 0 ? s.grid.interval : niceInterval(Math.max(x1 - x0, y1 - y0));
  if (!isFinite(step) || step <= 0 || (x1 - x0) / step > 60 || (y1 - y0) / step > 60) return [];
  const back = (X: number, Y: number): XY => {
    // from the project's system back to the page: invert numerically with the frame's own mapping (Newton steps)
    let [px, py] = [frame.x + frame.w / 2, frame.y + frame.h / 2];
    for (let i = 0; i < 12; i++) {
      const [a, b] = to(...proj.toLngLat(px, py));
      const [ax, bx] = to(...proj.toLngLat(px + 0.5, py)), [ay, by] = to(...proj.toLngLat(px, py + 0.5));
      const j = [[(ax - a) / 0.5, (ay - a) / 0.5], [(bx - b) / 0.5, (by - b) / 0.5]];
      const det = j[0][0] * j[1][1] - j[0][1] * j[1][0];
      if (!det) break;
      const dx = X - a, dy = Y - b;
      px += (j[1][1] * dx - j[0][1] * dy) / det; py += (-j[1][0] * dx + j[0][0] * dy) / det;
    }
    return [px, py];
  };
  const ops: Op[] = [];
  const measure = ctx.measure ?? roughMeasure;
  // rotated labels read upwards on both sides, on the alphabetic baseline, so a PDF and a canvas put them in the same place
  const lw = 0.18 * f, color = "#4b5563", pt = 6 * f;
  const lab = (v: number, axis: "x" | "y") => (geographic ? (axis === "x" ? dms(v, "E", "W") : dms(v, "N", "S")) : metresLabel(v));
  const crossings = (pts: XY[], test: (p: XY) => number) => {
    const out: XY[] = [];
    for (let i = 1; i < pts.length; i++) {
      const a = test(pts[i - 1]), b = test(pts[i]);
      if ((a <= 0 && b > 0) || (a > 0 && b <= 0)) { const t = a / (a - b); out.push([pts[i - 1][0] + t * (pts[i][0] - pts[i - 1][0]), pts[i - 1][1] + t * (pts[i][1] - pts[i - 1][1])]); }
    }
    return out;
  };
  const N = 24;
  for (let X = Math.ceil(x0 / step) * step; X <= x1; X += step) {
    const pts = Array.from({ length: N + 1 }, (_, i) => back(X, y0 + ((y1 - y0) * i) / N));
    ops.push({ k: "line", pts, stroke: color, lw, clip: frame });
    if (s.grid.labels) {
      for (const [cx] of crossings(pts, (p) => p[1] - frame.y)) if (cx > frame.x + 2 && cx < frame.x + frame.w - 2) ops.push({ k: "text", x: cx, y: frame.y - 1 * f, s: lab(X, "x"), pt, color, align: "center", base: "bottom" });
      for (const [cx] of crossings(pts, (p) => p[1] - (frame.y + frame.h))) if (cx > frame.x + 2 && cx < frame.x + frame.w - 2) ops.push({ k: "text", x: cx, y: frame.y + frame.h + 1 * f, s: lab(X, "x"), pt, color, align: "center", base: "top" });
    }
  }
  for (let Y = Math.ceil(y0 / step) * step; Y <= y1; Y += step) {
    const pts = Array.from({ length: N + 1 }, (_, i) => back(x0 + ((x1 - x0) * i) / N, Y));
    ops.push({ k: "line", pts, stroke: color, lw, clip: frame });
    if (s.grid.labels) {
      for (const [, cy] of crossings(pts, (p) => p[0] - frame.x)) if (cy > frame.y + 2 && cy < frame.y + frame.h - 2) { const t = lab(Y, "y"); ops.push({ k: "text", x: frame.x - 1.4 * f, y: cy + measure(t, pt) / 2, s: t, pt, color, angle: 90 }); }
      for (const [, cy] of crossings(pts, (p) => p[0] - (frame.x + frame.w))) if (cy > frame.y + 2 && cy < frame.y + frame.h - 2) { const t = lab(Y, "y"); ops.push({ k: "text", x: frame.x + frame.w + 1.4 * f + pt * PT * 0.72, y: cy + measure(t, pt) / 2, s: t, pt, color, angle: 90 }); }
    }
  }
  return ops;
}

// ---------- the legend ----------
type Row = { kind: "head"; text: string } | { kind: "sym"; layer: Layer; fill: string; text: string } | { kind: "ramp"; colors: string[]; hi: string; lo: string } | { kind: "more"; text: string };
const num = (v: number) => (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-GB") : Number(v.toPrecision(4)).toString());

/** What the legend lists for each visible layer, top layer first, as the Contents pane shows it. */
export function legendRows(layers: Layer[]): Row[] {
  const rows: Row[] = [];
  for (const l of [...layers].filter((x) => x.visible).sort((a, b) => b.position - a.position)) {
    if (l.geometry_type === "Raster") {
      if (!l.raster || l.raster.missing) continue;
      const rs = l.style as unknown as RasterStyle;
      rows.push({ kind: "head", text: l.name });
      if (rs.mode === "classes") {
        const cs = rs.categories.filter((c) => !c.hidden);
        cs.slice(0, 12).forEach((c) => rows.push({ kind: "sym", layer: { ...l, geometry_type: "Polygon" }, fill: c.color, text: c.label || String(c.value) }));
        if (cs.length > 12) rows.push({ kind: "more", text: `and ${cs.length - 12} more` });
      } else if (rs.mode === "rgb") rows.push({ kind: "more", text: "Red, green and blue bands" });
      else {
        const c = [...(RASTER_RAMPS[rs.ramp] ?? RASTER_RAMPS.Gray)];
        rows.push({ kind: "ramp", colors: rs.invert ? c : c.reverse(), hi: num(rs.max ?? 0), lo: num(rs.min ?? 0) });
      }
      continue;
    }
    const st = l.style;
    if (st.type === "categorized" && st.categories?.length) {
      rows.push({ kind: "head", text: st.field ? `${l.name}: ${st.field}` : l.name });
      st.categories.slice(0, 12).forEach((c) => rows.push({ kind: "sym", layer: l, fill: c.color, text: c.label || c.value || "(empty)" }));
      if (st.categories.length > 12) rows.push({ kind: "more", text: `and ${st.categories.length - 12} more` });
    } else if (st.type === "graduated" && st.classes?.length) {
      rows.push({ kind: "head", text: st.field ? `${l.name}: ${st.field}` : l.name });
      st.classes.forEach((c) => rows.push({ kind: "sym", layer: l, fill: c.color, text: `${num(c.min)} – ${num(c.max)}` }));
    } else rows.push({ kind: "sym", layer: l, fill: st.color, text: l.name });
  }
  return rows;
}

function symbolOps(l: Layer, fill: string, x: number, y: number, w: number, h: number, f: number): Op[] {
  const s = l.style;
  if (l.geometry_type === "Point") return [{ k: "circle", x: x + w / 2, y: y + h / 2, r: Math.min(h / 2, Math.max(0.9 * f, (s.size / 2) * 0.26)), fill, stroke: s.stroke, lw: 0.2 * f }];
  if (l.geometry_type === "LineString") return [{ k: "line", pts: [[x, y + h / 2], [x + w, y + h / 2]], stroke: fill, lw: Math.min(1.2, Math.max(0.35, s.width * 0.26)) * f }];
  return [{ k: "rect", x, y, w, h, fill, stroke: s.type === "single" ? s.stroke : "#333333", lw: 0.15 * f }];
}

/** Lays the legend out in columns inside `box`; what does not fit is counted at the end. */
export function legendOps(layers: Layer[], box: Rect, f: number, measure: Measure, title = true): { ops: Op[]; used: Rect } {
  const rows = legendRows(layers);
  const ops: Op[] = [];
  const pt = 7 * f, headPt = 7.5 * f, line = 4.6 * f, sw = 5 * f, sh = 3.2 * f, colGap = 5 * f;
  let x = box.x, y = box.y, colW = 0, maxX = box.x, maxY = box.y;
  if (title) { ops.push({ k: "text", x, y, s: "Legend", pt: 9 * f, bold: true, color: "#111827", base: "top" }); y += 6 * f; }
  const topY = y;
  const fit = (s: string, size: number, bold: boolean, width: number) => {
    if (measure(s, size, bold) <= width) return s;
    let t = s;
    while (t.length > 1 && measure(`${t}…`, size, bold) > width) t = t.slice(0, -1);
    return `${t}…`;
  };
  const colWidth = Math.min(box.w, 62 * f);
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    const rowH = (q?: Row) => (q?.kind === "ramp" ? 18 * f : line);
    const h = rowH(r) + (r.kind === "head" ? rowH(rows[i + 1]) : 0);      // a heading stays with its first entry
    if (y + h > box.y + box.h) {
      if (x + colW + colGap + colWidth * 0.6 <= box.x + box.w) { x += colW + colGap; y = topY; colW = 0; }
      else { ops.push({ k: "text", x, y: Math.min(y, box.y + box.h - line), s: `and ${rows.slice(i).filter((q) => q.kind !== "more").length} more entries`, pt: pt * 0.9, color: "#6b7280", base: "top" }); break; }
    }
    const room = Math.min(colWidth, box.x + box.w - x);
    if (r.kind === "head") {
      const s = fit(r.text, headPt, true, room);
      ops.push({ k: "text", x, y: y + 0.6 * f, s, pt: headPt, bold: true, color: "#111827", base: "top" });
      colW = Math.max(colW, measure(s, headPt, true));
    } else if (r.kind === "sym") {
      ops.push(...symbolOps(r.layer, r.fill, x, y + (line - sh) / 2 - 0.4 * f, sw, sh, f));
      const s = fit(r.text, pt, false, room - sw - 2 * f);
      ops.push({ k: "text", x: x + sw + 2 * f, y: y + line / 2 - 0.4 * f, s, pt, color: "#1f2937", base: "middle" });
      colW = Math.max(colW, sw + 2 * f + measure(s, pt, false));
    } else if (r.kind === "ramp") {
      ops.push({ k: "gradient", x, y: y + 1 * f, w: 4 * f, h: 15 * f, colors: r.colors, vertical: true });
      ops.push({ k: "rect", x, y: y + 1 * f, w: 4 * f, h: 15 * f, stroke: "#6b7280", lw: 0.15 * f });
      ops.push({ k: "text", x: x + 6 * f, y: y + 1 * f, s: r.hi, pt, color: "#1f2937", base: "top" });
      ops.push({ k: "text", x: x + 6 * f, y: y + 16 * f, s: r.lo, pt, color: "#1f2937", base: "bottom" });
      colW = Math.max(colW, 6 * f + Math.max(measure(r.hi, pt), measure(r.lo, pt)));
    } else {
      ops.push({ k: "text", x: x + sw + 2 * f, y: y + line / 2 - 0.4 * f, s: r.text, pt: pt * 0.9, color: "#6b7280", base: "middle" });
    }
    y += rowH(r);
    maxX = Math.max(maxX, x + colW); maxY = Math.max(maxY, y);
  }
  if (!rows.length) { ops.push({ k: "text", x, y, s: "No visible layers", pt, color: "#6b7280", base: "top" }); maxY = y + line; maxX = x + measure("No visible layers", pt); }
  return { ops, used: { x: box.x, y: box.y, w: maxX - box.x, h: maxY - box.y } };
}

// ---------- the whole sheet ----------
const wrap = (text: string, pt: number, width: number, measure: Measure) => {
  const out: string[] = [];
  for (const para of text.split(/\n/)) {
    let line = "";
    for (const word of para.split(/\s+/)) {
      const t = line ? `${line} ${word}` : word;
      if (line && measure(t, pt) > width) { out.push(line); line = word; } else line = t;
    }
    out.push(line);
  }
  return out;
};

/** Everything on the sheet except the map picture, which goes in the `map` operation's rectangle. */
export function composeSheet(s: LayoutSettings, ctx: LayoutContext): { sheet: Sheet; ops: Op[] } {
  const sheet = sheetFrames(s);
  const { w, h, f, frame, panel, header, footer } = sheet;
  const measure = ctx.measure ?? roughMeasure;
  const m = 10 * f;
  const ops: Op[] = [{ k: "rect", x: 0, y: 0, w, h, fill: "#ffffff" }];
  ops.push({ k: "map", ...frame });
  if (s.grid.on) ops.push(...gridOps(frame, s.center, s.scale, s, ctx, f));
  ops.push({ k: "rect", ...frame, stroke: "#1f2937", lw: 0.3 * f });
  ops.push({ k: "rect", x: m, y: m, w: w - 2 * m, h: h - 2 * m, stroke: "#1f2937", lw: 0.5 * f });     // neatline

  if (s.title) ops.push({ k: "text", x: header.x, y: header.y, s: s.title, pt: 18 * f, bold: true, color: "#111827", base: "top" });
  if (s.subtitle) ops.push({ k: "text", x: header.x, y: header.y + (s.title ? 8 * f : 0), s: s.subtitle, pt: 10 * f, color: "#4b5563", base: "top" });

  const blocks = (x: number, bottom: number, width: number) => {
    // north arrow and scale bar side by side, from the bottom of an area up
    const out: Op[] = [];
    let top = bottom;
    if (s.scalebar || s.north) {
      const sb = s.scalebar ? scaleBarOps(x + (s.north ? 10 * f : 0), bottom - 6 * f, width - (s.north ? 12 * f : 0), s.scale, f) : null;
      const nb = s.north ? northOps(x + 3.6 * f, bottom - 17 * f, f) : null;
      if (sb) out.push(...sb.ops);
      if (nb) out.push(...nb.ops);
      top = bottom - 19 * f;
    }
    return { ops: out, top };
  };

  if (panel) {
    ops.push({ k: "rect", ...panel, stroke: "#1f2937", lw: 0.25 * f });
    const inner = { x: panel.x + 4 * f, y: panel.y + 4 * f, w: panel.w - 8 * f, h: panel.h - 8 * f };
    if (s.orientation === "landscape") {
      const b = blocks(inner.x, inner.y + inner.h, inner.w);
      ops.push(...b.ops);
      let legendBottom = b.top - 2 * f;
      if (s.notes.trim()) {
        const lines = wrap(s.notes.trim(), 7 * f, inner.w, measure).slice(0, 12);
        const nh = lines.length * 3.4 * f;
        lines.forEach((t, i) => ops.push({ k: "text", x: inner.x, y: b.top - 2 * f - nh + i * 3.4 * f, s: t, pt: 7 * f, color: "#374151", base: "top" }));
        legendBottom = b.top - 4 * f - nh;
      }
      ops.push(...legendOps(ctx.layers, { ...inner, h: legendBottom - inner.y }, f, measure).ops);
    } else {
      const sideW = Math.min(70 * f, inner.w * 0.38);
      const right = { x: inner.x + inner.w - sideW, y: inner.y, w: sideW, h: inner.h };
      const b = blocks(right.x, right.y + right.h, right.w);
      ops.push(...b.ops);
      if (s.notes.trim()) {
        const lines = wrap(s.notes.trim(), 7 * f, right.w, measure).slice(0, Math.max(1, Math.floor((b.top - right.y - 2 * f) / (3.4 * f))));
        lines.forEach((t, i) => ops.push({ k: "text", x: right.x, y: right.y + i * 3.4 * f, s: t, pt: 7 * f, color: "#374151", base: "top" }));
      }
      ops.push(...legendOps(ctx.layers, { x: inner.x, y: inner.y, w: inner.w - sideW - 6 * f, h: inner.h }, f, measure).ops);
    }
  } else {
    // inside the map: the legend in a box at the bottom left, the scale bar and north arrow at the bottom right
    if (s.legend === "inside") {
      const box = { x: frame.x + 4 * f, y: frame.y + frame.h * 0.45, w: Math.min(70 * f, frame.w * 0.4), h: frame.h * 0.55 - 8 * f };
      const lo = legendOps(ctx.layers, { x: box.x + 3 * f, y: box.y + 3 * f, w: box.w - 6 * f, h: box.h - 6 * f }, f, measure);
      const used = { x: box.x, y: box.y + box.h - lo.used.h - 6 * f, w: lo.used.w + 6 * f, h: lo.used.h + 6 * f };
      const dy = used.y - box.y;
      ops.push({ k: "rect", ...used, fill: "#ffffff", stroke: "#9ca3af", lw: 0.2 * f });
      ops.push(...lo.ops.map((o) => shift(o, 0, dy)));
    }
    if (s.scalebar || s.north) {
      const bw = 58 * f, bh = 22 * f;
      const bx = frame.x + frame.w - bw - 4 * f, by = frame.y + frame.h - bh - 4 * f;
      ops.push({ k: "rect", x: bx, y: by, w: bw, h: bh, fill: "#ffffff", stroke: "#9ca3af", lw: 0.2 * f });
      ops.push(...blocks(bx + 3 * f, by + bh - 1 * f, bw - 6 * f).ops);
    }
    if (s.notes.trim()) {
      const lines = wrap(s.notes.trim(), 7 * f, frame.w * 0.5, measure).slice(0, 6);
      const nb = { x: frame.x + 4 * f, y: frame.y + 4 * f, w: Math.max(...lines.map((t) => measure(t, 7 * f))) + 6 * f, h: lines.length * 3.4 * f + 5 * f };
      ops.push({ k: "rect", ...nb, fill: "#ffffff", stroke: "#9ca3af", lw: 0.2 * f });
      lines.forEach((t, i) => ops.push({ k: "text", x: nb.x + 3 * f, y: nb.y + 2.5 * f + i * 3.4 * f, s: t, pt: 7 * f, color: "#374151", base: "top" }));
    }
  }

  if (footer) {
    const crsText = ctx.crs ? `${ctx.crs.name}${ctx.crs.epsg ? ` (EPSG:${ctx.crs.epsg})` : ""}` : "WGS 84";
    const leftText = [`Coordinate system: ${crsText}`, ctx.date && `Printed ${ctx.date}`, ctx.author && `by ${ctx.author}${ctx.org ? `, ${ctx.org}` : ""}`].filter(Boolean).join(" · ");
    const rightText = [ctx.credits, "Made with GeoEstate PolyGIS"].filter(Boolean).join(" · ");
    const pt = 6 * f;
    const rw = Math.min(measure(rightText, pt), footer.w * 0.55);
    ops.push({ k: "text", x: footer.x, y: footer.y + footer.h / 2, s: fitText(leftText, pt, footer.w - rw - 6 * f, measure), pt, color: "#4b5563", base: "middle" });
    ops.push({ k: "text", x: footer.x + footer.w, y: footer.y + footer.h / 2, s: fitText(rightText, pt, footer.w * 0.55, measure), pt, color: "#4b5563", base: "middle", align: "right" });
  }
  return { sheet, ops };
}

function fitText(s: string, pt: number, width: number, measure: Measure) {
  if (measure(s, pt) <= width) return s;
  let t = s;
  while (t.length > 1 && measure(`${t}…`, pt) > width) t = t.slice(0, -1);
  return `${t}…`;
}
function shift(o: Op, dx: number, dy: number): Op {
  if (o.k === "line" || o.k === "poly") return { ...o, pts: o.pts.map(([x, y]) => [x + dx, y + dy] as XY) };
  return { ...o, x: o.x + dx, y: o.y + dy } as Op;
}

/** Text size in millimetres, for the renderers. */
export const ptToMm = (pt: number) => pt * PT;
