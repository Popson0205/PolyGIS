/** Folder connections (ArcGIS/QGIS-style). Connect a folder on the computer once; PolyGIS browses the geodata in it
 *  and adds a dataset to the map by reading it straight from the folder. For a Shapefile you pick only the .shp — the
 *  companion files (.shx, .dbf, .prj, .cpg…) are gathered automatically from the same folder. Uses the File System
 *  Access API (Chrome, Edge and the PolyGIS desktop app); not available in Firefox/Safari. */

interface FileEntry { kind: "file"; name: string; getFile(): Promise<File>; }
interface DirEntry {
  kind: "directory"; name: string;
  values(): AsyncIterable<FileEntry | DirEntry>;
  getFileHandle(name: string): Promise<FileEntry>;
  queryPermission?(o: { mode: string }): Promise<PermissionState>;
  requestPermission?(o: { mode: string }): Promise<PermissionState>;
}
const w = window as unknown as { showDirectoryPicker?: (o?: unknown) => Promise<DirEntry> };
export const supportsFolders = typeof w.showDirectoryPicker === "function";

export type FolderConn = { id: string; name: string; handle: DirEntry };
export type Dataset = { id: string; label: string; kind: "shapefile" | "mapinfo" | "file"; primary: string; parts: string[]; ext: string };

// Shapefile companions we attach when the user picks a .shp (whitelist, so an unrelated same-named file isn't pulled in).
const SHP_PARTS = ["shp", "shx", "dbf", "prj", "cpg", "qpj", "sbn", "sbx", "qix", "fix", "ain", "aih", "atx", "ixs", "mxs", "shp.xml"];
const MAPINFO_PARTS: Record<string, string[]> = { tab: ["tab", "dat", "id", "map", "ind"], mif: ["mif", "mid"] };
// Single-file datasets PolyGIS can read (mirrors the importer); companion-only extensions are never listed on their own.
const SINGLE = new Set(["geojson", "json", "geojsonl", "geojsons", "ndjson", "topojson", "gpkg", "kml", "kmz", "csv", "txt",
  "gpx", "gml", "dxf", "dgn", "fgb", "zip", "sqlite", "osm", "pbf", "vrt", "wkt",
  "tif", "tiff", "img", "asc", "grd", "jp2", "ecw", "sid", "nc", "hdf", "grib", "grb", "grb2", "dt2", "dt1", "hgt", "mbtiles", "sdat", "rst", "dem"]);

const lower = (s: string) => s.toLowerCase();
const baseOf = (n: string) => n.slice(0, n.lastIndexOf("."));
const extOf = (n: string) => (lower(n).endsWith(".shp.xml") ? "shp.xml" : lower(n).slice(lower(n).lastIndexOf(".") + 1));

// ---- persistence (handles survive across sessions in IndexedDB; permission is re-granted on use) ----
const DB = "polygis-folders";
function idb(): Promise<IDBDatabase> {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore("dirs");
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
}
async function allFolders(): Promise<FolderConn[]> {
  try {
    const db = await idb();
    return await new Promise((res) => {
      const out: FolderConn[] = [];
      const store = db.transaction("dirs", "readonly").objectStore("dirs");
      const cur = store.openCursor();
      cur.onsuccess = () => { const c = cur.result; if (c) { out.push({ id: String(c.key), ...(c.value as { name: string; handle: DirEntry }) }); c.continue(); } else res(out); };
      cur.onerror = () => res(out);
    });
  } catch { return []; }
}
async function putFolder(c: FolderConn) {
  const db = await idb();
  db.transaction("dirs", "readwrite").objectStore("dirs").put({ name: c.name, handle: c.handle }, c.id);
}
export async function listFolders(): Promise<FolderConn[]> { return allFolders(); }
export async function removeFolder(id: string) {
  try { const db = await idb(); db.transaction("dirs", "readwrite").objectStore("dirs").delete(id); } catch { /* ignore */ }
}
export async function connectFolder(): Promise<FolderConn | null> {
  if (!supportsFolders) return null;
  let handle: DirEntry;
  try { handle = await w.showDirectoryPicker!({ id: "polygis-data", mode: "read" }); }
  catch (e) { if ((e as DOMException)?.name === "AbortError") return null; throw e; }
  const conn: FolderConn = { id: crypto.randomUUID(), name: handle.name, handle };
  await putFolder(conn);
  return conn;
}

export async function ensurePermission(handle: DirEntry): Promise<boolean> {
  if (!handle.queryPermission) return true;
  if ((await handle.queryPermission({ mode: "read" })) === "granted") return true;
  return handle.requestPermission ? (await handle.requestPermission({ mode: "read" })) === "granted" : false;
}

/** Top-level datasets in the folder: each Shapefile (its .shp plus companions), MapInfo tables, and single-file data. */
export async function scanFolder(handle: DirEntry): Promise<Dataset[]> {
  const names: string[] = [];
  for await (const entry of handle.values()) if (entry.kind === "file") names.push(entry.name);
  const byBase = new Map<string, string[]>();          // lowercased base → original file names
  for (const n of names) { const b = lower(baseOf(n)); (byBase.get(b) ?? byBase.set(b, []).get(b)!).push(n); }

  const out: Dataset[] = [];
  const claimed = new Set<string>();                    // companion files already folded into a dataset
  for (const n of names) {
    const ext = extOf(n);
    if (ext === "shp") {
      const sibs = byBase.get(lower(baseOf(n))) ?? [n];
      const parts = sibs.filter((s) => SHP_PARTS.includes(extOf(s)));
      parts.forEach((p) => claimed.add(p));
      out.push({ id: n, label: baseOf(n), kind: "shapefile", primary: n, parts, ext: "shp" });
    } else if (ext === "tab" || ext === "mif") {
      const want = MAPINFO_PARTS[ext];
      const sibs = (byBase.get(lower(baseOf(n))) ?? [n]).filter((s) => want.includes(extOf(s)));
      sibs.forEach((p) => claimed.add(p));
      out.push({ id: n, label: baseOf(n), kind: "mapinfo", primary: n, parts: sibs, ext });
    }
  }
  for (const n of names) {
    const ext = extOf(n);
    if (!claimed.has(n) && SINGLE.has(ext)) out.push({ id: n, label: n, kind: "file", primary: n, parts: [n], ext });
  }
  return out.sort((a, b) => a.label.localeCompare(b.label));
}

/** Read a dataset's files from the folder (the primary first), ready to hand to the importer. */
export async function readDataset(handle: DirEntry, ds: Dataset): Promise<File[]> {
  const ordered = [ds.primary, ...ds.parts.filter((p) => p !== ds.primary)];
  const files: File[] = [];
  for (const name of ordered) {
    try { files.push(await (await handle.getFileHandle(name)).getFile()); } catch { /* a listed companion vanished; skip it */ }
  }
  return files;
}
