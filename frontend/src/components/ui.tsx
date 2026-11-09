import { AlertCircle, CheckCircle2, Info } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";

export function Button({ variant = "default", small, busy, children, className = "", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "danger" | "ghost"; small?: boolean; busy?: boolean }) {
  return (
    <button type="button" {...rest} disabled={rest.disabled || busy} aria-busy={busy || undefined}
      className={`ge-btn ge-btn-${variant}${small ? " ge-btn-sm" : ""} ${className}`}>{children}</button>
  );
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null;
  return <p className="error" role="alert">{error instanceof Error ? error.message : String(error)}</p>;
}

type Tone = "ok" | "bad" | "info" | "success" | "error";
const TONE: Record<Tone, string> = { ok: "ok", success: "ok", bad: "bad", error: "bad", info: "info" };
const ToastCtx = createContext<(msg: string, tone?: Tone) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<{ id: number; msg: string; tone: Tone }[]>([]);
  const show = useCallback((msg: string, tone: Tone = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, msg, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div className="toasts" aria-live="polite">{toasts.map((t) => (
        <div key={t.id} className={`toast is-${TONE[t.tone]}`}>
          {TONE[t.tone] === "ok" ? <CheckCircle2 size={16} aria-hidden="true" /> : TONE[t.tone] === "bad" ? <AlertCircle size={16} aria-hidden="true" /> : <Info size={16} aria-hidden="true" />}
          <span>{t.msg}</span>
        </div>
      ))}</div>
    </ToastCtx.Provider>
  );
}

/** Run an action, show what happened, keep the button busy meanwhile. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const run = useCallback(async <T,>(key: string, f: () => Promise<T>, done?: string): Promise<T | undefined> => {
    setBusy(key);
    try { const r = await f(); if (done) toast(done); return r; }
    catch (e) { toast(e instanceof Error ? e.message : "That did not work.", "bad"); return undefined; }
    finally { setBusy(null); }
  }, [toast]);
  return { busy, run };
}

/** A square button with only an icon; its label is spoken and shown as a tooltip. */
export function IconButton({ label, children, on, className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; on?: boolean }) {
  return (
    <button type="button" aria-label={label} title={label} aria-pressed={on} {...rest} className={`ib${on ? " is-on" : ""} ${className}`}>{children}</button>
  );
}

export interface MenuItem { label: string; icon?: ReactNode; onSelect: () => void; danger?: boolean; disabled?: boolean; hint?: string; divider?: boolean }

/** A small menu that opens from a button: arrow keys move, Enter chooses, Escape or a click elsewhere closes it. */
export function Menu({ label, items, trigger, align = "end", className = "" }: { label: string; items: MenuItem[]; trigger: ReactNode; align?: "start" | "end"; className?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", away);
    setTimeout(() => list.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus(), 0);
    return () => window.removeEventListener("mousedown", away);
  }, [open]);
  const keys = (e: React.KeyboardEvent) => {
    const bs = Array.from(list.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? []);
    const k = bs.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowDown") { e.preventDefault(); bs[(k + 1) % bs.length]?.focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); bs[(k - 1 + bs.length) % bs.length]?.focus(); }
    if (e.key === "Escape" || e.key === "Tab") { setOpen(false); ref.current?.querySelector<HTMLButtonElement>("button")?.focus(); }
  };
  return (
    <div className={`menu-wrap ${className}`} ref={ref}>
      <button type="button" className="ib" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open} onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}>{trigger}</button>
      {open && (
        <div className={`menu menu-${align}`} role="menu" aria-label={label} ref={list} onKeyDown={keys}>
          {items.map((it, k) => it.divider ? <hr key={k} /> : (
            <button key={it.label} type="button" role="menuitem" className={it.danger ? "is-danger" : ""} disabled={it.disabled}
              onClick={(e) => { e.stopPropagation(); setOpen(false); it.onSelect(); }}>
              <span className="menu-icon" aria-hidden="true">{it.icon}</span><span>{it.label}</span>{it.hint && <kbd>{it.hint}</kbd>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** A bar to drag that resizes the pane beside it; arrow keys move it too. */
export function Resizer({ axis, value, min, max, onChange, invert, label, className = "" }: { axis: "x" | "y"; value: number; min: number; max: number; onChange: (v: number) => void; invert?: boolean; label: string; className?: string }) {
  const start = (e: React.PointerEvent) => {
    e.preventDefault();
    const from = axis === "x" ? e.clientX : e.clientY;
    const base = value;
    const move = (ev: PointerEvent) => {
      const d = (axis === "x" ? ev.clientX : ev.clientY) - from;
      onChange(Math.min(max, Math.max(min, base + (invert ? -d : d))));
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); document.body.classList.remove("is-resizing"); };
    document.body.classList.add("is-resizing");
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  };
  const key = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 40 : 10;
    const grow = axis === "x" ? (e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0) : (e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0);
    if (grow) { e.preventDefault(); onChange(Math.min(max, Math.max(min, value + (invert ? -grow : grow) * step))); }
  };
  return <div className={`resizer resizer-${axis} ${className}`} role="separator" aria-orientation={axis === "x" ? "vertical" : "horizontal"} aria-label={label}
    aria-valuenow={Math.round(value)} aria-valuemin={min} aria-valuemax={max} tabIndex={0} onPointerDown={start} onKeyDown={key} />;
}

/** A value kept in this browser between visits (pane widths, favourite tools). */
export function useStored<T>(key: string, initial: T): [T, (v: T | ((x: T) => T)) => void] {
  const [v, setV] = useState<T>(() => {
    try { const s = localStorage.getItem(`polygis:${key}`); return s ? (JSON.parse(s) as T) : initial; } catch { return initial; }
  });
  const set = useCallback((x: T | ((y: T) => T)) => {
    setV((cur) => {
      const next = typeof x === "function" ? (x as (y: T) => T)(cur) : x;
      try { localStorage.setItem(`polygis:${key}`, JSON.stringify(next)); } catch { /* private window */ }
      return next;
    });
  }, [key]);
  return [v, set];
}
