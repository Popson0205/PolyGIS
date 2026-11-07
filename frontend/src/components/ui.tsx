import { createContext, useCallback, useContext, useState, type ButtonHTMLAttributes, type ReactNode } from "react";

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
      <div className="toasts" aria-live="polite">{toasts.map((t) => <div key={t.id} className={`toast is-${TONE[t.tone]}`}>{t.msg}</div>)}</div>
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
