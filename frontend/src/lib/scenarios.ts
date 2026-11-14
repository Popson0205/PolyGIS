import type { Scenario, ScenarioKind } from "./types";

export const KINDS: { id: ScenarioKind; label: string; blurb: string }[] = [
  { id: "suitability", label: "Suitability", blurb: "Where is best? Weigh criteria (slope, distance to roads…), exclude protected land, find the best sites." },
  { id: "flood", label: "Flood and sea-level rise", blurb: "Water levels, a rising sea or a river stage on an elevation model: depth, area, and what is reached." },
  { id: "land_change", label: "Land-use change and urban growth", blurb: "Project land cover to future years from two past maps, with protected areas and growth policies." },
  { id: "spread", label: "Spread over time", blurb: "Fire, pollution or any front spreading from sources: arrival time with wind, friction and barriers." },
];
export const kindLabel = (k: ScenarioKind) => KINDS.find((x) => x.id === k)?.label ?? k;

const nice: Record<string, string> = {
  area_ha: "Area (ha)", max_depth: "Deepest (m)", mean_depth: "Mean depth (m)", people: "People", changed_ha: "Changed (ha)",
  mean: "Mean suitability", best_ha: "Best land (ha)", best_sites: "Best sites", largest_site_ha: "Largest site (ha)", excluded_ha: "Excluded (ha)",
  scored_ha: "Scored (ha)", best_threshold: "Best from score",
};
export const metricLabel = (k: string) => nice[k] ?? k;

export type Row = { group: string; label: string; values: (number | null)[] };

/** The figures of several scenarios side by side: overall figures first, then each step's (matched by its label),
 *  including counts of reached features and areas per class. */
export function compareRows(list: Scenario[], classNames: Record<string, string> = {}): Row[] {
  const rows: Row[] = [];
  const add = (group: string, label: string, get: (s: Scenario) => unknown) => {
    const values = list.map((s) => { const v = get(s); return typeof v === "number" && Number.isFinite(v) ? v : null; });
    if (values.some((v) => v != null)) rows.push({ group, label, values });
  };
  const kind = list[0]?.kind;
  if (kind === "suitability") {
    for (const k of ["mean", "best_ha", "best_sites", "largest_site_ha", "scored_ha", "excluded_ha"]) add("Overall", metricLabel(k), (s) => s.summary?.[k]);
    const classes = (list[0].summary?.classes as { label: string }[] | undefined) ?? [];
    for (const c of classes) add("Area by class (ha)", c.label, (s) => (s.summary?.classes as { label: string; area_ha: number }[] | undefined)?.find((x) => x.label === c.label)?.area_ha);
    return rows;
  }
  if (kind === "land_change") {
    const cls = Array.from(new Set(list.flatMap((s) => (s.summary?.classes as string[] | undefined) ?? [])));
    for (const c of cls) add(`Today (${String((list[0].summary?.start as { label?: string } | undefined)?.label ?? "")})`, classNames[c] ?? `Class ${c}`,
      (s) => (s.summary?.start as { areas_ha?: Record<string, number> } | undefined)?.areas_ha?.[c]);
  }
  // steps are matched in order (step 1 with step 1…), so variants with different levels or years still line up
  const most = Math.max(0, ...list.map((s) => s.summary?.steps?.length ?? 0));
  for (let i = 0; i < most; i++) {
    const step = (s: Scenario) => s.summary?.steps?.[i];
    const names = Array.from(new Set(list.map((s) => step(s)?.label).filter((x): x is string => !!x)));
    const label = most === 1 && names.length <= 1 ? (names[0] ?? "Result") : `Step ${i + 1}: ${names.join(" · ")}`;
    const keys = new Set<string>();
    for (const s of list) { const st = step(s); if (st) Object.keys(st).forEach((k) => keys.add(k)); }
    for (const k of keys) {
      if (k === "label") continue;
      if (k === "assets") {
        const names = new Set<string>();
        for (const s of list) Object.keys((step(s)?.assets as Record<string, number> | undefined) ?? {}).forEach((n) => names.add(n));
        for (const n of names) add(label, `${n} reached`, (s) => (step(s)?.assets as Record<string, number> | undefined)?.[n]);
      } else if (k === "areas_ha") {
        const cls = new Set<string>();
        for (const s of list) Object.keys((step(s)?.areas_ha as Record<string, number> | undefined) ?? {}).forEach((n) => cls.add(n));
        for (const c of cls) add(label, `${classNames[c] ?? `Class ${c}`} (ha)`, (s) => (step(s)?.areas_ha as Record<string, number> | undefined)?.[c]);
      } else add(label, metricLabel(k), (s) => step(s)?.[k]);
    }
  }
  return rows;
}

/** "1, 2.5; 4" → [1, 2.5, 4] (or the text unchanged when it is not a list of numbers, so the server can say so). */
export function numberList(text: string): number[] | string {
  const parts = text.split(/[,;\s]+/).filter(Boolean);
  const n = parts.map(Number);
  return parts.length && n.every((x) => Number.isFinite(x)) ? n : text;
}
