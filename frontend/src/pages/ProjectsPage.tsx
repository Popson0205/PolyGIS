import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { CrsPicker } from "../components/Dialogs";
import { Button, ErrorText, useAction } from "../components/ui";
import { api } from "../lib/api";
import { useLoad } from "../lib/useLoad";
import type { Me } from "../lib/types";

const when = (d: string) => new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** The organization's projects; editors start new ones. */
export default function ProjectsPage({ me }: { me: Me }) {
  const { data, error, reload } = useLoad(api.projects);
  const nav = useNavigate();
  const { busy, run } = useAction();
  const [form, setForm] = useState<{ name: string; description: string; epsg: number | null } | null>(null);
  const canMake = me.role !== "viewer" && me.can_write;
  return (
    <div className="ge-main">
      <div className="gis-head">
        <div><h1>Projects</h1><p className="ge-muted">Maps of {me.org_name ?? "your organization"}: open one to add data, style it, edit and measure.</p></div>
        {canMake && !form && <Button variant="primary" onClick={() => setForm({ name: "", description: "", epsg: 26332 })}>New project</Button>}
      </div>
      {form && (
        <form className="ge-card gis-new" onSubmit={(e) => { e.preventDefault(); void run("new", () => api.createProject({ name: form.name.trim(), description: form.description.trim(), display_epsg: form.epsg ?? 4326 }), "Project made.").then((p) => p && nav(`/projects/${p.id}`)); }}>
          <h2>New project</h2>
          <div className="ge-field"><label htmlFor="p-name">Name</label><input id="p-name" required maxLength={200} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Kuje layout survey" /></div>
          <div className="ge-field"><label htmlFor="p-desc">Description (optional)</label><textarea id="p-desc" rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
          <CrsPicker value={form.epsg} onChange={(epsg) => setForm({ ...form, epsg })} label="Coordinates shown in" />
          <div className="gis-actions"><Button type="submit" variant="primary" busy={busy === "new"}>Make the project</Button><Button variant="ghost" onClick={() => setForm(null)}>Cancel</Button></div>
        </form>
      )}
      <ErrorText error={error} />
      {data && data.length === 0 && !form && <div className="ge-card"><p>No projects yet.{canMake ? " Start one with New project." : ""}</p></div>}
      <ul className="gis-projects">
        {data?.map((p) => (
          <li key={p.id} className="ge-card">
            <Link to={`/projects/${p.id}`} className="gis-project-link"><strong>{p.name}</strong></Link>
            {p.description && <p className="ge-muted">{p.description}</p>}
            <p className="ge-small ge-muted">{p.layer_count} layer{p.layer_count === 1 ? "" : "s"} · EPSG:{p.display_epsg} · by {p.created_by_name || "—"} · changed {when(p.updated_at)}</p>
            {canMake && (me.role === "admin" || p.created_by === me.id) && <button type="button" className="ge-btn ge-btn-sm ge-btn-danger" onClick={() => {
              if (window.confirm(`Delete ${p.name} with all its layers? This can't be undone.`)) void run(p.id, () => api.deleteProject(p.id), "Project deleted.").then(() => reload());
            }} disabled={busy === p.id}>Delete</button>}
          </li>
        ))}
      </ul>
    </div>
  );
}
