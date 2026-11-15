/** Shown once to everyone after the new interface: where things went, in a few lines, and a way to try Ask. */
import { Sparkles, X } from "lucide-react";
import { useStored } from "./ui";

const MOVES: [string, string][] = [
  ["Ribbon tabs", "Modes down the left"], ["Contents", "Layers"], ["Catalog", "Library (Explore)"], ["Geoprocessing", "Tools, and Ask PolyGIS"],
  ["Model builder", "Flows (Analyse)"], ["Symbology", "Style"], ["Select tab", "The selection bar, and Explore"], ["Military", "Defence"], ["Share", "Publish"],
];

export function WhatsNew({ onAsk }: { onAsk: () => void }) {
  const [seen, setSeen] = useStored("whats-new-2026-11", false);
  if (seen) return null;
  return (
    <section className="wn" role="dialog" aria-labelledby="wn-title">
      <div className="wn-head"><Sparkles size={18} aria-hidden="true" /><h2 id="wn-title">PolyGIS has a new look</h2>
        <button type="button" className="wn-x" aria-label="Close" onClick={() => setSeen(true)}><X size={16} /></button></div>
      <p>Every tool is still here; some have new names and places.</p>
      <dl className="wn-moves">{MOVES.map(([a, b]) => <div key={a}><dt>{a}</dt><dd>{b}</dd></div>)}</dl>
      <p>New: <strong>Ask PolyGIS</strong> (Ctrl K) finds any tool and fills it in, e.g. “buffer roads by 500 m”; <strong>Focus</strong> shows the map only; the moon button turns on the dark <strong>Operations</strong> theme.</p>
      <div className="wn-acts">
        <button type="button" className="ge-btn ge-btn-sm ge-btn-primary" onClick={() => { setSeen(true); onAsk(); }}>Try Ask PolyGIS</button>
        <button type="button" className="ge-btn ge-btn-sm" onClick={() => setSeen(true)}>Got it</button>
      </div>
    </section>
  );
}
