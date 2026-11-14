import { ExternalLink, Loader2, UploadCloud } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api, type HubDataset } from "../lib/api";
import type { Layer } from "../lib/types";
import { Dialog } from "./Dialogs";
import { Button, ErrorText, useToast } from "./ui";

const LICENCES: [string, string][] = [["CC-BY-4.0", "CC BY 4.0: anyone may use it, crediting you"], ["ODbL-1.0", "ODbL 1.0: open, share-alike"],
  ["CC0-1.0", "CC0: no rights reserved"], ["Private", "Private: not for reuse"]];

/** Share › Publish to Data hub: the active layer (or its selection) as a dataset in Geohub's Data hub, or the next version of
 *  one published from it before. Geohub keeps the versions, previews and downloads. A raster layer goes as its GeoTIFF;
 *  Geohub then prepares it, and the dialog follows that until it is ready. */
export default function PublishDialog({ layer, selected, onClose }: { layer: Layer; selected: number[]; onClose: () => void }) {
  const toast = useToast();
  const [mine, setMine] = useState<HubDataset[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState("");
  const [onlySel, setOnlySel] = useState(selected.length > 0);
  const [title, setTitle] = useState(layer.name);
  const [description, setDescription] = useState("");
  const [license, setLicense] = useState("CC-BY-4.0");
  const [visibility, setVisibility] = useState("organization");
  const [tags, setTags] = useState("");
  const [done, setDone] = useState<(HubDataset & { version: number }) | null>(null);
  useEffect(() => { api.hubDatasets(layer.id).then(setMine, () => setMine([])); }, [layer.id]);
  const raster = layer.geometry_type === "Raster";
  const ids = !raster && onlySel && selected.length ? selected : undefined;
  const count = ids ? ids.length : layer.feature_count;

  // a raster Geohub is still preparing: ask how it is going every few seconds, for up to half an hour
  useEffect(() => {
    if (!done?.upload || done.status !== "processing") return;
    let n = 0;
    const t = setInterval(() => {
      if (++n > 600) { clearInterval(t); return; }
      api.hubUpload(layer.id, done.upload!).then((u) => {
        if (u.status === "processing") return;
        clearInterval(t);
        setDone({ ...done, ...u, version: u.version ?? done.version });
        if (u.status === "ready") toast(`${done.title} is ready in the Data hub (version ${u.version}).`, "success");
      }, () => undefined);
    }, 3000);
    return () => clearInterval(t);
  }, [done?.upload, done?.status]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function run(f: () => Promise<HubDataset & { version: number }>, what: string) {
    setError(null); setBusy(what);
    try {
      const d = await f(); setDone(d);
      toast(d.status === "processing" ? `${d.title}: sent to Geohub, which is preparing it.` : `Published: version ${d.version} of ${d.title}.`, "success");
    }
    catch (e) { setError(e instanceof ApiError ? e : new Error("Could not publish.")); }
    finally { setBusy(""); }
  }
  return (
    <Dialog title={`Publish ${layer.name} to the Data hub`} onClose={onClose} wide>
      <div className="ws-form">
        <p className="ws-muted">{raster
          ? <>The Data hub is Geohub's catalogue of datasets. Publishing sends the raster there as a GeoTIFF; Geohub prepares it (a few minutes for
            a large one), then people find it, see it on a map with this layer's colours, and download it, as you choose who may. Publish again for the next version.</>
          : <>The Data hub is Geohub's catalogue of datasets. Publishing copies the features and their fields there as a
          version that never changes; publish again for the next version. People find, preview and download it there, as you choose who may.</>}</p>
        <ErrorText error={error} />
        {done ? <div className={`pb-done${done.status === "failed" ? " is-failed" : ""}`} role="status">
          {done.status === "processing" ? <Loader2 size={22} className="pb-spin" aria-hidden="true" /> : <UploadCloud size={22} aria-hidden="true" />}
          <div>{done.status === "processing" ? <><strong>{done.title}</strong>: sent. Geohub is preparing the raster; this page updates when it is ready.</>
            : done.status === "failed" ? <><strong>{done.title}</strong> could not be prepared: {done.error}</>
            : raster ? <><strong>{done.title}</strong>, version {done.version}: the raster is in the Data hub.</>
            : <><strong>{done.title}</strong>, version {done.version}: {done.feature_count.toLocaleString("en-GB")} features are in the Data hub.</>}</div>
          {done.url && done.status !== "failed" && <a className="ge-btn ge-btn-sm ge-btn-primary" href={done.url} target="_blank" rel="noreferrer"><ExternalLink size={14} aria-hidden="true" />Open in Geohub</a>}
        </div> : <>
          {!raster && selected.length > 0 && <label className="lo-check"><input type="checkbox" checked={onlySel} onChange={(e) => setOnlySel(e.target.checked)} />
            Only the {selected.length.toLocaleString("en-GB")} selected feature{selected.length === 1 ? "" : "s"}</label>}
          {mine && mine.length > 0 && <fieldset className="sh-new"><legend className="pb-legend">Already published from this layer</legend>
            <ul className="sh-list">{mine.map((d) => (
              <li key={d.slug}><div className="sh-main"><strong>{d.title}</strong>
                <span className="ws-muted">{d.latest_version ? `Version ${d.latest_version}` : "Being prepared"} · {raster ? "raster" : `${d.feature_count.toLocaleString("en-GB")} features`} · {d.visibility === "public" ? "public" : d.visibility === "organization" ? "the organization" : "private"}</span></div>
                <div className="sh-acts"><Button small disabled={!!busy} busy={busy === d.slug} onClick={() => void run(() => api.publishHubVersion(layer.id, d.slug, ids), d.slug)}>
                  Publish version {d.latest_version + 1}</Button>
                  <a className="ge-btn ge-btn-sm ge-btn-ghost" href={d.url} target="_blank" rel="noreferrer"><ExternalLink size={14} aria-hidden="true" />Open</a></div></li>
            ))}</ul></fieldset>}
          <fieldset className="sh-new"><legend className="pb-legend">{mine?.length ? "Or a new dataset" : "A new dataset"}</legend>
            <label className="ws-f">Title<input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /></label>
            <label className="ws-f">Description<textarea rows={3} maxLength={5000} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What it is, where it came from, how current it is" /></label>
            <div className="ws-row">
              <label className="ws-f">Who can see it<select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
                <option value="private">Only me</option><option value="organization">My organization</option><option value="public">Anyone (public)</option></select></label>
              <label className="ws-f">Licence<select value={license} onChange={(e) => setLicense(e.target.value)}>{LICENCES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
            </div>
            <label className="ws-f">Tags<input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="Comma-separated, e.g. minna, parcels, 2026" /></label>
            <div className="ws-row"><Button variant="primary" disabled={!title.trim() || !!busy || (!raster && !count)} busy={busy === "new"}
              onClick={() => void run(() => api.publishHub(layer.id, { title: title.trim(), description, license, visibility, ids,
                tags: tags.split(",").map((t) => t.trim()).filter(Boolean).slice(0, 20) }), "new")}>
              <UploadCloud size={15} aria-hidden="true" />{raster ? "Publish the raster" : `Publish ${count.toLocaleString("en-GB")} feature${count === 1 ? "" : "s"}`}</Button>
              <Button variant="ghost" onClick={onClose}>Cancel</Button></div>
          </fieldset>
        </>}
      </div>
    </Dialog>
  );
}
