// The only bridge between the native shell and the hosted PolyGIS page. It exposes a tiny, safe API: the web app can
// learn it's running in the desktop shell, and receive a .polygis file the user opened from the OS (double-click or
// File → Open). Everything else about the page is unchanged from the browser.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("polygisDesktop", {
  isDesktop: true,
  info: () => ipcRenderer.invoke("polygis:info"),
  // Register a handler for a project file opened from the OS. Returns an unsubscribe function.
  onOpenProject: (cb) => {
    const h = (_e, payload) => { try { cb(payload); } catch { /* ignore */ } };
    ipcRenderer.on("polygis:open-project", h);
    return () => ipcRenderer.removeListener("polygis:open-project", h);
  },
});
