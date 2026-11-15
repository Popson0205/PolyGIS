/** Military symbols: a picker that builds a MIL-STD-2525C code (affiliation, status, echelon, headquarters or task
 *  force, and the symbol itself) with a live preview, and the Symbols pane that places them on the map. */
import { Plus, Stamp, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AFFILIATIONS, ECHELONS, MODIFIERS, STATUSES, SYMBOLS, isValidSidc, makeSidc, readSidc, symbolUrl } from "../lib/milsym";
import type { Layer } from "../lib/types";
import { Button, IconButton } from "./ui";

export function SymbolPicker({ value, onChange, compact }: { value: string; onChange: (sidc: string) => void; compact?: boolean }) {
  const parts = readSidc(value);
  const [q, setQ] = useState("");
  const [custom, setCustom] = useState(value);
  const set = (p: Partial<ReturnType<typeof readSidc>>) => {
    const n = { ...parts, ...p };
    const code = makeSidc({ affiliation: n.affiliation, dim: n.dim, status: n.status, fn: n.fn, modifier: n.modifier, echelon: n.echelon });
    setCustom(code); onChange(code);
  };
  const shown = useMemo(() => SYMBOLS.map((g) => ({ ...g, items: g.items.filter((i) => !q || i.name.toLowerCase().includes(q.toLowerCase())) }))
    .filter((g) => g.items.length), [q]);
  const isUnit = parts.dim === "G" && parts.fn.startsWith("U");
  return (
    <div className={`msp${compact ? " is-compact" : ""}`}>
      <div className="msp-head">
        <img src={symbolUrl(value, 44)} alt="" className="msp-preview" />
        <div className="msp-code">
          <label className="ws-f">Symbol code (SIDC)
            <input value={custom} spellCheck={false} onChange={(e) => { const v = e.target.value.toUpperCase().trim(); setCustom(v); if (isValidSidc(v)) onChange(v); }}
              aria-invalid={!isValidSidc(custom)} /></label>
          {!isValidSidc(custom) && <span className="msp-bad">Not a code milsymbol knows: 15 characters (2525C) or 20 digits (2525D).</span>}
        </div>
      </div>
      <div className="msp-row" role="radiogroup" aria-label="Affiliation">
        {AFFILIATIONS.map((a) => (
          <button key={a.id} type="button" role="radio" aria-checked={parts.affiliation === a.id} className={`msp-aff${parts.affiliation === a.id ? " is-on" : ""}`}
            onClick={() => set({ affiliation: a.id })} title={a.name}>
            <img src={symbolUrl(makeSidc({ affiliation: a.id, dim: parts.dim, status: "P", fn: parts.fn }), 22)} alt="" />{a.name}
          </button>
        ))}
      </div>
      <div className="mil-opts">
        <label className="ws-f">Status<select value={parts.status} onChange={(e) => set({ status: e.target.value })}>{STATUSES.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
        <label className="ws-f">Echelon<select value={isUnit ? parts.echelon : "-"} disabled={!isUnit} onChange={(e) => set({ echelon: e.target.value })}>
          {ECHELONS.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
        <label className="ws-f">Headquarters / task force<select value={isUnit ? parts.modifier : "-"} disabled={!isUnit} onChange={(e) => set({ modifier: e.target.value })}>
          {MODIFIERS.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
      </div>
      <input className="msp-search" type="search" placeholder="Find a symbol: infantry, tank, radar…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find a symbol" />
      <div className="msp-groups">
        {shown.map((g) => (
          <section key={g.group}>
            <h4>{g.group}</h4>
            <div className="msp-grid">
              {g.items.map((i) => {
                const code = makeSidc({ affiliation: parts.affiliation, dim: i.dim, status: parts.status, fn: i.fn, modifier: parts.modifier, echelon: parts.echelon });
                const on = parts.dim === i.dim && parts.fn === (i.fn + "------").slice(0, 6);
                return (
                  <button key={i.name} type="button" className={`msp-item${on ? " is-on" : ""}`} aria-pressed={on} title={`${i.name} (${code})`}
                    onClick={() => set({ dim: i.dim, fn: i.fn })}>
                    <img src={symbolUrl(code, 26)} alt="" /><span>{i.name}</span>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

/** The Symbols pane: choose a symbol and place it on the map, into a symbol layer. */
export function SymbolsPane({ layers, canEdit, placing, onPlace, onStop, onNewLayer, onClose }: {
  layers: Layer[]; canEdit: boolean; placing: boolean;
  onPlace: (layerId: string, props: Record<string, unknown>) => void; onStop: () => void;
  onNewLayer: () => Promise<Layer | null>; onClose: () => void;
}) {
  const symbolLayers = layers.filter((l) => l.geometry_type === "Point" && l.style.type === "milsymbol" && !l.source.linked);
  const [target, setTarget] = useState(symbolLayers[0]?.id ?? "");
  const [sidc, setSidc] = useState("SFGPUCI----D---");
  const [designation, setDesignation] = useState("");
  const [higher, setHigher] = useState("");
  const lay = symbolLayers.find((l) => l.id === target) ?? symbolLayers[0];
  const mil = lay?.style.mil;
  const props = () => {
    const p: Record<string, unknown> = {};
    if (mil?.sidc_field) p[mil.sidc_field] = sidc;
    if (mil?.designation_field && designation) p[mil.designation_field] = designation;
    if (mil?.higher_field && higher) p[mil.higher_field] = higher;
    return p;
  };
  // while placing, the next symbol is whatever is chosen now
  useEffect(() => { if (placing && lay && isValidSidc(sidc)) onPlace(lay.id, props()); }, [sidc, designation, higher, lay?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <aside className="ws-panel mil" aria-label="Military symbols">
      <div className="ws-pane-head"><strong>Symbols</strong><span className="ge-spacer" /><IconButton label="Close" onClick={onClose}><X size={16} /></IconButton></div>
      <div className="ws-scroll mil-body">
        <div className="mil-target">
          <label className="ws-f">Place into
            <select value={lay?.id ?? ""} onChange={(e) => setTarget(e.target.value)} disabled={!symbolLayers.length}>
              {!symbolLayers.length && <option value="">No symbol layer yet</option>}
              {symbolLayers.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
          <Button onClick={() => void onNewLayer().then((l) => l && setTarget(l.id))} disabled={!canEdit}><Plus size={15} aria-hidden="true" />New symbol layer</Button>
        </div>
        {lay && !mil?.sidc_field && <p className="ws-muted">{lay.name} shows one symbol for all its points: choose a code field in its Symbology to place different ones.</p>}
        <SymbolPicker value={sidc} onChange={setSidc} />
        <div className="mil-opts">
          <label className="ws-f">Unique designation<input value={designation} onChange={(e) => setDesignation(e.target.value)} placeholder="e.g. A/2-14" disabled={!mil?.designation_field} /></label>
          <label className="ws-f">Higher formation<input value={higher} onChange={(e) => setHigher(e.target.value)} placeholder="e.g. 3 BDE" disabled={!mil?.higher_field} /></label>
        </div>
        <div className="mil-acts">
          {placing
            ? <Button variant="primary" onClick={onStop}>Done placing</Button>
            : <Button variant="primary" disabled={!canEdit || !lay || !isValidSidc(sidc)} onClick={() => lay && onPlace(lay.id, props())}><Stamp size={15} aria-hidden="true" />Place on the map</Button>}
        </div>
        {placing && <p className="ws-muted" role="status">Click the map to place each symbol. Change the symbol or the text here between clicks; press Done when finished.</p>}
      </div>
    </aside>
  );
}
