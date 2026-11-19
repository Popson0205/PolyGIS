/** White-label branding, chosen at build time with VITE_BRAND (default "geoestate"). The same code ships as the
 *  GeoEstate product or, built with VITE_BRAND=osun, as the Osun State Land Information System — no fork. Colours live
 *  in brand.css keyed by [data-brand]; this holds the names, marks and metadata, and applies them at startup. */
export type Brand = {
  id: string;
  suite: string;          // the platform name shown beside the product (e.g. "GeoEstate", "OSLIS")
  suiteFull: string;      // long form for titles and sign-in
  product: string;        // this app's label within the suite
  vendor: string;         // the company behind it (licensing, invoices)
  logo: string;           // app icon / brand mark
  seal?: string;          // official crest, for sign-in and certified documents
  themeColor: string;     // browser theme-color
  poweredBy?: string;     // small co-brand line, when the suite isn't the vendor
};

const BRANDS: Record<string, Brand> = {
  geoestate: {
    id: "geoestate", suite: "GeoEstate", suiteFull: "GeoEstate", product: "PolyGIS",
    vendor: "GeoEstate NIG Limited", logo: "/logo.png", themeColor: "#0b1830",
  },
  osun: {
    id: "osun", suite: "OSLIS", suiteFull: "Osun State Land Information System", product: "Cadastral Mapping",
    vendor: "GeoEstate NIG Limited", logo: "/brand/osun-icon.svg", seal: "/brand/osun-seal.png",
    themeColor: "#064E30", poweredBy: "GeoEstate",
  },
};

export const BRAND: Brand = BRANDS[import.meta.env.VITE_BRAND as string] ?? BRANDS.geoestate;

/** Apply the brand to the document: colour theme (via a data attribute brand.css keys off), title, favicon, theme-color. */
export function applyBrand(): void {
  const d = document.documentElement;
  d.dataset.brand = BRAND.id;
  document.title = BRAND.id === "geoestate" ? `${BRAND.product} – ${BRAND.suite}` : `${BRAND.product} · ${BRAND.suiteFull}`;
  const set = (sel: string, rel: string, href: string) => {
    let el = document.head.querySelector<HTMLLinkElement>(`link[rel="${sel}"]`);
    if (!el) { el = document.createElement("link"); el.rel = rel; document.head.appendChild(el); }
    el.href = href;
  };
  if (BRAND.id !== "geoestate") { set("icon", "icon", BRAND.logo); set("apple-touch-icon", "apple-touch-icon", BRAND.logo); }
  const tc = document.head.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (tc) tc.content = BRAND.themeColor;
}
