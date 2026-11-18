/** Project files (.polygis): save a project to the person's computer and open it again. Where the browser supports
 *  the File System Access API (Chrome, Edge) this uses the native "where do you want to save?" dialog and can save
 *  back to the same file; elsewhere it falls back to a normal download / file-picker upload. */
import { BASE, api, projectFileUrl } from "./api";

type FileHandle = { getFile: () => Promise<File>; createWritable: () => Promise<{ write: (d: Blob) => Promise<void>; close: () => Promise<void> }> };
const w = window as unknown as {
  showSaveFilePicker?: (o: unknown) => Promise<FileHandle>;
  showOpenFilePicker?: (o: unknown) => Promise<FileHandle[]>;
};
export const supportsFsAccess = typeof w.showSaveFilePicker === "function";
const PICKER = { types: [{ description: "PolyGIS project", accept: { "application/x-polygis": [".polygis"] } }] };

// Keep a project's file handle so "Save" can write back to the same file, per browser.
const DB = "polygis-files";
function idb(): Promise<IDBDatabase> {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore("handles");
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
}
async function putHandle(id: string, h: FileHandle) {
  try { const db = await idb(); db.transaction("handles", "readwrite").objectStore("handles").put(h, id); } catch { /* ignore */ }
}
export async function getHandle(id: string): Promise<FileHandle | null> {
  try {
    const db = await idb();
    return await new Promise((res) => { const q = db.transaction("handles", "readonly").objectStore("handles").get(id); q.onsuccess = () => res(q.result ?? null); q.onerror = () => res(null); });
  } catch { return null; }
}

async function fetchProjectBlob(projectId: string): Promise<Blob> {
  const res = await fetch(projectFileUrl(projectId), { credentials: "same-origin", headers: { "X-GeoEstate": "1" } });
  if (!res.ok) throw new Error("Could not build the project file.");
  return res.blob();
}

/** Save the project to the computer. With a handle (stored or just picked) it writes back to that file; otherwise it
 *  opens the native save dialog (returning the handle) or, on unsupported browsers, downloads the file. */
export async function saveProjectFile(projectId: string, name: string, handle?: FileHandle | null): Promise<FileHandle | null> {
  const blob = await fetchProjectBlob(projectId);
  if (supportsFsAccess) {
    let h = handle ?? null;
    if (!h) h = await w.showSaveFilePicker!({ suggestedName: `${name}.polygis`, ...PICKER });
    const ws = await h.createWritable(); await ws.write(blob); await ws.close();
    await putHandle(projectId, h);
    return h;
  }
  download(blob, `${name}.polygis`);
  return null;
}

/** Pick a save location up front (within a user gesture), before the project exists — used on "create & save". */
export async function pickSaveLocation(name: string): Promise<FileHandle | null> {
  if (!supportsFsAccess) return null;
  return w.showSaveFilePicker!({ suggestedName: `${name}.polygis`, ...PICKER });
}
export async function writeToHandle(projectId: string, handle: FileHandle) {
  const blob = await fetchProjectBlob(projectId);
  const ws = await handle.createWritable(); await ws.write(blob); await ws.close();
  await putHandle(projectId, handle);
}

/** Open a .polygis file and import it as a new project. Returns the imported project summary. */
export async function openProjectFile(): Promise<{ id: string; name: string; layers: number; rasters_skipped: number } | null> {
  let file: File | null = null;
  let handle: FileHandle | null = null;
  if (supportsFsAccess && w.showOpenFilePicker) {
    const picked = await w.showOpenFilePicker(PICKER).catch((e: DOMException) => { if (e?.name === "AbortError") return null; throw e; });
    if (!picked) return null;
    handle = picked[0]; file = await handle.getFile();
  } else {
    file = await pickFileFallback();
    if (!file) return null;
  }
  const out = await api.importProjectFile(file);
  if (handle) await putHandle(out.id, handle);
  return out;
}

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = filename; document.body.appendChild(a); a.click();
  a.remove(); setTimeout(() => URL.revokeObjectURL(url), 4000);
}
function pickFileFallback(): Promise<File | null> {
  return new Promise((res) => {
    const input = document.createElement("input"); input.type = "file"; input.accept = ".polygis,application/zip,application/x-polygis";
    input.onchange = () => res(input.files?.[0] ?? null);
    input.oncancel = () => res(null);
    input.click();
  });
}
export { BASE };
