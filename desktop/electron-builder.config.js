// electron-builder configuration for the PolyGIS Windows installer.
//
// The whole point of this file (vs. the old inline "build" block in package.json) is CODE SIGNING: it turns signing
// on automatically when the right credentials are present in the environment, and quietly builds an unsigned
// installer when they are not — so local builds and un-configured CI never break, and a real certificate takes over
// the moment its secrets exist. See SIGNING.md for how to set each one up.
//
// Three signing routes are supported, chosen by which environment variables are set (first match wins):
//
//   1. Azure Trusted Signing  — recommended for CI. Microsoft's cloud signing service gives the same instant
//      SmartScreen trust as a hardware EV certificate, with no USB token, so it works on a GitHub-hosted runner.
//        AZURE_CODE_SIGNING_ENDPOINT, AZURE_CODE_SIGNING_ACCOUNT, AZURE_CODE_SIGNING_PROFILE
//        + AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET   (service-principal login)
//
//   2. Certificate file (.pfx / .p12) — an OV certificate, or any exportable cert. Good for CI too.
//        WINDOWS_PFX_BASE64 (base64 of the .pfx)  or  WINDOWS_PFX_FILE (a path)
//        + WINDOWS_PFX_PASSWORD
//
//   3. Certificate in the Windows store by subject name — for an EV certificate on a hardware token, signed on a
//      local Windows machine or a self-hosted runner that has the token plugged in (hosted CI cannot use a token).
//        WINDOWS_CERT_SUBJECT   (the certificate's subject / organization name)
//
// Common (optional):
//   WINDOWS_PUBLISHER_NAME  — the exact "O=" name on the certificate (helps NSIS/update checks match the signature)
//   WINDOWS_TIMESTAMP_URL   — RFC-3161 timestamp server (default DigiCert)

const fs = require("fs");
const os = require("os");
const path = require("path");

const TIMESTAMP = process.env.WINDOWS_TIMESTAMP_URL || "http://timestamp.digicert.com";
const PUBLISHER = process.env.WINDOWS_PUBLISHER_NAME || undefined;

function windowsSigning() {
  // 1. Azure Trusted Signing (cloud; EV-level SmartScreen trust, CI-friendly).
  if (process.env.AZURE_CODE_SIGNING_ENDPOINT) {
    console.log("[sign] Windows: Azure Trusted Signing");
    return {
      azureSignOptions: {
        endpoint: process.env.AZURE_CODE_SIGNING_ENDPOINT,
        codeSigningAccountName: process.env.AZURE_CODE_SIGNING_ACCOUNT,
        certificateProfileName: process.env.AZURE_CODE_SIGNING_PROFILE,
        ...(PUBLISHER ? { publisherName: PUBLISHER } : {}),
      },
    };
  }

  // 2. Certificate file (.pfx / .p12). Accept either a base64 blob (nice for CI secrets) or a path.
  const pfxBase64 = process.env.WINDOWS_PFX_BASE64;
  const pfxFile = process.env.WINDOWS_PFX_FILE;
  if (pfxBase64 || pfxFile) {
    let file = pfxFile;
    if (pfxBase64) {
      file = path.join(os.tmpdir(), `polygis-codesign-${Date.now()}.pfx`);
      fs.writeFileSync(file, Buffer.from(pfxBase64, "base64"));
    }
    console.log("[sign] Windows: certificate file (signtool)");
    return {
      signtoolOptions: {
        certificateFile: file,
        certificatePassword: process.env.WINDOWS_PFX_PASSWORD,
        signingHashAlgorithms: ["sha256"],
        rfc3161TimeStampServer: TIMESTAMP,
        ...(PUBLISHER ? { publisherName: PUBLISHER } : {}),
      },
    };
  }

  // 3. EV certificate in the Windows cert store, by subject name (hardware token / local or self-hosted only).
  if (process.env.WINDOWS_CERT_SUBJECT) {
    console.log("[sign] Windows: certificate store subject name (EV token / local)");
    return {
      signtoolOptions: {
        certificateSubjectName: process.env.WINDOWS_CERT_SUBJECT,
        signingHashAlgorithms: ["sha256"],
        rfc3161TimeStampServer: TIMESTAMP,
        ...(PUBLISHER ? { publisherName: PUBLISHER } : {}),
      },
    };
  }

  console.log("[sign] Windows: no signing credentials found — building UNSIGNED (SmartScreen will warn on first run).");
  return {};
}

module.exports = {
  appId: "com.geoestate.polygis",
  productName: "PolyGIS",
  copyright: "© 2026 GeoEstate",
  files: ["main.js", "preload.js", "assets/**"],
  directories: { output: "release", buildResources: "assets" },
  fileAssociations: [
    { ext: "polygis", name: "PolyGIS Project", description: "PolyGIS project file", icon: "assets/icon.ico", role: "Editor" },
  ],
  win: {
    target: [{ target: "nsis", arch: ["x64"] }],
    icon: "assets/icon.ico",
    ...windowsSigning(),
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: "PolyGIS",
  },
};
