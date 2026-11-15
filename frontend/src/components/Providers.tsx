/** Imagery providers: the organization's imagery services beside the free ones. Tile services become basemaps; STAC
 *  catalogues are searched over the map view and a scene's image is added for the area looked at. Admins add providers:
 *  a client's licensed Maxar, Planet, Airbus, UP42 or Satellogic account goes in here, its key kept on the server. */
import type maplibregl from "maplibre-gl";
import type { Map as MapLibre } from "maplibre-gl";
import { ImagePlus, Layers, Plus, Search, Settings2, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api } from "../lib/api";
import type { Layer, OrgImagery, OrgScene } from "../lib/types";
import { Button, ErrorText, IconButton, useToast } from "./ui";

const PRESETS: { label: string; kind: OrgImagery["kind"]; url: string; auth: { mode: string; name?: string }; config: OrgImagery["config"]; hint: string }[] = [
  { label: "STAC catalogue (Maxar, Airbus, UP42, Satellogic…)", kind: "stac", url: "https://", auth: { mode: "header", name: "Authorization" },
    config: { collections: [], asset: "visual" }, hint: "The provider's STAC API address, its collections, and the key as “Bearer …” (or as its docs say). List the hosts its images are on." },
  { label: "Planet basemap (XYZ)", kind: "xyz", url: "https://tiles.planet.com/basemaps/v1/planet-tiles/MOSAIC_NAME/gmap/{z}/{x}/{y}.png", auth: { mode: "query", name: "api_key" },
    config: { attribution: "© Planet Labs PBC", max_zoom: 18 }, hint: "Put the mosaic's name in the address and the Planet API key below." },
  { label: "Tile service (XYZ)", kind: "xyz", url: "https://", auth: { mode: "query", name: "api_key" }, config: { max_zoom: 20 },
    hint: "An address with {z}, {x} and {y} in it, as the provider gives it." },
  { label: "WMS", kind: "wms", url: "https://", auth: { mode: "none" }, config: { layers: "", format: "image/png" }, hint: "The WMS address and the layer names." },
];

export default function ProvidersPane({ projectId, map, isAdmin, canAdd, basemap, onBasemap, onLayer, onChanged, view, onClose }: {
  projectId: string; map: MapLibre | null; isAdmin: boolean; canAdd: boolean; basemap: string;
  onBasemap: (id: string) => void; onLayer: (l: Layer) => void; onChanged: (list: OrgImagery[]) => void;
  view: () => [number, number, number, number] | null; onClose: () => void;
}) {
  const [list, setList] = useState<OrgImagery[] | null>(null);
  const [open, setOpen] = useState<string | null>("builtin-earth-search");
  const [editing, setEditing] = useState<OrgImagery | "new" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const load = () => api.orgImagery().then((l) => { setList(l); onChanged(l); }, setError);
  useEffect(() => { void load(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <aside className="ws-panel mil prov" aria-label="Imagery providers">
      <div className="ws-pane-head"><strong>Imagery providers</strong><span className="ge-spacer" /><IconButton label="Close" onClick={onClose}><X size={16} /></IconButton></div>
      <div className="ws-scroll mil-body">
        <p className="ws-muted">Free imagery is built in. A licensed provider (Maxar, Planet, Airbus, UP42, Satellogic…) is added once by an organization admin; its key stays on the server.</p>
        <ErrorText error={error} />
        <ul className="trk-list">
          {(list ?? []).map((p) => (
            <li key={p.id} className={`trk-item${p.enabled ? "" : " is-paused"}`}>
              <button type="button" className="trk-row" onClick={() => setOpen(open === p.id ? null : p.id)} aria-expanded={open === p.id}>
                <Layers size={15} aria-hidden="true" />
                <span className="trk-name">{p.name}<span className="ws-muted"> · {p.kind === "stac" ? "catalogue" : p.kind.toUpperCase()}</span></span>
                {isAdmin && !p.builtin && <IconButton label={`Settings of ${p.name}`} onClick={() => setEditing(p)}><Settings2 size={14} /></IconButton>}
              </button>
              {open === p.id && (p.kind === "stac"
                ? <SceneSearch p={p} projectId={projectId} map={map} canAdd={canAdd} view={view} onLayer={onLayer} />
                : <div className="trk-detail">
                    <p className="ws-muted">{p.config.attribution || "Tiles"} · to zoom {p.config.max_zoom ?? 20}</p>
                    <Button variant={basemap === `provider:${p.id}` ? "primary" : undefined} onClick={() => onBasemap(`provider:${p.id}`)}>
                      {basemap === `provider:${p.id}` ? "The basemap now" : "Use as the basemap"}</Button>
                  </div>)}
            </li>))}
        </ul>
        {isAdmin && (editing ? <ProviderForm p={editing === "new" ? null : editing} onDone={(changed) => { setEditing(null); if (changed) void load(); }} />
          : <Button onClick={() => setEditing("new")}><Plus size={15} aria-hidden="true" />Add a provider</Button>)}
        {!isAdmin && <p className="ws-muted">Only an organization admin can add providers.</p>}
      </div>
    </aside>
  );
}

function SceneSearch({ p, projectId, map, canAdd, view, onLayer }: {
  p: OrgImagery; projectId: string; map: MapLibre | null; canAdd: boolean; view: () => [number, number, number, number] | null; onLayer: (l: Layer) => void;
}) {
  const toast = useToast();
  const today = new Date().toISOString().slice(0, 10);
  const [start, setStart] = useState(new Date(Date.now() - 60 * 86400e3).toISOString().slice(0, 10));
  const [end, setEnd] = useState(today);
  const [cloud, setCloud] = useState(20);
  const [scenes, setScenes] = useState<OrgScene[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  // footprints of the scenes found, and the one pointed at
  const show = (fc: GeoJSON.FeatureCollection, hover: string | null) => {
    if (!map || !map.isStyleLoaded()) return;
    const src = map.getSource("prov-scenes") as maplibregl.GeoJSONSource | undefined;
    if (src) src.setData(fc);
    else {
      map.addSource("prov-scenes", { type: "geojson", data: fc });
      map.addLayer({ id: "prov-scenes", type: "line", source: "prov-scenes", paint: { "line-color": "#e66101", "line-width": 1.5, "line-dasharray": [2, 1] } });
      map.addLayer({ id: "prov-scene-on", type: "fill", source: "prov-scenes", filter: ["==", ["get", "id"], ""], paint: { "fill-color": "#e66101", "fill-opacity": 0.18 } });
    }
    map.setFilter("prov-scene-on", ["==", ["get", "id"], hover ?? ""]);
  };
  const fc = (): GeoJSON.FeatureCollection => ({ type: "FeatureCollection", features: (scenes ?? []).filter((s) => s.geometry).map((s) => ({ type: "Feature", geometry: s.geometry!, properties: { id: s.id } })) });
  useEffect(() => { show(fc(), null); }, [scenes]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { if (map) { for (const id of ["prov-scene-on", "prov-scenes"]) if (map.getLayer(id)) map.removeLayer(id); if (map.getSource("prov-scenes")) map.removeSource("prov-scenes"); } }, [map]);
  async function search() {
    const b = view();
    if (!b) return;
    setBusy("search"); setError(null);
    try { setScenes((await api.searchOrgImagery(p.id, { bbox: b, start, end, max_cloud: cloud })).scenes); } catch (e) { setError(e); } finally { setBusy(null); }
  }
  async function add(s: OrgScene) {
    const b = view();
    if (!b) return;
    setBusy(s.id);
    try {
      const l = await api.addOrgScene(projectId, p.id, { scene_id: s.id, collection: s.collection, bbox: b });
      onLayer(l); toast(`${l.name} added for the area on the map.`, "success");
    } catch (e) { toast(e instanceof ApiError ? e.message : String(e), "error"); } finally { setBusy(null); }
  }
  return (
    <div className="trk-detail">
      <div className="mil-opts">
        <label className="ws-f">From<input type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="ws-f">To<input type="date" value={end} min={start} max={today} onChange={(e) => setEnd(e.target.value)} /></label>
      </div>
      <label className="ws-f">Cloud at most {cloud}%<input type="range" min={0} max={100} step={5} value={cloud} onChange={(e) => setCloud(Number(e.target.value))} /></label>
      <Button variant="primary" busy={busy === "search"} onClick={() => void search()}><Search size={15} aria-hidden="true" />Find scenes over the map view</Button>
      <ErrorText error={error} />
      {scenes && !scenes.length && <p className="ws-muted">No scenes there between those dates under that cloud cover.</p>}
      {scenes && scenes.length > 0 && <ul className="prov-scenes">
        {scenes.map((s) => (
          <li key={s.id} onMouseEnter={() => show(fc(), s.id)} onMouseLeave={() => show(fc(), null)}>
            {s.thumbnail ? <img src={p.builtin ? s.thumbnail : api.orgThumb(p.id, s.thumbnail)} alt="" loading="lazy" /> : <span className="prov-nothumb" />}
            <div>
              <strong>{s.datetime?.slice(0, 10) ?? "–"}</strong>
              <span className="ws-muted">{[s.platform, s.cloud != null ? `${Math.round(s.cloud)}% cloud` : null, s.gsd ? `${s.gsd} m` : null].filter(Boolean).join(" · ")}</span>
              <span className="ws-muted prov-id" title={s.id}>{s.id}</span>
            </div>
            <Button onClick={() => void add(s)} busy={busy === s.id} disabled={!canAdd || !s.has_image || !!busy} title={s.has_image ? "Add the image for the area on the map" : "No image to add"}>
              <ImagePlus size={14} aria-hidden="true" />Add</Button>
          </li>))}
      </ul>}
    </div>
  );
}

function ProviderForm({ p, onDone }: { p: OrgImagery | null; onDone: (changed: boolean) => void }) {
  const [preset, setPreset] = useState(0);
  const pre = PRESETS[preset];
  const [name, setName] = useState(p?.name ?? "");
  const [kind, setKind] = useState<OrgImagery["kind"]>(p?.kind ?? pre.kind);
  const [url, setUrl] = useState(p?.url ?? pre.url);
  const [mode, setMode] = useState(p?.auth_mode ?? pre.auth.mode);
  const [keyName, setKeyName] = useState(pre.auth.name ?? "");
  const [key, setKey] = useState("");
  const [user, setUser] = useState("");
  const [attribution, setAttribution] = useState(p?.config.attribution ?? pre.config.attribution ?? "");
  const [maxZoom, setMaxZoom] = useState(p?.config.max_zoom ?? pre.config.max_zoom ?? 20);
  const [layers, setLayersText] = useState(p?.config.layers ?? "");
  const [collections, setCollections] = useState((p?.config.collections ?? []).join(", "));
  const [asset, setAsset] = useState(p?.config.asset ?? "visual");
  const [hosts, setHosts] = useState((p?.config.hosts ?? []).join(", "));
  const [enabled, setEnabled] = useState(p?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const pick = (i: number) => { const x = PRESETS[i]; setPreset(i); setKind(x.kind); setUrl(x.url); setMode(x.auth.mode as OrgImagery["auth_mode"]); setKeyName(x.auth.name ?? "");
    setAttribution(x.config.attribution ?? ""); setMaxZoom(x.config.max_zoom ?? 20); setAsset(x.config.asset ?? "visual"); };
  const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  async function save() {
    setBusy(true); setError(null);
    const config = { attribution, max_zoom: maxZoom, ...(kind === "wms" ? { layers, format: "image/png" } : {}),
      ...(kind === "stac" ? { collections: list(collections), asset, cloud_property: "eo:cloud_cover" } : {}), hosts: list(hosts) };
    const auth = mode === "none" ? { mode } : mode === "basic" ? { mode, user, password: key || undefined } : { mode, name: keyName, value: key || undefined };
    try {
      if (p) await api.updateOrgImagery(p.id, { name, url, config, auth, enabled });
      else await api.newOrgImagery({ name, kind, url, config, auth, enabled });
      onDone(true);
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  return (
    <form className="trk-add" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <h3>{p ? `Settings of ${p.name}` : "Add an imagery provider"}</h3>
      {!p && <label className="ws-f">Kind<select value={preset} onChange={(e) => pick(Number(e.target.value))}>{PRESETS.map((x, i) => <option key={x.label} value={i}>{x.label}</option>)}</select></label>}
      {!p && <p className="ws-muted">{pre.hint}</p>}
      <label className="ws-f">Name<input value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} placeholder="e.g. Maxar archive" /></label>
      <label className="ws-f">Address<input value={url} onChange={(e) => setUrl(e.target.value)} required spellCheck={false} /></label>
      {kind === "wms" && <label className="ws-f">WMS layers<input value={layers} onChange={(e) => setLayersText(e.target.value)} required /></label>}
      {kind === "stac" && <>
        <label className="ws-f">Collections (separated by commas)<input value={collections} onChange={(e) => setCollections(e.target.value)} placeholder="as the catalogue names them" /></label>
        <label className="ws-f">Image asset to add<input value={asset} onChange={(e) => setAsset(e.target.value)} placeholder="visual" /></label>
      </>}
      <label className="ws-f">Hosts its images and thumbnails are on (separated by commas)<input value={hosts} onChange={(e) => setHosts(e.target.value)} placeholder="e.g. data.provider.com" /></label>
      <label className="ws-f">Key goes<select value={mode} onChange={(e) => setMode(e.target.value as OrgImagery["auth_mode"])}>
        <option value="none">Nowhere: no key</option><option value="query">In the address (a parameter)</option><option value="header">In a header</option><option value="basic">As a username and password</option></select></label>
      {(mode === "query" || mode === "header") && <label className="ws-f">{mode === "query" ? "Parameter" : "Header"} name<input value={keyName} onChange={(e) => setKeyName(e.target.value)} required /></label>}
      {mode === "basic" && <label className="ws-f">Username<input value={user} onChange={(e) => setUser(e.target.value)} required autoComplete="off" /></label>}
      {mode !== "none" && <label className="ws-f">{mode === "basic" ? "Password" : "Key"}{p?.key_set ? " (saved; type to replace)" : ""}
        <input type="password" value={key} onChange={(e) => setKey(e.target.value)} required={!p?.key_set && mode !== "basic"} autoComplete="new-password" /></label>}
      {kind !== "stac" && <div className="mil-opts">
        <label className="ws-f">Attribution<input value={attribution} onChange={(e) => setAttribution(e.target.value)} /></label>
        <label className="ws-f">Highest zoom<input type="number" min={1} max={24} value={maxZoom} onChange={(e) => setMaxZoom(Number(e.target.value) || 20)} /></label>
      </div>}
      {p && <label className="ws-check"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> In use</label>}
      <ErrorText error={error} />
      <div className="mil-acts">
        <Button type="submit" variant="primary" busy={busy}>{p ? "Save" : "Add"}</Button>
        <Button onClick={() => onDone(false)}>Cancel</Button>
        {p && <Button onClick={() => { if (window.confirm(`Remove ${p.name}? Layers already added stay.`)) void api.deleteOrgImagery(p.id).then(() => onDone(true)); }}>
          <Trash2 size={14} aria-hidden="true" />Remove</Button>}
      </div>
    </form>
  );
}
