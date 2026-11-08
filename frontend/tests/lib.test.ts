import proj4 from "proj4";
import { describe, expect, it } from "vitest";
import { formatCoords, metres, squareMetres } from "../src/lib/coords";
import { breaks, colour, mapLayers, rampColours } from "../src/lib/mapstyle";
import { snapPoint } from "../src/lib/snap";
import type { Layer } from "../src/lib/types";

const layer = (over: Partial<Layer> = {}): Layer => ({
  id: "L1", project_id: "P", name: "Plots", geometry_type: "Polygon", fields: [{ name: "use", type: "string" }, { name: "area", type: "number" }],
  style: { type: "single", color: "#2f6db5", stroke: "#ffffff", width: 1, size: 6, opacity: 0.4 }, labels: null, visible: true, position: 0,
  source: {}, feature_count: 3, bbox: null, version: 1, updated_at: "", ...over,
});

describe("styles", () => {
  it("one colour, categories, graduated classes", () => {
    expect(colour(layer())).toBe("#2f6db5");
    const cat = colour(layer({ style: { ...layer().style, type: "categorized", field: "use", categories: [{ value: "farm", color: "#00ff00" }, { value: "home", color: "#ff0000" }] } }));
    expect(cat).toEqual(["match", ["to-string", ["coalesce", ["get", "use"], ""]], "farm", "#00ff00", "home", "#ff0000", "#b8bfcc"]);
    const grad = colour(layer({ style: { ...layer().style, type: "graduated", field: "area", classes: [{ min: 0, max: 10, color: "#111111" }, { min: 10, max: 20, color: "#222222" }] } }));
    expect(JSON.stringify(grad)).toContain('["step",["get","area"],"#111111",10,"#222222"]');
  });
  it("a polygon layer has fill, outline and selection; labels when set; hidden while reshaping", () => {
    const ls = mapLayers(layer({ labels: { field: "use", size: 12, color: "#000000", halo: "#ffffff" } }), [5], [7]);
    expect(ls.map((l) => l.type)).toEqual(["fill", "line", "fill", "line", "symbol"]);
    expect(JSON.stringify(ls[0])).toContain("[\"literal\",[7]]");
    expect(JSON.stringify(ls[2])).toContain("[\"literal\",[5]]");
    expect(mapLayers(layer({ geometry_type: "Point" }), []).map((l) => l.type)).toEqual(["circle", "circle"]);
  });
  it("class breaks and colour ramps", () => {
    expect(breaks("equal", 0, 100, 4)).toEqual([0, 25, 50, 75, 100]);
    expect(breaks("quantile", 0, 100, 2, [0, 10, 100])).toEqual([0, 10, 100]);
    const r = rampColours("Greens", 3);
    expect(r).toHaveLength(3);
    expect(r[0]).toBe("#edf8e9");
    expect(r[2]).toBe("#006d2c");
  });
});

describe("coordinates", () => {
  it("reads out Minna / UTM 32N with the datum shift, and degrees for WGS 84", () => {
    proj4.defs("EPSG:26332", "+proj=utm +zone=32 +a=6378249.145 +rf=293.465 +units=m +towgs84=-92,-93,122 +no_defs +type=crs");
    // the server (pyproj) puts this point at E 331529.157, N 1003545.973
    expect(formatCoords({ epsg: 26332, name: "Minna / UTM zone 32N", proj4: "", geographic: false, unit: "metre" }, 7.4664, 9.0765))
      .toMatch(/^E 331,529\.1\d\d m {2}N 1,003,545\.9\d\d m$/);
    expect(formatCoords(undefined, 7.4664, 9.0765)).toBe("7.466400° E, 9.076500° N");
    expect(metres(1234.5)).toBe("1.235 km");
    expect(squareMetres(25000)).toMatch(/^2\.5 ha/);
  });
});

describe("snapping", () => {
  const sq: GeoJSON.Feature = { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]] } };
  const project = (lng: number, lat: number) => ({ x: lng * 10, y: lat * 10 });
  const unproject = (x: number, y: number) => ({ lng: x / 10, lat: y / 10 });
  it("snaps to a vertex first, else to the nearest point on an edge, within the distance", () => {
    expect(snapPoint(98, 103, [sq], project, unproject, 10)).toEqual([10, 10]);
    expect(snapPoint(50, 4, [sq], project, unproject, 10)).toEqual([5, 0]);
    expect(snapPoint(50, 50, [sq], project, unproject, 10)).toBeNull();
  });
});
