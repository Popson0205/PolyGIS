import { Link2, Lock } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api, type LinkedSource } from "../lib/api";
import type { Layer } from "../lib/types";
import { Dialog } from "./Dialogs";
import { Button, ErrorText } from "./ui";

const KIND: Record<string, string> = { Point: "points", LineString: "lines", Polygon: "polygons" };

/** Insert › Linked layer: data that lives in another GeoEstate app, as a layer that follows it. */
export default function LinkedDialog({ projectId, onClose, onDone }: { projectId: string; onClose: () => void; onDone: (l: Layer) => void }) {
  const [src, setSrc] = useState<LinkedSource[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  useEffect(() => { api.linkedSources(projectId).then(setSrc, setError); }, [projectId]);
  async function add(s: LinkedSource) {
    setError(null); setBusy(s.form_id ?? s.app);
    try { onDone(await api.addLinked(projectId, { app: s.app, form_id: s.form_id, status: all ? "all" : "approved" })); }
    catch (e) { setError(e instanceof ApiError ? e : new Error("Could not add it.")); }
    finally { setBusy(null); }
  }
  const apps = (src ?? []).filter((s) => s.app !== "hub");
  const forms = (src ?? []).filter((s) => s.app === "hub");
  return (
    <Dialog title="Add a linked layer" onClose={onClose} wide>
      <div className="ws-form">
        <p className="ws-muted">A linked layer shows data that lives in another GeoEstate app, as you may see it there. Style it, label it, query
          it, analyse it, print and share it like any layer; it is changed in its app and follows it (refresh it, or it refreshes when the
          project opens).</p>
        <ErrorText error={error} />
        {src === null && !error && <p className="ws-muted">Looking at your apps…</p>}
        {apps.length > 0 && <fieldset className="sh-new"><legend className="pb-legend">GeoEstate apps</legend>
          <ul className="sh-list">{apps.map((s) => (
            <li key={s.app}><div className="sh-main"><strong>{s.app_name}: {s.label}</strong>
              <span className="ws-muted">{s.available ? `${KIND[s.geometry_type]} · read as you` : <><Lock size={12} aria-hidden="true" /> {s.why}</>}</span></div>
              <div className="sh-acts"><Button small variant="primary" disabled={!s.available || !!busy} busy={busy === s.app} onClick={() => void add(s)}><Link2 size={14} aria-hidden="true" />Add</Button></div></li>
          ))}</ul></fieldset>}
        {src !== null && <fieldset className="sh-new"><legend className="pb-legend">Geohub forms</legend>
          {forms.length ? <>
            <label className="lo-check"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />Include records not yet approved</label>
            <ul className="sh-list">{forms.map((s) => (
              <li key={s.form_id}><div className="sh-main"><strong>{s.label}</strong>
                <span className="ws-muted">{KIND[s.geometry_type]} · {(all ? s.features : s.approved)?.toLocaleString("en-GB") ?? "?"} record{(all ? s.features : s.approved) === 1 ? "" : "s"}{all ? "" : " approved"}</span></div>
                <div className="sh-acts"><Button small disabled={!!busy} busy={busy === s.form_id} onClick={() => void add(s)}><Link2 size={14} aria-hidden="true" />Add</Button></div></li>
            ))}</ul></> : <p className="ws-muted">No Geohub forms with points, lines or areas that you can open.</p>}
        </fieldset>}
      </div>
    </Dialog>
  );
}
