/** Ask PolyGIS: what a typed request means, worked out here in the page (no AI service). It knows the analysis tools by
 *  their names and parameters, the project's layers by name, and the buttons of every mode by their labels, so
 *  "buffer roads by 500 m", "clip roads to areas", "zoom to checkpoints", "go to 6.45, 3.39" or "range rings" all
 *  land on the right thing, and anything else falls back to a search of the tools. */
import type { Layer, Tool, ToolParam } from "./types";

export type AskHit =
  | { kind: "tool"; key: string; label: string; hint: string; tool: Tool; preset: Record<string, unknown>; filled: string[] }
  | { kind: "layer"; key: string; label: string; hint: string; layer: Layer; action: "zoom" | "table" | "style" | "show" | "hide" }
  | { kind: "goto"; key: string; label: string; hint: string; lng: number; lat: number }
  | { kind: "command"; key: string; label: string; hint: string; id: string; disabled?: boolean };

/** A button of a mode, found on the page: its label, the mode it belongs to, and an id to press it by. */
export interface Command { id: string; label: string; mode: string; modeName: string; disabled: boolean; title?: string }

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9.,\- ]+/g, " ").replace(/\s+/g, " ").trim();
const words = (s: string) => norm(s).split(" ").filter(Boolean);
const STOP = new Set(["the", "a", "an", "of", "to", "by", "with", "for", "on", "in", "into", "from", "and", "me", "show", "please", "layer", "layers", "my", "all", "at", "around", "within", "near"]);

/** Units a distance can be written in, to metres. */
const UNITS: Record<string, number> = { m: 1, meter: 1, meters: 1, metre: 1, metres: 1, km: 1000, kilometer: 1000, kilometers: 1000, kilometre: 1000, kilometres: 1000,
  ft: 0.3048, feet: 0.3048, foot: 0.3048, mi: 1609.344, mile: 1609.344, miles: 1609.344, nm: 1852, nmi: 1852 };

/** Layers named in the text, longest names first, each with where it was found. */
export function layersIn(text: string, layers: Layer[]): Layer[] {
  const t = ` ${norm(text)} `;
  const found: { l: Layer; at: number }[] = [];
  let rest = t;
  for (const l of [...layers].sort((a, b) => b.name.length - a.name.length)) {
    const n = norm(l.name);
    if (!n) continue;
    const at = rest.indexOf(` ${n} `);
    if (at >= 0) { found.push({ l, at }); rest = rest.slice(0, at) + " " + "#".repeat(n.length) + " " + rest.slice(at + n.length + 2); }
  }
  if (!found.length) {
    // no whole name: a layer whose name has a word the request starts (at least 3 letters: "areas" for "Areas of interest")
    const qw = words(text).filter((w) => w.length >= 3 && !STOP.has(w));
    qw.forEach((w, i) => {
      const l = layers.find((x) => !found.some((f) => f.l === x) && words(x.name).some((n) => n === w || (w.length >= 4 && n.startsWith(w))));
      if (l) found.push({ l, at: i });
    });
  }
  return found.sort((a, b) => a.at - b.at).map((f) => f.l);
}

/** Distances in the text, in metres ("500 m", "2km", "1.5 miles"), and plain numbers. */
export function numbersIn(text: string): { metres: number[]; plain: number[] } {
  const metres: number[] = [], plain: number[] = [];
  const re = /(-?\d+(?:\.\d+)?)\s*([a-z]+)?/gi;
  for (const m of norm(text).matchAll(re)) {
    const v = Number(m[1]);
    const u = (m[2] ?? "").toLowerCase();
    if (UNITS[u]) metres.push(v * UNITS[u]);
    else plain.push(v);
  }
  return { metres, plain };
}

/** "6.45, 3.39" or "6.45 3.39" (latitude first, as people say it) or "lat 6.45 lon 3.39". */
export function coordsIn(text: string): { lng: number; lat: number } | null {
  const m = norm(text).match(/(?:^|[a-z ])(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(?:lon[a-z]* )?(-?\d{1,3}(?:\.\d+)?)\s*$/);
  if (!m) return null;
  const lat = Number(m[1]), lng = Number(m[2]);
  return Math.abs(lat) <= 90 && Math.abs(lng) <= 180 && (String(m[1]).includes(".") || String(m[2]).includes(".")) ? { lng, lat } : null;
}

/** How well a name matches the words typed: every word should start a word of the name (or of its other text). */
function score(qw: string[], name: string, extra = ""): number {
  if (!qw.length) return 0;
  const nw = words(name), ew = words(extra);
  let s = 0;
  for (const w of qw) {
    if (nw.some((x) => x === w)) s += 3;
    else if (nw.some((x) => x.startsWith(w))) s += 2;
    else if (ew.some((x) => x.startsWith(w))) s += 0.5;
    else return 0;
  }
  return s / qw.length + (norm(name).startsWith(qw.join(" ")) ? 1 : 0);
}

/** The tool a request starts with or names, by the longest tool name found in it. */
function toolNamed(text: string, tools: Tool[]): Tool | null {
  const t = ` ${norm(text)} `;
  let best: Tool | null = null;
  for (const tool of tools) {
    const n = norm(tool.name);
    if (t.includes(` ${n} `) && (!best || n.length > norm(best.name).length)) best = tool;
  }
  return best;
}

const fits = (p: ToolParam, l: Layer) => (p.geometry ? p.geometry.includes(l.geometry_type) : l.geometry_type !== "Raster" || p.name.includes("raster"));

/** A tool with what the request says filled in: the layers named go to its layer inputs in order, a distance to its
 *  first distance, a plain number to its first number. Returns null when the tool takes nothing that was named. */
export function fillTool(tool: Tool, text: string, layers: Layer[]): { preset: Record<string, unknown>; filled: string[] } {
  const preset: Record<string, unknown> = {}, filled: string[] = [];
  const named = layersIn(text, layers);
  const layerParams = tool.params.filter((p) => p.type === "layer");
  for (const p of layerParams) {
    const l = named.find((x) => fits(p, x) && !Object.values(preset).includes(x.id));
    if (l) { preset[p.name] = l.id; filled.push(`${p.label}: ${l.name}`); }
  }
  const { metres, plain } = numbersIn(text);
  const nums = tool.params.filter((p) => p.type === "number");
  const dist = nums.find((p) => p.unit === "m");
  if (dist && metres.length) { preset[dist.name] = Math.round(metres[0] * 1000) / 1000; filled.push(`${dist.label}: ${fmtM(metres[0])}`); }
  else if (dist && plain.length) { preset[dist.name] = plain.shift(); filled.push(`${dist.label}: ${preset[dist.name]} m`); }
  const other = nums.find((p) => p !== dist);
  if (other && plain.length) { preset[other.name] = plain[0]; filled.push(`${other.label}: ${plain[0]}`); }
  return { preset, filled };
}
const fmtM = (m: number) => (m >= 1000 ? `${Math.round(m) / 1000} km` : `${Math.round(m * 100) / 100} m`);

const LAYER_ACTIONS: [RegExp, "zoom" | "table" | "style" | "show" | "hide", string][] = [
  [/^(zoom( to)?|go to|fly to|find)\b/, "zoom", "Zoom to"], [/\b(table|attributes|rows|records)\b/, "table", "Open the table of"],
  [/^(style|colou?r|symboli[sz]e|label)\b/, "style", "Style"], [/^(hide|turn off)\b/, "hide", "Hide"], [/^(show|turn on|display)\b/, "show", "Show"]];

/** Everything the request could mean, best first (at most `max`). */
export function ask(text: string, ctx: { tools: Tool[]; layers: Layer[]; commands: Command[] }, max = 9): AskHit[] {
  const q = norm(text);
  if (!q) return [];
  const out: (AskHit & { s: number })[] = [];
  const qw = words(q).filter((w) => !STOP.has(w) && !/^\d/.test(w));

  // a place to go
  const at = coordsIn(q);
  if (at) out.push({ kind: "goto", key: "goto", label: `Go to ${at.lat.toFixed(5)}, ${at.lng.toFixed(5)}`, hint: "Latitude, longitude", ...at, s: 100 });

  // a layer action: "zoom to roads", "roads table", "hide sightings"
  const named = layersIn(q, ctx.layers);
  if (named.length) {
    const l = named[0];
    const act = LAYER_ACTIONS.find(([re]) => re.test(q));
    const tool = toolNamed(q, ctx.tools);
    if (act && !tool) out.push({ kind: "layer", key: `l-${act[1]}-${l.id}`, label: `${act[2]} ${l.name}`, hint: "Layer", layer: l, action: act[1], s: 90 });
    else if (!tool) for (const [, a, words] of LAYER_ACTIONS.filter(([, a]) => a === "zoom" || a === "table" || a === "style"))
      out.push({ kind: "layer", key: `l-${a}-${l.id}`, label: `${words} ${l.name}`, hint: "Layer", layer: l, action: a, s: 30 });
  }

  // a tool, with what was said filled in
  const named1 = toolNamed(q, ctx.tools);
  for (const tool of ctx.tools) {
    const s = tool === named1 ? 80 : score(qw.filter((w) => !named.some((l) => norm(l.name).split(" ").includes(w))), tool.name, `${tool.about} ${tool.group} ${tool.id}`) * 10;
    if (s <= 0) continue;
    const { preset, filled } = fillTool(tool, q, ctx.layers);
    out.push({ kind: "tool", key: `t-${tool.id}`, label: tool.name, hint: filled.length ? filled.join(" · ") : `${tool.group} · ${tool.about}`, tool, preset, filled, s: s + filled.length * 5 });
  }

  // a button of any mode
  for (const c of ctx.commands) {
    const s = score(qw, c.label, `${c.modeName} ${c.title ?? ""}`) * 10;
    if (s > 0) out.push({ kind: "command", key: `c-${c.id}`, label: c.label, hint: c.disabled ? `${c.modeName} · ${c.title || "not available now"}` : c.modeName, id: c.id, disabled: c.disabled, s: s + 2 });
  }
  // a mode's own button wins over the tool of the same name (it opens in its mode, e.g. Range rings in Defence)
  const buttons = new Set(out.filter((h) => h.kind === "command" && !h.disabled).map((h) => norm(h.label)));
  for (let i = out.length - 1; i >= 0; i--) if (out[i].kind === "tool" && buttons.has(norm(out[i].label))) out.splice(i, 1);
  const seen = new Set<string>();
  return out.sort((a, b) => b.s - a.s).filter((h) => (seen.has(h.label) ? false : (seen.add(h.label), true))).slice(0, max).map(({ s: _s, ...h }) => h as AskHit);
}
