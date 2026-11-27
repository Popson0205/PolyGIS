/** Admin panel for ingesting large (gigabyte / national) datasets. The file is placed in the
 *  server's ingest folder; PolyGIS streams it straight into PostGIS. These layers draw from
 *  vector tiles, so a national dataset shows on the map without a browser upload. */
import { Database, FileText, Loader2, Plus, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import type { ImportJob } from "../lib/types";
import { IconButton, useToast } from "./ui";

const size = (b: number) =>
  b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${Math.round(b / 1e6)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`;

const progress = (j: ImportJob) =>
  j.status === "failed" ? (j.message || "failed")
    : j.total ? `${Math.min(100, Math.round((j.features / j.total) * 100))}% · ${j.features.toLocaleString()} / ${j.total.toLocaleString()}`
      : j.status === "queued" ? "queued" : `${j.features.toLocaleString()} loaded`;

export default function LargeDatasets({ projectId, onImported }: { projectId: string; onImported: () => void }) {
  const [files, setFiles] = useState<{ name: string; size: number }[] | null>(null);
  const [jobs, setJobs] = useState<ImportJob[]>([]);
  const [naming, setNaming] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const toast = useToast();
  const doneSeen = useRef(0);

  const loadJobs = useCallback(async () => {
    try { setJobs((await api.projectImports(projectId)).imports); } catch { /* keep last */ }
  }, [projectId]);

  const loadFiles = useCallback(async () => {
    setErr("");
    try { setFiles((await api.importSources()).files); }
    catch (e) { setFiles([]); setErr(e instanceof Error ? e.message : "Could not read the server ingest folder."); }
  }, []);

  useEffect(() => { void loadFiles(); void loadJobs(); }, [loadFiles, loadJobs]);

  // Poll while anything is in flight; when a new one finishes, refresh the map.
  useEffect(() => {
    const busy = jobs.some((j) => j.status === "running" || j.status === "queued");
    if (!busy) return;
    const t = window.setTimeout(() => void loadJobs(), 2000);
    return () => window.clearTimeout(t);
  }, [jobs, loadJobs]);

  useEffect(() => {
    const done = jobs.filter((j) => j.status === "done").length;
    if (done > doneSeen.current) { doneSeen.current = done; onImported(); }
  }, [jobs, onImported]);

  const start = async (source: string, fallback: string) => {
    const n = name.trim() || fallback;
    setNaming(null); setName("");
    try { await api.startImport(projectId, { source, name: n }); toast(`Importing ${n}…`, "info"); await loadJobs(); }
    catch (e) { toast(e instanceof Error ? e.message : "Could not start the import.", "error"); }
  };

  const active = jobs.filter((j) => j.status !== "done");

  return (
    <section>
      <div className="cat-head"><Database size={15} aria-hidden="true" /><h3>Large datasets</h3>
        <span className="ge-spacer" />
        <IconButton label="Refresh" onClick={() => { void loadFiles(); void loadJobs(); }}><RefreshCw size={13} /></IconButton>
      </div>

      <p className="ws-muted cat-empty">Gigabyte / national data placed in the server's ingest folder. Import loads it
        straight into PolyGIS and draws it from vector tiles.</p>
      {err && <p className="ws-muted cat-empty" style={{ color: "var(--danger)" }}>{err}</p>}

      <ul className="cat-list">
        {active.map((j) => (
          <li key={j.id} className="cat-sub">
            <span className="cat-ds" aria-label={`${j.name} ${j.status}`}>
              <Loader2 size={14} className={j.status === "running" ? "ge-spin" : ""} aria-hidden="true" />
              <span className="cat-name">{j.name}</span>
              <span className="cat-meta" style={j.status === "failed" ? { color: "var(--danger)" } : undefined}>{progress(j)}</span>
            </span>
          </li>
        ))}
        {files && !files.length && !err && <li className="ws-muted cat-sub">No files in the ingest folder yet.</li>}
        {files?.map((f) => {
          const fallback = f.name.replace(/\.[^.]+$/, "");
          return naming === f.name ? (
            <li key={f.name} className="cat-sub">
              <input className="cat-rename" autoFocus placeholder={fallback} value={name} aria-label="Layer name"
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") void start(f.name, fallback); if (e.key === "Escape") { setNaming(null); setName(""); } }} />
              <IconButton label="Start import" onClick={() => void start(f.name, fallback)}><Plus size={15} /></IconButton>
            </li>
          ) : (
            <li key={f.name} className="cat-sub">
              <button type="button" className="cat-ds" onClick={() => { setNaming(f.name); setName(""); }} title={`Import ${f.name}`}>
                <FileText size={14} aria-hidden="true" /><span className="cat-name">{f.name}</span><span className="cat-meta">{size(f.size)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
