/** The coordinate conversion pane: type or paste a position in any notation, or pick it on the map,
 *  and see it in every notation; go there, copy any of them, or keep it as a point. */
import maplibregl, { type Map as MapLibre } from "maplibre-gl";
import { Copy, Crosshair, MapPin, Navigation, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { CoordError, NOTATIONS, NOTATION_NAMES, parseCoordinate, writeCoordinate, type Notation } from "../lib/milcoords";
import type { Layer, SightResult } from "../lib/types";
import { Button, IconButton, useToast } from "./ui";

export default function CoordinatePane({ map, onClose, onPoints, onTool }: {
  map: MapLibre | null; onClose: () => void;
  /** Open a Military tool with this position (or the kept list) as its origins. */
  onTool?: (tool: "range_rings" | "bearing_lines" | "ellipse" | "distance_bearing", origins: string) => void;
  /** Keep the positions as points (they go to the Coordinates to points tool). */
  onPoints?: (text: string) => void;
}) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [as, setAs] = useState<Notation | "auto">("auto");
  const [order, setOrder] = useState<"latlon" | "lonlat">("latlon");
  const [picking, setPicking] = useState(false);
  const [kept, setKept] = useState<string[]>([]);
  const marker = useRef<maplibregl.Marker | null>(null);

  type Read = { lng: number; lat: number; n: Notation } | { error: string } | null;
  const read = useMemo((): Read => {
    if (!text.trim()) return null;
    try { const [lng, lat, n] = parseCoordinate(text, as, order); return { lng, lat, n }; }
    catch (e) { return { error: e instanceof CoordError ? e.message : "That is not a coordinate." }; }
  }, [text, as, order]);
  const at = read && "lng" in read ? read : null;
  const atLng = at?.lng, atLat = at?.lat;

  // picking: the next click on the map gives the position
  useEffect(() => {
    if (!picking || !map) return;
    const canvas = map.getCanvas();
    const was = canvas.style.cursor;
    canvas.style.cursor = "crosshair";
    const click = (e: maplibregl.MapMouseEvent) => {
      setText(writeCoordinate("mgrs", e.lngLat.lng, e.lngLat.lat) ?? writeCoordinate("dd", e.lngLat.lng, e.lngLat.lat) ?? "");
      setAs("auto"); setPicking(false);
    };
    map.once("click", click);
    return () => { map.off("click", click); canvas.style.cursor = was; };
  }, [picking, map]);
  useEffect(() => () => { marker.current?.remove(); }, []);
  // the position shown on the map while it is being typed
  useEffect(() => {
    if (!map) return;
    if (!at) { marker.current?.remove(); marker.current = null; return; }
    if (!marker.current) {
      const el = document.createElement("div");
      el.className = "mil-mark"; el.setAttribute("aria-hidden", "true");
      marker.current = new maplibregl.Marker({ element: el }).setLngLat([at.lng, at.lat]).addTo(map);
    } else marker.current.setLngLat([at.lng, at.lat]);
  }, [atLng, atLat, map]);   // eslint-disable-line react-hooks/exhaustive-deps

  const go = () => { if (at && map) map.flyTo({ center: [at.lng, at.lat], zoom: Math.max(map.getZoom(), 13) }); };
  const copy = (s: string) => { void navigator.clipboard?.writeText(s).then(() => toast("Copied.", "success"), () => toast(s, "info")); };

  return (
    <aside className="ws-panel mil" aria-label="Coordinate conversion">
      <div className="ws-pane-head"><strong>Coordinates</strong><span className="ge-spacer" /><IconButton label="Close" onClick={onClose}><X size={16} /></IconButton></div>
      <div className="ws-scroll mil-body">
        <div className="ws-f">
          <label htmlFor="mil-in">Position</label>
          <div className="mil-in">
            <input id="mil-in" value={text} spellCheck={false} placeholder="32P LR 31452 03665 · 9.0765, 7.4664 · 9°04'35&quot;N 7°27'59&quot;E"
              onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") go(); }} aria-describedby="mil-read" />
            <IconButton label="Pick a position on the map" on={picking} onClick={() => setPicking((p) => !p)}><Crosshair size={16} /></IconButton>
          </div>
          <p id="mil-read" className={`mil-read${read && "error" in read ? " is-bad" : ""}`} role="status">
            {!read ? "Type or paste any notation: MGRS, USNG, UTM, degrees, GARS or GEOREF. Or pick it on the map."
              : "error" in read ? read.error : `Read as ${NOTATION_NAMES[read.n]}.`}
          </p>
        </div>
        <div className="mil-opts">
          <label className="ws-f">Written as<select value={as} onChange={(e) => setAs(e.target.value as Notation | "auto")}>
            <option value="auto">Work it out</option>{NOTATIONS.map((n) => <option key={n} value={n}>{NOTATION_NAMES[n]}</option>)}</select></label>
          <label className="ws-f">Plain numbers are<select value={order} onChange={(e) => setOrder(e.target.value as "latlon" | "lonlat")}>
            <option value="latlon">latitude, longitude</option><option value="lonlat">longitude, latitude</option></select></label>
        </div>
        {at && <>
          <table className="mil-table"><tbody>
            {NOTATIONS.map((n) => {
              const s = writeCoordinate(n, at.lng, at.lat);
              return (
                <tr key={n} className={n === at.n ? "is-in" : ""}>
                  <th scope="row">{NOTATION_NAMES[n]}</th>
                  <td>{s ?? <span className="ws-muted">not covered here</span>}</td>
                  <td>{s && <IconButton label={`Copy the ${NOTATION_NAMES[n]}`} onClick={() => copy(s)}><Copy size={14} /></IconButton>}</td>
                </tr>
              );
            })}
          </tbody></table>
          <div className="mil-acts">
            <Button variant="primary" onClick={go}><Navigation size={15} aria-hidden="true" />Go there</Button>
            <Button onClick={() => { const s = writeCoordinate("usng", at.lng, at.lat) ?? writeCoordinate("dd", at.lng, at.lat)!; setKept((k) => (k.includes(s) ? k : [...k, s])); }}>
              <MapPin size={15} aria-hidden="true" />Keep in the list</Button>
          </div>
          {onTool && <div className="mil-acts mil-from" aria-label="From here">
            <span className="ws-muted">From here:</span>
            <button type="button" className="ws-link" onClick={() => onTool("range_rings", writeCoordinate("usng", at.lng, at.lat) ?? writeCoordinate("dd", at.lng, at.lat)!)}>Range rings</button>
            <button type="button" className="ws-link" onClick={() => onTool("bearing_lines", writeCoordinate("usng", at.lng, at.lat) ?? writeCoordinate("dd", at.lng, at.lat)!)}>Bearing lines</button>
            <button type="button" className="ws-link" onClick={() => onTool("ellipse", writeCoordinate("usng", at.lng, at.lat) ?? writeCoordinate("dd", at.lng, at.lat)!)}>Ellipse</button>
            <button type="button" className="ws-link" onClick={() => onTool("distance_bearing", writeCoordinate("usng", at.lng, at.lat) ?? writeCoordinate("dd", at.lng, at.lat)!)}>Distance to…</button>
          </div>}
        </>}
        {kept.length > 0 && <section className="mil-kept" aria-label="Kept positions">
          <h3>Kept ({kept.length})</h3>
          <ol>{kept.map((k) => <li key={k}><button type="button" className="ws-link" onClick={() => setText(k)}>{k}</button>
            <IconButton label={`Remove ${k}`} onClick={() => setKept((x) => x.filter((y) => y !== k))}><X size={13} /></IconButton></li>)}</ol>
          <div className="mil-acts">
            {onPoints && <Button variant="primary" onClick={() => onPoints(kept.join("\n"))}>Make them points…</Button>}
            {onTool && <Button onClick={() => onTool("range_rings", kept.join("\n"))}>Range rings…</Button>}
            <Button onClick={() => copy(kept.join("\n"))}><Copy size={14} aria-hidden="true" />Copy all</Button>
          </div>
        </section>}
      </div>
    </aside>
  );
}

/** Line of sight, interactive: click an observer and a target on the map; the ground between
 *  them is drawn green where it can be seen and red where it is hidden, with the terrain profile. */
export function SightPane({ map, dems, onClose }: { map: MapLibre | null; dems: Layer[]; onClose: () => void }) {
  const toast = useToast();
  const [dem, setDem] = useState(dems[0]?.id ?? "");
  const [height, setHeight] = useState(2);
  const [targetHeight, setTargetHeight] = useState(0);
  const [curvature, setCurvature] = useState(true);
  const [step, setStep] = useState<"idle" | "observer" | "target">("idle");
  const [obs, setObs] = useState<[number, number] | null>(null);
  const [tgt, setTgt] = useState<[number, number] | null>(null);
  const [res, setRes] = useState<SightResult | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!dems.some((d) => d.id === dem)) setDem(dems[0]?.id ?? ""); }, [dems, dem]);

  // the picture on the map: the segments, and the two ends
  useEffect(() => {
    if (!map) return;
    const fc: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [
      ...(res?.segments.features ?? []),
      ...(obs ? [{ type: "Feature" as const, geometry: { type: "Point" as const, coordinates: obs }, properties: { end: "observer" } }] : []),
      ...(tgt ? [{ type: "Feature" as const, geometry: { type: "Point" as const, coordinates: tgt }, properties: { end: "target" } }] : []),
    ] };
    const src = map.getSource("mil-los") as maplibregl.GeoJSONSource | undefined;
    if (src) src.setData(fc);
    else if (map.isStyleLoaded()) {
      map.addSource("mil-los", { type: "geojson", data: fc });
      map.addLayer({ id: "mil-los-line", type: "line", source: "mil-los", filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round" }, paint: { "line-width": 4, "line-color": ["match", ["get", "visible"], "Visible", "#1a9850", "#d73027"] } });
      map.addLayer({ id: "mil-los-end", type: "circle", source: "mil-los", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 6, "circle-color": ["match", ["get", "end"], "observer", "#1d3557", "#c0262d"], "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    }
  }, [map, res, obs, tgt]);
  useEffect(() => () => {
    if (!map) return;
    for (const id of ["mil-los-line", "mil-los-end"]) if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource("mil-los")) map.removeSource("mil-los");
  }, [map]);

  // picking: the observer, then the target
  useEffect(() => {
    if (step === "idle" || !map) return;
    const canvas = map.getCanvas();
    const was = canvas.style.cursor;
    canvas.style.cursor = "crosshair";
    const click = (e: maplibregl.MapMouseEvent) => {
      const p: [number, number] = [e.lngLat.lng, e.lngLat.lat];
      if (step === "observer") { setObs(p); setTgt(null); setRes(null); setStep("target"); }
      else { setTgt(p); setStep("idle"); }
    };
    map.once("click", click);
    return () => { map.off("click", click); canvas.style.cursor = was; };
  }, [step, map]);

  // worked out again whenever the ends or the settings change
  useEffect(() => {
    if (!obs || !tgt || !dem) return;
    let live = true;
    setBusy(true);
    api.lineOfSight(dem, { observer: obs, target: tgt, height, target_height: targetHeight, curvature })
      .then((r) => { if (live) setRes(r); }, (e) => { if (live) { setRes(null); toast(e instanceof Error ? e.message : String(e), "error"); } })
      .finally(() => live && setBusy(false));
    return () => { live = false; };
  }, [obs, tgt, dem, height, targetHeight, curvature]);   // eslint-disable-line react-hooks/exhaustive-deps

  const say = (v: number | null | undefined, unit = " m") => (v == null ? "–" : `${v.toLocaleString("en-GB", { maximumFractionDigits: 1 })}${unit}`);
  return (
    <aside className="ws-panel mil" aria-label="Line of sight">
      <div className="ws-pane-head"><strong>Line of sight</strong><span className="ge-spacer" /><IconButton label="Close" onClick={onClose}><X size={16} /></IconButton></div>
      <div className="ws-scroll mil-body">
        {!dems.length ? <p className="ws-muted">Add an elevation model (a DEM raster) to this map first: Imagery → Get data from an open source has SRTM and Copernicus DEMs.</p> : <>
          <label className="ws-f">Elevation model<select value={dem} onChange={(e) => setDem(e.target.value)}>{dems.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
          <div className="mil-opts">
            <label className="ws-f">Observer height (m)<input type="number" min={0} step="any" value={height} onChange={(e) => setHeight(Math.max(0, Number(e.target.value) || 0))} /></label>
            <label className="ws-f">Target height (m)<input type="number" min={0} step="any" value={targetHeight} onChange={(e) => setTargetHeight(Math.max(0, Number(e.target.value) || 0))} /></label>
          </div>
          <label className="ws-check"><input type="checkbox" checked={curvature} onChange={(e) => setCurvature(e.target.checked)} /> Allow for the earth's curvature and refraction</label>
          <Button variant="primary" onClick={() => setStep("observer")}><Crosshair size={15} aria-hidden="true" />
            {step === "observer" ? "Click the observer on the map…" : step === "target" ? "Now click the target…" : obs ? "Pick again" : "Pick observer and target"}</Button>
          {busy && <p className="ws-muted" role="status">Working out the line of sight…</p>}
          {res && <>
            <p className={`mil-verdict ${res.target_visible ? "is-seen" : "is-hidden"}`} role="status">
              {res.target_visible ? "The target can be seen." : res.blocked_at_m != null ? `The target is hidden: the ground blocks the view ${say(res.blocked_at_m)} from the observer.` : "The target is hidden."}
            </p>
            <Profile r={res} />
            <table className="mil-table"><tbody>
              <tr><th scope="row">Distance</th><td>{say(res.distance_m)}</td></tr>
              <tr><th scope="row">Observer</th><td>{writeCoordinate("usng", obs![0], obs![1]) ?? ""}<br />ground {say(res.observer_ground)}, eye {say(res.observer_eye)}</td></tr>
              <tr><th scope="row">Target</th><td>{writeCoordinate("usng", tgt![0], tgt![1]) ?? ""}<br />ground {say(res.target_ground)}</td></tr>
              <tr><th scope="row">Ground seen</th><td>{Math.round(res.visible_share * 100)}% of the way</td></tr>
            </tbody></table>
          </>}
        </>}
      </div>
    </aside>
  );
}

/** The terrain between observer and target: ground seen (green) and hidden (red), and the sight line. */
function Profile({ r }: { r: SightResult }) {
  const W = 300, H = 140, pad = { l: 36, r: 8, t: 8, b: 20 };
  const pts = r.profile.filter((p) => p.z != null) as { d: number; z: number; seen: boolean; sight: number | null }[];
  if (pts.length < 2) return null;
  const zs = [...pts.map((p) => p.z), ...(r.observer_eye != null ? [r.observer_eye] : []), ...pts.map((p) => p.sight ?? p.z)];
  const lo = Math.min(...zs), hi = Math.max(...zs), span = hi - lo || 1;
  const X = (d: number) => pad.l + (d / r.distance_m) * (W - pad.l - pad.r);
  const Y = (z: number) => H - pad.b - ((z - lo) / span) * (H - pad.t - pad.b);
  const runs: { seen: boolean; d: string }[] = [];
  for (let i = 1; i < pts.length; i++) {
    const seg = `M${X(pts[i - 1].d).toFixed(1)},${Y(pts[i - 1].z).toFixed(1)}L${X(pts[i].d).toFixed(1)},${Y(pts[i].z).toFixed(1)}`;
    const last = runs[runs.length - 1];
    if (last && last.seen === pts[i].seen) last.d += seg; else runs.push({ seen: pts[i].seen, d: seg });
  }
  const ground = `M${X(pts[0].d)},${H - pad.b}` + pts.map((p) => `L${X(p.d).toFixed(1)},${Y(p.z).toFixed(1)}`).join("") + `L${X(pts[pts.length - 1].d)},${H - pad.b}Z`;
  const sight = pts.filter((p) => p.sight != null).map((p, i) => `${i ? "L" : "M"}${X(p.d).toFixed(1)},${Y(p.sight!).toFixed(1)}`).join("");
  return (
    <figure className="mil-profile">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Terrain profile over ${Math.round(r.distance_m)} metres, from ${Math.round(lo)} to ${Math.round(hi)} metres high`}>
        <path d={ground} fill="#e9ecef" />
        {runs.map((s, i) => <path key={i} d={s.d} fill="none" stroke={s.seen ? "#1a9850" : "#d73027"} strokeWidth={2.2} />)}
        <path d={sight} fill="none" stroke="#1d3557" strokeWidth={1.2} strokeDasharray="4 3" />
        {r.blocked_at_m != null && <line x1={X(r.blocked_at_m)} x2={X(r.blocked_at_m)} y1={pad.t} y2={H - pad.b} stroke="#d73027" strokeWidth={1} strokeDasharray="2 2" />}
        <text x={pad.l - 4} y={Y(hi) + 4} textAnchor="end" fontSize="9" fill="#5b6475">{Math.round(hi)}</text>
        <text x={pad.l - 4} y={Y(lo)} textAnchor="end" fontSize="9" fill="#5b6475">{Math.round(lo)}</text>
        <text x={pad.l} y={H - 6} fontSize="9" fill="#5b6475">0</text>
        <text x={W - pad.r} y={H - 6} textAnchor="end" fontSize="9" fill="#5b6475">{r.distance_m >= 1000 ? `${(r.distance_m / 1000).toFixed(2)} km` : `${Math.round(r.distance_m)} m`}</text>
      </svg>
      <figcaption><span className="mil-key is-seen" />seen <span className="mil-key is-hidden" />hidden <span className="mil-key is-sight" />sight line</figcaption>
    </figure>
  );
}
