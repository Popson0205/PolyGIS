import { ArrowLeft, BarChart3, Copy, Eye, FlaskConical, Play, Plus, Trash2, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../lib/api";
import { KINDS, compareRows, kindLabel, numberList } from "../lib/scenarios";
import type { GeometryType, Layer, RasterStyle, Scenario, ScenarioKind } from "../lib/types";
import { Button, ErrorText, IconButton, useToast } from "./ui";

type Params = Record<string, unknown>;
type View = { kind: "list" } | { kind: "edit"; id: string } | { kind: "compare"; of: ScenarioKind };
const STATUS: Record<Scenario["status"], string> = { draft: "Not run yet", ready: "Ready", failed: "Failed" };
const DEFAULTS: Record<ScenarioKind, Params> = {
  suitability: { criteria: [], exclude: [], cell: 30, top_percent: 10 },
  flood: { method: "level", levels: [1, 2, 3], assets: [] },
  land_change: { before_year: 2015, after_year: 2023, years: [2030, 2040], protect: [], drivers: [], neighbourhood: 5 },
  spread: { speed: 500, hours: [1, 3, 6], cell: 30, barriers: [], assets: [], wind_from: 0, wind_strength: 0 },
};

/** What-if scenarios: made, run, varied and compared, each drawn as a layer with a band per step. */
export default function Scenarios({ projectId, layers, canEdit, onClose, onLayer }: {
  projectId: string; layers: Layer[]; canEdit: boolean; onClose: () => void; onLayer: (l: Layer, show: boolean) => void;
}) {
  const toast = useToast();
  const [list, setList] = useState<Scenario[] | null>(null);
  const [view, setView] = useState<View>({ kind: "list" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { void api.scenarios(projectId).then(setList, (e) => { setError(e); setList([]); }); }, [projectId]);
  const put = (s: Scenario) => setList((cur) => (cur ?? []).some((x) => x.id === s.id) ? (cur ?? []).map((x) => (x.id === s.id ? s : x)) : [...(cur ?? []), s]);

  async function make(kind: ScenarioKind) {
    setError(null);
    const n = (list ?? []).filter((s) => s.kind === kind).length + 1;
    try { const s = await api.scenarioCreate(projectId, { kind, name: `${kindLabel(kind)} ${n}`, params: DEFAULTS[kind] }); put(s); setView({ kind: "edit", id: s.id }); }
    catch (e) { setError(e); }
  }
  const current = view.kind === "edit" ? list?.find((s) => s.id === view.id) : undefined;

  return (
    <aside className="ws-panel sc" aria-label="Scenarios">
      <div className="ws-pane-head">
        {view.kind !== "list" && <IconButton label="Back to all scenarios" onClick={() => setView({ kind: "list" })}><ArrowLeft size={16} /></IconButton>}
        <FlaskConical size={16} aria-hidden="true" className="ws-pane-icon" />
        <strong>{view.kind === "compare" ? `Compare: ${kindLabel(view.of)}` : current ? current.name : "Scenarios"}</strong>
        <IconButton label="Close scenarios" onClick={onClose}><X size={16} /></IconButton>
      </div>
      <div className="ws-scroll ws-panel-body sc-body">
        <ErrorText error={error} />
        {view.kind === "list" && (list == null ? <p className="ws-muted">Loading…</p> : <>
          <p className="ws-muted">Make what-if scenarios, run them, then duplicate one to try different settings and compare the results.</p>
          {KINDS.map((k) => {
            const mine = list.filter((s) => s.kind === k.id);
            return (
              <section key={k.id} className="sc-kind">
                <header><h3>{k.label}</h3>
                  {mine.filter((s) => s.status === "ready").length >= 2 && <Button small variant="ghost" onClick={() => setView({ kind: "compare", of: k.id })}><BarChart3 size={14} aria-hidden="true" />Compare</Button>}
                  {canEdit && <Button small onClick={() => void make(k.id)}><Plus size={14} aria-hidden="true" />New</Button>}</header>
                {!mine.length && <p className="ws-muted">{k.blurb}</p>}
                <ul>{mine.map((s) => <li key={s.id}><button type="button" onClick={() => setView({ kind: "edit", id: s.id })}>
                  <span>{s.name}</span><span className={`sc-status is-${s.status}`}>{STATUS[s.status]}</span></button></li>)}</ul>
              </section>
            );
          })}
        </>)}
        {current && <Editor key={current.id} s={current} layers={layers} canEdit={canEdit} busy={busy === current.id}
          onSaved={put}
          onRun={async () => {
            setBusy(current.id); setError(null);
            try {
              const r = await api.scenarioRun(current.id);
              put(r.scenario); onLayer(r.layer, true);
              toast(`${r.scenario.name} ran in ${((r.scenario.run_ms ?? 0) / 1000).toFixed(1)} s.`, "success");
            } catch (e) {
              if (e instanceof ApiError) put({ ...current, status: "failed", error: e.message });
              setError(e);
            } finally { setBusy(null); }
          }}
          onDuplicate={async () => { try { const v = await api.scenarioDuplicate(current.id); put(v); setView({ kind: "edit", id: v.id }); } catch (e) { setError(e); } }}
          onDelete={async () => {
            if (!window.confirm(`Delete the scenario ${current.name}? Its layer stays on the map.`)) return;
            try { await api.scenarioDelete(current.id); setList((c) => (c ?? []).filter((x) => x.id !== current.id)); setView({ kind: "list" }); } catch (e) { setError(e); }
          }}
          onLayer={onLayer} />}
        {view.kind === "compare" && list && <Compare of={view.of} list={list.filter((s) => s.kind === view.of && s.status === "ready")} layers={layers}
          onShow={(id) => { const l = layers.find((x) => x.id === id); if (l) onLayer(l, true); }} />}
      </div>
    </aside>
  );
}

// =====================================================================================================================
// Editing one scenario
// =====================================================================================================================
function Editor({ s, layers, canEdit, busy, onSaved, onRun, onDuplicate, onDelete, onLayer }: {
  s: Scenario; layers: Layer[]; canEdit: boolean; busy: boolean; onSaved: (s: Scenario) => void; onRun: () => Promise<void>;
  onDuplicate: () => void; onDelete: () => void; onLayer: (l: Layer, show: boolean) => void;
}) {
  const [name, setName] = useState(s.name);
  const [p, setP] = useState<Params>(s.params);
  const [saveError, setSaveError] = useState<unknown>(null);
  const first = useRef(true);
  // settings are saved shortly after each change
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    if (!canEdit) return;
    const t = window.setTimeout(() => { void api.scenarioUpdate(s.id, { name: name.trim() || s.name, params: p }).then((x) => { onSaved(x); setSaveError(null); }, setSaveError); }, 500);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, p]);
  const set = (k: string, v: unknown) => setP((o) => ({ ...o, [k]: v }));
  const layer = layers.find((l) => l.id === s.layer_id);
  const form: Record<ScenarioKind, ReactNode> = {
    suitability: <SuitabilityForm p={p} set={set} layers={layers} />,
    flood: <FloodForm p={p} set={set} layers={layers} />,
    land_change: <LandForm p={p} set={set} layers={layers} />,
    spread: <SpreadForm p={p} set={set} layers={layers} />,
  };
  return (
    <div className="sc-edit">
      <label className="ws-f">Scenario name<input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} disabled={!canEdit} /></label>
      <fieldset className="sc-form" disabled={!canEdit}>{form[s.kind]}</fieldset>
      <ErrorText error={saveError} />
      {s.status === "failed" && s.error && <p className="ws-error" role="alert">{s.error}</p>}
      <div className="ws-row sc-actions">
        <Button variant="primary" busy={busy} disabled={!canEdit} onClick={() => void onRun()}><Play size={14} aria-hidden="true" />{s.status === "ready" ? "Run again" : "Run"}</Button>
        {canEdit && <Button small variant="ghost" onClick={onDuplicate}><Copy size={14} aria-hidden="true" />Duplicate as a variant</Button>}
        {canEdit && <IconButton label="Delete this scenario" onClick={onDelete}><Trash2 size={15} /></IconButton>}
      </div>
      {s.status === "ready" && s.summary && <Results s={s} layer={layer} onLayer={onLayer} />}
    </div>
  );
}

function Results({ s, layer, onLayer }: { s: Scenario; layer?: Layer; onLayer: (l: Layer, show: boolean) => void }) {
  const steps = s.summary?.steps ?? [];
  const band = layer?.geometry_type === "Raster" ? Number((layer.style as unknown as { band?: number }).band ?? steps.length) : steps.length;
  const [err, setErr] = useState<unknown>(null);
  async function pick(b: number) {
    if (!layer) return;
    try { onLayer(await api.rasterStyle(layer.id, { ...(layer.style as unknown as RasterStyle), band: b }), false); } catch (e) { setErr(e); }
  }
  const rows = compareRows([s], ((s.summary?.labels as Record<string, string>) ?? {}));
  return (
    <section className="sc-results" aria-label="Results">
      <h3>Results <span className="ws-muted">{s.ran_at ? new Date(s.ran_at).toLocaleString() : ""}</span></h3>
      {layer ? <div className="ws-row"><Button small variant="ghost" onClick={() => onLayer(layer, true)}><Eye size={14} aria-hidden="true" />Show on the map</Button></div>
        : <p className="ws-muted">Its layer was removed; run it again to redraw it.</p>}
      {steps.length > 1 && layer && <label className="ws-f">Step on the map: <strong>{steps[band - 1]?.label}</strong>
        <input type="range" min={1} max={steps.length} step={1} value={band} onChange={(e) => void pick(Number(e.target.value))} aria-valuetext={steps[band - 1]?.label} /></label>}
      <ErrorText error={err} />
      <table className="sc-table"><tbody>
        {rows.map((r, i) => (
          <Fragment key={i}>
            {(i === 0 || rows[i - 1].group !== r.group) && <tr className="sc-grp"><th colSpan={2} scope="colgroup">{r.group}</th></tr>}
            <tr><th scope="row">{r.label}</th>
              <td>{r.values[0] == null ? "–" : r.values[0].toLocaleString("en-GB", { maximumFractionDigits: 2 })}</td></tr>
          </Fragment>
        ))}
      </tbody></table>
    </section>
  );
}

// =====================================================================================================================
// Comparing
// =====================================================================================================================
function Compare({ of, list, layers, onShow }: { of: ScenarioKind; list: Scenario[]; layers: Layer[]; onShow: (layerId: string) => void }) {
  const [chosen, setChosen] = useState<string[]>(() => list.slice(0, 4).map((s) => s.id));
  const picked = list.filter((s) => chosen.includes(s.id));
  const names = useMemo(() => Object.assign({}, ...list.map((s) => (s.summary?.labels as Record<string, string>) ?? {})), [list]);
  const rows = compareRows(picked, names);
  return (
    <div className="sc-compare">
      <fieldset className="ws-fieldset"><legend>Scenarios ({kindLabel(of)})</legend>
        {list.map((s) => <label key={s.id} className="ws-check"><input type="checkbox" checked={chosen.includes(s.id)}
          onChange={(e) => setChosen((c) => (e.target.checked ? [...c, s.id] : c.filter((x) => x !== s.id)))} /> {s.name}</label>)}
      </fieldset>
      {picked.length < 2 ? <p className="ws-muted">Choose two or more.</p> : (
        <div className="sc-wide">
          <table className="sc-table sc-cmp">
            <thead><tr><th />{picked.map((s) => <th key={s.id} scope="col">{s.name}
              {s.layer_id && layers.some((l) => l.id === s.layer_id) && <button type="button" className="ws-link" onClick={() => onShow(s.layer_id!)}>show</button>}</th>)}</tr></thead>
            <tbody>
              {rows.map((r, i) => {
                const nums = r.values.filter((v): v is number => v != null);
                const top = Math.max(...nums.map(Math.abs), 0) || 1;
                return (
                  <Fragment key={i}>
                  {(i === 0 || rows[i - 1].group !== r.group) && <tr className="sc-grp"><th colSpan={picked.length + 1} scope="colgroup">{r.group}</th></tr>}
                  <tr>
                    <th scope="row">{r.label}</th>
                    {r.values.map((v, j) => <td key={j}>
                      {v == null ? "–" : <><span className="sc-val">{v.toLocaleString("en-GB", { maximumFractionDigits: 2 })}</span>
                        <span className="sc-bar" aria-hidden="true"><i style={{ width: `${(Math.abs(v) / top) * 100}%` }} /></span></>}</td>)}
                  </tr>
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// =====================================================================================================================
// Settings forms
// =====================================================================================================================
type FormProps = { p: Params; set: (k: string, v: unknown) => void; layers: Layer[] };
const isR = (l?: Layer) => l?.geometry_type === "Raster";
const ofType = (layers: Layer[], types: (GeometryType | "Raster")[]) => layers.filter((l) => types.includes(l.geometry_type as GeometryType));
const VECTORS: GeometryType[] = ["Point", "LineString", "Polygon"];

function LayerPick({ label, value, onChange, layers, optional, help }: { label: string; value: unknown; onChange: (v: string | null) => void; layers: Layer[]; optional?: boolean; help?: string }) {
  return (
    <label className="ws-f">{label}{optional ? " (optional)" : ""}
      <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">{layers.length ? (optional ? "None" : "Choose…") : "No suitable layers in this project"}</option>
        {layers.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>{help && <span className="ws-help">{help}</span>}</label>
  );
}

function LayerChecks({ label, value, onChange, layers, help }: { label: string; value: unknown; onChange: (v: string[]) => void; layers: Layer[]; help?: string }) {
  const chosen = (value as string[] | undefined) ?? [];
  return (
    <fieldset className="ws-f ws-fieldset"><legend>{label} (optional)</legend>
      {!layers.length && <span className="ws-muted">No suitable layers in this project.</span>}
      {layers.map((l) => <label key={l.id} className="ws-check"><input type="checkbox" checked={chosen.includes(l.id)}
        onChange={(e) => onChange(e.target.checked ? [...chosen, l.id] : chosen.filter((x) => x !== l.id))} /> {l.name}</label>)}
      {help && <span className="ws-help">{help}</span>}
    </fieldset>
  );
}

function Num({ label, value, onChange, step, help, unit }: { label: string; value: unknown; onChange: (n: number | "") => void; step?: number; help?: string; unit?: string }) {
  return (
    <label className="ws-f">{label}{unit ? ` (${unit})` : ""}
      <input type="number" step={step ?? "any"} value={value === undefined || value === null ? "" : String(value)} onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />
      {help && <span className="ws-help">{help}</span>}</label>
  );
}

function NumList({ label, value, onChange, help }: { label: string; value: unknown; onChange: (v: number[] | string) => void; help?: string }) {
  const [text, setText] = useState(Array.isArray(value) ? value.join(", ") : String(value ?? ""));
  return (
    <label className="ws-f">{label}<input value={text} onChange={(e) => { setText(e.target.value); onChange(numberList(e.target.value)); }} placeholder="e.g. 1, 2, 3" />
      {help && <span className="ws-help">{help}</span>}</label>
  );
}

type Crit = { layer?: string; rule?: string; weight?: number; distance?: number; min?: number; max?: number };

function CriteriaList({ label, value, onChange, layers, drivers }: { label: string; value: unknown; onChange: (v: Crit[]) => void; layers: Layer[]; drivers?: boolean }) {
  const list = (value as Crit[] | undefined) ?? [];
  const edit = (i: number, x: Partial<Crit>) => onChange(list.map((c, j) => (j === i ? { ...c, ...x } : c)));
  return (
    <fieldset className="ws-f ws-fieldset sc-crit"><legend>{label}</legend>
      {list.map((c, i) => {
        const l = layers.find((x) => x.id === c.layer);
        const rules = isR(l) ? [["higher", "Higher is better"], ["lower", "Lower is better"], ...(drivers ? [] : [["between", "Best between two values"]])]
          : [["near", "Closer is better"], ["far", "Further is better"]];
        return (
          <div key={i} className="sc-crit-row">
            <select aria-label={`Criterion ${i + 1}: layer`} value={c.layer ?? ""} onChange={(e) => {
              const nl = layers.find((x) => x.id === e.target.value);
              edit(i, { layer: e.target.value, rule: isR(nl) ? "higher" : "near" });
            }}>
              <option value="">Choose a layer…</option>
              {layers.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
            <select aria-label={`Criterion ${i + 1}: scoring`} value={c.rule ?? rules[0][0]} onChange={(e) => edit(i, { rule: e.target.value })}>
              {rules.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
            </select>
            <label className="sc-w">Weight<input type="number" min={0} step="any" value={c.weight ?? 1} onChange={(e) => edit(i, { weight: Number(e.target.value) })} /></label>
            {l && !isR(l) && <label className="sc-w">Within (m)<input type="number" min={1} value={c.distance ?? 5000} onChange={(e) => edit(i, { distance: Number(e.target.value) })} /></label>}
            {c.rule === "between" && <>
              <label className="sc-w">From<input type="number" step="any" value={c.min ?? 0} onChange={(e) => edit(i, { min: Number(e.target.value) })} /></label>
              <label className="sc-w">To<input type="number" step="any" value={c.max ?? 1} onChange={(e) => edit(i, { max: Number(e.target.value) })} /></label></>}
            <IconButton label={`Remove criterion ${i + 1}`} onClick={() => onChange(list.filter((_, j) => j !== i))}><Trash2 size={14} /></IconButton>
          </div>
        );
      })}
      <Button small variant="ghost" onClick={() => onChange([...list, { weight: 1 }])}><Plus size={14} aria-hidden="true" />Add {drivers ? "a driver" : "a criterion"}</Button>
    </fieldset>
  );
}

function SuitabilityForm({ p, set, layers }: FormProps) {
  const crit = (p.criteria as Crit[] | undefined) ?? [];
  const anyRaster = crit.some((c) => isR(layers.find((l) => l.id === c.layer)));
  return <>
    <CriteriaList label="Criteria" value={p.criteria} onChange={(v) => set("criteria", v)} layers={layers} />
    <p className="ws-help">Rasters (slope, elevation, rainfall…) are scored by their values; vector layers (roads, rivers, schools…) by distance. Weights say how much each counts.</p>
    <LayerChecks label="Leave out these areas" value={p.exclude} onChange={(v) => set("exclude", v)} layers={ofType(layers, ["Polygon", "Raster"])}
      help="Protected land, water, flood zones: polygons, or rasters where any value other than 0 is left out." />
    <LayerPick label="Study area" optional value={p.area} onChange={(v) => set("area", v)} layers={ofType(layers, ["Polygon"])} />
    {!anyRaster && <Num label="Cell size" unit="m" value={p.cell} onChange={(v) => set("cell", v)} />}
    <Num label="The best share of the land" unit="%" value={p.top_percent} onChange={(v) => set("top_percent", v)} help="Counted as the best sites in the results." />
  </>;
}

function FloodForm({ p, set, layers }: FormProps) {
  const m = String(p.method ?? "level");
  return <>
    <LayerPick label="Elevation model" value={p.dem} onChange={(v) => set("dem", v)} layers={ofType(layers, ["Raster"])} />
    <label className="ws-f">How the water rises<select value={m} onChange={(e) => set("method", e.target.value)}>
      <option value="level">A water level (lake, reservoir, river reach)</option><option value="sea_rise">The sea rising</option>
      <option value="river">A river stage above the nearest stream</option></select></label>
    <NumList key={m} label={m === "level" ? "Water levels (m above datum)" : m === "sea_rise" ? "Sea-level rise (m)" : "Stage above the stream (m)"}
      value={p.levels} onChange={(v) => set("levels", v)} help="Several values make steps you can move between on the map and compare." />
    {m !== "river" && <LayerPick label={m === "sea_rise" ? "Where the sea is" : "Where the water comes from"} optional value={p.sources} onChange={(v) => set("sources", v)}
      layers={ofType(layers, VECTORS)} help={m === "sea_rise" ? "Leave empty to take land at or below today's sea level as the sea." : "Water spreads only from here; leave empty to flood every place below the level."} />}
    {m === "sea_rise" && <><Num label="Storm surge on top" unit="m" value={p.surge ?? 0} onChange={(v) => set("surge", v)} />
      <Num label="Today's sea level" unit="m" value={p.sea_level ?? 0} onChange={(v) => set("sea_level", v)} /></>}
    {m === "river" && <Num label="Streams drain at least" unit="km²" value={p.stream_km2 ?? 1} onChange={(v) => set("stream_km2", v)} help="Smaller finds smaller streams." />}
    <LayerChecks label="Count what is reached" value={p.assets} onChange={(v) => set("assets", v)} layers={ofType(layers, VECTORS)} help="Buildings, roads, schools, farms…" />
    <LayerPick label="Population raster" optional value={p.population} onChange={(v) => set("population", v)} layers={ofType(layers, ["Raster"])} help="People per cell, to count those affected." />
  </>;
}

function LandForm({ p, set, layers }: FormProps) {
  const rasters = ofType(layers, ["Raster"]);
  const after = layers.find((l) => l.id === p.after);
  const cats = ((after?.style as unknown as { categories?: { value: number; label: string }[] } | undefined)?.categories) ?? [];
  const demand = (p.demand as Record<string, number> | undefined) ?? {};
  return <>
    <div className="sc-pair"><LayerPick label="Earlier land cover" value={p.before} onChange={(v) => set("before", v)} layers={rasters} />
      <Num label="Its year" value={p.before_year} onChange={(v) => set("before_year", v)} step={1} /></div>
    <div className="sc-pair"><LayerPick label="Later land cover" value={p.after} onChange={(v) => set("after", v)} layers={rasters} />
      <Num label="Its year" value={p.after_year} onChange={(v) => set("after_year", v)} step={1} /></div>
    <NumList label="Years to project" value={p.years} onChange={(v) => set("years", v)} help="How each class changed between the two maps carries on into these years." />
    <CriteriaList label="Drivers of change (optional)" value={p.drivers} onChange={(v) => set("drivers", v)} layers={layers} drivers />
    <p className="ws-help">Change happens first next to the same land cover, and more where drivers say (near roads, on flat land…).</p>
    <LayerChecks label="Protected areas (no change)" value={p.protect} onChange={(v) => set("protect", v)} layers={ofType(layers, ["Polygon", "Raster"])} />
    {cats.length > 0 && <fieldset className="ws-f ws-fieldset"><legend>Growth policy (× the trend)</legend>
      {cats.map((c) => <label key={c.value} className="sc-w sc-demand">{c.label}<input type="number" min={0} max={10} step={0.1} value={demand[String(c.value)] ?? 1}
        onChange={(e) => set("demand", { ...demand, [String(c.value)]: Number(e.target.value) })} /></label>)}
      <span className="ws-help">1 keeps the trend; 2 doubles how fast a class grows; 0 stops it growing.</span></fieldset>}
    <Num label="Neighbourhood" unit="cells" value={p.neighbourhood ?? 5} onChange={(v) => set("neighbourhood", v)} step={2} />
  </>;
}

function SpreadForm({ p, set, layers }: FormProps) {
  return <>
    <LayerPick label="Where it starts" value={p.sources} onChange={(v) => set("sources", v)} layers={ofType(layers, VECTORS)} />
    <Num label="Speed" unit="metres per hour" value={p.speed} onChange={(v) => set("speed", v)} />
    <NumList label="Times to show (hours)" value={p.hours} onChange={(v) => set("hours", v)} />
    <LayerPick label="Friction raster" optional value={p.friction} onChange={(v) => set("friction", v)} layers={ofType(layers, ["Raster"])}
      help="1 is normal; 2 twice as slow; 0 or no data cannot be crossed (fuel, land cover, slope cost…)." />
    {!p.friction && <Num label="Cell size" unit="m" value={p.cell} onChange={(v) => set("cell", v)} />}
    <div className="sc-pair"><Num label="Wind from" unit="°" value={p.wind_from} onChange={(v) => set("wind_from", v)} />
      <Num label="Wind strength (0–0.95)" value={p.wind_strength} onChange={(v) => set("wind_strength", v)} step={0.05} /></div>
    <LayerChecks label="Barriers" value={p.barriers} onChange={(v) => set("barriers", v)} layers={ofType(layers, ["LineString", "Polygon"])} help="Rivers, firebreaks, roads it cannot cross." />
    <LayerPick label="Study area" optional value={p.area} onChange={(v) => set("area", v)} layers={ofType(layers, ["Polygon"])} />
    <LayerChecks label="Count what is reached" value={p.assets} onChange={(v) => set("assets", v)} layers={ofType(layers, VECTORS)} />
  </>;
}
