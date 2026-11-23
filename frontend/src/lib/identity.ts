/** White-label identity applied at runtime, so ONE PolyGIS build serves every client.
 *
 * The organization's brand (colour, logo, name, seal) is set by GeoEstate at org setup, carried in the GeoEstate ID
 * token and delivered on /me as `org_brand`. Here it is applied to the document: the violet accent is rebuilt from
 * the brand colour, and the page title follows the brand. The top-bar lockup uses `brandLockup()`.
 *
 * Nothing is baked in at build time; with no brand set the app keeps its GeoEstate PolyGIS look.
 */
import type { Me, OrgBrand } from "./types";

type Rgb = [number, number, number];
const parseHex = (hex: string): Rgb | null => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const toHex = (c: Rgb) => "#" + c.map((x) => Math.round(Math.max(0, Math.min(255, x))).toString(16).padStart(2, "0")).join("");
const mix = (a: Rgb, b: Rgb, t: number): Rgb => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const lin = (c: number) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const luminance = (c: Rgb) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
const contrastWhite = (c: Rgb) => 1.05 / (luminance(c) + 0.05);
/** Darken until white text reads at 4.5:1 — the accent carries white text (buttons, avatars). */
const readable = (c: Rgb): Rgb => { while (contrastWhite(c) < 4.5) c = c.map((x) => Math.floor(x * 0.92)) as Rgb; return c; };

const WHITE: Rgb = [255, 255, 255], BLACK: Rgb = [0, 0, 0];
const STYLE_ID = "org-brand";

/** Rebuild the violet accent scale (styles.css) from the brand colour, or remove the override with null. */
export function applyOrgBrand(brand: OrgBrand | null | undefined): void {
  if (typeof document === "undefined") return;
  document.getElementById(STYLE_ID)?.remove();
  const base = brand?.color ? parseHex(brand.color) : null;
  if (base) {
    const c = readable(base);
    const vars = {
      "--c-violet": toHex(c),
      "--c-violet-ink": toHex(mix(c, BLACK, 0.18)),
      "--c-violet-50": toHex(mix(c, WHITE, 0.93)),
      "--c-violet-100": toHex(mix(c, WHITE, 0.86)),
    };
    const el = document.createElement("style");
    el.id = STYLE_ID;
    el.textContent = `:root{${Object.entries(vars).map(([k, v]) => `${k}:${v}`).join(";")}}`;
    document.head.appendChild(el);
    setThemeColor(toHex(c));
  } else {
    setThemeColor("#6b3fa0");
  }
  document.title = brandName(brand) ?? "PolyGIS";
  // Remember a real client brand so the launch splash (which runs before sign-in) can show it next time.
  try {
    const nm = brandName(brand);
    if (nm || brand?.color) localStorage.setItem("ge-brand", JSON.stringify({ name: nm, color: brand?.color ?? null, logo_url: brand?.logo_url ?? null }));
  } catch { /* storage unavailable */ }
}

const THEME_META_ID = "brand-theme-color";
function setThemeColor(color: string): void {
  let meta = document.getElementById(THEME_META_ID) as HTMLMetaElement | null
    ?? (document.querySelector('meta[name="theme-color"]') as HTMLMetaElement | null);
  if (!meta) { meta = document.createElement("meta"); meta.name = "theme-color"; document.head.appendChild(meta); }
  meta.id = THEME_META_ID;
  meta.content = color;
}

const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);
/** The brand's wordmark, if set. */
export function brandName(brand: OrgBrand | null | undefined): string | null {
  return str(brand?.name);
}

/** What the top bar shows: a client lockup (brand logo + name) when branded, else the GeoEstate PolyGIS lockup. */
export function brandLockup(me: Me | null | undefined): { branded: boolean; name: string; logo_url: string | null } {
  const b = me?.org_brand;
  const name = brandName(b);
  if (name) return { branded: true, name, logo_url: str(b?.logo_url) };
  return { branded: false, name: "PolyGIS", logo_url: null };
}
