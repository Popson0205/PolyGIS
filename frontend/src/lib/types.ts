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
  geographic: boolean; missing?: boolean; res: [number, number]; res_m: [number, number]; bbox: [number, number, number, number]; bands: RasterBand[]; size_bytes: number;
}
export interface ClassBreak { min: number; max: number; color: string }
export interface Style {
  type: "single" | "categorized" | "graduated"; color: string; stroke: string; width: number; size: number; opacity: number;
  field?: string | null; categories?: Category[]; classes?: ClassBreak[]; method?: "quantile" | "equal" | "manual"; ramp?: string;
}
export interface Labels { field: string; size: number; color: string; halo: string }
export interface Layer {
  id: string; project_id: string; name: string; geometry_type: GeometryType; fields: Field[]; style: Style; labels: Labels | null;
  visible: boolean; position: number;
  source: { filename?: string; crs?: string | null; dropped?: number; drawn?: boolean; tool?: string; tool_name?: string; summary?: Record<string, unknown>;
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
}
export interface Row { id: number; props: Record<string, unknown> }
export interface Filter { field: string; op: "=" | "!=" | "<" | "<=" | ">" | ">=" | "contains" | "starts" | "empty" | "not_empty"; value?: string | number | boolean | null }
export interface Stats { field: string; type: FieldType; min?: number | null; max?: number | null; mean?: number | null; count?: number; quantiles?: number[]; values: { value: string | null; count: number }[]; distinct: number }
export interface Crs { epsg: number; name: string; kind?: string }
export interface CrsDef { epsg: number; name: string; proj4: string; geographic: boolean; unit: string }
export interface Selection { ids: number[]; count: number; bbox: [number, number, number, number] | null }
export interface Activity { actor_name: string; action: string; target_type: string | null; target_id: string | null; details: Record<string, unknown>; at: string }

export interface ToolParam {
  name: string; label: string; type: "layer" | "layers" | "number" | "bool" | "choice" | "field" | "fields" | "text" | "area" | "date";
  geometry?: GeometryType[]; unit?: string; min?: number; default?: unknown; choices?: [string, string][]; of?: string;
  numeric?: boolean; optional?: boolean; when?: Record<string, unknown>;
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
