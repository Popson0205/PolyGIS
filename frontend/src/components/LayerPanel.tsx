import { ArrowDown, ArrowDownUp, ArrowUp, ChevronDown, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, exportUrl } from "../lib/api";
import { CATEGORY_COLOURS, RAMPS, breaks, rampColours } from "../lib/mapstyle";
import type { FieldType, Labels, Layer, Style } from "../lib/types";
import { AutoApplyBar, useAutoApply } from "./AutoApply";
import { Button, ErrorText, IconButton, useAction } from "./ui";

type Tab = "symbology" | "labels" | "fields" | "source";

/** The right-hand pane for the active layer: symbology, labels, fields, and where the data came from. */
export default function LayerPanel({ layer, canEdit, selected, onChange, onExport, initialTab = "symbology" }: {
  layer: Layer; canEdit: boolean; selected: number[]; onChange: (l: Layer) => void; onExport: () => void; initialTab?: Tab;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  return (
    <aside className="ws-panel" aria-label={`${layer.name}: properties`}>
      <div className="ws-pane-head"><h2 className="ws-pane-title" title={layer.name}>{layer.name}</h2></div>
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

/** A colour scheme's colours, n of them (categories go round the palette; ramps are spread). */
function schemeColours(name: string, n: number): string[] {
  if (name === "Default" || !RAMPS[name]) return Array.from({ length: n }, (_, k) => CATEGORY_COLOURS[k % CATEGORY_COLOURS.length]);
  return rampColours(name, Math.max(1, n));
}
const SCHEMES = ["Default", ...Object.keys(RAMPS)];
const swatch = (name: string) => `linear-gradient(90deg, ${schemeColours(name, name === "Default" ? 10 : 7).join(", ")})`;

function SchemePicker({ value, onChange, disabled }: { value: string; onChange: (v: string) => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sy-scheme">
      <button type="button" className="sy-scheme-btn" disabled={disabled} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        aria-label={`Color scheme: ${value}`}><span style={{ background: swatch(value) }} /><ChevronDown size={14} aria-hidden="true" /></button>
      {open && <ul role="listbox" className="sy-scheme-list">
        {SCHEMES.map((n) => <li key={n} role="option" aria-selected={n === value}>
          <button type="button" onClick={() => { onChange(n); setOpen(false); }}><span style={{ background: swatch(n) }} />{n}</button></li>)}
      </ul>}
    </div>
  );
}

function Symbology({ layer, canEdit, onChange }: { layer: Layer; canEdit: boolean; onChange: (l: Layer) => void }) {
  const [s, setS] = useState<Style>(layer.style);
  const [n, setN] = useState(Math.max(2, layer.style.classes?.length || 5));
  const [picked, setPicked] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => setS(layer.style), [layer.style]);
  const numeric = layer.fields.filter((f) => f.type === "integer" || f.type === "number");
  const fieldsFor = s.type === "graduated" ? numeric : layer.fields;
  const live = useAutoApply<Style>({ layer, canEdit, onChange, preview: (v) => ({ ...layer, style: v }),
    save: (v) => api.updateLayer(layer.id, { style: v }), keepVersion: () => true });
  /** A change to the symbology: kept here, shown on the map and saved (with Auto Apply on). */
  const put = (next: Style) => { setS(next); live.change(next); };

  async function classify(next: Style) {
    setError(null);
    if (next.type === "single" || !next.field) { put(next); return; }
    try {
      const st = await api.stats(layer.id, next.field, n);
      if (next.type === "categorized") {
        const vals = st.values.slice(0, 40);
        const cols = schemeColours(next.ramp || "Default", vals.length);
        next = { ...next, categories: vals.map((v, k) => ({ value: v.value ?? "", label: v.value ?? "(empty)", color: cols[k] })) };
      } else {
        if (st.min == null || st.max == null) { setError(new Error(`${next.field} has no numbers to classify.`)); return; }
        const b = breaks(next.method === "equal" ? "equal" : "quantile", st.min, st.max, n, st.quantiles);
        const uniq = b.filter((x, k) => k === 0 || x > b[k - 1]);
        const cols = rampColours(next.ramp && RAMPS[next.ramp] ? next.ramp : "Greens", Math.max(1, uniq.length - 1));
        next = { ...next, classes: uniq.slice(0, -1).map((min, k) => ({ min, max: uniq[k + 1], color: cols[k] })) };
      }
      put(next);
    } catch (e) { setError(e); }
  }
  const rows = s.type === "categorized" ? s.categories ?? [] : s.type === "graduated" ? s.classes ?? [] : [];
  const setRow = (k: number, x: Record<string, unknown>) => put(s.type === "categorized"
    ? { ...s, categories: (s.categories ?? []).map((c, j) => (j === k ? { ...c, ...x } : c)) }
    : { ...s, classes: (s.classes ?? []).map((c, j) => (j === k ? { ...c, ...x } : c)) });
  const recolour = (scheme: string) => {
    const cols = s.type === "graduated" ? rampColours(RAMPS[scheme] ? scheme : "Greens", rows.length) : schemeColours(scheme, rows.length);
    put(s.type === "categorized" ? { ...s, ramp: scheme, categories: (s.categories ?? []).map((c, k) => ({ ...c, color: cols[k] })) }
      : { ...s, ramp: scheme, classes: (s.classes ?? []).map((c, k) => ({ ...c, color: cols[k] })) });
  };
  const reverse = () => {
    const cols = rows.map((r) => r.color).reverse();
    put(s.type === "categorized" ? { ...s, categories: (s.categories ?? []).map((c, k) => ({ ...c, color: cols[k] })) }
      : { ...s, classes: (s.classes ?? []).map((c, k) => ({ ...c, color: cols[k] })) });
  };
  const moveRow = (k: number, by: -1 | 1) => {
    const list = [...(s.categories ?? [])];
    const j = k + by;
    if (j < 0 || j >= list.length) return;
    [list[k], list[j]] = [list[j], list[k]];
    setPicked(j);
    put({ ...s, categories: list });
  };
  const removeRow = (k: number) => {
    setPicked(null);
    put(s.type === "categorized" ? { ...s, categories: (s.categories ?? []).filter((_, j) => j !== k) } : { ...s, classes: (s.classes ?? []).filter((_, j) => j !== k) });
  };
  const fmt = (v: number) => Number(v.toPrecision(5)).toLocaleString("en-GB");
  const geom = layer.geometry_type;
  const dis = !canEdit;
  return (
    <div className="ws-form sy">
      <fieldset disabled={dis} className="sy-body">
        <label className="ws-f">Primary symbology
          <select value={s.type} onChange={(e) => void classify({ ...s, type: e.target.value as Style["type"], ramp: e.target.value === "categorized" ? "Default" : s.type === "categorized" ? "Greens" : s.ramp,
            field: e.target.value === "single" ? null : (e.target.value === "graduated" ? numeric[0]?.name : layer.fields[0]?.name) ?? null })}>
            <option value="single">Single symbol</option>
            <option value="categorized" disabled={!layer.fields.length}>Unique values</option>
            <option value="graduated" disabled={!numeric.length}>Graduated colors</option>
          </select></label>
        {s.type !== "single" && <>
          <label className="ws-f">Field 1
            <select value={s.field ?? ""} onChange={(e) => void classify({ ...s, field: e.target.value })}>
              {fieldsFor.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
            </select></label>
          {s.type === "graduated" && <div className="ws-row">
            <label className="ws-f">Method
              <select value={s.method === "equal" ? "equal" : "quantile"} onChange={(e) => void classify({ ...s, method: e.target.value as "equal" | "quantile" })}>
                <option value="quantile">Quantile</option><option value="equal">Equal interval</option></select></label>
            <label className="ws-f">Classes
              <input type="number" min={2} max={12} value={n} onChange={(e) => setN(Math.min(12, Math.max(2, Number(e.target.value) || 2)))} onBlur={() => void classify(s)} /></label>
          </div>}
          <div className="ws-f"><span>Color scheme</span><SchemePicker value={s.ramp || (s.type === "graduated" ? "Greens" : "Default")} onChange={recolour} disabled={dis} /></div>
        </>}
        {s.type === "single" ? (
          <section className="sy-sec" aria-label="Symbol">
            <h3>Symbol</h3>
            <div className="sy-grid">
              <label>{geom === "LineString" ? "Line colour" : "Fill colour"}<input type="color" value={s.color} onChange={(e) => put({ ...s, color: e.target.value })} /></label>
              {geom !== "LineString" && <label>Outline colour<input type="color" value={s.stroke} onChange={(e) => put({ ...s, stroke: e.target.value })} /></label>}
              <label>{geom === "LineString" ? "Line width" : "Outline width"}
                <input type="number" min={0} max={20} step={0.5} value={s.width} onChange={(e) => put({ ...s, width: Number(e.target.value) })} /></label>
              {geom === "Point" && <label>Size<input type="number" min={1} max={40} value={s.size} onChange={(e) => put({ ...s, size: Number(e.target.value) })} /></label>}
            </div>
          </section>
        ) : (
          <section className="sy-sec" aria-label="Classes">
            <div className="sy-tabs" role="tablist"><span role="tab" aria-selected="true">Classes</span></div>
            <div className="sy-tools">
              <span className="ws-muted">{s.field} · {rows.length} symbol class{rows.length === 1 ? "" : "es"}</span>
              <span className="ge-spacer" />
              <IconButton label="Classify again (all values)" onClick={() => void classify(s)}><RefreshCw size={14} /></IconButton>
              {s.type === "categorized" && <>
                <IconButton label="Move up" onClick={() => picked != null && moveRow(picked, -1)}><ArrowUp size={14} /></IconButton>
                <IconButton label="Move down" onClick={() => picked != null && moveRow(picked, 1)}><ArrowDown size={14} /></IconButton></>}
              <IconButton label="Remove the selected class" onClick={() => picked != null && removeRow(picked)}><Trash2 size={14} /></IconButton>
              <IconButton label="Reverse the colours" onClick={reverse}><ArrowDownUp size={14} /></IconButton>
            </div>
            <table className="sy-table">
              <thead><tr><th>Symbol</th><th>Value</th><th>Label</th></tr></thead>
              <tbody>
                {rows.map((c, k) => {
                  const cat = "value" in c;
                  const value = cat ? (c.value === "" ? "(empty)" : String(c.value)) : `${fmt(c.min)} – ${fmt(c.max)}`;
                  return (
                    <tr key={k} className={picked === k ? "is-sel" : ""} onClick={() => setPicked(k)}>
                      <td><input type="color" aria-label={`Colour of ${value}`} value={c.color} onChange={(e) => setRow(k, { color: e.target.value })}
                        className={`sy-sym is-${geom.toLowerCase()}`} /></td>
                      <td title={value}>{value}</td>
                      <td><input className="sy-label" value={c.label ?? (cat ? value : "")} placeholder={value} aria-label={`Label of ${value}`}
                        onChange={(e) => setRow(k, { label: e.target.value })} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="sy-grid">
              {geom !== "LineString" && <label>Outline colour<input type="color" value={s.stroke} onChange={(e) => put({ ...s, stroke: e.target.value })} /></label>}
              <label>{geom === "LineString" ? "Line width" : "Outline width"}
                <input type="number" min={0} max={20} step={0.5} value={s.width} onChange={(e) => put({ ...s, width: Number(e.target.value) })} /></label>
              {geom === "Point" && <label>Size<input type="number" min={1} max={40} value={s.size} onChange={(e) => put({ ...s, size: Number(e.target.value) })} /></label>}
            </div>
          </section>
        )}
        <label className="ws-f sy-opacity">Transparency {Math.round((1 - s.opacity) * 100)} %
          <input type="range" min={0} max={1} step={0.01} value={1 - s.opacity} onChange={(e) => put({ ...s, opacity: Math.round((1 - Number(e.target.value)) * 100) / 100 })} /></label>
        {geom === "Polygon" && <fieldset className="ws-3d">
          <label className="lo-check"><input type="checkbox" checked={!!s.extrude}
            onChange={(e) => put({ ...s, extrude: e.target.checked ? { field: numeric[0]?.name ?? null, height: 10, scale: 1, base: 0 } : null })} />Draw in 3D (extrude)</label>
          {s.extrude && <div className="ws-row">
            <label className="ws-f">Height from
              <select value={s.extrude.field ?? ""} onChange={(e) => put({ ...s, extrude: { ...s.extrude!, field: e.target.value || null } })}>
                <option value="">A fixed height</option>{numeric.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}</select></label>
            {s.extrude.field
              ? <label className="ws-f">Metres per unit<input type="number" min={0.01} step="any" value={s.extrude.scale} onChange={(e) => put({ ...s, extrude: { ...s.extrude!, scale: Number(e.target.value) || 1 } })} />
                <span className="ws-help">e.g. 3 when the field counts storeys</span></label>
              : <label className="ws-f">Height (m)<input type="number" min={0} step="any" value={s.extrude.height} onChange={(e) => put({ ...s, extrude: { ...s.extrude!, height: Number(e.target.value) || 0 } })} /></label>}
          </div>}
          {s.extrude && <p className="ws-muted">Tilt the map (Map › 3D view, or right-drag) to see the heights.</p>}
        </fieldset>}
        <ErrorText error={error} />
      </fieldset>
      <AutoApplyBar canEdit={canEdit} auto={live.auto} setAuto={live.setAuto} busy={live.busy} dirty={live.dirty} error={live.error} onApply={() => live.apply(s)} />
    </div>
  );
}

const SIZES = [6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40];
const ZOOMS: [number, string][] = [[0, "World"], [3, "Continent"], [5, "Country"], [7, "Region"], [9, "State"], [11, "City"], [13, "Town"], [15, "Streets"],
  [17, "Buildings"], [19, "Very close"], [24, "Closest"]];
const scaleOf = (z: number) => `1:${Math.round(559082264 / 2 ** z).toLocaleString("en-GB")}`;
const DEFAULT_LABELS = (layer: Layer): Labels => ({
  field: layer.fields.find((f) => /name|label|title/i.test(f.name))?.name ?? layer.fields[0]?.name ?? "", size: 10, color: "#000000", halo: "#ffffff",
  expression: "", min_zoom: 0, max_zoom: 24, font: "regular", opacity: 1, halo_width: 1.4, halo_blur: 0, case: "normal", letter_spacing: 0, rotation: 0,
  rotation_field: null, anchor: "auto", offset: 4, line_placement: "along", line_spacing: 250, wrap: 10, allow_overlap: false,
});

function Section({ title, open, children }: { title: string; open?: boolean; children: React.ReactNode }) {
  return <details className="lb-sec" open={open}><summary>{title}</summary><div className="lb-sec-body">{children}</div></details>;
}

function LabelsTab({ layer, canEdit, onChange }: { layer: Layer; canEdit: boolean; onChange: (l: Layer) => void }) {
  const [lb, setLb] = useState<Labels>({ ...DEFAULT_LABELS(layer), ...(layer.labels ?? {}) });
  const [on, setOn] = useState(!!layer.labels);
  const [tab, setTab] = useState<"class" | "symbol" | "position">("symbol");
  const [offError, setOffError] = useState<unknown>(null);
  useEffect(() => { if (layer.labels) setLb((cur) => ({ ...cur, ...layer.labels })); setOn(!!layer.labels); }, [layer.labels]);
  const live = useAutoApply<Labels>({ layer, canEdit, onChange, preview: (v) => ({ ...layer, labels: v }),
    save: (v) => api.updateLayer(layer.id, { labels: v }), keepVersion: () => true });
  if (!layer.fields.length) return <p className="ws-muted">This layer has no fields to label with. Add one under Fields.</p>;
  const put = (x: Partial<Labels>) => { const next = { ...lb, ...x }; setLb(next); if (on) live.change(next); };
  async function toggle(v: boolean) {
    setOn(v); setOffError(null);
    if (v) live.apply(lb);
    else {
      try { onChange(await api.updateLayer(layer.id, { clear_labels: true })); } catch (e) { setOffError(e); setOn(true); }
    }
  }
  const numeric = layer.fields.filter((f) => f.type === "integer" || f.type === "number");
  const geom = layer.geometry_type;
  const GRID: Labels["anchor"][] = ["top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right"];
  return (
    <div className="ws-form lb">
      <label className="lb-on"><input type="checkbox" role="switch" checked={on} disabled={!canEdit} onChange={(e) => void toggle(e.target.checked)} />
        <span aria-hidden="true" className="aa-track"><span /></span>Label features in this layer</label>
      <ErrorText error={offError} />
      <div className="lb-tabs" role="tablist" aria-label="Label class">
        {([["class", "Class"], ["symbol", "Symbol"], ["position", "Position"]] as const).map(([k, t]) =>
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{t}</button>)}
      </div>
      <fieldset disabled={!canEdit} className={`lb-body${on ? "" : " is-off"}`}>
        {tab === "class" && <>
          <label className="ws-f">Label field
            <select value={lb.field} onChange={(e) => put({ field: e.target.value })}>{layer.fields.map((f) => <option key={f.name}>{f.name}</option>)}</select></label>
          <label className="ws-f">Expression (optional)
            <input value={lb.expression ?? ""} maxLength={500} placeholder={`{${lb.field}}`} onChange={(e) => put({ expression: e.target.value })} />
            <span className="ws-help">Text with fields in braces, e.g. {"{name} ({population})"}. Empty: the label field.</span></label>
          <label className="ws-f">Insert a field
            <select value="" onChange={(e) => { if (e.target.value) put({ expression: `${lb.expression || ""}{${e.target.value}}` }); }}>
              <option value="">Choose…</option>{layer.fields.map((f) => <option key={f.name}>{f.name}</option>)}</select></label>
          <Section title="Visibility range" open>
            <div className="ws-row">
              <label className="ws-f">Out beyond (zoomed out)
                <select value={lb.min_zoom ?? 0} onChange={(e) => put({ min_zoom: Number(e.target.value) })}>
                  {ZOOMS.filter(([z]) => z < 24).map(([z, t]) => <option key={z} value={z}>{z === 0 ? "None" : `${t} (${scaleOf(z)})`}</option>)}</select></label>
              <label className="ws-f">In beyond (zoomed in)
                <select value={lb.max_zoom ?? 24} onChange={(e) => put({ max_zoom: Number(e.target.value) })}>
                  {ZOOMS.filter(([z]) => z > 0).map(([z, t]) => <option key={z} value={z}>{z === 24 ? "None" : `${t} (${scaleOf(z)})`}</option>)}</select></label>
            </div>
          </Section>
        </>}
        {tab === "symbol" && <>
          <Section title="Appearance" open>
            <div className="lb-grid">
              <label>Font name<select value="Noto Sans" disabled><option>Noto Sans</option></select></label>
              <label>Font style<select value={lb.font ?? "regular"} onChange={(e) => put({ font: e.target.value as Labels["font"] })}>
                <option value="regular">Regular</option><option value="bold">Bold</option><option value="italic">Italic</option></select></label>
              <label>Size<select value={lb.size} onChange={(e) => put({ size: Number(e.target.value) })}>
                {Array.from(new Set([...SIZES, lb.size])).sort((a, b) => a - b).map((z) => <option key={z} value={z}>{z} pt</option>)}</select></label>
              <label>Text fill colour<input type="color" value={lb.color} onChange={(e) => put({ color: e.target.value })} /></label>
              <label>Text case<select value={lb.case ?? "normal"} onChange={(e) => put({ case: e.target.value as Labels["case"] })}>
                <option value="normal">Normal</option><option value="upper">UPPERCASE</option><option value="lower">lowercase</option></select></label>
              <label>Letter spacing<input type="number" step={0.05} min={-0.5} max={2} value={lb.letter_spacing ?? 0} onChange={(e) => put({ letter_spacing: Number(e.target.value) })} /></label>
              <label className="lb-wide">Transparency {Math.round((1 - (lb.opacity ?? 1)) * 100)} %
                <input type="range" min={0} max={1} step={0.01} value={1 - (lb.opacity ?? 1)} onChange={(e) => put({ opacity: Math.round((1 - Number(e.target.value)) * 100) / 100 })} /></label>
            </div>
            <p className="ws-help">The map draws labels with Noto Sans (regular, bold, italic). Underline and strikethrough are not available in map labels.</p>
          </Section>
          <Section title="Halo (outline)" open>
            <div className="lb-grid">
              <label>Halo colour<input type="color" value={lb.halo} onChange={(e) => put({ halo: e.target.value })} /></label>
              <label>Halo size (px)<input type="number" min={0} max={10} step={0.2} value={lb.halo_width ?? 1.4} onChange={(e) => put({ halo_width: Number(e.target.value) })} /></label>
              <label>Halo blur (px)<input type="number" min={0} max={10} step={0.2} value={lb.halo_blur ?? 0} onChange={(e) => put({ halo_blur: Number(e.target.value) })} /></label>
            </div>
          </Section>
          <Section title="Rotation">
            <div className="lb-grid">
              <label>Angle (°)<input type="number" min={-360} max={360} value={lb.rotation ?? 0} onChange={(e) => put({ rotation: Number(e.target.value) })} /></label>
              <label>Rotate by field<select value={lb.rotation_field ?? ""} onChange={(e) => put({ rotation_field: e.target.value || null })}>
                <option value="">None</option>{numeric.map((f) => <option key={f.name}>{f.name}</option>)}</select></label>
            </div>
          </Section>
        </>}
        {tab === "position" && <>
          {geom === "LineString" ? <Section title="Placement" open>
            <label className="ws-f">Lines<select value={lb.line_placement ?? "along"} onChange={(e) => put({ line_placement: e.target.value as Labels["line_placement"] })}>
              <option value="along">Along the line (curved, repeated)</option><option value="horizontal">Horizontal, once at the middle</option></select></label>
            {(lb.line_placement ?? "along") === "along" && <label className="ws-f">Repeat every (px)<input type="number" min={20} max={2000} value={lb.line_spacing ?? 250}
              onChange={(e) => put({ line_spacing: Number(e.target.value) })} /></label>}
          </Section> : <Section title="Placement" open>
            <label className="lo-check"><input type="checkbox" checked={(lb.anchor ?? "auto") === "auto"} onChange={(e) => put({ anchor: e.target.checked ? "auto" : geom === "Point" ? "top" : "center" })} />
              Best position (tries around the {geom === "Point" ? "point" : "centre"} to avoid overlaps)</label>
            {(lb.anchor ?? "auto") !== "auto" && <div className="lb-anchor" role="radiogroup" aria-label="Label position">
              {GRID.map((a) => <button key={a} type="button" role="radio" aria-checked={lb.anchor === a} aria-label={a!.replace("-", " ")} title={a!.replace("-", " ")}
                className={lb.anchor === a ? "is-on" : ""} onClick={() => put({ anchor: a })}><span /></button>)}
            </div>}
            {geom === "Point" && <label className="ws-f">Offset from the point (px)<input type="number" min={0} max={60} value={lb.offset ?? 4} onChange={(e) => put({ offset: Number(e.target.value) })} /></label>}
          </Section>}
          <Section title="Fitting" open>
            <label className="ws-f">Wrap after about (characters, 0 = never)<input type="number" min={0} max={100} value={lb.wrap ?? 10} onChange={(e) => put({ wrap: Number(e.target.value) })} /></label>
            <label className="lo-check"><input type="checkbox" checked={!!lb.allow_overlap} onChange={(e) => put({ allow_overlap: e.target.checked })} />Never remove (show labels even where they overlap)</label>
          </Section>
        </>}
        {!on && <p className="ws-muted">Turn on “Label features in this layer” to see these on the map.</p>}
      </fieldset>
      {on && <AutoApplyBar canEdit={canEdit} auto={live.auto} setAuto={live.setAuto} busy={live.busy} dirty={live.dirty} error={live.error} onApply={() => live.apply(lb)} />}
    </div>
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
