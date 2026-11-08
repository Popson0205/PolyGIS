import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Filter, Layer, Row } from "../lib/types";
import { ErrorText } from "./ui";

const PAGE = 100;

/** The attribute table: a page at a time, sorted by any field, all or only the selection. Click a row to select it
 *  (Ctrl or Shift to add); double-click to zoom to it. Cells can be changed while the layer is being edited. */
export default function AttributeTable({ layer, selected, editing, onSelect, onZoomTo, onClose, onEdited }: {
  layer: Layer; selected: number[]; editing: boolean; onSelect: (ids: number[], add: boolean) => void;
  onZoomTo: (id: number) => void; onClose: () => void; onEdited: () => void;
}) {
  const [onlySel, setOnlySel] = useState(false);
  const [sort, setSort] = useState<{ field: string | null; desc: boolean }>({ field: null, desc: false });
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState<{ field: string; text: string }>({ field: layer.fields.find((f) => f.type === "string")?.name ?? layer.fields[0]?.name ?? "", text: "" });
  const [data, setData] = useState<{ total: number; rows: Row[] } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [cell, setCell] = useState<{ id: number; field: string; value: string } | null>(null);

  const load = useCallback(async () => {
    const filters: Filter[] = search.text.trim() && search.field ? [{ field: search.field, op: "contains", value: search.text.trim() }] : [];
    try {
      setData(await api.rows(layer.id, { filters, sort: sort.field, desc: sort.desc, offset: page * PAGE, limit: PAGE, ids: onlySel ? selected : null }));
      setError(null);
    } catch (e) { setError(e); }
  }, [layer.id, layer.version, sort, page, onlySel, onlySel ? selected : null, search]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void load(); }, [load]);
  useEffect(() => setPage(0), [layer.id, onlySel, search, sort]);

  async function saveCell() {
    if (!cell) return;
    try {
      await api.changeFeature(layer.id, cell.id, { properties: { [cell.field]: cell.value } });
      setCell(null); onEdited(); void load();
    } catch (e) { setError(e); }
  }
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;
  const sel = new Set(selected);
  return (
    <section className="ws-table" aria-label={`${layer.name}: attribute table`}>
      <div className="ws-table-bar">
        <strong>{layer.name}</strong>
        <span className="ws-muted">{data ? `${data.total.toLocaleString("en-GB")} ${onlySel ? "selected" : "features"}` : "…"}{selected.length > 0 && !onlySel ? ` · ${selected.length.toLocaleString("en-GB")} selected` : ""}</span>
        <label className="ws-check"><input type="checkbox" checked={onlySel} onChange={(e) => setOnlySel(e.target.checked)} disabled={!selected.length && !onlySel} /> Selected only</label>
        {layer.fields.length > 0 && <span className="ws-search">
          <select aria-label="Search in field" value={search.field} onChange={(e) => setSearch({ ...search, field: e.target.value })}>{layer.fields.map((f) => <option key={f.name}>{f.name}</option>)}</select>
          <input aria-label="Search" placeholder="contains…" value={search.text} onChange={(e) => setSearch({ ...search, text: e.target.value })} />
        </span>}
        <span className="ge-spacer" />
        <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="Previous page">‹</button>
        <span className="ws-muted">{page + 1} / {pages}</span>
        <button type="button" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)} aria-label="Next page">›</button>
        <button type="button" onClick={onClose} aria-label="Close the table">✕</button>
      </div>
      <ErrorText error={error} />
      <div className="ws-table-scroll">
        <table className="ws-grid">
          <thead><tr>
            <th className="ws-oid">ID</th>
            {layer.fields.map((f) => (
              <th key={f.name} aria-sort={sort.field === f.name ? (sort.desc ? "descending" : "ascending") : "none"}>
                <button type="button" onClick={() => setSort({ field: f.name, desc: sort.field === f.name ? !sort.desc : false })}>
                  {f.name}{sort.field === f.name ? (sort.desc ? " ▼" : " ▲") : ""}</button></th>
            ))}
          </tr></thead>
          <tbody>
            {data?.rows.map((r) => (
              <tr key={r.id} className={sel.has(r.id) ? "is-sel" : ""} onClick={(e) => onSelect([r.id], e.ctrlKey || e.metaKey || e.shiftKey)} onDoubleClick={() => onZoomTo(r.id)}>
                <td className="ws-oid">{r.id}</td>
                {layer.fields.map((f) => {
                  const v = r.props[f.name];
                  if (cell && cell.id === r.id && cell.field === f.name) {
                    return <td key={f.name}><input autoFocus className="ws-cell" value={cell.value} onChange={(e) => setCell({ ...cell, value: e.target.value })}
                      onKeyDown={(e) => { if (e.key === "Enter") void saveCell(); if (e.key === "Escape") setCell(null); }} onBlur={() => void saveCell()} /></td>;
                  }
                  return <td key={f.name} className={typeof v === "number" ? "ws-num" : ""}
                    onDoubleClick={(e) => { if (editing) { e.stopPropagation(); setCell({ id: r.id, field: f.name, value: v == null ? "" : String(v) }); } }}>
                    {v == null ? <span className="ws-null">&lt;null&gt;</span> : typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.rows.length === 0 && <p className="ws-empty">No features{search.text ? " match" : ""}.</p>}
      </div>
      {editing && <p className="ws-hint">Editing: double-click a cell to change it; Enter saves.</p>}
    </section>
  );
}
