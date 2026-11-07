import { signInUrl } from "../lib/api";

/** After signing out, or when sign-in could not finish (no PolyGIS for the organization, a stale link…). */
export default function SignedOutPage({ reason }: { reason?: string }) {
  const p = new URLSearchParams(window.location.search);
  const why = reason ?? p.get("reason");
  const bye = p.get("bye");
  return (
    <main className="ge-main">
      <section className="ge-card signed-out">
        <img src="/logo.png" alt="GeoEstate" />
        <h1>{bye ? "You have signed out" : "PolyGIS"}</h1>
        {why ? <p role="alert">{why}</p> : <p className="ge-muted">{bye ? "Your Geohub account stays signed in." : "Sign in with the account you use for Geohub."}</p>}
        <p><a className="ge-btn ge-btn-primary" href={signInUrl("/")}>Sign in with GeoEstate</a></p>
      </section>
    </main>
  );
}
