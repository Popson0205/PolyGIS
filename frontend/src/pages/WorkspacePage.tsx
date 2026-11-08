import maplibregl, { type Map as MapLibre, type MapGeoJSONFeature } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import AttributeTable from "../components/AttributeTable";
import Contents from "../components/Contents";
import { AddData, CrsPicker, Dialog, Export, NewLayer, SelectByAttributes, SelectByLocation } from "../components/Dialogs";
import LayerPanel from "../components/LayerPanel";
import { Button, ErrorText, useToast } from "../components/ui";
import { ApiError, BASE, api, tileUrl } from "../lib/api";
import { formatCoords, loadCrs, metres, squareMetres } from "../lib/coords";
import { Editor } from "../lib/editor";
import { BASEMAPS, GLYPHS, allIds, basemapStyle, ids as layerIds, mapLayers, src } from "../lib/mapstyle";
import type { CrsDef, Layer, Me, Project, Selection } from "../lib/types";

type Tab = "map" | "insert" | "select" | "edit" | "analysis";
type Tool = "explore" | "select" | "select-area" | "measure-line" | "measure-area" | "create" | "reshape" | "pick-reshape";
type DialogName = "add" | "new" | "attr" | "loc" | "export" | "crs" | null;
const DRAW = { Point: "point", LineString: "linestring", Polygon: "polygon" } as const;

/** A project open in the workspace, laid out like a desktop GIS: ribbon, contents, map, properties, table, status. */
export default function WorkspacePage({ me }: { me: Me }) {
  const { id = "" } = useParams();
  const toast = useToast();
  const canEdit = me.role !== "viewer" && me.can_write;
  const [project, setProject] = useState<Project | null>(null);
  const [layers, setLayers] = useState<Layer[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selection, setSelection] = useState<Record<string, number[]>>({});
  const [tab, setTab] = useState<Tab>("map");
  const [tool, setTool] = useState<Tool>("explore");
  const [dialog, setDialog] = useState<DialogName>(null);
  const [tableId, setTableId] = useState<string | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [snap, setSnap] = useState(true);
  const [reshaping, setReshaping] = useState<number | null>(null);
  const [identify, setIdentify] = useState<{ layer: Layer; id: number; props: Record<string, unknown> } | null>(null);
  const [newFeature, setNewFeature] = useState<{ layerId: string; id: number } | null>(null);
  const [crs, setCrs] = useState<CrsDef | undefined>();
  const [pointer, setPointer] = useState<string>("");
  const [zoom, setZoom] = useState(0);
  const [measure, setMeasure] = useState<string>("");
  const [basemap, setBasemap] = useState("streets");

  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibre | null>(null);
  const editor = useRef<Editor | null>(null);
  const snapFeatures = useRef<GeoJSON.Feature[]>([]);
  const versions = useRef<Map<string, number>>(new Map());
  const state = useRef({ layers, selection, reshaping, editId, snap, tool, activeId });
  state.current = { layers, selection, reshaping, editId, snap, tool, activeId };

  const active = layers.find((l) => l.id === activeId) ?? null;
  const editLayer = layers.find((l) => l.id === editId) ?? null;
  const tableLayer = layers.find((l) => l.id === tableId) ?? null;
  const sel = (lid: string | null | undefined) => (lid ? selection[lid] ?? [] : []);

  // ---------- loading ----------
  const load = useCallback(async () => {
    try {
      const p = await api.project(id);
      setProject(p); setLayers(p.layers ?? []); setBasemap(p.view.basemap ?? "streets");
      setActiveId((a) => a ?? (p.layers?.length ? [...p.layers].sort((x, y) => y.position - x.position)[0].id : null));
      setCrs(await loadCrs(p.display_epsg).catch(() => undefined));
    } catch (e) { setError(e); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);
  const replaceLayer = (l: Layer) => setLayers((ls) => ls.map((x) => (x.id === l.id ? l : x)));
  const refreshLayer = async (lid: string) => {
    const p = await api.project(id);
    setLayers(p.layers ?? []);
    return p.layers?.find((l) => l.id === lid);
  };

  // ---------- the map ----------
  useEffect(() => {
    if (!project || map.current || !mapEl.current) return;
    const v = project.view;
    const m = new maplibregl.Map({ container: mapEl.current, style: basemapStyle(v.basemap), center: v.center ?? [8.68, 9.08], zoom: v.zoom ?? 5.5,
      bearing: v.bearing ?? 0, attributionControl: { compact: true }, boxZoom: true, doubleClickZoom: true });
    m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric", maxWidth: 120 }), "bottom-right");
    m.on("style.load", () => {
      if (!m.getStyle().glyphs) m.setGlyphs(GLYPHS);          // labels need fonts, whatever the basemap
      sync();
    });
    m.on("mousemove", (e) => setPointer(e.lngLat ? `${e.lngLat.lng},${e.lngLat.lat}` : ""));
    m.on("zoom", () => setZoom(m.getZoom()));
    let saveT: ReturnType<typeof setTimeout>;
    m.on("moveend", () => {
      setZoom(m.getZoom());
      if (state.current.editId) void loadSnap();
      if (!canEdit) return;
      clearTimeout(saveT);
      saveT = setTimeout(() => { const c = m.getCenter(); void api.updateProject(id, { view: { center: [c.lng, c.lat], zoom: m.getZoom(), bearing: m.getBearing(), basemap: basemapRef.current } }).catch(() => undefined); }, 1500);
    });
    m.on("click", (e) => void onMapClick(e.point));
    map.current = m;
    editor.current = new Editor(m, () => ({ on: state.current.snap, features: snapFeatures.current }));
    setZoom(m.getZoom());
    return () => { editor.current?.stop(); m.remove(); map.current = null; };
  }, [project?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const basemapRef = useRef(basemap);
  basemapRef.current = basemap;

  /** Put every layer on the map, bottom to top, below the drawing layers; sources are only rebuilt when a layer's
   *  data changed (its version). */
  const sync = useCallback(() => {
    const m = map.current;
    if (!m || !m.isStyleLoaded()) return;
    const { layers: ls, selection: s, reshaping: r, editId: e } = state.current;
    const before = m.getStyle().layers.find((x) => x.id.startsWith("gis-draw"))?.id;
    const wanted = new Set(ls.map((l) => l.id));
    for (const [lid] of versions.current) {
      if (!wanted.has(lid)) {
        for (const x of m.getStyle().layers.filter((y) => y.id.startsWith(`lyr-${lid}`))) m.removeLayer(x.id);
        if (m.getSource(`src-${lid}`)) m.removeSource(`src-${lid}`);
        versions.current.delete(lid);
      }
    }
    for (const l of [...ls].sort((a, b) => a.position - b.position)) {
      for (const x of allIds(l)) if (m.getLayer(x)) m.removeLayer(x);
      if (m.getSource(src(l)) && versions.current.get(l.id) !== l.version) m.removeSource(src(l));
      if (!m.getSource(src(l))) {
        m.addSource(src(l), { type: "vector", tiles: [tileUrl(l)], minzoom: 0, maxzoom: 16, promoteId: undefined });
        versions.current.set(l.id, l.version);
      }
      for (const spec of mapLayers(l, s[l.id] ?? [], l.id === e && r != null ? [r] : [])) m.addLayer(spec, before);
    }
  }, []);
  useEffect(() => { sync(); }, [layers, selection, reshaping, editId, sync]);

  // ---------- clicking on the map ----------
  const hitLayers = (only?: Layer | null) => {
    const ls = only ? [only] : [...state.current.layers].filter((l) => l.visible).sort((a, b) => b.position - a.position);
    return ls.flatMap((l) => { const i = layerIds(l); return [i.circle, i.line, i.fill].filter((x) => map.current?.getLayer(x)); });
  };
  const layerOf = (f: MapGeoJSONFeature) => state.current.layers.find((l) => f.source === src(l));

  async function onMapClick(pt: maplibregl.Point) {
    const m = map.current;
    if (!m) return;
    const t = state.current.tool;
    const box: [maplibregl.PointLike, maplibregl.PointLike] = [[pt.x - 4, pt.y - 4], [pt.x + 4, pt.y + 4]];
    if (t === "explore" || t === "select") {
      const target = t === "select" ? state.current.layers.find((l) => l.id === state.current.activeId) : null;
      if (t === "select" && !target) return;
      const hits = m.queryRenderedFeatures(box, { layers: hitLayers(target) });
      const f = hits[0];
      if (!f || f.id == null) { if (t === "select") setSelection((s) => ({ ...s, [target!.id]: [] })); else setIdentify(null); return; }
      const l = layerOf(f);
      if (!l) return;
      const fid = Number(f.id);
      if (t === "explore") {
        setIdentify({ layer: l, id: fid, props: f.properties ?? {} });
        setSelection((s) => ({ ...s, [l.id]: [fid] }));
      } else {
        const add = (window.event as MouseEvent | undefined)?.shiftKey;
        setSelection((s) => {
          const now = s[l.id] ?? [];
          return { ...s, [l.id]: add ? (now.includes(fid) ? now.filter((x) => x !== fid) : [...now, fid]) : [fid] };
        });
      }
    }
    if (t === "pick-reshape") {
      const el = state.current.layers.find((l) => l.id === state.current.editId);
      if (!el) return;
      const f = m.queryRenderedFeatures(box, { layers: hitLayers(el) })[0];
      if (!f || f.id == null) { toast("Click a feature of the layer you are editing.", "info"); return; }
      const fid = Number(f.id);
      const full = await api.feature(el.id, fid);
      if (!editor.current?.reshape(full.geometry)) { toast("Multi-part shapes can't be reshaped yet: change their attributes in the table.", "info"); return; }
      setReshaping(fid); setTool("reshape");
      setSelection((s) => ({ ...s, [el.id]: [fid] }));
    }
  }

  // ---------- selection helpers ----------
  function applySelection(lid: string, s: Selection, add: boolean) {
    setSelection((cur) => ({ ...cur, [lid]: add ? Array.from(new Set([...(cur[lid] ?? []), ...s.ids])) : s.ids }));
    toast(`${s.count.toLocaleString("en-GB")} selected.`, "success");
    if (s.bbox && s.count) zoomTo(s.bbox);
    setDialog(null);
  }
  function zoomTo(b: [number, number, number, number]) {
    const m = map.current;
    if (!m) return;
    if (b[0] === b[2] && b[1] === b[3]) m.flyTo({ center: [b[0], b[1]], zoom: Math.max(m.getZoom(), 16) });
    else m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, maxZoom: 18, duration: 600 });
  }
  async function zoomToFeature(l: Layer, fid: number) {
    const f = await api.feature(l.id, fid);
    const pts = JSON.stringify(f.geometry).match(/-?\d+\.?\d*(e-?\d+)?,-?\d+\.?\d*(e-?\d+)?/g) ?? [];
    const xy = pts.map((p) => p.split(",").map(Number));
    if (!xy.length) return;
    zoomTo([Math.min(...xy.map((p) => p[0])), Math.min(...xy.map((p) => p[1])), Math.max(...xy.map((p) => p[0])), Math.max(...xy.map((p) => p[1]))]);
  }

  async function switchSelection() {
    if (!active) return;
    const all = await api.select(active.id, { filters: [] });
    const now = new Set(sel(active.id));
    const ids = all.ids.filter((x) => !now.has(x));
    setSelection((cur) => ({ ...cur, [active.id]: ids }));
    toast(`${ids.length.toLocaleString("en-GB")} selected.`, "success");
  }
  async function zoomToSelection(l: Layer) {
    const ids = sel(l.id);
    if (ids.length === 1) { await zoomToFeature(l, ids[0]); return; }
    const fc = await api.features(l.id, ids.slice(0, 2000));
    const xy = fc.features.flatMap((f) => (JSON.stringify(f.geometry).match(/-?\d+\.?\d*(e-?\d+)?,-?\d+\.?\d*(e-?\d+)?/g) ?? []).map((p) => p.split(",").map(Number)));
    if (xy.length) zoomTo([Math.min(...xy.map((p) => p[0])), Math.min(...xy.map((p) => p[1])), Math.max(...xy.map((p) => p[0])), Math.max(...xy.map((p) => p[1]))]);
  }

  // ---------- tools ----------
  function chooseTool(t: Tool) {
    editor.current?.reset();
    setMeasure(""); setReshaping(null);
    setTool(t);
    if (t === "measure-line" || t === "measure-area") {
      editor.current?.measureWith(t === "measure-line" ? "linestring" : "polygon", (m) =>
        setMeasure([m.area != null ? `Area ${squareMetres(m.area)}` : "", m.length != null ? `${m.area != null ? "Perimeter" : "Length"} ${metres(m.length)}` : ""].filter(Boolean).join(" · ")));
    }
    if (t === "select-area") {
      const target = layers.find((l) => l.id === activeId);
      if (!target) { toast("Choose a layer in Contents first.", "info"); setTool("explore"); return; }
      editor.current?.create("polygon", (g) => {
        void api.select(target.id, { geometry: g }).then((s) => applySelection(target.id, s, false), (e) => toast(String(e.message ?? e), "error"));
        chooseTool("explore");
      });
    }
    if (t === "create" && editLayer) startCreate(editLayer);
  }
  function startCreate(l: Layer) {
    setTool("create");
    editor.current?.create(DRAW[l.geometry_type], (g) => {
      void api.addFeature(l.id, g).then(async (f) => {
        const fid = Number(f.id);
        const fresh = await refreshLayer(l.id);
        if (fresh) setSelection((s) => ({ ...s, [l.id]: [fid] }));
        setNewFeature(l.fields.length ? { layerId: l.id, id: fid } : null);
        toast("Added. Draw the next one, or fill in its attributes.", "success");
      }, (e) => toast(e instanceof ApiError ? e.message : "Could not add it.", "error"));
    });
  }
  async function loadSnap() {
    const m = map.current;
    if (!m || !state.current.editId) return;
    const b = m.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(",");
    const vis = state.current.layers.filter((l) => l.visible);
    const parts = await Promise.all(vis.map((l) => fetch(`${BASE}/layers/${l.id}/features.geojson?bbox=${bbox}&limit=3000`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : { features: [] })).catch(() => ({ features: [] }))));
    snapFeatures.current = parts.flatMap((p) => p.features as GeoJSON.Feature[]);
  }
  /** Edit a layer: drawing starts straight away. `fresh` is a layer just made, not yet in the list. */
  function startEditing(lid: string, fresh?: Layer) {
    setEditId(lid); setTab("edit"); setActiveId(lid);
    state.current.editId = lid;
    setTimeout(() => void loadSnap(), 0);
    const l = fresh ?? layers.find((x) => x.id === lid);
    if (l) startCreate(l);
  }
  function stopEditing() {
    editor.current?.reset();
    setEditId(null); setReshaping(null); setNewFeature(null); setTool("explore");
    snapFeatures.current = [];
  }
  async function saveReshape() {
    if (!editLayer || reshaping == null) return;
    const g = editor.current?.reshaped();
    if (!g) return;
    try {
      await api.changeFeature(editLayer.id, reshaping, { geometry: g });
      editor.current?.reset(); setReshaping(null); setTool("pick-reshape");
      await refreshLayer(editLayer.id); void loadSnap();
      toast("Shape saved.", "success");
    } catch (e) { toast(e instanceof ApiError ? e.message : "Could not save the shape.", "error"); }
  }
  async function deleteSelected() {
    if (!editLayer) return;
    const ids = sel(editLayer.id);
    if (!ids.length || !window.confirm(`Delete ${ids.length} selected feature${ids.length === 1 ? "" : "s"}? Undo brings them back one at a time.`)) return;
    await api.deleteFeatures(editLayer.id, ids);
    setSelection((s) => ({ ...s, [editLayer.id]: [] }));
    await refreshLayer(editLayer.id); void loadSnap();
  }
  async function undo() {
    if (!editLayer) return;
    try {
      const r = await api.undo(editLayer.id);
      toast(`Undone: the ${r.undone === "add" ? "added feature is removed" : r.undone === "delete" ? "deleted feature is back" : "change is reversed"}.`, "success");
      await refreshLayer(editLayer.id); void loadSnap();
    } catch (e) { toast(e instanceof ApiError ? e.message : "Nothing to undo.", "info"); }
  }

  // ---------- layers ----------
  async function toggle(l: Layer) {
    replaceLayer({ ...l, visible: !l.visible });
    if (canEdit) await api.updateLayer(l.id, { visible: !l.visible }).catch(() => undefined);
  }
  async function move(lid: string, dir: 1 | -1) {
    const order = [...layers].sort((a, b) => a.position - b.position).map((l) => l.id);
    const k = order.indexOf(lid), j = k + dir;
    if (j < 0 || j >= order.length) return;
    [order[k], order[j]] = [order[j], order[k]];
    setLayers(await api.order(id, order));
  }
  async function remove(l: Layer) {
    if (!window.confirm(`Remove ${l.name} and its ${l.feature_count.toLocaleString("en-GB")} features from the project? This can't be undone.`)) return;
    await api.deleteLayer(l.id);
    if (editId === l.id) stopEditing();
    if (tableId === l.id) setTableId(null);
    setLayers((ls) => ls.filter((x) => x.id !== l.id));
    setActiveId(null);
  }
  function switchBasemap(b: string) {
    if (editId) { toast("Stop editing before changing the basemap.", "info"); return; }
    setBasemap(b);
    editor.current?.stop();
    versions.current.clear();
    map.current?.setStyle(basemapStyle(b) as maplibregl.StyleSpecification | string);
    if (canEdit) void api.updateProject(id, { view: { ...(project?.view ?? {}), basemap: b } }).catch(() => undefined);
  }
  function zoomAll() {
    const bs = layers.map((l) => l.bbox).filter(Boolean) as [number, number, number, number][];
    if (bs.length) zoomTo([Math.min(...bs.map((b) => b[0])), Math.min(...bs.map((b) => b[1])), Math.max(...bs.map((b) => b[2])), Math.max(...bs.map((b) => b[3]))]);
  }

  const pointerText = useMemo(() => {
    if (!pointer) return "";
    const [lng, lat] = pointer.split(",").map(Number);
    return formatCoords(crs, lng, lat);
  }, [pointer, crs]);

  if (error) return <div className="ge-main"><ErrorText error={error} /><Link to="/">All projects</Link></div>;
  if (!project) return <p className="loading">Opening the project…</p>;
  const editable = layers.filter(() => canEdit);
  const totalSel = Object.values(selection).reduce((n, x) => n + x.length, 0);

  return (
    <div className={`ws${tableLayer ? " has-table" : ""}`}>
      <div className="ws-ribbon">
        <div className="ws-ribbon-tabs" role="tablist">
          <Link to="/" className="ws-back" aria-label="All projects">‹</Link>
          <strong className="ws-title" title={project.name}>{project.name}</strong>
          {(["map", "insert", "select", "edit", "analysis"] as Tab[]).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
          ))}
        </div>
        <div className="ws-ribbon-body">
          {tab === "map" && <>
            <Group name="Navigate">
              <Tb on={tool === "explore"} onClick={() => chooseTool("explore")} icon="☝" label="Explore" />
              <Tb onClick={zoomAll} icon="⤢" label="Full extent" disabled={!layers.some((l) => l.bbox)} />
            </Group>
            <Group name="Layer">
              <label className="ws-tb-select">Basemap<select value={basemap} onChange={(e) => switchBasemap(e.target.value)}>{BASEMAPS.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></label>
              <Tb onClick={() => setDialog("add")} icon="＋" label="Add data" disabled={!canEdit} />
            </Group>
            <Group name="Coordinates">
              <Tb onClick={() => setDialog("crs")} icon="⌖" label={`EPSG:${project.display_epsg}`} />
            </Group>
          </>}
          {tab === "insert" && <Group name="Data">
            <Tb onClick={() => setDialog("add")} icon="⇪" label="Add data" disabled={!canEdit} />
            <Tb onClick={() => setDialog("new")} icon="✎" label="New layer" disabled={!canEdit} />
            <Tb onClick={() => setDialog("export")} icon="⇩" label="Export layer" disabled={!active} />
          </Group>}
          {tab === "select" && <>
            <Group name="Interactive">
              <Tb on={tool === "select"} onClick={() => chooseTool("select")} icon="⬚" label="Select" disabled={!active} />
              <Tb on={tool === "select-area"} onClick={() => chooseTool("select-area")} icon="⬠" label="By shape" disabled={!active} />
            </Group>
            <Group name="Queries">
              <Tb onClick={() => setDialog("attr")} icon="≡" label="By attributes" disabled={!active} />
              <Tb onClick={() => setDialog("loc")} icon="◎" label="By location" disabled={!active} />
            </Group>
            <Group name="Selection">
              <Tb onClick={() => void switchSelection()} icon="⇄" label="Switch" disabled={!active} />
              <Tb onClick={() => setSelection({})} icon="✕" label="Clear" disabled={!totalSel} />
              <Tb onClick={() => active && void zoomToSelection(active)} icon="⌕" label="Zoom to" disabled={!active || !sel(active.id).length} />
            </Group>
          </>}
          {tab === "edit" && <>
            <Group name="Editing">
              {!editId ? <label className="ws-tb-select">Layer to edit
                <select value="" onChange={(e) => e.target.value && startEditing(e.target.value)} disabled={!canEdit}>
                  <option value="">{canEdit ? "Choose a layer…" : "Read-only"}</option>
                  {editable.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select></label>
                : <Tb onClick={stopEditing} icon="■" label={`Stop editing ${editLayer?.name ?? ""}`} />}
            </Group>
            {editLayer && <>
              <Group name="Features">
                <Tb on={tool === "create"} onClick={() => chooseTool("create")} icon="✚" label={`Create ${editLayer.geometry_type === "Point" ? "points" : editLayer.geometry_type === "LineString" ? "lines" : "polygons"}`} />
                <Tb on={tool === "pick-reshape" || tool === "reshape"} onClick={() => chooseTool("pick-reshape")} icon="◇" label="Reshape" />
                <Tb onClick={() => void deleteSelected()} icon="🗑" label="Delete selected" disabled={!sel(editLayer.id).length} />
                <Tb onClick={() => void undo()} icon="↶" label="Undo" />
              </Group>
              <Group name="Snapping">
                <Tb on={snap} onClick={() => setSnap(!snap)} icon="⌁" label={snap ? "Snapping on" : "Snapping off"} />
              </Group>
              {tool === "reshape" && <Group name="Shape">
                <Button small variant="primary" onClick={() => void saveReshape()}>Save shape</Button>
                <Button small onClick={() => chooseTool("pick-reshape")}>Cancel</Button>
              </Group>}
            </>}
          </>}
          {tab === "analysis" && <>
            <Group name="Measure">
              <Tb on={tool === "measure-line"} onClick={() => chooseTool("measure-line")} icon="📏" label="Distance" />
              <Tb on={tool === "measure-area"} onClick={() => chooseTool("measure-area")} icon="▱" label="Area" />
            </Group>
            <Group name="Geoprocessing"><span className="ws-soon">Buffer, clip, intersect, dissolve and more come in phase 2.</span></Group>
          </>}
        </div>
      </div>

      <div className="ws-body">
        <Contents layers={layers} activeId={activeId} editingId={editId} canEdit={canEdit} onActivate={setActiveId} onToggle={(l) => void toggle(l)}
          onMove={(lid, d) => void move(lid, d)} onZoom={(l) => l.bbox && zoomTo(l.bbox)} onTable={(l) => setTableId(l.id)} onRemove={(l) => void remove(l)} />
        <div className="ws-map-wrap">
          <div ref={mapEl} className={`ws-map tool-${tool}`} />
          {tool !== "explore" && <div className="ws-hintbar" role="status">{{
            select: "Click features of the active layer to select them; Shift-click to add or remove.",
            "select-area": "Draw a shape: click the corners, then click the first one to finish.",
            "measure-line": `Click along the line; click the last point again to finish. ${measure}`,
            "measure-area": `Click the corners; click the first one to finish. ${measure}`,
            create: `Drawing in ${editLayer?.name}: ${editLayer?.geometry_type === "Point" ? "click to place a point" : "click each corner; click the last (or first) point again to finish"}. ${snap ? "Snaps to features within 12 px." : ""}`,
            "pick-reshape": "Click the feature to reshape.",
            reshape: "Drag a vertex to move it; drag a midpoint to add one; right-click a vertex to remove it. Then Save shape.",
          }[tool] ?? ""}</div>}
        </div>
        {identify && tool === "explore" ? (
          <aside className="ws-panel" aria-label="Identify">
            <div className="ws-pane-head"><strong>{identify.layer.name}</strong><button type="button" onClick={() => setIdentify(null)} aria-label="Close">✕</button></div>
            <dl className="ws-dl ws-panel-body">
              <dt>ID</dt><dd>{identify.id}</dd>
              {identify.layer.fields.map((f) => <div key={f.name} className="ws-dl-row"><dt>{f.name}</dt><dd>{identify.props[f.name] == null ? <span className="ws-null">&lt;null&gt;</span> : String(identify.props[f.name])}</dd></div>)}
            </dl>
            <p className="ws-panel-body"><Button small onClick={() => void zoomToFeature(identify.layer, identify.id)}>Zoom to it</Button></p>
          </aside>
        ) : newFeature && editLayer ? (
          <NewFeatureForm layer={editLayer} fid={newFeature.id} onDone={() => { setNewFeature(null); void refreshLayer(editLayer.id); }} />
        ) : active ? (
          <LayerPanel layer={active} canEdit={canEdit} selected={sel(active.id)} onChange={(l) => { replaceLayer(l); }} onExport={() => setDialog("export")} />
        ) : <aside className="ws-panel"><p className="ws-empty">Choose a layer in Contents to see its symbology, labels and fields.</p></aside>}
      </div>

      {tableLayer && <AttributeTable layer={tableLayer} selected={sel(tableLayer.id)} editing={editId === tableLayer.id}
        onSelect={(ids, add) => setSelection((s) => ({ ...s, [tableLayer.id]: add ? Array.from(new Set([...(s[tableLayer.id] ?? []), ...ids])) : ids }))}
        onZoomTo={(fid) => void zoomToFeature(tableLayer, fid)} onClose={() => setTableId(null)} onEdited={() => void refreshLayer(tableLayer.id)} />}

      <footer className="ws-status">
        <span className="ws-mono" title={crs?.name}>{pointerText || "—"}</span>
        <span>{crs ? `${crs.name}` : `EPSG:${project.display_epsg}`}</span>
        <span>Zoom {zoom.toFixed(1)}</span>
        {totalSel > 0 && <span>{totalSel.toLocaleString("en-GB")} selected</span>}
        {measure && <strong>{measure}</strong>}
        <span className="ge-spacer" />
        {!canEdit && <span className="ge-badge">{me.can_write ? "Viewer: read-only" : "Read-only licence"}</span>}
      </footer>

      {dialog === "add" && <AddData projectId={id} onClose={() => setDialog(null)} onDone={(ls) => {
        setLayers((cur) => [...cur, ...ls]); setActiveId(ls[0].id); setDialog(null);
        const dropped = ls.reduce((n, l) => n + (l.source.dropped ?? 0), 0);
        toast(`Added ${ls.map((l) => `${l.name} (${l.feature_count.toLocaleString("en-GB")})`).join(", ")}.${dropped ? ` ${dropped} feature(s) had no usable shape and were left out.` : ""}`, "success");
        if (ls[0].bbox) zoomTo(ls[0].bbox);
      }} />}
      {dialog === "new" && <NewLayer projectId={id} onClose={() => setDialog(null)} onDone={(l) => { setLayers((cur) => [...cur, l]); setDialog(null); startEditing(l.id, l); }} />}
      {dialog === "attr" && active && <SelectByAttributes layer={active} onClose={() => setDialog(null)} onDone={(s, add) => applySelection(active.id, s, add)} />}
      {dialog === "loc" && active && <SelectByLocation layer={active} layers={layers} selection={selection} onClose={() => setDialog(null)} onDone={(s, add) => applySelection(active.id, s, add)} />}
      {dialog === "export" && active && <Export layer={active} selected={sel(active.id)} epsg={project.display_epsg} onClose={() => setDialog(null)} />}
      {dialog === "crs" && <CrsDialog project={project} canEdit={canEdit} onClose={() => setDialog(null)} onDone={async (p) => { setProject(p); setCrs(await loadCrs(p.display_epsg)); setDialog(null); }} />}
    </div>
  );
}

function Group({ name, children }: { name: string; children: React.ReactNode }) {
  return <div className="ws-group"><div className="ws-group-body">{children}</div><div className="ws-group-name">{name}</div></div>;
}
function Tb({ icon, label, onClick, on, disabled }: { icon: string; label: string; onClick: () => void; on?: boolean; disabled?: boolean }) {
  return <button type="button" className={`ws-tb${on ? " is-on" : ""}`} onClick={onClick} disabled={disabled} aria-pressed={on}><span aria-hidden="true">{icon}</span>{label}</button>;
}

function NewFeatureForm({ layer, fid, onDone }: { layer: Layer; fid: number; onDone: () => void }) {
  const [v, setV] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  return (
    <aside className="ws-panel" aria-label="New feature">
      <div className="ws-pane-head"><strong>New feature in {layer.name}</strong></div>
      <form className="ws-form ws-panel-body" onSubmit={(e) => { e.preventDefault(); void api.changeFeature(layer.id, fid, { properties: v }).then(onDone, setError); }}>
        {layer.fields.map((f) => (
          <label key={f.name} className="ws-f">{f.name}
            {f.type === "boolean" ? <select value={v[f.name] ?? ""} onChange={(e) => setV({ ...v, [f.name]: e.target.value })}><option value="">—</option><option value="true">Yes</option><option value="false">No</option></select>
              : <input type={f.type === "date" ? "date" : f.type === "integer" || f.type === "number" ? "number" : "text"} step={f.type === "number" ? "any" : undefined}
                  value={v[f.name] ?? ""} onChange={(e) => setV({ ...v, [f.name]: e.target.value })} />}
          </label>
        ))}
        <ErrorText error={error} />
        <div className="ws-row"><Button type="submit" variant="primary" small>Save attributes</Button><Button small variant="ghost" onClick={onDone}>Skip</Button></div>
      </form>
    </aside>
  );
}

function CrsDialog({ project, canEdit, onClose, onDone }: { project: Project; canEdit: boolean; onClose: () => void; onDone: (p: Project) => void }) {
  const [epsg, setEpsg] = useState<number | null>(project.display_epsg);
  const [error, setError] = useState<unknown>(null);
  return (
    <Dialog title="The project's coordinate system" onClose={onClose}>
      <div className="ws-form">
        <p className="ws-muted">Coordinates are shown in this system in the status bar, and exports offer it first. Data is stored in WGS 84 and converted with the right datum shift (e.g. Minna to WGS 84).</p>
        <CrsPicker value={epsg} onChange={setEpsg} />
        <ErrorText error={error} />
        <div className="ws-row">
          <Button variant="primary" disabled={!canEdit || !epsg} onClick={() => void api.updateProject(project.id, { display_epsg: epsg! }).then(onDone, setError)}>Use it</Button>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </Dialog>
  );
}
