/** "Folder connections" in the Library: connect a folder on the computer, browse its geodata, and add a dataset to the
 *  map by clicking it. For a Shapefile you pick only the .shp — the .shx/.dbf/.prj and other companions are read from
 *  the same folder automatically. Needs the File System Access API (Chrome, Edge, PolyGIS desktop). */
import { ChevronDown, ChevronRight, FileText, FolderOpen, FolderPlus, HardDrive, Layers, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import {
  connectFolder, ensurePermission, listFolders, readDataset, removeFolder, scanFolder, supportsFolders,
  type Dataset, type FolderConn,
} from "../lib/folderConnections";
import { IconButton, useToast } from "./ui";

export default function FolderConnections({ canEdit, onAddData }: {
  canEdit: boolean; onAddData: (files: File[]) => Promise<void>;
}) {
  const toast = useToast();
  const [conns, setConns] = useState<FolderConn[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [sets, setSets] = useState<Record<string, Dataset[]>>({});
  const [busy, setBusy] = useState<string | null>(null);   // "connect", a folder id (scanning), or a dataset id (adding)

  useEffect(() => { void listFolders().then(setConns); }, []);
  const fail = (e: unknown) => toast(e instanceof Error ? e.message : String(e), "error");

  async function connect() {
    setBusy("connect");
    try { const c = await connectFolder(); if (c) { setConns(await listFolders()); await open(c); } }
    catch (e) { fail(e); } finally { setBusy(null); }
  }
  async function open(c: FolderConn) {
    if (openId === c.id) { setOpenId(null); return; }
    setOpenId(c.id);
    if (sets[c.id]) return;
    setBusy(c.id);
    try {
      if (!(await ensurePermission(c.handle))) { toast(`Allow PolyGIS to read "${c.name}" to browse it.`, "error"); setOpenId(null); return; }
      const found = await scanFolder(c.handle);
      setSets((s) => ({ ...s, [c.id]: found }));
    } catch (e) { fail(e); setOpenId(null); } finally { setBusy(null); }
  }
  async function rescan(c: FolderConn) {
    setBusy(c.id);
    try { if (await ensurePermission(c.handle)) { const found = await scanFolder(c.handle); setSets((s) => ({ ...s, [c.id]: found })); } }
    catch (e) { fail(e); } finally { setBusy(null); }
  }
  async function add(c: FolderConn, ds: Dataset) {
    setBusy(ds.id + c.id);
    try {
      if (!(await ensurePermission(c.handle))) { toast(`Allow PolyGIS to read "${c.name}".`, "error"); return; }
      const files = await readDataset(c.handle, ds);
      if (!files.length) { toast("Those files could not be read from the folder.", "error"); return; }
      await onAddData(files);
    } catch (e) { fail(e); } finally { setBusy(null); }
  }
  async function forget(c: FolderConn) {
    await removeFolder(c.id);
    setConns(await listFolders());
    setSets((s) => { const n = { ...s }; delete n[c.id]; return n; });
    if (openId === c.id) setOpenId(null);
  }

  return (
    <section>
      <div className="cat-head"><HardDrive size={15} aria-hidden="true" /><h3>Folder connections</h3>
        <span className="cat-n">{conns.length}</span><span className="ge-spacer" />
        {canEdit && supportsFolders && <IconButton label="Connect a folder" onClick={() => void connect()}><FolderPlus size={15} /></IconButton>}
      </div>

      {!supportsFolders && <p className="ws-muted cat-empty">Folder connections need Chrome, Edge or the PolyGIS desktop app. In other browsers, use Add data and choose a Shapefile's parts together.</p>}
      {supportsFolders && !conns.length && <p className="ws-muted cat-empty">Connect a folder to browse its Shapefiles and other data. Add a Shapefile by its <strong>.shp</strong> — the .shx, .dbf and .prj come along automatically.</p>}

      <ul className="cat-list">
        {conns.map((c) => {
          const on = openId === c.id, scanning = busy === c.id, list = sets[c.id] ?? [];
          return (
            <li key={c.id} className="cat-folder">
              <div className="cat-item">
                <button type="button" className="cat-open" onClick={() => void open(c)} title={on ? "Collapse" : "Browse this folder"}>
                  {on ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                  <FolderOpen size={15} aria-hidden="true" /><span className="cat-name">{c.name}</span>
                </button>
                {on && <IconButton label={`Rescan ${c.name}`} onClick={() => void rescan(c)}><RefreshCw size={13} /></IconButton>}
                <IconButton label={`Disconnect ${c.name}`} onClick={() => void forget(c)}><Trash2 size={14} /></IconButton>
              </div>
              {on && (
                <ul className="cat-sublist">
                  {scanning && <li className="ws-muted cat-sub">Reading folder…</li>}
                  {!scanning && !list.length && <li className="ws-muted cat-sub">No data PolyGIS can read in this folder.</li>}
                  {!scanning && list.map((ds) => (
                    <li key={ds.id} className="cat-sub">
                      <button type="button" className="cat-ds" disabled={!canEdit || busy === ds.id + c.id} onClick={() => void add(c, ds)}
                        title={ds.kind === "shapefile" ? `Add Shapefile ${ds.label} (${ds.parts.length} files)` : `Add ${ds.label}`}>
                        {ds.kind === "file" ? <FileText size={14} aria-hidden="true" /> : <Layers size={14} aria-hidden="true" />}
                        <span className="cat-name">{ds.label}</span>
                        <span className="cat-meta">{busy === ds.id + c.id ? "adding…" : ds.kind === "shapefile" ? "Shapefile" : ds.kind === "mapinfo" ? "MapInfo" : ds.ext.toUpperCase()}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
