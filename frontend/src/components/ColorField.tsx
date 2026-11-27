/** A colour picker in PolyGIS's own style, used across symbology: a swatch button that opens a
 *  palette, with "No colour", a screen eyedropper (where the browser supports it), and a custom
 *  colour for anything the palette does not hold. "No colour" is the value `transparent`. */
import { Pipette } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { IconButton } from "./ui";

// A compact, GIS-friendly palette: a greyscale row, then hues light-to-dark.
const PALETTE: string[] = [
  "#ffffff", "#e5e7eb", "#c7ccd4", "#9aa2b1", "#6b7280", "#374151", "#1f2937", "#000000",
  "#fde2e1", "#f8b4b4", "#ef4444", "#dc2626", "#b91c1c", "#f97316", "#ea580c", "#9a3412",
  "#fef3c7", "#fde68a", "#f59e0b", "#d97706", "#fde047", "#facc15", "#eab308", "#a16207",
  "#dcfce7", "#86efac", "#4ade80", "#22c55e", "#16a34a", "#15803d", "#166534", "#14532d",
  "#cffafe", "#67e8f9", "#22d3ee", "#06b6d4", "#0ea5e9", "#2563eb", "#1d4ed8", "#1e3a8a",
  "#ede9fe", "#c4b5fd", "#8b5cf6", "#7c3aed", "#f0abfc", "#d946ef", "#a21caf", "#701a75",
];

const isNone = (v: string) => !v || v.toLowerCase() === "transparent" || v.toLowerCase() === "none";
const norm = (v: string) => (isNone(v) ? "#ffffff" : v);

type ED = { open: () => Promise<{ sRGBHex: string }> };

export default function ColorField({ value, onChange, allowNone, label }:
  { value: string; onChange: (c: string) => void; allowNone?: boolean; label?: string }) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const wrap = useRef<HTMLDivElement>(null);
  const eyedropper = typeof (window as unknown as { EyeDropper?: unknown }).EyeDropper === "function";

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  const show = (e: React.MouseEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setAt({ x: r.left, y: r.bottom + 4 });
    setOpen((o) => !o);
  };
  const pick = (c: string) => { onChange(c); setOpen(false); };
  const eyedrop = async () => {
    const ctor = (window as unknown as { EyeDropper?: new () => ED }).EyeDropper;
    if (!ctor) return;
    try { pick((await new ctor().open()).sRGBHex); } catch { /* cancelled */ }
  };

  return (
    <div className="cf" ref={wrap}>
      <button type="button" className={`cf-swatch${isNone(value) ? " cf-none" : ""}`} onClick={show}
        aria-label={label ? `${label}: ${isNone(value) ? "no colour" : value}` : "Choose a colour"} aria-haspopup="dialog" aria-expanded={open}
        style={isNone(value) ? undefined : { background: value }} title={isNone(value) ? "No colour" : value} />
      {open && (
        <div className="cf-pop" role="dialog" aria-label="Colour" style={{ left: at.x, top: at.y }}>
          {allowNone && (
            <button type="button" className="cf-row cf-norow" onClick={() => pick("transparent")}>
              <span className="cf-swatch cf-none" aria-hidden="true" /> No colour
            </button>
          )}
          <div className="cf-grid">
            {PALETTE.map((c) => (
              <button key={c} type="button" className={`cf-dot${!isNone(value) && value.toLowerCase() === c ? " is-on" : ""}`}
                style={{ background: c }} onClick={() => pick(c)} aria-label={c} title={c} />
            ))}
          </div>
          <div className="cf-foot">
            {eyedropper && <IconButton label="Pick a colour from the screen" onClick={() => void eyedrop()}><Pipette size={15} /></IconButton>}
            <label className="cf-custom" title="Custom colour">
              <input type="color" value={norm(value)} onChange={(e) => onChange(e.target.value)} aria-label="Custom colour" />
              <span>Custom</span>
            </label>
          </div>
        </div>
      )}
    </div>
  );
}
