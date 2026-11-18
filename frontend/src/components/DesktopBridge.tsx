/** Present only in the PolyGIS desktop app. When the user opens a .polygis file from the OS (double-click, or
 *  File → Open in the native menu), the desktop shell hands the bytes here; we import it and open the project.
 *  In a normal browser `window.polygisDesktop` is absent and this renders nothing. */
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { BASE } from "../lib/api";
import { useToast } from "./ui";

type OpenPayload = { name: string; base64: string };
type Bridge = { isDesktop?: boolean; onOpenProject?: (cb: (p: OpenPayload) => void) => () => void };

export default function DesktopBridge() {
  const nav = useNavigate();
  const toast = useToast();
  const busy = useRef(false);
  useEffect(() => {
    const d = (window as unknown as { polygisDesktop?: Bridge }).polygisDesktop;
    if (!d?.onOpenProject) return;
    // Import directly against the endpoint so the desktop shell doesn't depend on a particular web-app build; if the
    // server doesn't offer project files yet, the user sees a friendly note.
    return d.onOpenProject(async ({ name, base64 }) => {
      if (busy.current) return;
      busy.current = true;
      try {
        const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
        const fd = new FormData();
        fd.append("file", new File([bytes], name || "project.polygis", { type: "application/x-polygis" }));
        const res = await fetch(`${BASE}/projects/import-file`, { method: "POST", credentials: "same-origin", headers: { "X-GeoEstate": "1" }, body: fd });
        if (res.status === 404) { toast("This PolyGIS can't open project files yet — update to the latest version.", "error"); return; }
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Could not open the project file.");
        const out = await res.json() as { id: string; name: string; rasters_skipped: number };
        const note = out.rasters_skipped ? ` (${out.rasters_skipped} raster layer${out.rasters_skipped > 1 ? "s" : ""} weren't in the file)` : "";
        toast(`${out.name} opened${note}.`, "success");
        nav(`/projects/${out.id}`);
      } catch (e) {
        toast(e instanceof Error ? e.message : "Could not open the project file.", "error");
      } finally { busy.current = false; }
    });
  }, [nav, toast]);
  return null;
}
