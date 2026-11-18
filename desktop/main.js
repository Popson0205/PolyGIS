// PolyGIS desktop — a thin native shell around the hosted PolyGIS web app. It loads the live site (so sign-in and the
// whole backend work exactly as in a browser), adds a real app window, a menu, and a Windows file association so a
// .polygis project opens in the app. No app data lives here; this is the "software" wrapper, step one off the web.
const { app, BrowserWindow, Menu, shell, ipcMain, dialog } = require("electron");
const fs = require("fs");
const path = require("path");

// Where the hosted app lives. Override order: POLYGIS_URL env → <userData>/config.json {"url": "..."} → default.
const DEFAULT_URL = "https://polygis-production-636a.up.railway.app";
function appUrl() {
  if (process.env.POLYGIS_URL) return process.env.POLYGIS_URL.replace(/\/$/, "");
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(app.getPath("userData"), "config.json"), "utf8"));
    if (cfg && typeof cfg.url === "string" && cfg.url) return cfg.url.replace(/\/$/, "");
  } catch { /* no config yet */ }
  return DEFAULT_URL;
}
const APP_URL = appUrl();
const APP_ORIGIN = (() => { try { return new URL(APP_URL).origin; } catch { return null; } })();

let win = null;
let pendingFile = null;   // a .polygis chosen before the window/renderer was ready

/** A .polygis path from the command line (Windows/Linux file association), if any. */
function fileFromArgv(argv) {
  return (argv || []).slice(1).find((a) => a && a.toLowerCase().endsWith(".polygis") && fs.existsSync(a)) || null;
}

/** Read a .polygis file and hand it to the renderer (the hosted app picks it up via the preload bridge). */
function openProjectFile(filePath) {
  if (!filePath) return;
  try {
    const data = fs.readFileSync(filePath);
    const payload = { name: path.basename(filePath), base64: data.toString("base64") };
    if (win && !win.webContents.isLoading()) win.webContents.send("polygis:open-project", payload);
    else pendingFile = payload;   // deliver once the page has loaded
  } catch (e) {
    dialog.showErrorBox("PolyGIS", `Could not read the project file:\n${e.message}`);
  }
}

function createWindow() {
  win = new BrowserWindow({
    width: 1360, height: 900, minWidth: 900, minHeight: 600,
    backgroundColor: "#0a1830", show: false, autoHideMenuBar: false,
    title: "PolyGIS",
    icon: path.join(__dirname, "assets", process.platform === "win32" ? "icon.ico" : "icon.png"),
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, spellcheck: false },
  });

  win.once("ready-to-show", () => win.show());
  win.loadURL(APP_URL);

  // When the hosted page has loaded, flush any file that was waiting.
  win.webContents.on("did-finish-load", () => {
    if (pendingFile) { win.webContents.send("polygis:open-project", pendingFile); pendingFile = null; }
  });

  // Keep navigation inside the app; open everything else (docs, external links) in the real browser.
  const external = (url) => { if (APP_ORIGIN && new URL(url).origin !== APP_ORIGIN) { shell.openExternal(url); return true; } return false; };
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  win.webContents.on("will-navigate", (e, url) => { if (external(url)) e.preventDefault(); });

  win.webContents.on("render-process-gone", () => win && win.reload());
  win.on("closed", () => { win = null; });
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac ? [{ role: "appMenu" }] : []),
    {
      label: "File",
      submenu: [
        { label: "Open project file…", accelerator: "CmdOrCtrl+O", click: async () => {
            const r = await dialog.showOpenDialog(win, { title: "Open PolyGIS project", filters: [{ name: "PolyGIS project", extensions: ["polygis"] }], properties: ["openFile"] });
            if (!r.canceled && r.filePaths[0]) openProjectFile(r.filePaths[0]);
          } },
        { label: "New window", accelerator: "CmdOrCtrl+N", click: () => { if (!win) createWindow(); else win.focus(); } },
        { type: "separator" },
        isMac ? { role: "close" } : { role: "quit" },
      ],
    },
    { label: "Edit", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: "View", submenu: [
        { label: "Reload", accelerator: "CmdOrCtrl+R", click: () => win && win.reload() },
        { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" },
      ] },
    { label: "Help", submenu: [
        { label: "PolyGIS on the web", click: () => shell.openExternal(APP_URL) },
        { label: "About PolyGIS", click: () => dialog.showMessageBox(win, { type: "info", title: "About PolyGIS",
            message: `PolyGIS ${app.getVersion()}`, detail: `GeoEstate · Web GIS as desktop software\nConnected to: ${APP_URL}` }) },
      ] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// Single instance: a second launch (e.g. double-clicking a .polygis) focuses the window and opens the file.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", (_e, argv) => {
    if (win) { if (win.isMinimized()) win.restore(); win.focus(); }
    openProjectFile(fileFromArgv(argv));
  });
  app.on("open-file", (e, p) => { e.preventDefault(); if (app.isReady()) openProjectFile(p); else pendingFile = { path: p }; });

  app.whenReady().then(() => {
    buildMenu();
    createWindow();
    // macOS may have queued a path object; Windows/Linux pass it on argv.
    if (pendingFile && pendingFile.path) { const p = pendingFile.path; pendingFile = null; openProjectFile(p); }
    else openProjectFile(fileFromArgv(process.argv));
    app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
  });
  app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
}

// The renderer asks for the connected URL (for a small "connected to" note) and confirms the bridge is present.
ipcMain.handle("polygis:info", () => ({ version: app.getVersion(), url: APP_URL, platform: process.platform }));
