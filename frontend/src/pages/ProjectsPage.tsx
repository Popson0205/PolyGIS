import { Clock, FolderInput, FolderOpen, Globe2, HardDriveDownload, Layers, Map as MapIcon, MoreHorizontal, Plus, Search, Trash2, UserRound } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { CrsPicker, Dialog } from "../components/Dialogs";
import { Button, ErrorText, Menu, useAction, useStored, useToast } from "../components/ui";
import { BASE, api } from "../lib/api";
import { openProjectFile, pickSaveLocation, saveProjectFile, supportsFsAccess, writeToHandle } from "../lib/projectFile";
import { useLoad } from "../lib/useLoad";
import type { Me, Project } from "../lib/types";

const ago = (d: string) => {
  const s = (Date.now() - new Date(d).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
};
type Sort = "recent" | "name" | "layers";

/** The organization's projects as map cards; editors start new ones. */
export default function ProjectsPage({ me }: { me: Me }) {
  const { data, error, reload } = useLoad(api.projects);
  const nav = useNavigate();
  const { busy, run } = useAction();
  const [making, setMaking] = useState(false);
  const [importing, setImporting] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useStored<Sort>("project-sort", "recent");
  const toast = useToast();
  const canMake = me.role !== "viewer" && me.can_write;

  async function openFile() {
    setImporting(true);
    try {
      const out = await openProjectFile();
      if (out) {
        const note = out.rasters_skipped ? ` (${out.rasters_skipped} raster layer${out.rasters_skipped > 1 ? "s" : ""} weren't in the file)` : "";
        toast(`${out.name} opened${note}.`, "success");
        nav(`/projects/${out.id}`);
      }
    } catch (e) { toast(e instanceof Error ? e.message : "Could not open the project file.", "error"); }
    finally { setImporting(false); }
  }

  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const hit = (data ?? []).filter((p) => words.every((w) => `${p.name} ${p.description} ${p.created_by_name}`.toLowerCase().includes(w)));
    return [...hit].sort((a, b) => sort === "name" ? a.name.localeCompare(b.name) : sort === "layers" ? b.layer_count - a.layer_count
      : b.updated_at.localeCompare(a.updated_at));
  }, [data, q, sort]);

  const remove = (p: Project) => {
    if (window.confirm(`Delete ${p.name} with all its layers? This can't be undone.`)) void run(p.id, () => api.deleteProject(p.id), `${p.name} deleted.`).then(() => reload());
  };

  return (
    <main className="gis-start">
      <div className="gis-start-head">
        <div><h1>Projects</h1><p>{me.org_name ?? "Your organization"}'s maps. Open one to add data, style it, edit and analyse.</p></div>
        {canMake && (data?.length ?? 0) > 0 && <div className="ws-row" style={{ gap: 8 }}>
          <Button busy={importing} onClick={() => void openFile()}><FolderInput size={16} aria-hidden="true" />Open project file</Button>
          <Button variant="primary" onClick={() => setMaking(true)}><Plus size={17} aria-hidden="true" />New project</Button>
        </div>}
      </div>
      {(data?.length ?? 0) > 0 && (
        <div className="gis-tools">
          <label className="gis-search"><Search size={16} aria-hidden="true" />
            <input type="search" placeholder="Search projects" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search projects" /></label>
          <select className="gis-sort" value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sort projects">
            <option value="recent">Recently changed</option><option value="name">Name</option><option value="layers">Most layers</option>
          </select>
        </div>
      )}
      <ErrorText error={error} />
      {!data && !error && <p className="loading">Loading the projects…</p>}
      {data && data.length === 0 && (
        <div className="gis-empty"><MapIcon size={34} aria-hidden="true" /><strong>No projects yet</strong>
          <span>{canMake ? "A project is a map: start one, then add your survey files, imagery or elevation models." : "When someone in your organization makes a project, it appears here."}</span>
          {canMake && <div className="ws-row" style={{ gap: 8, justifyContent: "center" }}>
            <Button variant="primary" onClick={() => setMaking(true)}><Plus size={17} aria-hidden="true" />New project</Button>
            <Button busy={importing} onClick={() => void openFile()}><FolderInput size={16} aria-hidden="true" />Open project file</Button>
          </div>}
        </div>
      )}
      {data && data.length > 0 && shown.length === 0 && <p className="ge-muted">No project matches “{q}”.</p>}
      <ul className="gis-projects">
        {shown.map((p) => (
          <li key={p.id} className="gis-project">
            <span className="gis-thumb" aria-hidden="true">
              {p.layer_count > 0 ? <img src={`${BASE}/projects/${p.id}/thumbnail.svg?v=${encodeURIComponent(p.updated_at)}`} alt="" loading="lazy" />
                : <span className="gis-thumb-empty"><MapIcon size={26} />No layers yet</span>}
            </span>
            <div className="gis-project-body">
              <Link to={`/projects/${p.id}`} className="gis-project-link" title={p.name}>{p.name}</Link>
              {p.description && <p className="gis-desc">{p.description}</p>}
              <div className="gis-project-facts">
                <span title="Layers"><Layers size={13} aria-hidden="true" />{p.layer_count} layer{p.layer_count === 1 ? "" : "s"}</span>
                <span title="Coordinates shown in"><Globe2 size={13} aria-hidden="true" />EPSG:{p.display_epsg}</span>
                <span title={`Changed ${new Date(p.updated_at).toLocaleString("en-GB")}`}><Clock size={13} aria-hidden="true" />{ago(p.updated_at)}</span>
                {p.created_by_name && <span title="Made by"><UserRound size={13} aria-hidden="true" />{p.created_by_name}</span>}
              </div>
            </div>
            <div className="gis-project-menu">
              <Menu label={`${p.name}: more`} trigger={<MoreHorizontal size={16} />} items={[
                { label: "Open", icon: <FolderOpen size={15} />, onSelect: () => nav(`/projects/${p.id}`) },
                ...(canMake && (me.role === "admin" || p.created_by === me.id)
                  ? [{ label: "", onSelect: () => {}, divider: true }, { label: "Delete project", icon: <Trash2 size={15} />, danger: true, disabled: busy === p.id, onSelect: () => remove(p) }] : []),
              ]} />
            </div>
          </li>
        ))}
      </ul>
      {making && <NewProject onClose={() => setMaking(false)} onMade={(p) => nav(`/projects/${p.id}`)} />}
    </main>
  );
}

function NewProject({ onClose, onMade }: { onClose: () => void; onMade: (p: Project) => void }) {
  const { busy, run } = useAction();
  const toast = useToast();
  const [form, setForm] = useState<{ name: string; description: string; epsg: number | null }>({ name: "", description: "", epsg: 26332 });

  async function make(save: boolean) {
    const name = form.name.trim();
    if (!name) return;
    // On supported browsers, ask where to save FIRST, inside the click, so the native dialog is allowed to open.
    let handle = null;
    if (save && supportsFsAccess) {
      try { handle = await pickSaveLocation(name); } catch { return; }
      if (!handle) return;   // the person cancelled the save dialog
    }
    const p = await run(save ? "save" : "new", () => api.createProject({ name, description: form.description.trim(), display_epsg: form.epsg ?? 4326 }), "Project made.");
    if (!p) return;
    if (handle) {
      try { await writeToHandle(p.id, handle); toast("Project saved to your computer.", "success"); }
      catch { toast("Project made, but saving the file didn't finish.", "error"); }
    } else if (save) {
      try { await saveProjectFile(p.id, p.name); } catch { /* download fallback cancelled */ }
    }
    onMade(p);
  }

  return (
    <Dialog title="New project" onClose={onClose}>
      <form className="ws-form" onSubmit={(e) => { e.preventDefault(); void make(false); }}>
        <label className="ws-f">Name<input autoFocus required maxLength={200} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Kuje layout survey" /></label>
        <label className="ws-f">Description <span className="ws-help">Optional: what the map is for</span><textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
        <CrsPicker value={form.epsg} onChange={(epsg) => setForm({ ...form, epsg })} label="Show coordinates in" />
        <p className="ws-help">{supportsFsAccess ? "Save a copy to your computer to open it later as a project file." : "After it's made, you can save it to your computer as a project file."}</p>
        <div className="ws-row">
          <Button type="submit" variant="primary" busy={busy === "new"}>Make the project</Button>
          <Button type="button" busy={busy === "save"} onClick={() => void make(true)}><HardDriveDownload size={15} aria-hidden="true" />Make &amp; save to computer</Button>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Dialog>
  );
}
