import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, exportUrl } from "../lib/api";
import type { Crs, Field, FieldType, Filter, Layer, Selection, VectorType } from "../lib/types";
import { FileUp, X } from "lucide-react";
import { Button, ErrorText, IconButton, useAction } from "./ui";

export function Dialog({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="ws-dialog-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`ws-dialog${wide ? " is-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="ws-dialog-head"><h2>{title}</h2><IconButton label="Close" onClick={onClose}><X size={18} /></IconButton></div>
        {children}
      </div>
    </div>
  );
}

/** Pick a coordinate system: the common Nigerian ones first, or search by name or EPSG code. */
export function CrsPicker({ value, onChange, label = "Coordinate system", allowNone }: { value: number | null; onChange: (e: number | null) => void; label?: string; allowNone?: string }) {
  const [q, setQ] = useState("");
  const [list, setList] = useState<Crs[]>([]);
  useEffect(() => {
    const t = setTimeout(() => void api.crs(q).then(setList, () => setList([])), q ? 250 : 0);
    return () => clearTimeout(t);
  }, [q]);
  const shown = value && !list.some((c) => c.epsg === value) ? [{ epsg: value, name: `EPSG:${value}` }, ...list] : list;
  return (
    <div className="ws-f">
      <span>{label}</span>
      <input placeholder="Search: Minna, UTM 32N, 26332…" value={q} onChange={(e) => setQ(e.target.value)} aria-label={`Search ${label}`} />
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)} aria-label={label} size={Math.min(7, shown.length + (allowNone ? 1 : 0))}>
        {allowNone && <option value="">{allowNone}</option>}
        {shown.map((c) => <option key={c.epsg} value={c.epsg}>EPSG:{c.epsg} · {c.name}</option>)}
      </select>
    </div>
  );
}

export function AddData({ projectId, onDone, onClose, onImagery }: { projectId: string; onDone: (l: Layer[]) => void; onClose: () => void; onImagery?: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [epsg, setEpsg] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  async function go(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true); setError(null);
    try { onDone(await api.importFile(projectId, file, epsg ?? undefined)); } catch (x) { setError(x); } finally { setBusy(false); }
  }
  return (
    <Dialog title="Add data" onClose={onClose}>
      <form className="ws-form" onSubmit={go}>
        <label className={`ws-drop${drag ? " is-over" : ""}`} onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); }}>
          <input type="file" accept=".geojson,.json,.zip,.gpkg,.kml,.kmz,.csv,.txt,.dxf,.gpx,.gml,.tif,.tiff,.img,.asc,.jp2,.hgt,.dem,.bil,.nc,.vrt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <FileUp size={28} aria-hidden="true" />
          <strong>{file ? file.name : "Drop a file here, or click to choose one"}</strong>
          <span>GeoJSON, Shapefile (zipped with its .shp, .shx, .dbf and .prj), GeoPackage, KML/KMZ, CSV with longitude/latitude or WKT, DXF, GPX; or a raster: GeoTIFF (elevation models, drone and satellite imagery), Erdas IMG, ASCII grid, JPEG 2000, SRTM .hgt</span>
        </label>
        {onImagery && <p className="ws-muted">No imagery file? <button type="button" className="ws-link" onClick={onImagery}>Get satellite imagery from an open source</button> (Sentinel-2, Landsat; Planetary Computer, Earth Search or Google Earth Engine) instead of downloading it yourself.</p>}
        <CrsPicker value={epsg} onChange={setEpsg} label="Its coordinate system" allowNone="As the file says (WGS 84 if it does not say)" />
        <p className="ws-muted">Choose one when the file does not say, e.g. a CSV of eastings and northings, or a Shapefile without its .prj.</p>
        <ErrorText error={error} />
        <div className="ws-row"><Button type="submit" variant="primary" busy={busy} disabled={!file}>Add to the map</Button><Button variant="ghost" onClick={onClose}>Cancel</Button></div>
      </form>
    </Dialog>
  );
}

const TYPES: [FieldType, string][] = [["string", "Text"], ["integer", "Whole number"], ["number", "Number"], ["boolean", "Yes / no"], ["date", "Date"]];

export function NewLayer({ projectId, onDone, onClose }: { projectId: string; onDone: (l: Layer) => void; onClose: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<VectorType>("Polygon");
  const [fields, setFields] = useState<Field[]>([{ name: "name", type: "string" }]);
  const { busy, run } = useAction();
  return (
    <Dialog title="New layer" onClose={onClose}>
      <form className="ws-form" onSubmit={(e) => { e.preventDefault(); void run("new", () => api.newLayer(projectId, { name: name.trim(), geometry_type: kind, fields: fields.filter((f) => f.name.trim()) }), "Layer made: start editing to draw in it.").then((l) => l && onDone(l)); }}>
        <label className="ws-f">Name <input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Plots surveyed in March" /></label>
        <div className="ws-f"><span>It holds</span>
          <div className="ws-seg">{(["Point", "LineString", "Polygon"] as VectorType[]).map((k) => (
            <label key={k} className={kind === k ? "is-on" : ""}><input type="radio" name="kind" checked={kind === k} onChange={() => setKind(k)} />{k === "Point" ? "Points" : k === "LineString" ? "Lines" : "Polygons"}</label>))}</div></div>
        <div className="ws-f"><span>Fields</span>
          {fields.map((f, k) => (
            <div className="ws-row" key={k}>
              <input aria-label={`Field ${k + 1} name`} value={f.name} maxLength={64} onChange={(e) => setFields(fields.map((x, j) => (j === k ? { ...x, name: e.target.value } : x)))} />
              <select aria-label={`Field ${k + 1} type`} value={f.type} onChange={(e) => setFields(fields.map((x, j) => (j === k ? { ...x, type: e.target.value as FieldType } : x)))}>{TYPES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>
              <button type="button" onClick={() => setFields(fields.filter((_, j) => j !== k))} aria-label={`Remove field ${k + 1}`}>✕</button>
            </div>
          ))}
          <button type="button" className="ws-link" onClick={() => setFields([...fields, { name: "", type: "string" }])}>+ Add a field</button>
        </div>
        <div className="ws-row"><Button type="submit" variant="primary" busy={busy === "new"}>Make the layer</Button><Button variant="ghost" onClick={onClose}>Cancel</Button></div>
      </form>
    </Dialog>
  );
}

const OPS: [Filter["op"], string][] = [["=", "is"], ["!=", "is not"], ["<", "less than"], ["<=", "at most"], [">", "more than"], [">=", "at least"],
  ["contains", "contains"], ["starts", "starts with"], ["empty", "is empty"], ["not_empty", "is not empty"]];

export function SelectByAttributes({ layer, onDone, onClose }: { layer: Layer; onDone: (s: Selection, add: boolean) => void; onClose: () => void }) {
  const [rows, setRows] = useState<Filter[]>([{ field: layer.fields[0]?.name ?? "", op: "=", value: "" }]);
  const [add, setAdd] = useState(false);
  const { busy, run } = useAction();
  if (!layer.fields.length) return <Dialog title="Select by attributes" onClose={onClose}><p>{layer.name} has no fields.</p></Dialog>;
  return (
    <Dialog title={`Select by attributes: ${layer.name}`} onClose={onClose} wide>
      <form className="ws-form" onSubmit={(e) => { e.preventDefault(); void run("sel", () => api.select(layer.id, { filters: rows.map((r) => ({ ...r, value: r.value === "" ? null : r.value })) }), "").then((s) => s && onDone(s, add)); }}>
        <p className="ws-muted">Features where every line holds:</p>
        {rows.map((r, k) => (
          <div className="ws-row" key={k}>
            <select aria-label="Field" value={r.field} onChange={(e) => setRows(rows.map((x, j) => (j === k ? { ...x, field: e.target.value } : x)))}>{layer.fields.map((f) => <option key={f.name}>{f.name}</option>)}</select>
            <select aria-label="Test" value={r.op} onChange={(e) => setRows(rows.map((x, j) => (j === k ? { ...x, op: e.target.value as Filter["op"] } : x)))}>{OPS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>
            {r.op !== "empty" && r.op !== "not_empty" && <input aria-label="Value" value={String(r.value ?? "")} onChange={(e) => setRows(rows.map((x, j) => (j === k ? { ...x, value: e.target.value } : x)))} />}
            <button type="button" onClick={() => setRows(rows.filter((_, j) => j !== k))} disabled={rows.length === 1} aria-label="Remove this line">✕</button>
          </div>
        ))}
        <button type="button" className="ws-link" onClick={() => setRows([...rows, { field: layer.fields[0].name, op: "=", value: "" }])}>+ And…</button>
        <label className="ws-check"><input type="checkbox" checked={add} onChange={(e) => setAdd(e.target.checked)} /> Add to the current selection</label>
        <div className="ws-row"><Button type="submit" variant="primary" busy={busy === "sel"}>Select</Button><Button variant="ghost" onClick={onClose}>Cancel</Button></div>
      </form>
    </Dialog>
  );
}

export function SelectByLocation({ layer, layers, selection, onDone, onClose }: { layer: Layer; layers: Layer[]; selection: Record<string, number[]>; onDone: (s: Selection, add: boolean) => void; onClose: () => void }) {
  const others = layers.filter((l) => l.id !== layer.id);
  const [b, setB] = useState({ other: others[0]?.id ?? "", relation: "intersects", distance: 100, onlySel: false, add: false });
  const { busy, run } = useAction();
  if (!others.length) return <Dialog title="Select by location" onClose={onClose}><p>Add another layer to select against.</p></Dialog>;
  const otherSel = selection[b.other] ?? [];
  return (
    <Dialog title={`Select by location: ${layer.name}`} onClose={onClose}>
      <form className="ws-form" onSubmit={(e) => { e.preventDefault(); void run("loc", () => api.select(layer.id, { other_layer_id: b.other, relation: b.relation, distance_m: b.distance, other_ids: b.onlySel ? otherSel : null }), "").then((s) => s && onDone(s, b.add)); }}>
        <label className="ws-f">Features of {layer.name} that
          <select value={b.relation} onChange={(e) => setB({ ...b, relation: e.target.value })}>
            <option value="intersects">intersect</option><option value="within">are completely within</option><option value="contains">contain</option><option value="distance">are within a distance of</option>
          </select></label>
        {b.relation === "distance" && <label className="ws-f">Distance (metres) <input type="number" min={0} step="any" value={b.distance} onChange={(e) => setB({ ...b, distance: Number(e.target.value) })} /></label>}
        <label className="ws-f">the features of
          <select value={b.other} onChange={(e) => setB({ ...b, other: e.target.value })}>{others.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
        <label className="ws-check"><input type="checkbox" checked={b.onlySel} disabled={!otherSel.length} onChange={(e) => setB({ ...b, onlySel: e.target.checked })} /> Only its {otherSel.length.toLocaleString("en-GB")} selected</label>
        <label className="ws-check"><input type="checkbox" checked={b.add} onChange={(e) => setB({ ...b, add: e.target.checked })} /> Add to the current selection</label>
        <div className="ws-row"><Button type="submit" variant="primary" busy={busy === "loc"}>Select</Button><Button variant="ghost" onClick={onClose}>Cancel</Button></div>
      </form>
    </Dialog>
  );
}

export function Export({ layer, selected, epsg, onClose }: { layer: Layer; selected: number[]; epsg: number; onClose: () => void }) {
  const [fmt, setFmt] = useState("gpkg");
  const [crs, setCrs] = useState<number | null>(epsg);
  const [onlySel, setOnlySel] = useState(selected.length > 0);
  const [error] = useState<unknown>(null);
  const keepsWgs = fmt === "geojson" || fmt === "kml";
  return (
    <Dialog title={`Export ${layer.name}`} onClose={onClose}>
      <div className="ws-form">
        <label className="ws-f">Format
          <select value={fmt} onChange={(e) => setFmt(e.target.value)}>
            <option value="gpkg">GeoPackage (.gpkg)</option><option value="shp">Shapefile (.zip)</option><option value="geojson">GeoJSON</option>
            <option value="csv">CSV with WKT geometry</option><option value="kml">KML (Google Earth)</option></select></label>
        {keepsWgs ? <p className="ws-muted">GeoJSON and KML are always in WGS 84.</p> : <CrsPicker value={crs} onChange={setCrs} label="In the coordinate system" />}
        <label className="ws-check"><input type="checkbox" checked={onlySel} disabled={!selected.length} onChange={(e) => setOnlySel(e.target.checked)} /> Only the {selected.length.toLocaleString("en-GB")} selected</label>
        <ErrorText error={error} />
        <div className="ws-row">
          <a className="ge-btn ge-btn-primary" href={exportUrl(layer.id, fmt, keepsWgs ? 4326 : crs ?? 4326, onlySel ? selected : undefined)} onClick={() => setTimeout(onClose, 300)}>Download</a>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </Dialog>
  );
}
