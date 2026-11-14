/** The project's maps and layouts, as ArcGIS Pro's Catalog pane lists them, and the strip of open views (maps and
 *  layouts) above the map. */
import { Copy, FileText, Map as MapIcon, MoreHorizontal, PencilLine, Plus, Printer, Trash2, X } from "lucide-react";
import { useState } from "react";
import type { SavedLayout } from "../lib/api";
import type { GisMap } from "../lib/types";
import { IconButton, Menu } from "./ui";

export type ViewTab = { kind: "map"; id: string } | { kind: "layout"; id: string };
export const viewKey = (v: ViewTab) => `${v.kind}:${v.id}`;
/** An unsaved layout's tab: "new:1", "new:2"… */
export const isNewLayout = (id: string) => id.startsWith("new:");

export function ViewTabs({ tabs, current, maps, layouts, onPick, onClose, onNewMap, onNewLayout, canEdit, layoutsAllowed }: {
  tabs: ViewTab[]; current: string; maps: GisMap[]; layouts: SavedLayout[];
  onPick: (v: ViewTab) => void; onClose: (v: ViewTab) => void; onNewMap: () => void; onNewLayout: () => void; canEdit: boolean; layoutsAllowed: boolean;
}) {
  const name = (v: ViewTab) => v.kind === "map" ? maps.find((m) => m.id === v.id)?.name ?? "Map"
    : isNewLayout(v.id) ? `Layout${v.id === "new:1" ? "" : v.id.slice(4)}` : layouts.find((l) => l.id === v.id)?.name ?? "Layout";
  const mapTabs = tabs.filter((t) => t.kind === "map").length;
  return (
    <div className="vt" role="tablist" aria-label="Open maps and layouts">
      {tabs.map((v) => {
        const k = viewKey(v), on = k === current;
        const closable = v.kind === "layout" || mapTabs > 1;
        return (
          <div key={k} className={`vt-tab${on ? " is-on" : ""}`}>
            <button type="button" role="tab" aria-selected={on} onClick={() => onPick(v)} title={name(v)}
              onAuxClick={(e) => { if (e.button === 1 && closable) onClose(v); }}>
              {v.kind === "map" ? <MapIcon size={14} aria-hidden="true" /> : <Printer size={14} aria-hidden="true" />}<span>{name(v)}</span>
            </button>
            {closable && <button type="button" className="vt-x" aria-label={`Close ${name(v)}`} onClick={() => onClose(v)}><X size={12} /></button>}
          </div>
        );
      })}
      <Menu align="start" label="Insert a map or layout" trigger={<span className="vt-add" title="New map or layout"><Plus size={14} aria-hidden="true" /></span>}
        items={[
          { label: "New map", icon: <MapIcon size={15} />, disabled: !canEdit, onSelect: onNewMap },
          { label: "New layout", icon: <Printer size={15} />, disabled: !layoutsAllowed, onSelect: onNewLayout },
        ]} />
    </div>
  );
}

export default function Catalog({ maps, layouts, mapId, current, canEdit, layoutsAllowed, onOpenMap, onNewMap, onCopyMap, onRenameMap, onDeleteMap,
  onOpenLayout, onNewLayout, onRenameLayout, onDeleteLayout, onClose }: {
  maps: GisMap[]; layouts: SavedLayout[]; mapId: string | null; current: string; canEdit: boolean; layoutsAllowed: boolean;
  onOpenMap: (m: GisMap) => void; onNewMap: () => void; onCopyMap: (m: GisMap) => void; onRenameMap: (m: GisMap, name: string) => void; onDeleteMap: (m: GisMap) => void;
  onOpenLayout: (l: SavedLayout) => void; onNewLayout: () => void; onRenameLayout: (l: SavedLayout, name: string) => void; onDeleteLayout: (l: SavedLayout) => void;
  onClose: () => void;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [text, setText] = useState("");
  const start = (id: string, name: string) => { setRenaming(id); setText(name); };
  const finish = (save: (n: string) => void, old: string) => { const n = text.trim(); setRenaming(null); if (n && n !== old) save(n); };
  const nameField = (id: string, name: string, save: (n: string) => void) => renaming === id
    ? <input className="cat-rename" autoFocus value={text} aria-label="New name" onChange={(e) => setText(e.target.value)} onBlur={() => finish(save, name)}
      onKeyDown={(e) => { if (e.key === "Enter") finish(save, name); if (e.key === "Escape") setRenaming(null); }} />
    : <span className="cat-name">{name}</span>;
  return (
    <aside className="ws-panel cat" aria-label="Catalog">
      <div className="ws-pane-head"><strong>Catalog</strong><span className="ge-spacer" /><IconButton label="Close the Catalog" onClick={onClose}><X size={16} /></IconButton></div>
      <div className="ws-scroll cat-body">
        <section>
          <div className="cat-head"><MapIcon size={15} aria-hidden="true" /><h3>Maps</h3><span className="cat-n">{maps.length}</span><span className="ge-spacer" />
            {canEdit && <IconButton label="New map" onClick={onNewMap}><Plus size={15} /></IconButton>}</div>
          <ul className="cat-list">
            {maps.map((m) => (
              <li key={m.id} className={`cat-item${m.id === mapId ? " is-open" : ""}${current === `map:${m.id}` ? " is-on" : ""}`}>
                {renaming === m.id ? <span className="cat-open"><MapIcon size={15} aria-hidden="true" />{nameField(m.id, m.name, (n) => onRenameMap(m, n))}</span>
                  : <button type="button" className="cat-open" onClick={() => onOpenMap(m)} onDoubleClick={() => canEdit && start(m.id, m.name)} title="Open this map (double-click to rename)">
                    <MapIcon size={15} aria-hidden="true" /><span className="cat-name">{m.name}</span>
                    <span className="cat-meta">{m.layer_count} {m.layer_count === 1 ? "layer" : "layers"}</span>
                  </button>}
                <Menu label={`${m.name}: more`} trigger={<span className="cat-more"><MoreHorizontal size={15} aria-hidden="true" /></span>}
                  items={[
                    { label: "Open", icon: <MapIcon size={15} />, onSelect: () => onOpenMap(m) },
                    { label: "Rename", icon: <PencilLine size={15} />, disabled: !canEdit, onSelect: () => start(m.id, m.name) },
                    { label: "Copy", icon: <Copy size={15} />, disabled: !canEdit, onSelect: () => onCopyMap(m) },
                    { label: "", divider: true, onSelect: () => undefined },
                    { label: "Delete", icon: <Trash2 size={15} />, danger: true, disabled: !canEdit || maps.length < 2, onSelect: () => onDeleteMap(m) },
                  ]} />
              </li>
            ))}
          </ul>
        </section>
        <section>
          <div className="cat-head"><Printer size={15} aria-hidden="true" /><h3>Layouts</h3><span className="cat-n">{layouts.length}</span><span className="ge-spacer" />
            {layoutsAllowed && <IconButton label="New layout" onClick={onNewLayout}><Plus size={15} /></IconButton>}</div>
          {!layouts.length && <p className="ws-muted cat-empty">No layouts saved yet. A new layout opens in its own tab; save it to keep it with the project.</p>}
          <ul className="cat-list">
            {layouts.map((l) => (
              <li key={l.id} className={`cat-item${current === `layout:${l.id}` ? " is-on" : ""}`}>
                {renaming === l.id ? <span className="cat-open"><FileText size={15} aria-hidden="true" />{nameField(l.id, l.name, (n) => onRenameLayout(l, n))}</span>
                  : <button type="button" className="cat-open" onClick={() => onOpenLayout(l)} onDoubleClick={() => canEdit && start(l.id, l.name)} title="Open this layout (double-click to rename)">
                    <FileText size={15} aria-hidden="true" /><span className="cat-name">{l.name}</span>
                  </button>}
                <Menu label={`${l.name}: more`} trigger={<span className="cat-more"><MoreHorizontal size={15} aria-hidden="true" /></span>}
                  items={[
                    { label: "Open", icon: <Printer size={15} />, onSelect: () => onOpenLayout(l) },
                    { label: "Rename", icon: <PencilLine size={15} />, disabled: !canEdit, onSelect: () => start(l.id, l.name) },
                    { label: "", divider: true, onSelect: () => undefined },
                    { label: "Delete", icon: <Trash2 size={15} />, danger: true, disabled: !canEdit, onSelect: () => onDeleteLayout(l) },
                  ]} />
              </li>
            ))}
          </ul>
        </section>
      </div>
    </aside>
  );
}
