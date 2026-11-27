# Code signing the PolyGIS installer

An unsigned `.exe` makes Windows SmartScreen show a blue **"Windows protected your PC"** warning, and some antivirus
engines flag it as "unknown publisher." Code signing attaches your organization's verified identity to the installer
so Windows trusts it and the warning goes away.

**You do not need to change any code to turn signing on.** The build looks for signing credentials in the
environment (GitHub Actions secrets, or your shell when building locally). When they are present it signs; when they
are absent it builds an unsigned installer exactly as before. Pick **one** of the three routes below.

---

## Which certificate should GeoEstate get?

| Option | Up-front check | SmartScreen trust | Works in GitHub-hosted CI | Rough cost |
|---|---|---|---|---|
| **Azure Trusted Signing** (recommended) | Business verified by Microsoft once | **Immediate** (EV-level) | **Yes** | ~US$10 / month |
| **EV certificate on a USB token** (DigiCert, Sectigo…) | Business verified by the CA | **Immediate** | **No** — the token can't go in a cloud runner | ~US$250–600 / year |
| **OV certificate (.pfx file)** | Business verified by the CA | Builds up over time (warns at first until reputation accrues) | **Yes** | ~US$150–400 / year |

> **Recommendation:** use **Azure Trusted Signing**. It gives the same instant "no warning" result as a hardware EV
> certificate, but because the key lives in Microsoft's cloud HSM it signs on an ordinary GitHub runner — no USB
> token to plug in, no self-hosted machine. An EV token is the traditional route and works, but it cannot sign on
> GitHub's hosted Windows runners, so every release would have to be signed by hand on the machine that holds the
> token.

---

## Route 1 — Azure Trusted Signing (recommended)

**One-time setup**

1. In the Azure portal, create a **Trusted Signing account** and, inside it, a **Certificate profile** of type
   *Public Trust*. Microsoft verifies your organization (GeoEstate NIG Limited) during this step.
2. Note three values: the account **endpoint** (e.g. `https://eus.codesigning.azure.net`), the **account name**, and
   the **certificate profile name**.
3. Create an **app registration** (service principal) and give it the **Trusted Signing Certificate Profile Signer**
   role on the account. Note its **tenant ID**, **client ID**, and a **client secret**.

**Add these repository secrets** (GitHub → *Settings → Secrets and variables → Actions → New repository secret*):

| Secret | Value |
|---|---|
| `AZURE_CODE_SIGNING_ENDPOINT` | the account endpoint URL |
| `AZURE_CODE_SIGNING_ACCOUNT` | the Trusted Signing account name |
| `AZURE_CODE_SIGNING_PROFILE` | the certificate profile name |
| `AZURE_TENANT_ID` | service-principal tenant ID |
| `AZURE_CLIENT_ID` | service-principal client ID |
| `AZURE_CLIENT_SECRET` | service-principal client secret |
| `WINDOWS_PUBLISHER_NAME` | *(optional)* exact subject name, e.g. `GeoEstate NIG Limited` |

That's it. The next push to `main` (or any run of the **Desktop (Windows installer)** workflow) produces a signed
installer. Nothing in the repo needs editing.

---

## Route 2 — Certificate file (.pfx / .p12)

Use this for an **OV certificate**, or any certificate you can export to a password-protected `.pfx` file.

1. Export the certificate (with its private key) to a `.pfx`.
2. Base64-encode it so it can live in a secret:
   - macOS/Linux: `base64 -i cert.pfx | tr -d '\n' > cert.b64`
   - Windows PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx")) > cert.b64`
3. Add repository secrets:

| Secret | Value |
|---|---|
| `WINDOWS_PFX_BASE64` | the contents of `cert.b64` |
| `WINDOWS_PFX_PASSWORD` | the `.pfx` export password |
| `WINDOWS_PUBLISHER_NAME` | *(optional)* exact subject name |

> An OV certificate signs cleanly, but SmartScreen still warns until the signed installer builds up download
> "reputation." The warning fades as more people install it. EV / Trusted Signing skip this waiting period.

---

## Route 3 — EV certificate on a hardware token (local signing)

A hardware **EV token** keeps its key on a USB device that can't be exported or mounted in a cloud runner, so signing
happens on **your own Windows machine** (or a self-hosted runner with the token attached).

1. Plug in the token and install the vendor's driver (SafeNet, etc.). Confirm the certificate shows in
   **certmgr.msc → Personal → Certificates**.
2. In a terminal **in the `desktop` folder**, set the subject name and build:

   ```powershell
   $env:WINDOWS_CERT_SUBJECT = "GeoEstate NIG Limited"   # exactly as on the certificate
   npm ci
   npm run dist
   ```

   electron-builder calls `signtool` against the token; the token's PIN prompt appears during signing.
3. The signed installer is written to `desktop/release/`. Upload it to a GitHub Release by hand (or push a
   `desktop-v1.0.0` tag after placing the signed file).

> Because this can't run on GitHub's hosted runners, the automated workflow will still build an **unsigned**
> installer. For signed releases, build locally as above, or set up a self-hosted Windows runner that has the token
> plugged in (then the same `WINDOWS_CERT_SUBJECT` secret/variable drives it).

---

## Verifying a signed build

On Windows, right-click the installer → **Properties → Digital Signatures**, or run:

```powershell
signtool verify /pa /v "release\PolyGIS Setup <version>.exe"
```

A valid signature shows your organization as the signer and a timestamp (so the signature stays valid after the
certificate eventually expires).

---

## How it works (for maintainers)

`desktop/electron-builder.config.js` reads the environment and, on the first matching route, fills in
electron-builder's Windows signing options (`azureSignOptions` or `signtoolOptions`). Signing happens **during** the
build, so the generated `latest.yml` hashes the already-signed `.exe` and auto-update stays consistent. With no
signing variables set, the config adds nothing and the installer is unsigned. The workflow passes the secrets through
as environment variables on the build step — see `.github/workflows/desktop.yml`.
