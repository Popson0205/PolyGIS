import type { GeorefPoint } from "./types";

/** Where the next point probably is on the map, from an affine fit to the points so far (least squares in longitude and
 *  latitude, plenty for a suggestion). */
export function suggest(points: GeorefPoint[], col: number, row: number): [number, number] | null {
  const on = points.filter((p) => p.on);
  if (on.length < 3) return null;
  const solve = (key: "lng" | "lat") => {
    // normal equations for v = a·col + b·row + c
    const M = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], r = [0, 0, 0];
    for (const p of on) {
      const x = [p.col, p.row, 1];
      for (let i = 0; i < 3; i++) { r[i] += x[i] * p[key]; for (let j = 0; j < 3; j++) M[i][j] += x[i] * x[j]; }
    }
    const det = (m: number[][]) => m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    const d = det(M);
    if (Math.abs(d) < 1e-9) return null;
    const coef = [0, 1, 2].map((k) => det(M.map((row_, i) => row_.map((v, j) => (j === k ? r[i] : v)))) / d);
    return coef[0] * col + coef[1] * row + coef[2];
  };
  const lng = solve("lng"), lat = solve("lat");
  return lng == null || lat == null || !Number.isFinite(lng) || !Number.isFinite(lat) ? null : [lng, lat];
}

/** Degrees, minutes and seconds with a hemisphere letter, as decimal degrees (negative west and south). */
export function fromDms(deg: number, min: number, sec: number, hemi: "N" | "S" | "E" | "W"): number {
  if (![deg, min, sec].every((v) => Number.isFinite(v) && v >= 0)) throw new Error("Degrees, minutes and seconds must be numbers of 0 or more.");
  if (min >= 60 || sec >= 60) throw new Error("Minutes and seconds must be below 60.");
  const v = deg + min / 60 + sec / 3600;
  return hemi === "S" || hemi === "W" ? -v : v;
}

/** Decimal degrees as whole degrees, whole minutes and seconds (to 4 places), with the hemisphere. */
export function toDms(v: number, axis: "lat" | "lng"): { deg: number; min: number; sec: number; hemi: "N" | "S" | "E" | "W" } {
  const hemi = axis === "lat" ? (v < 0 ? "S" : "N") : (v < 0 ? "W" : "E");
  let a = Math.abs(v);
  let deg = Math.floor(a);
  a = (a - deg) * 60;
  let min = Math.floor(a);
  let sec = Math.round((a - min) * 60 * 10000) / 10000;
  if (sec >= 60) { sec -= 60; min += 1; }
  if (min >= 60) { min -= 60; deg += 1; }
  return { deg, min, sec, hemi };
}

export type PointRow = { col: number; row: number; x: number; y: number };

/** Control points from a file: ArcGIS's text (source x, source y, map x, map y, tab or space separated; the image's
 *  y runs negative downwards) or a CSV with a header naming col/row (or source_x/source_y) and x/y, easting/northing or
 *  lng/lat. Map coordinates are in the file's system, which the caller knows. */
export function parsePoints(text: string): PointRow[] {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
  if (!lines.length) throw new Error("The file has no control points.");
  const split = (l: string) => l.split(/[\t,;]|\s+/).map((x) => x.trim()).filter((x) => x !== "");
  const first = split(lines[0]);
  const isHeader = first.some((x) => Number.isNaN(Number(x)));
  let pick: (c: string[]) => [number, number, number, number];
  if (isHeader) {
    const h = first.map((x) => x.toLowerCase().replace(/[^a-z_]/g, ""));
    const find = (...names: string[]) => { const i = h.findIndex((x) => names.includes(x)); if (i < 0) throw new Error(`The header needs a column called ${names[0]}.`); return i; };
    const c = find("col", "column", "source_x", "sourcex", "image_x", "pixel_x", "image_col");
    const r = find("row", "source_y", "sourcey", "image_y", "pixel_y", "image_row");
    const x = find("x", "easting", "e", "lng", "lon", "longitude", "map_x", "target_x");
    const y = find("y", "northing", "n", "lat", "latitude", "map_y", "target_y");
    pick = (v) => [Number(v[c]), Number(v[r]), Number(v[x]), Number(v[y])];
  } else pick = (v) => [Number(v[0]), Number(v[1]), Number(v[2]), Number(v[3])];
  const rows = (isHeader ? lines.slice(1) : lines).map((l, i) => {
    const v = pick(split(l));
    if (v.some((n) => !Number.isFinite(n))) throw new Error(`Line ${i + (isHeader ? 2 : 1)} does not have four numbers.`);
    return v;
  });
  // ArcGIS writes an unplaced image's rows as negative y; keep rows counted downwards
  const negative = rows.every((v) => v[1] <= 0);
  return rows.map(([col, row, x, y]) => ({ col, row: negative ? -row : row, x, y }));
}

/** Control points in ArcGIS's text format: source x, source y (negative downwards), map x, map y, tab separated. */
export function pointsText(rows: PointRow[]): string {
  return rows.map((p) => [p.col, -p.row, p.x, p.y].map((v) => String(Math.round(v * 1e8) / 1e8)).join("\t")).join("\n") + "\n";
}
