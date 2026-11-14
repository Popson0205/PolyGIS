import { describe, expect, it } from "vitest";
import type { GeorefPoint } from "../lib/types";
import { suggest } from "../lib/georef";

const pt = (id: string, col: number, row: number, on = true): GeorefPoint => ({ id, col, row, lng: 4.5 + col * 0.001, lat: 7.5 - row * 0.002, on });

describe("suggested map position", () => {
  it("needs three points in use", () => {
    expect(suggest([pt("a", 0, 0), pt("b", 100, 0)], 50, 50)).toBeNull();
    expect(suggest([pt("a", 0, 0), pt("b", 100, 0), pt("c", 0, 100, false)], 50, 50)).toBeNull();
  });
  it("follows the image's placement so far", () => {
    const g = suggest([pt("a", 0, 0), pt("b", 100, 0), pt("c", 0, 100), pt("d", 100, 100)], 250, 40)!;
    expect(g[0]).toBeCloseTo(4.75, 9);
    expect(g[1]).toBeCloseTo(7.42, 9);
  });
  it("gives nothing for points on a line", () => {
    expect(suggest([pt("a", 0, 0), pt("b", 10, 10), pt("c", 20, 20)], 5, 50)).toBeNull();
  });
});

import { fromDms, parsePoints, pointsText, toDms } from "./georef";

describe("degrees, minutes and seconds", () => {
  it("reads them, west and south negative", () => {
    expect(fromDms(4, 33, 36, "E")).toBeCloseTo(4.56, 9);
    expect(fromDms(7, 30, 0, "S")).toBeCloseTo(-7.5, 9);
    expect(() => fromDms(4, 61, 0, "E")).toThrow(/below 60/);
  });
  it("writes them and back again", () => {
    const d = toDms(-4.5601234, "lng");
    expect(d.hemi).toBe("W");
    expect(fromDms(d.deg, d.min, d.sec, d.hemi)).toBeCloseTo(-4.5601234, 7);
    expect(toDms(7.9999999999, "lat")).toEqual({ deg: 8, min: 0, sec: 0, hemi: "N" });
  });
});

describe("control point files", () => {
  it("reads ArcGIS text, with the image's y negative downwards", () => {
    const p = parsePoints("100\t-200\t660000.5\t830000\n300 -50 660300 830150\n");
    expect(p).toEqual([{ col: 100, row: 200, x: 660000.5, y: 830000 }, { col: 300, row: 50, x: 660300, y: 830150 }]);
  });
  it("reads a CSV with a header", () => {
    const p = parsePoints("id,col,row,easting,northing\n1,10,20,500,600\n");
    expect(p).toEqual([{ col: 10, row: 20, x: 500, y: 600 }]);
  });
  it("writes what it reads", () => {
    const rows = [{ col: 1.5, row: 2, x: 3, y: 4 }];
    expect(parsePoints(pointsText(rows))).toEqual(rows);
  });
  it("says what is wrong", () => {
    expect(() => parsePoints("1 2 3\n")).toThrow(/four numbers/);
  });
});
