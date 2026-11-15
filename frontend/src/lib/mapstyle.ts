import type { ExpressionSpecification, LayerSpecification, StyleSpecification } from "maplibre-gl";
import { imageExpr } from "./milsym";
import type { Labels, Layer, RasterStyle } from "./types";

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
/** The organization's own imagery services (tile services added as imagery providers), as basemaps: "provider:<id>".
 *  Their tiles come through PolyGIS, which adds the provider's key. */
const PROVIDED: { id: string; name: string; style: StyleSpecification }[] = [];
export function setProviderBasemaps(list: { id: string; name: string; tiles: string; attribution?: string; maxzoom?: number }[]) {
  PROVIDED.splice(0, PROVIDED.length, ...list.map((p) => ({ id: `provider:${p.id}`, name: p.name, style: raster(p.tiles, p.attribution ?? "", p.maxzoom ?? 20) })));
}
export const allBasemaps = () => [...BASEMAPS.filter((b) => b.id !== "none"), ...PROVIDED, ...BASEMAPS.filter((b) => b.id === "none")];
export const basemapStyle = (id?: string) => (allBasemaps().find((b) => b.id === id) ?? BASEMAPS[0]).style;

export const SELECT = "#00d5ff";     // the selection colour, as in ArcGIS Pro
export const src = (l: Layer) => `src-${l.id}`;
/** Labels read the same tiles through a source of their own: when the label font cannot load, MapLibre fails the
 *  whole tile for every layer on that source, and the features would vanish with their labels. */
export const labelSrc = (l: Layer) => `src-${l.id}-labels`;
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
/** `flat`: draw polygons on the ground even when their style stands them up (the 2D view). */
export function mapLayers(l: Layer, selected: number[], hidden: number[] = [], flat = false): LayerSpecification[] {
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
  if (l.geometry_type === "Polygon" && s.extrude && !flat) {
    // in 3D: the polygons stand up to their height; the outline and selection stay on the ground
    const e = s.extrude;
    const height: ExpressionSpecification | number = e.field ? ["*", ["to-number", ["coalesce", ["get", e.field], 0], 0], e.scale || 1] : e.height;
    out.push({ id: i.fill, type: "fill-extrusion", ...base, ...(notHidden ? { filter: notHidden } : {}),
      paint: { "fill-extrusion-color": c, "fill-extrusion-opacity": Math.max(0.3, s.opacity), "fill-extrusion-height": height, "fill-extrusion-base": e.base || 0,
        "fill-extrusion-vertical-gradient": true } } as LayerSpecification);
    out.push({ id: i.line, type: "line", ...base, ...(notHidden ? { filter: notHidden } : {}),
      paint: { "line-color": s.type === "single" ? s.stroke : c, "line-width": s.width, "line-opacity": 0.6 } } as LayerSpecification);
    out.push({ id: i.selLine, type: "line", ...base, filter: sel, paint: { "line-color": SELECT, "line-width": Math.max(2.5, s.width + 1.5) } } as LayerSpecification);
  } else if (l.geometry_type === "Polygon") {
    out.push({ id: i.fill, type: "fill", ...base, ...(notHidden ? { filter: notHidden } : {}), paint: { "fill-color": c, "fill-opacity": s.opacity } } as LayerSpecification);
    out.push({ id: i.line, type: "line", ...base, ...(notHidden ? { filter: notHidden } : {}),
      paint: { "line-color": s.type === "single" ? s.stroke : c, "line-width": s.width } } as LayerSpecification);
    out.push({ id: i.selFill, type: "fill", ...base, filter: sel, paint: { "fill-color": SELECT, "fill-opacity": 0.25 } } as LayerSpecification);
    out.push({ id: i.selLine, type: "line", ...base, filter: sel, paint: { "line-color": SELECT, "line-width": Math.max(2.5, s.width + 1.5) } } as LayerSpecification);
  } else if (l.geometry_type === "LineString") {
    out.push({ id: i.line, type: "line", ...base, ...(notHidden ? { filter: notHidden } : {}),
      layout: { visibility: vis, "line-cap": "round", "line-join": "round" }, paint: { "line-color": c, "line-width": Math.max(0.5, s.width), "line-opacity": s.opacity } } as LayerSpecification);
    out.push({ id: i.selLine, type: "line", ...base, filter: sel, paint: { "line-color": SELECT, "line-width": Math.max(3, s.width + 2) } } as LayerSpecification);
  } else if (s.type === "milsymbol") {
    // military symbols: a picture per code, made when the map first needs it (milsym.ts); the layer keeps the circle's id
    // so that clicking and selecting work as for any point layer
    out.push({ id: i.circle, type: "symbol", ...base, ...(notHidden ? { filter: notHidden } : {}),
      layout: { visibility: vis, "icon-image": imageExpr(l), "icon-allow-overlap": true, "icon-ignore-placement": true, "icon-anchor": "center" },
      paint: { "icon-opacity": s.opacity ?? 1 } } as LayerSpecification);
    out.push({ id: i.selCircle, type: "circle", ...base, filter: sel,
      paint: { "circle-color": "rgba(0,0,0,0)", "circle-radius": (s.mil?.size ?? 36) / 2 + 6, "circle-stroke-color": SELECT, "circle-stroke-width": 2.5 } } as LayerSpecification);
  } else {
    out.push({ id: i.circle, type: "circle", ...base, ...(notHidden ? { filter: notHidden } : {}),
      paint: { "circle-color": c, "circle-radius": s.size / 2 + 1, "circle-opacity": s.opacity, "circle-stroke-color": s.stroke, "circle-stroke-width": Math.min(2, s.width) } } as LayerSpecification);
    out.push({ id: i.selCircle, type: "circle", ...base, filter: sel,
      paint: { "circle-color": "rgba(0,0,0,0)", "circle-radius": s.size / 2 + 4, "circle-stroke-color": SELECT, "circle-stroke-width": 2.5 } } as LayerSpecification);
  }
  if (l.labels) out.push({ id: i.label, ...base, ...labelLayer(l, l.labels), source: labelSrc(l) } as LayerSpecification);   // the label layout replaces base's
  return out;
}

/** The fonts the glyph server has, by font style. */
export const FONTS: Record<NonNullable<Labels["font"]>, string> = { regular: "Noto Sans Regular", bold: "Noto Sans Bold", italic: "Noto Sans Italic" };

/** A label's text: the field, or an expression like "{name} ({population})", in the chosen case. */
export function labelText(lb: Labels): ExpressionSpecification {
  const val = (f: string): ExpressionSpecification => ["to-string", ["coalesce", ["get", f.trim()], ""]];
  let expr: ExpressionSpecification;
  const t = (lb.expression ?? "").trim();
  if (!t) expr = val(lb.field);
  else {
    const parts: (string | ExpressionSpecification)[] = [];
    let last = 0;
    for (const m of t.matchAll(/\{([^{}]+)\}/g)) {
      if (m.index! > last) parts.push(t.slice(last, m.index));
      parts.push(val(m[1]));
      last = m.index! + m[0].length;
    }
    if (last < t.length) parts.push(t.slice(last));
    expr = parts.length === 1 && typeof parts[0] !== "string" ? parts[0] : ["concat", ...parts] as unknown as ExpressionSpecification;
  }
  return lb.case === "upper" ? ["upcase", expr] : lb.case === "lower" ? ["downcase", expr] : expr;
}

/** The MapLibre symbol layer (all but its id and source) for a layer's label class. */
export function labelLayer(l: Pick<Layer, "geometry_type" | "visible">, lb: Labels) {
  const point = l.geometry_type === "Point", line = l.geometry_type === "LineString";
  const size = lb.size || 12;
  const em = (px: number) => px / size;
  const anchor = lb.anchor ?? "auto";
  const offsetEm = em(lb.offset ?? 4) + (point ? 0.4 : 0);
  const at: Record<string, [number, number]> = { center: [0, 0], top: [0, -1], bottom: [0, 1], left: [-1, 0], right: [1, 0],
    "top-left": [-0.7, -0.7], "top-right": [0.7, -0.7], "bottom-left": [-0.7, 0.7], "bottom-right": [0.7, 0.7] };
  // ArcGIS's "top" means the label sits above the point: MapLibre names the anchor on the label (its bottom)
  const flip: Record<string, string> = { center: "center", top: "bottom", bottom: "top", left: "right", right: "left",
    "top-left": "bottom-right", "top-right": "bottom-left", "bottom-left": "top-right", "bottom-right": "top-left" };
  const layout: Record<string, unknown> = {
    visibility: l.visible ? "visible" : "none", "text-field": labelText(lb), "text-font": [FONTS[lb.font ?? "regular"] ?? FONT[0]], "text-size": size,
    "text-max-width": lb.wrap === 0 ? 1000 : (lb.wrap ?? 10), "text-letter-spacing": lb.letter_spacing ?? 0,
    "text-allow-overlap": !!lb.allow_overlap, "text-ignore-placement": !!lb.allow_overlap,
    "text-rotate": lb.rotation_field ? ["+", ["to-number", ["coalesce", ["get", lb.rotation_field], 0], 0], lb.rotation ?? 0] : (lb.rotation ?? 0),
  };
  if (line && (lb.line_placement ?? "along") === "along") {
    Object.assign(layout, { "symbol-placement": "line", "symbol-spacing": lb.line_spacing ?? 250, "text-rotation-alignment": "map", "text-keep-upright": true });
  } else if (line) {
    Object.assign(layout, { "symbol-placement": "line-center", "text-rotation-alignment": "viewport" });
  } else if (point && anchor === "auto") {
    Object.assign(layout, { "symbol-placement": "point", "text-variable-anchor": ["top", "bottom", "right", "left"], "text-radial-offset": offsetEm, "text-justify": "auto" });
  } else if (point) {
    const [dx, dy] = at[anchor];
    Object.assign(layout, { "symbol-placement": "point", "text-anchor": flip[anchor], "text-offset": [dx * offsetEm, dy * offsetEm] });
  } else {
    Object.assign(layout, { "symbol-placement": "point", "text-anchor": anchor === "auto" ? "center" : flip[anchor] });
  }
  return {
    type: "symbol" as const, minzoom: lb.min_zoom ?? 0, maxzoom: lb.max_zoom ?? 24, layout,
    paint: { "text-color": lb.color, "text-opacity": lb.opacity ?? 1, "text-halo-color": lb.halo, "text-halo-width": lb.halo_width ?? 1.4, "text-halo-blur": lb.halo_blur ?? 0 },
  };
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

/** Terrain for the 3D view: an elevation model in the project, or the open Terrain Tiles (Mapzen, AWS Open Data). */
export const GLOBAL_TERRAIN = { tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"], maxzoom: 15,
  attribution: '<a href="https://registry.opendata.aws/terrain-tiles/">Terrain Tiles</a> (Mapzen, AWS Open Data)' };
