/** Distances and bearings on the WGS 84 ellipsoid (Vincenty's inverse formula, to well under a millimetre), and the
 *  angle units soldiers use: NATO mils (6400 to the circle), and grid bearings in the UTM zone. */
import { utmZone } from "./milcoords";

const A = 6378137, F = 1 / 298.257223563, B = A * (1 - F);
const rad = (d: number) => (d * Math.PI) / 180, deg = (r: number) => (r * 180) / Math.PI;
export const norm360 = (d: number) => ((d % 360) + 360) % 360;
export const toMils = (d: number) => norm360(d) * (6400 / 360);

/** Distance in metres and the bearings (true north, degrees) out from the first point and back from the second. */
export function inverse(lng1: number, lat1: number, lng2: number, lat2: number): { distance: number; bearing: number; back: number } {
  const L = rad(lng2 - lng1);
  const U1 = Math.atan((1 - F) * Math.tan(rad(lat1))), U2 = Math.atan((1 - F) * Math.tan(rad(lat2)));
  const sinU1 = Math.sin(U1), cosU1 = Math.cos(U1), sinU2 = Math.sin(U2), cosU2 = Math.cos(U2);
  let lambda = L, prev = 0, iter = 0;
  let sinSigma = 0, cosSigma = 0, sigma = 0, cos2Alpha = 0, cos2SigmaM = 0, sinLambda = 0, cosLambda = 0;
  do {
    sinLambda = Math.sin(lambda); cosLambda = Math.cos(lambda);
    sinSigma = Math.hypot(cosU2 * sinLambda, cosU1 * sinU2 - sinU1 * cosU2 * cosLambda);
    if (sinSigma === 0) return { distance: 0, bearing: 0, back: 0 };
    cosSigma = sinU1 * sinU2 + cosU1 * cosU2 * cosLambda;
    sigma = Math.atan2(sinSigma, cosSigma);
    const sinAlpha = (cosU1 * cosU2 * sinLambda) / sinSigma;
    cos2Alpha = 1 - sinAlpha * sinAlpha;
    cos2SigmaM = cos2Alpha ? cosSigma - (2 * sinU1 * sinU2) / cos2Alpha : 0;
    const C = (F / 16) * cos2Alpha * (4 + F * (4 - 3 * cos2Alpha));
    prev = lambda;
    lambda = L + (1 - C) * F * sinAlpha * (sigma + C * sinSigma * (cos2SigmaM + C * cosSigma * (-1 + 2 * cos2SigmaM * cos2SigmaM)));
  } while (Math.abs(lambda - prev) > 1e-12 && ++iter < 200);
  const u2 = (cos2Alpha * (A * A - B * B)) / (B * B);
  const Ak = 1 + (u2 / 16384) * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)));
  const Bk = (u2 / 1024) * (256 + u2 * (-128 + u2 * (74 - 47 * u2)));
  const dSigma = Bk * sinSigma * (cos2SigmaM + (Bk / 4) * (cosSigma * (-1 + 2 * cos2SigmaM * cos2SigmaM)
    - (Bk / 6) * cos2SigmaM * (-3 + 4 * sinSigma * sinSigma) * (-3 + 4 * cos2SigmaM * cos2SigmaM)));
  const distance = B * Ak * (sigma - dSigma);
  const a1 = Math.atan2(cosU2 * sinLambda, cosU1 * sinU2 - sinU1 * cosU2 * cosLambda);
  const a2 = Math.atan2(cosU1 * sinLambda, -sinU1 * cosU2 + cosU1 * sinU2 * cosLambda);
  return { distance, bearing: norm360(deg(a1)), back: norm360(deg(a2) + 180) };
}

/** Grid convergence at a point (degrees): how far grid north (the UTM zone's) is turned from true north. */
export function convergence(lng: number, lat: number): number {
  const cm = utmZone(lng, lat) * 6 - 183;
  return deg(Math.atan(Math.tan(rad(lng - cm)) * Math.sin(rad(lat))));
}

/** "1,234.5 m" or "12.346 km (6.666 nmi)". */
export function distanceText(m: number): string {
  if (m < 1000) return `${m.toLocaleString("en-GB", { maximumFractionDigits: 1 })} m`;
  return `${(m / 1000).toLocaleString("en-GB", { maximumFractionDigits: 3 })} km (${(m / 1852).toLocaleString("en-GB", { maximumFractionDigits: 3 })} nmi)`;
}

/** The read-out of the Bearing tool for a line clicked on the map: from the first point to the last. */
export function bearingText(points: [number, number][]): string {
  if (points.length < 2) return "";
  const [x1, y1] = points[0], [x2, y2] = points[points.length - 1];
  const r = inverse(x1, y1, x2, y2);
  const grid = norm360(r.bearing - convergence(x1, y1));
  const along = points.slice(1).reduce((s, p, i) => s + inverse(points[i][0], points[i][1], p[0], p[1]).distance, 0);
  const parts = [`${distanceText(r.distance)}`, `bearing ${r.bearing.toFixed(2)}° (${Math.round(toMils(r.bearing))} mils)`,
    `grid ${grid.toFixed(2)}°`, `back ${r.back.toFixed(2)}°`];
  if (points.length > 2) parts.push(`along the line ${distanceText(along)}`);
  return parts.join(" · ");
}
