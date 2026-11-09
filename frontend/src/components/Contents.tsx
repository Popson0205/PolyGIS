import { ChevronDown, Download, Eye, EyeOff, FilePlus2, GripVertical, Layers, MoreHorizontal, PanelLeftClose, PenLine, SlidersHorizontal, Table2, Trash2, ZoomIn } from "lucide-react";
import { useState } from "react";
import { colour } from "../lib/mapstyle";
import type { Layer } from "../lib/types";
import { gradient, rasterStyle } from "./RasterPanel";
import { IconButton, Menu, useStored, type MenuItem } from "./ui";

const KIND: Record<string, string> = { Point: "points", LineString: "lines", Polygon: "polygons" };
const size = (l: Layer) => (l.raster ? `Raster, ${l.raster.width.toLocaleString("en-GB")} × ${l.raster.height.toLocaleString("en-GB")} cells`
  : `${l.feature_count.toLocaleString("en-GB")} ${KIND[l.geometry_type]}`);

/** A small swatch drawn the way the layer is drawn. */
export function Swatch({ layer }: { layer: Layer }) {
  if (layer.geometry_type === "Raster") {
    const rs = rasterStyle(layer);
    const bg = rs.mode === "rgb" ? "linear-gradient(135deg, #c9563c, #6f9a45, #3d6ea8)" : rs.mode === "classes"
      ? `linear-gradient(135deg, ${(rs.categories.length ? rs.categories.slice(0, 4).map((c) => c.color) : ["#888", "#ccc"]).join(", ")})` : gradient(rs.ramp, rs.invert);
    return <span className="ws-swatch-raster" style={{ background: bg }} aria-hidden="true" />;
  }
  const c = typeof colour(layer) === "string" ? (colour(layer) as string) : layer.style.categories?.[0]?.color ?? layer.style.classes?.[0]?.color ?? layer.style.color;
  if (layer.geometry_type === "Point") return <svg className="ws-swatch" width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="4.5" fill={c} stroke={layer.style.stroke} strokeWidth="1.5" /></svg>;
  if (layer.geometry_type === "LineString") return <svg className="ws-swatch" width="18" height="18" aria-hidden="true"><path d="M2 13 L7 6 L11 11 L16 4" fill="none" stroke={c} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  return <svg className="ws-swatch" width="18" height="18" aria-hidden="true"><rect x="2.5" y="3.5" width="13" height="11" rx="1.5" fill={c} fillOpacity={Math.max(0.4, layer.style.opacity)} stroke={layer.style.type === "single" ? layer.style.stroke : c} strokeWidth="1.2" /></svg>;
}

/** The Contents pane: layers top to bottom as they are drawn. Drag a layer to reorder; its ⋯ menu has the rest. */
export default function Contents({ layers, activeId, editingId, canEdit, selection, onActivate, onToggle, onReorder, onZoom, onTable, onRemove, onProperties, onExport, onEdit, onAdd, onCollapse }: {
  layers: Layer[]; activeId: string | null; editingId: string | null; canEdit: boolean; selection: Record<string, number[]>;
  onActivate: (id: string) => void; onToggle: (l: Layer) => void; onReorder: (bottomToTop: string[]) => void;
  onZoom: (l: Layer) => void; onTable: (l: Layer) => void; onRemove: (l: Layer) => void; onProperties: (l: Layer) => void; onExport: (l: Layer) => void;
  onEdit: (l: Layer) => void; onAdd: () => void; onCollapse: () => void;
}) {
  const top = [...layers].sort((a, b) => b.position - a.position);
  const [folded, setFolded] = useStored<string[]>("legend-folded", []);
  const [drag, setDrag] = useState<{ id: string; over: string | null; below: boolean } | null>(null);

  function drop() {
    if (!drag?.over || drag.over === drag.id) { setDrag(null); return; }
    const ids = top.map((l) => l.id).filter((x) => x !== drag.id);
    const k = ids.indexOf(drag.over) + (drag.below ? 1 : 0);
    ids.splice(k, 0, drag.id);
    setDrag(null);
    onReorder([...ids].reverse());
  }
  function move(l: Layer, dir: -1 | 1) {   // keyboard: Alt+↑ / Alt+↓
    const ids = top.map((x) => x.id);
    const k = ids.indexOf(l.id), j = k + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[k], ids[j]] = [ids[j], ids[k]];
    onReorder([...ids].reverse());
  }
  const items = (l: Layer): MenuItem[] => [
    { label: "Zoom to layer", icon: <ZoomIn size={15} />, onSelect: () => onZoom(l), disabled: !l.bbox },
    ...(l.geometry_type !== "Raster" ? [{ label: "Attribute table", icon: <Table2 size={15} />, onSelect: () => onTable(l) }] : []),
    { label: "Properties", icon: <SlidersHorizontal size={15} />, onSelect: () => onProperties(l) },
    ...(l.geometry_type !== "Raster" && canEdit && l.id !== editingId ? [{ label: "Edit features", icon: <PenLine size={15} />, onSelect: () => onEdit(l) }] : []),
    { label: l.geometry_type === "Raster" ? "Download GeoTIFF" : "Export", icon: <Download size={15} />, onSelect: () => onExport(l) },
    ...(canEdit ? [{ label: "", onSelect: () => {}, divider: true }, { label: "Remove from the project", icon: <Trash2 size={15} />, onSelect: () => onRemove(l), danger: true }] : []),
  ];

  return (
    <aside className="ws-contents" aria-label="Contents">
      <div className="ws-pane-head">
        <strong>Contents <span className="ws-count">{layers.length || ""}</span></strong>
        {canEdit && <IconButton label="Add data" onClick={onAdd}><FilePlus2 size={16} /></IconButton>}
        <IconButton label="Hide Contents" onClick={onCollapse}><PanelLeftClose size={16} /></IconButton>
      </div>
      <div className="ws-scroll">
        {top.length === 0 && (
          <div className="ws-empty-state"><Layers size={22} aria-hidden="true" />
            <span>No layers yet. Add a file (Shapefile, GeoJSON, GeoTIFF, KML, CSV…) or make a new layer to draw in.</span>
            {canEdit && <button type="button" className="ge-btn ge-btn-sm ge-btn-primary" onClick={onAdd}><FilePlus2 size={15} aria-hidden="true" />Add data</button>}
          </div>
        )}
        <ul className="ws-layers" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); drop(); }}>
          {top.map((l) => {
            const legend = legendOf(l);
            const open = legend && !folded.includes(l.id);
            const n = selection[l.id]?.length ?? 0;
            const cls = ["ws-layer", l.id === activeId && "is-active", !l.visible && "is-hidden", drag?.id === l.id && "is-dragging",
              drag?.over === l.id && drag.id !== l.id && (drag.below ? "is-drop-below" : "is-drop-above")].filter(Boolean).join(" ");
            return (
              <li key={l.id} className={cls} draggable={canEdit}
                onDragStart={(e) => { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", l.id); setDrag({ id: l.id, over: null, below: false }); }}
                onDragOver={(e) => { if (!drag) return; e.preventDefault(); const r = e.currentTarget.getBoundingClientRect(); setDrag({ ...drag, over: l.id, below: e.clientY > r.top + Math.min(r.height, 34) / 2 }); }}
                onDragEnd={() => setDrag(null)}>
                <div className="ws-layer-row" onContextMenu={(e) => { e.preventDefault(); onActivate(l.id); (e.currentTarget.querySelector(".menu-wrap button") as HTMLButtonElement | null)?.click(); }}>
                  {canEdit ? <span className="ws-grip" aria-hidden="true"><GripVertical size={14} /></span> : <span className="ws-grip" />}
                  <button type="button" className={`ws-fold${legend ? "" : " is-blank"}`} aria-expanded={!!open} aria-label={open ? `Fold ${l.name}'s legend` : `Show ${l.name}'s legend`}
                    onClick={() => setFolded(open ? [...folded, l.id] : folded.filter((x) => x !== l.id))} tabIndex={legend ? 0 : -1}><ChevronDown size={14} /></button>
                  <button type="button" className="ws-eye" aria-pressed={l.visible} aria-label={`${l.visible ? "Hide" : "Show"} ${l.name}`} title={l.visible ? "Hide" : "Show"} onClick={() => onToggle(l)}>
                    {l.visible ? <Eye size={15} /> : <EyeOff size={15} />}
                  </button>
                  <button type="button" className="ws-layer-name" onClick={() => onActivate(l.id)} onDoubleClick={() => onZoom(l)} title={`${l.name}: ${size(l)}. Double-click to zoom to it.`}
                    aria-current={l.id === activeId || undefined}
                    onKeyDown={(e) => { if (canEdit && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); move(l, e.key === "ArrowUp" ? -1 : 1); } }}>
                    <Swatch layer={l} /><span>{l.name}</span>
                    {l.id === editingId && <em className="ws-tag">Editing</em>}
                    {n > 0 && <em className="ws-tag" style={{ background: "#dff6fd", color: "#00627a" }}>{n.toLocaleString("en-GB")}</em>}
                  </button>
                  <Menu label={`${l.name}: more`} trigger={<MoreHorizontal size={16} />} items={items(l)} />
                </div>
                {open && <Legend layer={l} />}
              </li>
            );
          })}
        </ul>
      </div>
    </aside>
  );
}

function legendOf(l: Layer): boolean {
  if (l.geometry_type === "Raster") return rasterStyle(l).mode !== "rgb";
  return l.style.type !== "single";
}

function Legend({ layer: l }: { layer: Layer }) {
  if (l.geometry_type === "Raster") {
    const rs = rasterStyle(l);
    if (rs.mode === "classes") return (
      <ul className="ws-legend">{rs.categories.filter((c) => !c.hidden).slice(0, 14).map((c) => <li key={c.value}><i style={{ background: c.color }} />{c.label || c.value}</li>)}</ul>
    );
    return (
      <div className="ws-legend"><div className="ws-ramp" style={{ background: gradient(rs.ramp, rs.invert) }} />
        <div className="ws-ramp-ends"><span>{fmt(rs.min ?? 0)}</span><span>{fmt(rs.max ?? 0)}</span></div></div>
    );
  }
  const rows = l.style.type === "categorized" ? (l.style.categories ?? []).slice(0, 14).map((c) => [c.label || c.value || "(empty)", c.color])
    : (l.style.classes ?? []).map((c) => [`${fmt(c.min)} – ${fmt(c.max)}`, c.color]);
  return (
    <ul className="ws-legend">
      {l.style.field && <li style={{ color: "var(--c-faint)" }}>{l.style.field}</li>}
      {rows.map(([t, c]) => <li key={`${t}${c}`}><i style={{ background: c }} />{t}</li>)}
    </ul>
  );
}

const fmt = (v: number) => (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-GB") : Number(v.toPrecision(4)).toString());
