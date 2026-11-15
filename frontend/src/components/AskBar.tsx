/** Ask PolyGIS: one box at the top for anything — a tool with its values filled in, a layer to zoom to or open, a place to
 *  go, or any button of any mode. Enter does the first answer; arrows choose another. Worked out in the page (lib/ask). */
import { forwardRef, useMemo, useState } from "react";
import { Crosshair, Layers as LayersIcon, Sparkles, SquareMousePointer, Wrench } from "lucide-react";
import { ask, type AskHit, type Command } from "../lib/ask";
import type { Layer, Tool } from "../lib/types";

const ICON = { tool: Wrench, layer: LayersIcon, goto: Crosshair, command: SquareMousePointer } as const;

export const AskBar = forwardRef<HTMLInputElement, {
  tools: Tool[]; layers: Layer[]; commands: () => Command[]; onPick: (h: AskHit) => void; onSearchTools: (q: string) => void;
}>(function AskBar({ tools, layers, commands, onPick, onSearchTools }, ref) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(0);
  const [cmds, setCmds] = useState<Command[]>([]);
  const hits = useMemo(() => ask(q, { tools, layers, commands: cmds }), [q, tools, layers, cmds]);
  const examples = useMemo(() => {
    const vec = layers.filter((l) => l.geometry_type !== "Raster");
    const poly = vec.find((l) => l.geometry_type.includes("Polygon"));
    const other = vec.find((l) => l !== poly) ?? vec[0];
    const n = (l?: Layer) => (l ? l.name.toLowerCase() : "roads");
    return [`buffer ${n(other)} by 500 m`, ...(poly && other && poly !== other ? [`clip ${n(other)} to ${n(poly)}`] : []), `zoom to ${n(vec[0])}`, "range rings", "6.4550, 3.3941"];
  }, [layers]);
  const pick = (h: AskHit) => { if (h.kind === "command" && h.disabled) return; setOpen(false); setQ(""); onPick(h); };
  const listId = "ask-results";

  return (
    <form className="ws-command ask" role="search" onSubmit={(e) => {
      e.preventDefault();
      if (hits[at]) pick(hits[at]);
      else if (q.trim()) { onSearchTools(q); setOpen(false); }
    }}>
      <Sparkles size={16} aria-hidden="true" />
      <input ref={ref} value={q} placeholder="Ask PolyGIS or find a tool" aria-label="Ask PolyGIS or find a tool" role="combobox" aria-expanded={open}
        aria-controls={listId} aria-autocomplete="list" aria-activedescendant={open && hits[at] ? `ask-${hits[at].key}` : undefined} autoComplete="off"
        onFocus={() => { setCmds(commands()); setOpen(true); }} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => { setQ(e.target.value); setAt(0); setOpen(true); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setAt((i) => Math.min(i + 1, Math.max(hits.length - 1, 0))); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setAt((i) => Math.max(i - 1, 0)); }
          else if (e.key === "Escape") { setQ(""); setOpen(false); e.currentTarget.blur(); }
        }} />
      <span className="kbd" aria-hidden="true">Ctrl K</span>
      {open && (
        <div className="ask-pop" id={listId} role="listbox" aria-label="Answers">
          {!q.trim() && <div className="ask-try">
            <p>Ask in your own words, for example:</p>
            {examples.map((x) => <button key={x} type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => { setQ(x); setAt(0); }}>{x}</button>)}
          </div>}
          {q.trim() && !hits.length && <p className="ask-none">Nothing matches yet. Press Enter to search every tool for “{q}”.</p>}
          {hits.map((h, i) => {
            const Icon = ICON[h.kind];
            const off = h.kind === "command" && h.disabled;
            return (
              <button key={h.key} id={`ask-${h.key}`} type="button" role="option" aria-selected={i === at} aria-disabled={off || undefined}
                className={`ask-hit${i === at ? " is-at" : ""}${off ? " is-off" : ""}`} onMouseDown={(e) => e.preventDefault()} onMouseEnter={() => setAt(i)} onClick={() => pick(h)}>
                <span className={`ask-ico is-${h.kind}`} aria-hidden="true"><Icon size={16} /></span>
                <span className="ask-text"><strong>{h.label}</strong><span>{h.hint}</span></span>
                {i === at && !off && <span className="ask-enter" aria-hidden="true">Enter</span>}
              </button>
            );
          })}
        </div>
      )}
    </form>
  );
});
