import { useCallback, useEffect, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import Bar from "./components/Bar";
import { ErrorText } from "./components/ui";
import { ApiError, api, signInUrl } from "./lib/api";
import type { Me } from "./lib/types";
import { useLoad } from "./lib/useLoad";
import { splash } from "./splash";
import ProjectsPage from "./pages/ProjectsPage";
import SharePage from "./pages/SharePage";
import SignedOutPage from "./pages/SignedOutPage";
import WorkspacePage from "./pages/WorkspacePage";

/** PolyGIS has no sign-in page of its own: someone not signed in goes to Geohub and comes straight back. */
export default function App() {
  const loc = useLocation();
  const [me, setMe] = useState<Me | null>(null);
  const [stop, setStop] = useState<string | null>(null);
  const out = loc.pathname === "/signed-out";
  const shared = loc.pathname.startsWith("/share/");          // a map shared by a link: no sign-in needed

  const load = useCallback(async () => {
    splash.step("Checking your GeoEstate ID", 40);
    try {
      const m = await api.me();
      const plan = m.product?.plan_name?.replace(/^PolyGIS\s*/i, "");
      if (plan) splash.edition(plan);
      splash.step(`Opening ${m.org_name ?? "your organization"}'s projects`, 80);
      setMe(m);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401 && e.code !== "no_product") { splash.step("Taking you to sign in", 90); window.location.assign(signInUrl()); return; }
      setStop(e instanceof Error ? e.message : "PolyGIS could not open.");
    }
  }, []);
  useEffect(() => { if (!out && !shared) void load(); }, [load, out, shared]);
  useEffect(() => { if (me || stop || out) splash.done(); }, [me, stop, out]);
  useEffect(() => {
    const gone = (e: Event) => {
      const why = (e as CustomEvent).detail;
      window.location.assign(`/signed-out?reason=${encodeURIComponent(typeof why === "string" ? why : "Your sign-in has ended. Sign in again.")}`);
    };
    window.addEventListener("gis:signed-out", gone);
    return () => window.removeEventListener("gis:signed-out", gone);
  }, []);

  if (shared) return <Routes><Route path="/share/:token" element={<SharePage />} /></Routes>;
  if (out) return <SignedOutPage />;
  if (stop) return <SignedOutPage reason={stop} />;
  if (!me) return <p className="loading">Opening PolyGIS…</p>;
  const signOut = async () => {
    try { await api.signOut(); } finally { window.location.assign("/signed-out?bye=1"); }
  };
  const inWorkspace = loc.pathname.startsWith("/projects/");
  return (
    <div className={inWorkspace ? "gis-app is-workspace" : "gis-app"}>
      {!inWorkspace && <Bar me={me} onSignOut={signOut} />}
      <Routes>
        <Route path="/" element={<ProjectsPage me={me} />} />
        <Route path="/projects/:id" element={<WorkspacePage me={me} onSignOut={signOut} />} />
        <Route path="/activity" element={<ActivityPage />} />
        <Route path="*" element={<div className="ge-main"><div className="ge-card"><h1>Not found</h1><p><a href="/">Back to the projects</a></p></div></div>} />
      </Routes>
    </div>
  );
}

const WORDS: Record<string, string> = { "project.create": "made the project", "project.delete": "deleted the project", "layer.import": "imported",
  "layer.create": "made the layer", "layer.delete": "removed the layer", "layer.export": "exported", "field.add": "added a field to",
  "field.rename": "renamed a field of", "field.delete": "deleted a field of", "field.calculate": "calculated a field of" };

function ActivityPage() {
  const { data, error } = useLoad(api.activity);
  return (
    <div className="ge-main">
      <h1>Activity</h1>
      <ErrorText error={error} />
      <table className="ge-table">
        <thead><tr><th>When</th><th>Who</th><th>What</th></tr></thead>
        <tbody>{data?.map((a, k) => (
          <tr key={k}><td>{new Date(a.at).toLocaleString("en-GB")}</td><td>{a.actor_name}</td>
            <td>{WORDS[a.action] ?? a.action} {String(a.details.name ?? a.details.file ?? "")}{a.details.features != null ? ` (${Number(a.details.features).toLocaleString("en-GB")} features)` : ""}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}
