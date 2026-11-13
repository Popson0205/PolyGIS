import { ArrowDown, ArrowUp, CheckCircle2, Play, Plus, Save, Search, Trash2, Workflow, X, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApiError, api, type GisModel, type ModelDraft, type ModelInput, type ModelRun, type ModelStep } from "../lib/api";
import type { Layer, Tool, ToolParam } from "../lib/types";
import { Dialog } from "./Dialogs";
import { Button, ErrorText, IconButton, useToast } from "./ui";

const KINDS: ModelInput["kinds"] = ["Point", "LineString", "Polygon", "Raster"];
const WORD: Record<string, string> = { Point: "points", LineString: "lines", Polygon: "polygons", Raster: "raster" };
const EMPTY: ModelDraft = { name: "New model", description: "", inputs: [{ name: "input", label: "Input layer", kinds: ["Point", "LineString", "Polygon"] }], steps: [] };
type Sel = { kind: "input"; i: number } | { kind: "step"; i: number } | null;

/** Model builder: chain tools into a model the organization can run again on other layers. The diagram shows the
 *  inputs, the steps and what feeds each; the side panel edits the node chosen. */
export default function ModelBuilder({ projectId, layers, canEdit, rasterAllowed, onClose, onDone }: {
  projectId: string; layers: Layer[]; canEdit: boolean; rasterAllowed: boolean; onClose: () => void; onDone: (ls: Layer[]) => void;
}) {
  const toast = useToast();
  const [tools, setTools] = useState<Tool[]>([]);
  const [saved, setSaved] = useState<GisModel[]>([]);
  const [current, setCurrent] = useState<GisModel | null>(null);
  const [m, setM] = useState<ModelDraft>(EMPTY);
  const [sel, setSel] = useState<Sel>({ kind: "input", i: 0 });
  const [error, setError] = useState<unknown>(null);
  const [running, setRunning] = useState(false);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    void Promise.all([api.tools(), rasterAllowed ? api.rasterTools() : Promise.resolve([] as Tool[]), api.models()])
      .then(([v, r, ms]) => { setTools([...v, ...r.map((t) => ({ ...t, kind: "raster" as const, group: `Raster: ${t.group}` }))]); setSaved(ms); }, setError);
  }, [rasterAllowed]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !(e.target as HTMLElement)?.closest?.("input, textarea, select, [role=dialog] [role=dialog]")) onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  const change = (next: ModelDraft) => { setM(next); setDirty(true); };
  const toolOf = (s: ModelStep) => tools.find((t) => t.id === s.tool && (t.kind === "raster") === (s.kind === "raster"));

  function open(id: string) {
    const x = saved.find((y) => y.id === id) ?? null;
    if (dirty && !window.confirm("Leave this model without saving it?")) return;
    setCurrent(x); setM(x ? { name: x.name, description: x.description, inputs: x.inputs, steps: x.steps } : EMPTY); setSel(null); setDirty(false);
  }
  async function save(asNew = false) {
    setError(null);
    try {
      const out = current && !asNew ? await api.updateModel(current.id, m) : await api.createModel(asNew ? { ...m, name: `${m.name} (copy)` } : m);
      setCurrent(out); setSaved((x) => [out, ...x.filter((y) => y.id !== out.id)]); setDirty(false);
      if (asNew) setM({ ...m, name: out.name });
      toast(`${out.name} saved for your organization.`, "success");
    } catch (e) { setError(e instanceof ApiError ? e : new Error("Could not save the model.")); }
  }
  async function remove() {
    if (!current || !window.confirm(`Delete the model ${current.name}? Layers it made stay.`)) return;
    try { await api.deleteModel(current.id); setSaved((x) => x.filter((y) => y.id !== current.id)); setCurrent(null); setM(EMPTY); setDirty(false); } catch (e) { setError(e); }
  }
  function addStep() {
    const n = m.steps.length + 1;
    let id = `s${n}`;
    while (m.steps.some((s) => s.id === id)) id = `${id}_`;
    const prev = m.steps.length ? `step:${m.steps[m.steps.length - 1].id}` : m.inputs[0] ? `input:${m.inputs[0].name}` : "";
    change({ ...m, steps: [...m.steps, { id, tool: "", kind: "vector", params: { input: prev }, output_name: "" }] });
    setSel({ kind: "step", i: m.steps.length });
  }
  function addInput() {
    let n = m.inputs.length + 1, name = `input_${n}`;
    while (m.inputs.some((i) => i.name === name)) name = `input_${++n}`;
    change({ ...m, inputs: [...m.inputs, { name, label: `Input ${n}`, kinds: ["Point", "LineString", "Polygon"] }] });
    setSel({ kind: "input", i: m.inputs.length });
  }

  return (
    <div className="lo mb" role="dialog" aria-modal="true" aria-label="Model builder">
      <header className="lo-bar">
        <Workflow size={18} aria-hidden="true" />
        <strong>Model builder</strong>
        <select className="lo-pick" value={current?.id ?? ""} onChange={(e) => open(e.target.value)} aria-label="Saved models">
          <option value="">{saved.length ? "New model" : "New model (none saved yet)"}</option>
          {saved.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </select>
        <input className="mb-name" value={m.name} maxLength={200} onChange={(e) => change({ ...m, name: e.target.value })} aria-label="Model name" />
        {canEdit && <>
          <Button small onClick={() => void save()}><Save size={14} aria-hidden="true" />{current ? (dirty ? "Save" : "Saved") : "Save model"}</Button>
          {current && <Button small variant="ghost" onClick={() => void save(true)}>Save as new</Button>}
          {current && <IconButton label="Delete this model" onClick={() => void remove()}><Trash2 size={16} /></IconButton>}
        </>}
        <span className="ge-spacer" />
        <Button small variant="primary" disabled={!canEdit || !current || dirty || !m.steps.length} onClick={() => setRunning(true)}
          title={!current || dirty ? "Save the model first" : undefined}><Play size={14} aria-hidden="true" />Run…</Button>
        <IconButton label="Close the model builder" onClick={onClose}><X size={18} /></IconButton>
      </header>
      <div className="lo-body">
        <div className="mb-stage">
          <ErrorText error={error} />
          <Diagram m={m} tools={tools} sel={sel} onSel={setSel} />
          <div className="mb-add">
            <Button small onClick={addInput} disabled={!canEdit}><Plus size={14} aria-hidden="true" />Input</Button>
            <Button small variant="primary" onClick={addStep} disabled={!canEdit}><Plus size={14} aria-hidden="true" />Step</Button>
          </div>
        </div>
        <aside className="lo-side ws-scroll" aria-label="Edit the model">
          <label className="ws-f">What the model does<textarea rows={2} value={m.description} maxLength={4000} onChange={(e) => change({ ...m, description: e.target.value })}
            placeholder="e.g. Buffers boreholes by 50 m, clips them to the site and dissolves the result" /></label>
          {sel?.kind === "input" && m.inputs[sel.i] && <InputEditor m={m} i={sel.i} onChange={change} onDelete={() => { change({ ...m, inputs: m.inputs.filter((_, k) => k !== sel.i) }); setSel(null); }} />}
          {sel?.kind === "step" && m.steps[sel.i] && <StepEditor m={m} i={sel.i} tools={tools} tool={toolOf(m.steps[sel.i])} onChange={change}
            onMove={(d) => { const s = [...m.steps]; const j = sel.i + d; [s[sel.i], s[j]] = [s[j], s[sel.i]]; change({ ...m, steps: s }); setSel({ kind: "step", i: j }); }}
            onDelete={() => { change({ ...m, steps: m.steps.filter((_, k) => k !== sel.i) }); setSel(null); }} />}
          {!sel && <p className="ws-muted">Click an input or a step in the diagram to change it. Add steps with <b>+ Step</b>: each takes the layers of earlier steps or the
            model's inputs. Save the model, then <b>Run…</b> it on this project's layers; everyone in your organization can use it.</p>}
        </aside>
      </div>
      {running && current && <RunDialog projectId={projectId} model={current} layers={layers} onClose={() => setRunning(false)}
        onDone={onDone} />}
    </div>
  );
}

/** The model as a flow: inputs on top, then each step, with a line from whatever feeds it. */
function Diagram({ m, tools, sel, onSel }: { m: ModelDraft; tools: Tool[]; sel: Sel; onSel: (s: Sel) => void }) {
  const W = 200, H = 54, GX = 36, GY = 46;
  const cols = Math.max(m.inputs.length, 1);
  const width = Math.max(cols * (W + GX), W + GX * 2);
  const pos = new Map<string, [number, number]>();
  m.inputs.forEach((inp, k) => pos.set(`input:${inp.name}`, [GX / 2 + k * (W + GX) + (width - cols * (W + GX)) / 2, 16]));
  m.steps.forEach((s, k) => pos.set(`step:${s.id}`, [(width - W) / 2, 16 + (k + 1) * (H + GY)]));
  const height = 16 + (m.steps.length + 1) * (H + GY);
  const name = (s: ModelStep) => tools.find((t) => t.id === s.tool)?.name ?? (s.tool || "Choose a tool");
  const refs = (s: ModelStep) => Object.values(s.params).flatMap((v) => (Array.isArray(v) ? v : [v])).filter((v): v is string => typeof v === "string" && /^(input|step):/.test(v));
  return (
    <svg className="mb-svg" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label="The model's flow">
      <defs><marker id="mb-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="#8b93a2" /></marker></defs>
      {m.steps.flatMap((s) => refs(s).map((r) => {
        const a = pos.get(r), b = pos.get(`step:${s.id}`);
        if (!a || !b) return null;
        const [x1, y1, x2, y2] = [a[0] + W / 2, a[1] + H, b[0] + W / 2, b[1]];
        return <path key={`${s.id}-${r}`} d={`M${x1} ${y1} C${x1} ${y1 + 30}, ${x2} ${y2 - 30}, ${x2} ${y2 - 2}`} fill="none" stroke="#8b93a2" strokeWidth="1.6" markerEnd="url(#mb-arrow)" />;
      }))}
      {m.inputs.map((inp, k) => { const [x, y] = pos.get(`input:${inp.name}`)!; const on = sel?.kind === "input" && sel.i === k; return (
        <g key={inp.name} className={`mb-node is-input${on ? " is-on" : ""}`} onClick={() => onSel({ kind: "input", i: k })} role="button" tabIndex={0}
          onKeyDown={(e) => e.key === "Enter" && onSel({ kind: "input", i: k })}>
          <rect x={x} y={y} width={W} height={H} rx={27} /><text x={x + W / 2} y={y + 23}>{inp.label || inp.name}</text>
          <text className="mb-sub" x={x + W / 2} y={y + 40}>input · {inp.kinds.map((q) => WORD[q]).join(", ")}</text></g>); })}
      {m.steps.map((s, k) => { const [x, y] = pos.get(`step:${s.id}`)!; const on = sel?.kind === "step" && sel.i === k; return (
        <g key={s.id} className={`mb-node${on ? " is-on" : ""}${s.tool ? "" : " is-empty"}`} onClick={() => onSel({ kind: "step", i: k })} role="button" tabIndex={0}
          onKeyDown={(e) => e.key === "Enter" && onSel({ kind: "step", i: k })}>
          <rect x={x} y={y} width={W} height={H} rx={8} /><text x={x + W / 2} y={y + 23}>{k + 1}. {name(s)}</text>
          <text className="mb-sub" x={x + W / 2} y={y + 40}>{s.output_name || (k === m.steps.length - 1 ? "the result" : "kept for the next step")}</text></g>); })}
    </svg>
  );
}

function InputEditor({ m, i, onChange, onDelete }: { m: ModelDraft; i: number; onChange: (m: ModelDraft) => void; onDelete: () => void }) {
  const inp = m.inputs[i];
  const set = (x: Partial<ModelInput>) => {
    const next = { ...inp, ...x };
    const renamed = x.name !== undefined && x.name !== inp.name;
    // a renamed input keeps feeding the steps that used it
    const steps = renamed ? m.steps.map((s) => ({ ...s, params: Object.fromEntries(Object.entries(s.params).map(([k, v]) => [k,
      Array.isArray(v) ? v.map((r) => (r === `input:${inp.name}` ? `input:${next.name}` : r)) : v === `input:${inp.name}` ? `input:${next.name}` : v])) })) : m.steps;
    onChange({ ...m, inputs: m.inputs.map((y, k) => (k === i ? next : y)), steps });
  };
  return (
    <fieldset className="lo-set"><legend>Input</legend>
      <label className="ws-f">Label<input value={inp.label} maxLength={100} onChange={(e) => set({ label: e.target.value })} /></label>
      <label className="ws-f">Name in the model<input value={inp.name} maxLength={31} onChange={(e) => set({ name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })} />
        <span className="ws-help">Lower-case letters, digits and _</span></label>
      <div className="ws-f">Takes
        {KINDS.map((q) => <label key={q} className="lo-check"><input type="checkbox" checked={inp.kinds.includes(q)}
          onChange={(e) => set({ kinds: e.target.checked ? [...inp.kinds, q] : inp.kinds.filter((x) => x !== q) })} />{WORD[q]}</label>)}</div>
      <Button small variant="ghost" onClick={onDelete}><Trash2 size={14} aria-hidden="true" />Remove this input</Button>
    </fieldset>
  );
}

function StepEditor({ m, i, tools, tool, onChange, onMove, onDelete }: {
  m: ModelDraft; i: number; tools: Tool[]; tool: Tool | undefined; onChange: (m: ModelDraft) => void; onMove: (d: -1 | 1) => void; onDelete: () => void;
}) {
  const s = m.steps[i];
  const [q, setQ] = useState("");
  const set = (x: Partial<ModelStep>) => onChange({ ...m, steps: m.steps.map((y, k) => (k === i ? { ...y, ...x } : y)) });
  const sources = [...m.inputs.map((inp) => ({ ref: `input:${inp.name}`, label: `${inp.label || inp.name} (input)`, kinds: inp.kinds as string[] })),
    ...m.steps.slice(0, i).map((x, k) => ({ ref: `step:${x.id}`, label: `Result of step ${k + 1}${x.output_name ? `: ${x.output_name}` : ""}`, kinds: KINDS as string[] }))];
  const found = useMemo(() => {
    const w = q.toLowerCase().split(/\s+/).filter(Boolean);
    return tools.filter((t) => w.every((x) => `${t.name} ${t.group} ${t.id}`.toLowerCase().includes(x))).slice(0, 40);
  }, [tools, q]);
  const pick = (t: Tool) => {
    const params: Record<string, unknown> = {};
    for (const p of t.params) {
      if (p.type === "layer") params[p.name] = p.name === "input" ? (s.params.input ?? sources[sources.length - 1]?.ref ?? "") : "";
      else if (p.type === "layers" || p.type === "fields") params[p.name] = [];
      else if (p.default !== undefined) params[p.name] = p.default;
    }
    set({ tool: t.id, kind: t.kind === "raster" ? "raster" : "vector", params }); setQ("");
  };
  const shown = (p: ToolParam) => !p.when || Object.entries(p.when).every(([k, x]) => s.params[k] === x);
  const value = (p: ToolParam) => s.params[p.name];
  const setP = (name: string, v: unknown) => set({ params: { ...s.params, [name]: v } });
  return (
    <fieldset className="lo-set"><legend>Step {i + 1}</legend>
      {!tool ? <div className="ws-f">Tool
        <div className="ws-gp-search"><Search size={15} aria-hidden="true" /><input className="ws-find" autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a tool: buffer, clip, slope…" aria-label="Find a tool" /></div>
        <ul className="mb-tools">{found.map((t) => <li key={`${t.kind}-${t.id}`}><button type="button" onClick={() => pick(t)}><strong>{t.name}</strong><span>{t.group}</span></button></li>)}</ul>
      </div> : <>
        <p className="mb-tool"><strong>{tool.name}</strong> <button type="button" className="ws-link" onClick={() => set({ tool: "", params: {} })}>change</button><br /><span className="ws-muted">{tool.about}</span></p>
        {tool.params.filter(shown).map((p) => {
          if (p.type === "layer") {
            const ok = sources.filter((x) => !p.geometry || x.kinds.some((k) => p.geometry!.includes(k as never)));
            return <label key={p.name} className="ws-f">{p.label}<select value={String(value(p) ?? "")} onChange={(e) => setP(p.name, e.target.value)}>
              <option value="">{p.optional ? "Nothing" : "Choose…"}</option>{ok.map((x) => <option key={x.ref} value={x.ref}>{x.label}</option>)}</select></label>;
          }
          if (p.type === "layers") {
            const chosen = (value(p) as string[]) ?? [];
            return <div key={p.name} className="ws-f">{p.label}{sources.map((x) => <label key={x.ref} className="lo-check"><input type="checkbox" checked={chosen.includes(x.ref)}
              onChange={(e) => setP(p.name, e.target.checked ? [...chosen, x.ref] : chosen.filter((y) => y !== x.ref))} />{x.label}</label>)}</div>;
          }
          if (p.type === "number") return <label key={p.name} className="ws-f">{p.label}{p.unit ? ` (${p.unit === "m" ? "metres" : p.unit})` : ""}
            <input type="number" step="any" min={p.min} value={String(value(p) ?? "")} onChange={(e) => setP(p.name, e.target.value === "" ? "" : Number(e.target.value))} /></label>;
          if (p.type === "bool") return <label key={p.name} className="lo-check"><input type="checkbox" checked={!!value(p)} onChange={(e) => setP(p.name, e.target.checked)} />{p.label}</label>;
          if (p.type === "choice") return <label key={p.name} className="ws-f">{p.label}<select value={String(value(p) ?? "")} onChange={(e) => setP(p.name, e.target.value)}>
            {(p.choices ?? []).map(([x, t]) => <option key={x} value={x}>{t}</option>)}</select></label>;
          if (p.type === "fields") return <label key={p.name} className="ws-f">{p.label} (optional)
            <input value={((value(p) as string[]) ?? []).join(", ")} onChange={(e) => setP(p.name, e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} placeholder="Field names, comma-separated" />
            <span className="ws-help">The fields of the layer this step gets when the model runs</span></label>;
          return <label key={p.name} className="ws-f">{p.label}{p.optional ? " (optional)" : ""}
            <input value={String(value(p) ?? "")} onChange={(e) => setP(p.name, e.target.value)} placeholder={p.type === "field" ? "A field name" : ""} /></label>;
        })}
        <label className="ws-f">Result's name (optional)<input value={s.output_name} maxLength={200} onChange={(e) => set({ output_name: e.target.value })} placeholder="Named after the model and tool" /></label>
        {i < m.steps.length - 1 && <label className="lo-check"><input type="checkbox" checked={!!s.keep} onChange={(e) => set({ keep: e.target.checked })} />Keep this step's result too</label>}
      </>}
      <div className="ws-row">
        <IconButton label="Move up" disabled={i === 0} onClick={() => onMove(-1)}><ArrowUp size={15} /></IconButton>
        <IconButton label="Move down" disabled={i === m.steps.length - 1} onClick={() => onMove(1)}><ArrowDown size={15} /></IconButton>
        <Button small variant="ghost" onClick={onDelete}><Trash2 size={14} aria-hidden="true" />Remove this step</Button>
      </div>
    </fieldset>
  );
}

function RunDialog({ projectId, model, layers, onClose, onDone }: { projectId: string; model: GisModel; layers: Layer[]; onClose: () => void; onDone: (ls: Layer[]) => void }) {
  const [pick, setPick] = useState<Record<string, string>>(() => Object.fromEntries(model.inputs.map((i) => [i.name, layers.find((l) => i.kinds.includes(l.geometry_type as never))?.id ?? ""])));
  const [keep, setKeep] = useState<"final" | "all">("final");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<ModelRun | null>(null);
  const [error, setError] = useState<unknown>(null);
  async function go() {
    setBusy(true); setError(null); setOut(null);
    try { const r = await api.runModel(projectId, model.id, pick, keep); setOut(r); if (r.status === "done") onDone(r.layers); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  return (
    <Dialog title={`Run ${model.name}`} onClose={onClose}>
      <div className="ws-form">
        {model.description && <p className="ws-muted">{model.description}</p>}
        {model.inputs.map((i) => (
          <label key={i.name} className="ws-f">{i.label || i.name}<select value={pick[i.name] ?? ""} onChange={(e) => setPick({ ...pick, [i.name]: e.target.value })}>
            <option value="">Choose…</option>{layers.filter((l) => i.kinds.includes(l.geometry_type as never)).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
        ))}
        <label className="ws-f">Keep<select value={keep} onChange={(e) => setKeep(e.target.value as "final" | "all")}>
          <option value="final">The final result (and steps marked to keep)</option><option value="all">Every step's result</option></select></label>
        <ErrorText error={error} />
        {out && <ol className="mb-steps">{out.steps.map((s) => (
          <li key={s.id} className={s.error ? "is-failed" : ""}>{s.error ? <XCircle size={15} aria-hidden="true" /> : <CheckCircle2 size={15} aria-hidden="true" />}
            <span><strong>{s.name}</strong> <span className="ws-muted">{(s.ms / 1000).toFixed(1)} s{s.features != null ? ` · ${s.features.toLocaleString("en-GB")} ${s.features === 1 ? "feature" : "features"}` : ""}</span>
              {s.error && <><br /><span className="ws-bad">{s.error}</span></>}</span></li>))}</ol>}
        {out?.status === "failed" && <p className="ws-bad">{out.message}</p>}
        <div className="ws-row">
          <Button variant="primary" busy={busy} disabled={model.inputs.some((i) => !pick[i.name])} onClick={() => void go()}><Play size={15} aria-hidden="true" />{busy ? "Running…" : "Run"}</Button>
          <Button variant="ghost" onClick={onClose}>{out?.status === "done" ? "Close" : "Cancel"}</Button>
        </div>
      </div>
    </Dialog>
  );
}
