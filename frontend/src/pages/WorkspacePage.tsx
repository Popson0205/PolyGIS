import maplibregl, { type Map as MapLibre, type MapGeoJSONFeature } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
  ArrowLeftRight, Box, BarChart3, ChevronDown, CloudFog, FlaskConical, CloudOff, CloudSun, Contrast, Earth, Focus, Gauge, Haze, MapPinned, PaintBucket, Palette, Sparkles, SunMedium, Flame, Layers3, Leaf, LineChart, Map as MapIcon, Sprout, Table2, Thermometer, ChevronRight, ChevronUp, CircleDot, CircleStop, Combine, Download, Droplets, Eraser, FilePlus2, Globe2, Grid3x3,
  LandPlot, Lasso, Layers, Link2, ListFilter, LocateFixed, Magnet, Maximize, Merge, Mountain, MousePointerClick, PanelLeftOpen,
  PanelRightClose, PanelRightOpen, Printer, FolderTree, Crosshair, Grid2x2, ListPlus, Shield, Compass, Radar, MoveUpRight, Route, Orbit, Eye, ScanEye, Sun, MountainSnow, Shapes, ClipboardList, Activity, Target, Radio, Play, BellRing, Satellite, Ruler, UploadCloud, Scissors, SlidersHorizontal, Spline, SquarePen, Trash2, TrendingUp, Undo2, Waves, Workflow, Wrench, ZoomIn, Hand, Maximize2, Scan, Video, Minimize2, Moon, Database, PenLine, Image as ImageIcon, TerminalSquare, HardDriveDownload, FolderInput,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import AttributeTable from "../components/AttributeTable";
import Contents, { type PanelTab, type SelectionOp } from "../components/Contents";
import { ImageryDialog } from "../components/Imagery";
import { AddData, CrsPicker, Dialog, Export, NewLayer, SelectByAttributes, SelectByLocation } from "../components/Dialogs";
import Geoprocessing from "../components/Geoprocessing";
import { AskBar } from "../components/AskBar";
import { ToolCards } from "../components/ToolCards";
import Notebook from "../components/Notebook";
import { WhatsNew } from "../components/WhatsNew";
import { CameraWall, CamerasPane, useCameras } from "../components/Cameras";
import { FocusLayers, Inspector, SelectionBar } from "../components/Inspector";
import type { AskHit, Command } from "../lib/ask";
import type { Tool as GpTool } from "../lib/types";
import PolyEarth, { useEarthJobs } from "../components/PolyEarth";
import LayerPanel from "../components/LayerPanel";
import LayoutView from "../components/LayoutView";
import Catalog, { ViewTabs, isNewLayout, viewKey, type ViewTab } from "../components/Catalog";
import CoordinatePane, { SightPane } from "../components/Military";
import { SymbolsPane } from "../components/MilSymbols";
import BriefPane from "../components/AreaBrief";
import { TrackingPane, useLiveTracking } from "../components/Tracking";
import ProvidersPane from "../components/Providers";
import Geodatabases from "../components/Geodatabases";
import type { OrgImagery, TrackAlert } from "../lib/types";
import { NOTATIONS, NOTATION_NAMES, writeCoordinate, type Notation } from "../lib/milcoords";
import { bearingText } from "../lib/geodesy";
import Georeference from "../components/Georeference";
import ModelBuilder from "../components/ModelBuilder";
import Scenarios from "../components/Scenarios";
import { Group, Tb, Ts, RibbonBody } from "../components/Ribbon";
import ShareDialog from "../components/ShareDialog";
import PublishDialog from "../components/PublishDialog";
import LinkedDialog from "../components/LinkedDialog";
import { Apps, UserMenu, licenceNotice, useApps } from "../components/Bar";
import { Button, ErrorText, IconButton, Resizer, useStored, useToast } from "../components/ui";
import { getHandle, openProjectFile, saveProjectFile, supportsFsAccess } from "../lib/projectFile";
import RasterPanel from "../components/RasterPanel";
import { ApiError, BASE, api, type SavedLayout, rasterDownloadUrl, rasterTileUrl, terrainTileUrl, tileUrl } from "../lib/api";
import { formatCoords, loadCrs, metres, squareMetres } from "../lib/coords";
import { Editor } from "../lib/editor";
import { allBasemaps, setProviderBasemaps, GLOBAL_TERRAIN, GLYPHS, allIds, basemapStyle, ids as layerIds, labelSrc, mapLayers, src } from "../lib/mapstyle";
import type { CrsDef, GisMap, Layer, Me, Project, Selection, VectorType, View } from "../lib/types";
import { attachMilSymbols } from "../lib/milsym";
import { brandLockup } from "../lib/identity";

type Tab = "map" | "insert" | "select" | "edit" | "analysis" | "imagery" | "military" | "earth" | "share";
type Tool = "explore" | "select" | "select-area" | "draw-area" | "measure-line" | "measure-area" | "measure-bearing" | "create" | "reshape" | "pick-reshape";
type DialogName = "add" | "imagery" | "new" | "attr" | "loc" | "export" | "crs" | "plan" | "share" | "publish" | "linked" | null;
const DRAW = { Point: "point", LineString: "linestring", Polygon: "polygon" } as const;

/** A project open in the workspace: modes down the left, the mode's tools in one row, layers, map, inspector, table, status. */
export default function WorkspacePage({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const { id = "" } = useParams();
  const toast = useToast();
  const canEdit = me.role !== "viewer" && me.can_write;
  const layoutsAllowed = me.features.includes("layouts");
  const [project, setProject] = useState<Project | null>(null);
  // every layer of the project; `layers` are those of the map that is open
  const [allLayers, setLayers] = useState<Layer[]>([]);
  const [maps, setMaps] = useState<GisMap[]>([]);
  const [mapId, setMapId] = useState<string | null>(null);
  const layers = useMemo(() => (mapId ? allLayers.filter((l) => !l.map_id || l.map_id === mapId) : allLayers), [allLayers, mapId]);
  const openMapDoc = maps.find((m) => m.id === mapId) ?? null;
  // the open views, in tabs: maps and print sheets
  const [views, setViews] = useState<ViewTab[]>([]);
  const [viewNow, setViewNow] = useState("");
  const [savedLayouts, setSavedLayouts] = useState<SavedLayout[]>([]);
  const [dirtyLayouts, setDirtyLayouts] = useState<Record<string, boolean>>({});
  const layoutKeys = useRef<Record<string, string>>({});     // a saved layout's tab keeps the key it had before it was saved
  const [catalogOpen, setCatalogOpen] = useState(false);
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
  const [gp, setGp] = useState<{ open: boolean; start: string | null; query: string; preset?: Record<string, unknown> }>({ open: false, start: null, query: "" });
  const [coordsOpen, setCoordsOpen] = useState(false);
  const [sightOpen, setSightOpen] = useState(false);
  const [symbolsOpen, setSymbolsOpen] = useState(false);
  const [briefOpen, setBriefOpen] = useState(false);
  const [trackingOpen, setTrackingOpen] = useState(false);
  const [camerasOpen, setCamerasOpen] = useState(false);
  const [watching, setWatching] = useState<string[]>([]);
  const [providersOpen, setProvidersOpen] = useState(false);
  const [gdbOpen, setGdbOpen] = useState(false);
  const [providerTick, setProviderTick] = useState(0);
  const providerApplied = useRef(false);
  const [liveOn, setLiveOn] = useStored<boolean>(`tracking-live-${id}`, false);
  const [alerts, setAlerts] = useState<TrackAlert[]>([]);
  const alertIds = useRef<Set<number>>(new Set());
  const [placing, setPlacing] = useState(false);
  const placeProps = useRef<Record<string, unknown> | null>(null);
  const areaWait = useRef<((g: GeoJSON.Geometry | null) => void) | null>(null);     // attributes each placed symbol gets
  const [coordFmt, setCoordFmt] = useStored<"crs" | Notation>("coord-format", "crs");
  const [earth, setEarth] = useState<{ open: boolean; start: string | null }>({ open: false, start: null });
  const [panelTab, setPanelTab] = useState<{ tab: PanelTab; n: number }>({ tab: "symbology", n: 0 });
  const [modelsOpen, setModelsOpen] = useState(false);
  const [georefOpen, setGeorefOpen] = useState(false);
  const [scenariosOpen, setScenariosOpen] = useState(false);
  const [notebookOpen, setNotebookOpen] = useState(false);
  const [georefFile, setGeorefFile] = useState<File | null>(null);
  const [georefLayer, setGeorefLayer] = useState<string | null>(null);
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
  // Focus: the map only (modes in a dock, tools and layers float); and the dark Operations theme, for the whole app
  const [focus, setFocus] = useStored("focus", false);
  const [theme, setTheme] = useStored<"light" | "ops">("theme", "light");
  useEffect(() => {
    if (theme === "ops") document.documentElement.dataset.theme = "ops"; else delete document.documentElement.dataset.theme;
    // "No basemap" is a plain background: dark in the Operations theme (the map is made after the project loads: wait a tick)
    let m: maplibregl.Map | null = null;
    const paint = () => { if (m?.getLayer("background")) m.setPaintProperty("background", "background-color", theme === "ops" ? "#10151f" : "#f4f6fa"); };
    const t = window.setTimeout(() => { m = map.current; paint(); m?.on("styledata", paint); }, 0);
    return () => { window.clearTimeout(t); m?.off("styledata", paint); };
  }, [theme, project?.id]);
  // on a wide screen the panes stay docked (and remembered); on a tablet or phone one at a time slides over the map
  const narrow = useNarrow(1100);
  const [leftDocked, setLeftDocked] = useStored("left-open", true);
  const [rightDocked, setRightDocked] = useStored("right-open", true);
  const [drawer, setDrawer] = useState<"left" | "right" | null>(null);
  const leftOpen = narrow ? drawer === "left" : leftDocked;
  const rightOpen = narrow ? drawer === "right" : rightDocked;
  const setLeftOpen = (v: boolean) => (narrow ? setDrawer(v ? "left" : drawer === "left" ? null : drawer) : setLeftDocked(v));
  const setRightOpen = (v: boolean) => (narrow ? setDrawer(v ? "right" : drawer === "right" ? null : drawer) : setRightDocked(v));
  // in Focus the side pane is closed until there is something to show: a clicked feature opens it (floating)
  useEffect(() => { if (focus && identify) setRightOpen(true); }, [identify]);   // eslint-disable-line react-hooks/exhaustive-deps
  const [leftW, setLeftW] = useStored("left-width", 272);
  const [rightW, setRightW] = useStored("right-width", 320);
  const [tableH, setTableH] = useStored("table-height", 260);
  const [scale, setScale] = useState(0);
  const commandEl = useRef<HTMLInputElement>(null);
  const apps = useApps();
  const mapIdRef = useRef<string | null>(null);

  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibre | null>(null);
  // the style is in place (MapLibre's isStyleLoaded() also waits for every source's tiles, so one slow or failing tile
  // server, such as the terrain, would stop the map from following changes)
  const styleReady = useRef(false);
  const tilt = useRef<boolean | null>(null);      // 3D turned on or off: tilt the camera once the terrain is in place
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
      const ms = p.maps ?? [];
      const am = ms.find((m) => m.id === p.active_map_id) ?? ms[0];
      const view: View = am?.view?.center ? am.view : p.view;
      setProject((cur) => cur ?? { ...p, view }); setLayers(p.layers ?? []); setMaps(ms);
      const mid = mapIdRef.current && ms.some((m) => m.id === mapIdRef.current) ? mapIdRef.current : am?.id ?? null;
      setMapId(mid);
      if (!mapIdRef.current) setBasemap(view.basemap ?? "streets");
      mapIdRef.current = mid;
      const mine = (p.layers ?? []).filter((l) => !mid || !l.map_id || l.map_id === mid);
      setActiveId((a) => a ?? (mine.length ? [...mine].sort((x, y) => y.position - x.position)[0].id : null));
      if (mid) setViews((vs) => {
        if (vs.length) return vs;
        let saved: { views: ViewTab[]; now: string } | null = null;
        try { saved = JSON.parse(localStorage.getItem(`polygis-views-${id}`) ?? "null"); } catch { /* none kept */ }
        const ok = (saved?.views ?? []).filter((v) => (v.kind === "map" ? ms.some((m) => m.id === v.id) : !isNewLayout(v.id)));
        const list = ok.some((v) => v.kind === "map" && v.id === mid) ? ok : [{ kind: "map", id: mid } as ViewTab, ...ok];
        const now = saved?.now && list.some((v) => viewKey(v) === saved!.now) && saved.now.startsWith("layout:") ? saved.now : `map:${mid}`;
        setViewNow(now);
        return list;
      });
      api.layouts(id).then(setSavedLayouts, () => undefined);
      setCrs(await loadCrs(p.display_epsg).catch(() => undefined));
    } catch (e) { setError(e); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);
  const providersLoaded = (list: OrgImagery[]) => {
    setProviderBasemaps(list.filter((p) => p.enabled && p.tiles).map((p) => ({ id: p.id, name: p.name, tiles: `${BASE}${p.tiles}`,
      attribution: p.config.attribution, maxzoom: p.config.max_zoom })));
    setProviderTick((n) => n + 1);
  };
  useEffect(() => { api.orgImagery().then(providersLoaded, () => undefined); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  // a provider's basemap saved with the map: drawn once the providers are known (the map may have opened first)
  useEffect(() => {
    if (providerApplied.current || !providerTick || !map.current || !basemap.startsWith("provider:")) return;
    providerApplied.current = true;
    switchBasemap(basemap, false);
  }, [providerTick, basemap, project]);   // eslint-disable-line react-hooks/exhaustive-deps
  // the live picture of tracked devices, and a word when a geofence is crossed
  useLiveTracking(liveOn && project ? map.current : null, id, liveOn, (a) => {
    const fresh = a.filter((x) => !alertIds.current.has(x.id));
    for (const x of fresh) alertIds.current.add(x.id);
    if (fresh.length && alertIds.current.size > fresh.length) toast(`${fresh[0].name || fresh[0].device} ${fresh[0].kind === "enter" ? "entered" : "left"} ${fresh[0].zone}${fresh.length > 1 ? ` (and ${fresh.length - 1} more)` : ""}.`, "info");
    setAlerts(a);
  });
  useEffect(() => {
    if (!views.length) return;
    try { localStorage.setItem(`polygis-views-${id}`, JSON.stringify({ views: views.filter((v) => v.kind === "map" || !isNewLayout(v.id)), now: viewNow })); } catch { /* not kept */ }
  }, [views, viewNow, id]);
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
    attachMilSymbols(m);
    m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric", maxWidth: 120 }), "bottom-left");
    m.on("style.load", () => {
      styleReady.current = true;
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
    return () => { editor.current?.stop(); m.remove(); map.current = null; styleReady.current = false; };
  }, [project?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const basemapRef = useRef(basemap);
  basemapRef.current = basemap;

  /** Put every layer on the map, bottom to top, below the drawing layers; sources are only rebuilt when a layer's
   *  data changed (its version). */
  const sync = useCallback(() => {
    const m = map.current;
    if (!m || !styleReady.current) return;
    const { layers: ls, selection: s, reshaping: r, editId: e } = state.current;
    const before = m.getStyle().layers.find((x) => x.id.startsWith("gis-draw"))?.id;
    const wanted = new Set(ls.map((l) => l.id));
    for (const [lid] of versions.current) {
      if (!wanted.has(lid)) {
        for (const x of m.getStyle().layers.filter((y) => y.id.startsWith(`lyr-${lid}`))) m.removeLayer(x.id);
        for (const sid of [`src-${lid}`, `src-${lid}-labels`]) if (m.getSource(sid)) m.removeSource(sid);
        versions.current.delete(lid);
      }
    }
    for (const l of [...ls].sort((a, b) => a.position - b.position)) {
      for (const x of allIds(l)) if (m.getLayer(x)) m.removeLayer(x);
      if (l.geometry_type === "Raster" && l.raster?.missing) continue;     // its file is gone: asking for tiles would only fail
      if (m.getSource(src(l)) && versions.current.get(l.id) !== l.version) for (const sid of [src(l), labelSrc(l)]) if (m.getSource(sid)) m.removeSource(sid);
      if (!m.getSource(src(l))) {
        if (l.geometry_type === "Raster") m.addSource(src(l), { type: "raster", tiles: [rasterTileUrl(l)], tileSize: 256, minzoom: 0, maxzoom: 22, bounds: l.bbox ?? undefined });
        else m.addSource(src(l), { type: "vector", tiles: [tileUrl(l)], minzoom: 0, maxzoom: 16, promoteId: undefined });
        versions.current.set(l.id, l.version);
      }
      if (l.geometry_type !== "Raster" && l.labels && !m.getSource(labelSrc(l))) m.addSource(labelSrc(l), { type: "vector", tiles: [tileUrl(l)], minzoom: 0, maxzoom: 16 });
      if (!l.labels && m.getSource(labelSrc(l))) m.removeSource(labelSrc(l));
      for (const spec of mapLayers(l, s[l.id] ?? [], l.id === e && r != null ? [r] : [], !state.current.view3d.on)) m.addLayer(spec, before);
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
    const ease = () => {
      if (tilt.current === null) return;
      const on = tilt.current;
      tilt.current = null;
      m.easeTo({ pitch: on ? 60 : 0, bearing: on ? (m.getBearing() || -20) : 0, duration: 900 });
    };
    if (have && have !== want) {
      m.setTerrain(null);
      if (m.getSource(have)) m.removeSource(have);
      m.fire("terrain");                       // the credits refresh on this, now the terrain source has gone
    }
    if (!want) { ease(); return; }
    if (!m.getSource(want)) {
      m.addSource(want, dem ? { type: "raster-dem", tiles: [terrainTileUrl(dem)], tileSize: 256, encoding: "terrarium", bounds: dem.bbox ?? undefined, maxzoom: 16 }
        : { type: "raster-dem", tiles: GLOBAL_TERRAIN.tiles, tileSize: 256, encoding: "terrarium", maxzoom: GLOBAL_TERRAIN.maxzoom, attribution: GLOBAL_TERRAIN.attribution });
    }
    m.setTerrain({ source: want, exaggeration: v.exaggeration });
    const sky = (m as unknown as { setSky?: (s: Record<string, unknown>) => void }).setSky;
    sky?.call(m, { "sky-color": "#a9c8ec", "horizon-color": "#e8eef6", "fog-color": "#eef2f7", "sky-horizon-blend": 0.6, "horizon-fog-blend": 0.5, "fog-ground-blend": 0.85 });
    ease();
  }
  function toggle3d(on: boolean) {
    setView3d({ ...view3d, on });
    tilt.current = on;                       // applyTerrain() eases the camera after the terrain changes
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
    else m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, maxZoom: 20, duration: 600 });
  }
  async function zoomToFeature(l: Layer, fid: number) {
    const f = await api.feature(l.id, fid);
    const pts = JSON.stringify(f.geometry).match(/-?\d+\.?\d*(e-?\d+)?,-?\d+\.?\d*(e-?\d+)?/g) ?? [];
    const xy = pts.map((p) => p.split(",").map(Number));
    if (!xy.length) return;
    zoomTo([Math.min(...xy.map((p) => p[0])), Math.min(...xy.map((p) => p[1])), Math.max(...xy.map((p) => p[0])), Math.max(...xy.map((p) => p[1]))]);
  }

  async function switchSelection(of: Layer | null = active) {
    if (!of) return;
    const all = await api.select(of.id, { filters: [] });
    const now = new Set(sel(of.id));
    const ids = all.ids.filter((x) => !now.has(x));
    setSelection((cur) => ({ ...cur, [of.id]: ids }));
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
    if (areaWait.current) { const r = areaWait.current; areaWait.current = null; r(null); }     // an area being drawn: given up
    editor.current?.reset();
    setMeasure(""); setReshaping(null);
    setTool(t);
    if (t === "measure-line" || t === "measure-area") {
      editor.current?.measureWith(t === "measure-line" ? "linestring" : "polygon", (m) =>
        setMeasure([m.area != null ? `Area ${squareMetres(m.area)}` : "", m.length != null ? `${m.area != null ? "Perimeter" : "Length"} ${metres(m.length)}` : ""].filter(Boolean).join(" · ")));
    }
    if (t === "measure-bearing") editor.current?.measureWith("linestring", (m) => setMeasure(m.points ? bearingText(m.points) : ""));
    if (t === "select-area") {
      const target = layers.find((l) => l.id === activeId);
      if (!target) { toast("Choose a layer in Layers first.", "info"); setTool("explore"); return; }
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
      const preset = placeProps.current;
      void api.addFeature(l.id, g, preset ?? {}).then(async (f) => {
        const fid = Number(f.id);
        const fresh = await refreshLayer(l.id);
        if (fresh) setSelection((s) => ({ ...s, [l.id]: [fid] }));
        if (preset) return;                                    // a symbol placed from the Symbols pane: on to the next one
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
    placeProps.current = null; setPlacing(false);
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
        if (!fieldOk(from.style.field)) { toast(`${target.name} has no field ${from.style.field}, which ${from.name}'s style uses.`, "error"); return; }
        const lbFields = from.labels ? [from.labels.field, from.labels.rotation_field, ...Array.from((from.labels.expression ?? "").matchAll(/\{([^{}]+)\}/g), (m) => m[1].trim())].filter(Boolean) as string[] : [];
        replaceLayer(await api.updateLayer(target.id, { style: from.style, ...(from.labels && lbFields.every(fieldOk) ? { labels: from.labels } : {}) }));
      }
      toast(`${from.name}'s style pasted onto ${target.name}.`, "success");
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
    if (l.data_id == null && allLayers.some((x) => x.data_id === l.id)) { const p = await api.project(id); setLayers(p.layers ?? []); }   // its data was handed on
  }
  function switchBasemap(b: string, save = true) {
    if (editId && save) { toast("Stop editing before changing the basemap.", "info"); return; }
    setBasemap(b);
    editor.current?.stop();
    versions.current.clear();
    styleReady.current = false;
    map.current?.setStyle(basemapStyle(b) as maplibregl.StyleSpecification | string);
    if (canEdit && save) void api.updateProject(id, { view: { ...(openMapDoc?.view ?? project?.view ?? {}), basemap: b } }).catch(() => undefined);
    if (save) setMaps((ms) => ms.map((m) => (m.id === mapId ? { ...m, view: { ...m.view, basemap: b } } : m)));
  }

  // ---------- maps and layouts, in tabs ----------
  const camera = (): View | null => {
    const m = map.current;
    if (!m) return null;
    const c = m.getCenter();
    return { center: [c.lng, c.lat], zoom: m.getZoom(), bearing: m.getBearing(), basemap };
  };
  const addView = (v: ViewTab) => setViews((vs) => (vs.some((x) => viewKey(x) === viewKey(v)) ? vs : [...vs, v]));
  async function openMap(target: GisMap) {
    addView({ kind: "map", id: target.id });
    setViewNow(`map:${target.id}`);
    requestAnimationFrame(() => map.current?.resize());
    if (target.id === mapId) return;
    if (editId) stopEditing();
    const here = camera();
    if (here && mapId) {                                    // where the map that is closing was left
      setMaps((ms) => ms.map((m) => (m.id === mapId ? { ...m, view: here } : m)));
      if (canEdit) void api.updateMap(mapId, { view: here }).catch(() => undefined);
    }
    setSelection({}); setIdentify(null); setTableId(null); setNewFeature(null);
    mapIdRef.current = target.id;
    setMapId(target.id);
    await api.openMap(target.id).catch(() => undefined);   // what is added from now on goes into this map
    const v = maps.find((m) => m.id === target.id)?.view ?? target.view;
    const m = map.current;
    if (m && v.center) m.jumpTo({ center: v.center, zoom: v.zoom ?? m.getZoom(), bearing: v.bearing ?? 0 });
    else if (m && canEdit && here) void api.updateMap(target.id, { view: here }).catch(() => undefined);
    if ((v.basemap ?? "streets") !== basemap) switchBasemap(v.basemap ?? "streets", false);
    try {                                                    // the same data may have changed in the other map
      const p = await api.project(id);
      setLayers(p.layers ?? []); setMaps(p.maps ?? []);
      const mine = (p.layers ?? []).filter((l) => l.map_id === target.id).sort((a, b) => b.position - a.position);
      setActiveId(mine[0]?.id ?? null);
    } catch { /* kept as it was */ }
  }
  const nextName = (base: string, taken: string[]) => {
    if (!taken.includes(base)) return base;
    for (let n = 1; ; n++) if (!taken.includes(`${base}${n}`)) return `${base}${n}`;
  };
  async function newMap(copyOf?: GisMap) {
    try {
      const name = copyOf ? nextName(`${copyOf.name} copy`, maps.map((m) => m.name)) : nextName("Map", maps.map((m) => m.name));
      const m = await api.createMap(id, name, copyOf?.id);
      setMaps((ms) => [...ms, m]);
      if (copyOf) { const p = await api.project(id); setLayers(p.layers ?? []); }
      await openMap(m);
      toast(copyOf ? `${m.name}: a copy of ${copyOf.name}, its layers showing the same data.` : `${m.name} added. Add data to it, or add layers from another map.`, "success");
    } catch (e) { toast(e instanceof ApiError ? e.message : "Could not add the map.", "error"); }
  }
  async function renameMap(m: GisMap, name: string) {
    try { const u = await api.updateMap(m.id, { name }); setMaps((ms) => ms.map((x) => (x.id === m.id ? { ...x, name: u.name } : x))); }
    catch (e) { toast(e instanceof ApiError ? e.message : "Could not rename the map.", "error"); }
  }
  async function deleteMap(m: GisMap) {
    if (maps.length < 2) return;
    if (!window.confirm(`Delete the map ${m.name} and its ${m.layer_count === 1 ? "layer" : `${m.layer_count} layers`}? Data that other maps show stays in them. This can't be undone.`)) return;
    try {
      if (m.id === mapId) await openMap(maps.find((x) => x.id !== m.id)!);
      await api.deleteMap(m.id);
      setViews((vs) => vs.filter((v) => !(v.kind === "map" && v.id === m.id)));
      const p = await api.project(id);
      setLayers(p.layers ?? []); setMaps(p.maps ?? []);
      toast(`${m.name} deleted.`, "success");
    } catch (e) { toast(e instanceof ApiError ? e.message : "Could not delete the map.", "error"); }
  }
  function newLayout() {
    if (!layoutsAllowed) { setDialog("plan"); return; }
    let n = 1;
    while (views.some((v) => v.kind === "layout" && v.id === `new:${n}`)) n++;
    const v: ViewTab = { kind: "layout", id: `new:${n}` };
    addView(v); setViewNow(viewKey(v));
  }
  function openLayoutTab(l: SavedLayout) {
    if (!layoutsAllowed) { setDialog("plan"); return; }
    const v: ViewTab = { kind: "layout", id: l.id };
    addView(v); setViewNow(viewKey(v));
  }
  function closeView(v: ViewTab, ask = true) {
    const k = viewKey(v);
    if (ask && v.kind === "layout" && dirtyLayouts[v.id] && !window.confirm("Close the layout without saving it?")) return;
    const rest = views.filter((x) => viewKey(x) !== k);
    if (v.kind === "map" && !rest.some((x) => x.kind === "map")) return;     // a map stays open
    setViews(rest);
    setDirtyLayouts((d) => { const { [v.id]: _, ...o } = d; return o; });
    if (viewNow === k) {
      const i = views.findIndex((x) => viewKey(x) === k);
      const next = rest[Math.min(i, rest.length - 1)] ?? rest[0];
      if (next.kind === "map") { const m = maps.find((x) => x.id === next.id); if (m) void openMap(m); }
      else setViewNow(viewKey(next));
    }
    if (v.kind === "map" && v.id === mapId && viewNow !== k) {
      const m = maps.find((x) => rest.some((r) => r.kind === "map" && r.id === x.id));
      if (m) void openMap(m);
    }
  }
  function pickView(v: ViewTab) {
    if (v.kind === "map") { const m = maps.find((x) => x.id === v.id); if (m) void openMap(m); }
    else setViewNow(viewKey(v));
  }
  async function renameLayout(l: SavedLayout, name: string) {
    try { const u = await api.updateLayout(l.id, { name }); setSavedLayouts((ls) => ls.map((x) => (x.id === l.id ? u : x))); }
    catch (e) { toast(e instanceof ApiError ? e.message : "Could not rename the print sheet.", "error"); }
  }
  async function deleteLayout(l: SavedLayout) {
    if (!window.confirm(`Delete the print sheet ${l.name}? The maps and their layers are not touched.`)) return;
    try {
      await api.deleteLayout(l.id);
      setSavedLayouts((ls) => ls.filter((x) => x.id !== l.id));
      const v = views.find((x) => x.kind === "layout" && x.id === l.id);
      if (v) closeView(v, false);
    } catch (e) { toast(e instanceof ApiError ? e.message : "Could not delete the print sheet.", "error"); }
  }
  async function addToMap(l: Layer, target: string, how: "share" | "copy") {
    try {
      const made = await api.addToMap(l.id, target, how);
      setLayers((cur) => [...cur, made]);
      setMaps((ms) => ms.map((m) => (m.id === target ? { ...m, layer_count: m.layer_count + 1 } : m)));
      const name = maps.find((m) => m.id === target)?.name ?? "the map";
      toast(how === "share" ? `${l.name} added to ${name}, showing the same data: style it there as you like.` : `${l.name} copied to ${name}.`, "success");
    } catch (e) { toast(e instanceof ApiError ? e.message : "Could not add it to that map.", "error"); }
  }
  function zoomAll() {
    const bs = layers.map((l) => l.bbox).filter(Boolean) as [number, number, number, number][];
    if (bs.length) zoomTo([Math.min(...bs.map((b) => b[0])), Math.min(...bs.map((b) => b[1])), Math.max(...bs.map((b) => b[2])), Math.max(...bs.map((b) => b[3]))]);
  }

  const pointerText = useMemo(() => {
    if (!pointer) return "";
    const [lng, lat] = pointer.split(",").map(Number);
    if (coordFmt !== "crs") return writeCoordinate(coordFmt, lng, lat) ?? `${NOTATION_NAMES[coordFmt]} does not reach here`;
    return formatCoords(crs, lng, lat);
  }, [pointer, crs, coordFmt]);

  // live cameras: asked for while their pane is open or one is being watched; drawn on the map
  const [cams, setCams] = useCameras(id, map, camerasOpen || watching.length > 0, (cid) => { setWatching((w) => (w.includes(cid) ? w : [...w, cid].slice(-4))); });
  // Ask PolyGIS: the analysis tools it knows, and the buttons it found (by id) to press
  const [allTools, setAllTools] = useState<GpTool[]>([]);
  useEffect(() => {
    void Promise.all([api.tools(), me.features.includes("raster") ? api.rasterTools().catch(() => [] as GpTool[]) : Promise.resolve([] as GpTool[])])
      .then(([v, r]) => setAllTools([...v, ...r.map((t) => ({ ...t, kind: "raster" as const }))])).catch(() => undefined);
  }, [me.features]);
  const cmdEls = useRef(new Map<string, HTMLButtonElement>());

  if (error) return <div className="ge-main"><ErrorText error={error} /><Link to="/">All projects</Link></div>;
  if (!project) return <p className="loading">Opening the project…</p>;
  const editable = layers.filter((l) => canEdit && l.geometry_type !== "Raster" && !l.source.linked);
  const totalSel = Object.values(selection).reduce((n, x) => n + x.length, 0);
  const notice = licenceNotice(me.product);
  // ---------- Ask PolyGIS ----------
  /** Every mode's buttons, as they are on the page now (each mode's are there, hidden when it is not open). */
  const modeCommands = (): Command[] => {
    cmdEls.current.clear();
    const names = Object.fromEntries(tabs.map(([t, n]) => [t, n]));
    const out: Command[] = [];
    document.querySelectorAll<HTMLButtonElement>(".ws-mode-tools button:not(.ws-group-fold)").forEach((b, i) => {
      const label = (b.textContent ?? "").replace(/\s+/g, " ").trim();
      const mode = (b.closest("[data-mode]") as HTMLElement | null)?.dataset.mode ?? "";
      if (!label || !names[mode]) return;
      const id = `${mode}-${i}`;
      cmdEls.current.set(id, b);
      out.push({ id, label, mode, modeName: names[mode], disabled: b.disabled, title: b.title || undefined });
    });
    return out;
  };
  function doAsk(h: AskHit) {
    if (h.kind === "tool") { openTool(h.tool.id, "", h.preset); setTab(h.tool.kind === "raster" ? "imagery" : "analysis"); if (h.filled.length) toast(`${h.tool.name}: ${h.filled.join(", ")}. Check and run.`, "info"); }
    else if (h.kind === "layer") {
      const l = h.layer;
      if (h.action === "zoom") { setActiveId(l.id); if (l.bbox) zoomTo(l.bbox); }
      else if (h.action === "table") setTableId(l.id);
      else if (h.action === "style") openPanel(l, "symbology");
      else if ((h.action === "hide") === l.visible) void toggle(l);
    } else if (h.kind === "goto") {
      map.current?.flyTo({ center: [h.lng, h.lat], zoom: Math.max(map.current.getZoom(), 14) });
    } else {
      const b = cmdEls.current.get(h.id);
      const mode = b?.closest<HTMLElement>("[data-mode]")?.dataset.mode as Tab | undefined;
      if (mode) { setTab(mode); setRibbonFolded(false); }
      requestAnimationFrame(() => b?.click());
    }
  }
  const openTool = (t: string | null, q = "", preset?: Record<string, unknown>) => { setEarth({ open: false, start: null }); setScenariosOpen(false); setNotebookOpen(false); setCatalogOpen(false); setGdbOpen(false); setCoordsOpen(false); setSightOpen(false); setSymbolsOpen(false); setBriefOpen(false); setTrackingOpen(false); setProvidersOpen(false); setGp({ open: true, start: t, query: q, preset }); setRightOpen(true); };
  const openEarth = (t: string | null) => { setGp({ open: false, start: null, query: "" }); setScenariosOpen(false); setNotebookOpen(false); setCatalogOpen(false); setGdbOpen(false); setCoordsOpen(false); setSightOpen(false); setSymbolsOpen(false); setBriefOpen(false); setTrackingOpen(false); setProvidersOpen(false); setEarth({ open: true, start: t }); setRightOpen(true); };
  const openNotebook = () => { setGp({ open: false, start: null, query: "" }); setEarth({ open: false, start: null }); setScenariosOpen(false); setCatalogOpen(false); setGdbOpen(false); setCoordsOpen(false); setSightOpen(false); setSymbolsOpen(false); setBriefOpen(false); setTrackingOpen(false); setProvidersOpen(false); setNotebookOpen(true); setRightOpen(true); };
  const openCoords = () => { setBriefOpen(false); setTrackingOpen(false); setProvidersOpen(false); setProvidersOpen(false);  setCatalogOpen(false); setGdbOpen(false); setSightOpen(false); setSymbolsOpen(false); setBriefOpen(false); setTrackingOpen(false); setProvidersOpen(false); setCoordsOpen(true); setRightOpen(true); };
  const openProviders = () => { setCatalogOpen(false); setGdbOpen(false); setSightOpen(false); setCoordsOpen(false); setSymbolsOpen(false); setBriefOpen(false); setTrackingOpen(false); setProvidersOpen(true); setRightOpen(true); };
  const openCameras = () => { setProvidersOpen(false); setCatalogOpen(false); setGdbOpen(false); setSightOpen(false); setCoordsOpen(false); setSymbolsOpen(false); setBriefOpen(false); setTrackingOpen(false); setCamerasOpen(true); setRightOpen(true); };
  const openTracking = () => { setCamerasOpen(false); setProvidersOpen(false);  setCatalogOpen(false); setGdbOpen(false); setSightOpen(false); setCoordsOpen(false); setSymbolsOpen(false); setBriefOpen(false); setTrackingOpen(true); setRightOpen(true); };
  const openBrief = () => { setTrackingOpen(false); setProvidersOpen(false);  setCatalogOpen(false); setGdbOpen(false); setSightOpen(false); setCoordsOpen(false); setSymbolsOpen(false); setBriefOpen(true); setRightOpen(true); };
  const openSymbols = () => { setBriefOpen(false); setTrackingOpen(false); setProvidersOpen(false); setProvidersOpen(false);  setCatalogOpen(false); setGdbOpen(false); setSightOpen(false); setCoordsOpen(false); setSymbolsOpen(true); setRightOpen(true); };
  const openSight = () => { setBriefOpen(false); setTrackingOpen(false); setProvidersOpen(false); setProvidersOpen(false);  setCatalogOpen(false); setGdbOpen(false); setCoordsOpen(false); setSymbolsOpen(false); setSightOpen(true); setRightOpen(true); if (tool !== "explore") chooseTool("explore"); };
  async function saveProjectToComputer() {
    if (!project) return;
    try { await saveProjectFile(id, project.name, await getHandle(id)); toast(supportsFsAccess ? "Project saved to your computer." : "Project file downloaded.", "success"); }
    catch (e) { if (!(e instanceof DOMException && e.name === "AbortError")) toast(e instanceof Error ? e.message : "Could not save the project file.", "error"); }
  }
  async function openProjectFromFile() {
    try { const out = await openProjectFile(); if (out) { toast(`${out.name} opened.`, "success"); window.location.assign(`/projects/${out.id}`); } }
    catch (e) { if (!(e instanceof DOMException && e.name === "AbortError")) toast(e instanceof Error ? e.message : "Could not open the project file.", "error"); }
  }
  const rasterOk = me.features.includes("raster");
  const kindWord = editLayer?.geometry_type === "Point" ? "points" : editLayer?.geometry_type === "LineString" ? "lines" : "polygons";
  const hint: Partial<Record<Tool, ReactNode>> = {
    select: <>Click features of <strong>{active?.name}</strong> to select them. Shift-click adds or removes.</>,
    "select-area": <>Draw a shape around the features of <strong>{active?.name}</strong>: click the corners, then the first one again.</>,
    "measure-line": <>{measure ? <strong>{measure}</strong> : "Click along the line"}; click the last point again to finish.</>,
    "draw-area": <>Draw the area of interest: click its corners, then the first one again to finish.</>,
    "measure-bearing": <>{measure ? <strong>{measure}</strong> : "Click where you are, then the target"} (true bearings on the ellipsoid; grid is UTM grid north); click the last point again to finish.</>,
    "measure-area": <>{measure ? <strong>{measure}</strong> : "Click the corners"}; click the first one again to finish.</>,
    create: <>Drawing {kindWord} in <strong>{editLayer?.name}</strong>: {editLayer?.geometry_type === "Point" ? "click to place one" : "click each corner, then the last (or first) point again"}.{snap ? " Snapping is on." : ""}</>,
    "pick-reshape": <>Click the feature of <strong>{editLayer?.name}</strong> to reshape.</>,
    reshape: <>Drag a vertex to move it, a midpoint to add one; right-click a vertex to remove it.</>,
  };
  // ---------- military symbols ----------
  function placeSymbols(lid: string, props: Record<string, unknown>) {
    const l = layers.find((x) => x.id === lid);
    if (!l) return;
    placeProps.current = props;
    if (placing && editId === lid) return;                     // already placing there: the next click uses the new symbol
    setEditId(lid); setActiveId(lid); state.current.editId = lid; setPlacing(true);
    startCreate(l);
  }
  async function newSymbolLayer(): Promise<Layer | null> {
    try {
      const names = layers.map((l) => l.name);
      let name = "Military symbols", k = 2;
      while (names.includes(name)) name = `Military symbols ${k++}`;
      const made = await api.newLayer(id, { name, geometry_type: "Point", fields: [
        { name: "sidc", type: "string" }, { name: "designation", type: "string" }, { name: "higher_formation", type: "string" }, { name: "remarks", type: "string" }] });
      const styled = await api.updateLayer(made.id, { style: { ...made.style, type: "milsymbol", field: null,
        mil: { sidc: "SFGPU----------", sidc_field: "sidc", designation_field: "designation", higher_field: "higher_formation", size: 36 } } });
      setLayers((cur) => [...cur, styled]); setActiveId(styled.id);
      toast(`${styled.name} added: choose a symbol and place it on the map.`, "success");
      return styled;
    } catch (e) { toast(e instanceof ApiError ? e.message : "Could not add the layer.", "error"); return null; }
  }

  // the one polygon selected on the map, if any: an area to brief on
  const selPolys = layers.filter((l) => l.geometry_type === "Polygon" && (selection[l.id]?.length ?? 0) > 0);
  const selectedPolygon = selPolys.length === 1 && selection[selPolys[0].id].length === 1 ? { layer: selPolys[0], id: selection[selPolys[0].id][0] } : null;
  const drawArea = () => new Promise<GeoJSON.Geometry | null>((resolve) => {
    if (!editor.current) { resolve(null); return; }
    chooseTool("explore");
    setTool("draw-area");                        // the hint bar says what to do; Esc (or another tool) gives up
    areaWait.current = resolve;
    // the editor clears the finished shape itself after this call; stop drawing just after
    editor.current.create("polygon", (g) => { areaWait.current = null; resolve(g); setTimeout(() => { editor.current?.reset(); setTool("explore"); }, 0); });
  });

  const right = gdbOpen ? (
    <Geodatabases projectId={id} canEdit={canEdit} onChanged={() => void load()} onClose={() => setGdbOpen(false)} />
  ) : providersOpen ? (
    <ProvidersPane projectId={id} map={map.current} isAdmin={me.role === "admin" && me.can_write} canAdd={canEdit && rasterOk} basemap={basemap}
      onBasemap={(b) => switchBasemap(b)} onLayer={(l) => { setLayers((cur) => [...cur, l]); setActiveId(l.id); }}
      onChanged={providersLoaded} view={() => (map.current ? mapBox() : null)} onClose={() => setProvidersOpen(false)} />
  ) : trackingOpen ? (
    <TrackingPane projectId={id} layers={layers} canEdit={canEdit} live={liveOn} onLive={setLiveOn} alerts={alerts}
      onAlertsSeen={() => void api.alertsSeen(id).then(() => setAlerts([]))} view={() => (map.current ? mapBox() : null)}
      onLayer={(l) => { setLayers((cur) => (cur.some((x) => x.id === l.id) ? cur.map((x) => (x.id === l.id ? l : x)) : [...cur, l])); }}
      onZoom={(x, y) => map.current?.flyTo({ center: [x, y], zoom: Math.max(map.current.getZoom(), 14) })} onClose={() => setTrackingOpen(false)} />
  ) : camerasOpen ? (
    <CamerasPane projectId={id} cams={cams} setCams={setCams} canEdit={canEdit} watching={watching}
      onWatch={(cid) => setWatching((w) => (w.includes(cid) ? w.filter((x) => x !== cid) : [...w, cid].slice(-4)))}
      onZoom={(c) => map.current?.flyTo({ center: [c.lng!, c.lat!], zoom: Math.max(map.current.getZoom(), 16) })} onClose={() => setCamerasOpen(false)} />
  ) : briefOpen ? (
    <BriefPane projectId={id} dems={layers.filter((l) => l.geometry_type === "Raster" && l.raster && l.raster.count === 1 && !l.raster.missing)}
      selectedPolygon={selectedPolygon} onDraw={drawArea} onClose={() => setBriefOpen(false)} />
  ) : symbolsOpen ? (
    <SymbolsPane layers={layers} canEdit={canEdit} placing={placing} onPlace={placeSymbols} onStop={stopEditing}
      onNewLayer={newSymbolLayer} onClose={() => { if (placing) stopEditing(); setSymbolsOpen(false); }} />
  ) : sightOpen ? (
    <SightPane map={map.current} dems={layers.filter((l) => l.geometry_type === "Raster" && l.raster && l.raster.count === 1 && !l.raster.missing)} onClose={() => setSightOpen(false)} />
  ) : coordsOpen ? (
    <CoordinatePane map={map.current} onClose={() => setCoordsOpen(false)} onPoints={canEdit ? (text) => openTool("points_from_coordinates", "", { coordinates: text }) : undefined}
      onTool={canEdit ? (t, text) => openTool(t, "", { origin: "typed", origin_text: text }) : undefined} />
  ) : catalogOpen ? (
    <Catalog maps={maps} layouts={savedLayouts} mapId={mapId} current={viewNow} canEdit={canEdit} layoutsAllowed={layoutsAllowed}
      onOpenMap={(m) => void openMap(m)} onNewMap={() => void newMap()} onCopyMap={(m) => void newMap(m)} onRenameMap={(m, n) => void renameMap(m, n)} onDeleteMap={(m) => void deleteMap(m)}
      onOpenLayout={openLayoutTab} onNewLayout={newLayout} onRenameLayout={(l, n) => void renameLayout(l, n)} onDeleteLayout={(l) => void deleteLayout(l)}
      onAddData={async (files) => {
        const ls = await api.importFile(id, files, undefined, map.current ? mapBox() : undefined);
        setLayers((cur) => [...cur, ...ls]); setActiveId(ls[ls.length - 1].id); setLeftOpen(true);
        if (ls[0]?.bbox) zoomTo(ls[0].bbox);
        toast(ls.length === 1 ? `${ls[0].name} added to the map.` : `${ls.length} layers added to the map.`, "success");
      }}
      onClose={() => setCatalogOpen(false)} />
  ) : notebookOpen ? (
    <Notebook projectId={id} layers={layers} activeId={activeId} onClose={() => setNotebookOpen(false)}
      onLayer={(l) => { setLayers((cur) => (cur.some((x) => x.id === l.id) ? cur.map((x) => (x.id === l.id ? l : x)) : [...cur, l])); setActiveId(l.id); if (l.bbox) zoomTo(l.bbox); }} />
  ) : scenariosOpen ? (
    <Scenarios projectId={id} layers={layers} canEdit={canEdit} onClose={() => setScenariosOpen(false)}
      onLayer={(l, show) => {
        setLayers((cur) => (cur.some((x) => x.id === l.id) ? cur.map((x) => (x.id === l.id ? l : x)) : [...cur, l]));
        if (show) { setActiveId(l.id); if (l.bbox) zoomTo(l.bbox); }
      }} />
  ) : earth.open ? (
    <PolyEarth projectId={id} layers={layers} selection={selection} activeId={activeId} canEdit={canEdit} allowed={me.features.includes("raster")}
      start={earth.start} jobs={earthJobs.jobs} viewBox={mapBox} onStarted={earthJobs.add} onStop={(jid) => void earthJobs.stop(jid)} onClose={() => setEarth({ open: false, start: null })}
      onShowLayer={(l) => { setActiveId(l.id); if (l.bbox) zoomTo(l.bbox); }} />
  ) : gp.open ? (
    <Geoprocessing projectId={id} layers={layers} selection={selection} activeId={activeId} canEdit={canEdit} query={gp.query} preset={gp.preset} mapView={() => (map.current ? mapBox() : null)}
      allowed={me.features.includes("geoprocessing")} rasterAllowed={me.features.includes("raster")} start={gp.start} onClose={() => setGp({ open: false, start: null, query: "" })}
      onDone={(l) => { setLayers((cur) => [...cur, l]); setActiveId(l.id); if (l.bbox) zoomTo(l.bbox); toast(`${l.name} added to the map.`, "success"); }} />
  ) : identify && tool === "explore" ? (
    <Inspector layer={identify.layer} fid={identify.id} props={identify.props} canEdit={canEdit} selected={sel(identify.layer.id).includes(identify.id)}
      onClose={() => setIdentify(null)} onZoom={() => void zoomToFeature(identify.layer, identify.id)}
      onSelect={() => setSelection((s) => { const cur = s[identify.layer.id] ?? []; return { ...s, [identify.layer.id]: cur.includes(identify.id) ? cur.filter((x) => x !== identify.id) : [...cur, identify.id] }; })}
      onTable={() => setTableId(identify.layer.id)} onStyle={() => openPanel(identify.layer, "symbology")}
      onEdit={() => { setIdentify(null); startEditing(identify.layer.id); }} />
  ) : newFeature && editLayer ? (
    <NewFeatureForm layer={editLayer} fid={newFeature.id} onDone={() => { setNewFeature(null); void refreshLayer(editLayer.id); }} />
  ) : active?.geometry_type === "Raster" ? (
    <RasterPanel key={`${active.id}-${panelTab.n}`} initialTab={panelTab.tab} layer={active} canEdit={canEdit} canExport={me.features.includes("export")} onChange={(l) => { replaceLayer(l); }}
      onGeoreference={me.features.includes("raster") ? (l) => { setGeorefFile(null); setGeorefLayer(l.id); setGeorefOpen(true); } : undefined} />
  ) : active ? (
    <LayerPanel key={`${active.id}-${panelTab.n}`} initialTab={panelTab.tab} layer={active} canEdit={canEdit} selected={sel(active.id)} onChange={(l) => { replaceLayer(l); }} onExport={() => setDialog("export")} />
  ) : (
    <aside className="ws-panel" aria-label="Properties">
      <div className="ws-pane-head"><strong>Properties</strong></div>
      <div className="ws-empty-state"><SlidersHorizontal size={22} aria-hidden="true" />Choose a layer in Layers to style it, label it and see its fields.</div>
    </aside>
  );
  /** The modes down the left: each brings its own tools into the tool row. */
  const tabs: [Tab, string, ReactNode][] = [["map", "Explore", <Compass key="i" />], ["insert", "Data", <Database key="i" />], ["select", "Select", <MousePointerClick key="i" />],
    ["edit", "Edit", <PenLine key="i" />], ["analysis", "Analyse", <Wrench key="i" />], ...(me.features.includes("raster") ? [["imagery", "Imagery", <ImageIcon key="i" />] as [Tab, string, ReactNode]] : []),
    ["military", "Defence", <Shield key="i" />], ["earth", "Earth", <Earth key="i" />], ["share", "Publish", <UploadCloud key="i" />]];
  const modeName = tabs.find(([t]) => t === tab)?.[1] ?? "";
  const openLayout = newLayout;
  const viewing = views.find((v) => viewKey(v) === viewNow) ?? null;
  const onLayout = viewing?.kind === "layout";

  return (
    <div className={`ws${onLayout ? " is-layout" : ""}${focus && !onLayout ? " is-focus" : ""}`} style={{ "--left": `${leftW}px`, "--right": `${rightW}px`, "--table": `${tableH}px` } as React.CSSProperties}>
      <div className="ws-top">
      <header className="ws-titlebar">
        {(() => { const lk = brandLockup(me); return (
          <a className="ge-brand" href="/" aria-label={`${lk.branded ? lk.name : "GeoEstate PolyGIS"}: all projects`}>
            <img src={lk.logo_url ?? "/logo.png"} alt="" />
            {lk.branded ? <span>{lk.name}</span> : <span>Poly<strong>GIS</strong></span>}
          </a>
        ); })()}
        <nav className="ws-crumbs" aria-label="Where you are">
          <ChevronRight size={14} aria-hidden="true" /><Link to="/">Projects</Link><ChevronRight size={14} aria-hidden="true" />
          <h1 className="ws-crumb-title" title={project.description || project.name}>{project.name}</h1>
        </nav>
        <AskBar ref={commandEl} tools={allTools} layers={layers} commands={modeCommands} onPick={doAsk} onSearchTools={(q) => openTool(null, q)} />
        <button type="button" className="ws-top-btn" aria-pressed={focus} onClick={() => setFocus(!focus)} title={focus ? "Back to the panes" : "The map only: modes in a dock, tools and layers float"}>
          {focus ? <Minimize2 size={15} aria-hidden="true" /> : <Maximize2 size={15} aria-hidden="true" />}{focus ? "Leave focus" : "Focus"}</button>
        <button type="button" className="ws-top-btn ws-top-icon" aria-pressed={theme === "ops"} onClick={() => setTheme(theme === "ops" ? "light" : "ops")}
          aria-label={theme === "ops" ? "Light theme" : "Operations theme (dark)"} title={theme === "ops" ? "Light theme" : "Operations theme (dark)"}>
          {theme === "ops" ? <Sun size={16} /> : <Moon size={16} />}</button>
        <Apps apps={apps} />
        <UserMenu me={me} onSignOut={onSignOut} />
      </header>
      {notice && <div className={`ge-notice ge-notice-${notice.tone}`} role="status">{notice.text}</div>}
      </div>

      <nav className="ws-modes" aria-label="Modes">
        {tabs.map(([t, label, icon]) => (
          <button key={t} type="button" aria-pressed={tab === t} className={`ws-mode${t === "edit" && editId ? " is-ctx" : ""}`} title={t === "edit" && editId ? "Edit: editing" : label}
            onClick={() => { if (tab === t) setRibbonFolded(!ribbonFolded); else { setRibbonFolded(false); setTab(t); } }}>
            <span className="ws-mode-icon" aria-hidden="true">{icon}</span>{label}</button>
        ))}
        <span className="ws-modes-gap" />
        <button type="button" className="ws-mode ws-mode-fold" aria-expanded={!ribbonFolded} onClick={() => setRibbonFolded(!ribbonFolded)}
          title={ribbonFolded ? `Show the ${modeName} tools` : "Fold the tools away"}>
          <span className="ws-mode-icon" aria-hidden="true">{ribbonFolded ? <ChevronDown /> : <ChevronUp />}</span>{ribbonFolded ? "Show tools" : "Fold tools"}</button>
      </nav>

      <section className={`ws-ribbon${ribbonFolded ? " is-folded" : ""}`} aria-label={`${modeName} tools`}>
        {ribbonFolded && <span className="ws-strip-name">{modeName}{tab === "edit" && editId ? ": editing" : ""}</span>}
        <RibbonBody label={`${modeName} tools`}>
          <div className="ws-mode-tools" data-mode="map" hidden={tab !== "map"}>
            <Group name="Navigate">
              <Tb on={tool === "explore"} onClick={() => chooseTool("explore")} icon={<Hand />} label="Pan" />
              <Tb onClick={zoomAll} icon={<Maximize />} label="Fit all" disabled={!layers.some((l) => l.bbox)} />
              <Tb onClick={() => active?.bbox && zoomTo(active.bbox)} icon={<ZoomIn />} label="Zoom to layer" disabled={!active?.bbox} />
            </Group>
            <Group name="Basemap">
              <div className="ws-basemaps" role="radiogroup" aria-label="Basemap">
                {allBasemaps().map((b) => (
                  <button key={b.id} type="button" role="radio" aria-checked={basemap === b.id} className={`ws-basemap${basemap === b.id ? " is-on" : ""}`} onClick={() => switchBasemap(b.id)} title={b.name}>
                    <i style={{ background: BASEMAP_LOOK[b.id] ?? (b.id.startsWith("provider:") ? "linear-gradient(135deg, #3d5a3c, #8a7f5a 55%, #b9b39a)" : "#ddd") }} />{b.name.split(" (")[0]}
                  </button>
                ))}
              </div>
            </Group>
            <Group name="3D">
              <Tb on={view3d.on} onClick={() => toggle3d(!view3d.on)} icon={<Box />} label="3D view" />
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
            <Group name="Project">
              <Tb on={catalogOpen && rightOpen} onClick={() => { if (catalogOpen) { setCatalogOpen(false); } else { setCoordsOpen(false); setSightOpen(false); setSymbolsOpen(false); setBriefOpen(false); setTrackingOpen(false); setProvidersOpen(false); setGdbOpen(false); setCatalogOpen(true); setRightOpen(true); } }} icon={<FolderTree />} label="Library" />
              <Tb on={gdbOpen && rightOpen} onClick={() => { if (gdbOpen) { setGdbOpen(false); } else { setCoordsOpen(false); setSightOpen(false); setSymbolsOpen(false); setBriefOpen(false); setTrackingOpen(false); setProvidersOpen(false); setCamerasOpen(false); setCatalogOpen(false); setScenariosOpen(false); setNotebookOpen(false); setEarth({ open: false, start: null }); setGp({ open: false, start: null, query: "" }); setGdbOpen(true); setRightOpen(true); } }} icon={<Database />} label="Geodatabases" />
              <Tb onClick={() => void saveProjectToComputer()} icon={<HardDriveDownload />} label={supportsFsAccess ? "Save to computer" : "Download file"} />
              <Tb onClick={() => void openProjectFromFile()} icon={<FolderInput />} label="Open file" />
            </Group>
            <Group name="Coordinates">
              <Tb onClick={() => setDialog("crs")} icon={<Globe2 />} label={crs ? shortCrs(crs.name) : `EPSG:${project.display_epsg}`} />
            </Group>
          </div>
          <div className="ws-mode-tools" data-mode="select" hidden={tab !== "select"}>
            <Group name="Interactive">
              <Tb on={tool === "select"} onClick={() => chooseTool("select")} icon={<MousePointerClick />} label="Select" disabled={!active || active.geometry_type === "Raster"} />
              <Tb on={tool === "select-area"} onClick={() => chooseTool("select-area")} icon={<Lasso />} label="By shape" disabled={!active || active.geometry_type === "Raster"} />
            </Group>
            <Group name="Queries">
              <Tb onClick={() => setDialog("attr")} icon={<ListFilter />} label="Filter by values" disabled={!active || active.geometry_type === "Raster"} />
              <Tb onClick={() => setDialog("loc")} icon={<LocateFixed />} label="By place" disabled={!active || active.geometry_type === "Raster"} />
            </Group>
            <Group name="Selection">
              <Tb onClick={() => void switchSelection()} icon={<ArrowLeftRight />} label="Invert" disabled={!active || active.geometry_type === "Raster"} />
              <Tb onClick={() => setSelection({})} icon={<Eraser />} label="Clear" disabled={!totalSel} />
              <Tb onClick={() => active && void zoomToSelection(active)} icon={<Scan />} label="Zoom to" disabled={!active || !sel(active.id).length} />
            </Group>
          </div>
          <div className="ws-mode-tools" data-mode="insert" hidden={tab !== "insert"}>
            <Group name="Data">
              <Tb onClick={() => setDialog("add")} icon={<FilePlus2 />} label="Add data" disabled={!canEdit} />
              <Tb onClick={() => setDialog("new")} icon={<Layers />} label="New layer" disabled={!canEdit} />
              <Tb onClick={() => setDialog("linked")} icon={<Link2 />} label="Linked layer" disabled={!canEdit} />
              <Tb onClick={() => setDialog("export")} icon={<Download />} label="Export layer" disabled={!active || active.geometry_type === "Raster"} />
            </Group>
            <Group name="Project">
              <Tb onClick={() => void newMap()} icon={<MapIcon />} label="New map" disabled={!canEdit} />
              <Tb onClick={() => openMapDoc && void newMap(openMapDoc)} icon={<Layers3 />} label="Copy map" disabled={!canEdit || !openMapDoc} />
              <Tb onClick={newLayout} icon={<Printer />} label="New print" />
            </Group>
          </div>
          <div className="ws-mode-tools" data-mode="share" hidden={tab !== "share"}>
            <Group name="Link">
              <Tb onClick={() => setDialog("share")} icon={<Link2 />} label="Share link" />
            </Group>
            <Group name="Data hub">
              <Tb onClick={() => setDialog("publish")} icon={<UploadCloud />} label="Publish layer" disabled={!canEdit || !active} />
            </Group>
            <Group name="Print">
              <Tb onClick={openLayout} icon={<Printer />} label="Print studio" />
            </Group>
            <Group name="Export">
              <Tb onClick={() => setDialog("export")} icon={<Download />} label="Export layer" disabled={!active || active.geometry_type === "Raster"} />
            </Group>
          </div>
          <div className="ws-mode-tools" data-mode="edit" hidden={tab !== "edit"}>
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
          </div>
          <div className="ws-mode-tools" data-mode="analysis" hidden={tab !== "analysis"}>
            {me.features.includes("raster") && <Group name="What if">
              <Tb on={scenariosOpen && rightOpen} onClick={() => { if (scenariosOpen) setScenariosOpen(false); else { setGdbOpen(false); setGp({ open: false, start: null, query: "" }); setEarth({ open: false, start: null }); setScenariosOpen(true); setRightOpen(true); } }}
                icon={<FlaskConical />} label="Scenarios" />
            </Group>}
            <Group name="Measure">
              <Tb on={tool === "measure-line"} onClick={() => chooseTool("measure-line")} icon={<Ruler />} label="Distance" />
              <Tb on={tool === "measure-area"} onClick={() => chooseTool("measure-area")} icon={<LandPlot />} label="Area" />
            </Group>
            <Group name="Tools">
              <Tb on={gp.open && !gp.start && rightOpen} onClick={() => (gp.open && !gp.start ? setGp({ open: false, start: null, query: "" }) : openTool(null))} icon={<Wrench />} label="All tools" />
              <Tb onClick={() => openTool("buffer")} icon={<CircleDot />} label="Buffer" />
              <Tb onClick={() => openTool("clip")} icon={<Scissors />} label="Clip" />
              <Tb onClick={() => openTool("intersect")} icon={<Combine />} label="Intersect" />
              <Tb onClick={() => openTool("dissolve")} icon={<Merge />} label="Dissolve" />
              <Tb onClick={() => openTool("spatial_join")} icon={<Link2 />} label="Spatial join" />
            </Group>
            <Group name="Models">
              <Tb onClick={() => (me.features.includes("geoprocessing") ? setModelsOpen(true) : setDialog("plan"))} icon={<Workflow />} label="Flows" />
              {canEdit && me.features.includes("geoprocessing") && <Tb on={notebookOpen && rightOpen} onClick={() => (notebookOpen ? setNotebookOpen(false) : openNotebook())} icon={<TerminalSquare />} label="Notebook" />}
            </Group>
            <Group name="Satellite">
              <Tb onClick={() => { setTab("earth"); openEarth(null); }} icon={<Earth />} label="Earth" />
            </Group>
            {me.features.includes("raster") && <Group name="Raster">
              <Tb onClick={() => openTool("hillshade")} icon={<Mountain />} label="Hillshade" />
              <Tb onClick={() => openTool("slope")} icon={<TrendingUp />} label="Slope" />
              <Tb onClick={() => openTool("contours")} icon={<Waves />} label="Contours" />
              <Tb onClick={() => openTool("watershed")} icon={<Droplets />} label="Watershed" />
              <Tb onClick={() => openTool("zonal_statistics")} icon={<Grid3x3 />} label="Zonal statistics" />
            </Group>}
          </div>
          <div className="ws-mode-tools" data-mode="imagery" hidden={tab !== "imagery"}>
            <Group name="Sources & place">
              <Tb on={providersOpen && rightOpen} onClick={() => (providersOpen ? setProvidersOpen(false) : openProviders())} icon={<Satellite />} label="Sources" />
              <Tb onClick={() => { setGeorefFile(null); setGeorefLayer(null); setGeorefOpen(true); }} icon={<MapPinned />} label="Georeference" disabled={!canEdit} />
            </Group>
            <Group name="Correct">
              <div className="ws-tb-stack">
                <Ts onClick={() => openTool("to_reflectance")} icon={<Gauge size={16} />} label="To reflectance" />
                <Ts onClick={() => openTool("dark_object_subtraction")} icon={<CloudFog size={16} />} label="Atmospheric" />
                <Ts onClick={() => openTool("cloud_mask")} icon={<CloudOff size={16} />} label="Cloud mask" />
              </div>
              <div className="ws-tb-stack">
                <Ts onClick={() => openTool("topographic_correction")} icon={<Mountain size={16} />} label="Topographic" />
                <Ts onClick={() => openTool("pan_sharpen")} icon={<Focus size={16} />} label="Pan-sharpen" />
                <Ts onClick={() => openTool("fill_nodata")} icon={<PaintBucket size={16} />} label="Fill gaps" />
              </div>
            </Group>
            <Group name="Enhance">
              <div className="ws-tb-stack">
                <Ts onClick={() => openTool("brightness_contrast")} icon={<SunMedium size={16} />} label="Brightness" />
                <Ts onClick={() => openTool("stretch")} icon={<SlidersHorizontal size={16} />} label="Stretch" />
                <Ts onClick={() => openTool("clahe")} icon={<Contrast size={16} />} label="Local contrast" />
              </div>
              <div className="ws-tb-stack">
                <Ts onClick={() => openTool("unsharp_mask")} icon={<Sparkles size={16} />} label="Sharpen" />
                <Ts onClick={() => openTool("dehaze")} icon={<Haze size={16} />} label="Haze removal" />
                <Ts onClick={() => openTool("denoise")} icon={<Eraser size={16} />} label="Noise removal" />
              </div>
              <div className="ws-tb-stack">
                <Ts onClick={() => openTool("histogram_match")} icon={<Palette size={16} />} label="Match colours" />
              </div>
            </Group>
            <Group name="Analyse">
              <Tb onClick={() => openTool("normalized_difference")} icon={<Leaf />} label="NDVI" />
              <Tb onClick={() => openTool("supervised_classification")} icon={<MapIcon />} label="Classify" />
              <Tb onClick={() => openTool("change_detection")} icon={<ArrowLeftRight />} label="Change" />
            </Group>
          </div>
          <div className="ws-mode-tools" data-mode="military" hidden={tab !== "military"}>
            <Group name="Coordinates & grids">
              <Tb on={coordsOpen && rightOpen} onClick={() => (coordsOpen ? setCoordsOpen(false) : openCoords())} icon={<Crosshair />} label="Convert" />
              <div className="ws-tb-stack">
                <Ts onClick={() => openTool("points_from_coordinates")} icon={<ListPlus size={16} />} label="To points" title="Coordinates to points" />
                <Ts onClick={() => openTool("convert_notation")} icon={<Shield size={16} />} label="Notation" title="Convert coordinate notation (as fields)" />
              </div>
              <div className="ws-tb-stack">
                <Ts onClick={() => openTool("mgrs_grid")} icon={<Grid3x3 size={16} />} label="MGRS grid" />
                <Ts onClick={() => openTool("gars_grid")} icon={<Grid2x2 size={16} />} label="GARS grid" />
              </div>
            </Group>
            <Group name="Distance & direction">
              <Tb on={tool === "measure-bearing"} onClick={() => chooseTool(tool === "measure-bearing" ? "explore" : "measure-bearing")} icon={<Compass />} label="Bearing" />
              <Tb onClick={() => openTool("range_rings")} icon={<Radar />} label="Range rings" />
              <div className="ws-tb-stack">
                <Ts onClick={() => openTool("bearing_lines")} icon={<MoveUpRight size={16} />} label="Bearing lines" />
                <Ts onClick={() => openTool("distance_bearing")} icon={<Route size={16} />} label="Distances" title="Distance and bearing between points" />
                <Ts onClick={() => openTool("ellipse")} icon={<Orbit size={16} />} label="Ellipse" />
              </div>
            </Group>
            <Group name="Visibility">
              <Tb on={sightOpen && rightOpen} onClick={() => (sightOpen ? setSightOpen(false) : openSight())} icon={<Eye />} label="Line of sight" disabled={!rasterOk} />
              <Tb onClick={() => openTool("sector_viewshed")} icon={<ScanEye />} label="Viewshed" disabled={!rasterOk} />
              <div className="ws-tb-stack">
                <Ts onClick={() => openTool("line_of_sight")} icon={<Eye size={16} />} label="Sight lines" disabled={!rasterOk} />
                <Ts onClick={() => openTool("radial_line_of_sight")} icon={<Sun size={16} />} label="Radial" disabled={!rasterOk} />
                <Ts onClick={() => openTool("highest_points")} icon={<MountainSnow size={16} />} label="Hilltops" title="Highest points" disabled={!rasterOk} />
              </div>
            </Group>
            <Group name="Symbols">
              <Tb on={symbolsOpen && rightOpen} onClick={() => (symbolsOpen ? setSymbolsOpen(false) : openSymbols())} icon={<Shapes />} label="Symbols" />
            </Group>
            <Group name="Intelligence">
              <Tb on={briefOpen && rightOpen} onClick={() => (briefOpen ? setBriefOpen(false) : openBrief())} icon={<ClipboardList />} label="Area brief" />
              <div className="ws-tb-stack">
                <Ts onClick={() => openTool("weapon_ranges")} icon={<Target size={16} />} label="Ranges" title="Weapon and sensor ranges" />
                <Ts onClick={() => openTool("activity_change")} icon={<Activity size={16} />} label="Activity" title="Activity change between periods" />
                <Ts onClick={() => openTool("hotspots")} icon={<Flame size={16} />} label="Hot spots" />
              </div>
            </Group>
            <Group name="Tracking">
              <Tb on={trackingOpen && rightOpen} onClick={() => (trackingOpen ? setTrackingOpen(false) : openTracking())} icon={<Radio />} label="Tracking" />
              <Tb on={camerasOpen && rightOpen} onClick={() => (camerasOpen ? setCamerasOpen(false) : openCameras())} icon={<Video />} label="Cameras" />
              <div className="ws-tb-stack">
                <Ts on={liveOn} onClick={() => setLiveOn(!liveOn)} icon={<Play size={16} />} label={liveOn ? "Live: on" : "Live"} />
                <Ts onClick={() => { openTracking(); }} icon={<BellRing size={16} />} label={alerts.length ? `Alerts (${alerts.length})` : "Alerts"} />
              </div>
            </Group>
          </div>
          <div className="ws-mode-tools" data-mode="earth" hidden={tab !== "earth"}>
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
          </div>
        </RibbonBody>
      </section>

      <ViewTabs tabs={views} current={viewNow} maps={maps} layouts={savedLayouts} onPick={pickView} onClose={closeView}
        onNewMap={() => void newMap()} onNewLayout={newLayout} canEdit={canEdit} layoutsAllowed={layoutsAllowed} />

      <main className={`ws-body${leftOpen ? "" : " no-left"}${rightOpen ? "" : " no-right"}`}>
        {leftOpen ? (
          <Contents projectName={project.name} layers={layers} activeId={activeId} editingId={editId} canEdit={canEdit} canExport={me.features.includes("export")}
            selection={selection} onActivate={setActiveId} onToggle={(l) => void toggle(l)}
            onReorder={(ids) => void reorder(ids)} onZoom={(l) => l.bbox && zoomTo(l.bbox)} onTable={(l) => setTableId(l.id)} onRemove={(l) => void remove(l)}
            onPanel={openPanel} onSelection={(l, op) => void selectionOp(l, op)} onLabels={(l, on) => void labelsOn(l, on)} onRename={(l, n) => void renameLayer(l, n)}
            onPaste={(t, f) => void pasteProperties(t, f)} onSourceResolution={sourceResolution}
            onExport={(l) => { setActiveId(l.id); if (l.geometry_type !== "Raster") setDialog("export"); else window.location.assign(rasterDownloadUrl(l.id)); }}
            mapName={openMapDoc?.name ?? "Map"} otherMaps={maps.filter((m) => m.id !== mapId)} onAddToMap={(l, mid, how) => void addToMap(l, mid, how)}
            onEdit={(l) => startEditing(l.id)} onAdd={() => setDialog("add")} onCollapse={() => setLeftOpen(false)} onRefresh={(l) => void refreshLinked(l)} />
        ) : (
          <div className="ws-rail is-left"><IconButton label="Show Layers" onClick={() => setLeftOpen(true)}><PanelLeftOpen size={18} /></IconButton>
            <IconButton label="Add data" onClick={() => setDialog("add")} disabled={!canEdit}><FilePlus2 size={18} /></IconButton></div>
        )}
        {leftOpen && <Resizer axis="x" value={leftW} min={200} max={480} onChange={setLeftW} label="Resize Layers" className="rs-left" />}
        <div className="ws-map-wrap">
          <div ref={mapEl} className={`ws-map tool-${tool}`} />
          {!onLayout && <WhatsNew onAsk={() => { commandEl.current?.focus(); }} />}
          {!onLayout && <CameraWall cams={cams} watching={watching} onClose={(cid) => setWatching((w) => w.filter((x) => x !== cid))} />}
          {focus && !onLayout && <FocusLayers layers={layers} activeId={activeId} onToggle={(l) => void toggle(l)}
            onPick={(l) => { setActiveId(l.id); setIdentify(null); setRightOpen(true); }} onAdd={() => setDialog("add")} canAdd={canEdit} onLeave={() => setFocus(false)} />}
          {totalSel > 0 && <SelectionBar selection={selection} layers={layers}
            onFilter={(l) => { setActiveId(l.id); setDialog("attr"); }} onPlace={(l) => { setActiveId(l.id); setDialog("loc"); }} onInvert={(l) => void switchSelection(l)}
            onZoom={(l) => void zoomToSelection(l)} onTable={(l) => setTableId(l.id)} onExport={(l) => { setActiveId(l.id); setDialog("export"); }}
            onClear={() => setSelection({})} onPick={(l) => setActiveId(l.id)} activeId={activeId} />}
          {tab === "analysis" && tool === "explore" && !onLayout && !gp.open && <ToolCards tools={allTools.filter((t) => t.kind !== "raster")} onOpen={(t) => openTool(t.id)}
            onAll={() => openTool(null)} onFlows={() => (me.features.includes("geoprocessing") ? setModelsOpen(true) : setDialog("plan"))} />}
          {tool !== "explore" && hint[tool] && <div className="ws-hintbar" role="status"><span>{hint[tool]}</span>
            <button type="button" onClick={() => (tool === "create" || tool === "pick-reshape" || tool === "reshape" ? chooseTool("explore") : chooseTool("explore"))}>Done <span className="kbd" style={{ background: "transparent", color: "#cfd6e4", borderColor: "rgba(255,255,255,.3)" }}>Esc</span></button></div>}
        </div>
        {rightOpen && <Resizer axis="x" value={rightW} min={260} max={560} onChange={setRightW} invert label="Resize the side pane" className="rs-right" />}
        {rightOpen ? <div className="ws-right">{right}<IconButton className="ws-collapse" label="Hide the side pane" onClick={() => setRightOpen(false)}><PanelRightClose size={16} /></IconButton></div> : (
          <div className="ws-rail is-right"><IconButton label="Show the side pane" onClick={() => setRightOpen(true)}><PanelRightOpen size={18} /></IconButton>
            <IconButton label="Tools" onClick={() => openTool(null)}><Wrench size={18} /></IconButton></div>
        )}
        {(leftOpen || rightOpen) && <div className="ws-scrim" onClick={() => { setLeftOpen(false); setRightOpen(false); }} aria-hidden="true" />}
      </main>

      {tableLayer && <AttributeTable layer={tableLayer} selected={sel(tableLayer.id)} editing={editId === tableLayer.id}
        resizer={<Resizer axis="y" value={tableH} min={140} max={640} invert onChange={setTableH} label="Resize the table" />}
        onSelect={(ids, add) => setSelection((s) => ({ ...s, [tableLayer.id]: add ? Array.from(new Set([...(s[tableLayer.id] ?? []), ...ids])) : ids }))}
        onZoomTo={(fid) => void zoomToFeature(tableLayer, fid)} onClose={() => setTableId(null)} onEdited={() => void refreshLayer(tableLayer.id)} />}

      <footer className="ws-status" aria-label="Status">
        <span className="ws-coords" title="Pointer position">{pointerText || "Point at the map for coordinates"}</span>
        <select className="ws-fmt" value={coordFmt} onChange={(e) => setCoordFmt(e.target.value as "crs" | Notation)} aria-label="Show the pointer position in" title="Show the pointer position in">
          <option value="crs">{crs ? crs.name : `EPSG:${project.display_epsg}`}</option>{NOTATIONS.map((n) => <option key={n} value={n}>{NOTATION_NAMES[n]}</option>)}</select>
        <button type="button" className="ws-crs" onClick={() => setDialog("crs")} title="Change the coordinate system">{coordFmt === "crs" ? "Change…" : (crs ? crs.name : `EPSG:${project.display_epsg}`)}</button>
        <span title="Map scale at the centre">1:{niceScale(scale)}</span>
        <span className="ws-zoom">Zoom {zoom.toFixed(1)}</span>
        {measure && <span className="ws-measure">{measure}</span>}
        <span className="ge-spacer" />
        {!canEdit && <span>{me.can_write ? "Viewer: read-only" : "Read-only licence"}</span>}
      </footer>

      {dialog === "add" && <AddData projectId={id} view={map.current ? mapBox() : undefined} onClose={() => setDialog(null)} onImagery={() => setDialog("imagery")}
        onGeoreference={me.features.includes("raster") ? (f) => { setDialog(null); setGeorefFile(f); setGeorefOpen(true); } : undefined} onDone={(ls) => {
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
      {modelsOpen && <ModelBuilder projectId={id} layers={layers} canEdit={canEdit} rasterAllowed={me.features.includes("raster")} onClose={() => setModelsOpen(false)}
        onDone={(ls) => { if (!ls.length) return; setLayers((cur) => [...cur, ...ls]); setActiveId(ls[ls.length - 1].id); if (ls[ls.length - 1].bbox) zoomTo(ls[ls.length - 1].bbox!); toast(ls.length === 1 ? `${ls[0].name} added to the map.` : `${ls.length} layers added to the map.`, "success"); }} />}
      {georefOpen && <Georeference projectId={id} layers={layers} canEdit={canEdit} file={georefFile} layerId={georefLayer}
        view={map.current ? { center: map.current.getCenter().toArray() as [number, number], zoom: map.current.getZoom() } : null}
        onClose={() => { setGeorefOpen(false); setGeorefFile(null); setGeorefLayer(null); }}
        onDone={(l) => { setGeorefOpen(false); setGeorefFile(null); setGeorefLayer(null); setLayers((cur) => (cur.some((x) => x.id === l.id) ? cur.map((x) => (x.id === l.id ? l : x)) : [...cur, l])); setActiveId(l.id); if (l.bbox) zoomTo(l.bbox); }} />}
      {map.current && views.filter((v) => v.kind === "layout").map((v) => (
        <LayoutView key={layoutKeys.current[v.id] ?? v.id} embedded hidden={viewNow !== viewKey(v)} layoutId={isNewLayout(v.id) ? null : v.id}
          project={project} layers={layers} crs={crs} me={me} map={map.current!} canSave={canEdit && layoutsAllowed} gisMaps={maps} mapId={mapId} allLayers={allLayers}
          onClose={() => closeView(v, false)} onDirty={(d) => setDirtyLayouts((x) => (!!x[v.id] === d ? x : { ...x, [v.id]: d }))}
          onSaved={(l) => {
            setSavedLayouts((ls) => [l, ...ls.filter((x) => x.id !== l.id)]);
            if (l.id === v.id) return;
            layoutKeys.current[l.id] = layoutKeys.current[v.id] ?? v.id;          // the same tab, now holding the saved layout
            setViews((vs) => vs.map((x) => (x.kind === "layout" && x.id === v.id ? { kind: "layout", id: l.id } : x)));
            setViewNow(`layout:${l.id}`);
          }}
          onDeleted={(lid) => { setSavedLayouts((ls) => ls.filter((x) => x.id !== lid)); closeView(v, false); }} />
      ))}
      {dialog === "plan" && <Dialog title="Print studio" onClose={() => setDialog(null)}>
        <div className="ws-form"><p>The print studio (map sheets with a title, legend, scale bar, north arrow and grid, exported as PDF) are in PolyGIS Advanced.
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
