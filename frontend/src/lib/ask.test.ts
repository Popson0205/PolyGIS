import { describe, expect, it } from "vitest";
import { ask, coordsIn, fillTool, layersIn, numbersIn, type Command } from "./ask";
import type { Layer, Tool } from "./types";

const L = (id: string, name: string, geometry_type = "LineString") => ({ id, name, geometry_type } as unknown as Layer);
const layers = [L("r", "Roads"), L("a", "Areas of interest", "Polygon"), L("c", "Checkpoints", "Point"), L("img", "Sentinel 2", "Raster")];
const tools: Tool[] = [
  { id: "buffer", name: "Buffer", group: "Proximity", about: "Areas within a distance of each feature.",
    params: [{ name: "input", label: "Input layer", type: "layer" }, { name: "distance", label: "Distance", type: "number", unit: "m" }, { name: "dissolve", label: "Merge", type: "bool" }] },
  { id: "clip", name: "Clip", group: "Overlay", about: "The parts of the input inside the clip polygons.",
    params: [{ name: "input", label: "Input layer", type: "layer" }, { name: "clip", label: "Clip polygons", type: "layer", geometry: ["Polygon"] }] },
  { id: "hot_spots", name: "Hot spots", group: "Statistics", about: "Where things cluster.", params: [{ name: "input", label: "Input layer", type: "layer" }] },
];
const commands: Command[] = [{ id: "c1", label: "Range rings", mode: "military", modeName: "Defence", disabled: false }, { id: "c2", label: "Fit all", mode: "map", modeName: "Explore", disabled: false }];

describe("ask", () => {
  it("reads layers, distances and places", () => {
    expect(layersIn("clip roads to areas of interest", layers).map((l) => l.id)).toEqual(["r", "a"]);
    expect(numbersIn("buffer by 2 km and 3").metres).toEqual([2000]);
    expect(numbersIn("1.5 miles").metres[0]).toBeCloseTo(2414.016);
    expect(coordsIn("go to 6.45, 3.39")).toEqual({ lat: 6.45, lng: 3.39 });
    expect(coordsIn("buffer roads by 500 m")).toBeNull();
  });
  it("fills a tool from the request", () => {
    expect(fillTool(tools[0], "buffer roads by 500 m", layers).preset).toEqual({ input: "r", distance: 500 });
    expect(fillTool(tools[0], "buffer checkpoints 2km", layers).preset).toEqual({ input: "c", distance: 2000 });
    expect(fillTool(tools[1], "clip roads to areas of interest", layers).preset).toEqual({ input: "r", clip: "a" });
  });
  it("puts the meant thing first", () => {
    const ctx = { tools, layers, commands };
    const b = ask("buffer roads by 500 m", ctx)[0];
    expect(b.kind === "tool" && b.tool.id === "buffer" && b.preset).toEqual({ input: "r", distance: 500 });
    expect(ask("zoom to checkpoints", ctx)[0]).toMatchObject({ kind: "layer", action: "zoom" });
    expect(ask("roads table", ctx)[0]).toMatchObject({ kind: "layer", action: "table" });
    expect(ask("range", ctx)[0]).toMatchObject({ kind: "command", id: "c1" });
    expect(ask("hot", ctx)[0]).toMatchObject({ kind: "tool" });
    expect(ask("6.45 3.39", ctx)[0]).toMatchObject({ kind: "goto" });
    expect(ask("zzz", ctx)).toEqual([]);
    expect(ask("zoom to areas", ctx)[0]).toMatchObject({ kind: "layer", action: "zoom", layer: { id: "a" } });
    const withTool = { ...ctx, tools: [...tools, { id: "range_rings", name: "Range rings", group: "Military", about: "Rings.", params: [] }] };
    expect(ask("range rings", withTool)[0]).toMatchObject({ kind: "command", id: "c1" });
  });
});
