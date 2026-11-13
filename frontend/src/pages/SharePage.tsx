import maplibregl, { type Map as MapLibre } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Download, Info, Layers, Maximize, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Legend } from "../components/Contents";
import { ApiError, publicApi, signInUrl, type PublicShare } from "../lib/api";
import { BASEMAPS, GLYPHS, basemapStyle, ids as layerIds, labelSrc, mapLayers, src } from "../lib/mapstyle";
import type { Layer } from "../lib/types";
import { splash } from "../splash";

const show = (v: unknown) => (v == null || v === "" ? "–" : typeof v === "number" ? v.toLocaleString("en-GB", { maximumFractionDigits: Math.abs(v) < 1 ? 6 : 3 }) : String(v));

/** A map shared by a link: read-only, no sign-in (unless the link is for an organization's people). Its layers, their
 *  legend, a feature's details on click, the basemap, and downloads when the link allows them. */
export default function SharePage() {
  const { token = "" } = useParams();
  const [share, setShare] = useState<PublicShare | null>(null);
  const [fail, setFail] = useState<ApiError | null>(null);
  const [panel, setPanel] = useState(() => window.innerWidth > 760);
  const [basemap, setBasemap] = useState("streets");
  const [hidden, setHidden] = useState<string[]>([]);
  const [picked, setPicked] = useState<{ layer: Layer; props: Record<string, unknown> } | null>(null);
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibre | null>(null);
  const styleReady = useRef(false);        // not isStyleLoaded(): that also waits for every source's tiles
  const state = useRef({ share, hidden });
  state.current = { share, hidden };

  useEffect(() => {
    publicApi.open(token).then((s) => { setShare(s); setBasemap(s.view.basemap ?? "streets"); document.title = `${s.title} · PolyGIS`; },
      (e) => setFail(e instanceof ApiError ? e : new ApiError(0, "This map could not be opened.")))
      .finally(() => splash.done());
  }, [token]);

  const draw = () => {
    const m = map.current, s = state.current.share;
    if (!m || !s || !styleReady.current) return;
    if (!m.getStyle().glyphs) m.setGlyphs(GLYPHS);
    for (const l of [...s.layers].sort((a, b) => a.position - b.position)) {
      if (l.geometry_type === "Raster" && l.raster?.missing) continue;
      const shown = { ...l, visible: !state.current.hidden.includes(l.id) };
      for (const spec of mapLayers(shown, [])) if (m.getLayer(spec.id)) m.removeLayer(spec.id);
      if (!m.getSource(src(l))) {
        if (l.geometry_type === "Raster") m.addSource(src(l), { type: "raster", tiles: [publicApi.rasterTiles(token, l)], tileSize: 256, bounds: l.bbox ?? undefined });
        else m.addSource(src(l), { type: "vector", tiles: [publicApi.tiles(token, l)], maxzoom: 16 });
      }
      if (l.geometry_type !== "Raster" && l.labels && !m.getSource(labelSrc(l))) m.addSource(labelSrc(l), { type: "vector", tiles: [publicApi.tiles(token, l)], maxzoom: 16 });
      for (const spec of mapLayers(shown, []).filter((x) => !x.id.includes("-sel-"))) m.addLayer(spec);
    }
  };
  useEffect(() => {
    if (!share || map.current || !el.current) return;
    const v = share.view;
    const m = new maplibregl.Map({ container: el.current, style: basemapStyle(v.basemap), center: v.center ?? [8.68, 9.08], zoom: v.zoom ?? 5.5,
      bearing: v.bearing ?? 0, attributionControl: { compact: true } });
    m.addControl(new maplibregl.NavigationControl(), "top-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    m.addControl(new maplibregl.FullscreenControl(), "top-right");
    m.on("style.load", () => { styleReady.current = true; draw(); });
    m.on("click", (e) => void identify(e.point, e.lngLat));
    map.current = m;
    if (!v.center) fit();
    return () => { m.remove(); map.current = null; };
  }, [share]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(draw, [hidden]);   // eslint-disable-line react-hooks/exhaustive-deps

  function fit() {
    const bs = (state.current.share?.layers ?? []).map((l) => l.bbox).filter(Boolean) as [number, number, number, number][];
    if (!bs.length || !map.current) return;
    map.current.fitBounds([[Math.min(...bs.map((b) => b[0])), Math.min(...bs.map((b) => b[1]))], [Math.max(...bs.map((b) => b[2])), Math.max(...bs.map((b) => b[3]))]], { padding: 50, maxZoom: 17 });
  }
  async function identify(pt: maplibregl.Point, at: maplibregl.LngLat) {
    const m = map.current, s = state.current.share;
    if (!m || !s) return;
    const order = [...s.layers].filter((l) => !state.current.hidden.includes(l.id)).sort((a, b) => b.position - a.position);
    const hitIds = order.flatMap((l) => { const i = layerIds(l); return [i.circle, i.line, i.fill].filter((x) => m.getLayer(x)); });
    const f = m.queryRenderedFeatures([[pt.x - 4, pt.y - 4], [pt.x + 4, pt.y + 4]], { layers: hitIds })[0];
    if (f && f.id != null) {
      const l = order.find((x) => f.source === src(x));
      if (l) { const full = await publicApi.feature(token, l.id, Number(f.id)); setPicked({ layer: l, props: full.properties ?? {} }); return; }
    }
    const r = order.find((l) => l.geometry_type === "Raster" && l.bbox && at.lng >= l.bbox[0] && at.lng <= l.bbox[2] && at.lat >= l.bbox[1] && at.lat <= l.bbox[3]);
    if (r) {
      const v = await publicApi.rasterValue(token, r.id, at.lng, at.lat);
      if (v.values) { setPicked({ layer: r, props: Object.fromEntries(v.bands.map((b, k) => [b, v.values![k]])) }); return; }
    }
    setPicked(null);
  }
  function switchBasemap(b: string) {
    setBasemap(b);
    const m = map.current;
    if (!m) return;
    styleReady.current = false;
    m.setStyle(basemapStyle(b) as maplibregl.StyleSpecification | string);
  }

  if (fail) {
    const signIn = fail.status === 401;
    return (
      <div className="ge-main sp-fail"><div className="ge-card">
        <img src="/logo.png" alt="" width={48} height={48} />
        <h1>{signIn ? "Sign in to see this map" : fail.status === 410 ? "This link has ended" : fail.status === 403 ? "This map is not shared with you" : "This map is not available"}</h1>
        <p>{fail.message}</p>
        {signIn && <a className="ge-btn ge-btn-primary" href={signInUrl()}>Sign in with GeoEstate</a>}
      </div></div>
    );
  }
  if (!share) return <p className="loading">Opening the map…</p>;
  const order = [...share.layers].sort((a, b) => b.position - a.position);
  return (
    <div className="sp">
      <header className="sp-bar">
        <a className="ge-brand" href="/" aria-label="GeoEstate PolyGIS"><img src="/logo.png" alt="" /><span>Poly<strong>GIS</strong></span></a>
        <div className="sp-title"><h1>{share.title}</h1>{share.description && <p>{share.description}</p>}</div>
        <span className="sp-by">Shared by {share.shared_by}</span>
        <button type="button" className="sp-toggle" aria-expanded={panel} onClick={() => setPanel(!panel)}><Layers size={16} aria-hidden="true" />Layers</button>
      </header>
      <div className={`sp-body${panel ? "" : " no-panel"}`}>
        {panel && <aside className="sp-panel ws-scroll" aria-label="Layers and legend">
          <ul className="sp-layers">
            {order.map((l) => (
              <li key={l.id}>
                <label className="sp-layer"><input type="checkbox" checked={!hidden.includes(l.id)} onChange={(e) => setHidden((h) => (e.target.checked ? h.filter((x) => x !== l.id) : [...h, l.id]))} />
                  <strong>{l.name}</strong></label>
                {!hidden.includes(l.id) && <Legend layer={l} />}
                {share.downloads && l.geometry_type !== "Raster" && <div className="sp-dl"><Download size={12} aria-hidden="true" />
                  {(["geojson", "gpkg", "shp", "csv", "kml"] as const).map((f) => <a key={f} href={publicApi.download(token, l.id, f)}>{f === "shp" ? "Shapefile" : f === "gpkg" ? "GeoPackage" : f === "geojson" ? "GeoJSON" : f.toUpperCase()}</a>)}</div>}
              </li>
            ))}
          </ul>
          <div className="sp-basemaps" role="radiogroup" aria-label="Basemap">
            {BASEMAPS.map((b) => <button key={b.id} type="button" role="radio" aria-checked={basemap === b.id} className={basemap === b.id ? "is-on" : ""} onClick={() => switchBasemap(b.id)}>{b.name.split(" (")[0]}</button>)}
          </div>
          <p className="sp-foot">Read-only map from <a href="/">GeoEstate PolyGIS</a>. Click a feature for its details.</p>
        </aside>}
        <main className="sp-map-wrap" aria-label="Map">
          <div ref={el} className="sp-map" />
          <button type="button" className="sp-fit" onClick={fit} title="Show all the layers"><Maximize size={15} aria-hidden="true" /><span>All layers</span></button>
          {picked && <div className="sp-pop" role="dialog" aria-label={picked.layer.name}>
            <div className="sp-pop-head"><Info size={15} aria-hidden="true" /><strong>{picked.layer.name}</strong><button type="button" aria-label="Close" onClick={() => setPicked(null)}><X size={15} /></button></div>
            <dl>{Object.entries(picked.props).filter(([k]) => !k.startsWith("_")).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{show(v)}</dd></div>)}</dl>
          </div>}
        </main>
      </div>
    </div>
  );
}
