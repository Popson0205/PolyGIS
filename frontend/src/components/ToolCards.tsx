/** Tool cards: the analysis tools you use most (your favourites, then the latest), as cards over the bottom of the map in
 *  Analyse mode. One click opens the tool; Ask PolyGIS and "All tools" reach the rest. Folds away to a small button. */
import { ChevronDown, Star, Workflow, Wrench } from "lucide-react";
import { useStored } from "./ui";
import type { Tool } from "../lib/types";

const read = (k: string, d: string[]) => { try { return JSON.parse(localStorage.getItem(`polygis:${k}`) ?? "null") ?? d; } catch { return d; } };

export function ToolCards({ tools, onOpen, onAll, onFlows }: { tools: Tool[]; onOpen: (t: Tool) => void; onAll: () => void; onFlows: () => void }) {
  const [folded, setFolded] = useStored("tool-cards-folded", false);
  if (!tools.length) return null;
  // the same favourites and latest tools as the Tools pane keeps
  const stars: string[] = read("tool-stars", ["buffer", "clip", "dissolve", "spatial_join"]);
  const recent: string[] = read("tool-recent", []);
  const ids = Array.from(new Set([...stars, ...recent, "intersect", "near", "hot_spots"]));
  const cards = ids.map((x) => tools.find((t) => t.id === x)).filter(Boolean).slice(0, 6) as Tool[];
  if (folded) return <button type="button" className="tc-open" onClick={() => setFolded(false)}><Wrench size={15} aria-hidden="true" />Tool cards</button>;
  return (
    <section className="tc" aria-label="Tool cards">
      <div className="tc-head">
        <strong>Analyse</strong><span>Your tools · {Math.max(tools.length - cards.length, 0)} more: ask above</span>
        <span className="tc-gap" />
        <button type="button" className="tc-link" onClick={onFlows}><Workflow size={14} aria-hidden="true" />Flows</button>
        <button type="button" className="tc-link" onClick={onAll}>All tools</button>
        <button type="button" className="tc-fold" aria-label="Fold the tool cards" onClick={() => setFolded(true)}><ChevronDown size={16} /></button>
      </div>
      <div className="tc-grid">
        {cards.map((t) => (
          <button key={t.id} type="button" className="tc-card" onClick={() => onOpen(t)} title={t.about}>
            <strong>{stars.includes(t.id) && <Star size={12} fill="currentColor" aria-label="Favourite" />}{t.name}</strong><span>{t.about}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
