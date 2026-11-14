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
