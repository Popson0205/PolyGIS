import { useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import type { ImageryProvider, ImageryProviderId, ImageryRequest, ImageryScene, Layer, Satellite } from "../lib/types";
import { Dialog } from "./Dialogs";
import { Button, ErrorText } from "./ui";

type Box = [number, number, number, number];
const MOST_SCENES = 12;

const day = (back: number) => new Date(Date.now() - back * 86_400_000).toISOString().slice(0, 10);

/** The area in words: its corners, and about how big it is on the ground. */
export function describeBox([w, s, e, n]: Box): string {
  const km = (v: number) => (v >= 100 ? v.toFixed(0) : v.toFixed(1));
  const wide = (e - w) * 111.32 * Math.cos((((s + n) / 2) * Math.PI) / 180);
  const high = (n - s) * 110.57;
  return `${w.toFixed(3)}° to ${e.toFixed(3)}° E, ${s.toFixed(3)}° to ${n.toFixed(3)}° N · about ${km(wide)} × ${km(high)} km`;
}

/** Get satellite imagery for the map's view from an open source (Planetary Computer, Earth Search, Google Earth Engine) as a new raster layer. */
export function ImageryDialog({ projectId, viewBox, layerBox, layerName, onDone, onClose }:
  { projectId: string; viewBox: Box; layerBox: Box | null; layerName?: string; onDone: (l: Layer) => void; onClose: () => void }) {
  const [view] = useState(viewBox);                                   // the map does not move while the dialog is open
  const [providers, setProviders] = useState<ImageryProvider[] | null>(null);
  const [provider, setProvider] = useState<ImageryProviderId>("planetary-computer");
  const [satellite, setSatellite] = useState<Satellite>("sentinel2");
  const [start, setStart] = useState(day(90));
  const [end, setEnd] = useState(day(0));
  const [cloud, setCloud] = useState(30);
  const [areaOf, setAreaOf] = useState<"view" | "layer">("view");
  const [name, setName] = useState("");
  const [scenes, setScenes] = useState<ImageryScene[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<"search" | "fetch" | null>(null);

  useEffect(() => { void api.imageryProviders().then(setProviders, () => setProviders([])); }, []);
  const current = providers?.find((p) => p.id === provider);
  const satellites = current?.satellites ?? [];
  useEffect(() => {                                                  // a source may not have the satellite chosen: take its first
    if (satellites.length && !satellites.some((s) => s.id === satellite)) setSatellite(satellites[0].id);
  }, [satellites, satellite]);

  const bbox = areaOf === "layer" && layerBox ? layerBox : view;
  const request = (): ImageryRequest => ({ provider, satellite, bbox, start, end, max_cloud: cloud });
  /** Changing what is asked for makes the list of scenes found before out of date. */
  const change = <T,>(set: (v: T) => void) => (v: T) => { set(v); setScenes(null); setPicked([]); };

  async function find() {
    setBusy("search"); setError(null);
    try { const r = await api.searchImagery(request()); setScenes(r.scenes); setPicked([]); } catch (x) { setError(x); } finally { setBusy(null); }
  }
  async function add(e: FormEvent) {
    e.preventDefault();
    setBusy("fetch"); setError(null);
    try { onDone(await api.fetchImagery(projectId, { ...request(), scene_ids: picked, name: name.trim() || undefined })); } catch (x) { setError(x); } finally { setBusy(null); }
  }
  const toggle = (id: string) => setPicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length < MOST_SCENES ? [...cur, id] : cur));
  const ready = !!current?.ready && start <= end;

  return (
    <Dialog title="Satellite imagery from an open source" onClose={onClose} wide>
      <form className="ws-form" onSubmit={add}>
        <p className="ws-muted">PolyGIS fetches the part of the image inside the area you choose and adds it as a raster layer, so you need not download scenes yourself.
          The bands are red, green, blue and near-infrared, so the NDVI tool works on it straight away.</p>
        <div className="ws-row">
          <label className="ws-f">Source
            <select value={provider} onChange={(e) => change(setProvider)(e.target.value as ImageryProviderId)} disabled={!providers}>
              {(providers ?? []).map((p) => <option key={p.id} value={p.id} disabled={!p.ready}>{p.name}{p.ready ? "" : " (not set up)"}</option>)}
            </select>
            {current && <span className="ws-help">{current.note}</span>}
          </label>
          <label className="ws-f">Satellite
            <select value={satellite} onChange={(e) => change(setSatellite)(e.target.value as Satellite)} disabled={!satellites.length}>
              {satellites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        </div>
        <div className="ws-row">
          <label className="ws-f">From <input type="date" value={start} max={end} onChange={(e) => change(setStart)(e.target.value)} required /></label>
          <label className="ws-f">To <input type="date" value={end} min={start} max={day(0)} onChange={(e) => change(setEnd)(e.target.value)} required /></label>
        </div>
        <label className="ws-f">Most cloud allowed: {cloud} %
          <input type="range" min={0} max={100} step={5} value={cloud} onChange={(e) => change(setCloud)(Number(e.target.value))} />
        </label>
        <div className="ws-f"><span>Area</span>
          <label className="ws-check"><input type="radio" name="area" checked={areaOf === "view"} onChange={() => change(setAreaOf)("view")} />What the map shows now</label>
          {layerBox && <label className="ws-check"><input type="radio" name="area" checked={areaOf === "layer"} onChange={() => change(setAreaOf)("layer")} />Extent of the selected layer{layerName ? `: ${layerName}` : ""}</label>}
          <span className="ws-help">{describeBox(bbox)}. Zoom in first for a small area: it is quicker, and keeps the cells at the satellite&apos;s own size.</span>
        </div>

        <div className="ws-row" style={{ justifyContent: "flex-start" }}>
          <Button variant="default" busy={busy === "search"} disabled={!ready || busy === "fetch"} onClick={() => void find()}>Find scenes</Button>
          <span className="ws-muted">Optional: choose the scenes yourself. Otherwise PolyGIS uses the least cloudy{provider === "gee" ? " ones, as a cloud-masked median composite" : " ones, joining them where one does not cover the area"}.</span>
        </div>
        {scenes && (scenes.length === 0
          ? <p className="ws-muted" role="status">No scenes found for that area and period. Try a longer period, or allow more cloud.</p>
          : <fieldset className="ws-fieldset" style={{ maxHeight: 180, overflowY: "auto" }}>
            <legend>{scenes.length} scene{scenes.length > 1 ? "s" : ""}, least cloudy first{picked.length ? ` · ${picked.length} chosen` : ""}</legend>
            {scenes.map((s) => (
              <label key={s.id} className="ws-check" title={s.id}>
                <input type="checkbox" checked={picked.includes(s.id)} onChange={() => toggle(s.id)} />
                <span>{s.datetime ? s.datetime.slice(0, 10) : "–"} · {s.cloud == null ? "cloud unknown" : `${s.cloud.toFixed(0)} % cloud`}{s.platform ? ` · ${s.platform}` : ""}</span>
              </label>
            ))}
          </fieldset>)}

        <label className="ws-f">Layer name (optional) <input maxLength={200} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Minna, dry season" /></label>
        <ErrorText error={error} />
        {busy === "fetch" && <p className="ws-muted" role="status">Fetching the imagery: this can take a minute or two for a large area.</p>}
        <div className="ws-row">
          <Button type="submit" variant="primary" busy={busy === "fetch"} disabled={!ready || busy === "search"}>
            {picked.length ? `Add ${picked.length} chosen scene${picked.length > 1 ? "s" : ""} to the map` : "Add the least cloudy imagery to the map"}
          </Button>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </form>
    </Dialog>
  );
}
