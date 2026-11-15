import { describe, expect, it } from "vitest";
import { bearingText, convergence, inverse, toMils } from "./geodesy";

describe("geodesy", () => {
  it("matches pyproj's geodesics (Lagos to Abuja)", () => {
    // pyproj Geod(ellps="WGS84").inv(3.3841, 6.4550, 7.4664, 9.0765)
    const r = inverse(3.3841, 6.455, 7.4664, 9.0765);
    expect(Math.abs(r.distance - 535_524.7557)).toBeLessThan(0.001);
    expect(Math.abs(r.bearing - 56.962008324)).toBeLessThan(1e-7);
    expect(Math.abs(r.back - (360 - 122.486002022))).toBeLessThan(1e-7);
  });
  it("is symmetric and knows the back bearing", () => {
    const a = inverse(0, 0, 1, 1), b = inverse(1, 1, 0, 0);
    expect(a.distance).toBeCloseTo(b.distance, 6);
    expect(a.back).toBeCloseTo(b.bearing, 6);
  });
  it("converts to mils and grid north", () => {
    expect(toMils(90)).toBe(1600);
    expect(toMils(-90)).toBe(4800);
    expect(convergence(9, 0)).toBeCloseTo(0, 6);                // on the central meridian at the equator
    expect(convergence(10, 45)).toBeGreaterThan(0.7);           // east of the central meridian (9° E) in the north
  });
  it("reads out a clicked line", () => {
    expect(bearingText([[3.3841, 6.455], [7.4664, 9.0765]])).toMatch(/^535\.525 km .* bearing 56\.96° \(1013 mils\)/);
  });
});
