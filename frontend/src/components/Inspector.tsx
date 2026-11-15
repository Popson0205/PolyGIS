/** The Inspector: what was clicked on the map, named and described, with what can be done with it next.
 *  And the selection bar: over the map while anything is selected, with what is done to a selection. */
import { ArrowLeftRight, Download, Eraser, ListFilter, LocateFixed, MousePointerClick, Palette, PenLine, Scan, Table2, X, ZoomIn } from "lucide-react";
import type { Layer } from "../lib/types";
import { IconButton } from "./ui";

const KIND: Record<string, string> = { Point: "point", MultiPoint: "points", LineString: "line", MultiLineString: "lines", Polygon: "area", MultiPolygon: "areas", Raster: "raster" };
/** The field that names a feature, when it has one. */
const NAME_FIELDS = ["name", "title", "label", "callsign", "designation", "device", "id_name", "ref"];
export function featureTitle(layer: Layer, fid: number, props: Record<string, unknown>): string {
  const keys = Object.keys(props);
  for (const n of NAME_FIELDS) {
    const k = keys.find((x) => x.toLowerCase() === n);
    if (k && props[k] != null && String(props[k]).trim()) return String(props[k]);
  }
  return layer.geometry_type === "Raster" ? "Pixel values" : `Feature ${fid}`;
}
const show = (v: unknown) => (typeof v === "object" ? JSON.stringify(v) : String(v));

export function Inspector({ layer, fid, props, canEdit, selected, onClose, onZoom, onSelect, onTable, onStyle, onEdit }: {
  layer: Layer; fid: number; props: Record<string, unknown>; canEdit: boolean; selected: boolean;
  onClose: () => void; onZoom: () => void; onSelect: () => void; onTable: () => void; onStyle: () => void; onEdit: () => void;
}) {
  const raster = layer.geometry_type === "Raster";
  const title = featureTitle(layer, fid, props);
  const rows: [string, unknown][] = raster ? Object.entries(props) : layer.fields.map((f) => [f.name, props[f.name]]);
  const filled = rows.filter(([, v]) => v != null && v !== "");
  const empty = rows.length - filled.length;
  return (
    <aside className="ws-panel insp" aria-label="Inspector">
      <div className="insp-head">
        <span className="insp-kind">{raster ? "Raster" : "Feature"} · {layer.name}{raster ? "" : ` · ${KIND[layer.geometry_type] ?? layer.geometry_type}`}</span>
        <div className="insp-title"><h2 title={title}>{title}</h2><IconButton label="Close the inspector" onClick={onClose}><X size={16} /></IconButton></div>
        {!raster && <div className="insp-acts">
          <button type="button" onClick={onZoom}><ZoomIn size={15} aria-hidden="true" />Zoom to</button>
          <button type="button" onClick={onSelect} aria-pressed={selected}><MousePointerClick size={15} aria-hidden="true" />{selected ? "Selected" : "Select"}</button>
          <button type="button" onClick={onTable}><Table2 size={15} aria-hidden="true" />Table</button>
          <button type="button" onClick={onStyle}><Palette size={15} aria-hidden="true" />Style</button>
          {canEdit && <button type="button" onClick={onEdit}><PenLine size={15} aria-hidden="true" />Edit</button>}
        </div>}
      </div>
      <div className="ws-scroll ws-panel-body">
        <dl className="insp-dl">
          {!raster && <div><dt>ID</dt><dd>{fid}</dd></div>}
          {filled.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{show(v)}</dd></div>)}
        </dl>
        {empty > 0 && <details className="insp-empty"><summary>{empty} empty {empty === 1 ? "field" : "fields"}</summary>
          <p>{rows.filter(([, v]) => v == null || v === "").map(([k]) => k).join(", ")}</p></details>}
        {raster && !filled.length && <p className="ws-muted">No data here.</p>}
      </div>
    </aside>
  );
}

export function SelectionBar({ selection, layers, activeId, onFilter, onPlace, onInvert, onZoom, onTable, onExport, onClear, onPick }: {
  selection: Record<string, number[]>; layers: Layer[]; activeId: string | null;
  onFilter: (l: Layer) => void; onPlace: (l: Layer) => void; onInvert: (l: Layer) => void; onZoom: (l: Layer) => void; onTable: (l: Layer) => void; onExport: (l: Layer) => void;
  onClear: () => void; onPick: (l: Layer) => void;
}) {
  const withSel = layers.filter((l) => (selection[l.id] ?? []).length);
  if (!withSel.length) return null;
  const l = withSel.find((x) => x.id === activeId) ?? withSel[0];
  const n = (selection[l.id] ?? []).length;
  return (
    <div className="selbar" role="toolbar" aria-label="Selection">
      <span className="selbar-count">
        <strong>{n.toLocaleString("en-GB")} selected</strong>
        {withSel.length > 1 ? (
          <select aria-label="Layer of the selection" value={l.id} onChange={(e) => { const x = withSel.find((y) => y.id === e.target.value); if (x) onPick(x); }}>
            {withSel.map((x) => <option key={x.id} value={x.id}>{x.name} ({(selection[x.id] ?? []).length})</option>)}
          </select>
        ) : <span>in {l.name}</span>}
      </span>
      <span className="selbar-sep" aria-hidden="true" />
      <button type="button" onClick={() => onZoom(l)} title="Zoom to the selection"><Scan size={15} aria-hidden="true" /><span>Zoom to</span></button>
      <button type="button" onClick={() => onTable(l)} title="Open in the table"><Table2 size={15} aria-hidden="true" /><span>Table</span></button>
      <button type="button" onClick={() => onFilter(l)} title="Filter by values"><ListFilter size={15} aria-hidden="true" /><span>Filter by values</span></button>
      <button type="button" onClick={() => onPlace(l)} title="Select by place"><LocateFixed size={15} aria-hidden="true" /><span>By place</span></button>
      <button type="button" onClick={() => onInvert(l)} title="Invert the selection"><ArrowLeftRight size={15} aria-hidden="true" /><span>Invert</span></button>
      <button type="button" onClick={() => onExport(l)} title="Export the selection"><Download size={15} aria-hidden="true" /><span>Export</span></button>
      <button type="button" onClick={onClear} className="selbar-clear" title="Clear the selection"><Eraser size={15} aria-hidden="true" /><span>Clear</span></button>
    </div>
  );
}
