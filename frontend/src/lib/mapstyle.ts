import type { ExpressionSpecification, LayerSpecification, StyleSpecification } from "maplibre-gl";
import type { Layer, RasterStyle } from "./types";

/** Basemaps that may be used in a paid product without a key: OpenFreeMap (OpenStreetMap data) and Sentinel-2
 *  cloudless 2016 by EOX (CC BY 4.0). VITE_IMAGERY_URL can add licensed high-resolution imagery. */
export const GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
export const FONT = ["Noto Sans Regular"];
const raster = (url: string, attribution: string, maxzoom = 19): StyleSpecification => ({
  version: 8, glyphs: GLYPHS,
  sources: { basemap: { type: "raster", tiles: [url], tileSize: 256, attribution, maxzoom } },
  layers: [{ id: "basemap", type: "raster", source: "basemap" }],
});
const extraImagery = import.meta.env.VITE_IMAGERY_URL as string | undefined;
export const BASEMAPS: { id: string; name: string; style: string | StyleSpecification }[] = [
  { id: "streets", name: "Streets", style: "https://tiles.openfreemap.org/styles/liberty" },
  { id: "light", name: "Light grey", style: "https://tiles.openfreemap.org/styles/positron" },
  { id: "satellite", name: "Satellite (Sentinel-2, 10 m)", style: raster("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg",
    '<a href="https://s2maps.eu">Sentinel-2 cloudless – https://s2maps.eu by EOX IT Services GmbH</a> (Contains modified Copernicus Sentinel data 2016)', 15) },
  ...(extraImagery ? [{ id: "imagery", name: "Imagery (high resolution)", style: raster(extraImagery, import.meta.env.VITE_IMAGERY_ATTRIBUTION ?? "") }] : []),
  { id: "none", name: "No basemap", style: { version: 8, glyphs: GLYPHS, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#f4f6fa" } }] } },
];
export const basemapStyle = (id?: string) => (BASEMAPS.find((b) => b.id === id) ?? BASEMAPS[0]).style;

export const SELECT = "#00d5ff";     // the selection colour, as in ArcGIS Pro
export const src = (l: Layer) => `src-${l.id}`;
export const ids = (l: Layer) => {
  const base = `lyr-${l.id}`;
  return { raster: `${base}-raster`, fill: `${base}-fill`, line: `${base}-line`, circle: `${base}-circle`, label: `${base}-label`,
    selFill: `${base}-sel-fill`, selLine: `${base}-sel-line`, selCircle: `${base}-sel-circle` };
};
export const allIds = (l: Layer) => Object.values(ids(l));

/** The colour of each feature from the layer's style: one colour, a colour per category, or graduated classes. */
export function colour(l: Layer): ExpressionSpecification | string {
  const s = l.style;
  const f = s.field;
  if (s.type === "categorized" && f && s.categories?.length) {
    const pairs = s.categories.flatMap((c) => [String(c.value), c.color]);
    return ["match", ["to-string", ["coalesce", ["get", f], ""]], ...pairs, "#b8bfcc"] as unknown as ExpressionSpecification;
  }
  if (s.type === "graduated" && f && s.classes?.length) {
    const steps = s.classes.slice(1).flatMap((c) => [c.min, c.color]);
    return ["case", ["==", ["typeof", ["get", f]], "number"],
      ["step", ["get", f], s.classes[0].color, ...steps], "#b8bfcc"] as unknown as ExpressionSpecification;
  }
  return s.color;
}

/** MapLibre layers that draw one PolyGIS layer, its selection and its labels. */
export function mapLayers(l: Layer, selected: number[], hidden: number[] = []): LayerSpecification[] {
  const i = ids(l);
  if (l.geometry_type === "Raster") {
    const rs = l.style as unknown as RasterStyle;
    return [{ id: i.raster, type: "raster", source: src(l), layout: { visibility: l.visible ? "visible" : "none" },
      paint: { "raster-opacity": rs.opacity ?? 1, "raster-resampling": rs.mode === "classes" ? "nearest" : "linear", "raster-fade-duration": 0 } } as LayerSpecification];
  }
  const s = l.style;
  const vis = l.visible ? "visible" : "none";
  const base = { source: src(l), "source-layer": "features", layout: { visibility: vis } } as const;
  const notHidden: ExpressionSpecification | undefined = hidden.length ? ["!", ["in", ["id"], ["literal", hidden]]] : undefined;
  const sel: ExpressionSpecification = ["in", ["id"], ["literal", selected.length ? selected : [-1]]];
  const c = colour(l);
  const out: LayerSpecification[] = [];
  if (l.geometry_type === "Polygon") {
    out.push({ id: i.fill, type: "fill", ...base, ...(notHidden ? { filter: notHidden } : {}), paint: { "fill-color": c, "fill-opacity": s.opacity } } as LayerSpecification);
    out.push({ id: i.line, type: "line", ...base, ...(notHidden ? { filter: notHidden } : {}),
      paint: { "line-color": s.type === "single" ? s.stroke : c, "line-width": s.width } } as LayerSpecification);
    out.push({ id: i.selFill, type: "fill", ...base, filter: sel, paint: { "fill-color": SELECT, "fill-opacity": 0.25 } } as LayerSpecification);
    out.push({ id: i.selLine, type: "line", ...base, filter: sel, paint: { "line-color": SELECT, "line-width": Math.max(2.5, s.width + 1.5) } } as LayerSpecification);
  } else if (l.geometry_type === "LineString") {
    out.push({ id: i.line, type: "line", ...base, ...(notHidden ? { filter: notHidden } : {}),
      layout: { visibility: vis, "line-cap": "round", "line-join": "round" }, paint: { "line-color": c, "line-width": Math.max(0.5, s.width), "line-opacity": s.opacity } } as LayerSpecification);
    out.push({ id: i.selLine, type: "line", ...base, filter: sel, paint: { "line-color": SELECT, "line-width": Math.max(3, s.width + 2) } } as LayerSpecification);
  } else {
    out.push({ id: i.circle, type: "circle", ...base, ...(notHidden ? { filter: notHidden } : {}),
      paint: { "circle-color": c, "circle-radius": s.size / 2 + 1, "circle-opacity": s.opacity, "circle-stroke-color": s.stroke, "circle-stroke-width": Math.min(2, s.width) } } as LayerSpecification);
    out.push({ id: i.selCircle, type: "circle", ...base, filter: sel,
      paint: { "circle-color": "rgba(0,0,0,0)", "circle-radius": s.size / 2 + 4, "circle-stroke-color": SELECT, "circle-stroke-width": 2.5 } } as LayerSpecification);
  }
  if (l.labels) {
    const lb = l.labels;
    out.push({ id: i.label, type: "symbol", ...base,
      layout: { visibility: vis, "text-field": ["to-string", ["coalesce", ["get", lb.field], ""]], "text-font": FONT, "text-size": lb.size,
        "symbol-placement": l.geometry_type === "LineString" ? "line" : "point", "text-offset": l.geometry_type === "Point" ? [0, 1.1] : [0, 0],
        "text-anchor": l.geometry_type === "Point" ? "top" : "center", "text-max-width": 10 },
      paint: { "text-color": lb.color, "text-halo-color": lb.halo, "text-halo-width": 1.4 } } as LayerSpecification);
  }
  return out;
}

/** Ten colours for categories, and ramps for graduated classes (ColorBrewer). */
export const CATEGORY_COLOURS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
  "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5", "#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5"];
export const RAMPS: Record<string, string[]> = {
  Greens: ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
  Blues: ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
  Reds: ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
  Viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
  "Red to green": ["#d7191c", "#fdae61", "#ffffbf", "#a6d96a", "#1a9641"],
};
/** n colours spread along a ramp. */
export function rampColours(name: string, n: number): string[] {
  const r = RAMPS[name] ?? RAMPS.Greens;
  if (n <= 1) return [r[r.length - 1]];
  return Array.from({ length: n }, (_, k) => {
    const t = (k / (n - 1)) * (r.length - 1);
    const a = Math.floor(t), b = Math.min(r.length - 1, a + 1), w = t - a;
    const p = (h: string) => [1, 3, 5].map((j) => parseInt(h.slice(j, j + 2), 16));
    const [x, y] = [p(r[a]), p(r[b])];
    return "#" + x.map((v, j) => Math.round(v + (y[j] - v) * w).toString(16).padStart(2, "0")).join("");
  });
}

/** Class breaks: equal intervals, or quantiles from the server's statistics. */
export function breaks(method: "equal" | "quantile", min: number, max: number, n: number, quantiles?: number[]): number[] {
  if (method === "quantile" && quantiles && quantiles.length === n + 1) return quantiles;
  return Array.from({ length: n + 1 }, (_, k) => min + ((max - min) * k) / n);
}
