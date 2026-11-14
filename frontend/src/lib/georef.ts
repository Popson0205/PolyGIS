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
