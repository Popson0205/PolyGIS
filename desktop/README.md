# PolyGIS Desktop

PolyGIS as installed Windows software — version 1. This is a thin native shell (Electron) around the hosted PolyGIS
web app: it opens the live site in its own app window, so **sign-in and the whole backend work exactly as in the
browser**, and adds desktop touches:

- A real application window, menu, and taskbar/Start-menu entry.
- A **`.polygis` file association** — double-click a project file in Windows and it opens in the app.
- External links (documentation, other sites) open in the system browser.

It needs an internet connection; it talks to the hosted PolyGIS. A fully offline build (bundling the database and the
geo-engine) is a later milestone.

## Which server it connects to

By default it connects to the production URL set in `main.js` (`DEFAULT_URL`). You can override it without rebuilding:

- set the `POLYGIS_URL` environment variable, or
- create `config.json` in the app's user-data folder (`%APPDATA%/PolyGIS/config.json`):

  ```json
  { "url": "https://your-polygis-host" }
  ```

## Build the installer

### In GitHub Actions (recommended)

The **Desktop (Windows installer)** workflow builds the `.exe` on a Windows runner.

- Run it by hand from the repo's **Actions** tab (**Run workflow**) → download the `PolyGIS-Windows-installer`
  artifact (contains `PolyGIS Setup <version>.exe`).
- Or push a tag like `desktop-v1.0.0` to also attach the installer to a GitHub Release.

### Locally on Windows

```bash
cd desktop
npm ci
npm run dist        # writes release/PolyGIS Setup <version>.exe
```

`npm start` runs the app without packaging (handy while developing).

## Notes

- The installer is **unsigned** for now, so Windows SmartScreen will warn on first run ("More info → Run anyway").
  Code-signing is a later step (needs a certificate).
- Double-click-to-open a `.polygis` file needs the web app's project-file support deployed on the server it connects
  to. If the server doesn't have it yet, the app shows a short "update to the latest version" message.
