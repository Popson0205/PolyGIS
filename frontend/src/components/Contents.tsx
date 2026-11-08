import type { Layer } from "../lib/types";
import { colour } from "../lib/mapstyle";

const KIND: Record<string, string> = { Point: "points", LineString: "lines", Polygon: "polygons" };

/** A small swatch drawn the way the layer is drawn. */
export function Swatch({ layer }: { layer: Layer }) {
  const c = typeof colour(layer) === "string" ? (colour(layer) as string) : layer.style.categories?.[0]?.color ?? layer.style.classes?.[0]?.color ?? layer.style.color;
  if (layer.geometry_type === "Point") return <svg width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="5" fill={c} stroke={layer.style.stroke} strokeWidth="1.5" /></svg>;
  if (layer.geometry_type === "LineString") return <svg width="18" height="18" aria-hidden="true"><path d="M2 14 L8 6 L16 10" fill="none" stroke={c} strokeWidth="2.5" strokeLinecap="round" /></svg>;
  return <svg width="18" height="18" aria-hidden="true"><rect x="2.5" y="3.5" width="13" height="11" rx="1.5" fill={c} fillOpacity={Math.max(0.35, layer.style.opacity)} stroke={layer.style.type === "single" ? layer.style.stroke : c} /></svg>;
}

/** The Contents pane: layers top to bottom as they are drawn, with visibility, order and quick actions. */
export default function Contents({ layers, activeId, editingId, canEdit, onActivate, onToggle, onMove, onZoom, onTable, onRemove }: {
  layers: Layer[]; activeId: string | null; editingId: string | null; canEdit: boolean;
  onActivate: (id: string) => void; onToggle: (l: Layer) => void; onMove: (id: string, dir: 1 | -1) => void;
  onZoom: (l: Layer) => void; onTable: (l: Layer) => void; onRemove: (l: Layer) => void;
}) {
  const top = [...layers].sort((a, b) => b.position - a.position);
  return (
    <aside className="ws-contents" aria-label="Contents">
      <div className="ws-pane-head"><strong>Contents</strong><span>{layers.length} layer{layers.length === 1 ? "" : "s"}</span></div>
      {top.length === 0 && <p className="ws-empty">No layers yet. Use <b>Add data</b> to bring in a file, or <b>New layer</b> to draw one.</p>}
      <ul className="ws-layers">
        {top.map((l, k) => (
          <li key={l.id} className={`ws-layer${l.id === activeId ? " is-active" : ""}`}>
            <div className="ws-layer-row">
              <input type="checkbox" checked={l.visible} aria-label={`Show ${l.name}`} onChange={() => onToggle(l)} />
              <button type="button" className="ws-layer-name" onClick={() => onActivate(l.id)} onDoubleClick={() => onZoom(l)} title={`${l.name}: ${l.feature_count.toLocaleString("en-GB")} ${KIND[l.geometry_type]}`}>
                <Swatch layer={l} /><span>{l.name}</span>{l.id === editingId && <em className="ws-tag">editing</em>}
              </button>
            </div>
            {l.id === activeId && (
              <div className="ws-layer-actions">
                <span className="ws-muted">{l.feature_count.toLocaleString("en-GB")} {KIND[l.geometry_type]}</span>
                <button type="button" onClick={() => onZoom(l)} disabled={!l.bbox}>Zoom to</button>
                <button type="button" onClick={() => onTable(l)}>Table</button>
                {canEdit && <>
                  <button type="button" aria-label={`Move ${l.name} up`} onClick={() => onMove(l.id, 1)} disabled={k === 0}>▲</button>
                  <button type="button" aria-label={`Move ${l.name} down`} onClick={() => onMove(l.id, -1)} disabled={k === top.length - 1}>▼</button>
                  <button type="button" className="ws-danger" onClick={() => onRemove(l)}>Remove</button>
                </>}
              </div>
            )}
            {l.style.type !== "single" && l.visible && (
              <ul className="ws-legend">
                {(l.style.type === "categorized" ? (l.style.categories ?? []).slice(0, 12).map((c) => [c.label || c.value || "(empty)", c.color])
                  : (l.style.classes ?? []).map((c) => [`${fmt(c.min)} – ${fmt(c.max)}`, c.color])).map(([t, c]) => (
                  <li key={`${t}${c}`}><i style={{ background: c }} />{t}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </aside>
  );
}

const fmt = (v: number) => (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-GB") : Number(v.toPrecision(4)).toString());
