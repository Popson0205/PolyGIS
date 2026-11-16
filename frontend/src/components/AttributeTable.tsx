import { ArrowDown, ArrowUp, Calculator, Columns3, Plus, Ruler, Search, Table2, Trash2, Type, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import type { FieldType, Filter, Layer, Row } from "../lib/types";
import { Dialog } from "./Dialogs";
import { Button, ErrorText, IconButton, Menu, useToast } from "./ui";

const PAGE = 200;

const TYPES: { v: FieldType; label: string }[] = [
  { v: "string", label: "Text" }, { v: "integer", label: "Whole number" }, { v: "number", label: "Decimal number" },
  { v: "boolean", label: "Yes / No" }, { v: "date", label: "Date" },
];

type Dlg = { kind: "add" } | { kind: "rename" } | { kind: "delete" } | { kind: "calc" } | { kind: "geom" } | null;

/** The attribute table: one continuously scrolling page (more rows load as you reach the bottom), sorted by any field,
 *  all or only the selection. Click a row to select it (Ctrl or Shift to add); double-click to zoom to it. While the
 *  layer is being edited, cells can be changed, fields added, renamed or deleted, and fields filled from a value, another
 *  field or a geometry measure (area, length, longitude/latitude). */
export default function AttributeTable({ layer, selected, editing, resizer, onSelect, onZoomTo, onClose, onEdited }: {
  layer: Layer; selected: number[]; editing: boolean; resizer?: ReactNode; onSelect: (ids: number[], add: boolean) => void;
  onZoomTo: (id: number) => void; onClose: () => void; onEdited: () => void;
}) {
  const toast = useToast();
  const [onlySel, setOnlySel] = useState(false);
  const [sort, setSort] = useState<{ field: string | null; desc: boolean }>({ field: null, desc: false });
  const [search, setSearch] = useState<{ field: string; text: string }>({ field: layer.fields.find((f) => f.type === "string")?.name ?? layer.fields[0]?.name ?? "", text: "" });
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [cell, setCell] = useState<{ id: number; field: string; value: string } | null>(null);
  const [dlg, setDlg] = useState<Dlg>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const rowsRef = useRef<Row[]>([]);
  const busy = useRef(false);
  rowsRef.current = rows;

  const filters = useCallback((): Filter[] =>
    search.text.trim() && search.field ? [{ field: search.field, op: "contains", value: search.text.trim() }] : [], [search]);

  // One query is defined by the layer (and its version), the sort, the search, and the selection when "Selected only".
  const key = [layer.id, layer.version, sort.field, sort.desc, onlySel, onlySel ? selected.join(",") : "", search.field, search.text].join("|");

  const fetchPage = useCallback(async (fresh: boolean) => {
    if (busy.current) return;
    busy.current = true; setLoading(true);
    try {
      const offset = fresh ? 0 : rowsRef.current.length;
      const d = await api.rows(layer.id, { filters: filters(), sort: sort.field, desc: sort.desc, offset, limit: PAGE, ids: onlySel ? selected : null });
      setTotal(d.total);
      setRows((prev) => (fresh ? d.rows : [...prev, ...d.rows]));
      setError(null);
    } catch (e) { setError(e); }
    finally { busy.current = false; setLoading(false); }
  }, [layer.id, filters, sort, onlySel, selected]);

  // A fresh query: back to the top, load the first page.
  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = 0;
    setRows([]); rowsRef.current = []; setTotal(null);
    void fetchPage(true);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const atEnd = total != null && rows.length >= total;
  const onScroll = () => {
    const el = scroller.current;
    if (!el || busy.current || atEnd) return;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 240) void fetchPage(false);
  };

  async function saveCell() {
    if (!cell) return;
    try {
      await api.changeFeature(layer.id, cell.id, { properties: { [cell.field]: cell.value } });
      setRows((rs) => rs.map((r) => (r.id === cell.id ? { ...r, props: { ...r.props, [cell.field]: cell.value } } : r)));
      setCell(null); onEdited();
    } catch (e) { setError(e); }
  }

  const reload = () => { onEdited(); void fetchPage(true); };
  const sel = new Set(selected);
  const count = total != null ? `${total.toLocaleString("en-GB")} ${onlySel ? "selected" : "features"}` : "…";

  return (
    <section className="ws-table" aria-label={`${layer.name}: attribute table`}>
      {resizer}
      <div className="ws-table-bar">
        <Table2 size={16} aria-hidden="true" style={{ color: "var(--c-navy)", flex: "none" }} />
        <strong>{layer.name}</strong>
        <span className="ws-count">{count}{selected.length > 0 && !onlySel ? `, ${selected.length.toLocaleString("en-GB")} selected` : ""}</span>
        <label className="ws-check"><input type="checkbox" checked={onlySel} onChange={(e) => setOnlySel(e.target.checked)} disabled={!selected.length && !onlySel} /> Selected only</label>
        {editing && (
          <div className="ws-table-tools">
            <Menu label="Fields" align="start" className="tbl-menu" trigger={<><Columns3 size={14} aria-hidden="true" />Fields</>} items={[
              { label: "Add field…", icon: <Plus size={14} />, onSelect: () => setDlg({ kind: "add" }) },
              { label: "Rename a field…", icon: <Type size={14} />, disabled: !layer.fields.length, onSelect: () => setDlg({ kind: "rename" }) },
              { label: "Delete a field…", icon: <Trash2 size={14} />, danger: true, disabled: !layer.fields.length, onSelect: () => setDlg({ kind: "delete" }) },
            ]} />
            <Menu label="Calculate" align="start" className="tbl-menu" trigger={<><Calculator size={14} aria-hidden="true" />Calculate</>} items={[
              { label: "Calculate field…", icon: <Calculator size={14} />, onSelect: () => setDlg({ kind: "calc" }) },
              { label: "Calculate geometry…", icon: <Ruler size={14} />, onSelect: () => setDlg({ kind: "geom" }) },
            ]} />
          </div>
        )}
        {layer.fields.length > 0 && <span className="ws-search">
          <Search size={14} aria-hidden="true" style={{ alignSelf: "center", color: "var(--c-faint)" }} />
          <select aria-label="Search in field" value={search.field} onChange={(e) => setSearch({ ...search, field: e.target.value })}>{layer.fields.map((f) => <option key={f.name}>{f.name}</option>)}</select>
          <input aria-label="Search" placeholder="contains…" value={search.text} onChange={(e) => setSearch({ ...search, text: e.target.value })} />
        </span>}
        <span className="ge-spacer" />
        <IconButton label="Close the table" onClick={onClose}><X size={16} /></IconButton>
      </div>
      <ErrorText error={error} />
      <div className="ws-table-scroll" ref={scroller} onScroll={onScroll}>
        <table className="ws-grid">
          <thead><tr>
            <th className="ws-oid">ID</th>
            {layer.fields.map((f) => (
              <th key={f.name} aria-sort={sort.field === f.name ? (sort.desc ? "descending" : "ascending") : "none"}>
                <button type="button" onClick={() => setSort({ field: f.name, desc: sort.field === f.name ? !sort.desc : false })}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>{f.name}{sort.field === f.name ? (sort.desc ? <ArrowDown size={12} aria-hidden="true" /> : <ArrowUp size={12} aria-hidden="true" />) : null}</span></button></th>
            ))}
            <th className="ws-fill" aria-hidden="true" />
          </tr></thead>
          <tbody>
            {rows.map((r) => (
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
                    {v == null ? <span className="ws-null">empty</span> : typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)}</td>;
                })}
                <td className="ws-fill" />
              </tr>
            ))}
          </tbody>
        </table>
        {total === 0 && !loading && <p className="ws-empty">No features{search.text ? " match" : ""}.</p>}
        {loading && <p className="ws-loading-row">Loading…</p>}
        {atEnd && rows.length > PAGE && <p className="ws-loading-row ws-muted">All {total?.toLocaleString("en-GB")} shown.</p>}
      </div>
      {editing ? <p className="ws-hint">Editing: double-click a cell to change it; Enter saves, Esc cancels. Use Fields and Calculate above to change the columns.</p>
        : <p className="ws-hint">Click a row to select it (Ctrl-click adds); double-click to zoom to it.</p>}

      {dlg?.kind === "add" && <AddFieldDialog layer={layer} onClose={() => setDlg(null)} onDone={(msg) => { setDlg(null); toast(msg, "success"); reload(); }} onError={setError} />}
      {dlg?.kind === "rename" && <RenameFieldDialog layer={layer} onClose={() => setDlg(null)} onDone={(msg) => { setDlg(null); toast(msg, "success"); reload(); }} onError={setError} />}
      {dlg?.kind === "delete" && <DeleteFieldDialog layer={layer} onClose={() => setDlg(null)} onDone={(msg) => { setDlg(null); toast(msg, "success"); reload(); }} onError={setError} />}
      {dlg?.kind === "calc" && <CalcFieldDialog layer={layer} selected={selected} onClose={() => setDlg(null)} onDone={(msg) => { setDlg(null); toast(msg, "success"); reload(); }} onError={setError} />}
      {dlg?.kind === "geom" && <CalcGeometryDialog layer={layer} selected={selected} onClose={() => setDlg(null)} onDone={(msg) => { setDlg(null); toast(msg, "success"); reload(); }} onError={setError} />}
    </section>
  );
}

type DlgProps = { layer: Layer; onClose: () => void; onDone: (msg: string) => void; onError: (e: unknown) => void };

function AddFieldDialog({ layer, onClose, onDone, onError }: DlgProps) {
  const [name, setName] = useState("");
  const [type, setType] = useState<FieldType>("string");
  const [busy, setBusy] = useState(false);
  const taken = layer.fields.some((f) => f.name.toLowerCase() === name.trim().toLowerCase());
  async function go() {
    setBusy(true);
    try { await api.changeFields(layer.id, { op: "add", name: name.trim(), type }); onDone(`Added the field “${name.trim()}”.`); }
    catch (e) { onError(e); setBusy(false); }
  }
  return (
    <Dialog title="Add field" onClose={onClose}>
      <div className="ws-form">
        <label className="ws-f"><span>Name</span><input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. population" maxLength={64} /></label>
        {taken && <p className="ws-warn">A field with that name already exists.</p>}
        <label className="ws-f"><span>Type</span><select value={type} onChange={(e) => setType(e.target.value as FieldType)}>{TYPES.map((t) => <option key={t.v} value={t.v}>{t.label}</option>)}</select></label>
      </div>
      <div className="ws-row"><Button variant="primary" busy={busy} disabled={!name.trim() || taken} onClick={() => void go()}>Add field</Button><Button variant="ghost" onClick={onClose}>Cancel</Button></div>
    </Dialog>
  );
}

function RenameFieldDialog({ layer, onClose, onDone, onError }: DlgProps) {
  const [name, setName] = useState(layer.fields[0]?.name ?? "");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const taken = layer.fields.some((f) => f.name !== name && f.name.toLowerCase() === to.trim().toLowerCase());
  async function go() {
    setBusy(true);
    try { await api.changeFields(layer.id, { op: "rename", name, to: to.trim() }); onDone(`Renamed “${name}” to “${to.trim()}”.`); }
    catch (e) { onError(e); setBusy(false); }
  }
  return (
    <Dialog title="Rename field" onClose={onClose}>
      <div className="ws-form">
        <label className="ws-f"><span>Field</span><select value={name} onChange={(e) => setName(e.target.value)}>{layer.fields.map((f) => <option key={f.name}>{f.name}</option>)}</select></label>
        <label className="ws-f"><span>New name</span><input autoFocus value={to} onChange={(e) => setTo(e.target.value)} placeholder="New name" maxLength={64} /></label>
        {taken && <p className="ws-warn">A field with that name already exists.</p>}
      </div>
      <div className="ws-row"><Button variant="primary" busy={busy} disabled={!to.trim() || taken} onClick={() => void go()}>Rename</Button><Button variant="ghost" onClick={onClose}>Cancel</Button></div>
    </Dialog>
  );
}

function DeleteFieldDialog({ layer, onClose, onDone, onError }: DlgProps) {
  const [name, setName] = useState(layer.fields[0]?.name ?? "");
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    try { await api.changeFields(layer.id, { op: "delete", name }); onDone(`Deleted the field “${name}”.`); }
    catch (e) { onError(e); setBusy(false); }
  }
  return (
    <Dialog title="Delete field" onClose={onClose}>
      <div className="ws-form">
        <label className="ws-f"><span>Field</span><select value={name} onChange={(e) => setName(e.target.value)}>{layer.fields.map((f) => <option key={f.name}>{f.name}</option>)}</select></label>
        <p className="ws-warn">This removes the field and its values from every feature. It cannot be undone.</p>
      </div>
      <div className="ws-row"><Button variant="danger" busy={busy} onClick={() => void go()}>Delete field</Button><Button variant="ghost" onClick={onClose}>Cancel</Button></div>
    </Dialog>
  );
}

/** Ensure a field exists (creating it as `type` when the typed name is new); returns nothing, throws on failure. */
async function ensureField(layer: Layer, name: string, type: FieldType) {
  if (!layer.fields.some((f) => f.name === name)) await api.changeFields(layer.id, { op: "add", name, type });
}

function CalcFieldDialog({ layer, selected, onClose, onDone, onError }: DlgProps & { selected: number[] }) {
  const numeric = layer.fields;
  const [field, setField] = useState(layer.fields[0]?.name ?? "");
  const [newField, setNewField] = useState(false);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<FieldType>("number");
  const [mode, setMode] = useState<"value" | "copy">("value");
  const [value, setValue] = useState("");
  const [from, setFrom] = useState(layer.fields.find((f) => f.name !== field)?.name ?? layer.fields[0]?.name ?? "");
  const [onlySel, setOnlySel] = useState(false);
  const [busy, setBusy] = useState(false);
  const target = newField ? newName.trim() : field;
  async function go() {
    setBusy(true);
    try {
      if (newField) await ensureField(layer, target, newType);
      const ids = onlySel && selected.length ? selected : null;
      const r = await api.calculate(layer.id, mode === "copy" ? { field: target, from_field: from, ids } : { field: target, value, ids });
      onDone(`Set “${target}” on ${r.changed.toLocaleString("en-GB")} feature${r.changed === 1 ? "" : "s"}.`);
    } catch (e) { onError(e); setBusy(false); }
  }
  return (
    <Dialog title="Calculate field" onClose={onClose}>
      <div className="ws-form">
        <label className="ws-f"><span>Field to set</span>
          {newField
            ? <input autoFocus value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="New field name" maxLength={64} />
            : <select value={field} onChange={(e) => setField(e.target.value)}>{numeric.map((f) => <option key={f.name}>{f.name}</option>)}</select>}
        </label>
        <label className="ws-check"><input type="checkbox" checked={newField} onChange={(e) => setNewField(e.target.checked)} /> Make a new field</label>
        {newField && <label className="ws-f"><span>New field type</span><select value={newType} onChange={(e) => setNewType(e.target.value as FieldType)}>{TYPES.map((t) => <option key={t.v} value={t.v}>{t.label}</option>)}</select></label>}
        <label className="ws-f"><span>Set it to</span><select value={mode} onChange={(e) => setMode(e.target.value as "value" | "copy")}><option value="value">A value</option><option value="copy">Another field’s value</option></select></label>
        {mode === "value"
          ? <label className="ws-f"><span>Value</span><input value={value} onChange={(e) => setValue(e.target.value)} placeholder="e.g. 0, Lagos, true" /></label>
          : <label className="ws-f"><span>Copy from</span><select value={from} onChange={(e) => setFrom(e.target.value)}>{layer.fields.map((f) => <option key={f.name}>{f.name}</option>)}</select></label>}
        {selected.length > 0 && <label className="ws-check"><input type="checkbox" checked={onlySel} onChange={(e) => setOnlySel(e.target.checked)} /> Only the {selected.length.toLocaleString("en-GB")} selected</label>}
      </div>
      <div className="ws-row"><Button variant="primary" busy={busy} disabled={!target} onClick={() => void go()}>Calculate</Button><Button variant="ghost" onClick={onClose}>Cancel</Button></div>
    </Dialog>
  );
}

function CalcGeometryDialog({ layer, selected, onClose, onDone, onError }: DlgProps & { selected: number[] }) {
  const g = layer.geometry_type;
  const measures: { v: string; label: string }[] =
    g.includes("Polygon") ? [{ v: "area_m2", label: "Area (m²)" }, { v: "area_ha", label: "Area (hectares)" }, { v: "perimeter_m", label: "Perimeter (m)" }]
    : g.includes("Line") ? [{ v: "length_m", label: "Length (m)" }]
    : [{ v: "longitude", label: "Longitude" }, { v: "latitude", label: "Latitude" }];
  const numeric = layer.fields.filter((f) => f.type === "number" || f.type === "integer");
  const [measure, setMeasure] = useState(measures[0].v);
  const [field, setField] = useState(numeric[0]?.name ?? "");
  const [newField, setNewField] = useState(!numeric.length);
  const [newName, setNewName] = useState(measures[0].v);
  const [onlySel, setOnlySel] = useState(false);
  const [busy, setBusy] = useState(false);
  const target = newField ? newName.trim() : field;
  async function go() {
    setBusy(true);
    try {
      if (newField) await ensureField(layer, target, "number");
      const ids = onlySel && selected.length ? selected : null;
      const r = await api.calculateGeometry(layer.id, { field: target, measure, ids });
      onDone(`Wrote ${measures.find((m) => m.v === measure)?.label.toLowerCase()} to “${target}” on ${r.changed.toLocaleString("en-GB")} feature${r.changed === 1 ? "" : "s"}.`);
    } catch (e) { onError(e); setBusy(false); }
  }
  return (
    <Dialog title="Calculate geometry" onClose={onClose}>
      <div className="ws-form">
        <label className="ws-f"><span>Measure</span><select value={measure} onChange={(e) => { setMeasure(e.target.value); if (newField) setNewName(e.target.value); }}>{measures.map((m) => <option key={m.v} value={m.v}>{m.label}</option>)}</select></label>
        <label className="ws-f"><span>Store in field</span>
          {newField
            ? <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="New field name" maxLength={64} />
            : <select value={field} onChange={(e) => setField(e.target.value)}>{numeric.map((f) => <option key={f.name}>{f.name}</option>)}</select>}
        </label>
        <label className="ws-check"><input type="checkbox" checked={newField} onChange={(e) => setNewField(e.target.checked)} disabled={!numeric.length} /> Make a new number field</label>
        {selected.length > 0 && <label className="ws-check"><input type="checkbox" checked={onlySel} onChange={(e) => setOnlySel(e.target.checked)} /> Only the {selected.length.toLocaleString("en-GB")} selected</label>}
        <p className="ws-muted">Lengths and areas are measured on the Earth’s surface, in metres.</p>
      </div>
      <div className="ws-row"><Button variant="primary" busy={busy} disabled={!target} onClick={() => void go()}>Calculate</Button><Button variant="ghost" onClick={onClose}>Cancel</Button></div>
    </Dialog>
  );
}
