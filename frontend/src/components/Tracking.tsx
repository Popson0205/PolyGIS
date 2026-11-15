/** Live tracking: the Tracking pane (add and manage feeds: own devices and apps, aircraft by ADS-B, ships by AIS, a Geohub
 *  form's submissions; how to set each device up; geofences; tracks kept as a layer) and the live picture on the map. */
import maplibregl, { type Map as MapLibre } from "maplibre-gl";
import { AlertTriangle, Copy, History, MessageCircle, Pause, Play, Plus, Radio, RefreshCw, Send, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, type LinkedSource } from "../lib/api";
import { MIL_PREFIX } from "../lib/milsym";
import type { Layer, Tracker, TrackAlert, TrackingLive, TrackNotify } from "../lib/types";
import { Button, ErrorText, IconButton, useToast } from "./ui";

const KINDS: { id: Tracker["kind"]; name: string; about: string }[] = [
  { id: "device", name: "Own devices and apps", about: "Phones and GPS trackers send their positions to an address: Traccar Client, OsmAnd, OwnTracks, a Traccar server, or any system that can post JSON." },
  { id: "adsb", name: "Aircraft (ADS-B)", about: "Aircraft over an area, from adsb.lol (open data, no key) or the OpenSky Network." },
  { id: "ais", name: "Ships (AIS)", about: "Ships over an area, from aisstream.io: a free API key from aisstream.io is needed." },
  { id: "geohub", name: "Geohub form", about: "Each submission of a Geohub form (patrol check-ins, sightings, reports) as a position of the person who sent it." },
];
/** How often the feeds PolyGIS asks are asked while the picture is live, in seconds. */
const EVERY: Record<Tracker["kind"], number> = { device: 0, adsb: 10, ais: 30, geohub: 30 };
const ago = (iso: string | null) => {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 60 ? `${Math.round(s)} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(iso).toLocaleString("en-GB");
};

export function TrackingPane({ projectId, layers, canEdit, live, onLive, alerts, onAlertsSeen, view, onLayer, onZoom, onClose }: {
  projectId: string; layers: Layer[]; canEdit: boolean; live: boolean; onLive: (on: boolean) => void;
  alerts: TrackAlert[]; onAlertsSeen: () => void;
  view: () => [number, number, number, number] | null; onLayer: (l: Layer) => void; onZoom: (x: number, y: number) => void; onClose: () => void;
}) {
  const toast = useToast();
  const [list, setList] = useState<Tracker[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const load = () => api.trackers(projectId).then(setList, setError);
  useEffect(() => { void load(); const t = setInterval(() => void load(), 15000); return () => clearInterval(t); }, [projectId]);   // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (f: () => Promise<unknown>, done?: string) => {
    try { await f(); if (done) toast(done, "success"); await load(); } catch (e) { toast(e instanceof ApiError ? e.message : String(e), "error"); }
  };
  return (
    <aside className="ws-panel mil trk" aria-label="Tracking">
      <div className="ws-pane-head"><strong>Tracking</strong><span className="ge-spacer" /><IconButton label="Close" onClick={onClose}><X size={16} /></IconButton></div>
      <div className="ws-scroll mil-body">
        <div className="trk-live">
          <Button variant={live ? "primary" : undefined} onClick={() => onLive(!live)}>{live ? <><Pause size={15} aria-hidden="true" />Live: on</> : <><Play size={15} aria-hidden="true" />Show live</>}</Button>
          <span className="ws-muted">{live ? "Positions every 5 s; aircraft every 10 s, ships and Geohub every 30 s." : "Turn on to follow positions on the map."}</span>
        </div>
        {alerts.length > 0 && <section className="trk-alerts" aria-label="Alerts">
          <h3><AlertTriangle size={14} aria-hidden="true" />Alerts ({alerts.length})<span className="ge-spacer" /><button type="button" className="ws-link" onClick={onAlertsSeen}>Mark all seen</button></h3>
          <ul>{alerts.slice(0, 20).map((a) => (
            <li key={a.id}><button type="button" className="ws-link" onClick={() => onZoom(a.x, a.y)}>
              {a.kind === "new" ? <>New device: <strong>{a.name || a.device}</strong></>
                : <><strong>{a.name || a.device}</strong> {a.kind === "enter" ? "entered" : "left"} <strong>{a.zone}</strong></>}</button>
              <span className="ws-muted"> · {a.tracker} · {ago(a.ts)}</span></li>))}</ul>
        </section>}
        <ErrorText error={error} />
        {list && !list.length && !adding && <p className="ws-muted">No trackers yet. Add one: your own devices, aircraft, ships or a Geohub form.</p>}
        <ul className="trk-list">
          {(list ?? []).map((t) => (
            <li key={t.id} className={`trk-item${t.active ? "" : " is-paused"}`}>
              <button type="button" className="trk-row" onClick={() => setOpen(open === t.id ? null : t.id)} aria-expanded={open === t.id}>
                <Radio size={15} aria-hidden="true" />
                <span className="trk-name">{t.name}<span className="ws-muted"> · {t.kind_name}</span></span>
                <span className="trk-meta">{t.kind !== "device" && t.asked_at ? `${t.last_seen ?? 0} now · asked ${ago(t.asked_at)}` : `${t.devices} · ${ago(t.last_at)}`}</span>
              </button>
              {t.last_error && <p className="trk-err">{t.last_error}</p>}
              {open === t.id && <TrackerDetail t={t} layers={layers} canEdit={canEdit} view={view} act={act}
                onLayer={onLayer} />}
            </li>))}
        </ul>
        {canEdit && (adding ? <AddTracker projectId={projectId} layers={layers} view={view} onDone={(l) => { setAdding(false); if (l) { onLayer(l); void load(); } }} />
          : <Button onClick={() => setAdding(true)}><Plus size={15} aria-hidden="true" />Add a tracker</Button>)}
      </div>
    </aside>
  );
}

function AddTracker({ projectId, layers, view, onDone }: { projectId: string; layers: Layer[]; view: () => [number, number, number, number] | null; onDone: (l: Layer | null) => void }) {
  const toast = useToast();
  const [kind, setKind] = useState<Tracker["kind"]>("device");
  const [name, setName] = useState("");
  const [source, setSource] = useState("adsb.lol");
  const [key, setKey] = useState("");
  const [form, setForm] = useState("");
  const [forms, setForms] = useState<LinkedSource[]>([]);
  const [fence, setFence] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { if (kind === "geohub") api.linkedSources(projectId).then((s) => setForms(s.filter((x) => x.app === "hub" && x.form_id)), () => setForms([])); }, [kind, projectId]);
  const polys = layers.filter((l) => l.geometry_type === "Polygon");
  const k = KINDS.find((x) => x.id === kind)!;
  async function go() {
    setBusy(true); setError(null);
    try {
      const config: Record<string, unknown> = { ...(fence ? { geofence_layer_id: fence } : {}) };
      if (kind === "adsb" || kind === "ais") config.bbox = view();
      if (kind === "adsb") config.source = source;
      if (kind === "geohub") { config.form_id = form; config.form_label = forms.find((f) => f.form_id === form)?.label; }
      const t = await api.newTracker(projectId, { name: name.trim() || k.name, kind, config, ...(kind === "ais" && key ? { secret: { api_key: key } } : {}) });
      toast(`${t.name} added.${kind === "device" ? " Open it for the address to send to." : ""}`, "success");
      onDone(t.layer);
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  return (
    <form className="trk-add" onSubmit={(e) => { e.preventDefault(); void go(); }}>
      <h3>Add a tracker</h3>
      <div className="trk-kinds" role="radiogroup" aria-label="What to track">
        {KINDS.map((x) => <button key={x.id} type="button" role="radio" aria-checked={kind === x.id} className={`trk-kind${kind === x.id ? " is-on" : ""}`} onClick={() => setKind(x.id)}>{x.name}</button>)}
      </div>
      <p className="ws-muted">{k.about}</p>
      <label className="ws-f">Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder={k.name} maxLength={200} /></label>
      {(kind === "adsb" || kind === "ais") && <p className="ws-muted">The feed covers the map as it is now (at most about 20° × 20°): zoom to the area first.</p>}
      {kind === "adsb" && <label className="ws-f">Source<select value={source} onChange={(e) => setSource(e.target.value)}>
        <option value="adsb.lol">adsb.lol (open data, no key; recommended)</option><option value="opensky">OpenSky Network (often refuses cloud servers; adsb.lol is used then)</option></select></label>}
      {kind === "ais" && <label className="ws-f">aisstream.io API key<input value={key} onChange={(e) => setKey(e.target.value)} placeholder="Free at aisstream.io" autoComplete="off" /></label>}
      {kind === "geohub" && <label className="ws-f">Form<select value={form} onChange={(e) => setForm(e.target.value)} required>
        <option value="">{forms.length ? "Choose…" : "No Geohub forms you can open"}</option>{forms.map((f) => <option key={f.form_id} value={f.form_id}>{f.label}</option>)}</select></label>}
      <label className="ws-f">Geofences (optional)<select value={fence} onChange={(e) => setFence(e.target.value)}>
        <option value="">None</option>{polys.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
      <ErrorText error={error} />
      <div className="mil-acts"><Button type="submit" variant="primary" busy={busy}>Add</Button><Button onClick={() => onDone(null)}>Cancel</Button></div>
    </form>
  );
}

/** Across the area, in km (the narrower way). */
const km = (b: [number, number, number, number]) => Math.round(Math.min((b[2] - b[0]) * 111 * Math.cos(((b[1] + b[3]) / 2) * Math.PI / 180), (b[3] - b[1]) * 111));
const small = (b: [number, number, number, number]) => km(b) < 60;

const SENDERS: { id: TrackNotify["provider"]; name: string; keys: { k: string; label: string; optional?: boolean }[]; how: string }[] = [
  { id: "waha", name: "WAHA (free, self-hosted)", keys: [{ k: "waha_url", label: "WAHA address" }, { k: "waha_key", label: "WAHA API key (if set)", optional: true },
    { k: "waha_session", label: "Session (default: default)", optional: true }],
    how: "Free and open source: deploy WAHA (devlikeapro/waha) as a service in your Railway project and link a WhatsApp number to it by scanning its QR code (as WhatsApp Web). Use a spare number: it sends the alerts. The address is e.g. http://waha.railway.internal:3000. WAHA is not an official WhatsApp API, so WhatsApp may block a number that sends a lot." },
  { id: "brevo", name: "Brevo", keys: [{ k: "brevo_key", label: "Brevo API key" }, { k: "brevo_sender", label: "Sender (your WhatsApp number on Brevo)" },
    { k: "brevo_template", label: "Template ID (approved WhatsApp template)", optional: true }],
    how: "WhatsApp turned on in Brevo (Settings → WhatsApp, with a sender number). WhatsApp only lets a business start a conversation with an approved template: make one in Brevo (e.g. “PolyGIS alert: {{params.ALERT}}”) and give its ID. Plain text is sent while the conversation is open, the template otherwise." },
  { id: "callmebot", name: "CallMeBot (free, own number)", keys: [{ k: "callmebot_key", label: "CallMeBot API key" }],
    how: "From the WhatsApp number that gets the alerts, send “I allow callmebot to send me messages” to CallMeBot's number (see callmebot.com); it answers with the API key." },
  { id: "meta", name: "WhatsApp Cloud API (Meta)", keys: [{ k: "wa_token", label: "Access token" }, { k: "wa_phone_id", label: "Phone number ID" },
    { k: "wa_template", label: "Template name (optional)", optional: true }],
    how: "A WhatsApp Business app in Meta for Developers. Plain messages reach the number only within 24 hours of it writing to your business number; otherwise give an approved template whose body has one {{1}} variable." },
  { id: "twilio", name: "Twilio WhatsApp", keys: [{ k: "twilio_sid", label: "Account SID" }, { k: "twilio_token", label: "Auth token" }, { k: "twilio_from", label: "From (your Twilio WhatsApp number)" }],
    how: "A Twilio account with a WhatsApp sender, or its sandbox (the number joins it first by sending the sandbox's join code)." },
];
const EVENT_NAMES: Record<TrackNotify["events"][number], string> = { new: "A new device is seen", enter: "A device enters a geofence", exit: "A device leaves a geofence" };

/** Alerts of the tracker sent to a WhatsApp number. The keys stay on the server: the page only learns which are set. */
function WhatsApp({ t, act }: { t: Tracker; act: (f: () => Promise<unknown>, done?: string) => Promise<void> }) {
  const n = t.config.notify;
  const [provider, setProvider] = useState<TrackNotify["provider"]>(n?.provider ?? "waha");
  const [to, setTo] = useState(n?.to ?? "");
  const [events, setEvents] = useState<TrackNotify["events"]>(n?.events ?? (t.kind === "device" || t.kind === "geohub" ? ["new", "enter", "exit"] : ["enter", "exit"]));
  const [keys, setKeys] = useState<Record<string, string>>({});
  const s = SENDERS.find((x) => x.id === provider)!;
  const save = () => act(() => api.updateTracker(t.id, {
    config: { notify: to.trim() ? { provider, to, events, active: true } : null },
    ...(Object.values(keys).some(Boolean) ? { secret: Object.fromEntries(Object.entries(keys).filter(([, v]) => v)) } : {}),
  }).then(() => setKeys({})), to.trim() ? "WhatsApp alerts saved." : "WhatsApp alerts turned off.");
  return (
    <details className="trk-setup trk-wa" open={!!n}>
      <summary><MessageCircle size={14} aria-hidden="true" /> WhatsApp alerts{n ? (n.active ? ` · to ${n.to}` : " · off") : ""}</summary>
      <label className="ws-f">Send with<select value={provider} onChange={(e) => setProvider(e.target.value as TrackNotify["provider"])}>
        {SENDERS.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
      <p className="ws-muted">{s.how}</p>
      <label className="ws-f">WhatsApp number<input value={to} onChange={(e) => setTo(e.target.value)} placeholder="+234 …" inputMode="tel" autoComplete="off" /></label>
      {s.keys.map(({ k, label }) => <label key={k} className="ws-f">{label}
        <input value={keys[k] ?? ""} onChange={(e) => setKeys({ ...keys, [k]: e.target.value })} type={k === "wa_template" || k === "brevo_template" || k === "brevo_sender" || k === "waha_url" || k === "waha_session" ? "text" : "password"} autoComplete="off"
          placeholder={t.secret_set.includes(k) ? "Set; type to change" : ""} /></label>)}
      <fieldset className="trk-events"><legend>Send when</legend>
        {(Object.keys(EVENT_NAMES) as TrackNotify["events"]).map((e) => <label key={e}><input type="checkbox" checked={events.includes(e)}
          onChange={() => setEvents(events.includes(e) ? events.filter((x) => x !== e) : [...events, e])} /> {EVENT_NAMES[e]}</label>)}
      </fieldset>
      {t.kind === "adsb" && events.includes("new") && <p className="ws-muted">Every aircraft that flies into the area is a new device: expect many messages.</p>}
      <p className="ws-muted">Alerts that come together are sent as one message, at most one a minute.</p>
      {t.notify_error && <p className="trk-err">Last message: {t.notify_error}</p>}
      <div className="mil-acts">
        <Button variant="primary" onClick={() => void save()}>Save</Button>
        {n && <Button onClick={() => void act(() => api.notifyTest(t.id), "Test message sent: check WhatsApp.")}><Send size={14} aria-hidden="true" />Send a test</Button>}
      </div>
    </details>
  );
}

function TrackerDetail({ t, layers, canEdit, view, act, onLayer }: {
  t: Tracker; layers: Layer[]; canEdit: boolean; view: () => [number, number, number, number] | null;
  act: (f: () => Promise<unknown>, done?: string) => Promise<void>; onLayer: (l: Layer) => void;
}) {
  const toast = useToast();
  const [key, setKey] = useState("");
  const [hours, setHours] = useState(24);
  const copy = (s: string) => void navigator.clipboard?.writeText(s).then(() => toast("Copied.", "success"), () => toast(s, "info"));
  const polys = layers.filter((l) => l.geometry_type === "Polygon");
  const url = t.ingest_url ?? "";
  return (
    <div className="trk-detail">
      {t.kind === "device" && <>
        <label className="ws-f">Address to send to (keep it secret)
          <div className="mil-in"><input readOnly value={url} onFocus={(e) => e.currentTarget.select()} /><IconButton label="Copy the address" onClick={() => copy(url)}><Copy size={14} /></IconButton></div></label>
        <details className="trk-setup"><summary>How to set up a device</summary>
          <dl>
            <dt>Traccar Client (Android, iPhone)</dt><dd>Server URL: the address above. Device identifier: a name for this device (e.g. <code>patrol-2</code>).</dd>
            <dt>OsmAnd (online tracking)</dt><dd><code>{`${url}?id=NAME&lat={0}&lon={1}&timestamp={2}&altitude={4}&speed_ms={5}&bearing={6}`}</code></dd>
            <dt>OwnTracks</dt><dd>Mode HTTP, URL: the address above; set a username and device ID.</dd>
            <dt>A Traccar server</dt><dd><code>forward.enable=true</code>, <code>forward.json=true</code>, <code>forward.url=</code> the address above.</dd>
            <dt>Any system</dt><dd>POST JSON: <code>{`{"device_id": "truck-1", "lat": 9.05, "lon": 7.45, "time": "2026-11-15T10:00:00Z", "speed": 40, "course": 90}`}</code> (speed in km/h; a list is fine), or GeoJSON points.</dd>
          </dl>
        </details>
      </>}
      {t.kind === "geohub" && <p className="ws-muted">Form: {t.config.form_label ?? t.config.form_id}. New submissions come in while the picture is live, read as you.</p>}
      {(t.kind === "adsb" || t.kind === "ais") && <p className="ws-muted">Area: {t.config.bbox?.map((v) => v.toFixed(2)).join(", ")}{t.kind === "adsb" ? ` · ${t.config.source ?? "adsb.lol"}` : ""}
        {t.asked_at && ` · last asked ${ago(t.asked_at)}: ${t.last_seen ?? 0} in the area`}</p>}
      {(t.kind === "adsb" || t.kind === "ais") && t.config.bbox && small(t.config.bbox) && <p className="trk-err">
        The area is only about {km(t.config.bbox)} km across, so little may be in it. Zoom the map out and click “Area: the map view”.</p>}
      {canEdit && <div className="trk-grid">
        {(t.kind === "adsb" || t.kind === "ais") && <Button onClick={() => void act(() => api.updateTracker(t.id, { config: { bbox: view() } }), "The area is now the map view.")}>Area: the map view</Button>}
        {t.kind !== "device" && <Button onClick={() => void act(() => api.refreshTracker(t.id))}><RefreshCw size={14} aria-hidden="true" />Ask now</Button>}
        {t.kind === "ais" && <div className="mil-in"><input value={key} onChange={(e) => setKey(e.target.value)} placeholder={t.secret_set.includes("api_key") ? "API key set; a new one…" : "aisstream.io API key"} autoComplete="off" />
          <Button onClick={() => void act(() => api.updateTracker(t.id, { secret: { api_key: key } }), "Key saved.")} disabled={!key}>Save</Button></div>}
        <label className="ws-f">Geofences<select value={t.config.geofence_layer_id ?? ""} onChange={(e) => void act(() => api.updateTracker(t.id, { config: { geofence_layer_id: e.target.value || null } }))}>
          <option value="">None</option>{polys.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
        <label className="ws-f">Trail on the map<select value={t.config.trail_minutes ?? 60} onChange={(e) => void act(() => api.updateTracker(t.id, { config: { trail_minutes: Number(e.target.value) } }))}>
          {[0, 15, 30, 60, 180, 720, 1440].map((m) => <option key={m} value={m}>{m ? (m < 60 ? `${m} minutes` : `${m / 60} hours`) : "None"}</option>)}</select></label>
        <div className="mil-in"><label className="ws-f">Tracks of the last<select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          {[1, 6, 24, 72, 168].map((h) => <option key={h} value={h}>{h < 24 ? `${h} h` : `${h / 24} days`}</option>)}</select></label>
          <Button onClick={() => void act(async () => { const r = await api.trackHistory(t.id, { since: new Date(Date.now() - hours * 3600e3).toISOString() }); onLayer(r.layer); },
            "Tracks added as a layer.")}><History size={14} aria-hidden="true" />As a layer</Button></div>
        <WhatsApp t={t} act={act} />
        <div className="mil-acts">
          <Button onClick={() => void act(() => api.updateTracker(t.id, { active: !t.active }))}>{t.active ? "Pause" : "Resume"}</Button>
          {t.kind === "device" && <Button onClick={() => { if (window.confirm("Make a new address? Devices using the old one stop reaching PolyGIS.")) void act(() => api.trackerToken(t.id), "New address made."); }}>New address</Button>}
          <Button onClick={() => { if (window.confirm(`Delete ${t.name} and its position history? Its layer stays.`)) void act(() => api.deleteTracker(t.id), "Tracker deleted."); }}>
            <Trash2 size={14} aria-hidden="true" />Delete</Button>
        </div>
      </div>}
    </div>
  );
}

/** The live picture: polls the positions, asks the feeds in turn, and draws trails and symbols over the map. */
export function useLiveTracking(map: MapLibre | null, projectId: string, on: boolean, onAlerts: (a: TrackAlert[]) => void) {
  const asked = useRef<Record<string, number>>({});
  const known = useRef<{ at: number; list: Tracker[] }>({ at: 0, list: [] });   // the trackers, looked up every 30 s
  const popup = useRef<maplibregl.Popup | null>(null);
  useEffect(() => {
    if (!map || !on) return;
    let stop = false;
    const ensure = () => {
      if (!map.isStyleLoaded()) return false;
      if (!map.getSource("trk-trails")) {
        map.addSource("trk-trails", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        map.addSource("trk-pos", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        map.addLayer({ id: "trk-trails", type: "line", source: "trk-trails", layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-width": 2.2, "line-opacity": 0.75, "line-color": ["match", ["get", "kind"], "adsb", "#7b3294", "ais", "#0571b0", "geohub", "#e66101", "#1a9850"] } });
        map.addLayer({ id: "trk-pos", type: "symbol", source: "trk-pos",
          layout: { "icon-image": ["concat", `${MIL_PREFIX}28|`, ["get", "sidc"], "|", ["coalesce", ["get", "designation"], ""], "|"], "icon-allow-overlap": true, "icon-ignore-placement": true },
          paint: { "icon-opacity": ["interpolate", ["linear"], ["to-number", ["coalesce", ["get", "age_s"], 0], 0], 0, 1, 600, 1, 3600, 0.45] } });
      }
      return true;
    };
    const click = (e: maplibregl.MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f) return;
      const p = f.properties as Record<string, unknown>;
      const rows = ([["Tracker", p.tracker], ["Grid", p.mgrs], ["Speed", p.speed_kmh != null ? `${Math.round(Number(p.speed_kmh))} km/h` : null],
        ["Course", p.course != null ? `${Math.round(Number(p.course))}°` : null], ["Altitude", p.altitude != null ? `${Math.round(Number(p.altitude))} m` : null],
        ["Seen", ago(String(p.time))], ["Callsign", p.callsign], ["Registration", p.registration], ["Type", p.type], ["Destination", p.destination], ["Report", p.report]] as [string, unknown][])
        .filter(([, v]) => v != null && v !== "" && v !== "null");
      const esc = (s: unknown) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));
      popup.current?.remove();
      popup.current = new maplibregl.Popup({ closeButton: true, maxWidth: "280px", className: "trk-pop" }).setLngLat(e.lngLat)
        .setHTML(`<strong>${esc(p.name ?? p.device)}</strong><table>${rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("")}</table>`).addTo(map);
    };
    let running = false;
    const tick = async () => {
      if (stop || running) return;              // one update at a time: a slow feed never stacks requests up
      running = true;
      try {
        const now = Date.now() / 1000;
        if (now - known.current.at > 30) known.current = { at: now, list: await api.trackers(projectId) };
        // the feeds PolyGIS asks, each at its own pace; not waited for (the picture shows what has come in so far)
        for (const t of known.current.list.filter((x) => x.active && EVERY[x.kind] && now - (asked.current[x.id] ?? 0) >= EVERY[x.kind])) {
          asked.current[t.id] = now;
          void api.refreshTracker(t.id).catch(() => undefined);
        }
        const d: TrackingLive = await api.trackingLive(projectId);
        if (stop || !ensure()) return;
        (map.getSource("trk-trails") as maplibregl.GeoJSONSource).setData(d.trails);
        (map.getSource("trk-pos") as maplibregl.GeoJSONSource).setData(d.positions);
        onAlerts(d.alerts);
      } catch { /* the next tick tries again */ } finally { running = false; }
    };
    void tick();
    const timer = setInterval(() => void tick(), 5000);
    map.on("click", "trk-pos", click);
    const enter = () => { map.getCanvas().style.cursor = "pointer"; };
    const leave = () => { map.getCanvas().style.cursor = ""; };
    map.on("mouseenter", "trk-pos", enter); map.on("mouseleave", "trk-pos", leave);
    return () => {
      stop = true; clearInterval(timer);
      map.off("click", "trk-pos", click); map.off("mouseenter", "trk-pos", enter); map.off("mouseleave", "trk-pos", leave);
      popup.current?.remove();
      for (const id of ["trk-pos", "trk-trails"]) { if (map.getLayer(id)) map.removeLayer(id); if (map.getSource(id)) map.removeSource(id); }
    };
  }, [map, projectId, on]);   // eslint-disable-line react-hooks/exhaustive-deps
}
