import { useEffect, useRef, useState } from "react";
import type { Layer } from "../lib/types";
import { Button, ErrorText, useStored } from "./ui";

/** Auto Apply, as in ArcGIS: with it on, every change shows on the map at once (while dragging a slider too) and is
 *  saved a moment after the last one; with it off, changes wait for Apply. Saving a style does not reload the layer's
 *  data, so the map does not flicker (`keepVersion`). */
export function useAutoApply<T>({ layer, canEdit, preview, save, keepVersion, onChange }: {
  layer: Layer; canEdit: boolean; preview: (v: T) => Layer; save: (v: T) => Promise<Layer>; keepVersion: (v: T) => boolean; onChange: (l: Layer) => void;
}) {
  const [auto, setAuto] = useStored<boolean>("auto-apply", true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const seq = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef({ layer, preview, save, keepVersion, onChange });
  latest.current = { layer, preview, save, keepVersion, onChange };
  useEffect(() => () => window.clearTimeout(timer.current), []);

  async function commit(v: T, n: number) {
    const { save: sv, keepVersion: kv, onChange: oc } = latest.current;
    setBusy(true);
    try {
      const saved = await sv(v);
      if (n === seq.current) { oc(kv(v) ? { ...saved, version: latest.current.layer.version } : saved); setError(null); setDirty(false); }
    } catch (e) {
      if (n === seq.current) setError(e);
    } finally {
      if (n === seq.current) setBusy(false);
    }
  }
  /** A change: shown and saved shortly (Auto Apply), or only noted until Apply. */
  function change(v: T) {
    if (!canEdit) return;
    setDirty(true);
    if (!auto) return;
    latest.current.onChange(latest.current.preview(v));
    window.clearTimeout(timer.current);
    const n = ++seq.current;
    timer.current = window.setTimeout(() => void commit(v, n), 450);
  }
  /** Apply now (the button, or a change that should not wait). */
  function apply(v: T) {
    if (!canEdit) return;
    window.clearTimeout(timer.current);
    latest.current.onChange(latest.current.preview(v));
    void commit(v, ++seq.current);
  }
  return { auto, setAuto, change, apply, busy, error, dirty };
}

/** The bottom of a symbology or label pane: the Auto Apply switch, and Apply when it is off. */
export function AutoApplyBar({ auto, setAuto, busy, dirty, error, onApply, canEdit }: {
  auto: boolean; setAuto: (v: boolean) => void; busy: boolean; dirty: boolean; error: unknown; onApply: () => void; canEdit: boolean;
}) {
  if (!canEdit) return <p className="ws-muted aa-bar">Only editors can change how a layer is drawn.</p>;
  return (
    <div className="aa-bar">
      <ErrorText error={error} />
      <div className="aa-row">
        <label className="aa-switch"><input type="checkbox" role="switch" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          <span aria-hidden="true" className="aa-track"><span /></span>Auto Apply</label>
        <span className="aa-state" aria-live="polite">{busy ? "Saving…" : auto ? (dirty ? "" : "Saved") : dirty ? "Not applied yet" : ""}</span>
        {!auto && <Button small variant="primary" busy={busy} disabled={!dirty} onClick={onApply}>Apply</Button>}
      </div>
    </div>
  );
}
