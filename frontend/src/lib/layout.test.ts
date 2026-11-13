import proj4 from "proj4";
import { describe, expect, it } from "vitest";
import {
  DEFAULTS, composeSheet, frameProjection, gridOps, legendRows, mapPixels, mercPerMm, niceInterval, pageSize, roundScale, scaleBar, scaleForZoom, sheetFrames,
  zoomForScale, type LayoutSettings, type Op,
} from "./layout";
import { pdfSafe } from "./layoutRender";
import type { Layer } from "./types";

const S: LayoutSettings = { ...DEFAULTS, title: "Bosso parcels", center: [6.53, 9.65], scale: 2500 };
const layer = (o: Partial<Layer>): Layer => ({
  id: "l1", project_id: "p", name: "Parcels", geometry_type: "Polygon", fields: [], labels: null, visible: true, position: 1, source: {},
  feature_count: 10, bbox: null, version: 1, updated_at: "",
  style: { type: "single", color: "#f4c542", stroke: "#333333", width: 1, size: 6, opacity: 0.8 }, ...o,
});

describe("scale and zoom", () => {
  it("prints the scale asked for: one mm on paper is scale/1000 m on the ground at the centre", () => {
    const frame = { x: 0, y: 0, w: 100, h: 100 };
    const p = frameProjection(frame, [6.53, 9.65], 2500);
    // 40 mm on the page east of the centre is 100 m on the ground
    const [lng] = p.toLngLat(90, 50);
    const metres = ((lng - 6.53) * Math.PI / 180) * 6378137 * Math.cos((9.65 * Math.PI) / 180);
    expect(metres).toBeCloseTo(100, 1);
    expect(p.toPage(6.53, 9.65)).toEqual([50, 50]);
    const [x, y] = p.toPage(...p.toLngLat(12.5, 77.25));
    expect(x).toBeCloseTo(12.5, 6); expect(y).toBeCloseTo(77.25, 6);
  });
  it("zoom and scale agree both ways, and scales round as maps print them", () => {
    expect(scaleForZoom(zoomForScale(2500, 9.65), 9.65)).toBeCloseTo(2500, 3);
    expect(mercPerMm(1000, 0)).toBeCloseTo(1, 9);
    expect(roundScale(2380)).toBe(2500); expect(roundScale(9100)).toBe(10000); expect(roundScale(1180)).toBe(1250);
  });
  it("keeps the map picture within what a browser can draw", () => {
    const a0 = sheetFrames({ ...S, page: "A0" }).frame;
    const p = mapPixels(a0, 300);
    expect(p.dpi).toBeLessThan(300);
    expect(Math.max(p.w, p.h)).toBeLessThanOrEqual(8192);
    expect(p.w * p.h).toBeLessThanOrEqual(48e6);
    const a4 = mapPixels(sheetFrames(S).frame, 300);
    expect(a4.dpi).toBe(300);
    expect(a4.ratio).toBeCloseTo(300 / 96, 6);
  });
});

describe("the sheet", () => {
  it("lays out landscape with a side column and portrait with a band below, inside the margins", () => {
    for (const orientation of ["landscape", "portrait"] as const) {
      const sh = sheetFrames({ ...S, orientation });
      const [w, h] = pageSize({ page: "A4", orientation });
      expect(sh.panel).not.toBeNull();
      for (const r of [sh.frame, sh.panel!]) {
        expect(r.x).toBeGreaterThanOrEqual(10); expect(r.y).toBeGreaterThanOrEqual(10);
        expect(r.x + r.w).toBeLessThanOrEqual(w - 10 + 1e-9); expect(r.y + r.h).toBeLessThanOrEqual(h - 10 + 1e-9);
      }
      if (orientation === "landscape") expect(sh.panel!.x).toBeGreaterThan(sh.frame.x + sh.frame.w);
      else expect(sh.panel!.y).toBeGreaterThan(sh.frame.y + sh.frame.h);
    }
    const none = sheetFrames({ ...S, legend: "none" });
    expect(none.panel).toBeNull();
    expect(none.frame.w).toBeGreaterThan(sheetFrames(S).frame.w);
  });
  it("text grows with the page", () => {
    const title = (page: LayoutSettings["page"]) => (composeSheet({ ...S, page }, { layers: [] }).ops.find((o) => o.k === "text" && o.s === "Bosso parcels") as Extract<Op, { k: "text" }>).pt;
    expect(title("A4")).toBe(18);
    expect(title("A3")).toBeGreaterThan(18);
    expect(title("A0")).toBeGreaterThan(title("A1"));
  });
  it("has the map, frame, neatline, title, legend, scale bar, north arrow and footer", () => {
    const { ops } = composeSheet(S, { layers: [layer({})], crs: { epsg: 26332, name: "Minna / UTM zone 32N", proj4: "", geographic: false, unit: "m" }, author: "Ada", date: "13 November 2026" });
    const texts = ops.filter((o): o is Extract<Op, { k: "text" }> => o.k === "text").map((o) => o.s);
    expect(ops.filter((o) => o.k === "map")).toHaveLength(1);
    expect(texts).toEqual(expect.arrayContaining(["Bosso parcels", "Legend", "Parcels", "N", "Scale 1:2,500"]));
    expect(texts.some((t) => t.startsWith("Coordinate system: Minna / UTM zone 32N (EPSG:26332) · Printed 13 Nov"))).toBe(true);
  });
});

describe("scale bar", () => {
  it("is a round length that fits", () => {
    const b = scaleBar(2500, 50);           // 50 mm at 1:2,500 is 125 m
    expect(b.metres).toBe(100); expect(b.mm).toBeCloseTo(40, 6); expect(b.labels).toEqual(["0", "50", "100"]); expect(b.unit).toBe("m");
    const k = scaleBar(250000, 50);
    expect(k.unit).toBe("km"); expect(k.labels[2]).toBe("10");
  });
});

describe("grid", () => {
  it("draws Minna / UTM 32N lines at round eastings and northings, labelled where they leave the frame", () => {
    proj4.defs("EPSG:26332", "+proj=utm +zone=32 +a=6378249.145 +rf=293.465 +towgs84=-92,-93,122,0,0,0,0 +units=m +no_defs");
    const toCrs = (lng: number, lat: number) => proj4("EPSG:4326", "EPSG:26332", [lng, lat]) as [number, number];
    const s = { ...S, grid: { on: true, interval: 100, labels: true } };
    const frame = sheetFrames(s).frame;
    const ops = gridOps(frame, s.center, s.scale, s, { layers: [], toCrs, crs: { epsg: 26332, name: "Minna", proj4: "", geographic: false, unit: "m" } }, 1);
    const lines = ops.filter((o): o is Extract<Op, { k: "line" }> => o.k === "line");
    expect(lines.length).toBeGreaterThan(4);
    // every point of a vertical line has the same easting, a multiple of 100 m
    const p = frameProjection(frame, s.center, s.scale);
    const first = lines[0].pts.map(([x, y]) => toCrs(...p.toLngLat(x, y)));
    const e = first[0][0];
    expect(Math.abs(e / 100 - Math.round(e / 100))).toBeLessThan(1e-4);
    for (const q of first) expect(q[0]).toBeCloseTo(e, 2);
    const labels = ops.filter((o): o is Extract<Op, { k: "text" }> => o.k === "text").map((o) => o.s);
    expect(labels.every((t) => /^\d{1,3}( \d{3})*$/.test(t))).toBe(true);
    expect(ops.filter((o) => o.k === "text" && o.angle)).not.toHaveLength(0);
  });
  it("labels a geographic grid in degrees and minutes", () => {
    const s = { ...S, scale: 250000, grid: { on: true, interval: null, labels: true } };
    const ops = gridOps(sheetFrames(s).frame, s.center, s.scale, s, { layers: [] }, 1);
    const labels = ops.filter((o): o is Extract<Op, { k: "text" }> => o.k === "text").map((o) => o.s);
    expect(labels.length).toBeGreaterThan(2);
    expect(labels.every((t) => /^\d+°\d\d'(\d\d")?[NSEW]$/.test(t))).toBe(true);
    expect(niceInterval(4300)).toBe(1000);
  });
});

describe("legend", () => {
  it("lists visible layers top first, with categories and classes, and skips hidden ones", () => {
    const rows = legendRows([
      layer({ id: "a", name: "Parcels", position: 1, style: { type: "categorized", field: "use", color: "#000", stroke: "#000", width: 1, size: 6, opacity: 1,
        categories: [{ value: "Residential", color: "#f4c542" }, { value: "Commercial", color: "#e66d4f" }] } }),
      layer({ id: "b", name: "Boreholes", geometry_type: "Point", position: 3 }),
      layer({ id: "c", name: "Hidden", position: 2, visible: false }),
    ]);
    expect(rows.map((r) => ("text" in r ? r.text : r.kind))).toEqual(["Boreholes", "Parcels: use", "Residential", "Commercial"]);
  });
});

describe("PDF text", () => {
  it("keeps Western letters and spells others without their marks", () => {
    expect(pdfSafe("Área 1,250 m² – Ọ̀yọ́ · ©")).toBe("Área 1,250 m² – Oyo · ©");
    expect(pdfSafe("北京")).toBe("??");
  });
});
