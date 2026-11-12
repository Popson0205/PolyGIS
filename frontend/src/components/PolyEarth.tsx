import { ArrowLeft, BarChart3, CheckCircle2, ChevronDown, CloudSun, Download, Earth, Flame, Layers3, Leaf, LineChart, Loader2, Map as MapIcon, Mountain, Play,
  Search, Sparkles, Sprout, Table2, Thermometer, TrendingUp, Waves, X, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import type { EarthChart, EarthJob, EarthReport, Layer, Tool, ToolParam } from "../lib/types";
import { describeBox } from "./Imagery";
import { Button, ErrorText, IconButton, useStored } from "./ui";

type Box = [number, number, number, number];
const WORD: Record<string, string> = { Point: "points", LineString: "lines", Polygon: "polygons", Raster: "raster" };
const ICON: Record<string, ReactNode> = {
  composite: <Layers3 />, index: <Leaf />, band_formula: <Sparkles />, surface_temperature: <Thermometer />, rainfall: <CloudSun />, terrain: <Mountain />,
  water_history: <Waves />, time_series: <LineChart />, trend: <TrendingUp />, anomaly: <BarChart3 />, change: <Sprout />, burn_severity: <Flame />,
  water_change: <Waves />, flood: <Waves />, classify: <MapIcon />, cluster: <MapIcon />, zonal_index: <Table2 />, land_cover: <Earth />,
};
const ago = (days: number) => new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
const seconds = (ms: number | null) => (ms == null ? "" : ms < 60_000 ? `${Math.round(ms / 1000)} s` : `${Math.floor(ms / 60_000)} min ${Math.round((ms % 60_000) / 1000)} s`);

/** Runs keep going while the pane is closed: the workspace polls them and adds their layers to the map when they finish. */
export function useEarthJobs(onFinished: (job: EarthJob) => void) {
  const [jobs, setJobs] = useState<EarthJob[]>([]);
  const latest = useRef(jobs);
  latest.current = jobs;
  const done = useRef(onFinished);
  done.current = onFinished;
  const running = jobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => {
      for (const j of latest.current.filter((x) => x.status === "queued" || x.status === "running")) {
        void api.earthJob(j.id).then((n) => {
          const was = latest.current.find((x) => x.id === n.id);
          setJobs((c) => c.map((x) => (x.id === n.id ? n : x)));
          if (was && was.status !== n.status && (n.status === "done" || n.status === "failed")) done.current(n);
        }, () => undefined);
      }
    }, 1500);
    return () => window.clearInterval(t);
  }, [running]);
  const add = useCallback((j: EarthJob) => setJobs((cur) => [j, ...cur.filter((x) => x.id !== j.id)].slice(0, 20)), []);
  return { jobs, add };
}

/** Poly Earth: Earth Engine's usual analyses as forms. Pick a tool, the area and the dates; the server reads the open satellite
 * data, does the work and adds the result to the map, with a chart or an accuracy report where there is one. */
export default function PolyEarth({ projectId, layers, selection, activeId, canEdit, allowed, start, jobs, viewBox, onStarted, onClose, onShowLayer }: {
  projectId: string; layers: Layer[]; selection: Record<string, number[]>; activeId: string | null; canEdit: boolean; allowed: boolean;
  start?: string | null; jobs: EarthJob[]; viewBox: () => Box; onStarted: (j: EarthJob) => void; onClose: () => void; onShowLayer: (l: Layer) => void;
}) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [tool, setTool] = useState<Tool | null>(null);
  const [q, setQ] = useState("");
  const [closed, setClosed] = useStored<string[]>("earth-groups-closed", []);
  const [view, setView] = useState<"tools" | "runs">("tools");
  useEffect(() => { if (allowed) void api.earthTools().then(setTools, setError); }, [allowed]);
  useEffect(() => { if (start && tools.length) setTool(tools.find((t) => t.id === start) ?? null); }, [start, tools]);
  const groups = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const hit = tools.filter((t) => words.every((w) => `${t.name} ${t.about} ${t.group}`.toLowerCase().includes(w)));
    return Array.from(new Set(hit.map((t) => t.group))).map((g) => [g, hit.filter((t) => t.group === g)] as const);
  }, [tools, q]);
  const active = jobs.filter((j) => j.status === "queued" || j.status === "running").length;

  return (
    <aside className="ws-panel pe" aria-label="Poly Earth">
      <div className="ws-pane-head">
        {tool ? <IconButton label="All Poly Earth tools" onClick={() => setTool(null)}><ArrowLeft size={16} /></IconButton> : <Earth size={16} aria-hidden="true" className="ws-pane-icon pe-mark" />}
        <strong>{tool ? tool.name : "Poly Earth"}</strong>
        <IconButton label="Close Poly Earth" onClick={onClose}><X size={16} /></IconButton>
      </div>
      {!allowed ? (
        <div className="ws-empty-state"><Earth size={22} aria-hidden="true" /><span>Poly Earth is in <b>PolyGIS Advanced</b>. Ask GeoEstate NIG Limited to move your organization to Advanced.</span></div>
      ) : tool ? (
        <EarthForm key={tool.id} tool={tool} projectId={projectId} layers={layers} selection={selection} activeId={activeId} canEdit={canEdit} viewBox={viewBox}
          job={jobs.find((j) => j.tool === tool.id) ?? null} onStarted={onStarted} onShowLayer={onShowLayer} />
      ) : (
        <>
          <div className="pe-hero">
            <strong>Satellite analysis without code</strong>
            <span>What people write Earth Engine scripts for, as forms: open Sentinel-2, Landsat, Sentinel-1, CHIRPS and JRC data, read for your area and analysed on the server.</span>
          </div>
          <div className="ws-tabs" role="tablist">
            <button role="tab" aria-selected={view === "tools"} onClick={() => setView("tools")}>Tools</button>
            <button role="tab" aria-selected={view === "runs"} onClick={() => setView("runs")}>Runs{active ? ` (${active})` : ""}</button>
          </div>
          <ErrorText error={error} />
          {view === "tools" ? <>
            <div className="ws-gp-search"><Search size={15} aria-hidden="true" />
              <input className="ws-find" placeholder="Search: NDVI, flood, rainfall, classify…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search Poly Earth tools" />
            </div>
            <div className="ws-scroll">
              {groups.map(([g, ts]) => {
                const open = q.trim().length > 0 || !closed.includes(g);
                return (
                  <section key={g} className="ws-toolgroup">
                    <h3><button type="button" aria-expanded={open} onClick={() => setClosed(closed.includes(g) ? closed.filter((x) => x !== g) : [...closed, g])}>
                      <ChevronDown size={14} aria-hidden="true" />{g}<span className="ws-count">{ts.length}</span></button></h3>
                    {open && <ul>{ts.map((t) => (
                      <li key={t.id} className="ws-tool pe-tool">
                        <button type="button" onClick={() => setTool(t)} title={t.about}>
                          <span className="pe-tool-icon" aria-hidden="true">{ICON[t.id] ?? <Earth />}</span>
                          <span><strong>{t.name}</strong><span>{t.about}</span></span>
                        </button>
                      </li>
                    ))}</ul>}
                  </section>
                );
              })}
              {tools.length > 0 && !groups.length && <p className="ws-empty">No tool matches “{q}”.</p>}
            </div>
          </> : <div className="ws-scroll ws-panel-body"><Runs jobs={jobs} onOpen={(j) => setTool(tools.find((t) => t.id === j.tool) ?? null)} /></div>}
        </>
      )}
    </aside>
  );
}

function Runs({ jobs, onOpen }: { jobs: EarthJob[]; onOpen: (j: EarthJob) => void }) {
  if (!jobs.length) return <p className="ws-muted">Runs started in this session appear here while they work and after. Every finished run is also in Geoprocessing's history.</p>;
  return (
    <ul className="ws-runs">
      {jobs.map((j) => (
        <li key={j.id} className={j.status === "failed" ? "is-failed" : ""}>
          {j.status === "failed" ? <XCircle size={16} aria-hidden="true" /> : j.status === "done" ? <CheckCircle2 size={16} aria-hidden="true" /> : <Loader2 size={16} className="pe-spin" aria-hidden="true" />}
          <span><button type="button" className="ws-link" onClick={() => onOpen(j)}><strong>{j.tool_name}</strong></button><br />
            {j.status === "done" ? <span className="ws-runs-what">{j.layers.length ? `Made ${j.layers.map((l) => l.name).join(", ")}` : "Chart ready"} in {seconds(j.ms)}.</span>
              : j.status === "failed" ? <span className="ws-bad">Did not finish: {j.error}</span>
                : <span className="ws-muted">{j.message} ({Math.round(j.progress * 100)} %)</span>}</span>
        </li>
      ))}
    </ul>
  );
}

const shownIf = (p: ToolParam, v: Record<string, unknown>) =>
  !p.when || Object.entries(p.when).every(([k, x]) => (Array.isArray(x) ? x.includes(v[k]) : v[k] === x));

function EarthForm({ tool, projectId, layers, selection, activeId, canEdit, viewBox, job, onStarted, onShowLayer }: {
  tool: Tool; projectId: string; layers: Layer[]; selection: Record<string, number[]>; activeId: string | null; canEdit: boolean; viewBox: () => Box;
  job: EarthJob | null; onStarted: (j: EarthJob) => void; onShowLayer: (l: Layer) => void;
}) {
  const fits = (p: ToolParam) => layers.filter((l) => (p.geometry ? p.geometry.includes(l.geometry_type) : l.geometry_type !== "Raster"));
  const [v, setV] = useState<Record<string, unknown>>(() => {
    const out: Record<string, unknown> = {};
    for (const p of tool.params) {
      if (p.type === "layer") out[p.name] = p.optional ? "" : (fits(p).find((l) => l.id === activeId) ?? fits(p)[0])?.id ?? "";
      else if (p.type === "date") out[p.name] = typeof p.default === "object" && p.default && "ago" in p.default ? ago((p.default as { ago: number }).ago) : "";
      else if (p.type === "area") out[p.name] = "view";
      else if (p.default !== undefined) out[p.name] = p.default;
    }
    return out;
  });
  const [areaLayer, setAreaLayer] = useState(() => layers.find((l) => l.id === activeId && l.bbox)?.id ?? layers.find((l) => l.bbox)?.id ?? "");
  const [onlySel, setOnlySel] = useState<Record<string, boolean>>({});
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [, tick] = useState(0);
  const layerOf = (key?: string) => layers.find((l) => l.id === v[key ?? "input"]);
  const running = job && (job.status === "queued" || job.status === "running");
  const areaBox = (): Box | null => (v.bbox === "layer" ? (layers.find((l) => l.id === areaLayer)?.bbox ?? null) : viewBox());

  async function go() {
    setBusy(true); setError(null);
    const params: Record<string, unknown> = {};
    for (const p of tool.params) {
      if (!shownIf(p, v)) continue;
      if (p.type === "area") { const b = areaBox(); if (!b) { setError(new Error("Choose a layer for the area.")); setBusy(false); return; } params[p.name] = b; continue; }
      if (p.type === "layer" && !v[p.name]) continue;
      params[p.name] = v[p.name];
      if (p.type === "layer" && onlySel[p.name] && selection[String(v[p.name])]?.length) params[`${p.name}_ids`] = selection[String(v[p.name])];
    }
    try { onStarted(await api.startEarth(projectId, tool.id, params, name.trim() || undefined)); } catch (e) { setError(e); } finally { setBusy(false); }
  }
  return (
    <form className="ws-gp-form" onSubmit={(e) => { e.preventDefault(); void go(); }}>
      <div className="ws-form">
        <p className="ws-gp-about">{tool.about}</p>
        {tool.params.filter((p) => shownIf(p, v)).map((p) => {
          const id = `pe-${p.name}`;
          if (p.type === "area") {
            const b = areaBox();
            return (
              <fieldset key={p.name} className="ws-f ws-fieldset"><legend>{p.label}</legend>
                <label className="ws-check"><input type="radio" name={id} checked={v.bbox !== "layer"} onChange={() => setV({ ...v, bbox: "view" })} /> What the map shows</label>
                <label className="ws-check"><input type="radio" name={id} checked={v.bbox === "layer"} disabled={!layers.some((l) => l.bbox)} onChange={() => setV({ ...v, bbox: "layer" })} /> A layer's extent</label>
                {v.bbox === "layer" && <select aria-label="Layer for the area" value={areaLayer} onChange={(e) => setAreaLayer(e.target.value)}>
                  {layers.filter((l) => l.bbox).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select>}
                {b && <span className="ws-help">{describeBox(b)}{v.bbox !== "layer" && <> · <button type="button" className="ws-link" onClick={() => tick((n) => n + 1)}>update</button></>}</span>}
              </fieldset>
            );
          }
          if (p.type === "date") return (
            <div key={p.name} className="ws-f"><label htmlFor={id}>{p.label}</label>
              <input id={id} type="date" required max={ago(0)} value={String(v[p.name] ?? "")} onChange={(e) => setV({ ...v, [p.name]: e.target.value })} /></div>
          );
          if (p.type === "layer") {
            const ok = fits(p);
            const sel = selection[String(v[p.name])] ?? [];
            return (
              <div key={p.name} className="ws-f"><label htmlFor={id}>{p.label}</label>
                <select id={id} value={String(v[p.name] ?? "")} required={!p.optional} onChange={(e) => setV({ ...v, [p.name]: e.target.value })}>
                  <option value="">{p.optional ? "None" : ok.length ? "Choose…" : `No ${p.geometry?.map((g) => WORD[g]).join(" or ")} yet`}</option>
                  {ok.map((l) => <option key={l.id} value={l.id}>{l.name} ({l.feature_count.toLocaleString("en-GB")} {WORD[l.geometry_type]})</option>)}
                </select>
                {sel.length > 0 && <label className="ws-check"><input type="checkbox" checked={!!onlySel[p.name]} onChange={(e) => setOnlySel({ ...onlySel, [p.name]: e.target.checked })} /> Only its {sel.length.toLocaleString("en-GB")} selected</label>}
              </div>
            );
          }
          if (p.type === "number") return (
            <div key={p.name} className="ws-f"><label htmlFor={id}>{p.label}{p.unit ? ` (${p.unit})` : ""}</label>
              <input id={id} type="number" step="any" min={p.min} required value={String(v[p.name] ?? "")} onChange={(e) => setV({ ...v, [p.name]: e.target.value === "" ? "" : Number(e.target.value) })} /></div>
          );
          if (p.type === "text") return (
            <div key={p.name} className="ws-f"><label htmlFor={id}>{p.label}</label>
              <input id={id} className="ws-mono" value={String(v[p.name] ?? "")} required onChange={(e) => setV({ ...v, [p.name]: e.target.value })} spellCheck={false} /></div>
          );
          if (p.type === "bool") return (
            <label key={p.name} className="ws-check"><input type="checkbox" checked={!!v[p.name]} onChange={(e) => setV({ ...v, [p.name]: e.target.checked })} /> {p.label}</label>
          );
          if (p.type === "choice") return (
            <div key={p.name} className="ws-f"><label htmlFor={id}>{p.label}</label>
              <select id={id} value={String(v[p.name] ?? "")} onChange={(e) => setV({ ...v, [p.name]: e.target.value })}>{(p.choices ?? []).map(([x, t]) => <option key={x} value={x}>{t}</option>)}</select></div>
          );
          const of = layerOf(p.of);
          return (
            <div key={p.name} className="ws-f"><label htmlFor={id}>{p.label}{p.optional ? " (optional)" : ""}</label>
              <select id={id} value={String(v[p.name] ?? "")} required={!p.optional} onChange={(e) => setV({ ...v, [p.name]: e.target.value })}>
                <option value="">{p.optional ? "Nothing" : of ? "Choose…" : "Choose the layer first"}</option>{(of?.fields ?? []).map((f) => <option key={f.name}>{f.name}</option>)}</select></div>
          );
        })}
        <div className="ws-f"><label htmlFor="pe-out">New layer's name (optional)</label><input id="pe-out" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="Named after the analysis" /></div>
        {job && <Outcome job={job} onShowLayer={onShowLayer} />}
      </div>
      <div className="ws-run">
        <ErrorText error={error} />
        {running && <div className="pe-progress" role="status"><div className="pe-bar"><i style={{ width: `${Math.max(3, Math.round(job.progress * 100))}%` }} /></div>
          <span>{job.message}… {Math.round(job.progress * 100)} %</span></div>}
        <Button type="submit" variant="primary" busy={busy} disabled={!canEdit || !!running}>{running ? "Working…" : <><Play size={15} aria-hidden="true" />Run</>}</Button>
        {!canEdit ? <p className="ws-muted">Only editors can run analyses.</p> : <p className="ws-muted">Larger areas and longer periods take longer (often a minute or two). You can close this pane meanwhile.</p>}
      </div>
    </form>
  );
}

function Outcome({ job, onShowLayer }: { job: EarthJob; onShowLayer: (l: Layer) => void }) {
  if (job.status === "failed") return <p className="error" role="alert">Did not finish: {job.error}</p>;
  if (job.status !== "done") return null;
  return (
    <div className="pe-result">
      <p className="ws-ok" role="status"><CheckCircle2 size={16} aria-hidden="true" /><span>Done in {seconds(job.ms)}{job.layers.length ? `: ${job.layers.length} layer${job.layers.length > 1 ? "s" : ""} added to the map.` : "."}</span></p>
      {job.layers.map((l) => {
        const s = l.source.summary ?? {};
        const facts = Object.entries(s).filter(([, x]) => x !== null && x !== "" && typeof x !== "object").slice(0, 8);
        return (
          <div key={l.id} className="pe-layer">
            <button type="button" className="ws-link" onClick={() => onShowLayer(l)}><strong>{l.name}</strong></button>
            {facts.length > 0 && <dl className="ws-dl">{facts.map(([k, x]) => <div key={k} className="ws-dl-row"><dt>{k}</dt><dd>{typeof x === "number" ? x.toLocaleString("en-GB") : String(x)}</dd></div>)}</dl>}
          </div>
        );
      })}
      {job.notes.map((n) => <p key={n} className="ws-muted">{n}</p>)}
      {job.chart && <Chart chart={job.chart} />}
      {job.report && <Accuracy report={job.report} />}
    </div>
  );
}

const LINES = ["#6b3fa0", "#1a9641", "#2b83ba", "#d7191c", "#fdae61", "#00a6a6", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22"];

/** A line chart of the series (one line per place), with the values as a CSV file to download. */
export function Chart({ chart }: { chart: EarthChart }) {
  const W = 300, H = 170, L = 38, R = 8, T = 10, B = 34;
  const all = chart.series.flatMap((s) => s.values).filter((x): x is number => x != null);
  let lo = Math.min(...all), hi = Math.max(...all);
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const n = chart.x.length;
  const X = (i: number) => L + (n <= 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (n - 1));
  const Y = (y: number) => T + ((hi - y) * (H - T - B)) / (hi - lo);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => lo + f * (hi - lo));
  const every = Math.max(1, Math.ceil(n / 6));
  const fmt = (y: number) => (Math.abs(hi - lo) >= 20 ? Math.round(y).toString() : y.toFixed(Math.abs(hi - lo) >= 2 ? 1 : 2));
  const csv = () => {
    const rows = [["period", ...chart.series.map((s) => s.name)], ...chart.x.map((x, i) => [x, ...chart.series.map((s) => (s.values[i] == null ? "" : String(s.values[i])))])];
    const text = rows.map((r) => r.map((c) => (/[",\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c)).join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    a.download = `${chart.title.replace(/[^\w-]+/g, "_")}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <figure className="pe-chart">
      <figcaption><strong>{chart.title}</strong>{chart.unit && <span> ({chart.unit})</span>}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${chart.title} from ${chart.x[0]} to ${chart.x[n - 1]}`}>
        {ticks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={Y(t)} y2={Y(t)} className="pe-grid" /><text x={L - 4} y={Y(t) + 3} textAnchor="end">{fmt(t)}</text></g>)}
        {chart.x.map((x, i) => (i % every === 0 || i === n - 1) && <text key={x} x={X(i)} y={H - B + 13} textAnchor="middle">{x.length > 7 ? x.slice(2) : x}</text>)}
        {chart.series.map((s, k) => {
          const c = LINES[k % LINES.length];
          const segs: string[] = []; let cur = "";
          s.values.forEach((y, i) => { if (y == null) { if (cur) segs.push(cur); cur = ""; } else cur += `${cur ? "L" : "M"}${X(i).toFixed(1)},${Y(y).toFixed(1)}`; });
          if (cur) segs.push(cur);
          return <g key={s.name}>{segs.map((d) => <path key={d} d={d} fill="none" stroke={c} strokeWidth={1.8} />)}
            {s.values.map((y, i) => y != null && <circle key={i} cx={X(i)} cy={Y(y)} r={n > 40 ? 1.2 : 2.2} fill={c}><title>{`${s.name}, ${chart.x[i]}: ${y}`}</title></circle>)}</g>;
        })}
      </svg>
      {chart.series.length > 1 && <ul className="pe-legend">{chart.series.slice(0, 10).map((s, k) => <li key={s.name}><i style={{ background: LINES[k % LINES.length] }} />{s.name}</li>)}
        {chart.series.length > 10 && <li>and {chart.series.length - 10} more (in the CSV)</li>}</ul>}
      <Button small variant="ghost" onClick={csv}><Download size={14} aria-hidden="true" />Download CSV</Button>
    </figure>
  );
}

/** A classification's accuracy on the training places held back: the error matrix, overall accuracy, kappa, and by class. */
export function Accuracy({ report: r }: { report: EarthReport }) {
  return (
    <div className="pe-accuracy">
      <p><strong>Accuracy on the places held back</strong></p>
      <div className="pe-scores"><span><b>{r.overall ?? "–"} %</b>overall</span><span><b>{r.kappa}</b>kappa</span></div>
      <div className="pe-matrix-wrap">
        <table className="pe-matrix">
          <thead><tr><th />{r.classes.map((c) => <th key={c} scope="col">{c}</th>)}<th scope="col">Producer's %</th></tr></thead>
          <tbody>{r.classes.map((c, i) => (
            <tr key={c}><th scope="row">{c}</th>{r.matrix[i].map((n, j) => <td key={j} className={i === j ? "is-diag" : n ? "is-off" : ""}>{n}</td>)}<td>{r.producers[i] ?? "–"}</td></tr>
          ))}
            <tr><th scope="row">User's %</th>{r.users.map((u, j) => <td key={j}>{u ?? "–"}</td>)}<td /></tr></tbody>
        </table>
      </div>
      <p className="pe-matrix-note">Rows: what the place is. Columns: what it was classified as.</p>
      {r.note && <p className="ws-muted">{r.note}</p>}
    </div>
  );
}
