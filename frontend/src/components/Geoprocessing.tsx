import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { Layer, Run, Tool, ToolParam } from "../lib/types";
import { Button, ErrorText } from "./ui";

const WORD: Record<string, string> = { Point: "points", LineString: "lines", Polygon: "polygons" };
const when = (d: string) => new Date(d).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** The geoprocessing pane: find a tool, fill in its form, run it; the result arrives as a new layer. */
export default function Geoprocessing({ projectId, layers, selection, activeId, allowed, rasterAllowed, canEdit, start, onClose, onDone }: {
  projectId: string; layers: Layer[]; selection: Record<string, number[]>; activeId: string | null; allowed: boolean; rasterAllowed?: boolean; canEdit: boolean;
  start?: string | null; onClose: () => void; onDone: (l: Layer) => void;
}) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [view, setView] = useState<"list" | "history">("list");
  const [q, setQ] = useState("");
  const [tool, setTool] = useState<Tool | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    void Promise.all([api.tools(), rasterAllowed ? api.rasterTools() : Promise.resolve([] as Tool[])])
      .then(([v, r]) => setTools([...v, ...r.map((t) => ({ ...t, kind: "raster" as const, group: `Raster: ${t.group}` }))]), setError);
  }, [rasterAllowed]);
  useEffect(() => { if (start && tools.length) setTool(tools.find((t) => t.id === start) ?? null); }, [start, tools]);

  const groups = useMemo(() => {
    const hit = tools.filter((t) => !q || `${t.name} ${t.about} ${t.group}`.toLowerCase().includes(q.toLowerCase()));
    return Array.from(new Set(hit.map((t) => t.group))).map((g) => [g, hit.filter((t) => t.group === g)] as const);
  }, [tools, q]);

  return (
    <aside className="ws-panel" aria-label="Geoprocessing">
      <div className="ws-pane-head"><strong>{tool ? tool.name : "Geoprocessing"}</strong><button type="button" onClick={onClose} aria-label="Close geoprocessing">✕</button></div>
      {!allowed ? (
        <div className="ws-panel-body"><p>Geoprocessing tools are in <b>PolyGIS Advanced</b>.</p><p className="ws-muted">Ask GeoEstate NIG Limited to move your organization to Advanced.</p></div>
      ) : tool ? (
        <ToolForm key={tool.id} tool={tool} projectId={projectId} layers={layers} selection={selection} activeId={activeId} canEdit={canEdit}
          onBack={() => setTool(null)} onDone={onDone} />
      ) : (
        <>
          <div className="ws-tabs" role="tablist">
            <button role="tab" aria-selected={view === "list"} onClick={() => setView("list")}>Tools</button>
            <button role="tab" aria-selected={view === "history"} onClick={() => setView("history")}>History</button>
          </div>
          <ErrorText error={error} />
          {view === "list" ? (
            <div className="ws-panel-body">
              <input className="ws-find" placeholder={`Find one of ${tools.length || ""} tools: buffer, hot spot, route…`} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find a tool" />
              {groups.map(([g, ts]) => (
                <section key={g} className="ws-toolgroup"><h3>{g}</h3>
                  <ul>{ts.map((t) => <li key={t.id}><button type="button" onClick={() => setTool(t)}><strong>{t.name}</strong><span>{t.about}</span></button></li>)}</ul>
                </section>
              ))}
              {!groups.length && <p className="ws-muted">No tool matches.</p>}
            </div>
          ) : <History projectId={projectId} tools={tools} />}
        </>
      )}
    </aside>
  );
}

function ToolForm({ tool, projectId, layers, selection, activeId, canEdit, onBack, onDone }: {
  tool: Tool; projectId: string; layers: Layer[]; selection: Record<string, number[]>; activeId: string | null; canEdit: boolean;
  onBack: () => void; onDone: (l: Layer) => void;
}) {
  // a raster parameter takes rasters only; any other layer parameter takes vector layers only
  const fits = (p: ToolParam) => layers.filter((l) => (p.geometry ? p.geometry.includes(l.geometry_type) : l.geometry_type !== "Raster"));
  const [v, setV] = useState<Record<string, unknown>>(() => {
    const out: Record<string, unknown> = {};
    for (const p of tool.params) {
      if (p.type === "layer") {
        const ok = fits(p);
        out[p.name] = (p.name === "input" && ok.find((l) => l.id === activeId)?.id) || ok[p.name === "input" ? 0 : Math.min(1, ok.length - 1)]?.id || "";
      } else if (p.type === "layers") out[p.name] = [];
      else if (p.type === "fields") out[p.name] = [];
      else if (p.default !== undefined) out[p.name] = p.default;
    }
    return out;
  });
  const [onlySel, setOnlySel] = useState<Record<string, boolean>>({});
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);
  const layerOf = (key?: string) => layers.find((l) => l.id === v[key ?? "input"]);
  const shown = (p: ToolParam) => !p.when || Object.entries(p.when).every(([k, x]) => v[k] === x);

  async function go() {
    setBusy(true); setError(null); setDone(null);
    const params: Record<string, unknown> = { ...v };
    for (const p of tool.params) if (p.type === "layer" && onlySel[p.name] && selection[String(v[p.name])]?.length) params[`${p.name}_ids`] = selection[String(v[p.name])];
    try {
      const r = await (tool.kind === "raster" ? api.runRasterTool : api.runTool)(projectId, tool.id, params, name.trim() || undefined);
      const what = r.layer.raster ? `a raster of ${r.layer.raster.width.toLocaleString("en-GB")} × ${r.layer.raster.height.toLocaleString("en-GB")} cells`
        : `${r.layer.feature_count.toLocaleString("en-GB")} ${WORD[r.layer.geometry_type]}`;
      setDone(`${r.layer.name}: ${what}, in ${((r.run.ms ?? 0) / 1000).toFixed(1)} s.`);
      onDone(r.layer);
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  return (
    <form className="ws-form ws-panel-body" onSubmit={(e) => { e.preventDefault(); void go(); }}>
      <button type="button" className="ws-link" onClick={onBack}>‹ All tools</button>
      <p className="ws-muted">{tool.about}</p>
      {tool.params.filter(shown).map((p) => {
        const id = `tp-${p.name}`;
        if (p.type === "layer") {
          const ok = fits(p);
          const sel = selection[String(v[p.name])] ?? [];
          return (
            <div key={p.name} className="ws-f"><label htmlFor={id}>{p.label}</label>
              <select id={id} value={String(v[p.name] ?? "")} onChange={(e) => setV({ ...v, [p.name]: e.target.value })} required>
                <option value="">{ok.length ? "Choose…" : `No ${p.geometry?.map((g) => WORD[g]).join(" or ") ?? "layers"} yet`}</option>
                {ok.map((l) => <option key={l.id} value={l.id}>{l.name} ({l.feature_count.toLocaleString("en-GB")} {WORD[l.geometry_type]})</option>)}
              </select>
              {sel.length > 0 && <label className="ws-check"><input type="checkbox" checked={!!onlySel[p.name]} onChange={(e) => setOnlySel({ ...onlySel, [p.name]: e.target.checked })} /> Only its {sel.length.toLocaleString("en-GB")} selected</label>}
            </div>
          );
        }
        if (p.type === "layers") {
          const chosen = (v[p.name] as string[]) ?? [];
          return (
            <fieldset key={p.name} className="ws-f ws-fieldset"><legend>{p.label}</legend>
              {fits(p).map((l) => (
                <label key={l.id} className="ws-check"><input type="checkbox" checked={chosen.includes(l.id)}
                  onChange={(e) => setV({ ...v, [p.name]: e.target.checked ? [...chosen, l.id] : chosen.filter((x) => x !== l.id) })} /> {l.name} <span className="ws-muted">({WORD[l.geometry_type]})</span></label>
              ))}
            </fieldset>
          );
        }
        if (p.type === "number") return (
          <div key={p.name} className="ws-f"><label htmlFor={id}>{p.label}{p.unit ? ` (${p.unit === "m" ? "metres" : p.unit})` : ""}</label>
            <input id={id} type="number" step="any" min={p.min} required value={String(v[p.name] ?? "")} onChange={(e) => setV({ ...v, [p.name]: e.target.value === "" ? "" : Number(e.target.value) })} /></div>
        );
        if (p.type === "text") return (
          <div key={p.name} className="ws-f"><label htmlFor={id}>{p.label}{p.optional ? " (optional)" : ""}</label>
            <input id={id} value={String(v[p.name] ?? "")} required={!p.optional} onChange={(e) => setV({ ...v, [p.name]: e.target.value })} /></div>
        );
        if (p.type === "bool") return (
          <label key={p.name} className="ws-check"><input type="checkbox" checked={!!v[p.name]} onChange={(e) => setV({ ...v, [p.name]: e.target.checked })} /> {p.label}</label>
        );
        if (p.type === "choice") return (
          <div key={p.name} className="ws-f"><label htmlFor={id}>{p.label}</label>
            <select id={id} value={String(v[p.name] ?? "")} onChange={(e) => setV({ ...v, [p.name]: e.target.value })}>{(p.choices ?? []).map(([x, t]) => <option key={x} value={x}>{t}</option>)}</select></div>
        );
        const of = layerOf(p.of);
        const fields = (of?.fields ?? []).filter((f) => !p.numeric || f.type === "integer" || f.type === "number");
        if (p.type === "field") return (
          <div key={p.name} className="ws-f"><label htmlFor={id}>{p.label}{p.optional ? " (optional)" : ""}</label>
            <select id={id} value={String(v[p.name] ?? "")} onChange={(e) => setV({ ...v, [p.name]: e.target.value })}>
              <option value="">{p.optional ? "Nothing" : "Choose…"}</option>{fields.map((f) => <option key={f.name}>{f.name}</option>)}</select></div>
        );
        const chosen = (v[p.name] as string[]) ?? [];
        return (
          <fieldset key={p.name} className="ws-f ws-fieldset"><legend>{p.label}{p.optional ? " (optional)" : ""}</legend>
            {!fields.length && <span className="ws-muted">{of ? `${of.name} has no fields.` : "Choose the layer first."}</span>}
            {fields.map((f) => <label key={f.name} className="ws-check"><input type="checkbox" checked={chosen.includes(f.name)}
              onChange={(e) => setV({ ...v, [p.name]: e.target.checked ? [...chosen, f.name] : chosen.filter((x) => x !== f.name) })} /> {f.name}</label>)}
          </fieldset>
        );
      })}
      <div className="ws-f"><label htmlFor="tp-out">New layer's name (optional)</label><input id="tp-out" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="Named after the tool" /></div>
      <ErrorText error={error} />
      {done && <p className="ws-ok" role="status">✓ {done}</p>}
      <Button type="submit" variant="primary" busy={busy} disabled={!canEdit}>Run</Button>
      {!canEdit && <p className="ws-muted">Only editors can run tools.</p>}
    </form>
  );
}

function History({ projectId, tools }: { projectId: string; tools: Tool[] }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { void api.runs(projectId).then(setRuns, setError); }, [projectId]);
  const nameOf = (id: string) => tools.find((t) => t.id === id)?.name ?? id;
  return (
    <div className="ws-panel-body">
      <ErrorText error={error} />
      {runs && !runs.length && <p className="ws-muted">Nothing has been run in this project yet.</p>}
      <ul className="ws-runs">
        {runs?.map((r) => (
          <li key={r.id} className={r.status === "failed" ? "is-failed" : ""}>
            <strong>{nameOf(r.tool)}</strong> <span className="ws-muted">{when(r.at)} · {r.actor_name}</span><br />
            {r.status === "done" ? <span>→ {r.output_name} ({r.feature_count == null ? "raster" : `${r.feature_count.toLocaleString("en-GB")} features`}, {((r.ms ?? 0) / 1000).toFixed(1)} s){!r.output_layer_id && <em className="ws-muted"> · layer since removed</em>}</span>
              : <span className="ws-bad">Failed: {r.message}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
