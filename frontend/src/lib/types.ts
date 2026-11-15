export type Role = "admin" | "editor" | "viewer";
export interface Me {
  id: string; name: string; email: string; org_id: string | null; org_name: string | null; org_role: string | null; role: Role;
  staff_role: string | null; licence: string | null; can_write: boolean; features: string[]; geohub_url: string;
  product: { id: string; name: string; plan: string; plan_name: string; status: string; subscription_ends: string | null; grace_ends: string | null } | null;
}
export interface App { product: string; name: string; status: string; url: string | null; current: boolean }

export type GeometryType = "Point" | "LineString" | "Polygon" | "Raster";
export type VectorType = Exclude<GeometryType, "Raster">;
export type FieldType = "string" | "integer" | "number" | "boolean" | "date";
export interface Field { name: string; type: FieldType }
export interface Category { value: string; color: string; label?: string }
export interface RasterCategory { value: number; color: string; label?: string | null; hidden?: boolean }
export interface RasterStyle {
  type: "raster"; mode: "ramp" | "rgb" | "classes"; band: number; bands: [number, number, number]; min?: number | null; max?: number | null;
  mins: [number, number, number]; maxs: [number, number, number]; ramp: string; invert?: boolean; categories: RasterCategory[]; opacity: number;
}
export interface RasterBand { band: number; name: string; min?: number; max?: number; mean?: number; std?: number; p2?: number; p98?: number; values?: number[] }
export interface RasterMeta {
  key: string; width: number; height: number; count: number; dtype: string; nodata: number | null; crs: string; epsg: number | null; crs_name: string;
  geographic: boolean; missing?: boolean; unplaced?: boolean; res: [number, number]; res_m: [number, number]; bbox: [number, number, number, number]; bands: RasterBand[]; size_bytes: number;
}
export interface ClassBreak { min: number; max: number; color: string; label?: string }
/** Military symbols on a point layer: the code (SIDC) from a field, or one for all, and the text beside each symbol. */
export interface MilStyle { sidc: string; sidc_field?: string | null; designation_field?: string | null; higher_field?: string | null; size: number }
export interface Style {
  type: "single" | "categorized" | "graduated" | "milsymbol"; mil?: MilStyle | null; color: string; stroke: string; width: number; size: number; opacity: number;
  field?: string | null; categories?: Category[]; classes?: ClassBreak[]; method?: "quantile" | "equal" | "manual"; ramp?: string;
  /** Polygons drawn in 3D: the height from a field (times scale) or a fixed height, in metres. */
  extrude?: { field: string | null; height: number; scale: number; base: number } | null;
}
/** A label class (ArcGIS-style): Class (what it says, when), Symbol (how it looks), Position (where). Only `field` is
 *  required; labels saved before the rest existed take the defaults. */
export interface Labels {
  field: string; size: number; color: string; halo: string;
  expression?: string; min_zoom?: number; max_zoom?: number;
  font?: "regular" | "bold" | "italic"; opacity?: number; halo_width?: number; halo_blur?: number; case?: "normal" | "upper" | "lower";
  letter_spacing?: number; rotation?: number; rotation_field?: string | null;
  anchor?: "auto" | "center" | "top" | "bottom" | "left" | "right" | "top-left" | "top-right" | "bottom-left" | "bottom-right";
  offset?: number; line_placement?: "along" | "horizontal"; line_spacing?: number; wrap?: number; allow_overlap?: boolean;
}
export interface Layer {
  id: string; project_id: string; name: string; geometry_type: GeometryType; fields: Field[]; style: Style; labels: Labels | null;
  visible: boolean; position: number;
  map_id?: string | null;              // the map the layer is in
  data_id?: string | null;             // the layer whose data this one shows (the same data in another map)
  source: { filename?: string; crs?: string | null; dropped?: number; drawn?: boolean; tool?: string; tool_name?: string; summary?: Record<string, unknown>;
    linked?: { app: "land" | "farm" | "hub"; app_name: string; kind: string; label: string; form_id?: string; status?: string; refreshed_at?: string };
    imagery?: { provider: ImageryProviderId; dataset: ImageryDatasetId; bands: string[]; start: string | null; end: string | null; max_cloud: number | null; scenes: string[]; cell_m: number } };
  raster?: RasterMeta | null; feature_count: number; bbox: [number, number, number, number] | null; version: number; updated_at: string;
}
export type ImageryProviderId = "planetary-computer" | "earth-search" | "gee";
export type ImageryDatasetId = "sentinel2" | "landsat" | "sentinel1" | "copdem" | "nasadem" | "worldcover" | "chirps";
export interface ImageryBand { id: string; label: string; res: number; default: boolean }
export interface ImageryDataset { id: ImageryDatasetId; name: string; kind: "optical" | "radar" | "static" | "total"; dated: boolean; cloud: boolean; scenes: boolean; bands: ImageryBand[] }
export interface ImageryProvider { id: ImageryProviderId; name: string; ready: boolean; note: string; datasets: ImageryDataset[] }
export interface ImageryScene { id: string; datetime: string | null; cloud: number | null; platform: string | null }
export interface ImageryRequest { provider: ImageryProviderId; dataset: ImageryDatasetId; bands: string[]; bbox: [number, number, number, number]; start?: string; end?: string; max_cloud: number }
export interface View { center?: [number, number]; zoom?: number; bearing?: number; pitch?: number; basemap?: string }
export interface Project {
  id: string; name: string; description: string; view: View; display_epsg: number; created_by: string | null; created_by_name: string;
  created_at: string; updated_at: string; layer_count: number; layers?: Layer[];
  maps?: GisMap[]; active_map_id?: string | null;
}
/** One map of a project, as in ArcGIS Pro: its own layers, view and basemap. */
export interface GisMap { id: string; project_id: string; name: string; position: number; view: View; layer_count: number; created_at: string; updated_at: string }
export interface Row { id: number; props: Record<string, unknown> }
export interface Filter { field: string; op: "=" | "!=" | "<" | "<=" | ">" | ">=" | "contains" | "starts" | "empty" | "not_empty"; value?: string | number | boolean | null }
export interface Stats { field: string; type: FieldType; min?: number | null; max?: number | null; mean?: number | null; count?: number; quantiles?: number[]; values: { value: string | null; count: number }[]; distinct: number }
export interface Crs { epsg: number; name: string; kind?: string }
export interface CrsDef { epsg: number; name: string; proj4: string; geographic: boolean; unit: string }
export interface Selection { ids: number[]; count: number; bbox: [number, number, number, number] | null }
export interface Activity { actor_name: string; action: string; target_type: string | null; target_id: string | null; details: Record<string, unknown>; at: string }

export interface ToolParam {
  name: string; label: string; type: "layer" | "layers" | "number" | "bool" | "choice" | "field" | "fields" | "text" | "area" | "date" | "extent";
  geometry?: GeometryType[]; unit?: string; min?: number; default?: unknown; choices?: [string, string][]; of?: string;
  numeric?: boolean; optional?: boolean; when?: Record<string, unknown>;
  lines?: boolean;                   // a text box of several lines
}
export interface Tool { id: string; name: string; group: string; about: string; params: ToolParam[]; kind?: "raster" | "earth" }
export interface EarthChart { title: string; unit: string; x: string[]; series: { name: string; values: (number | null)[] }[] }
export interface EarthReport { classes: string[]; matrix: number[][]; overall: number | null; kappa: number; producers: (number | null)[]; users: (number | null)[]; note?: string }
export interface EarthJob {
  id: string; tool: string; tool_name: string; status: "queued" | "running" | "stopping" | "done" | "failed" | "stopped"; message: string; progress: number;
  layers: Layer[]; chart: EarthChart | null; report: EarthReport | null; notes: string[]; error: string | null; ms: number | null; started?: number;
}
export interface Run {
  id: number; tool: string; params: Record<string, unknown>; output_layer_id: string | null; output_name: string | null;
  status: "done" | "failed"; message: string | null; feature_count: number | null; ms: number | null; actor_name: string; at: string;
}

/** Georeferencing: a control point matches an image pixel (col, row from the top-left corner) to a place on the map. */
export interface GeorefPoint { id: string; col: number; row: number; lng: number; lat: number; on: boolean }
export type GeorefMethod = "similarity" | "affine" | "poly2" | "poly3" | "tps";
export interface GeorefSource {
  id: string; project_id: string; name: string; width: number; height: number; band_count: number; layer_id: string | null; had_crs: string | null;
  points: GeorefPoint[]; method: GeorefMethod; methods: { id: GeorefMethod; label: string; min_points: number }[]; updated_at: string;
}
export interface GeorefFit {
  method: GeorefMethod; crs: string; rmse_m: number | null; rmse_px: number | null; cell_m: number; exact: boolean;
  points: { id: string; dx_m: number; dy_m: number; error_m: number; error_px: number }[];
  preview?: { url: string; coordinates: [number, number][] };
}

/** Scenarios: named what-if settings for a simulation, run into a raster layer (a band per time step or level). */
export type ScenarioKind = "suitability" | "flood" | "land_change" | "spread" | "raster_forecast" | "value_forecast";
export interface ScenarioStep { label: string; [k: string]: unknown }
export interface Scenario {
  id: string; project_id: string; kind: ScenarioKind; name: string; notes: string; params: Record<string, unknown>;
  status: "draft" | "ready" | "failed"; summary: (Record<string, unknown> & { steps?: ScenarioStep[] }) | null; error: string | null;
  layer_id: string | null; ran_at: string | null; run_ms: number | null; created_at: string; updated_at: string;
}

/** A line of sight over an elevation model: the profile, and what can be seen. */
export interface SightResult {
  distance_m: number; target_visible: boolean; target_on_dem: boolean; observer_ground: number | null; observer_eye: number | null;
  target_ground: number | null; blocked_at_m: number | null; visible_share: number;
  profile: { d: number; z: number | null; seen: boolean; sight: number | null }[];
  segments: GeoJSON.FeatureCollection;
}

/** What lies inside an area of interest (an intelligence summary). */
export interface AreaBrief {
  name: string; project: string; area_km2: number; perimeter_km: number;
  centre: { lng: number; lat: number } & Record<string, string | number | null>;
  extent: { south_west: string | null; north_east: string | null };
  ground: null | { dem: string; covered: boolean; share_covered?: number; min_m?: number; max_m?: number; mean_m?: number; relief_m?: number;
    mean_slope_deg?: number; steep_share?: number; very_steep_share?: number; highest?: { elevation_m: number; position: string; lng: number; lat: number } };
  layers: { id: string; name: string; kind: "Point" | "LineString" | "Polygon"; features: number; measure: number | null; unit: string | null }[];
  units: { layer: string; sidc: string; affiliation: string; designation: string | null; higher: string | null; position: string }[];
  unit_counts: Record<string, number>;
  geometry: GeoJSON.Geometry;
}

/** A feed of live positions: own devices and apps, aircraft (ADS-B), ships (AIS) or a Geohub form's submissions. */
export interface Tracker {
  id: string; project_id: string; name: string; kind: "device" | "adsb" | "ais" | "geohub"; kind_name: string;
  config: { bbox?: [number, number, number, number]; source?: string; form_id?: string; form_label?: string; geofence_layer_id?: string | null;
    geofence_field?: string; keep_days?: number; trail_minutes?: number };
  secret_set: string[]; layer_id: string | null; active: boolean; last_at: string | null; refreshed_at: string | null; last_error: string | null;
  devices: number; ingest_url?: string; created_at: string;
}
export interface TrackAlert { id: number; tracker_id: string; tracker: string; device: string; name: string | null; kind: "enter" | "exit"; zone: string; ts: string; x: number; y: number }
export interface TrackingLive { positions: GeoJSON.FeatureCollection; trails: GeoJSON.FeatureCollection; alerts: TrackAlert[]; at: string }
