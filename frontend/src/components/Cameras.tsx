/** Defence › Cameras: live cameras of the project. Add one, open its secret link on a phone (or scan the code), and watch
 *  it live here and on the map, where each camera with a position is a point (green while live). Several can be watched at
 *  once in the floating video wall over the map. */
import { useEffect, useRef, useState } from "react";
import type maplibregl from "maplibre-gl";
import QRCode from "qrcode";
import { Copy, Eye, KeyRound, MapPin, Plus, Trash2, Video, X } from "lucide-react";
import { api } from "../lib/api";
import { FONT } from "../lib/mapstyle";
import type { Camera } from "../lib/types";
import { Button, IconButton, useToast } from "./ui";

const SRC = "cam-points";             // (not "gis-…": those names are the project's own layers, which the map keeps in step)
const ago = (iso: string | null) => {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? `${s} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(iso).toLocaleDateString("en-GB");
};

/** The project's cameras, asked for every few seconds, and drawn on the map as points. */
export function useCameras(projectId: string, map: React.MutableRefObject<maplibregl.Map | null>, on: boolean, onPick: (id: string) => void) {
  const [cams, setCams] = useState<Camera[]>([]);
  const pick = useRef(onPick);
  pick.current = onPick;
  const data = useRef<GeoJSON.FeatureCollection>({ type: "FeatureCollection", features: [] });
  const hooked = useRef(new WeakSet<maplibregl.Map>());
  useEffect(() => {
    if (!on) return;
    let stop = false;
    const tick = () => api.cameras(projectId).then((c) => { if (!stop) setCams(c); }).catch(() => undefined);
    void tick();
    const t = window.setInterval(tick, 5000);
    return () => { stop = true; window.clearInterval(t); };
  }, [projectId, on]);
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const fc: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: cams.filter((c) => c.lng != null && c.lat != null).map((c) => ({
      type: "Feature", geometry: { type: "Point", coordinates: [c.lng!, c.lat!] }, properties: { id: c.id, name: c.name, live: c.live ? 1 : 0 } })) };
    data.current = fc;
    const ensure = () => {                       // (again after a style change: the map drops what it did not make)
      if (m.getSource(SRC)) return;
      try {
        m.addSource(SRC, { type: "geojson", data: data.current });
        m.addLayer({ id: `${SRC}-ring`, type: "circle", source: SRC, paint: { "circle-radius": 13, "circle-color": ["case", ["==", ["get", "live"], 1], "#1f9d55", "#6f7889"], "circle-opacity": 0.22 } });
        m.addLayer({ id: `${SRC}-dot`, type: "circle", source: SRC, paint: { "circle-radius": 7, "circle-color": ["case", ["==", ["get", "live"], 1], "#1f9d55", "#6f7889"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
        m.addSource(`${SRC}-names`, { type: "geojson", data: data.current });   // names apart: if the font cannot load, the points still show
        m.addLayer({ id: `${SRC}-label`, type: "symbol", source: `${SRC}-names`, layout: { "text-field": ["get", "name"], "text-size": 12, "text-offset": [0, 1.3], "text-anchor": "top", "text-font": [FONT[0]] },
          paint: { "text-color": "#172033", "text-halo-color": "#fff", "text-halo-width": 1.5 } });
      } catch { /* the style is being set up: on the next idle */ }
    };
    (m.getSource(SRC) as maplibregl.GeoJSONSource | undefined)?.setData(fc);
    (m.getSource(`${SRC}-names`) as maplibregl.GeoJSONSource | undefined)?.setData(fc);
    if (!hooked.current.has(m)) {                  // once per map: re-add after style changes, and the clicks
      hooked.current.add(m);
      m.on("idle", ensure);
      m.on("click", `${SRC}-dot`, (e) => { const id = e.features?.[0]?.properties?.id; if (id) pick.current(String(id)); });
      m.on("mouseenter", `${SRC}-dot`, () => { m.getCanvas().style.cursor = "pointer"; });
      m.on("mouseleave", `${SRC}-dot`, () => { m.getCanvas().style.cursor = ""; });
    }
    m.triggerRepaint();
  }, [cams, map]);
  return [cams, setCams] as const;
}

export function CamerasPane({ projectId, cams, setCams, canEdit, watching, onWatch, onZoom, onClose }: {
  projectId: string; cams: Camera[]; setCams: (c: Camera[]) => void; canEdit: boolean; watching: string[];
  onWatch: (id: string) => void; onZoom: (c: Camera) => void; onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const toast = useToast();
  const sel = cams.find((c) => c.id === open) ?? null;
  useEffect(() => {
    if (!sel) { setQr(null); return; }
    void QRCode.toDataURL(sel.send_url, { margin: 1, width: 200 }).then(setQr).catch(() => setQr(null));
  }, [sel?.send_url]); // eslint-disable-line react-hooks/exhaustive-deps

  const add = async () => {
    try { const c = await api.addCamera(projectId, name.trim() || "Phone camera"); setCams([...cams, c]); setOpen(c.id); setName(""); }
    catch (e) { toast(e instanceof Error ? e.message : String(e), "error"); }
  };
  return (
    <aside className="ws-panel cams" aria-label="Cameras">
      <div className="ws-pane-head"><strong>Cameras</strong><IconButton label="Close Cameras" onClick={onClose}><X size={16} /></IconButton></div>
      <div className="ws-scroll ws-panel-body">
        <p className="ws-muted">A phone becomes a live camera: open its link on the phone, press Start, and watch it here. Its position shows on the map.</p>
        {canEdit && <form className="cams-add" onSubmit={(e) => { e.preventDefault(); void add(); }}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. Gate 2" aria-label="New camera's name" maxLength={120} />
          <Button type="submit" variant="primary"><Plus size={15} aria-hidden="true" />Add camera</Button></form>}
        {!cams.length && <p className="ws-muted">No cameras yet.</p>}
        <ul className="cams-list">
          {cams.map((c) => (
            <li key={c.id} className={open === c.id ? "is-open" : ""}>
              <div className="cams-row">
                <span className={`cams-dot${c.live ? " is-live" : ""}`} aria-hidden="true" />
                <button type="button" className="cams-name" onClick={() => setOpen(open === c.id ? null : c.id)} aria-expanded={open === c.id}>
                  <strong>{c.name}</strong><span>{c.live ? "Live" : `Last picture ${ago(c.last_at)}`}</span></button>
                <IconButton label={watching.includes(c.id) ? `Stop watching ${c.name}` : `Watch ${c.name}`} onClick={() => onWatch(c.id)}><Eye size={16} /></IconButton>
                {c.lng != null && <IconButton label={`Find ${c.name} on the map`} onClick={() => onZoom(c)}><MapPin size={16} /></IconButton>}
              </div>
              {open === c.id && (
                <div className="cams-more">
                  {c.live && <img className="cams-preview" src={api.cameraLive(c.id)} alt={`Live from ${c.name}`} />}
                  <p><strong>On the phone:</strong> scan the code, or open the link, then press <em>Start sending</em>.</p>
                  {qr && <img className="cams-qr" src={qr} alt="Code of the camera's link" width={160} height={160} />}
                  <div className="mil-in"><input readOnly value={c.send_url} aria-label="The camera's link" onFocus={(e) => e.target.select()} />
                    <IconButton label="Copy the link" onClick={() => void navigator.clipboard.writeText(c.send_url).then(() => toast("Link copied.", "success"))}><Copy size={15} /></IconButton></div>
                  <p className="ws-muted">Whoever has the link can send pictures to this camera (never watch it). Keep it private; make a new link if it leaked.</p>
                  {c.located_at && <p className="ws-muted">Position ±{Math.round(c.accuracy ?? 0)} m, {ago(c.located_at)}.</p>}
                  {canEdit && <div className="mil-acts">
                    <Button onClick={() => void api.updateCamera(c.id, { new_address: true }).then((n) => { setCams(cams.map((x) => (x.id === n.id ? n : x))); toast("New link made: the old one stopped working.", "success"); })}>
                      <KeyRound size={14} aria-hidden="true" />New link</Button>
                    <Button onClick={() => { if (window.confirm(`Remove the camera ${c.name}?`)) void api.deleteCamera(c.id).then(() => { setCams(cams.filter((x) => x.id !== c.id)); setOpen(null); }); }}>
                      <Trash2 size={14} aria-hidden="true" />Remove</Button>
                  </div>}
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}

/** The video wall: the cameras being watched, live, over the map (up to four). */
export function CameraWall({ cams, watching, onClose }: { cams: Camera[]; watching: string[]; onClose: (id: string) => void }) {
  const shown = watching.map((id) => cams.find((c) => c.id === id)).filter(Boolean).slice(0, 4) as Camera[];
  if (!shown.length) return null;
  return (
    <section className={`camwall n${shown.length}`} aria-label="Live cameras">
      {shown.map((c) => (
        <figure key={c.id}>
          <img src={api.cameraLive(c.id)} alt={`Live from ${c.name}`} />
          <figcaption><span className={`cams-dot${c.live ? " is-live" : ""}`} aria-hidden="true" /><Video size={13} aria-hidden="true" />{c.name}
            {!c.live && <em> · not sending</em>}
            <button type="button" aria-label={`Close ${c.name}`} onClick={() => onClose(c.id)}><X size={14} /></button></figcaption>
        </figure>
      ))}
    </section>
  );
}
