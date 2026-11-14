import { describe, expect, it } from "vitest";
import { labelLayer, labelText } from "./mapstyle";
import type { Labels } from "./types";

const base: Labels = { field: "name", size: 12, color: "#111111", halo: "#ffffff" };

describe("label text", () => {
  it("is the field, or an expression with placeholders", () => {
    expect(labelText(base)).toEqual(["to-string", ["coalesce", ["get", "name"], ""]]);
    expect(labelText({ ...base, expression: "{name} ({pop})" })).toEqual(["concat", ["to-string", ["coalesce", ["get", "name"], ""]], " (",
      ["to-string", ["coalesce", ["get", "pop"], ""]], ")"]);
    expect(labelText({ ...base, case: "upper" })[0]).toBe("upcase");
  });
});

describe("label layer", () => {
  const pt = { geometry_type: "Point" as const, visible: true };
  it("keeps the old look for labels saved before", () => {
    const l = labelLayer(pt, base);
    expect(l.layout["text-font"]).toEqual(["Noto Sans Regular"]);
    expect(l.paint["text-halo-width"]).toBe(1.4);
    expect(l.layout["text-variable-anchor"]).toEqual(["top", "bottom", "right", "left"]);
  });
  it("puts a label above and to the right of its point when asked", () => {
    const l = labelLayer(pt, { ...base, anchor: "top-right", offset: 12 });
    expect(l.layout["text-anchor"]).toBe("bottom-left");
    const [dx, dy] = l.layout["text-offset"] as [number, number];
    expect(dx).toBeGreaterThan(0);
    expect(dy).toBeLessThan(0);
  });
  it("runs along lines or sits flat at their middle, and keeps its zoom range and font", () => {
    const along = labelLayer({ geometry_type: "LineString", visible: true }, { ...base, font: "bold", min_zoom: 10, max_zoom: 16 });
    expect(along.layout["symbol-placement"]).toBe("line");
    expect(along.layout["text-font"]).toEqual(["Noto Sans Bold"]);
    expect([along.minzoom, along.maxzoom]).toEqual([10, 16]);
    expect(labelLayer({ geometry_type: "LineString", visible: true }, { ...base, line_placement: "horizontal" }).layout["symbol-placement"]).toBe("line-center");
  });
  it("turns with a field", () => {
    expect(labelLayer(pt, { ...base, rotation_field: "heading", rotation: 90 }).layout["text-rotate"]).toEqual(["+", ["to-number", ["coalesce", ["get", "heading"], 0], 0], 90]);
  });
});
