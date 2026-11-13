import { useEffect, useState } from "react";
import { api, exportUrl } from "../lib/api";
import { CATEGORY_COLOURS, RAMPS, breaks, rampColours } from "../lib/mapstyle";
import type { FieldType, Layer, Style } from "../lib/types";
import { Button, ErrorText, useAction } from "./ui";

type Tab = "symbology" | "labels" | "fields" | "source";

/** The right-hand pane for the active layer: symbology, labels, fields, and where the data came from. */
export default function LayerPanel({ layer, canEdit, selected, onChange, onExport, initialTab = "symbology" }: {
  layer: Layer; canEdit: boolean; selected: number[]; onChange: (l: Layer) => void; onExport: () => void; initialTab?: Tab;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  return (
    <aside className="ws-panel" aria-label={`${layer.name}: properties`}>
      <div className="ws-pane-head"><strong title={layer.name}>{layer.name}</strong></div>
      <div className="ws-tabs" role="tablist">
        {(["symbology", "labels", "fields", "source"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
        ))}
      </div>
      <div className="ws-panel-body">
        {tab === "symbology" && <Symbology key={layer.id} layer={layer} canEdit={canEdit} onChange={onChange} />}
        {tab === "labels" && <LabelsTab key={layer.id} layer={layer} canEdit={canEdit} onChange={onChange} />}
        {tab === "fields" && <Fields key={layer.id} layer={layer} canEdit={canEdit} selected={selected} onChange={onChange} />}
        {tab === "source" && <Source layer={layer} onExport={onExport} />}
      </div>
    </aside>
  );
}

function Symbology({ layer, canEdit, onChange }: { layer: Layer; canEdit: boolean; onChange: (l: Layer) => void }) {
  const [s, setS] = useState<Style>(layer.style);
  const [n, setN] = useState(Math.max(2, layer.style.classes?.length || 5));
  const { busy, run } = useAction();
  const [error, setError] = useState<unknown>(null);
  useEffect(() => setS(layer.style), [layer.style]);
  const numeric = layer.fields.filter((f) => f.type === "integer" || f.type === "number");
  const fieldsFor = s.type === "graduated" ? numeric : layer.fields;

  async function classify(next: Style) {
    setError(null);
    if (next.type === "single" || !next.field) { setS(next); return; }
    try {
      const st = await api.stats(layer.id, next.field, n);
      if (next.type === "categorized") {
        const vals = st.values.slice(0, 40);
        next = { ...next, categories: vals.map((v, k) => ({ value: v.value ?? "", label: v.value ?? "(empty)", color: CATEGORY_COLOURS[k % CATEGORY_COLOURS.length] })) };
      } else {
        if (st.min == null || st.max == null) { setError(new Error(`${next.field} has no numbers to classify.`)); return; }
        const b = breaks(next.method === "equal" ? "equal" : "quantile", st.min, st.max, n, st.quantiles);
        const uniq = b.filter((x, k) => k === 0 || x > b[k - 1]);
        const cols = rampColours(next.ramp || "Greens", Math.max(1, uniq.length - 1));
        next = { ...next, classes: uniq.slice(0, -1).map((min, k) => ({ min, max: uniq[k + 1], color: cols[k] })) };
      }
      setS(next);
    } catch (e) { setError(e); }
  }
  const save = () => void run("style", () => api.updateLayer(layer.id, { style: s }), "Style saved.").then((l) => l && onChange(l));
  const disabled = !canEdit;
  return (
    <form className="ws-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <fieldset disabled={disabled}>
        <label className="ws-f">How to draw it
          <select value={s.type} onChange={(e) => void classify({ ...s, type: e.target.value as Style["type"], field: e.target.value === "single" ? null : (e.target.value === "graduated" ? numeric[0]?.name : layer.fields[0]?.name) ?? null })}>
            <option value="single">Single symbol</option>
            <option value="categorized" disabled={!layer.fields.length}>Unique values (categories)</option>
            <option value="graduated" disabled={!numeric.length}>Graduated colours (numbers)</option>
          </select></label>
        {s.type !== "single" && <>
          <label className="ws-f">Field
            <select value={s.field ?? ""} onChange={(e) => void classify({ ...s, field: e.target.value })}>
              {fieldsFor.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
            </select></label>
          {s.type === "graduated" && <div className="ws-row">
            <label className="ws-f">Method
              <select value={s.method === "equal" ? "equal" : "quantile"} onChange={(e) => void classify({ ...s, method: e.target.value as "equal" | "quantile" })}>
                <option value="quantile">Quantile</option><option value="equal">Equal interval</option></select></label>
            <label className="ws-f">Classes
              <input type="number" min={2} max={12} value={n} onChange={(e) => setN(Math.min(12, Math.max(2, Number(e.target.value) || 2)))} onBlur={() => void classify(s)} /></label>
            <label className="ws-f">Colours
              <select value={s.ramp || "Greens"} onChange={(e) => void classify({ ...s, ramp: e.target.value })}>
                {Object.keys(RAMPS).map((r) => <option key={r}>{r}</option>)}</select></label>
          </div>}
          <ul className="ws-classes">
            {(s.type === "categorized" ? s.categories ?? [] : s.classes ?? []).map((c, k) => (
              <li key={k}>
                <input type="color" aria-label="Colour" value={c.color} onChange={(e) => {
                  const color = e.target.value;
                  setS(s.type === "categorized" ? { ...s, categories: (s.categories ?? []).map((x, j) => (j === k ? { ...x, color } : x)) }
                    : { ...s, classes: (s.classes ?? []).map((x, j) => (j === k ? { ...x, color } : x)) });
                }} />
                <span>{"value" in c ? (c.label || c.value || "(empty)") : `${Number(c.min.toPrecision(5))} – ${Number(c.max.toPrecision(5))}`}</span>
              </li>
            ))}
          </ul>
        </>}
        {s.type === "single" && <label className="ws-f">Colour <input type="color" value={s.color} onChange={(e) => setS({ ...s, color: e.target.value })} /></label>}
        {layer.geometry_type !== "LineString" && <label className="ws-f">Outline <input type="color" value={s.stroke} onChange={(e) => setS({ ...s, stroke: e.target.value })} /></label>}
        <div className="ws-row">
          <label className="ws-f">{layer.geometry_type === "LineString" ? "Line width" : "Outline width"}
            <input type="number" min={0} max={20} step={0.5} value={s.width} onChange={(e) => setS({ ...s, width: Number(e.target.value) })} /></label>
          {layer.geometry_type === "Point" && <label className="ws-f">Size <input type="number" min={1} max={40} value={s.size} onChange={(e) => setS({ ...s, size: Number(e.target.value) })} /></label>}
          <label className="ws-f">Opacity <input type="range" min={0} max={1} step={0.05} value={s.opacity} onChange={(e) => setS({ ...s, opacity: Number(e.target.value) })} /></label>
        </div>
        {layer.geometry_type === "Polygon" && <fieldset className="ws-3d">
          <label className="lo-check"><input type="checkbox" checked={!!s.extrude}
            onChange={(e) => setS({ ...s, extrude: e.target.checked ? { field: numeric[0]?.name ?? null, height: 10, scale: 1, base: 0 } : null })} />Draw in 3D (extrude)</label>
          {s.extrude && <div className="ws-row">
            <label className="ws-f">Height from
              <select value={s.extrude.field ?? ""} onChange={(e) => setS({ ...s, extrude: { ...s.extrude!, field: e.target.value || null } })}>
                <option value="">A fixed height</option>{numeric.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}</select></label>
            {s.extrude.field
              ? <label className="ws-f">Metres per unit<input type="number" min={0.01} step="any" value={s.extrude.scale} onChange={(e) => setS({ ...s, extrude: { ...s.extrude!, scale: Number(e.target.value) || 1 } })} />
                <span className="ws-help">e.g. 3 when the field counts storeys</span></label>
              : <label className="ws-f">Height (m)<input type="number" min={0} step="any" value={s.extrude.height} onChange={(e) => setS({ ...s, extrude: { ...s.extrude!, height: Number(e.target.value) || 0 } })} /></label>}
          </div>}
          {s.extrude && <p className="ws-muted">Tilt the map (Map › 3D view, or right-drag) to see the heights.</p>}
        </fieldset>}
        <ErrorText error={error} />
        <Button type="submit" variant="primary" small busy={busy === "style"}>Apply</Button>
      </fieldset>
      {!canEdit && <p className="ws-muted">Only editors can change how a layer is drawn.</p>}
    </form>
  );
}

function LabelsTab({ layer, canEdit, onChange }: { layer: Layer; canEdit: boolean; onChange: (l: Layer) => void }) {
  const [lb, setLb] = useState(layer.labels ?? { field: layer.fields[0]?.name ?? "", size: 12, color: "#1a2233", halo: "#ffffff" });
  const { busy, run } = useAction();
  if (!layer.fields.length) return <p className="ws-muted">This layer has no fields to label with. Add one under Fields.</p>;
  return (
    <form className="ws-form" onSubmit={(e) => { e.preventDefault(); void run("labels", () => api.updateLayer(layer.id, { labels: lb }), "Labels on.").then((l) => l && onChange(l)); }}>
      <fieldset disabled={!canEdit}>
        <label className="ws-f">Label with
          <select value={lb.field} onChange={(e) => setLb({ ...lb, field: e.target.value })}>{layer.fields.map((f) => <option key={f.name}>{f.name}</option>)}</select></label>
        <div className="ws-row">
          <label className="ws-f">Size <input type="number" min={6} max={40} value={lb.size} onChange={(e) => setLb({ ...lb, size: Number(e.target.value) })} /></label>
          <label className="ws-f">Colour <input type="color" value={lb.color} onChange={(e) => setLb({ ...lb, color: e.target.value })} /></label>
          <label className="ws-f">Halo <input type="color" value={lb.halo} onChange={(e) => setLb({ ...lb, halo: e.target.value })} /></label>
        </div>
        <div className="ws-row">
          <Button type="submit" variant="primary" small busy={busy === "labels"}>{layer.labels ? "Update labels" : "Show labels"}</Button>
          {layer.labels && <Button small busy={busy === "off"} onClick={() => void run("off", () => api.updateLayer(layer.id, { clear_labels: true }), "Labels off.").then((l) => l && onChange(l))}>Turn off</Button>}
        </div>
      </fieldset>
    </form>
  );
}

const TYPES: [FieldType, string][] = [["string", "Text"], ["integer", "Whole number"], ["number", "Number"], ["boolean", "Yes / no"], ["date", "Date"]];

function Fields({ layer, canEdit, selected, onChange }: { layer: Layer; canEdit: boolean; selected: number[]; onChange: (l: Layer) => void }) {
  const { busy, run } = useAction();
  const [add, setAdd] = useState({ name: "", type: "string" as FieldType });
  const [calc, setCalc] = useState({ field: layer.fields[0]?.name ?? "", mode: "value" as "value" | "copy", value: "", from: "", only: selected.length > 0 });
  return (
    <div className="ws-form">
      <table className="ws-fields">
        <thead><tr><th>Field</th><th>Type</th>{canEdit && <th />}</tr></thead>
        <tbody>
          {layer.fields.map((f) => (
            <tr key={f.name}>
              <td>{f.name}</td><td>{TYPES.find((t) => t[0] === f.type)?.[1]}</td>
              {canEdit && <td className="ws-nowrap">
                <button type="button" onClick={() => { const to = window.prompt(`Rename ${f.name} to`, f.name); if (to && to !== f.name) void run("f", () => api.fieldOp(layer.id, { op: "rename", name: f.name, to }), "Renamed.").then((l) => l && onChange(l)); }}>Rename</button>
                <button type="button" className="ws-danger" onClick={() => { if (window.confirm(`Delete the field ${f.name} and its values in every feature?`)) void run("f", () => api.fieldOp(layer.id, { op: "delete", name: f.name }), "Field deleted.").then((l) => l && onChange(l)); }}>Delete</button>
              </td>}
            </tr>
          ))}
          {!layer.fields.length && <tr><td colSpan={3} className="ws-muted">No fields yet.</td></tr>}
        </tbody>
      </table>
      {canEdit && <>
        <h3>Add a field</h3>
        <form className="ws-row" onSubmit={(e) => { e.preventDefault(); void run("add", () => api.fieldOp(layer.id, { op: "add", name: add.name.trim(), type: add.type }), "Field added.").then((l) => { if (l) { onChange(l); setAdd({ name: "", type: "string" }); } }); }}>
          <label className="ws-f">Name <input required maxLength={64} value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} /></label>
          <label className="ws-f">Type <select value={add.type} onChange={(e) => setAdd({ ...add, type: e.target.value as FieldType })}>{TYPES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
          <Button type="submit" small busy={busy === "add"}>Add</Button>
        </form>
        {layer.fields.length > 0 && <>
          <h3>Field calculator</h3>
          <form className="ws-form" onSubmit={(e) => {
            e.preventDefault();
            const b = { field: calc.field, ids: calc.only ? selected : null, ...(calc.mode === "copy" ? { from_field: calc.from } : { value: calc.value }) };
            void run("calc", () => api.calculate(layer.id, b), "").then((r) => { if (r) { window.alert(`${r.changed.toLocaleString("en-GB")} feature${r.changed === 1 ? "" : "s"} changed.`); onChange({ ...layer, version: Date.now() }); } });
          }}>
            <div className="ws-row">
              <label className="ws-f">Set <select value={calc.field} onChange={(e) => setCalc({ ...calc, field: e.target.value })}>{layer.fields.map((f) => <option key={f.name}>{f.name}</option>)}</select></label>
              <label className="ws-f">to <select value={calc.mode} onChange={(e) => setCalc({ ...calc, mode: e.target.value as "value" | "copy" })}><option value="value">a value</option><option value="copy">another field</option></select></label>
            </div>
            {calc.mode === "value" ? <label className="ws-f">Value <input value={calc.value} onChange={(e) => setCalc({ ...calc, value: e.target.value })} placeholder="Empty clears it" /></label>
              : <label className="ws-f">Field <select required value={calc.from} onChange={(e) => setCalc({ ...calc, from: e.target.value })}><option value="">Choose…</option>{layer.fields.filter((f) => f.name !== calc.field).map((f) => <option key={f.name}>{f.name}</option>)}</select></label>}
            <label className="ws-check"><input type="checkbox" checked={calc.only} disabled={!selected.length} onChange={(e) => setCalc({ ...calc, only: e.target.checked })} /> Only the {selected.length.toLocaleString("en-GB")} selected</label>
            <Button type="submit" small busy={busy === "calc"}>Calculate</Button>
          </form>
        </>}
      </>}
    </div>
  );
}

function Source({ layer, onExport }: { layer: Layer; onExport: () => void }) {
  const b = layer.bbox;
  return (
    <div className="ws-form">
      <dl className="ws-dl">
        <dt>Features</dt><dd>{layer.feature_count.toLocaleString("en-GB")} {layer.geometry_type === "Point" ? "points" : layer.geometry_type === "LineString" ? "lines" : "polygons"}</dd>
        <dt>Came from</dt><dd>{layer.source.linked ? `${layer.source.linked.app_name}: ${layer.source.linked.label}` : layer.source.drawn ? "Drawn in PolyGIS" : layer.source.filename ?? "—"}</dd>
        {layer.source.linked && <><dt>Up to date as of</dt><dd>{layer.source.linked.refreshed_at ? new Date(layer.source.linked.refreshed_at).toLocaleString("en-GB") : "—"}</dd>
          <dt>Changes</dt><dd>Made in {layer.source.linked.app_name}; refresh the layer to see them (it also refreshes when the project opens).</dd></>}
        {layer.source.crs && <><dt>Its coordinate system</dt><dd>{layer.source.crs} (stored in WGS 84)</dd></>}
        {!!layer.source.dropped && <><dt>Left out</dt><dd>{layer.source.dropped} feature{layer.source.dropped === 1 ? "" : "s"} without a usable shape</dd></>}
        {b && <><dt>Extent</dt><dd className="ws-mono">{b.map((x) => x.toFixed(5)).join(", ")}</dd></>}
      </dl>
      <p className="ws-row"><Button small onClick={onExport}>Export…</Button>
        <a className="ge-btn ge-btn-sm" href={exportUrl(layer.id, "geojson")}>GeoJSON</a></p>
      <ErrorText error={null} />
    </div>
  );
}
