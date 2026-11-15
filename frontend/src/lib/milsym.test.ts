import { describe, expect, it } from "vitest";
import { isValidSidc, makeSidc, readSidc, SYMBOLS } from "./milsym";

describe("military symbol codes", () => {
  it("builds 2525C codes", () => {
    expect(makeSidc({ affiliation: "F", dim: "G", status: "P", fn: "UCI", echelon: "D" })).toBe("SFGPUCI----D---");
    expect(makeSidc({ affiliation: "H", dim: "G", status: "A", fn: "UCA", modifier: "A", echelon: "F" })).toBe("SHGAUCA---AF---");
    expect(makeSidc({ affiliation: "H", dim: "A", status: "P", fn: "MFF", echelon: "D" })).toBe("SHAPMFF--------");   // echelons are for land units
  });
  it("reads a code back", () => {
    expect(readSidc("SHGAUCA---AF---")).toEqual({ affiliation: "H", dim: "G", status: "A", fn: "UCA---", modifier: "A", echelon: "F" });
  });
  it("knows every symbol in the picker", () => {
    for (const g of SYMBOLS) for (const i of g.items) {
      expect(isValidSidc(makeSidc({ affiliation: "F", dim: i.dim, status: "P", fn: i.fn })), `${g.group}: ${i.name}`).toBe(true);
    }
  });
  it("refuses nonsense", () => {
    expect(isValidSidc("hello")).toBe(false);
    expect(isValidSidc("")).toBe(false);
  });
});
