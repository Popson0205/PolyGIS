import { Building2, LogOut, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../lib/api";
import { ROLE_NAME } from "../lib/format";
import type { App, Me } from "../lib/types";

const WHAT: Record<string, string> = { hub: "Field data, maps and dashboards", land: "Parcels, titles and transfers",
  check: "Location and due-diligence reports", farm: "Crop monitoring from satellite and drone", gis: "Maps, editing and analysis" };
const NOTE: Record<string, [string, string]> = { demo: ["Demo", "ge-badge-info"], grace: ["Renewal due", "ge-badge-warn"],
  expired: ["Read-only", "ge-badge-bad"], suspended: ["Suspended", "ge-badge-bad"] };
const longDay = (d: string | null) => (d ? new Date(`${d}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }) : "");

/** What the licence means today, in Geohub's words (the GeoEstate UI kit's licenceNotice). */
export function licenceNotice(p: Me["product"]): { tone: string; text: string } | null {
  if (!p) return null;
  if (p.status === "expired") return { tone: "bad", text: "Your organization's PolyGIS subscription has ended, so it is read-only: you can view and export your work but not add or change anything. Renew with GeoEstate NIG Limited to carry on." };
  if (p.status === "grace") return { tone: "warn", text: `Your organization's PolyGIS subscription ended on ${longDay(p.subscription_ends)}. Everything works until ${longDay(p.grace_ends)}; after that it is read-only. Renew with GeoEstate NIG Limited.` };
  if (p.status === "demo") return { tone: "info", text: `You are on a PolyGIS demo until ${longDay(p.subscription_ends)}. Ask GeoEstate NIG Limited for an invoice to keep using it.` };
  return null;
}

export function Apps({ apps }: { apps: App[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", away); window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", away); window.removeEventListener("keydown", esc); };
  }, [open]);
  if (apps.length < 2) return null;
  return (
    <div className="ge-apps" ref={ref}>
      <button type="button" className="ge-apps-btn" aria-label="GeoEstate apps" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">{[5.5, 12, 18.5].flatMap((y) => [5.5, 12, 18.5].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.8" fill="currentColor" />))}</svg>
      </button>
      {open && (
        <div className="ge-apps-pop" role="menu" aria-label="GeoEstate apps">
          <div className="ge-apps-head"><strong>GeoEstate apps</strong><span>One sign-in for all of them</span></div>
          {apps.map((a) => {
            const body = <>
              <span className="ge-app-tile" aria-hidden="true">{a.name.slice(0, 1)}</span>
              <span className="ge-app-text"><strong>{a.name}</strong><span>{a.current ? "You are here" : WHAT[a.product] ?? ""}</span></span>
              {NOTE[a.status] && <span className={`ge-badge ${NOTE[a.status][1]}`}>{NOTE[a.status][0]}</span>}
            </>;
            const cls = `ge-app ge-app-${a.product}`;
            if (a.current) return <div key={a.product} className={`${cls} is-here`} role="menuitem" aria-current="page">{body}</div>;
            if (!a.url) return <div key={a.product} className={`${cls} is-off`} role="menuitem" aria-disabled="true">{body}</div>;
            return <a key={a.product} className={cls} role="menuitem" href={a.url} target="_blank" rel="noopener">{body}</a>;   // each app in its own tab
          })}
        </div>
      )}
    </div>
  );
}

export const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "?";

/** Who is signed in, as a round badge that opens their account menu. */
export function UserMenu({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", away); window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", away); window.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="menu-wrap" ref={ref}>
      <button type="button" className="gis-user" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)} aria-label={`${me.name}: account`}>
        <span className="gis-avatar" aria-hidden="true">{initials(me.name)}</span><span>{me.name.split(" ")[0]}</span>
      </button>
      {open && (
        <div className="gis-user-pop" role="menu">
          <header><strong>{me.name}</strong><span>{me.email}</span></header>
          <button type="button" role="menuitem" disabled><Building2 size={16} aria-hidden="true" />{me.org_name ?? "GeoEstate staff"}</button>
          <button type="button" role="menuitem" disabled><UserRound size={16} aria-hidden="true" />{ROLE_NAME[me.role]}{me.product ? `, ${me.product.plan_name}` : ""}</button>
          <hr style={{ border: 0, borderTop: "1px solid var(--c-line-2)", margin: "4px 2px" }} />
          <button type="button" role="menuitem" onClick={onSignOut}><LogOut size={16} aria-hidden="true" />Sign out</button>
        </div>
      )}
    </div>
  );
}

/** GeoEstate's top bar (from the UI kit), with PolyGIS's sections. */
export default function Bar({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const [apps, setApps] = useState<App[]>([]);
  useEffect(() => { void api.apps().then(setApps, () => setApps([])); }, []);
  const notice = licenceNotice(me.product);
  const links: [string, string, boolean][] = [["/", "Projects", true], ["/activity", "Activity", me.role === "admin"]];
  return (
    <>
      <header className="ge-bar">
        <a className="ge-brand" href="/"><img src="/logo.png" alt="GeoEstate" /><span>GeoEstate <strong>PolyGIS</strong></span></a>
        <nav className="gis-nav" aria-label="Sections">
          {links.filter((l) => l[2]).map(([to, label]) => <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>)}
        </nav>
        <span className="ge-spacer" />
        <Apps apps={apps} />
        <UserMenu me={me} onSignOut={onSignOut} />
      </header>
      {notice && <div className={`ge-notice ge-notice-${notice.tone}`} role="status">{notice.text}</div>}
    </>
  );
}

/** The apps list, loaded once. */
export function useApps() {
  const [apps, setApps] = useState<App[]>([]);
  useEffect(() => { void api.apps().then(setApps, () => setApps([])); }, []);
  return apps;
}
