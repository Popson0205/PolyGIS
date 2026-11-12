/** The GeoEstate launch screen (public/geoestate-splash.js, shared by every GeoEstate app): what PolyGIS is doing while it
 * starts, and when it is ready. Safe when the screen is not on the page. */
declare global {
  interface Window { GeoEstateSplash?: { step: (text: string, pct?: number) => void; edition: (text: string) => void; done: () => void } }
}
export const splash = {
  step: (text: string, pct?: number) => window.GeoEstateSplash?.step(text, pct),
  edition: (text: string) => window.GeoEstateSplash?.edition(text),
  done: () => window.GeoEstateSplash?.done(),
};
