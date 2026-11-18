/** Geodatabases: a project's data stores, ArcGIS-style. Every project has a default geodatabase where new layers and
 *  analysis results land; you can add more, rename them, choose the default, download one as a GeoPackage, or delete
 *  one (and the layers in it). */
import { Check, Database, Download, MoreHorizontal, Plus, Star, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, geodatabaseDownloadUrl } from "../lib/api";
import type { Geodatabase } from "../lib/types";
import { Button, ErrorText, IconButton, Menu, useToast } from "./ui";

export default function Geodatabases({ projectId, canEdit, onChanged, onClose }: {
  projectId: string; canEdit: boolean; onChanged: () => void; onClose: () => void;
}) {
  const toast = useToast();
  const [list, setList] = useState<Geodatabase[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  const load = () => api.geodatabases(projectId).then(setList, setError);
  useEffect(() => { void load(); }, [projectId]);   // eslint-disable-line react-hooks/exhaustive-deps
  const fail = (e: unknown) => toast(e instanceof ApiError ? e.message : String(e), "error");

  async function add(name: string) {
    const n = name.trim();
    if (!n) return;
    setBusy("add");
    try { await api.createGeodatabase(projectId, n); setAdding(false); await load(); } catch (e) { fail(e); } finally { setBusy(null); }
  }
  async function rename(g: Geodatabase, name: string) {
    const n = name.trim();
    if (!n || n === g.name) { setRenaming(null); return; }
    setBusy(g.id);
    try { await api.updateGeodatabase(g.id, { name: n }); setRenaming(null); await load(); } catch (e) { fail(e); } finally { setBusy(null); }
  }
  async function makeDefault(g: Geodatabase) {
    setBusy(g.id);
    try { await api.updateGeodatabase(g.id, { is_default: true }); await load(); onChanged(); } catch (e) { fail(e); } finally { setBusy(null); }
  }
  async function remove(g: Geodatabase) {
    setBusy(g.id);
    try { await api.deleteGeodatabase(g.id); setConfirm(null); await load(); onChanged(); toast(`${g.name} deleted.`, "success"); }
    catch (e) { fail(e); } finally { setBusy(null); }
  }

  return (
    <aside className="ws-panel prov" aria-label="Geodatabases">
      <div className="ws-pane-head"><strong>Geodatabases</strong><span className="ge-spacer" /><IconButton label="Close" onClick={onClose}><X size={16} /></IconButton></div>
      <div className="ws-scroll mil-body">
        <p className="ws-muted">Each project has a default geodatabase. New layers and analysis results are stored in it — a working environment, like an ArcGIS project's default geodatabase. Download one as a GeoPackage to open it in QGIS or ArcGIS.</p>
        <ErrorText error={error} />
        <ul className="trk-list">
          {(list ?? []).map((g) => (
            <li key={g.id} className="trk-item">
              {confirm === g.id ? (
                <div className="trk-row" style={{ gap: 8 }}>
                  <span className="trk-name">Delete “{g.name}”{g.layer_count > 0 ? ` and its ${g.layer_count} layer${g.layer_count > 1 ? "s" : ""}?` : "?"}</span>
                  <Button small variant="danger" busy={busy === g.id} onClick={() => void remove(g)}>Delete</Button>
                  <Button small onClick={() => setConfirm(null)}>Cancel</Button>
                </div>
              ) : renaming === g.id ? (
                <div className="trk-row" style={{ gap: 8 }}>
                  <Database size={15} aria-hidden="true" />
                  <input autoFocus defaultValue={g.name} className="cat-rename" aria-label="Geodatabase name"
                    onKeyDown={(e) => { if (e.key === "Enter") void rename(g, (e.target as HTMLInputElement).value); if (e.key === "Escape") setRenaming(null); }}
                    onBlur={(e) => void rename(g, e.target.value)} />
                </div>
              ) : (
                <div className="trk-row">
                  <Database size={15} aria-hidden="true" />
                  <span className="trk-name">{g.name}
                    {g.is_default && <span className="ws-tag"><Star size={11} aria-hidden="true" /> Default</span>}
                    <span className="ws-muted"> · {g.layer_count} layer{g.layer_count === 1 ? "" : "s"}{g.feature_count ? ` · ${g.feature_count.toLocaleString()} feature${g.feature_count === 1 ? "" : "s"}` : ""}</span>
                  </span>
                  <a className="ib" href={geodatabaseDownloadUrl(g.id)} title={`Download ${g.name} as a GeoPackage`} aria-label={`Download ${g.name} as a GeoPackage`} download>
                    <Download size={15} aria-hidden="true" />
                  </a>
                  {canEdit && <Menu align="end" label={`Actions for ${g.name}`} trigger={<MoreHorizontal size={16} />}
                    items={[
                      { label: "Set as default", icon: <Check size={15} />, disabled: g.is_default, onSelect: () => void makeDefault(g) },
                      { label: "Rename", icon: <Database size={15} />, onSelect: () => setRenaming(g.id) },
                      { label: "Download GeoPackage", icon: <Download size={15} />, onSelect: () => { window.location.href = geodatabaseDownloadUrl(g.id); } },
                      { label: "Delete", icon: <Trash2 size={15} />, danger: true, disabled: g.is_default, onSelect: () => setConfirm(g.id) },
                    ]} />}
                </div>
              )}
            </li>))}
        </ul>
        {canEdit && (adding ? (
          <div className="trk-row" style={{ gap: 8 }}>
            <Database size={15} aria-hidden="true" />
            <input ref={nameRef} autoFocus className="cat-rename" aria-label="New geodatabase name" placeholder="Geodatabase name"
              onKeyDown={(e) => { if (e.key === "Enter") void add((e.target as HTMLInputElement).value); if (e.key === "Escape") setAdding(false); }} />
            <Button small variant="primary" busy={busy === "add"} onClick={() => void add(nameRef.current?.value ?? "")}>Add</Button>
            <Button small onClick={() => setAdding(false)}>Cancel</Button>
          </div>
        ) : <Button onClick={() => setAdding(true)}><Plus size={15} aria-hidden="true" />New geodatabase</Button>)}
        {!canEdit && <p className="ws-muted">You can download a geodatabase. Editors can add, rename and set the default.</p>}
      </div>
    </aside>
  );
}
