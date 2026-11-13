import { Check, Copy, ExternalLink, Globe2, Link2, Lock, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api, shareUrl, type Share } from "../lib/api";
import type { Layer, Project, View } from "../lib/types";
import { Dialog } from "./Dialogs";
import { Button, ErrorText, IconButton, useToast } from "./ui";

const when = (s: string) => new Date(s).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** Share › Share link: read-only links to the map, for people with or without a GeoEstate sign-in. */
export default function ShareDialog({ project, layers, view, canEdit, onClose }: {
  project: Project; layers: Layer[]; view: () => View; canEdit: boolean; onClose: () => void;
}) {
  const toast = useToast();
  const [shares, setShares] = useState<Share[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [making, setMaking] = useState(false);
  const [title, setTitle] = useState(project.name);
  const [pick, setPick] = useState<string[]>(() => layers.filter((l) => l.visible && !(l.raster?.missing)).map((l) => l.id));
  const [audience, setAudience] = useState<"anyone" | "organization">("anyone");
  const [downloads, setDownloads] = useState(false);
  const [days, setDays] = useState<number>(0);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => { api.shares(project.id).then(setShares, setError); }, [project.id]);
  const live = (shares ?? []).filter((s) => !s.revoked_at && !(s.expires_at && new Date(s.expires_at) < new Date()));
  const order = [...layers].sort((a, b) => b.position - a.position);

  async function make() {
    setError(null);
    try {
      const s = await api.createShare(project.id, { title: title.trim() || project.name, layer_ids: pick, view: view(), audience, downloads, expires_in_days: days || null });
      setShares((x) => [s, ...(x ?? [])]); setMaking(false);
      void copy(s);
    } catch (e) { setError(e instanceof ApiError ? e : new Error("Could not make the link.")); }
  }
  async function copy(s: Share) {
    try { await navigator.clipboard.writeText(shareUrl(s.token)); setCopied(s.id); setTimeout(() => setCopied(null), 1800); toast("Link copied.", "success"); }
    catch { window.prompt("Copy the link:", shareUrl(s.token)); }
  }
  async function refresh(s: Share) {
    try {
      const u = await api.updateShare(s.id, { view: view(), layer_ids: layers.filter((l) => l.visible).map((l) => l.id).filter(Boolean) });
      setShares((x) => (x ?? []).map((y) => (y.id === u.id ? u : y)));
      toast("The link now shows the map as it is now. Its address stays the same.", "success");
    } catch (e) { setError(e); }
  }
  async function revoke(s: Share) {
    if (!window.confirm(`Turn off "${s.title}"? Anyone who opens it will be told it no longer works.`)) return;
    try { const u = await api.revokeShare(s.id); setShares((x) => (x ?? []).map((y) => (y.id === u.id ? u : y))); } catch (e) { setError(e); }
  }

  return (
    <Dialog title="Share a link to the map" onClose={onClose} wide>
      <div className="ws-form sh">
        <p className="ws-muted">A link opens a read-only map of the layers you choose, in any browser, with its legend and the details of a feature
          when clicked. It shows the layers as they are when it is opened, so later edits appear in it. You can turn a link off at any time.</p>
        <ErrorText error={error} />
        {shares === null ? <p className="ws-muted">Loading the links…</p> : live.length ? (
          <ul className="sh-list">
            {live.map((s) => (
              <li key={s.id}>
                <div className="sh-main">
                  <strong>{s.title}</strong>
                  <span className="ws-muted">{s.audience === "anyone" ? <><Globe2 size={12} aria-hidden="true" /> Anyone with the link</> : <><Lock size={12} aria-hidden="true" /> People of the organization</>}
                    {" · "}{s.layer_ids.length} layer{s.layer_ids.length === 1 ? "" : "s"}{s.downloads ? " · downloads on" : ""}
                    {" · "}{s.expires_at ? `ends ${when(s.expires_at)}` : "no end date"}{" · "}{s.views.toLocaleString("en-GB")} view{s.views === 1 ? "" : "s"}
                    {" · "}by {s.created_by_name}</span>
                  <input className="sh-url" readOnly value={shareUrl(s.token)} onFocus={(e) => e.currentTarget.select()} aria-label={`Link for ${s.title}`} />
                </div>
                <div className="sh-acts">
                  <Button small onClick={() => void copy(s)}>{copied === s.id ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}{copied === s.id ? "Copied" : "Copy"}</Button>
                  <a className="ge-btn ge-btn-sm ge-btn-ghost" href={shareUrl(s.token)} target="_blank" rel="noreferrer"><ExternalLink size={14} aria-hidden="true" />Open</a>
                  {canEdit && <IconButton label="Show the map as it is now (same link)" onClick={() => void refresh(s)}><RefreshCw size={15} /></IconButton>}
                  {canEdit && <IconButton label="Turn this link off" onClick={() => void revoke(s)}><Trash2 size={15} /></IconButton>}
                </div>
              </li>
            ))}
          </ul>
        ) : <p className="ws-muted">No links yet.</p>}

        {canEdit && !making && <div className="ws-row"><Button variant="primary" onClick={() => setMaking(true)}><Link2 size={15} aria-hidden="true" />New link</Button></div>}
        {making && <fieldset className="sh-new">
          <label className="ws-f">Title<input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /></label>
          <div className="ws-f">Layers it shows
            <div className="sh-layers">
              {order.map((l) => (
                <label key={l.id} className="lo-check"><input type="checkbox" checked={pick.includes(l.id)} disabled={!!l.raster?.missing}
                  onChange={(e) => setPick((p) => (e.target.checked ? [...p, l.id] : p.filter((x) => x !== l.id)))} />{l.name}</label>
              ))}
            </div>
          </div>
          <div className="ws-row">
            <label className="ws-f">Who can open it<select value={audience} onChange={(e) => setAudience(e.target.value as typeof audience)}>
              <option value="anyone">Anyone with the link (no sign-in)</option><option value="organization">People of the organization, signed in</option></select></label>
            <label className="ws-f">Ends<select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={0}>Never</option><option value={7}>In a week</option><option value={30}>In 30 days</option><option value={90}>In 90 days</option><option value={365}>In a year</option></select></label>
          </div>
          <label className="lo-check"><input type="checkbox" checked={downloads} onChange={(e) => setDownloads(e.target.checked)} />Let viewers download these layers (GeoJSON, GeoPackage, Shapefile, CSV, KML)</label>
          <p className="ws-muted">The link opens where the map is now: move the map first if you want it to open somewhere else.</p>
          <div className="ws-row">
            <Button variant="primary" disabled={!pick.length} onClick={() => void make()}>Make the link and copy it</Button>
            <Button variant="ghost" onClick={() => setMaking(false)}>Cancel</Button>
          </div>
        </fieldset>}
      </div>
    </Dialog>
  );
}
