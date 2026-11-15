/** Military coordinate notations on WGS 84, worked out in the browser (the same rules as the server's milcoords.py):
 *  DD, DDM, DMS, UTM, MGRS, USNG, GARS and GEOREF, written from a longitude/latitude and read back from text. */
import proj4 from "proj4";

export type Notation = "dd" | "ddm" | "dms" | "utm" | "mgrs" | "usng" | "gars" | "georef";
export const NOTATIONS: Notation[] = ["dd", "ddm", "dms", "utm", "mgrs", "usng", "gars", "georef"];
export const NOTATION_NAMES: Record<Notation, string> = {
  dd: "Decimal degrees", ddm: "Degrees decimal minutes", dms: "Degrees minutes seconds", utm: "UTM",
  mgrs: "MGRS", usng: "USNG", gars: "GARS", georef: "GEOREF",
};

const BANDS = "CDEFGHJKLMNPQRSTUVWX";
const COLS = ["ABCDEFGH", "JKLMNPQR", "STUVWXYZ"];
const ROWS = "ABCDEFGHJKLMNPQRSTUV";
const GARS_LAT = "ABCDEFGHJKLMNPQRSTUVWXYZ";
const GEOREF_LON = "ABCDEFGHJKLMNPQRSTUVWXYZ";
const GEOREF_LAT = "ABCDEFGHJKLM";
const GEOREF_DEG = "ABCDEFGHJKLMNPQ";

export class CoordError extends Error {}

const utmDef = (zone: number, south: boolean) => `+proj=utm +zone=${zone}${south ? " +south" : ""} +datum=WGS84 +units=m +no_defs`;
const fwd = (zone: number, south: boolean, lng: number, lat: number) => proj4("EPSG:4326", utmDef(zone, south), [lng, lat]) as [number, number];
const inv = (zone: number, south: boolean, e: number, n: number) => proj4(utmDef(zone, south), "EPSG:4326", [e, n]) as [number, number];

export function utmZone(lng: number, lat: number): number {
  lng = ((((lng + 180) % 360) + 360) % 360) - 180;
  const z = Math.min(60, Math.floor((lng + 180) / 6) + 1);
  if (lat >= 56 && lat < 64 && lng >= 3 && lng < 12) return 32;
  if (lat >= 72 && lat <= 84 && lng >= 0) {
    if (lng < 9) return 31;
    if (lng < 21) return 33;
    if (lng < 33) return 35;
    if (lng < 42) return 37;
  }
  return z;
}

function bandIndex(lat: number): number {
  if (!(lat >= -80 && lat <= 84)) throw new CoordError("MGRS and UTM cover 80° S to 84° N only.");
  return Math.min(19, Math.floor((lat + 80) / 8));
}

export function toUtm(lng: number, lat: number): string {
  bandIndex(lat);
  const zone = utmZone(lng, lat), south = lat < 0;
  const [e, n] = fwd(zone, south, lng, lat);
  return `${zone}${south ? "S" : "N"} ${Math.round(e)} ${Math.round(n)}`;
}

export function toMgrs(lng: number, lat: number, digits = 5, spaced = false): string {
  const bi = bandIndex(lat);
  const zone = utmZone(lng, lat);
  const [e, n] = fwd(zone, lat < 0, lng, lat);
  const col = COLS[(zone - 1) % 3][Math.floor(e / 100000) - 1];
  const row = ROWS[(Math.floor(n / 100000) + (zone % 2 === 0 ? 5 : 0)) % 20];
  const div = 10 ** (5 - digits);
  const pad = (v: number) => String(Math.floor((Math.floor(v) % 100000) / div)).padStart(digits, "0");
  const ee = digits ? pad(e) : "", nn = digits ? pad(n) : "";
  const gzd = `${zone}${BANDS[bi]}`;
  return spaced ? [gzd, col + row, ee, nn].filter(Boolean).join(" ") : `${gzd}${col}${row}${ee}${nn}`;
}

const MGRS_RE = /^\s*(\d{1,2})\s*([C-HJ-NP-X])\s*([A-HJ-NP-Z])\s*([A-HJ-NP-V])\s*(\d*)\s*(\d*)\s*$/i;

export function fromMgrs(text: string): [number, number] {
  const m = MGRS_RE.exec(text);
  if (!m) throw new CoordError("Not an MGRS reference (like 32P LR 31452 03665).");
  const zone = Number(m[1]), band = m[2].toUpperCase(), col = m[3].toUpperCase(), row = m[4].toUpperCase();
  const digits = m[5] + m[6];
  if (zone < 1 || zone > 60) throw new CoordError("An MGRS zone is 1 to 60.");
  if (digits.length % 2 || digits.length > 10) throw new CoordError("An MGRS reference has as many easting digits as northing digits (up to 5 each).");
  const k = digits.length / 2;
  const cols = COLS[(zone - 1) % 3];
  if (!cols.includes(col)) throw new CoordError(`${col} is not a 100 km column letter in zone ${zone}.`);
  const size = 10 ** (5 - k);
  const e = (cols.indexOf(col) + 1) * 100000 + (k ? Number(digits.slice(0, k)) * size : 0);
  const r = (((ROWS.indexOf(row) - (zone % 2 === 0 ? 5 : 0)) % 20) + 20) % 20;
  let n = r * 100000 + (k ? Number(digits.slice(k)) * size : 0);
  const bi = BANDS.indexOf(band), south = bi < 10, bottom = -80 + bi * 8, cm = zone * 6 - 183;
  const lo = Math.min(fwd(zone, south, cm, bottom)[1], fwd(zone, south, cm + 3, bottom)[1], fwd(zone, south, cm - 3, bottom)[1]);
  while (n < lo - 100000) n += 2000000;
  const [lng, lat] = inv(zone, south, e + size / 2, n + size / 2);
  const top = bottom + (bi === 19 ? 12 : 8);
  if (lat < bottom - 0.5 || lat > top + 0.5) throw new CoordError(`${text.trim()} does not fall in latitude band ${band}.`);
  return [lng, lat];
}

const hemi = (v: number, p: string, q: string) => (v >= 0 ? p : q);

export function toDd(lng: number, lat: number, places = 6): string {
  return `${Math.abs(lat).toFixed(places)}° ${hemi(lat, "N", "S")} ${Math.abs(lng).toFixed(places)}° ${hemi(lng, "E", "W")}`;
}

function ddm(v: number, width: number, places: number): string {
  const a = Math.abs(v);
  let d = Math.floor(a);
  let m = Number(((a - d) * 60).toFixed(places));
  if (m >= 60) { d += 1; m = 0; }
  return `${String(d).padStart(width, "0")}° ${m.toFixed(places).padStart(places + 3, "0")}'`;
}

export function toDdm(lng: number, lat: number, places = 3): string {
  return `${ddm(lat, 2, places)} ${hemi(lat, "N", "S")} ${ddm(lng, 3, places)} ${hemi(lng, "E", "W")}`;
}

function dms(v: number, width: number, places: number): string {
  const a = Math.abs(v);
  let d = Math.floor(a);
  let m = Math.floor((a - d) * 60);
  let s = Number((((a - d) * 60 - m) * 60).toFixed(places));
  if (s >= 60) { s = 0; m += 1; }
  if (m >= 60) { m = 0; d += 1; }
  return `${String(d).padStart(width, "0")}° ${String(m).padStart(2, "0")}' ${s.toFixed(places).padStart(places ? places + 3 : 2, "0")}"`;
}

export function toDms(lng: number, lat: number, places = 1): string {
  return `${dms(lat, 2, places)} ${hemi(lat, "N", "S")} ${dms(lng, 3, places)} ${hemi(lng, "E", "W")}`;
}

export function toGars(lng: number, lat: number, level: 30 | 15 | 5 = 5): string {
  if (!(lat >= -90 && lat <= 90)) throw new CoordError("Latitude is −90 to 90.");
  lng = ((((lng + 180) % 360) + 360) % 360) - 180;
  const x = Math.min(Math.floor((lng + 180) * 12), 4319), y = Math.min(Math.floor((lat + 90) * 12), 2159);
  const cx = Math.floor(x / 6), cy = Math.floor(y / 6);
  let out = `${String(cx + 1).padStart(3, "0")}${GARS_LAT[Math.floor(cy / 24)]}${GARS_LAT[cy % 24]}`;
  if (level >= 30) return out;
  const qx = Math.floor((x % 6) / 3), qy = Math.floor((y % 6) / 3);
  out += String(qy ? (qx ? 2 : 1) : (qx ? 4 : 3));
  if (level >= 15) return out;
  return out + String((2 - (y % 3)) * 3 + (x % 3) + 1);
}

const GARS_RE = /^\s*(\d{3})\s*([A-HJ-NP-Z])([A-HJ-NP-Z])\s*([1-4])?\s*([1-9])?\s*$/i;

export function fromGars(text: string): [number, number] {
  const m = GARS_RE.exec(text);
  if (!m || Number(m[1]) < 1 || Number(m[1]) > 720) throw new CoordError("Not a GARS cell (like 375LS28).");
  const cy = GARS_LAT.indexOf(m[2].toUpperCase()) * 24 + GARS_LAT.indexOf(m[3].toUpperCase());
  if (cy >= 360) throw new CoordError("Those GARS letters are past the North Pole.");
  let w = -180 + (Number(m[1]) - 1) * 0.5, s = -90 + cy * 0.5, size = 0.5;
  if (m[4]) {
    const q = Number(m[4]);
    if (q === 2 || q === 4) w += 0.25;
    if (q === 1 || q === 2) s += 0.25;
    size = 0.25;
    if (m[5]) {
      const k = Number(m[5]) - 1;
      w += (k % 3) * size / 3;
      s += (2 - Math.floor(k / 3)) * size / 3;
      size /= 3;
    }
  }
  return [w + size / 2, s + size / 2];
}

export function toGeoref(lng: number, lat: number, digits = 2): string {
  if (!(lat >= -90 && lat <= 90)) throw new CoordError("Latitude is −90 to 90.");
  lng = ((((lng + 180) % 360) + 360) % 360) - 180;
  const x = Math.min(lng + 180, 359.999999999), y = Math.min(lat + 90, 179.999999999);
  let out = GEOREF_LON[Math.floor(x / 15)] + GEOREF_LAT[Math.floor(y / 15)] + GEOREF_DEG[Math.floor(x % 15)] + GEOREF_DEG[Math.floor(y % 15)];
  if (digits) {
    const f = 10 ** (digits - 2);
    out += String(Math.floor((x % 1) * 60 * f)).padStart(digits, "0") + String(Math.floor((y % 1) * 60 * f)).padStart(digits, "0");
  }
  return out;
}

const GEOREF_RE = /^\s*([A-HJ-NP-Z])([A-HJ-M])([A-HJ-NP-Q])([A-HJ-NP-Q])\s*(\d*)\s*$/i;

export function fromGeoref(text: string): [number, number] {
  const m = GEOREF_RE.exec(text);
  if (!m || m[5].length % 2 || m[5].length > 10) throw new CoordError("Not a GEOREF reference (like MKML5004).");
  const w = -180 + GEOREF_LON.indexOf(m[1].toUpperCase()) * 15 + GEOREF_DEG.indexOf(m[3].toUpperCase());
  const s = -90 + GEOREF_LAT.indexOf(m[2].toUpperCase()) * 15 + GEOREF_DEG.indexOf(m[4].toUpperCase());
  const d = m[5];
  if (!d) return [w + 0.5, s + 0.5];
  const k = d.length / 2, unit = 10 ** -(k - 2) / 60;
  const mx = Number(d.slice(0, k)) * unit, my = Number(d.slice(k)) * unit;
  if (mx >= 1 || my >= 1) throw new CoordError("GEOREF minutes go up to 59.");
  return [w + mx + unit / 2, s + my + unit / 2];
}

// ---------- reading degrees ----------
function angle(part: string): [number, string | null] {
  const p = part.trim();
  let m = /^([NSEW])?\s*([-+]?\d+(?:\.\d+)?)\s*°?\s*([NSEW])?$/i.exec(p);
  let v: number, h: string | null;
  if (m) {
    v = Number(m[2]); h = (m[1] || m[3] || "").toUpperCase() || null;
  } else {
    m = /^([NSEW])?\s*([-+]?\d+(?:\.\d+)?)\s*(?:[°º:d]|\s|deg)\s*(?:(\d+(?:\.\d+)?)\s*(?:['′’:m]|\s)\s*)?(?:(\d+(?:\.\d+)?)\s*(?:["″”]|''|s)?\s*)?([NSEW])?$/i.exec(p);
    if (!m) throw new CoordError(`Can't read "${p}" as degrees.`);
    const d = Number(m[2]), mi = Number(m[3] || 0), s = Number(m[4] || 0);
    if (mi >= 60 || s >= 60) throw new CoordError("Minutes and seconds go up to 59.");
    v = Math.abs(d) + mi / 60 + s / 3600;
    if (m[2].startsWith("-")) v = -v;
    h = (m[1] || m[5] || "").toUpperCase() || null;
  }
  if (h === "S" || h === "W") v = -Math.abs(v);
  return [v, h];
}

function splitDegrees(text: string): [string, string] {
  const t = text.trim().replace(/″/g, '"').replace(/[′’]/g, "'").replace(/”/g, '"');
  for (const sep of [";", ","]) {
    const parts = t.split(sep);
    if (parts.length === 2 && parts.every((x) => x.trim())) return [parts[0], parts[1]];
  }
  let m = /^([NSEW]\s*[^NSEW]+?)\s*[,;]?\s*([NSEW]\s*[^NSEW]+)$/i.exec(t);
  if (m) return [m[1], m[2]];
  m = /^([^NSEW]+?[NS])\s*[,;]?\s*([^NSEW]+[EW])$/i.exec(t) || /^([^NSEW]+?[EW])\s*[,;]?\s*([^NSEW]+[NS])$/i.exec(t);
  if (m) return [m[1], m[2]];
  const nums = t.replace(/;/g, " ").split(/\s+/);
  if (nums.length === 2) return [nums[0], nums[1]];
  throw new CoordError("Write a latitude and a longitude (e.g. 9.0765, 7.4664 or 9°04'35\"N 7°27'59\"E).");
}

export function fromDegrees(text: string, order: "latlon" | "lonlat" = "latlon"): [number, number] {
  const [a, b] = splitDegrees(text);
  const [v1, h1] = angle(a), [v2, h2] = angle(b);
  let lng: number, lat: number;
  if (h1 === "E" || h1 === "W" || h2 === "N" || h2 === "S") [lng, lat] = [v1, v2];
  else if (h1 === "N" || h1 === "S" || h2 === "E" || h2 === "W") [lat, lng] = [v1, v2];
  else if (order === "lonlat" || (Math.abs(v1) > 90 && Math.abs(v2) <= 90)) [lng, lat] = [v1, v2];
  else [lat, lng] = [v1, v2];
  if (!(lat >= -90 && lat <= 90) || !(lng >= -180 && lng <= 180)) throw new CoordError("Latitude is −90 to 90 and longitude −180 to 180.");
  return [lng, lat];
}

const UTM_RE = /^\s*(\d{1,2})\s*([NS]|[C-HJ-NP-X])?\s*[, ]\s*(\d+(?:\.\d+)?)\s*m?\s*[EN]?\s*[, ]\s*(\d+(?:\.\d+)?)\s*m?\s*[EN]?\s*$/i;

export function fromUtm(text: string): [number, number] {
  const m = UTM_RE.exec(text);
  if (!m) throw new CoordError("Not a UTM coordinate (like 32N 331529 1003546).");
  const zone = Number(m[1]), h = (m[2] || "N").toUpperCase();
  const south = h === "N" || h === "S" ? h === "S" : BANDS.indexOf(h) < 10;
  const e = Number(m[3]), n = Number(m[4]);
  if (zone < 1 || zone > 60) throw new CoordError("A UTM zone is 1 to 60.");
  if (e < 100000 || e > 900000 || n < 0 || n > 10000000) throw new CoordError("That easting or northing is outside a UTM zone.");
  return inv(zone, south, e, n);
}

export function detect(text: string): Notation {
  const t = text.trim();
  if (MGRS_RE.test(t) && /^\d{1,2}\s*[A-Z]\s*[A-Z]{2}/i.test(t)) return /\s/.test(t) ? "usng" : "mgrs";
  const u = UTM_RE.exec(t);
  if (u && Number(u[3]) >= 100000) return "utm";
  if (GARS_RE.test(t)) return "gars";
  if (GEOREF_RE.test(t)) return "georef";
  if (/['′’"″]|\d\s*[°º]\s*\d/.test(t)) return /["″]|''/.test(t) ? "dms" : "ddm";
  return "dd";
}

/** [longitude, latitude, the notation it was in]. */
export function parseCoordinate(text: string, notation: Notation | "auto" = "auto", order: "latlon" | "lonlat" = "latlon"): [number, number, Notation] {
  if (!text.trim()) throw new CoordError("No coordinate given.");
  const n = notation === "auto" ? detect(text) : notation;
  let r: [number, number];
  if (n === "mgrs" || n === "usng") r = fromMgrs(text);
  else if (n === "utm") r = fromUtm(text);
  else if (n === "gars") r = fromGars(text);
  else if (n === "georef") r = fromGeoref(text);
  else r = fromDegrees(text, order);
  return [r[0], r[1], n];
}

/** The position in a notation, or null where it does not reach (MGRS and UTM near the poles). */
export function writeCoordinate(n: Notation, lng: number, lat: number): string | null {
  try {
    switch (n) {
      case "dd": return toDd(lng, lat);
      case "ddm": return toDdm(lng, lat);
      case "dms": return toDms(lng, lat);
      case "utm": return toUtm(lng, lat);
      case "mgrs": return toMgrs(lng, lat);
      case "usng": return toMgrs(lng, lat, 5, true);
      case "gars": return toGars(lng, lat);
      case "georef": return toGeoref(lng, lat);
    }
  } catch { return null; }
}
