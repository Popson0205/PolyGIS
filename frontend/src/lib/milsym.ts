/** Military symbols (MIL-STD-2525C / APP-6), drawn by milsymbol (MIT licence). A point layer styled "milsymbol" takes
 *  each feature's symbol identification code (SIDC) from a field, or one code for all, with its unique designation and
 *  higher formation written beside it. The map asks for each symbol picture as it needs it (styleimagemissing). */
import type { ExpressionSpecification, Map as MapLibre } from "maplibre-gl";
import ms from "milsymbol";
import type { Layer, MilStyle } from "./types";

export const MIL_PREFIX = "mil|";

export const AFFILIATIONS = [
  { id: "F", name: "Friend" }, { id: "H", name: "Hostile" }, { id: "N", name: "Neutral" }, { id: "U", name: "Unknown" },
  { id: "A", name: "Assumed friend" }, { id: "S", name: "Suspect" },
] as const;
export const STATUSES = [{ id: "P", name: "Present" }, { id: "A", name: "Planned / anticipated" }] as const;
export const ECHELONS = [
  { id: "-", name: "None" }, { id: "A", name: "Team / crew" }, { id: "B", name: "Squad" }, { id: "C", name: "Section" }, { id: "D", name: "Platoon / detachment" },
  { id: "E", name: "Company / battery / troop" }, { id: "F", name: "Battalion / squadron" }, { id: "G", name: "Regiment / group" }, { id: "H", name: "Brigade" },
  { id: "I", name: "Division" }, { id: "J", name: "Corps" }, { id: "K", name: "Army" },
] as const;
/** Symbol modifier (position 11): headquarters, task force, feint/dummy. */
export const MODIFIERS = [{ id: "-", name: "None" }, { id: "A", name: "Headquarters" }, { id: "E", name: "Task force" }, { id: "B", name: "Task force HQ" }] as const;

/** Common symbols, by battle dimension (position 3) and function (positions 5–10). */
export const SYMBOLS: { group: string; items: { name: string; dim: string; fn: string; unit?: boolean }[] }[] = [
  { group: "Land units", items: [
    { name: "Infantry", dim: "G", fn: "UCI---", unit: true }, { name: "Mechanised infantry", dim: "G", fn: "UCIZ--", unit: true },
    { name: "Motorised infantry", dim: "G", fn: "UCIM--", unit: true }, { name: "Airborne infantry", dim: "G", fn: "UCIA--", unit: true },
    { name: "Armour", dim: "G", fn: "UCA---", unit: true }, { name: "Reconnaissance", dim: "G", fn: "UCR---", unit: true },
    { name: "Field artillery", dim: "G", fn: "UCF---", unit: true }, { name: "Self-propelled artillery", dim: "G", fn: "UCFHE-", unit: true },
    { name: "Rocket artillery", dim: "G", fn: "UCFR--", unit: true }, { name: "Mortar", dim: "G", fn: "UCFM--", unit: true },
    { name: "Air defence", dim: "G", fn: "UCD---", unit: true }, { name: "Anti-armour", dim: "G", fn: "UCAA--", unit: true },
    { name: "Engineer", dim: "G", fn: "UCE---", unit: true }, { name: "Aviation", dim: "G", fn: "UCV---", unit: true },
    { name: "Special forces", dim: "G", fn: "UCS---", unit: true }, { name: "Military police", dim: "G", fn: "UULM--", unit: true },
    { name: "Signal", dim: "G", fn: "UUS---", unit: true }, { name: "Military intelligence", dim: "G", fn: "UUM---", unit: true },
    { name: "CBRN defence", dim: "G", fn: "UUA---", unit: true }, { name: "Medical", dim: "G", fn: "USM---", unit: true },
    { name: "Supply", dim: "G", fn: "USS---", unit: true }, { name: "Transport", dim: "G", fn: "UST---", unit: true },
    { name: "Maintenance", dim: "G", fn: "USX---", unit: true }, { name: "Civil affairs", dim: "G", fn: "USAC--", unit: true },
  ] },
  { group: "Land equipment", items: [
    { name: "Tank", dim: "G", fn: "EVAT--" }, { name: "Armoured personnel carrier", dim: "G", fn: "EVAA--" },
    { name: "Infantry fighting vehicle", dim: "G", fn: "EVAI--" }, { name: "Howitzer", dim: "G", fn: "EWHH--" },
    { name: "Multiple rocket launcher", dim: "G", fn: "EWMS--" }, { name: "Mortar (weapon)", dim: "G", fn: "EWO---" },
    { name: "Machine gun", dim: "G", fn: "EWRH--" }, { name: "Anti-tank missile", dim: "G", fn: "EWMAT-" },
    { name: "Air defence missile", dim: "G", fn: "EWMA--" }, { name: "Truck", dim: "G", fn: "EVUS--" },
    { name: "Radar", dim: "G", fn: "ESR---" },
  ] },
  { group: "Installations", items: [
    { name: "Installation", dim: "G", fn: "I-----" }, { name: "Airport / air base", dim: "G", fn: "IBA---" },
    { name: "Military base", dim: "G", fn: "IB----" }, { name: "Supply point", dim: "G", fn: "IR----" },
    { name: "Medical facility", dim: "G", fn: "IXH---" }, { name: "Power plant", dim: "G", fn: "IUE---" },
  ] },
  { group: "Air", items: [
    { name: "Military aircraft", dim: "A", fn: "MF----" }, { name: "Fighter", dim: "A", fn: "MFF---" }, { name: "Bomber", dim: "A", fn: "MFB---" },
    { name: "Transport aircraft", dim: "A", fn: "MFC---" }, { name: "Helicopter (rotary wing)", dim: "A", fn: "MH----" },
    { name: "Attack helicopter", dim: "A", fn: "MHA---" }, { name: "Drone (UAV)", dim: "A", fn: "MFQ---" }, { name: "Civil aircraft", dim: "A", fn: "C-----" },
  ] },
  { group: "Sea", items: [
    { name: "Surface combatant", dim: "S", fn: "C-----" }, { name: "Patrol boat", dim: "S", fn: "CP----" },
    { name: "Amphibious ship", dim: "S", fn: "CA----" }, { name: "Merchant ship", dim: "S", fn: "X-----" },
    { name: "Fishing vessel", dim: "S", fn: "XF----" }, { name: "Submarine", dim: "U", fn: "S-----" },
  ] },
];

/** A 15-character MIL-STD-2525C code. */
export function makeSidc(o: { affiliation: string; dim: string; status: string; fn: string; modifier?: string; echelon?: string }): string {
  const fn = (o.fn + "------").slice(0, 6);
  const mod = o.dim === "G" && fn.startsWith("U") ? `${o.modifier ?? "-"}${o.echelon ?? "-"}` : "--";
  return `S${o.affiliation}${o.dim}${o.status}${fn}${mod}---`.slice(0, 15).padEnd(15, "-");
}

/** The parts of a 2525C code (as far as it has them), to fill the symbol picker from a code. */
export function readSidc(sidc: string) {
  const s = (sidc || "").toUpperCase();
  return { affiliation: s[1] ?? "F", dim: s[2] ?? "G", status: s[3] ?? "P", fn: s.slice(4, 10) || "------", modifier: s[10] ?? "-", echelon: s[11] ?? "-" };
}

export function isValidSidc(sidc: string): boolean {
  if (!sidc || sidc.length < 10) return false;
  try { return new ms.Symbol(sidc, { size: 20 }).isValid() === true; } catch { return false; }
}

export interface SymbolText { designation?: string; higher?: string }

/** The picture of a symbol: a canvas with the symbol's insertion point at its centre (so the map can anchor it there). */
export function drawSymbol(sidc: string, size: number, text: SymbolText = {}, ratio = 1): HTMLCanvasElement {
  const sym = new ms.Symbol(sidc, { size: size * ratio, uniqueDesignation: text.designation || "", higherFormation: text.higher || "", standard: "2525", outlineWidth: 2 * ratio, outlineColor: "rgba(255,255,255,0.9)" });
  const src = sym.asCanvas();
  const a = sym.getAnchor();
  const w = Math.ceil(2 * Math.max(a.x, src.width - a.x)), h = Math.ceil(2 * Math.max(a.y, src.height - a.y));
  const c = document.createElement("canvas");
  c.width = Math.max(1, w); c.height = Math.max(1, h);
  c.getContext("2d")!.drawImage(src, w / 2 - a.x, h / 2 - a.y);
  return c;
}

/** A data URL of a symbol, for previews and legends. */
export function symbolUrl(sidc: string, size = 28): string {
  try { return new ms.Symbol(sidc, { size, standard: "2525" }).asCanvas().toDataURL(); } catch { return ""; }
}

const enc = (v: unknown) => String(v ?? "").replace(/\|/g, "/");
/** The map image id for a symbol: "mil|<size>|<sidc>|<designation>|<higher formation>". */
export function imageExpr(l: Layer): ExpressionSpecification {
  const m = l.style.mil ?? ({} as MilStyle);
  const code: unknown = m.sidc_field ? ["coalesce", ["get", m.sidc_field], m.sidc || "SUGP-----------"] : m.sidc || "SFGPU----------";
  const text = (f?: string | null): unknown => (f ? ["to-string", ["coalesce", ["get", f], ""]] : "");
  return ["concat", `${MIL_PREFIX}${Math.round(m.size || 36)}|`, ["to-string", code], "|", text(m.designation_field), "|", text(m.higher_field)] as unknown as ExpressionSpecification;
}

/** Let a map draw military symbols: each one is made the first time the map asks for it. */
export function attachMilSymbols(map: MapLibre, pixelRatio?: number): void {
  const ratio = Math.min(4, Math.max(1, pixelRatio ?? (typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1)));
  map.on("styleimagemissing", (e: { id: string }) => {
    if (!e.id.startsWith(MIL_PREFIX) || map.hasImage(e.id)) return;
    const [, size, sidc, designation, higher] = e.id.split("|");
    let c: HTMLCanvasElement;
    try { c = drawSymbol(sidc, Number(size) || 36, { designation: designation ? enc(designation) : "", higher: higher ? enc(higher) : "" }, ratio); }
    catch { c = drawSymbol("SUGP-----------", Number(size) || 36, {}, ratio); }
    const img = c.getContext("2d")!.getImageData(0, 0, c.width, c.height);
    map.addImage(e.id, { width: c.width, height: c.height, data: new Uint8Array(img.data.buffer) }, { pixelRatio: ratio });
  });
}
