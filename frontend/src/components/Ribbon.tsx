import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { ChevronDown, LayoutGrid } from "lucide-react";

/** A ribbon group: its buttons, with its name under them. When the ribbon is too narrow for all its groups, the ones at
 *  its end fold (RibbonBody marks them): the group is then one button with its name, which opens its tools below it. */
export function Group({ name, children }: { name: string; children: ReactNode }) {
  const [open, setOpen] = useState<CSSProperties | null>(null);
  const el = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(null), []);
  useEffect(() => {
    if (!open) return;
    const out = (e: Event) => { if (!el.current?.contains(e.target as Node)) close(); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("pointerdown", out, true);
    document.addEventListener("keydown", key);
    window.addEventListener("resize", close);
    return () => { document.removeEventListener("pointerdown", out, true); document.removeEventListener("keydown", key); window.removeEventListener("resize", close); };
  }, [open, close]);
  const toggle = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (open) { close(); return; }
    const r = e.currentTarget.getBoundingClientRect();
    setOpen({ top: Math.round(r.bottom + 4), left: Math.round(Math.max(8, Math.min(r.left, window.innerWidth - 8 - 420))) });
  };
  return (
    <div ref={el} className={`ws-group${open ? " is-open" : ""}`} role="group" aria-label={name}>
      <button type="button" className="ws-tb ws-group-fold" aria-expanded={!!open} aria-haspopup="true" onClick={toggle} title={`${name}: its tools`}>
        <span aria-hidden="true" className="ws-tb-icon"><LayoutGrid /></span>{name}<ChevronDown size={13} aria-hidden="true" className="ws-group-fold-arrow" /></button>
      {/* a click on one of its tools closes the folded group's panel */}
      <div className="ws-group-body" style={open ?? undefined} onClick={open ? (e) => { if ((e.target as HTMLElement).closest("button, select")?.tagName === "BUTTON") window.setTimeout(close, 0); } : undefined}>{children}</div>
      <div className="ws-group-name">{name}</div>
    </div>
  );
}

/** The ribbon's row of groups. It never scrolls: when the groups do not all fit, the last ones fold into a button each
 *  (see Group), as many as needed, and unfold again when there is room (a wider window, a mode with fewer tools). */
export function RibbonBody({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  const el = useRef<HTMLDivElement>(null);
  const fit = useCallback(() => {
    const body = el.current;
    if (!body || !body.clientWidth) return;
    const groups = Array.from(body.querySelectorAll<HTMLElement>(".ws-group")).filter((g) => g.getClientRects().length > 0 || g.hasAttribute("data-folded"));
    groups.forEach((g) => g.removeAttribute("data-folded"));
    const shown = groups.filter((g) => g.getClientRects().length > 0);
    for (let i = shown.length - 1; i > 0 && body.scrollWidth > body.clientWidth + 1; i--) shown[i].setAttribute("data-folded", "");
  }, []);
  useLayoutEffect(() => {
    const body = el.current;
    if (!body) return;
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(body);
    // a mode opened or closed (its tools are shown or hidden), or tools added or taken away
    const mo = new MutationObserver(fit);
    mo.observe(body, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
    document.fonts?.ready.then(fit).catch(() => undefined);
    return () => { ro.disconnect(); mo.disconnect(); };
  }, [fit]);
  return <div ref={el} className={`ws-ribbon-body${className ? ` ${className}` : ""}`} role="toolbar" aria-label={label}>{children}</div>;
}
/** A large ribbon button: an icon over its label. */
export function Tb({ icon, label, onClick, on, disabled, title }: { icon: ReactNode; label: string; onClick: () => void; on?: boolean; disabled?: boolean; title?: string }) {
  return <button type="button" className={`ws-tb${on ? " is-on" : ""}`} onClick={onClick} disabled={disabled} aria-pressed={on} title={title}><span aria-hidden="true" className="ws-tb-icon">{icon}</span>{label}</button>;
}

/** A small ribbon button (icon and label on one line), stacked three high beside the large ones. */
export function Ts({ icon, label, onClick, on, disabled, title }: { icon: ReactNode; label: string; onClick: () => void; on?: boolean; disabled?: boolean; title?: string }) {
  return <button type="button" className={`ws-tb-small${on ? " is-on" : ""}`} onClick={onClick} disabled={disabled} aria-pressed={on} title={title}><span aria-hidden="true" className="ws-tb-icon">{icon}</span>{label}</button>;
}
