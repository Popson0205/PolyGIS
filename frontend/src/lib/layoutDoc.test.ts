import { describe, expect, it } from "vitest";
import { DEFAULTS, type LayoutSettings, type Op } from "./layout";
import {
  composeDoc, docFrom, elementOps, makeElement, mapFor, resizeBox, snap, snapLines, templateDoc, type LayoutDoc, type MapEl, type ScaleBarEl, type TextEl,
} from "./layoutDoc";

const S: LayoutSettings = { ...DEFAULTS, title: "Bosso parcels", center: [6.53, 9.65], scale: 2500 };
const empty = (): LayoutDoc => ({ v: 2, page: "A4", orientation: "landscape", dpi: 300, elements: [] });
const texts = (ops: Op[]) => ops.filter((o): o is Extract<Op, { k: "text" }> => o.k === "text").map((o) => o.s);

describe("templates", () => {
  it("lays the classic sheet out as elements to rearrange", () => {
    const d = templateDoc(S);
    expect(d.elements.map((e) => e.name)).toEqual(["Neatline", "Map frame", "Title", "Legend panel", "North arrow", "Scale bar", "Legend", "Footer", "Credits"]);
    const m = d.elements.find((e) => e.kind === "map") as MapEl;
    expect(m.center).toEqual([6.53, 9.65]); expect(m.scale).toBe(2500);
    for (const e of d.elements) { expect(e.x).toBeGreaterThanOrEqual(0); expect(e.x + e.w).toBeLessThanOrEqual(297.01); expect(e.y + e.h).toBeLessThanOrEqual(210.01); }
    expect(new Set(d.elements.map((e) => e.id)).size).toBe(d.elements.length);
  });
  it("opens layouts saved before elements, and keeps new ones as they are", () => {
    const old = docFrom({ page: "A3", orientation: "portrait", title: "Old sheet", legend: "inside", center: [7, 9], scale: 5000 }, { center: [0, 0], scale: 1, title: "x" });
    expect(old.page).toBe("A3"); expect(old.orientation).toBe("portrait");
    expect(old.elements.find((e) => e.name === "Title")).toMatchObject({ text: "Old sheet" });
    expect(old.elements.find((e) => e.kind === "map")).toMatchObject({ center: [7, 9], scale: 5000 });
    expect(docFrom(old, { center: [0, 0], scale: 1, title: "x" }).elements).toEqual(old.elements);
  });
});

describe("elements", () => {
  it("draws each map frame as its own picture, and the scale bar follows its map", () => {
    const d = empty();
    const a = makeElement(d, "map", { x: 10, y: 10, w: 150, h: 100 }, { center: [6.53, 9.65], scale: 2500 }) as MapEl; d.elements.push(a);
    const b = { ...makeElement(d, "map", { x: 170, y: 10, w: 100, h: 60 }, { center: [6.5, 9.6], scale: 50000 }), name: "Inset" } as MapEl; d.elements.push(b);
    const sb = { ...makeElement(d, "scalebar", { x: 10, y: 120, w: 60, h: 10 }), map: b.id } as ScaleBarEl; d.elements.push(sb);
    const ops = composeDoc(d, { layers: [] }).ops;
    expect(ops.filter((o) => o.k === "map").map((o) => (o as { id?: string }).id)).toEqual([a.id, b.id]);
    expect(mapFor(d, sb.map)?.id).toBe(b.id);
    expect(texts(ops)).toContain("Scale 1:50,000");
    // the bar is a round length that fits the box, drawn at the map's scale
    const bars = ops.filter((o): o is Extract<Op, { k: "rect" }> => o.k === "rect" && o.y >= 120 && o.y < 130);
    const mm = bars.reduce((s, r) => s + r.w, 0);
    expect(mm).toBeLessThanOrEqual(60); expect(mm).toBeGreaterThan(20);
    expect([1, 2, 2.5, 5].some((n) => Math.abs(((mm * 50000) / 1000) / (n * 10 ** Math.floor(Math.log10((mm * 50000) / 1000))) - 1) < 1e-6)).toBe(true);
  });
  it("fills dynamic text, wraps it in its box and cuts what does not fit", () => {
    const d = empty();
    d.elements.push(makeElement(d, "map", { x: 0, y: 0, w: 100, h: 100 }, { center: [6.5, 9.6], scale: 10000 }));
    const t = { ...makeElement(d, "text", { x: 10, y: 10, w: 60, h: 20 }), text: "{project}, {scale} · by {author}, {org}", size: 10 } as TextEl;
    expect(texts(elementOps(t, d, { layers: [], project: "Bosso", author: "Amina", org: "GeoEstate" })).join(" ")).toBe("Bosso, 1:10,000 · by Amina, GeoEstate");
    const long = { ...t, text: "word ".repeat(200), h: 9 };
    const lines = texts(elementOps(long, d, { layers: [] }));
    expect(lines.length).toBe(2); expect(lines[1].endsWith("…")).toBe(true);
    // a credit left empty leaves no stray separator
    expect(texts(elementOps({ ...t, text: "{credits} · Made with GeoEstate PolyGIS" }, d, { layers: [] }))).toEqual(["Made with GeoEstate PolyGIS"]);
  });
  it("hidden elements are not drawn", () => {
    const d = templateDoc(S);
    const n = composeDoc(d, { layers: [] }).ops.length;
    d.elements = d.elements.map((e) => (e.name === "Title" ? { ...e, visible: false } : e));
    expect(composeDoc(d, { layers: [] }).ops.length).toBe(n - 1);
  });
});

describe("moving and resizing", () => {
  it("resizes from any handle, keeps the shape with Shift and never turns a box inside out", () => {
    const b = { x: 10, y: 10, w: 40, h: 20 };
    expect(resizeBox(b, "se", 10, 5)).toEqual({ x: 10, y: 10, w: 50, h: 25 });
    expect(resizeBox(b, "nw", 5, 5)).toEqual({ x: 15, y: 15, w: 35, h: 15 });
    expect(resizeBox(b, "e", 10, 99)).toEqual({ x: 10, y: 10, w: 50, h: 20 });
    const r = resizeBox(b, "se", 20, 0, true); expect(r.w / r.h).toBeCloseTo(2);
    const tiny = resizeBox(b, "w", 100, 0); expect(tiny.w).toBe(3); expect(tiny.x).toBe(47);
  });
  it("snaps edges to the page and to other elements", () => {
    const d = templateDoc(S);
    const lines = snapLines(d, new Set());
    expect(lines.xs).toContain(297 / 2);
    expect(snap([148], lines.xs, 1.5)).toEqual({ d: 0.5, at: 148.5 });
    expect(snap([100.2], [50, 200], 1.5).at).toBeNull();
  });
});
