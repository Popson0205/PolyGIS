import { Play, TerminalSquare, X } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../lib/api";
import type { Layer, NotebookResult } from "../lib/types";
import { Button, ErrorText } from "./ui";

const SAMPLE = `# The chosen layer is loaded as a GeoPandas GeoDataFrame called gdf.
# geopandas (gpd), pandas (pd), numpy (np) and shapely are available.
# Leave your answer in "result": a GeoDataFrame becomes a new layer,
# a DataFrame becomes a table, anything else is shown as text.

print(gdf.head())
result = gdf
`;

/** The Python notebook (Analyse): run Python on the server against a layer. The layer arrives as \`gdf\`; whatever is
 *  left in \`result\` comes back — a GeoDataFrame as a new layer, a DataFrame as a table, else text. */
export default function Notebook({ projectId, layers, activeId, onClose, onLayer }: {
  projectId: string; layers: Layer[]; activeId: string | null; onClose: () => void; onLayer: (l: Layer) => void;
}) {
  const vectors = layers.filter((l) => l.geometry_type !== "Raster");
  const [layerId, setLayerId] = useState<string>(vectors.find((l) => l.id === activeId)?.id ?? vectors[0]?.id ?? "");
  const [name, setName] = useState("");
  const [code, setCode] = useState(SAMPLE);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<NotebookResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const area = useRef<HTMLTextAreaElement>(null);

  async function run() {
    setBusy(true); setError(null);
    try {
      const r = await api.notebookRun(projectId, { code, layer_id: layerId || null, output_name: name.trim() || null });
      setRes(r);
      if (r.kind === "layer" && r.layer) onLayer(r.layer);
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  }
  // Ctrl/Cmd+Enter runs; Tab inserts two spaces instead of leaving the box.
  function keys(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); void run(); }
    if (e.key === "Tab") {
      e.preventDefault();
      const t = e.currentTarget, s = t.selectionStart, en = t.selectionEnd;
      setCode(code.slice(0, s) + "  " + code.slice(en));
      requestAnimationFrame(() => { t.selectionStart = t.selectionEnd = s + 2; });
    }
  }

  return (
    <aside className="ws-panel nb" aria-label="Python notebook">
      <div className="ws-pane-head"><TerminalSquare size={16} aria-hidden="true" /><strong>Notebook</strong>
        <span className="ge-spacer" /><button type="button" className="ib" aria-label="Close the notebook" onClick={onClose}><X size={16} /></button></div>
      <div className="nb-body">
        <label className="ws-f"><span>Load layer as <code>gdf</code></span>
          <select value={layerId} onChange={(e) => setLayerId(e.target.value)}>
            <option value="">— none (empty gdf) —</option>
            {vectors.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </label>
        <textarea ref={area} className="nb-code" spellCheck={false} value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={keys}
          aria-label="Python code" rows={12} />
        <div className="nb-run">
          <Button variant="primary" busy={busy} onClick={() => void run()}><Play size={14} aria-hidden="true" /> Run</Button>
          <span className="ws-muted nb-hint">Ctrl/⌘+Enter</span>
          <span className="ge-spacer" />
          <label className="ws-f nb-name"><span>New layer name</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="If result is a layer" /></label>
        </div>
        <ErrorText error={error} />
        {res && <NotebookOutput res={res} />}
      </div>
    </aside>
  );
}

function NotebookOutput({ res }: { res: NotebookResult }) {
  return (
    <div className="nb-out" aria-live="polite">
      {res.error && <pre className="nb-error" role="alert">{res.error}</pre>}
      {res.input_truncated && <p className="ws-warn">Only the first rows of the layer were loaded; the result may be partial.</p>}
      {res.kind === "layer" && res.layer && <p className="nb-ok">Added the layer “{res.layer.name}” to the map{res.rows != null ? ` (${res.rows.toLocaleString("en-GB")} features)` : ""}.</p>}
      {res.kind === "text" && <pre className="nb-value">{res.value}</pre>}
      {res.kind === "table" && res.table && <TablePreview t={res.table} rows={res.rows} />}
      {res.stdout && <><p className="nb-label">Output</p><pre className="nb-stdout">{res.stdout}</pre></>}
      {!res.error && res.kind === "none" && !res.stdout && <p className="ws-muted">Ran, with nothing in <code>result</code> and nothing printed.</p>}
    </div>
  );
}

function TablePreview({ t, rows }: { t: NonNullable<NotebookResult["table"]>; rows?: number }) {
  const shown = t.data.slice(0, 50);
  return (
    <div className="nb-table-wrap">
      <p className="nb-label">Table{rows != null ? ` · ${rows.toLocaleString("en-GB")} rows` : ""}{t.data.length > shown.length ? ` (first ${shown.length})` : ""}</p>
      <div className="nb-table-scroll">
        <table className="ws-grid">
          <thead><tr>{t.columns.map((c, i) => <th key={i}>{String(c)}</th>)}</tr></thead>
          <tbody>{shown.map((row, i) => <tr key={i}>{row.map((v, j) => <td key={j} className={typeof v === "number" ? "ws-num" : ""}>{v == null ? "" : String(v)}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}
