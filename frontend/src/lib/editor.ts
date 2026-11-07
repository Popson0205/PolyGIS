import area from "@turf/area";
import length from "@turf/length";
import type { Map as MapLibre } from "maplibre-gl";
import { TerraDraw, TerraDrawLineStringMode, TerraDrawPointMode, TerraDrawPolygonMode, TerraDrawRenderMode, TerraDrawSelectMode,
  type GeoJSONStoreFeatures, type SnapToCustom } from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import { snapPoint } from "./snap";

export type DrawKind = "point" | "linestring" | "polygon";
const INK = "#1b2b5e", GO = "#00a0c8";

/** Drawing and reshaping on the map (Terra Draw). One per map; started when editing or measuring begins.
 *  `snapTo` gives the features to snap to at full precision, and whether snapping is on. */
export class Editor {
  private draw: TerraDraw;
  private onDone?: (g: GeoJSON.Geometry) => void;
  private onMeasure?: (text: { length?: number; area?: number }) => void;
  private shapeId: string | null = null;
  constructor(private map: MapLibre, snapTo: () => { on: boolean; features: GeoJSON.Feature[] }) {
    const custom: SnapToCustom = (event) => {
      const s = snapTo();
      if (!s.on) return undefined;
      const p = snapPoint(event.containerX, event.containerY, s.features,
        (lng, lat) => this.map.project([lng, lat]), (x, y) => this.map.unproject([x, y]), 12);
      return p ?? undefined;
    };
    const snapping = { toCustom: custom, toCoordinate: true };
    const style = { fillColor: GO, outlineColor: INK, outlineWidth: 2, closingPointColor: INK, closingPointWidth: 4, snappingPointColor: "#ff00aa", snappingPointWidth: 5 } as const;
    this.draw = new TerraDraw({
      adapter: new TerraDrawMapLibreGLAdapter({ map, prefixId: "gis-draw" }),
      modes: [
        new TerraDrawPointMode({ styles: { pointColor: GO, pointWidth: 6, pointOutlineColor: INK, pointOutlineWidth: 2 } }),
        new TerraDrawLineStringMode({ snapping, styles: { lineStringColor: INK, lineStringWidth: 3, closingPointColor: INK, closingPointWidth: 4, snappingPointColor: "#ff00aa", snappingPointWidth: 5 } }),
        new TerraDrawPolygonMode({ snapping, styles: { fillColor: GO, fillOpacity: 0.25, outlineColor: INK, outlineWidth: 2, closingPointColor: style.closingPointColor, closingPointWidth: 4, snappingPointColor: "#ff00aa", snappingPointWidth: 5 } }),
        new TerraDrawSelectMode({
          flags: Object.fromEntries((["point", "linestring", "polygon"] as const).map((m) => [m, { feature: { draggable: true,
            coordinates: { draggable: true, deletable: true, midpoints: m !== "point", snappable: snapping } } }])),
          styles: { selectedPolygonColor: GO, selectedPolygonOutlineColor: INK, selectedLineStringColor: INK, selectionPointColor: INK, midPointColor: GO, selectedPointColor: GO },
        }),
        new TerraDrawRenderMode({ modeName: "static", styles: {} }),
      ],
    });
    this.draw.on("finish", (id) => {
      const f = this.draw.getSnapshotFeature(id);
      if (!f) return;
      if (this.onMeasure) { this.measure(f); return; }
      if (this.onDone && !this.shapeId) {
        this.onDone(f.geometry as GeoJSON.Geometry);
        this.draw.removeFeatures([id]);
      }
    });
    this.draw.on("change", (changed) => {
      if (!this.onMeasure) return;
      for (const id of changed) { const f = this.draw.getSnapshotFeature(id); if (f) this.measure(f); }
    });
  }

  private ensure() { if (!this.draw.enabled) this.draw.start(); }
  private measure(f: GeoJSONStoreFeatures) {
    if (f.geometry.type === "LineString" && (f.geometry.coordinates as unknown[]).length > 1) this.onMeasure?.({ length: length(f as GeoJSON.Feature, { units: "meters" }) });
    if (f.geometry.type === "Polygon") {
      const ring = f.geometry.coordinates[0] as GeoJSON.Position[];
      const perim = length({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: ring } }, { units: "meters" });
      this.onMeasure?.({ area: area(f as GeoJSON.Feature), length: perim });
    }
  }

  /** Draw new shapes; each finished one is handed to `done` and cleared from the sketch. */
  create(kind: DrawKind, done: (g: GeoJSON.Geometry) => void) {
    this.ensure();
    this.reset();
    this.onDone = done;
    this.draw.setMode(kind);
  }

  /** Reshape one feature: drag its vertices (and the midpoints to add one); right-click a vertex to remove it. */
  reshape(geometry: GeoJSON.Geometry): boolean {
    const mode = geometry.type === "Point" ? "point" : geometry.type === "LineString" ? "linestring" : geometry.type === "Polygon" ? "polygon" : null;
    if (!mode) return false;                 // multi-part shapes: attributes only, for now
    this.ensure();
    this.reset();
    const id = crypto.randomUUID();
    const res = this.draw.addFeatures([{ id, type: "Feature", geometry: geometry as GeoJSONStoreFeatures["geometry"], properties: { mode } } as GeoJSONStoreFeatures]);
    if (!res[0]?.valid) return false;
    this.shapeId = id;
    this.draw.setMode("select");
    this.draw.selectFeature(id);
    return true;
  }

  /** The reshaped geometry, as it stands now. */
  reshaped(): GeoJSON.Geometry | null {
    return this.shapeId ? (this.draw.getSnapshotFeature(this.shapeId)?.geometry as GeoJSON.Geometry) ?? null : null;
  }

  /** Measure a distance (a line) or an area (a polygon); `show` gets the numbers as they change. */
  measureWith(kind: "linestring" | "polygon", show: (m: { length?: number; area?: number }) => void) {
    this.ensure();
    this.reset();
    this.onMeasure = show;
    this.draw.setMode(kind);
  }

  reset() {
    this.onDone = undefined;
    this.onMeasure = undefined;
    this.shapeId = null;
    if (this.draw.enabled) { this.draw.clear(); this.draw.setMode("static"); }
  }

  stop() {
    this.reset();
    if (this.draw.enabled) this.draw.stop();
  }
}
