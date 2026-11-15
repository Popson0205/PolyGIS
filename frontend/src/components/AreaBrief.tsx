/** Area brief: an intelligence summary of an area of interest. Draw the area (or use a selected polygon); the brief
 *  gives its size and position, the ground, what each layer has inside, and the units there by affiliation. It can
 *  be printed or saved as PDF. */
import { FileText, PenLine, Printer, SquareDashed, X } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { symbolUrl } from "../lib/milsym";
import type { AreaBrief, Layer } from "../lib/types";
import { Button, ErrorText, IconButton } from "./ui";

const n = (v: number | null | undefined, d = 1) => (v == null ? "–" : v.toLocaleString("en-GB", { maximumFractionDigits: d }));
const pct = (v?: number) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

export default function BriefPane({ projectId, dems, selectedPolygon, onDraw, onClose }: {
  projectId: string; dems: Layer[];
  /** A polygon chosen on the map (one selected feature of a polygon layer), to brief on. */
  selectedPolygon: { layer: Layer; id: number } | null;
  /** Draw an area on the map; the polygon comes back through the promise (null if cancelled). */
  onDraw: () => Promise<GeoJSON.Geometry | null>;
  onClose: () => void;
}) {
  const [name, setName] = useState("Area of interest");
  const [dem, setDem] = useState<string>(dems[0]?.id ?? "");
  const [brief, setBrief] = useState<AreaBrief | null>(null);
  const [busy, setBusy] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function run(area: { geometry?: GeoJSON.Geometry; layer_id?: string; feature_id?: number }) {
    setBusy(true); setError(null);
    try { setBrief(await api.areaBrief(projectId, { ...area, dem_id: dem || null, name: name.trim() || "Area of interest" })); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function draw() {
    setDrawing(true);
    const g = await onDraw();
    setDrawing(false);
    if (g) await run({ geometry: g });
  }

  return (
    <aside className="ws-panel mil" aria-label="Area brief">
      <div className="ws-pane-head"><strong>Area brief</strong><span className="ge-spacer" /><IconButton label="Close" onClick={onClose}><X size={16} /></IconButton></div>
      <div className="ws-scroll mil-body">
        <label className="ws-f">Name of the area<input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} /></label>
        <label className="ws-f">Ground from<select value={dem} onChange={(e) => setDem(e.target.value)}>
          <option value="">No elevation model</option>{dems.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
        <div className="mil-acts">
          <Button variant="primary" onClick={() => void draw()} busy={busy && !drawing}><PenLine size={15} aria-hidden="true" />{drawing ? "Click the corners; the first again to finish" : "Draw the area"}</Button>
          <Button disabled={!selectedPolygon} onClick={() => selectedPolygon && void run({ layer_id: selectedPolygon.layer.id, feature_id: selectedPolygon.id })}
            title={selectedPolygon ? `The selected polygon of ${selectedPolygon.layer.name}` : "Select one polygon on the map first"}>
            <SquareDashed size={15} aria-hidden="true" />Use the selected polygon</Button>
        </div>
        <ErrorText error={error} />
        {busy && <p className="ws-muted" role="status">Putting the brief together…</p>}
        {brief && <BriefView b={brief} />}
      </div>
    </aside>
  );
}

function BriefView({ b }: { b: AreaBrief }) {
  const g = b.ground;
  const forces = Object.entries(b.unit_counts).sort((x, y) => y[1] - x[1]);
  return (
    <article className="brief" aria-label={`Brief: ${b.name}`}>
      <header className="brief-head">
        <h3><FileText size={16} aria-hidden="true" />{b.name}</h3>
        <Button onClick={() => printBrief(b)}><Printer size={15} aria-hidden="true" />Print or save as PDF</Button>
      </header>
      <section><h4>Area</h4>
        <dl className="brief-dl">
          <dt>Size</dt><dd>{n(b.area_km2, 3)} km² · perimeter {n(b.perimeter_km, 2)} km</dd>
          <dt>Centre</dt><dd className="mono">{String(b.centre.usng ?? b.centre.dd ?? "")}<br />{String(b.centre.dd ?? "")}</dd>
          <dt>Extent</dt><dd className="mono">SW {b.extent.south_west}<br />NE {b.extent.north_east}</dd>
        </dl>
      </section>
      {g && <section><h4>Ground <span className="ws-muted">({g.dem})</span></h4>
        {!g.covered ? <p className="ws-muted">The elevation model does not cover this area.</p> : (
          <dl className="brief-dl">
            <dt>Height</dt><dd>{n(g.min_m)} – {n(g.max_m)} m (mean {n(g.mean_m)} m, relief {n(g.relief_m)} m)</dd>
            <dt>Slopes</dt><dd>mean {n(g.mean_slope_deg)}° · steeper than 15°: {pct(g.steep_share)} · than 30°: {pct(g.very_steep_share)}</dd>
            <dt>Highest point</dt><dd className="mono">{n(g.highest?.elevation_m)} m at {g.highest?.position}</dd>
            {g.share_covered != null && g.share_covered < 0.98 && <><dt>Covered</dt><dd>{pct(g.share_covered)} of the area has heights</dd></>}
          </dl>)}
      </section>}
      <section><h4>Forces</h4>
        {!b.units.length ? <p className="ws-muted">No military symbols inside the area.</p> : <>
          <p className="brief-forces">{forces.map(([k, v]) => <span key={k} className={`brief-aff is-${k.toLowerCase().replace(/\s+/g, "-")}`}>{v} {k}</span>)}</p>
          <table className="brief-table"><thead><tr><th /><th>Unit</th><th>Affiliation</th><th>Position</th></tr></thead><tbody>
            {b.units.slice(0, 100).map((u, k) => (
              <tr key={k}><td><img src={symbolUrl(u.sidc, 18)} alt="" /></td><td>{u.designation || "–"}{u.higher ? <span className="ws-muted"> / {u.higher}</span> : null}</td>
                <td>{u.affiliation}</td><td className="mono">{u.position}</td></tr>))}
          </tbody></table>
          {b.units.length > 100 && <p className="ws-muted">And {b.units.length - 100} more.</p>}
        </>}
      </section>
      <section><h4>Layers inside</h4>
        {!b.layers.length ? <p className="ws-muted">No features of the open map's layers fall inside the area.</p> : (
          <table className="brief-table"><thead><tr><th>Layer</th><th>Features</th><th>Inside</th></tr></thead><tbody>
            {b.layers.map((l) => <tr key={l.id}><td>{l.name}</td><td>{l.features.toLocaleString("en-GB")}</td><td>{l.measure == null ? "" : `${n(l.measure, 3)} ${l.unit}`}</td></tr>)}
          </tbody></table>)}
      </section>
    </article>
  );
}

/** The brief as a page of its own, printed (or saved as PDF from the print dialog). */
function printBrief(b: AreaBrief) {
  const g = b.ground;
  const rows = (pairs: [string, string][]) => pairs.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join("");
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${esc(b.name)} – area brief</title><style>
    body{font:12.5px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1a2233;margin:28px 36px}
    h1{font-size:20px;margin:0 0 2px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:#46506a;border-bottom:1px solid #d5d9e2;padding-bottom:3px;margin:20px 0 8px}
    .sub{color:#5b6475;margin:0 0 12px}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:4px 8px 4px 0;vertical-align:top;border-bottom:1px solid #eef0f4}
    th{color:#5b6475;font-weight:500;width:28%}.m{font-family:ui-monospace,Menlo,monospace}img{height:18px}
    .cls{display:inline-block;border:1px solid #888;padding:1px 6px;font-size:10.5px;letter-spacing:.06em;margin-bottom:10px}
    @page{margin:16mm}</style></head><body>
    <div class="cls">FOR OFFICIAL USE – MARK AS YOUR ORGANISATION REQUIRES</div>
    <h1>${esc(b.name)}</h1><p class="sub">Area brief · ${esc(b.project)} · ${new Date().toLocaleString("en-GB")}</p>
    <h2>Area</h2><table>${rows([["Size", `${n(b.area_km2, 3)} km² · perimeter ${n(b.perimeter_km, 2)} km`],
      ["Centre", `<span class="m">${esc(b.centre.usng ?? "")}<br>${esc(b.centre.dd ?? "")}</span>`],
      ["Extent", `<span class="m">SW ${esc(b.extent.south_west)}<br>NE ${esc(b.extent.north_east)}</span>`]])}</table>
    ${g ? `<h2>Ground (${esc(g.dem)})</h2>${g.covered ? `<table>${rows([["Height", `${n(g.min_m)} – ${n(g.max_m)} m (mean ${n(g.mean_m)} m, relief ${n(g.relief_m)} m)`],
      ["Slopes", `mean ${n(g.mean_slope_deg)}° · steeper than 15°: ${pct(g.steep_share)} · than 30°: ${pct(g.very_steep_share)}`],
      ["Highest point", `<span class="m">${n(g.highest?.elevation_m)} m at ${esc(g.highest?.position)}</span>`]])}</table>` : "<p>The elevation model does not cover this area.</p>"}` : ""}
    <h2>Forces</h2>${b.units.length ? `<p>${Object.entries(b.unit_counts).map(([k, v]) => `${v} ${esc(k)}`).join(" · ")}</p><table><tr><th></th><th>Unit</th><th>Affiliation</th><th>Position</th></tr>
      ${b.units.map((u) => `<tr><td><img src="${symbolUrl(u.sidc, 18)}" alt=""></td><td>${esc(u.designation || "–")}${u.higher ? ` / ${esc(u.higher)}` : ""}</td><td>${esc(u.affiliation)}</td><td class="m">${esc(u.position)}</td></tr>`).join("")}</table>`
      : "<p>No military symbols inside the area.</p>"}
    <h2>Layers inside</h2>${b.layers.length ? `<table><tr><th>Layer</th><th>Features</th><th>Inside</th></tr>${b.layers.map((l) =>
      `<tr><td>${esc(l.name)}</td><td>${l.features}</td><td>${l.measure == null ? "" : `${n(l.measure, 3)} ${esc(l.unit)}`}</td></tr>`).join("")}</table>` : "<p>None.</p>"}
    <script>window.onload=()=>setTimeout(()=>window.print(),200)</script></body></html>`;
  const w = window.open("", "_blank");
  if (!w) return;
  w.document.open(); w.document.write(html); w.document.close();
}
