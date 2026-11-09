import { ArrowLeft, CheckCircle2, ChevronDown, Play, Search, Star, Wrench, X, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { Layer, Run, Tool, ToolParam } from "../lib/types";
import { Button, ErrorText, IconButton, useStored } from "./ui";

const WORD: Record<string, string> = { Point: "points", LineString: "lines", Polygon: "polygons" };
const when = (d: string) => new Date(d).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** The geoprocessing pane: find a tool, fill in its form, run it; the result arrives as a new layer. */
export default function Geoprocessing({ projectId, layers, selection, activeId, allowed, rasterAllowed, canEdit, start, query = "", onClose, onDone }: {
  projectId: string; layers: Layer[]; selection: Record<string, number[]>; activeId: string | null; allowed: boolean; rasterAllowed?: boolean; canEdit: boolean;
  start?: string | null; query?: string; onClose: () => void; onDone: (l: Layer) => void;
}) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [view, setView] = useState<"list" | "history">("list");
  const [q, setQ] = useState(query);
  const [tool, setTool] = useState<Tool | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [stars, setStars] = useStored<string[]>("tool-stars", ["buffer", "clip", "dissolve", "spatial_join"]);
  const [recent, setRecent] = useStored<string[]>("tool-recent", []);
  const [openGroups, setOpenGroups] = useStored<string[]>("tool-groups-open", []);
  useEffect(() => {
    void Promise.all([api.tools(), rasterAllowed ? api.rasterTools() : Promise.resolve([] as Tool[])])
      .then(([v, r]) => setTools([...v, ...r.map((t) => ({ ...t, kind: "raster" as const, group: `Raster: ${t.group}` }))]), setError);
  }, [rasterAllowed]);
  useEffect(() => { if (start && tools.length) setTool(tools.find((t) => t.id === start) ?? null); }, [start, tools]);
  useEffect(() => { setQ(query); if (query) { setTool(null); setView("list"); } }, [query]);
  const pick = (t: Tool) => { setTool(t); setRecent((r) => [t.id, ...r.filter((x) => x !== t.id)].slice(0, 6)); };

  const groups = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const hit = tools.filter((t) => words.every((w) => `${t.name} ${t.about} ${t.group} ${t.id}`.toLowerCase().includes(w)));
    return Array.from(new Set(hit.map((t) => t.group))).map((g) => [g, hit.filter((t) => t.group === g)] as const);
  }, [tools, q]);
  const byId = (ids: string[]) => ids.map((x) => tools.find((t) => t.id === x)).filter(Boolean) as Tool[];
  const row = (t: Tool) => (
    <li key={t.id} className="ws-tool">
      <button type="button" onClick={() => pick(t)} title={t.about}><strong>{t.name}</strong><span>{t.about}</span></button>
      <IconButton label={stars.includes(t.id) ? `Remove ${t.name} from favourites` : `Add ${t.name} to favourites`} on={stars.includes(t.id)}
        onClick={() => setStars(stars.includes(t.id) ? stars.filter((x) => x !== t.id) : [...stars, t.id])}>
        <Star size={14} fill={stars.includes(t.id) ? "currentColor" : "none"} /></IconButton>
    </li>
  );
  const section = (key: string, title: string, ts: Tool[], openNow: boolean) => (
    <section key={key} className="ws-toolgroup">
      <h3><button type="button" aria-expanded={openNow} onClick={() => setOpenGroups(openGroups.includes(key) ? openGroups.filter((x) => x !== key) : [...openGroups, key])}>
        <ChevronDown size={14} aria-hidden="true" />{title}<span className="ws-count">{ts.length}</span></button></h3>
      {openNow && <ul>{ts.map(row)}</ul>}
    </section>
  );
  const searching = q.trim().length > 0;

  return (
    <aside className="ws-panel" aria-label="Geoprocessing">
      <div className="ws-pane-head">
        {tool ? <IconButton label="All tools" onClick={() => setTool(null)}><ArrowLeft size={16} /></IconButton> : <Wrench size={16} aria-hidden="true" className="ws-pane-icon" />}
        <strong>{tool ? tool.name : "Geoprocessing"}</strong>
        <IconButton label="Close geoprocessing" onClick={onClose}><X size={16} /></IconButton>
      </div>
      {!allowed ? (
        <div className="ws-empty-state"><Wrench size={22} aria-hidden="true" /><span>Geoprocessing tools are in <b>PolyGIS Advanced</b>. Ask GeoEstate NIG Limited to move your organization to Advanced.</span></div>
      ) : tool ? (
        <ToolForm key={tool.id} tool={tool} projectId={projectId} layers={layers} selection={selection} activeId={activeId} canEdit={canEdit}
          starred={stars.includes(tool.id)} onStar={() => setStars(stars.includes(tool.id) ? stars.filter((x) => x !== tool.id) : [...stars, tool.id])} onDone={onDone} />
      ) : (
        <>
          <div className="ws-tabs" role="tablist">
            <button role="tab" aria-selected={view === "list"} onClick={() => setView("list")}>Tools</button>
            <button role="tab" aria-selected={view === "history"} onClick={() => setView("history")}>History</button>
          </div>
          <ErrorText error={error} />
          {view === "list" ? (
            <>
              <div className="ws-gp-search"><Search size={15} aria-hidden="true" />
                <input className="ws-find" placeholder={`Search ${tools.length || ""} tools: buffer, hot spot, slope…`} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search the tools" autoFocus={!!query} />
              </div>
              <div className="ws-scroll">
                {!searching && byId(stars).length > 0 && section("_stars", "Favourites", byId(stars), !openGroups.includes("_stars"))}
                {!searching && byId(recent).length > 0 && section("_recent", "Recently used", byId(recent), !openGroups.includes("_recent"))}
                {groups.map(([g, ts]) => section(g, g, ts, searching || openGroups.includes(g)))}
                {!groups.length && <p className="ws-empty">No tool matches “{q}”. Try another word, such as distance, overlay or terrain.</p>}
              </div>
            </>
          ) : <div className="ws-scroll ws-panel-body"><History projectId={projectId} tools={tools} /></div>}
        </>
      )}
    </aside>
  );
}

function ToolForm({ tool, projectId, layers, selection, activeId, canEdit, starred, onStar, onDone }: {
  tool: Tool; projectId: string; layers: Layer[]; selection: Record<string, number[]>; activeId: string | null; canEdit: boolean;
  starred: boolean; onStar: () => void; onDone: (l: Layer) => void;
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
    <form className="ws-gp-form" onSubmit={(e) => { e.preventDefault(); void go(); }}>
     <div className="ws-form">
      <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
        <p className="ws-gp-about" style={{ flex: 1 }}>{tool.about}</p>
        <IconButton label={starred ? "Remove from favourites" : "Add to favourites"} on={starred} className="ws-star" onClick={onStar}><Star size={15} fill={starred ? "currentColor" : "none"} /></IconButton>
      </div>
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
     </div>
     <div className="ws-run">
      <ErrorText error={error} />
      {done && <p className="ws-ok" role="status"><CheckCircle2 size={16} aria-hidden="true" /><span>{done}</span></p>}
      <Button type="submit" variant="primary" busy={busy} disabled={!canEdit}>{busy ? "Running…" : <><Play size={15} aria-hidden="true" />Run</>}</Button>
      {!canEdit && <p className="ws-muted">Only editors can run tools.</p>}
     </div>
    </form>
  );
}

function History({ projectId, tools }: { projectId: string; tools: Tool[] }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { void api.runs(projectId).then(setRuns, setError); }, [projectId]);
  const nameOf = (id: string) => tools.find((t) => t.id === id)?.name ?? id;
  return (
    <div>
      <ErrorText error={error} />
      {runs && !runs.length && <p className="ws-muted">Nothing has been run in this project yet. Each run is listed here, failed ones too, with the settings it used.</p>}
      <ul className="ws-runs">
        {runs?.map((r) => (
          <li key={r.id} className={r.status === "failed" ? "is-failed" : ""}>
            {r.status === "failed" ? <XCircle size={16} aria-hidden="true" /> : <CheckCircle2 size={16} aria-hidden="true" />}
            <span><strong>{nameOf(r.tool)}</strong> <span className="ws-muted">{when(r.at)}, {r.actor_name}</span><br />
            {r.status === "done" ? <span className="ws-runs-what">Made {r.output_name} ({r.feature_count == null ? "a raster" : `${r.feature_count.toLocaleString("en-GB")} features`}, {((r.ms ?? 0) / 1000).toFixed(1)} s){!r.output_layer_id && "; the layer has since been removed"}.</span>
              : <span className="ws-bad">Did not finish: {r.message}</span>}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
