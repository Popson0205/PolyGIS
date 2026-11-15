/** The Coordinate conversion pane (as ArcGIS Pro's): type or paste a position in any notation, or pick it on the map,
 *  and see it in every notation; go there, copy any of them, or keep it as a point. */
import maplibregl, { type Map as MapLibre } from "maplibre-gl";
import { Copy, Crosshair, MapPin, Navigation, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { CoordError, NOTATIONS, NOTATION_NAMES, parseCoordinate, writeCoordinate, type Notation } from "../lib/milcoords";
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
