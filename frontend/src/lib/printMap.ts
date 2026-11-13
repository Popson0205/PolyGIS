/** The map picture for a print layout: the live map's style (basemap and layers as they are drawn now, without the
 *  selection and the drawing tools) rendered off screen at the layout's scale and resolution. */
import maplibregl, { type LayerSpecification, type StyleSpecification } from "maplibre-gl";
import { zoomForScale } from "./layout";

/** The live map's style, ready to print: no selection highlight, no drawing or measuring layers. */
export function printableStyle(style: StyleSpecification): StyleSpecification {
  const keep = style.layers.filter((l) => !/-sel-/.test(l.id) && !l.id.startsWith("gis-draw") && !l.id.startsWith("td-"))
    .map((l) => (l.type !== "fill-extrusion" ? l : {                     // polygons drawn in 3D are printed on the ground
      id: l.id, type: "fill", source: l.source, "source-layer": l["source-layer"], ...(l.filter ? { filter: l.filter } : {}), layout: { visibility: l.layout?.visibility ?? "visible" },
      paint: { "fill-color": l.paint?.["fill-extrusion-color"] ?? "#888", "fill-opacity": l.paint?.["fill-extrusion-opacity"] ?? 1 } } as LayerSpecification));
  const used = new Set(keep.map((l) => ("source" in l ? (l.source as string) : "")).filter(Boolean));
  const sources = Object.fromEntries(Object.entries(style.sources).filter(([k]) => used.has(k)));
  const { terrain: _t, sky: _s, ...flat } = style as StyleSpecification & { sky?: unknown };   // a sheet is printed flat, from above
  return { ...flat, layers: keep, sources } as StyleSpecification;
}

export interface PrintRequest { style: StyleSpecification; center: [number, number]; scale: number; cssW: number; cssH: number; ratio: number }

/** Draws the map and gives it back as a canvas (and a JPEG for the PDF). Waits until every tile has arrived, up to a
 *  minute; a map that is still loading after that is used as it is, and `complete` says so. */
export async function renderMap(r: PrintRequest, signal?: AbortSignal): Promise<{ canvas: HTMLCanvasElement; jpeg: () => string; complete: boolean }> {
  const holder = document.createElement("div");
  holder.setAttribute("aria-hidden", "true");             // drawn off screen for the sheet only
  Object.assign(holder.style, { position: "fixed", left: "-20000px", top: "0", width: `${r.cssW}px`, height: `${r.cssH}px`, pointerEvents: "none" });
  document.body.appendChild(holder);
  const map = new maplibregl.Map({
    container: holder, style: r.style, center: r.center, zoom: zoomForScale(r.scale, r.center[1]), bearing: 0, pitch: 0,
    interactive: false, attributionControl: false, pixelRatio: r.ratio, preserveDrawingBuffer: true, fadeDuration: 0,
    maxCanvasSize: [16384, 16384], maxZoom: 24,
  } as maplibregl.MapOptions & { maxCanvasSize: [number, number] });
  try {
    const complete = await new Promise<boolean>((resolve, reject) => {
      const t = setTimeout(() => resolve(false), 60000);
      map.once("idle", () => { clearTimeout(t); resolve(true); });
      map.on("error", () => undefined);            // a missing tile is drawn as nothing, not a failure
      signal?.addEventListener("abort", () => { clearTimeout(t); reject(new DOMException("Stopped", "AbortError")); });
    });
    const src = map.getCanvas();
    const canvas = document.createElement("canvas");
    canvas.width = src.width; canvas.height = src.height;
    canvas.getContext("2d")!.drawImage(src, 0, 0);
    return { canvas, jpeg: () => canvas.toDataURL("image/jpeg", 0.92), complete };
  } finally {
    map.remove();
    holder.remove();
  }
}
