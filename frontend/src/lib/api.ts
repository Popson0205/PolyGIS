import type { LayoutSettings } from "./layout";
import type { LayoutDoc } from "./layoutDoc";
import type { Activity, App, Crs, CrsDef, EarthJob, Field, FieldType, Filter, ImageryProvider, ImageryRequest, ImageryScene, Labels, Layer, Me, Project, RasterStyle, Row, Run, Selection, Stats, Style, Tool, VectorType, View } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public body?: Record<string, unknown>) { super(message); }
}
export const BASE = import.meta.env.VITE_API_URL ?? "/api";

async function handle<T>(res: Response, path: string): Promise<T> {
  if (!res.ok) {
    let p: Record<string, unknown> = {};
    try { p = await res.json(); } catch { /* not JSON */ }
    if (res.status === 401 && path !== "/me") window.dispatchEvent(new CustomEvent("gis:signed-out", { detail: p.detail }));
    throw new ApiError(res.status, String(p.detail ?? p.title ?? res.statusText), p.code as string | undefined, p);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/** Signed in by cookie; every change carries X-GeoEstate so another site's form cannot make one. */
export async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, { method, credentials: "same-origin",
      headers: { "X-GeoEstate": "1", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "Can't reach PolyGIS. Check your connection.");
  }
  return handle<T>(res, path);
}

export const signInUrl = (next = window.location.pathname + window.location.search) => `${BASE}/auth/sign-in?next=${encodeURIComponent(next)}`;
export const tileUrl = (layer: Layer) => `${window.location.origin}${BASE}/layers/${layer.id}/tiles/{z}/{x}/{y}.pbf?v=${layer.version}`;
export const rasterTileUrl = (layer: Layer) => `${window.location.origin}${BASE}/layers/${layer.id}/raster/{z}/{x}/{y}.png?v=${layer.version}`;
export const terrainTileUrl = (layer: Layer) => `${window.location.origin}${BASE}/layers/${layer.id}/raster/terrain/{z}/{x}/{y}.png?v=${layer.version}`;
export const rasterDownloadUrl = (layerId: string) => `${BASE}/layers/${layerId}/raster/download`;
export const exportUrl = (layerId: string, format: string, epsg = 4326, ids?: number[]) =>
  `${BASE}/layers/${layerId}/export?format=${format}&epsg=${epsg}${ids?.length ? `&ids=${ids.join(",")}` : ""}`;

export const api = {
  me: () => req<Me>("GET", "/me"),
  apps: () => req<App[]>("GET", "/apps"),
  signOut: () => req<void>("POST", "/auth/sign-out"),
  activity: () => req<Activity[]>("GET", "/activity"),

  projects: () => req<Project[]>("GET", "/projects"),
  project: (id: string) => req<Project>("GET", `/projects/${id}`),
  createProject: (b: { name: string; description?: string; display_epsg?: number }) => req<Project>("POST", "/projects", b),
  layouts: (projectId: string) => req<SavedLayout[]>("GET", `/projects/${projectId}/layouts`),
  createLayout: (projectId: string, name: string, settings: LayoutDoc) => req<SavedLayout>("POST", `/projects/${projectId}/layouts`, { name, settings }),
  updateLayout: (id: string, b: { name?: string; settings?: LayoutDoc }) => req<SavedLayout>("PATCH", `/layouts/${id}`, b),
  deleteLayout: (id: string) => req<void>("DELETE", `/layouts/${id}`),
  linkedSources: (projectId: string) => req<LinkedSource[]>("GET", `/projects/${projectId}/linked/sources`),
  addLinked: (projectId: string, b: { app: string; form_id?: string; status?: "approved" | "all" }) => req<Layer>("POST", `/projects/${projectId}/layers/linked`, b),
  refreshLinked: (layerId: string) => req<Layer>("POST", `/layers/${layerId}/refresh`),
  hubDatasets: (layerId: string) => req<HubDataset[]>("GET", `/layers/${layerId}/hub`),
  publishHub: (layerId: string, b: { title: string; description: string; license: string; tags: string[]; visibility: string; ids?: number[] }) => req<HubDataset & { version: number }>("POST", `/layers/${layerId}/hub`, b),
  publishHubVersion: (layerId: string, slug: string, ids?: number[]) => req<HubDataset & { version: number }>("POST", `/layers/${layerId}/hub/${slug}/versions`, { ids }),
  shares: (projectId: string) => req<Share[]>("GET", `/projects/${projectId}/shares`),
  createShare: (projectId: string, b: ShareIn) => req<Share>("POST", `/projects/${projectId}/shares`, b),
  updateShare: (id: string, b: Partial<ShareIn>) => req<Share>("PATCH", `/shares/${id}`, b),
  revokeShare: (id: string) => req<Share>("DELETE", `/shares/${id}`),
  updateProject: (id: string, b: { name?: string; description?: string; display_epsg?: number; view?: View }) => req<Project>("PATCH", `/projects/${id}`, b),
  deleteProject: (id: string) => req<void>("DELETE", `/projects/${id}`),

  importFile: async (projectId: string, file: File, epsg?: number): Promise<Layer[]> => {
    const form = new FormData();
    form.append("file", file);
    if (epsg) form.append("epsg", String(epsg));
    let res: Response;
    try {
      res = await fetch(`${BASE}/projects/${projectId}/layers/import`, { method: "POST", credentials: "same-origin", headers: { "X-GeoEstate": "1" }, body: form });
    } catch {
      throw new ApiError(0, "Can't reach PolyGIS. Check your connection.");
    }
    return handle<Layer[]>(res, "/import");
  },
  imageryProviders: () => req<ImageryProvider[]>("GET", "/imagery/providers"),
  searchImagery: (b: ImageryRequest) => req<{ count: number; scenes: ImageryScene[] }>("POST", "/imagery/search", b),
  fetchImagery: (projectId: string, b: ImageryRequest & { scene_ids: string[]; name?: string }) => req<Layer>("POST", `/projects/${projectId}/imagery`, b),
  newLayer: (projectId: string, b: { name: string; geometry_type: VectorType; fields: Field[] }) => req<Layer>("POST", `/projects/${projectId}/layers`, b),
  updateLayer: (id: string, b: { name?: string; visible?: boolean; style?: Style; labels?: Labels; clear_labels?: boolean }) => req<Layer>("PATCH", `/layers/${id}`, b),
  deleteLayer: (id: string) => req<void>("DELETE", `/layers/${id}`),
  order: (projectId: string, ids: string[]) => req<Layer[]>("PUT", `/projects/${projectId}/layer-order`, { layer_ids: ids }),
  fieldOp: (id: string, b: { op: "add" | "rename" | "delete"; name: string; to?: string; type?: FieldType }) => req<Layer>("POST", `/layers/${id}/fields`, b),
  rows: (id: string, b: { filters?: Filter[]; sort?: string | null; desc?: boolean; offset?: number; limit?: number; ids?: number[] | null }) =>
    req<{ total: number; rows: Row[] }>("POST", `/layers/${id}/rows`, b),
  stats: (id: string, field: string, classes = 5) => req<Stats>("GET", `/layers/${id}/stats?field=${encodeURIComponent(field)}&classes=${classes}`),
  select: (id: string, b: { filters?: Filter[]; geometry?: GeoJSON.Geometry; relation?: string; distance_m?: number; other_layer_id?: string; other_ids?: number[] | null }) =>
    req<Selection>("POST", `/layers/${id}/select`, b),

  feature: (layerId: string, fid: number) => req<GeoJSON.Feature>("GET", `/layers/${layerId}/features/${fid}`),
  features: (layerId: string, ids: number[]) => req<GeoJSON.FeatureCollection>("POST", `/layers/${layerId}/features/geojson`, ids),
  addFeature: (layerId: string, geometry: GeoJSON.Geometry, properties: Record<string, unknown> = {}) =>
    req<GeoJSON.Feature>("POST", `/layers/${layerId}/features`, { geometry, properties }),
  changeFeature: (layerId: string, fid: number, b: { geometry?: GeoJSON.Geometry; properties?: Record<string, unknown> }) =>
    req<GeoJSON.Feature>("PATCH", `/layers/${layerId}/features/${fid}`, b),
  deleteFeatures: (layerId: string, ids: number[]) => req<{ deleted: number }>("POST", `/layers/${layerId}/features/delete`, { ids }),
  calculate: (layerId: string, b: { field: string; value?: unknown; from_field?: string; ids?: number[] | null }) => req<{ changed: number }>("POST", `/layers/${layerId}/calculate`, b),
  undo: (layerId: string) => req<{ undone: string; feature_id: number }>("POST", `/layers/${layerId}/undo`),

  tools: () => req<Tool[]>("GET", "/tools"),
  models: () => req<GisModel[]>("GET", "/models"),
  createModel: (m: ModelDraft) => req<GisModel>("POST", "/models", m),
  updateModel: (id: string, m: Partial<ModelDraft>) => req<GisModel>("PATCH", `/models/${id}`, m),
  deleteModel: (id: string) => req<void>("DELETE", `/models/${id}`),
  runModel: (projectId: string, id: string, inputs: Record<string, string>, keep: "final" | "all") =>
    req<ModelRun>("POST", `/projects/${projectId}/models/${id}/run`, { inputs, keep }),
  runTool: (projectId: string, tool: string, params: Record<string, unknown>, output_name?: string) =>
    req<{ run: Run; layer: Layer }>("POST", `/projects/${projectId}/tools/${tool}`, { params, output_name: output_name || null }),
  rasterTools: () => req<Tool[]>("GET", "/raster-tools"),
  runRasterTool: (projectId: string, tool: string, params: Record<string, unknown>, output_name?: string) =>
    req<{ run: Run; layer: Layer }>("POST", `/projects/${projectId}/raster-tools/${tool}`, { params, output_name: output_name || null }),
  rasterStyle: (id: string, style: RasterStyle) => req<Layer>("PUT", `/layers/${id}/raster-style`, style),
  rasterValue: (id: string, lng: number, lat: number) => req<{ values: (number | null)[] | null; bands: string[] }>("GET", `/layers/${id}/raster/value?lng=${lng}&lat=${lat}`),
  histogram: (id: string, band: number) => req<{ counts: number[]; edges: number[] }>("GET", `/layers/${id}/raster/histogram?band=${band}`),
  earthTools: () => req<Tool[]>("GET", "/poly-earth/tools"),
  startEarth: (projectId: string, tool: string, params: Record<string, unknown>, output_name?: string) =>
    req<EarthJob>("POST", `/projects/${projectId}/poly-earth/${tool}`, { params, output_name: output_name || null }),
  earthJob: (id: string) => req<EarthJob>("GET", `/poly-earth/jobs/${id}`),
  stopEarth: (id: string) => req<EarthJob>("POST", `/poly-earth/jobs/${id}/stop`),
  runs: (projectId: string) => req<Run[]>("GET", `/projects/${projectId}/runs`),
  crs: (q = "") => req<Crs[]>("GET", `/crs${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  crsDef: (epsg: number) => req<CrsDef>("GET", `/crs/${epsg}`),
};

export interface SavedLayout { id: string; project_id: string; name: string; settings: LayoutDoc | Partial<LayoutSettings>; created_by_name: string; updated_by_name: string; updated_at: string }

export interface ShareIn { title: string; description?: string; layer_ids: string[]; view?: View; audience: "anyone" | "organization"; downloads: boolean; expires_in_days?: number | null }
export interface Share {
  id: string; project_id: string; token: string; title: string; description: string; layer_ids: string[]; view: View; audience: "anyone" | "organization";
  downloads: boolean; expires_at: string | null; revoked_at: string | null; views: number; last_viewed_at: string | null; created_by_name: string; created_at: string;
}
export interface PublicShare {
  title: string; description: string; project_name: string; view: View; layers: Layer[]; downloads: boolean; audience: "anyone" | "organization";
  expires_at: string | null; shared_by: string;
}
export const shareUrl = (token: string) => `${window.location.origin}/share/${token}`;
const pub = (token: string) => `${BASE}/public/shares/${encodeURIComponent(token)}`;
export const publicApi = {
  /** A shared map. Errors keep their status (401: sign in for an organization's link; 404 revoked; 410 ended). */
  open: async (token: string): Promise<PublicShare> => {
    let res: Response;
    try { res = await fetch(pub(token), { credentials: "same-origin" }); } catch { throw new ApiError(0, "Can't reach PolyGIS. Check your connection."); }
    if (!res.ok) { let p: Record<string, unknown> = {}; try { p = await res.json(); } catch { /* not JSON */ } throw new ApiError(res.status, String(p.detail ?? res.statusText), p.code as string | undefined); }
    return res.json();
  },
  tiles: (token: string, l: Layer) => `${window.location.origin}${pub(token)}/layers/${l.id}/tiles/{z}/{x}/{y}.pbf?v=${l.version}`,
  rasterTiles: (token: string, l: Layer) => `${window.location.origin}${pub(token)}/layers/${l.id}/raster/{z}/{x}/{y}.png?v=${l.version}`,
  feature: async (token: string, layerId: string, fid: number) => (await fetch(`${pub(token)}/layers/${layerId}/features/${fid}`, { credentials: "same-origin" })).json() as Promise<{ properties: Record<string, unknown> }>,
  rasterValue: async (token: string, layerId: string, lng: number, lat: number) =>
    (await fetch(`${pub(token)}/layers/${layerId}/raster/value?lng=${lng}&lat=${lat}`, { credentials: "same-origin" })).json() as Promise<{ values: (number | null)[] | null; bands: string[] }>,
  download: (token: string, layerId: string, format: string) => `${pub(token)}/layers/${layerId}/export?format=${format}`,
};
export interface HubDataset { slug: string; title: string; visibility: string; latest_version: number; feature_count: number; url: string; published_at?: string }
export interface LinkedSource { app: "land" | "farm" | "hub"; app_name: string; label: string; geometry_type: string; form_id?: string; features?: number; approved?: number; available: boolean; why: string | null }
export interface ModelInput { name: string; label: string; kinds: ("Point" | "LineString" | "Polygon" | "Raster")[] }
export interface ModelStep { id: string; tool: string; kind: "vector" | "raster"; params: Record<string, unknown>; output_name: string; keep?: boolean }
export interface ModelDraft { name: string; description: string; inputs: ModelInput[]; steps: ModelStep[] }
export interface GisModel extends ModelDraft { id: string; runs: number; created_by_name: string; updated_by_name: string; updated_at: string }
export interface ModelRun { status: "done" | "failed"; message?: string; failed_step?: string; ms?: number; layers: Layer[];
  steps: { id: string; tool: string; name: string; ms: number; layer_id?: string; features?: number | null; error?: string }[] }
