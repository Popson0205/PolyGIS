import maplibregl, { type Map as MapLibre, type MapGeoJSONFeature } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
  ArrowLeftRight, Box, BarChart3, ChevronDown, CloudSun, Earth, Flame, Layers3, Leaf, LineChart, Map as MapIcon, Sprout, Table2, Thermometer, ChevronRight, ChevronUp, CircleDot, CircleStop, Combine, Download, Droplets, Eraser, FilePlus2, Globe2, Grid3x3, Info,
  LandPlot, Lasso, Layers, Link2, ListFilter, LocateFixed, Magnet, Maximize, Merge, Mountain, MousePointer2, MousePointerClick, PanelLeftOpen,
  PanelRightClose, PanelRightOpen, Printer, Ruler, UploadCloud, Scan, Scissors, Search, SlidersHorizontal, Spline, SquarePen, Trash2, TrendingUp, Undo2, Waves, Wrench, X, ZoomIn,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import AttributeTable from "../components/AttributeTable";
import Contents, { type PanelTab, type SelectionOp } from "../components/Contents";
import { ImageryDialog } from "../components/Imagery";
import { AddData, CrsPicker, Dialog, Export, NewLayer, SelectByAttributes, SelectByLocation } from "../components/Dialogs";
import Geoprocessing from "../components/Geoprocessing";
import PolyEarth, { useEarthJobs } from "../components/PolyEarth";
import LayerPanel from "../components/LayerPanel";
import LayoutView from "../components/LayoutView";
import ShareDialog from "../components/ShareDialog";
import PublishDialog from "../components/PublishDialog";
import LinkedDialog from "../components/LinkedDialog";
import { Apps, UserMenu, licenceNotice, useApps } from "../components/Bar";
import { Button, ErrorText, IconButton, Resizer, useStored, useToast } from "../components/ui";
import RasterPanel from "../components/RasterPanel";
import { ApiError, BASE, api, rasterDownloadUrl, rasterTileUrl, terrainTileUrl, tileUrl } from "../lib/api";
import { formatCoords, loadCrs, metres, squareMetres } from "../lib/coords";
import { Editor } from "../lib/editor";
import { BASEMAPS, GLOBAL_TERRAIN, GLYPHS, allIds, basemapStyle, ids as layerIds, mapLayers, src } from "../lib/mapstyle";
import type { CrsDef, Layer, Me, Project, Selection, VectorType } from "../lib/types";

type Tab = "map" | "insert" | "select" | "edit" | "analysis" | "earth" | "share";
type Tool = "explore" | "select" | "select-area" | "measure-line" | "measure-area" | "create" | "reshape" | "pick-reshape";
type DialogName = "add" | "imagery" | "new" | "attr" | "loc" | "export" | "crs" | "plan" | "share" | "publish" | "linked" | null;
const DRAW = { Point: "point", LineString: "linestring", Polygon: "polygon" } as const;

/** A project open in the workspace, laid out like a desktop GIS: ribbon, contents, map, properties, table, status. */
export default function WorkspacePage({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
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
  const [gp, setGp] = useState<{ open: boolean; start: string | null; query: string }>({ open: false, start: null, query: "" });
  const [earth, setEarth] = useState<{ open: boolean; start: string | null }>({ open: false, start: null });
  const [panelTab, setPanelTab] = useState<{ tab: PanelTab; n: number }>({ tab: "symbology", n: 0 });
  const [layoutOpen, setLayoutOpen] = useState(false);
  // the 3D view: tilted, on terrain from an elevation model in the project or the open global one
  const [view3d, setView3d] = useStored<{ on: boolean; terrain: string; exaggeration: number }>(`view3d-${id}`, { on: false, terrain: "global", exaggeration: 1.5 });
  const earthJobs = useEarthJobs((j) => {
    if (j.status === "failed") { toast(`${j.tool_name} did not finish: ${j.error}`, "bad"); return; }
    if (j.status === "stopped") { toast(`${j.tool_name} stopped. Nothing was kept.`, "info"); return; }
    if (j.layers.length) {
      setLayers((cur) => [...cur.filter((l) => !j.layers.some((x) => x.id === l.id)), ...j.layers]);
      setActiveId(j.layers[0].id);
      setLeftOpen(true);
    }
    toast(j.layers.length ? `${j.tool_name}: ${j.layers.map((l) => l.name).join(", ")} added to the map.` : `${j.tool_name} is ready.`, "success");
  });
  const [ribbonFolded, setRibbonFolded] = useStored("ribbon-folded", false);
  // on a wide screen the panes stay docked (and remembered); on a tablet or phone one at a time slides over the map
  const narrow = useNarrow(1100);
  const [leftDocked, setLeftDocked] = useStored("left-open", true);
  const [rightDocked, setRightDocked] = useStored("right-open", true);
  const [drawer, setDrawer] = useState<"left" | "right" | null>(null);
  const leftOpen = narrow ? drawer === "left" : leftDocked;
  const rightOpen = narrow ? drawer === "right" : rightDocked;
  const setLeftOpen = (v: boolean) => (narrow ? setDrawer(v ? "left" : drawer === "left" ? null : drawer) : setLeftDocked(v));
  const setRightOpen = (v: boolean) => (narrow ? setDrawer(v ? "right" : drawer === "right" ? null : drawer) : setRightDocked(v));
  const [leftW, setLeftW] = useStored("left-width", 272);
  const [rightW, setRightW] = useStored("right-width", 320);
  const [tableH, setTableH] = useStored("table-height", 260);
  const [command, setCommand] = useState("");
  const [scale, setScale] = useState(0);
  const commandEl = useRef<HTMLInputElement>(null);
  const apps = useApps();

  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibre | null>(null);
  const editor = useRef<Editor | null>(null);
  const snapFeatures = useRef<GeoJSON.Feature[]>([]);
  const versions = useRef<Map<string, number>>(new Map());
  const state = useRef({ layers, selection, reshaping, editId, snap, tool, activeId, view3d });
  state.current = { layers, selection, reshaping, editId, snap, tool, activeId, view3d };

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
    m.addControl(new maplibregl.ScaleControl({ unit: "metric", maxWidth: 120 }), "bottom-left");
    m.on("style.load", () => {
      if (!m.getStyle().glyphs) m.setGlyphs(GLYPHS);          // labels need fonts, whatever the basemap
      sync();
    });
    m.on("mousemove", (e) => setPointer(e.lngLat ? `${e.lngLat.lng},${e.lngLat.lat}` : ""));
    const rescale = () => {
      const lat = m.getCenter().lat;
      setScale((40075016.686 * Math.cos((lat * Math.PI) / 180)) / (512 * 2 ** m.getZoom()) * (96 / 0.0254));
    };
    m.on("zoom", () => { setZoom(m.getZoom()); rescale(); });
    m.on("move", rescale);
    rescale();
    let saveT: ReturnType<typeof setTimeout>;
    m.on("moveend", () => {
      setZoom(m.getZoom());
      if (state.current.editId) void loadSnap();
      if (!canEdit) return;
      clearTimeout(saveT);
      saveT = setTimeout(() => { const c = m.getCenter(); void api.updateProject(id, { view: { center: [c.lng, c.lat], zoom: m.getZoom(), bearing: m.getBearing(), basemap: basemapRef.current } }).catch(() => undefined); }, 1500);
    });
    m.on("click", (e) => { setPointer(`${e.lngLat.lng},${e.lngLat.lat}`); void onMapClick(e.point); });
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
      if (l.geometry_type === "Raster" && l.raster?.missing) continue;     // its file is gone: asking for tiles would only fail
      if (m.getSource(src(l)) && versions.current.get(l.id) !== l.version) m.removeSource(src(l));
      if (!m.getSource(src(l))) {
        if (l.geometry_type === "Raster") m.addSource(src(l), { type: "raster", tiles: [rasterTileUrl(l)], tileSize: 256, minzoom: 0, maxzoom: 22, bounds: l.bbox ?? undefined });
        else m.addSource(src(l), { type: "vector", tiles: [tileUrl(l)], minzoom: 0, maxzoom: 16, promoteId: undefined });
        versions.current.set(l.id, l.version);
      }
      for (const spec of mapLayers(l, s[l.id] ?? [], l.id === e && r != null ? [r] : [])) m.addLayer(spec, before);
    }
    applyTerrain(m);
  }, []);
  useEffect(() => { sync(); }, [layers, selection, reshaping, editId, view3d, sync]);

  /** Terrain under the map in the 3D view (an elevation model of the project, or the global Terrain Tiles); none in 2D. */
  function applyTerrain(m: MapLibre) {
    const v = state.current.view3d;
    const dem = state.current.layers.find((l) => l.id === v.terrain && l.geometry_type === "Raster" && !l.raster?.missing);
    const want = !v.on ? null : dem ? `dem-${dem.id}-${dem.version}` : "global";
    const have = (m.getTerrain()?.source as string | undefined) ?? null;
    if (have && have !== want) { m.setTerrain(null); if (m.getSource(have)) m.removeSource(have); }
    if (!want) return;
    if (!m.getSource(want)) {
      m.addSource(want, dem ? { type: "raster-dem", tiles: [terrainTileUrl(dem)], tileSize: 256, encoding: "terrarium", bounds: dem.bbox ?? undefined, maxzoom: 16 }
        : { type: "raster-dem", tiles: GLOBAL_TERRAIN.tiles, tileSize: 256, encoding: "terrarium", maxzoom: GLOBAL_TERRAIN.maxzoom, attribution: GLOBAL_TERRAIN.attribution });
    }
    m.setTerrain({ source: want, exaggeration: v.exaggeration });
    const sky = (m as unknown as { setSky?: (s: Record<string, unknown>) => void }).setSky;
    sky?.call(m, { "sky-color": "#a9c8ec", "horizon-color": "#e8eef6", "fog-color": "#eef2f7", "sky-horizon-blend": 0.6, "horizon-fog-blend": 0.5, "fog-ground-blend": 0.85 });
  }
  function toggle3d(on: boolean) {
    setView3d({ ...view3d, on });
    const m = map.current;
    if (!m) return;
    m.easeTo({ pitch: on ? 60 : 0, bearing: on ? (m.getBearing() || -20) : 0, duration: 900 });
    if (on && !layers.some((l) => l.raster && !l.raster.missing) && view3d.terrain !== "global") setView3d({ ...view3d, on, terrain: "global" });
  }

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
      if (t === "explore" && (!f || f.id == null)) {
        // no feature here: the value of the topmost visible raster under the pointer
        const at = m.unproject(pt);
        const r = [...state.current.layers].filter((l) => l.visible && l.geometry_type === "Raster" && l.bbox && at.lng >= l.bbox[0] && at.lng <= l.bbox[2]
          && at.lat >= l.bbox[1] && at.lat <= l.bbox[3]).sort((a, b) => b.position - a.position)[0];
        if (r) {
          try {
            const v = await api.rasterValue(r.id, at.lng, at.lat);
            if (v.values) { setIdentify({ layer: r, id: 0, props: Object.fromEntries(v.bands.map((b, k) => [b, v.values![k]])) }); return; }
          } catch { /* fall through */ }
        }
      }
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
  /** What the map shows now, as west, south, east, north (the whole world's extent when the map is not ready). */
  function mapBox(): [number, number, number, number] {
    const b = map.current?.getBounds();
    return b ? [Math.max(-180, b.getWest()), Math.max(-85, b.getSouth()), Math.min(180, b.getEast()), Math.min(85, b.getNorth())] : [-180, -85, 180, 85];
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
    editor.current?.create(DRAW[l.geometry_type as VectorType], (g) => {
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
  /** A new drawing order, bottom to top. */
  async function reorder(ids: string[]) {
    const pos = new Map(ids.map((x, k) => [x, k]));
    setLayers((ls) => ls.map((l) => ({ ...l, position: pos.get(l.id) ?? l.position })));
    try { setLayers(await api.order(id, ids)); } catch (e) { toast(e instanceof ApiError ? e.message : "Could not change the order.", "error"); void load(); }
  }

  /** A linked layer brought up to date with its app (quietly when the project opens). */
  async function refreshLinked(l: Layer, quiet = false) {
    try {
      const fresh = await api.refreshLinked(l.id);
      replaceLayer(fresh);
      if (!quiet) toast(`${l.name} is up to date with ${l.source.linked?.app_name}: ${fresh.feature_count.toLocaleString("en-GB")} features.`, "success");
    } catch (e) { if (!quiet) toast(e instanceof ApiError ? e.message : "Could not refresh it.", "error"); }
  }
  const refreshedOnOpen = useRef(false);
  useEffect(() => {
    if (refreshedOnOpen.current || !project || !canEdit) return;
    refreshedOnOpen.current = true;
    const stale = layers.filter((l) => l.source.linked && Date.now() - new Date(l.source.linked.refreshed_at ?? 0).getTime() > 10 * 60 * 1000);
    for (const l of stale) void refreshLinked(l, true);
  }, [project, layers]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---------- keyboard: Esc ends a tool, Ctrl+K finds a tool ----------
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); commandEl.current?.focus(); commandEl.current?.select(); return; }
      const typing = (e.target as HTMLElement)?.closest?.("input, textarea, select, [role=dialog]");
      if (e.key === "Escape" && !typing && state.current.tool !== "explore") chooseTool("explore");
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  /** Contents' layer menu: open the layer's properties at a tab. */
  function openPanel(l: Layer, tab: PanelTab) {
    setActiveId(l.id); setGp({ open: false, start: null, query: "" }); setEarth({ open: false, start: null }); setIdentify(null);
    setPanelTab((p) => ({ tab, n: p.n + 1 })); setRightOpen(true);
  }
  async function selectionOp(l: Layer, op: SelectionOp) {
    setActiveId(l.id);
    try {
      if (op === "clear") setSelection((cur) => { const n = { ...cur }; delete n[l.id]; return n; });
      else if (op === "zoom") await zoomToSelection(l);
      else if (op === "attributes") setDialog("attr");
      else if (op === "location") setDialog("loc");
      else {
        const all = await api.select(l.id, { filters: [] });
        const now = new Set(sel(l.id));
        const ids = op === "all" ? all.ids : all.ids.filter((x) => !now.has(x));
        setSelection((cur) => ({ ...cur, [l.id]: ids }));
        toast(`${ids.length.toLocaleString("en-GB")} selected.`, "success");
      }
    } catch (e) { toast(e instanceof ApiError ? e.message : "That did not work.", "error"); }
  }
  async function labelsOn(l: Layer, on: boolean) {
    if (on && !l.labels) {
      const f = l.fields.find((x) => /name|label|id/i.test(x.name)) ?? l.fields[0];
      if (!f) { openPanel(l, "labels"); return; }
      try { replaceLayer(await api.updateLayer(l.id, { labels: { field: f.name, size: 12, color: "#1f2937", halo: "#ffffff" } })); toast(`Labelled by ${f.name}: change it in Labeling properties.`, "success"); }
      catch (e) { toast(e instanceof ApiError ? e.message : "Could not label it.", "error"); }
      return;
    }
    if (!on && l.labels) {
      try { replaceLayer(await api.updateLayer(l.id, { clear_labels: true })); } catch (e) { toast(e instanceof ApiError ? e.message : "Could not remove the labels.", "error"); }
    }
  }
  async function renameLayer(l: Layer, name: string) {
    try { replaceLayer(await api.updateLayer(l.id, { name })); } catch (e) { toast(e instanceof ApiError ? e.message : "Could not rename it.", "error"); }
  }
  /** Paste properties: the copied layer's symbology (and labels, when the field is there) onto another layer of the same kind. */
  async function pasteProperties(target: Layer, from: Layer) {
    try {
      if (target.geometry_type === "Raster") replaceLayer(await api.rasterStyle(target.id, from.style as unknown as Parameters<typeof api.rasterStyle>[1]));
      else {
        const fieldOk = (f?: string | null) => !f || target.fields.some((x) => x.name === f);
        if (!fieldOk(from.style.field)) { toast(`${target.name} has no field ${from.style.field}, which ${from.name}'s symbology uses.`, "error"); return; }
        replaceLayer(await api.updateLayer(target.id, { style: from.style, ...(from.labels && fieldOk(from.labels.field) ? { labels: from.labels } : {}) }));
      }
      toast(`${from.name}'s symbology pasted onto ${target.name}.`, "success");
    } catch (e) { toast(e instanceof ApiError ? e.message : "Could not paste the properties.", "error"); }
  }
  /** Zoom so one cell of the raster is one pixel on screen. */
  function sourceResolution(l: Layer) {
    const m = map.current;
    if (!m || !l.raster || !l.bbox) return;
    const lat = (l.bbox[1] + l.bbox[3]) / 2;
    const metresPerPixelAtZ0 = 156543.03392 * Math.cos((lat * Math.PI) / 180);
    const z = Math.log2(metresPerPixelAtZ0 / Math.max(0.01, l.raster.res_m[0]));
    m.flyTo({ center: m.getCenter().toArray() as [number, number], zoom: Math.min(22, Math.max(0, z)) });
  }
  async function remove(l: Layer) {
    const what = l.raster ? "the raster" : `its ${l.feature_count.toLocaleString("en-GB")} features`;
    if (!window.confirm(`Remove ${l.name} and ${what} from the project? This can't be undone.`)) return;
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
  const editable = layers.filter((l) => canEdit && l.geometry_type !== "Raster" && !l.source.linked);
  const totalSel = Object.values(selection).reduce((n, x) => n + x.length, 0);
  const notice = licenceNotice(me.product);
  const openTool = (t: string | null, q = "") => { setEarth({ open: false, start: null }); setGp({ open: true, start: t, query: q }); setRightOpen(true); };
  const openEarth = (t: string | null) => { setGp({ open: false, start: null, query: "" }); setEarth({ open: true, start: t }); setRightOpen(true); };
  const kindWord = editLayer?.geometry_type === "Point" ? "points" : editLayer?.geometry_type === "LineString" ? "lines" : "polygons";
  const hint: Partial<Record<Tool, ReactNode>> = {
    select: <>Click features of <strong>{active?.name}</strong> to select them. Shift-click adds or removes.</>,
    "select-area": <>Draw a shape around the features of <strong>{active?.name}</strong>: click the corners, then the first one again.</>,
    "measure-line": <>{measure ? <strong>{measure}</strong> : "Click along the line"}; click the last point again to finish.</>,
    "measure-area": <>{measure ? <strong>{measure}</strong> : "Click the corners"}; click the first one again to finish.</>,
    create: <>Drawing {kindWord} in <strong>{editLayer?.name}</strong>: {editLayer?.geometry_type === "Point" ? "click to place one" : "click each corner, then the last (or first) point again"}.{snap ? " Snapping is on." : ""}</>,
    "pick-reshape": <>Click the feature of <strong>{editLayer?.name}</strong> to reshape.</>,
    reshape: <>Drag a vertex to move it, a midpoint to add one; right-click a vertex to remove it.</>,
  };
  const right = earth.open ? (
    <PolyEarth projectId={id} layers={layers} selection={selection} activeId={activeId} canEdit={canEdit} allowed={me.features.includes("raster")}
      start={earth.start} jobs={earthJobs.jobs} viewBox={mapBox} onStarted={earthJobs.add} onStop={(jid) => void earthJobs.stop(jid)} onClose={() => setEarth({ open: false, start: null })}
      onShowLayer={(l) => { setActiveId(l.id); if (l.bbox) zoomTo(l.bbox); }} />
  ) : gp.open ? (
    <Geoprocessing projectId={id} layers={layers} selection={selection} activeId={activeId} canEdit={canEdit} query={gp.query}
      allowed={me.features.includes("geoprocessing")} rasterAllowed={me.features.includes("raster")} start={gp.start} onClose={() => setGp({ open: false, start: null, query: "" })}
      onDone={(l) => { setLayers((cur) => [...cur, l]); setActiveId(l.id); if (l.bbox) zoomTo(l.bbox); toast(`${l.name} added to the map.`, "success"); }} />
  ) : identify && tool === "explore" ? (
    <aside className="ws-panel" aria-label="Identify">
      <div className="ws-pane-head"><Info size={16} aria-hidden="true" className="ws-pane-icon" /><strong>{identify.layer.name}</strong>
        <IconButton label="Close" onClick={() => setIdentify(null)}><X size={16} /></IconButton></div>
      <div className="ws-scroll ws-panel-body">
        <dl className="ws-dl">
          {identify.layer.geometry_type === "Raster"
            ? Object.entries(identify.props).map(([k, v]) => <div key={k} className="ws-dl-row"><dt>{k}</dt><dd>{v == null ? <span className="ws-null">no data</span> : String(v)}</dd></div>)
            : <><dt>ID</dt><dd>{identify.id}</dd>
              {identify.layer.fields.map((f) => <div key={f.name} className="ws-dl-row"><dt>{f.name}</dt><dd>{identify.props[f.name] == null ? <span className="ws-null">empty</span> : String(identify.props[f.name])}</dd></div>)}</>}
        </dl>
        {identify.layer.geometry_type !== "Raster" && <p><button type="button" className="ws-link" onClick={() => void zoomToFeature(identify.layer, identify.id)}><ZoomIn size={14} aria-hidden="true" />Zoom to this feature</button></p>}
      </div>
    </aside>
  ) : newFeature && editLayer ? (
    <NewFeatureForm layer={editLayer} fid={newFeature.id} onDone={() => { setNewFeature(null); void refreshLayer(editLayer.id); }} />
  ) : active?.geometry_type === "Raster" ? (
    <RasterPanel key={`${active.id}-${panelTab.n}`} initialTab={panelTab.tab} layer={active} canEdit={canEdit} canExport={me.features.includes("export")} onChange={(l) => { replaceLayer(l); }} />
  ) : active ? (
    <LayerPanel key={`${active.id}-${panelTab.n}`} initialTab={panelTab.tab} layer={active} canEdit={canEdit} selected={sel(active.id)} onChange={(l) => { replaceLayer(l); }} onExport={() => setDialog("export")} />
  ) : (
    <aside className="ws-panel" aria-label="Properties">
      <div className="ws-pane-head"><strong>Properties</strong></div>
      <div className="ws-empty-state"><SlidersHorizontal size={22} aria-hidden="true" />Choose a layer in Contents to style it, label it and see its fields.</div>
    </aside>
  );
  const tabs: [Tab, string][] = [["map", "Map"], ["insert", "Insert"], ["select", "Select"], ["edit", "Edit"], ["analysis", "Analysis"], ["earth", "Poly Earth"], ["share", "Share"]];
  const layoutsAllowed = me.features.includes("layouts");
  const openLayout = () => { if (!layoutsAllowed) { setDialog("plan"); return; } if (map.current) setLayoutOpen(true); };

  return (
    <div className="ws" style={{ "--left": `${leftW}px`, "--right": `${rightW}px`, "--table": `${tableH}px` } as React.CSSProperties}>
      <div className="ws-top">
      <header className="ws-titlebar">
        <a className="ge-brand" href="/" aria-label="GeoEstate PolyGIS: all projects"><img src="/logo.png" alt="" /><span>Poly<strong>GIS</strong></span></a>
        <nav className="ws-crumbs" aria-label="Where you are">
          <ChevronRight size={14} aria-hidden="true" /><Link to="/">Projects</Link><ChevronRight size={14} aria-hidden="true" />
          <strong title={project.description || project.name}>{project.name}</strong>
        </nav>
        <form className="ws-command" role="search" onSubmit={(e) => { e.preventDefault(); openTool(null, command); }}>
          <Search size={15} aria-hidden="true" />
          <input ref={commandEl} value={command} placeholder="Find a tool…" aria-label="Find a tool"
            onChange={(e) => { setCommand(e.target.value); if (e.target.value.trim()) openTool(null, e.target.value); }}
            onKeyDown={(e) => { if (e.key === "Escape") { setCommand(""); e.currentTarget.blur(); } }} />
          <span className="kbd" aria-hidden="true">Ctrl K</span>
        </form>
        <Apps apps={apps} />
        <UserMenu me={me} onSignOut={onSignOut} />
      </header>
      {notice && <div className={`ge-notice ge-notice-${notice.tone}`} role="status">{notice.text}</div>}
      </div>

      <div className={`ws-ribbon${ribbonFolded ? " is-folded" : ""}`}>
        <div className="ws-ribbon-tabs" role="tablist" aria-label="Ribbon">
          {tabs.map(([t, label]) => (
            <button key={t} role="tab" aria-selected={tab === t} className={t === "edit" && editId ? "ws-tab-ctx" : ""}
              onClick={() => { if (ribbonFolded && tab === t) setRibbonFolded(false); setTab(t); }} onDoubleClick={() => setRibbonFolded(!ribbonFolded)}>{label}{t === "edit" && editId ? ": editing" : ""}</button>
          ))}
          <IconButton label={ribbonFolded ? "Show the ribbon" : "Fold the ribbon away"} onClick={() => setRibbonFolded(!ribbonFolded)}>
            {ribbonFolded ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
          </IconButton>
        </div>
        <div className="ws-ribbon-body" role="tabpanel">
          {tab === "map" && <>
            <Group name="Navigate">
              <Tb on={tool === "explore"} onClick={() => chooseTool("explore")} icon={<MousePointer2 />} label="Explore" />
              <Tb onClick={zoomAll} icon={<Maximize />} label="Full extent" disabled={!layers.some((l) => l.bbox)} />
              <Tb onClick={() => active?.bbox && zoomTo(active.bbox)} icon={<ZoomIn />} label="Zoom to layer" disabled={!active?.bbox} />
            </Group>
            <Group name="Basemap">
              <div className="ws-basemaps" role="radiogroup" aria-label="Basemap">
                {BASEMAPS.map((b) => (
                  <button key={b.id} type="button" role="radio" aria-checked={basemap === b.id} className={`ws-basemap${basemap === b.id ? " is-on" : ""}`} onClick={() => switchBasemap(b.id)} title={b.name}>
                    <i style={{ background: BASEMAP_LOOK[b.id] ?? "#ddd" }} />{b.name.split(" (")[0]}
                  </button>
                ))}
              </div>
            </Group>
            <Group name="3D">
              <Tb on={view3d.on} onClick={() => toggle3d(!view3d.on)} icon={<Box />} label={view3d.on ? "3D on" : "3D view"} />
              <div className="ws-tb-stack ws-tb-mini">
                <label className="ws-tb-select">Terrain
                  <select value={view3d.terrain} onChange={(e) => setView3d({ ...view3d, terrain: e.target.value })}>
                    <option value="global">Global (Terrain Tiles)</option>
                    {layers.filter((l) => l.raster && !l.raster.missing && l.raster.count === 1).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                  </select></label>
                <label className="ws-tb-select">Exaggeration
                  <select value={view3d.exaggeration} onChange={(e) => setView3d({ ...view3d, exaggeration: Number(e.target.value) })}>
                    {[1, 1.5, 2, 3, 5].map((x) => <option key={x} value={x}>{x}×</option>)}</select></label>
              </div>
            </Group>
            <Group name="Layer">
              <Tb onClick={() => setDialog("add")} icon={<FilePlus2 />} label="Add data" disabled={!canEdit} />
            </Group>
            <Group name="Coordinates">
              <Tb onClick={() => setDialog("crs")} icon={<Globe2 />} label={crs ? shortCrs(crs.name) : `EPSG:${project.display_epsg}`} />
            </Group>
          </>}
          {tab === "insert" && <>
            <Group name="Data">
              <Tb onClick={() => setDialog("add")} icon={<FilePlus2 />} label="Add data" disabled={!canEdit} />
              <Tb onClick={() => setDialog("new")} icon={<Layers />} label="New layer" disabled={!canEdit} />
              <Tb onClick={() => setDialog("linked")} icon={<Link2 />} label="Linked layer" disabled={!canEdit} />
              <Tb onClick={() => setDialog("export")} icon={<Download />} label="Export layer" disabled={!active || active.geometry_type === "Raster"} />
            </Group>
            <Group name="Layout">
              <Tb onClick={openLayout} icon={<Printer />} label="New layout" />
            </Group>
          </>}
          {tab === "share" && <>
            <Group name="Link">
              <Tb onClick={() => setDialog("share")} icon={<Link2 />} label="Share link" />
            </Group>
            <Group name="Data hub">
              <Tb onClick={() => setDialog("publish")} icon={<UploadCloud />} label="Publish layer" disabled={!canEdit || !active || active.geometry_type === "Raster"} />
            </Group>
            <Group name="Print">
              <Tb onClick={openLayout} icon={<Printer />} label="Print layout" />
            </Group>
            <Group name="Export">
              <Tb onClick={() => setDialog("export")} icon={<Download />} label="Export layer" disabled={!active || active.geometry_type === "Raster"} />
            </Group>
          </>}
          {tab === "select" && <>
            <Group name="Interactive">
              <Tb on={tool === "select"} onClick={() => chooseTool("select")} icon={<MousePointerClick />} label="Select" disabled={!active || active.geometry_type === "Raster"} />
              <Tb on={tool === "select-area"} onClick={() => chooseTool("select-area")} icon={<Lasso />} label="By shape" disabled={!active || active.geometry_type === "Raster"} />
            </Group>
            <Group name="Queries">
              <Tb onClick={() => setDialog("attr")} icon={<ListFilter />} label="By attributes" disabled={!active || active.geometry_type === "Raster"} />
              <Tb onClick={() => setDialog("loc")} icon={<LocateFixed />} label="By location" disabled={!active || active.geometry_type === "Raster"} />
            </Group>
            <Group name="Selection">
              <Tb onClick={() => void switchSelection()} icon={<ArrowLeftRight />} label="Switch" disabled={!active || active.geometry_type === "Raster"} />
              <Tb onClick={() => setSelection({})} icon={<Eraser />} label="Clear" disabled={!totalSel} />
              <Tb onClick={() => active && void zoomToSelection(active)} icon={<Scan />} label="Zoom to" disabled={!active || !sel(active.id).length} />
            </Group>
          </>}
          {tab === "edit" && <>
            <Group name="Editing">
              {!editId ? <label className="ws-tb-select">Layer to edit
                <select value="" onChange={(e) => e.target.value && startEditing(e.target.value)} disabled={!canEdit || !editable.length}>
                  <option value="">{!canEdit ? "Read-only" : editable.length ? "Choose a layer…" : "No vector layers yet"}</option>
                  {editable.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select></label>
                : <Tb onClick={stopEditing} icon={<CircleStop />} label="Stop editing" />}
            </Group>
            {editLayer && <>
              <Group name="Features">
                <Tb on={tool === "create"} onClick={() => chooseTool("create")} icon={<SquarePen />} label={`Create ${kindWord}`} />
                <Tb on={tool === "pick-reshape" || tool === "reshape"} onClick={() => chooseTool("pick-reshape")} icon={<Spline />} label="Reshape" />
                <Tb onClick={() => void deleteSelected()} icon={<Trash2 />} label="Delete selected" disabled={!sel(editLayer.id).length} />
                <Tb onClick={() => void undo()} icon={<Undo2 />} label="Undo" />
              </Group>
              <Group name="Snapping">
                <Tb on={snap} onClick={() => setSnap(!snap)} icon={<Magnet />} label={snap ? "Snapping on" : "Snapping off"} />
              </Group>
              {tool === "reshape" && <Group name="Shape">
                <div className="ws-tb-stack" style={{ paddingTop: 6 }}>
                  <Button small variant="primary" onClick={() => void saveReshape()}>Save shape</Button>
                  <Button small variant="ghost" onClick={() => chooseTool("pick-reshape")}>Cancel</Button>
                </div>
              </Group>}
            </>}
          </>}
          {tab === "analysis" && <>
            <Group name="Measure">
              <Tb on={tool === "measure-line"} onClick={() => chooseTool("measure-line")} icon={<Ruler />} label="Distance" />
              <Tb on={tool === "measure-area"} onClick={() => chooseTool("measure-area")} icon={<LandPlot />} label="Area" />
            </Group>
            <Group name="Geoprocessing">
              <Tb on={gp.open && !gp.start && rightOpen} onClick={() => (gp.open && !gp.start ? setGp({ open: false, start: null, query: "" }) : openTool(null))} icon={<Wrench />} label="All tools" />
              <Tb onClick={() => openTool("buffer")} icon={<CircleDot />} label="Buffer" />
              <Tb onClick={() => openTool("clip")} icon={<Scissors />} label="Clip" />
              <Tb onClick={() => openTool("intersect")} icon={<Combine />} label="Intersect" />
              <Tb onClick={() => openTool("dissolve")} icon={<Merge />} label="Dissolve" />
              <Tb onClick={() => openTool("spatial_join")} icon={<Link2 />} label="Spatial join" />
            </Group>
            <Group name="Satellite">
              <Tb onClick={() => { setTab("earth"); openEarth(null); }} icon={<Earth />} label="Poly Earth" />
            </Group>
            {me.features.includes("raster") && <Group name="Raster">
              <Tb onClick={() => openTool("hillshade")} icon={<Mountain />} label="Hillshade" />
              <Tb onClick={() => openTool("slope")} icon={<TrendingUp />} label="Slope" />
              <Tb onClick={() => openTool("contours")} icon={<Waves />} label="Contours" />
              <Tb onClick={() => openTool("watershed")} icon={<Droplets />} label="Watershed" />
              <Tb onClick={() => openTool("zonal_statistics")} icon={<Grid3x3 />} label="Zonal statistics" />
            </Group>}
          </>}
          {tab === "earth" && <>
            <Group name="Poly Earth">
              <Tb on={earth.open && !earth.start && rightOpen} onClick={() => (earth.open && !earth.start ? setEarth({ open: false, start: null }) : openEarth(null))} icon={<Earth />} label="All tools" />
            </Group>
            <Group name="Images">
              <Tb onClick={() => openEarth("composite")} icon={<Layers3 />} label="Composite" />
              <Tb onClick={() => openEarth("index")} icon={<Leaf />} label="NDVI and indices" />
              <Tb onClick={() => openEarth("surface_temperature")} icon={<Thermometer />} label="Temperature" />
              <Tb onClick={() => openEarth("rainfall")} icon={<CloudSun />} label="Rainfall" />
              <Tb onClick={() => openEarth("terrain")} icon={<Mountain />} label="Terrain" />
            </Group>
            <Group name="Over time">
              <Tb onClick={() => openEarth("time_series")} icon={<LineChart />} label="Time series" />
              <Tb onClick={() => openEarth("trend")} icon={<TrendingUp />} label="Trend" />
              <Tb onClick={() => openEarth("anomaly")} icon={<BarChart3 />} label="Anomaly" />
            </Group>
            <Group name="Change">
              <Tb onClick={() => openEarth("change")} icon={<Sprout />} label="Before / after" />
              <Tb onClick={() => openEarth("flood")} icon={<Waves />} label="Flood" />
              <Tb onClick={() => openEarth("burn_severity")} icon={<Flame />} label="Burn severity" />
              <Tb onClick={() => openEarth("water_change")} icon={<Droplets />} label="Water" />
            </Group>
            <Group name="Classify">
              <Tb onClick={() => openEarth("classify")} icon={<MapIcon />} label="Classify" />
              <Tb onClick={() => openEarth("land_cover")} icon={<Earth />} label="WorldCover" />
              <Tb onClick={() => openEarth("zonal_index")} icon={<Table2 />} label="By polygon" />
            </Group>
          </>}
        </div>
      </div>

      <div className={`ws-body${leftOpen ? "" : " no-left"}${rightOpen ? "" : " no-right"}`}>
        {leftOpen ? (
          <Contents projectName={project.name} layers={layers} activeId={activeId} editingId={editId} canEdit={canEdit} canExport={me.features.includes("export")}
            selection={selection} onActivate={setActiveId} onToggle={(l) => void toggle(l)}
            onReorder={(ids) => void reorder(ids)} onZoom={(l) => l.bbox && zoomTo(l.bbox)} onTable={(l) => setTableId(l.id)} onRemove={(l) => void remove(l)}
            onPanel={openPanel} onSelection={(l, op) => void selectionOp(l, op)} onLabels={(l, on) => void labelsOn(l, on)} onRename={(l, n) => void renameLayer(l, n)}
            onPaste={(t, f) => void pasteProperties(t, f)} onSourceResolution={sourceResolution}
            onExport={(l) => { setActiveId(l.id); if (l.geometry_type !== "Raster") setDialog("export"); else window.location.assign(rasterDownloadUrl(l.id)); }}
            onEdit={(l) => startEditing(l.id)} onAdd={() => setDialog("add")} onCollapse={() => setLeftOpen(false)} onRefresh={(l) => void refreshLinked(l)} />
        ) : (
          <div className="ws-rail is-left"><IconButton label="Show Contents" onClick={() => setLeftOpen(true)}><PanelLeftOpen size={18} /></IconButton>
            <IconButton label="Add data" onClick={() => setDialog("add")} disabled={!canEdit}><FilePlus2 size={18} /></IconButton></div>
        )}
        {leftOpen && <Resizer axis="x" value={leftW} min={200} max={480} onChange={setLeftW} label="Resize Contents" className="rs-left" />}
        <div className="ws-map-wrap">
          <div ref={mapEl} className={`ws-map tool-${tool}`} />
          {tool !== "explore" && hint[tool] && <div className="ws-hintbar" role="status"><span>{hint[tool]}</span>
            <button type="button" onClick={() => (tool === "create" || tool === "pick-reshape" || tool === "reshape" ? chooseTool("explore") : chooseTool("explore"))}>Done <span className="kbd" style={{ background: "transparent", color: "#cfd6e4", borderColor: "rgba(255,255,255,.3)" }}>Esc</span></button></div>}
        </div>
        {rightOpen && <Resizer axis="x" value={rightW} min={260} max={560} onChange={setRightW} invert label="Resize the side pane" className="rs-right" />}
        {rightOpen ? <div className="ws-right">{right}<IconButton className="ws-collapse" label="Hide the side pane" onClick={() => setRightOpen(false)}><PanelRightClose size={16} /></IconButton></div> : (
          <div className="ws-rail is-right"><IconButton label="Show the side pane" onClick={() => setRightOpen(true)}><PanelRightOpen size={18} /></IconButton>
            <IconButton label="Geoprocessing tools" onClick={() => openTool(null)}><Wrench size={18} /></IconButton></div>
        )}
        {(leftOpen || rightOpen) && <div className="ws-scrim" onClick={() => { setLeftOpen(false); setRightOpen(false); }} aria-hidden="true" />}
      </div>

      {tableLayer && <AttributeTable layer={tableLayer} selected={sel(tableLayer.id)} editing={editId === tableLayer.id}
        resizer={<Resizer axis="y" value={tableH} min={140} max={640} invert onChange={setTableH} label="Resize the table" />}
        onSelect={(ids, add) => setSelection((s) => ({ ...s, [tableLayer.id]: add ? Array.from(new Set([...(s[tableLayer.id] ?? []), ...ids])) : ids }))}
        onZoomTo={(fid) => void zoomToFeature(tableLayer, fid)} onClose={() => setTableId(null)} onEdited={() => void refreshLayer(tableLayer.id)} />}

      <footer className="ws-status" aria-label="Status">
        <span className="ws-coords" title="Pointer position">{pointerText || "Point at the map for coordinates"}</span>
        <button type="button" className="ws-crs" onClick={() => setDialog("crs")} title="Change the coordinate system">{crs ? crs.name : `EPSG:${project.display_epsg}`}</button>
        <span title="Map scale at the centre">1:{niceScale(scale)}</span>
        <span className="ws-zoom">Zoom {zoom.toFixed(1)}</span>
        {totalSel > 0 && <button type="button" className="ws-sel" onClick={() => setSelection({})} title="Clear the selection">{totalSel.toLocaleString("en-GB")} selected <X size={12} aria-hidden="true" /></button>}
        {measure && <span className="ws-measure">{measure}</span>}
        <span className="ge-spacer" />
        {!canEdit && <span>{me.can_write ? "Viewer: read-only" : "Read-only licence"}</span>}
      </footer>

      {dialog === "add" && <AddData projectId={id} onClose={() => setDialog(null)} onImagery={() => setDialog("imagery")} onDone={(ls) => {
        setLayers((cur) => [...cur, ...ls]); setActiveId(ls[0].id); setDialog(null); setLeftOpen(true);
        const dropped = ls.reduce((n, l) => n + (l.source.dropped ?? 0), 0);
        toast(`Added ${ls.map((l) => l.name).join(", ")}.${dropped ? ` ${dropped} feature(s) had no usable shape and were left out.` : ""}`, "success");
        if (ls[0].bbox) zoomTo(ls[0].bbox);
      }} />}
      {dialog === "imagery" && <ImageryDialog projectId={id} viewBox={mapBox()} layerBox={active?.bbox ?? null} layerName={active?.name} onClose={() => setDialog(null)}
        onDone={(ls) => { setLayers((cur) => [...cur, ...ls]); setActiveId(ls[ls.length - 1].id); setDialog(null); setLeftOpen(true); toast(ls.length === 1 ? `${ls[0].name} added to the map.` : `${ls.length} layers added to the map.`, "success"); if (ls[0].bbox) zoomTo(ls[0].bbox); }} />}
      {dialog === "new" && <NewLayer projectId={id} onClose={() => setDialog(null)} onDone={(l) => { setLayers((cur) => [...cur, l]); setDialog(null); startEditing(l.id, l); }} />}
      {dialog === "attr" && active && <SelectByAttributes layer={active} onClose={() => setDialog(null)} onDone={(s, add) => applySelection(active.id, s, add)} />}
      {dialog === "loc" && active && <SelectByLocation layer={active} layers={layers} selection={selection} onClose={() => setDialog(null)} onDone={(s, add) => applySelection(active.id, s, add)} />}
      {dialog === "export" && active && <Export layer={active} selected={sel(active.id)} epsg={project.display_epsg} onClose={() => setDialog(null)} />}
      {layoutOpen && map.current && <LayoutView project={project} layers={layers} crs={crs} me={me} map={map.current} canSave={canEdit && layoutsAllowed} onClose={() => setLayoutOpen(false)} />}
      {dialog === "plan" && <Dialog title="Print layouts" onClose={() => setDialog(null)}>
        <div className="ws-form"><p>Print layouts (map sheets with a title, legend, scale bar, north arrow and grid, exported as PDF) are in PolyGIS Advanced.
          Ask GeoEstate NIG Limited to move your organization to Advanced.</p>
          <div className="ws-row"><Button variant="primary" onClick={() => setDialog(null)}>OK</Button></div></div>
      </Dialog>}
      {dialog === "linked" && <LinkedDialog projectId={id} onClose={() => setDialog(null)} onDone={(l) => {
        setLayers((cur) => [...cur, l]); setActiveId(l.id); setDialog(null); setLeftOpen(true);
        toast(`${l.name} added from ${l.source.linked?.app_name}: ${l.feature_count.toLocaleString("en-GB")} features.`, "success"); if (l.bbox) zoomTo(l.bbox);
      }} />}
      {dialog === "publish" && active && <PublishDialog layer={active} selected={sel(active.id)} onClose={() => setDialog(null)} />}
      {dialog === "share" && <ShareDialog project={project} layers={layers} canEdit={canEdit} onClose={() => setDialog(null)}
        view={() => { const m = map.current; const c = m?.getCenter(); return c && m ? { center: [c.lng, c.lat], zoom: m.getZoom(), bearing: m.getBearing(), basemap } : project.view; }} />}
      {dialog === "crs" && <CrsDialog project={project} canEdit={canEdit} onClose={() => setDialog(null)} onDone={async (p) => { setProject(p); setCrs(await loadCrs(p.display_epsg)); setDialog(null); }} />}
    </div>
  );
}

/** Whether the window is narrower than `px` (a tablet or phone). */
function useNarrow(px: number) {
  const q = `(max-width: ${px}px)`;
  const [n, setN] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const f = () => setN(m.matches);
    m.addEventListener("change", f);
    return () => m.removeEventListener("change", f);
  }, [q]);
  return n;
}

/** What each basemap looks like, for its button. */
const BASEMAP_LOOK: Record<string, string> = {
  streets: "linear-gradient(135deg, #f2efe6 0 40%, #fbd38d 40% 44%, #f2efe6 44% 70%, #b9dcf0 70%)",
  light: "linear-gradient(135deg, #f4f4f2 0 55%, #dcdcdc 55% 58%, #f4f4f2 58%)",
  satellite: "linear-gradient(135deg, #4e6b3a, #7a8f55 45%, #a99a6b 70%, #556b45)",
  imagery: "linear-gradient(135deg, #3d5a2e, #8a8a5c 60%, #6b7d4a)",
  none: "#f4f6fa",
};
const shortCrs = (name: string) => (name.length > 22 ? `${name.slice(0, 20)}…` : name);
/** A scale denominator rounded the way maps print it. */
function niceScale(s: number) {
  if (!isFinite(s) || s <= 0) return "–";
  const p = 10 ** Math.max(0, Math.floor(Math.log10(s)) - 1);
  return (Math.round(s / p) * p).toLocaleString("en-GB");
}

function Group({ name, children }: { name: string; children: React.ReactNode }) {
  return <div className="ws-group" role="group" aria-label={name}><div className="ws-group-body">{children}</div><div className="ws-group-name">{name}</div></div>;
}
function Tb({ icon, label, onClick, on, disabled }: { icon: ReactNode; label: string; onClick: () => void; on?: boolean; disabled?: boolean }) {
  return <button type="button" className={`ws-tb${on ? " is-on" : ""}`} onClick={onClick} disabled={disabled} aria-pressed={on}><span aria-hidden="true" className="ws-tb-icon">{icon}</span>{label}</button>;
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
