import {
  AlertTriangle, ArrowLeftRight, ChevronRight, ClipboardPaste, Copy, Database, Download, Eraser, FilePlus2, Filter, Info, Layers, ListFilter, ListTree,
  LocateFixed, Map as MapIcon, MousePointerSquareDashed, PanelLeftClose, PenLine, Ratio, Scan, Search, SlidersHorizontal, SquareMousePointer, Table2, Tag,
  Tags, Trash2, X, ZoomIn,
} from "lucide-react";
import { useMemo, useRef, useState, type ReactNode } from "react";
import { colour } from "../lib/mapstyle";
import type { Layer, RasterStyle } from "../lib/types";
import { gradient, rasterStyle } from "./RasterPanel";
import { IconButton, PopMenu, useStored, type PopItem } from "./ui";

export type PanelTab = "symbology" | "labels" | "fields" | "source";
export type SelectionOp = "all" | "clear" | "switch" | "zoom" | "attributes" | "location";
type View = "order" | "source" | "selection" | "labels";
type Show = "all" | "visible" | "vector" | "raster";

const KIND: Record<string, string> = { Point: "points", LineString: "lines", Polygon: "polygons" };
const size = (l: Layer) => (l.raster ? `Raster, ${l.raster.width.toLocaleString("en-GB")} × ${l.raster.height.toLocaleString("en-GB")} cells`
  : `${l.feature_count.toLocaleString("en-GB")} ${KIND[l.geometry_type]}`);
const fmt = (v: number) => (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-GB") : Number(v.toPrecision(4)).toString());
const broken = (l: Layer) => l.geometry_type === "Raster" && (!l.raster || !!l.raster.missing);

/** A legend symbol drawn the way the map draws the layer: a marker for points, a stroke for lines, a patch for polygons. */
export function Symbol({ layer, fill, big }: { layer: Layer; fill?: string; big?: boolean }) {
  const s = layer.style;
  const c = fill ?? (typeof colour(layer) === "string" ? (colour(layer) as string) : s.categories?.[0]?.color ?? s.classes?.[0]?.color ?? s.color);
  const w = big ? 20 : 18;
  if (layer.geometry_type === "Point") return <svg className="ct-sym" width={w} height={w} aria-hidden="true"><circle cx={w / 2} cy={w / 2} r={Math.min(6.5, Math.max(3.5, s.size / 2))} fill={c} stroke={s.stroke} strokeWidth={Math.min(2, Math.max(1, s.width))} /></svg>;
  if (layer.geometry_type === "LineString") return <svg className="ct-sym" width={w} height={w} aria-hidden="true"><line x1="2" y1={w / 2} x2={w - 2} y2={w / 2} stroke={c} strokeWidth={Math.min(4, Math.max(1.5, s.width))} strokeLinecap="round" /></svg>;
  return <svg className="ct-sym" width={w} height={w} aria-hidden="true"><rect x="2" y="3" width={w - 4} height={w - 6} fill={c} fillOpacity={Math.max(0.55, s.opacity)} stroke={s.type === "single" ? s.stroke : "#333"} strokeWidth="1" /></svg>;
}

/** The small symbol beside a layer's name (kept for other panes). */
export function Swatch({ layer }: { layer: Layer }) {
  if (layer.geometry_type === "Raster") {
    const rs = rasterStyle(layer);
    const bg = rs.mode === "rgb" ? "linear-gradient(135deg, #c9563c, #6f9a45, #3d6ea8)" : rs.mode === "classes"
      ? `linear-gradient(135deg, ${(rs.categories.length ? rs.categories.slice(0, 4).map((c) => c.color) : ["#888", "#ccc"]).join(", ")})` : gradient(rs.ramp, rs.invert);
    return <span className="ws-swatch-raster" style={{ background: bg }} aria-hidden="true" />;
  }
  return <Symbol layer={layer} />;
}

/** The Contents pane, laid out like a desktop GIS's: the map's layers in drawing order with their legends, or listed by data
 * source, by selection or by labelling; a right-click (or the ⋯ button) opens each layer's menu. */
export default function Contents({ projectName, layers, activeId, editingId, canEdit, canExport, selection, onActivate, onToggle, onReorder, onZoom, onTable, onRemove,
  onPanel, onExport, onEdit, onAdd, onCollapse, onSelection, onLabels, onRename, onPaste, onSourceResolution }: {
  projectName: string; layers: Layer[]; activeId: string | null; editingId: string | null; canEdit: boolean; canExport: boolean; selection: Record<string, number[]>;
  onActivate: (id: string) => void; onToggle: (l: Layer) => void; onReorder: (bottomToTop: string[]) => void;
  onZoom: (l: Layer) => void; onTable: (l: Layer) => void; onRemove: (l: Layer) => void; onPanel: (l: Layer, tab: PanelTab) => void; onExport: (l: Layer) => void;
  onEdit: (l: Layer) => void; onAdd: () => void; onCollapse: () => void; onSelection: (l: Layer, op: SelectionOp) => void;
  onLabels: (l: Layer, on: boolean) => void; onRename: (l: Layer, name: string) => void; onPaste: (target: Layer, from: Layer) => void; onSourceResolution: (l: Layer) => void;
}) {
  const [view, setView] = useStored<View>("contents-view", "order");
  const [show, setShow] = useStored<Show>("contents-show", "all");
  const [q, setQ] = useState("");
  const [folded, setFolded] = useStored<string[]>("legend-folded", []);
  const [mapOpen, setMapOpen] = useState(true);
  const [drag, setDrag] = useState<{ id: string; over: string | null; below: boolean } | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; layer: Layer } | null>(null);
  const [filterMenu, setFilterMenu] = useState<{ x: number; y: number } | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const copied = useRef<Layer | null>(null);
  const [, bump] = useState(0);

  const top = useMemo(() => [...layers].sort((a, b) => b.position - a.position), [layers]);
  const shown = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    return top.filter((l) => words.every((w) => l.name.toLowerCase().includes(w))
      && (show === "all" || (show === "visible" && l.visible) || (show === "raster") === (l.geometry_type === "Raster") && show !== "visible"));
  }, [top, q, show]);

  function drop() {
    if (!drag?.over || drag.over === drag.id) { setDrag(null); return; }
    const ids = top.map((l) => l.id).filter((x) => x !== drag.id);
    ids.splice(ids.indexOf(drag.over) + (drag.below ? 1 : 0), 0, drag.id);
    setDrag(null);
    onReorder([...ids].reverse());
  }
  function move(l: Layer, dir: -1 | 1) {
    const ids = top.map((x) => x.id);
    const k = ids.indexOf(l.id), j = k + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[k], ids[j]] = [ids[j], ids[k]];
    onReorder([...ids].reverse());
  }
  const pasteable = (l: Layer) => !!copied.current && copied.current.id !== l.id
    && (copied.current.geometry_type === l.geometry_type) && (l.geometry_type !== "Raster" || copied.current.raster?.count === l.raster?.count);
  const copy = (l: Layer) => { copied.current = l; bump((n) => n + 1); };

  function items(l: Layer): PopItem[] {
    const raster = l.geometry_type === "Raster";
    const n = selection[l.id]?.length ?? 0;
    const sel: PopItem[] = [
      { label: "Select all", icon: <SquareMousePointer size={15} />, onSelect: () => onSelection(l, "all") },
      { label: "Clear selection", icon: <Eraser size={15} />, onSelect: () => onSelection(l, "clear"), disabled: !n },
      { label: "Switch selection", icon: <ArrowLeftRight size={15} />, onSelect: () => onSelection(l, "switch") },
      { label: "Zoom to selection", icon: <Scan size={15} />, onSelect: () => onSelection(l, "zoom"), disabled: !n },
      { label: "", divider: true },
      { label: "Select by attributes…", icon: <ListFilter size={15} />, onSelect: () => onSelection(l, "attributes") },
      { label: "Select by location…", icon: <LocateFixed size={15} />, onSelect: () => onSelection(l, "location") },
    ];
    return [
      { label: "Copy", icon: <Copy size={15} />, hint: "Ctrl+C", onSelect: () => copy(l) },
      { label: "Paste properties", icon: <ClipboardPaste size={15} />, hint: "Ctrl+Shift+V", disabled: !canEdit || !pasteable(l), onSelect: () => copied.current && onPaste(l, copied.current) },
      ...(canEdit ? [{ label: "Remove", icon: <Trash2 size={15} />, hint: "Delete", danger: true, onSelect: () => onRemove(l) }] : []),
      { label: "", divider: true },
      ...(!raster ? [{ label: "Attribute table", icon: <Table2 size={15} />, hint: "Ctrl+T", onSelect: () => onTable(l) }, { label: "", divider: true }] : []),
      { label: "Zoom to layer", icon: <ZoomIn size={15} />, onSelect: () => onZoom(l), disabled: !l.bbox },
      ...(raster ? [{ label: "Zoom to source resolution", icon: <Ratio size={15} />, onSelect: () => onSourceResolution(l), disabled: !l.raster }] : []),
      { label: "", divider: true },
      ...(!raster ? [{ label: "Selection", icon: <MousePointerSquareDashed size={15} />, items: sel }, { label: "", divider: true },
        { label: "Label", icon: <Tag size={15} />, checked: !!l.labels, disabled: !canEdit, onSelect: () => onLabels(l, !l.labels) },
        { label: "Labeling properties…", icon: <Tags size={15} />, onSelect: () => onPanel(l, "labels") }, { label: "", divider: true }] : []),
      { label: "Symbology", icon: <SlidersHorizontal size={15} />, onSelect: () => onPanel(l, "symbology") },
      ...(!raster && canEdit && l.id !== editingId ? [{ label: "Edit features", icon: <PenLine size={15} />, onSelect: () => onEdit(l) }] : []),
      { label: "", divider: true },
      { label: "Data", icon: <Database size={15} />, items: [
        raster ? { label: "Download GeoTIFF", icon: <Download size={15} />, disabled: !canExport, onSelect: () => onExport(l) }
          : { label: "Export features…", icon: <Download size={15} />, disabled: !canExport, onSelect: () => onExport(l) },
        ...(!raster ? [{ label: "Fields", icon: <Table2 size={15} />, onSelect: () => onPanel(l, "fields") }] : []),
      ] },
      { label: "", divider: true },
      { label: "View metadata", icon: <Info size={15} />, onSelect: () => onPanel(l, "source") },
      { label: "Properties", icon: <SlidersHorizontal size={15} />, onSelect: () => onPanel(l, "symbology") },
    ];
  }
  const keys = (e: React.KeyboardEvent, l: Layer) => {
    const ctrl = e.ctrlKey || e.metaKey;
    if (canEdit && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); move(l, e.key === "ArrowUp" ? -1 : 1); }
    else if (e.key === "F2" && canEdit) { e.preventDefault(); setRenaming(l.id); }
    else if (e.key === "Delete" && canEdit) { e.preventDefault(); onRemove(l); }
    else if (ctrl && e.shiftKey && e.key.toLowerCase() === "v") { e.preventDefault(); if (canEdit && pasteable(l) && copied.current) onPaste(l, copied.current); }
    else if (ctrl && e.key.toLowerCase() === "c") { e.preventDefault(); copy(l); }
    else if (ctrl && e.key.toLowerCase() === "t" && l.geometry_type !== "Raster") { e.preventDefault(); onTable(l); }
    else if ((e.key === "ContextMenu" || (e.shiftKey && e.key === "F10"))) { e.preventDefault(); const r = e.currentTarget.getBoundingClientRect(); setMenu({ x: r.left + 24, y: r.bottom, layer: l }); }
  };
  const openMenu = (e: React.MouseEvent, l: Layer) => { e.preventDefault(); onActivate(l.id); setMenu({ x: e.clientX, y: e.clientY, layer: l }); };

  const name = (l: Layer, extra?: ReactNode) => renaming === l.id ? (
    <input className="ct-rename" autoFocus defaultValue={l.name} maxLength={200} aria-label={`New name for ${l.name}`}
      onBlur={(e) => { const v = e.currentTarget.value.trim(); setRenaming(null); if (v && v !== l.name) onRename(l, v); }}
      onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") { e.currentTarget.value = l.name; e.currentTarget.blur(); } e.stopPropagation(); }} />
  ) : (
    <button type="button" className="ct-name" onClick={() => onActivate(l.id)} onDoubleClick={() => onPanel(l, "symbology")} onKeyDown={(e) => keys(e, l)}
      onContextMenu={(e) => openMenu(e, l)} aria-current={l.id === activeId || undefined} title={`${l.name}: ${size(l)}. Double-click for its properties; F2 renames it.`}>
      <span>{l.name}</span>
      {broken(l) && <AlertTriangle size={14} className="ct-broken" aria-label="The data cannot be found" />}
      {l.id === editingId && <em className="ws-tag">Editing</em>}
      {extra}
    </button>
  );
  const check = (l: Layer) => (
    <input type="checkbox" className="ct-check" checked={l.visible} onChange={() => onToggle(l)} aria-label={`Show ${l.name}`} title={l.visible ? "Hide" : "Show"} />
  );
  const more = (l: Layer) => (
    <IconButton label={`${l.name}: menu`} className="ct-more" onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); onActivate(l.id); setMenu({ x: r.left, y: r.bottom + 2, layer: l }); }}>
      <ChevronRight size={14} style={{ transform: "rotate(90deg)" }} /></IconButton>
  );

  const VIEWS: [View, string, ReactNode][] = [["order", "List by drawing order", <ListTree size={17} />], ["source", "List by data source", <Database size={17} />],
    ["selection", "List by selection", <SquareMousePointer size={17} />], ["labels", "List by labeling", <Tags size={17} />]];

  return (
    <aside className="ws-contents ct" aria-label="Contents">
      <div className="ws-pane-head">
        <strong>Contents</strong>
        {canEdit && <IconButton label="Add data" onClick={onAdd}><FilePlus2 size={16} /></IconButton>}
        <IconButton label="Hide Contents" onClick={onCollapse}><PanelLeftClose size={16} /></IconButton>
      </div>
      <div className="ct-search">
        <IconButton label="Show which layers" on={show !== "all"} onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setFilterMenu({ x: r.left, y: r.bottom + 2 }); }}><Filter size={15} /></IconButton>
        <label className="ct-find"><input type="search" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search the layers" />
          {q ? <button type="button" aria-label="Clear the search" onClick={() => setQ("")}><X size={14} /></button> : <Search size={14} aria-hidden="true" />}</label>
      </div>
      <div className="ct-views" role="tablist" aria-label="List the layers">
        {VIEWS.map(([v, label, icon]) => <button key={v} role="tab" aria-selected={view === v} title={label} aria-label={label} onClick={() => setView(v)}>{icon}</button>)}
      </div>
      <div className="ws-scroll ct-body">
        {top.length === 0 ? (
          <div className="ws-empty-state"><Layers size={22} aria-hidden="true" />
            <span>No layers yet. Add a file (Shapefile, GeoJSON, GeoTIFF, KML, CSV…), satellite imagery, or make a new layer to draw in.</span>
            {canEdit && <button type="button" className="ge-btn ge-btn-sm ge-btn-primary" onClick={onAdd}><FilePlus2 size={15} aria-hidden="true" />Add data</button>}
          </div>
        ) : view === "order" ? (
          <>
            <h3 className="ct-heading">Drawing Order</h3>
            <div className="ct-map">
              <button type="button" className="ct-tw" aria-expanded={mapOpen} aria-label={mapOpen ? "Fold the map" : "Unfold the map"} onClick={() => setMapOpen(!mapOpen)}><Tri open={mapOpen} /></button>
              <MapIcon size={16} className="ct-map-icon" aria-hidden="true" /><span title={projectName}>Map</span>
            </div>
            {mapOpen && (
              <ul className="ct-layers" role="tree" aria-label="Layers, top to bottom" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); drop(); }}>
                {shown.map((l) => {
                  const open = !folded.includes(l.id);
                  const cls = ["ct-layer", l.id === activeId && "is-active", !l.visible && "is-hidden", drag?.id === l.id && "is-dragging",
                    drag?.over === l.id && drag.id !== l.id && (drag.below ? "is-drop-below" : "is-drop-above")].filter(Boolean).join(" ");
                  return (
                    <li key={l.id} className={cls} role="treeitem" aria-expanded={open} aria-selected={l.id === activeId} draggable={canEdit && renaming !== l.id}
                      onDragStart={(e) => { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", l.id); setDrag({ id: l.id, over: null, below: false }); }}
                      onDragOver={(e) => { if (!drag) return; e.preventDefault(); const r = e.currentTarget.getBoundingClientRect(); setDrag({ ...drag, over: l.id, below: e.clientY > r.top + Math.min(r.height, 30) / 2 }); }}
                      onDragEnd={() => setDrag(null)}>
                      <div className="ct-row" onContextMenu={(e) => openMenu(e, l)}>
                        <button type="button" className="ct-tw" aria-label={open ? `Fold ${l.name}'s legend` : `Unfold ${l.name}'s legend`} aria-expanded={open}
                          onClick={() => setFolded(open ? [...folded, l.id] : folded.filter((x) => x !== l.id))}><Tri open={open} /></button>
                        {check(l)}{name(l)}{more(l)}
                      </div>
                      {open && <Legend layer={l} />}
                    </li>
                  );
                })}
                {!shown.length && <li className="ct-none">No layer matches.</li>}
              </ul>
            )}
          </>
        ) : view === "source" ? (
          <BySource layers={shown} render={(l) => <div className="ct-row ct-flat" onContextMenu={(e) => openMenu(e, l)}>{check(l)}{name(l, <small className="ct-src">{sourceOf(l)}</small>)}{more(l)}</div>} />
        ) : view === "selection" ? (
          <>
            <h3 className="ct-heading">Selection</h3>
            <ul className="ct-layers">{shown.filter((l) => l.geometry_type !== "Raster").map((l) => {
              const n = selection[l.id]?.length ?? 0;
              return (
                <li key={l.id} className={`ct-layer${l.id === activeId ? " is-active" : ""}`}>
                  <div className="ct-row ct-flat" onContextMenu={(e) => openMenu(e, l)}>
                    <Symbol layer={l} />{name(l, <small className={`ct-count${n ? " has" : ""}`}>{n.toLocaleString("en-GB")} of {l.feature_count.toLocaleString("en-GB")}</small>)}
                    <IconButton label={`Zoom to ${l.name}'s selection`} disabled={!n} onClick={() => onSelection(l, "zoom")}><Scan size={14} /></IconButton>
                    <IconButton label={`Clear ${l.name}'s selection`} disabled={!n} onClick={() => onSelection(l, "clear")}><Eraser size={14} /></IconButton>
                  </div>
                </li>
              );
            })}</ul>
          </>
        ) : (
          <>
            <h3 className="ct-heading">Labeling</h3>
            <ul className="ct-layers">{shown.filter((l) => l.geometry_type !== "Raster").map((l) => (
              <li key={l.id} className={`ct-layer${l.id === activeId ? " is-active" : ""}`}>
                <div className="ct-row ct-flat" onContextMenu={(e) => openMenu(e, l)}>
                  <input type="checkbox" className="ct-check" checked={!!l.labels} disabled={!canEdit} aria-label={`Label ${l.name}`} onChange={(e) => onLabels(l, e.target.checked)} />
                  {name(l, l.labels ? <small className="ct-src">by {l.labels.field}</small> : undefined)}
                  <IconButton label={`${l.name}: labeling properties`} onClick={() => onPanel(l, "labels")}><Tags size={14} /></IconButton>
                </div>
              </li>
            ))}</ul>
          </>
        )}
      </div>
      {menu && <PopMenu x={menu.x} y={menu.y} items={items(menu.layer)} label={`${menu.layer.name}: menu`} onClose={() => setMenu(null)} />}
      {filterMenu && <PopMenu x={filterMenu.x} y={filterMenu.y} label="Show which layers" onClose={() => setFilterMenu(null)} items={([
        ["all", "All layers"], ["visible", "Visible layers"], ["vector", "Vector layers"], ["raster", "Raster layers"]] as [Show, string][])
        .map(([v, t]) => ({ label: t, checked: show === v, onSelect: () => setShow(v) }))} />}
    </aside>
  );
}

function Tri({ open }: { open: boolean }) {
  return <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" className={open ? "is-open" : ""}><path d={open ? "M1 3h8L5 8z" : "M3 1v8l5-4z"} /></svg>;
}

function sourceOf(l: Layer): string {
  const s = l.source;
  if (s.imagery) return `${{ "planetary-computer": "Planetary Computer", "earth-search": "Earth Search", gee: "Earth Engine" }[s.imagery.provider] ?? s.imagery.provider} · ${s.imagery.dataset}`;
  if (s.tool?.startsWith("earth:")) return s.tool_name?.replace(/^Poly Earth: /, "") ?? "Poly Earth";
  if (s.tool) return s.tool_name ?? s.tool;
  if (s.filename) return s.filename;
  return s.drawn ? "drawn in PolyGIS" : "made in PolyGIS";
}
function groupOf(l: Layer): string {
  const s = l.source;
  if (s.imagery) return "Satellite imagery";
  if (s.tool?.startsWith("earth:")) return "Poly Earth results";
  if (s.tool) return "Geoprocessing results";
  if (s.filename) return "Imported files";
  return "Made in PolyGIS";
}
function BySource({ layers, render }: { layers: Layer[]; render: (l: Layer) => ReactNode }) {
  const groups = ["Imported files", "Satellite imagery", "Poly Earth results", "Geoprocessing results", "Made in PolyGIS"]
    .map((g) => [g, layers.filter((l) => groupOf(l) === g)] as const).filter(([, ls]) => ls.length);
  return (
    <>
      <h3 className="ct-heading">Data Source</h3>
      {groups.map(([g, ls]) => (
        <section key={g} className="ct-group">
          <div className="ct-group-head"><Database size={14} aria-hidden="true" />{g}<span>{ls.length}</span></div>
          <ul className="ct-layers">{ls.map((l) => <li key={l.id} className="ct-layer">{render(l)}</li>)}</ul>
        </section>
      ))}
    </>
  );
}

/** A layer's legend under its name, as ArcGIS Pro shows it: one symbol; or the field and a symbol for each category or class;
 * for a raster, "Value" with its colour ramp and the two ends, or its classes, or the bands shown as red, green and blue. */
export function Legend({ layer: l }: { layer: Layer }) {
  if (l.geometry_type === "Raster") {
    const rs: RasterStyle = rasterStyle(l);
    if (!l.raster || l.raster.missing) return <p className="ct-legend ct-missing">The raster's file is missing from storage: add it again.</p>;
    if (rs.mode === "classes") return (
      <ul className="ct-legend"><li className="ct-field">Value</li>
        {rs.categories.filter((c) => !c.hidden).slice(0, 30).map((c) => <li key={c.value}><i className="ct-patch" style={{ background: c.color }} />{c.label || c.value}</li>)}</ul>
    );
    if (rs.mode === "rgb") {
      const b = l.raster.bands;
      return (
        <ul className="ct-legend"><li className="ct-field">RGB</li>
          {(["Red", "Green", "Blue"] as const).map((k, i) => <li key={k}><i className="ct-patch" style={{ background: ["#e5484d", "#30a46c", "#3e63dd"][i] }} />
            {k}: {b[(rs.bands?.[i] ?? i + 1) - 1]?.name ?? `Band ${rs.bands?.[i] ?? i + 1}`}</li>)}</ul>
      );
    }
    return (
      <div className="ct-legend"><div className="ct-field">Value</div>
        <div className="ct-ramp"><span className="ct-ramp-bar" style={{ background: gradient(rs.ramp, !rs.invert).replace("to right", "to bottom") }} />
          <span className="ct-ramp-ends"><span>{fmt(rs.max ?? 0)}</span><span>{fmt(rs.min ?? 0)}</span></span></div></div>
    );
  }
  const s = l.style;
  if (s.type === "categorized" && s.categories?.length) return (
    <ul className="ct-legend">{s.field && <li className="ct-field">{s.field}</li>}
      {s.categories.slice(0, 40).map((c) => <li key={`${c.value}${c.color}`}><Symbol layer={l} fill={c.color} />{c.label || c.value || "(empty)"}</li>)}
      {s.categories.length > 40 && <li className="ct-more-note">and {s.categories.length - 40} more</li>}
      <li><Symbol layer={l} fill="#b8bfcc" /><span className="ct-other">all other values</span></li></ul>
  );
  if (s.type === "graduated" && s.classes?.length) return (
    <ul className="ct-legend">{s.field && <li className="ct-field">{s.field}</li>}
      {s.classes.map((c) => <li key={`${c.min}${c.color}`}><Symbol layer={l} fill={c.color} />{fmt(c.min)} – {fmt(c.max)}</li>)}</ul>
  );
  return <ul className="ct-legend"><li><Symbol layer={l} big /></li></ul>;
}
