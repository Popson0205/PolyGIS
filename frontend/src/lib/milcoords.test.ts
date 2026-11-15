import { describe, expect, it } from "vitest";
import { NOTATIONS, fromMgrs, parseCoordinate, toGars, toGeoref, toMgrs, writeCoordinate } from "./milcoords";

describe("military coordinates (the same values the server is tested with)", () => {
  it.each([
    [7.4664, 9.0765, "32PLR3145203665"], [-77.0352, 38.8895, "18SUJ2348606483"], [151.2153, -33.8568, "56HLH3490052288"],
    [15.6267, 78.2232, "33XWG1427883355"], [5.32, 60.39, "32VKN9723000510"],
  ])("MGRS of %s, %s", (lng, lat, ref) => {
    expect(toMgrs(lng, lat)).toBe(ref);
    const [x, y] = fromMgrs(ref);
    expect(Math.abs(x - lng)).toBeLessThan(2e-5);
    expect(Math.abs(y - lat)).toBeLessThan(2e-5);
  });
  it("writes every notation as the server does", () => {
    const got = Object.fromEntries(NOTATIONS.map((n) => [n, writeCoordinate(n, 7.4664, 9.0765)]));
    expect(got).toEqual({ dd: "9.076500° N 7.466400° E", ddm: "09° 04.590' N 007° 27.984' E", dms: "09° 04' 35.4\" N 007° 27' 59.0\" E",
      utm: "32N 331452 1003666", mgrs: "32PLR3145203665", usng: "32P LR 31452 03665", gars: "375JG49", georef: "NGHK2704" });
    expect(toGars(-77.0352, 38.8895)).toBe("206LT26");
    expect(toGeoref(-77.0352, 38.8895)).toBe("GJNJ5753");
  });
  it.each(["9.0765, 7.4664", "7.4664 E 9.0765 N", "N 9 4 35.4 E 7 27 59", "9°04'35.4\"N 7°27'59.0\"E", "32P LR 31452 03665", "32plr3145203665",
    "32N 331452 1003666", "32P 331452 1003666"])("reads %s", (t) => {
    const [x, y] = parseCoordinate(t);
    expect(Math.abs(x - 7.4664)).toBeLessThan(1e-3);
    expect(Math.abs(y - 9.0765)).toBeLessThan(1e-3);
  });
  it.each(["32P ZZ 1 2", "hello", "32P LR 3145 036", "95.1, 200", ""])("refuses %s", (t) => {
    expect(() => parseCoordinate(t)).toThrow();
  });
  it("has no MGRS at the poles", () => { expect(writeCoordinate("mgrs", 0, 85)).toBeNull(); });
});
