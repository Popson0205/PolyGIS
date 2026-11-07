import { useCallback, useEffect, useState } from "react";

/** Load something once (and again on reload()); errors are kept to show. */
export function useLoad<T>(f: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const go = useCallback(f, deps);
  const reload = useCallback(async () => {
    try { setData(await go()); setError(null); } catch (e) { setError(e); }
  }, [go]);
  useEffect(() => { void reload(); }, [reload]);
  return { data, error, reload, setData };
}
