import { useEffect, useState } from "react";
import { api, rasterDownloadUrl } from "../lib/api";
import { CATEGORY_COLOURS } from "../lib/mapstyle";
import type { Layer, RasterStyle } from "../lib/types";
import { Button, ErrorText, useAction } from "./ui";

/** The colour ramps the server draws rasters with (the same list as app/raster_style.py). */
export const RASTER_RAMPS: Record<string, string[]> = {
  Gray: ["#000000", "#ffffff"],
  Terrain: ["#33a02c", "#b2df8a", "#ffffbf", "#d9b38c", "#a6754d", "#ffffff"],
  Greens: ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
  Blues: ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
  Reds: ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
  Viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
  Magma: ["#000004", "#51127c", "#b73779", "#fc8961", "#fcfdbf"],
  "Red to green": ["#d7191c", "#fdae61", "#ffffbf", "#a6d96a", "#1a9641"],
  Spectral: ["#2b83ba", "#abdda4", "#ffffbf", "#fdae61", "#d7191c"],
  "Blue to red": ["#2166ac", "#67a9cf", "#f7f7f7", "#ef8a62", "#b2182b"],
};
export const gradient = (ramp: string, invert = false) => {
  const c = [...(RASTER_RAMPS[ramp] ?? RASTER_RAMPS.Gray)];
  return `linear-gradient(to right, ${(invert ? c.reverse() : c).join(", ")})`;
};
export const rasterStyle = (l: Layer) => l.style as unknown as RasterStyle;
const fmt = (v: number | null | undefined) => (v == null ? "–" : Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-GB") : Number(v.toPrecision(5)).toString());

type Tab = "symbology" | "source";

/** The right-hand pane for a raster layer: how it is coloured, and what it is. */
export default function RasterPanel({ layer, canEdit, canExport, onChange }: { layer: Layer; canEdit: boolean; canExport: boolean; onChange: (l: Layer) => void }) {
  const [tab, setTab] = useState<Tab>("symbology");
  return (
    <aside className="ws-panel" aria-label={`${layer.name}: properties`}>
      <div className="ws-pane-head"><strong title={layer.name}>{layer.name}</strong></div>
      <div className="ws-tabs" role="tablist">
        {(["symbology", "source"] as Tab[]).map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>)}
      </div>
      <div className="ws-panel-body">
        {tab === "symbology" ? <Symbology key={layer.id} layer={layer} canEdit={canEdit} onChange={onChange} /> : <Source layer={layer} canExport={canExport} />}
      </div>
    </aside>
  );
}

function Symbology({ layer, canEdit, onChange }: { layer: Layer; canEdit: boolean; onChange: (l: Layer) => void }) {
  const m = layer.raster!;
  const [s, setS] = useState<RasterStyle>(() => ({ mode: "ramp", bands: [1, Math.min(2, m.count), Math.min(3, m.count)], mins: [0, 0, 0], maxs: [255, 255, 255], categories: [],
    ramp: "Terrain", band: 1, opacity: 1, ...(rasterStyle(layer) as Partial<RasterStyle>), type: "raster" } as RasterStyle));
  const [error, setError] = useState<unknown>(null);
  const { busy, run } = useAction();
  useEffect(() => setS((cur) => ({ ...cur, ...rasterStyle(layer) })), [layer.style]);   // eslint-disable-line react-hooks/exhaustive-deps
  const band = m.bands[(s.band ?? 1) - 1] ?? m.bands[0];
  const bandOpts = m.bands.map((b) => <option key={b.band} value={b.band}>{b.band}: {b.name}</option>);
  const r6 = (v?: number) => (v == null ? v : Number(v.toPrecision(6)));
  const stretch = (how: "p" | "mm") => setS({ ...s, min: r6(how === "p" ? band.p2 : band.min), max: r6(how === "p" ? band.p98 : band.max) });
  const classify = () => {
    const vals = band.values ?? [];
    if (!vals.length) { setError(new Error("This band has too many different values to show as classes: use a colour ramp, or reclassify it first.")); return; }
    setError(null);
    setS({ ...s, mode: "classes", categories: vals.map((v, k) => ({ value: v, label: String(v), color: CATEGORY_COLOURS[k % CATEGORY_COLOURS.length] })) });
  };
  const save = () => void run("rstyle", () => api.rasterStyle(layer.id, s), "Style saved.").then((l) => l && onChange(l));
  const dis = !canEdit;
  return (
    <form className="ws-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <label className="ws-f">Show as
        <select value={s.mode} disabled={dis} onChange={(e) => { const mode = e.target.value as RasterStyle["mode"]; if (mode === "classes" && !s.categories.length) classify(); else setS({ ...s, mode }); }}>
          <option value="ramp">A colour ramp (one band)</option>
          {m.count >= 2 && <option value="rgb">Red, green, blue (three bands)</option>}
          <option value="classes">Classes (a colour for each value)</option>
        </select></label>
      {s.mode === "ramp" && <>
        {m.count > 1 && <label className="ws-f">Band<select value={s.band} disabled={dis} onChange={(e) => { const b = m.bands[Number(e.target.value) - 1]; setS({ ...s, band: b.band, min: r6(b.p2), max: r6(b.p98) }); }}>{bandOpts}</select></label>}
        <label className="ws-f">Colour ramp
          <select value={s.ramp} disabled={dis} onChange={(e) => setS({ ...s, ramp: e.target.value })}>{Object.keys(RASTER_RAMPS).map((r) => <option key={r}>{r}</option>)}</select></label>
        <div className="ws-ramp" style={{ background: gradient(s.ramp, s.invert) }} aria-hidden="true" />
        <label className="ws-check"><input type="checkbox" checked={!!s.invert} disabled={dis} onChange={(e) => setS({ ...s, invert: e.target.checked })} /> Reverse the ramp</label>
        <div className="ws-row2">
          <label className="ws-f">From<input type="number" step="any" value={s.min ?? ""} disabled={dis} onChange={(e) => setS({ ...s, min: Number(e.target.value) })} /></label>
          <label className="ws-f">To<input type="number" step="any" value={s.max ?? ""} disabled={dis} onChange={(e) => setS({ ...s, max: Number(e.target.value) })} /></label>
        </div>
        <div className="ws-row"><Button small variant="ghost" disabled={dis} onClick={() => stretch("p")}>Stretch 2–98 %</Button><Button small variant="ghost" disabled={dis} onClick={() => stretch("mm")}>Minimum to maximum</Button></div>
        <p className="ws-muted">Values {fmt(band.min)} to {fmt(band.max)}, mean {fmt(band.mean)}.</p>
      </>}
      {s.mode === "rgb" && ([0, 1, 2] as const).map((k) => (
        <fieldset key={k} className="ws-f ws-fieldset"><legend>{["Red", "Green", "Blue"][k]}</legend>
          <select value={s.bands[k]} disabled={dis} aria-label={`${["Red", "Green", "Blue"][k]} band`} onChange={(e) => {
            const b = m.bands[Number(e.target.value) - 1];
            const bands = [...s.bands] as RasterStyle["bands"]; const mins = [...s.mins] as RasterStyle["mins"]; const maxs = [...s.maxs] as RasterStyle["maxs"];
            bands[k] = b.band; mins[k] = r6(b.p2) ?? 0; maxs[k] = r6(b.p98) ?? 255; setS({ ...s, bands, mins, maxs });
          }}>{bandOpts}</select>
          <div className="ws-row2">
            <input type="number" step="any" aria-label="From" value={s.mins[k]} disabled={dis} onChange={(e) => { const mins = [...s.mins] as RasterStyle["mins"]; mins[k] = Number(e.target.value); setS({ ...s, mins }); }} />
            <input type="number" step="any" aria-label="To" value={s.maxs[k]} disabled={dis} onChange={(e) => { const maxs = [...s.maxs] as RasterStyle["maxs"]; maxs[k] = Number(e.target.value); setS({ ...s, maxs }); }} />
          </div>
        </fieldset>
      ))}
      {s.mode === "classes" && <>
        {m.count > 1 && <label className="ws-f">Band<select value={s.band} disabled={dis} onChange={(e) => setS({ ...s, band: Number(e.target.value) })}>{bandOpts}</select></label>}
        <ul className="ws-cats">
          {s.categories.map((c, k) => (
            <li key={`${c.value}`}>
              <input type="color" value={c.color} disabled={dis} aria-label={`Colour of ${c.value}`} onChange={(e) => setS({ ...s, categories: s.categories.map((x, j) => (j === k ? { ...x, color: e.target.value } : x)) })} />
              <span className="ws-mono">{c.value}</span>
              <input value={c.label ?? ""} disabled={dis} aria-label={`Label of ${c.value}`} maxLength={200} onChange={(e) => setS({ ...s, categories: s.categories.map((x, j) => (j === k ? { ...x, label: e.target.value } : x)) })} />
              <label className="ws-check" title="Hide"><input type="checkbox" checked={!c.hidden} disabled={dis} onChange={(e) => setS({ ...s, categories: s.categories.map((x, j) => (j === k ? { ...x, hidden: !e.target.checked } : x)) })} /> Show</label>
            </li>
          ))}
        </ul>
        <Button small variant="ghost" disabled={dis} onClick={classify}>Colour every value again</Button>
      </>}
      <label className="ws-f">Opacity {Math.round(s.opacity * 100)} %
        <input type="range" min={0} max={1} step={0.05} value={s.opacity} disabled={dis} onChange={(e) => setS({ ...s, opacity: Number(e.target.value) })} /></label>
      <ErrorText error={error} />
      {canEdit && <Button type="submit" variant="primary" busy={busy === "rstyle"}>Save style</Button>}
    </form>
  );
}

function Source({ layer, canExport }: { layer: Layer; canExport: boolean }) {
  const m = layer.raster!;
  const unit = m.geographic ? "°" : " m";
  const summary = layer.source.summary ?? {};
  return (
    <div>
      <dl className="ws-dl">
        <dt>Cells</dt><dd>{m.width.toLocaleString("en-GB")} × {m.height.toLocaleString("en-GB")}, {m.count} band{m.count > 1 ? "s" : ""} ({m.dtype})</dd>
        <dt>Cell size</dt><dd>{fmt(m.res[0])}{unit}{m.geographic ? ` (about ${fmt(m.res_m[0])} m)` : ""}</dd>
        <dt>Coordinate system</dt><dd>{m.crs_name}{m.epsg ? ` (EPSG:${m.epsg})` : ""}</dd>
        <dt>No data</dt><dd>{m.nodata ?? "–"}</dd>
        <dt>File</dt><dd>{layer.source.filename ?? layer.source.tool_name ?? "–"} · {(m.size_bytes / 1048576).toFixed(1)} MB</dd>
        {m.bands.map((b) => <div key={b.band} className="ws-dl-row"><dt>{b.name}</dt><dd>{fmt(b.min)} – {fmt(b.max)}, mean {fmt(b.mean)}</dd></div>)}
        {Object.entries(summary).map(([k, v]) => <div key={k} className="ws-dl-row"><dt>{k.replace(/_/g, " ")}</dt><dd>{typeof v === "number" ? fmt(v) : Array.isArray(v) ? v.map((x) => fmt(Number(x))).join(", ") : String(v)}</dd></div>)}
      </dl>
      {canExport && <p><a className="ge-btn" href={rasterDownloadUrl(layer.id)} download>Download GeoTIFF</a></p>}
    </div>
  );
}
