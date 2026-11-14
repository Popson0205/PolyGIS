import { describe, expect, it } from "vitest";
import { compareRows, numberList } from "./scenarios";
import type { Scenario } from "./types";

const sc = (name: string, kind: Scenario["kind"], summary: Scenario["summary"]): Scenario => ({
  id: name, project_id: "p", kind, name, notes: "", params: {}, status: "ready", summary, error: null, layer_id: null, ran_at: null, run_ms: null, created_at: "", updated_at: "",
});

describe("comparing scenarios", () => {
  it("lines up steps by label, with what each reaches", () => {
    const a = sc("Low", "flood", { steps: [{ label: "1 m", area_ha: 10, assets: { houses: 3 } }, { label: "2 m", area_ha: 25, assets: { houses: 8 } }] });
    const b = sc("High", "flood", { steps: [{ label: "2 m", area_ha: 30, assets: { houses: 9 } }] });
    const rows = compareRows([a, b]);
    expect(rows.find((r) => r.group === "Step 1: 1 m · 2 m" && r.label === "Area (ha)")!.values).toEqual([10, 30]);   // step by step
    expect(rows.find((r) => r.group === "Step 2: 2 m" && r.label === "houses reached")!.values).toEqual([8, null]);
  });
  it("shows suitability overall and by class", () => {
    const s = sc("A", "suitability", { mean: 61.5, best_ha: 12, classes: [{ label: "Very high", area_ha: 40 }] });
    const rows = compareRows([s]);
    expect(rows[0]).toEqual({ group: "Overall", label: "Mean suitability", values: [61.5] });
    expect(rows.find((r) => r.label === "Very high")!.values).toEqual([40]);
  });
  it("names land-cover classes", () => {
    const s = sc("T", "land_change", { classes: ["2"], start: { label: "2020", areas_ha: { "2": 5 } }, steps: [{ label: "2030", areas_ha: { "2": 9 }, changed_ha: 4 }] });
    const rows = compareRows([s], { "2": "Built-up" });
    expect(rows.find((r) => r.group === "2030" && r.label === "Built-up (ha)")!.values).toEqual([9]);
    expect(rows.find((r) => r.group.startsWith("Today"))!.label).toBe("Built-up");
  });
});

describe("number lists", () => {
  it("reads commas, semicolons and spaces", () => { expect(numberList("1, 2.5; 4 6")).toEqual([1, 2.5, 4, 6]); });
  it("leaves bad text for the server to explain", () => { expect(numberList("1, two")).toBe("1, two"); });
});
