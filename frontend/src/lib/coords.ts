import proj4 from "proj4";
import { api } from "./api";
import type { CrsDef } from "./types";

/** Coordinates in the project's system, worked out in the browser with the server's own definition (which carries
 *  the datum shift, e.g. Minna to WGS 84), so the read-out agrees with what the server stores to the millimetre. */
const defs = new Map<number, CrsDef>();

export async function loadCrs(epsg: number): Promise<CrsDef> {
  const have = defs.get(epsg);
  if (have) return have;
  const d = epsg === 4326 ? { epsg, name: "WGS 84", proj4: "+proj=longlat +datum=WGS84 +no_defs", geographic: true, unit: "degree" } : await api.crsDef(epsg);
  proj4.defs(`EPSG:${epsg}`, d.proj4);
  defs.set(epsg, d);
  return d;
}

export function fromLngLat(epsg: number, lng: number, lat: number): [number, number] {
  if (epsg === 4326) return [lng, lat];
  return proj4("EPSG:4326", `EPSG:${epsg}`, [lng, lat]) as [number, number];
}

export function toLngLat(epsg: number, x: number, y: number): [number, number] {
  if (epsg === 4326) return [x, y];
  return proj4(`EPSG:${epsg}`, "EPSG:4326", [x, y]) as [number, number];
}

/** "7.466400° E, 9.076500° N" or "E 331,529.157 m  N 1,003,545.973 m". */
export function formatCoords(d: CrsDef | undefined, lng: number, lat: number): string {
  if (!d || d.geographic) {
    const [x, y] = d && d.epsg !== 4326 ? fromLngLat(d.epsg, lng, lat) : [lng, lat];
    return `${Math.abs(x).toFixed(6)}° ${x >= 0 ? "E" : "W"}, ${Math.abs(y).toFixed(6)}° ${y >= 0 ? "N" : "S"}`;
  }
  const [x, y] = fromLngLat(d.epsg, lng, lat);
  const f = (v: number) => v.toLocaleString("en-GB", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  return `E ${f(x)} m  N ${f(y)} m`;
}

/** A readable length and area. */
export const metres = (m: number) => (m >= 1000 ? `${(m / 1000).toLocaleString("en-GB", { maximumFractionDigits: 3 })} km` : `${m.toLocaleString("en-GB", { maximumFractionDigits: 2 })} m`);
export const squareMetres = (a: number) => (a >= 10000 ? `${(a / 10000).toLocaleString("en-GB", { maximumFractionDigits: 4 })} ha (${Math.round(a).toLocaleString("en-GB")} m²)` : `${a.toLocaleString("en-GB", { maximumFractionDigits: 2 })} m²`);
