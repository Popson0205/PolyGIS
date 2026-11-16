/** The camera's own page, opened on a phone from its secret address (/cam/<token>): no sign-in. It sends what the phone's
 *  camera sees, a few pictures a second, and where the phone is, to PolyGIS, where people of the organization watch it
 *  live in Defence › Cameras. Keep the page open with the screen on (it asks the phone to keep the screen awake). */
import { useEffect, useRef, useState } from "react";
import { Camera as CamIcon, MapPin, RefreshCw, Square, Video } from "lucide-react";
import { BASE } from "../lib/api";

const RATES = [{ fps: 2, w: 960, q: 0.7, label: "Sharper (2 a second)" }, { fps: 5, w: 640, q: 0.6, label: "Smooth (5 a second)" }, { fps: 8, w: 480, q: 0.55, label: "Fastest (8 a second, small)" }];

export default function CameraPage() {
  const token = decodeURIComponent(window.location.pathname.split("/")[2] ?? "");
  const [name, setName] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [on, setOn] = useState(false);
  const [facing, setFacing] = useState<"environment" | "user">("environment");
  const [rate, setRate] = useState(1);
  const [sent, setSent] = useState(0);
  const [status, setStatus] = useState("Not sending.");
  const [where, setWhere] = useState<GeolocationCoordinates | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const run = useRef(0);
  const pos = useRef<GeolocationCoordinates | null>(null);

  useEffect(() => {
    fetch(`${BASE}/cam/${encodeURIComponent(token)}`).then(async (r) => {
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? "This camera address is not in use.");
      setName((await r.json()).name);
    }).catch((e) => setProblem(e instanceof Error ? e.message : String(e)));
  }, [token]);

  useEffect(() => () => { run.current++; }, []);

  async function start() {
    setProblem(null);
    if (!navigator.mediaDevices?.getUserMedia) { setProblem("This browser cannot use the camera here. Open the link in Chrome or Safari (it must start with https://)."); return; }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: facing }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
    } catch (e) {
      setProblem(e instanceof DOMException && e.name === "NotAllowedError" ? "The camera was not allowed. Allow it in the browser's site settings and try again." : "The camera could not be opened.");
      return;
    }
    const v = video.current!;
    v.srcObject = stream;
    await v.play().catch(() => undefined);
    // where the phone is, as it moves
    const watchId = navigator.geolocation?.watchPosition((p) => { pos.current = p.coords; setWhere(p.coords); }, () => undefined,
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 });
    // keep the screen on while sending
    let lock: { release: () => Promise<void> } | null = null;
    try { lock = await (navigator as unknown as { wakeLock?: { request: (t: string) => Promise<{ release: () => Promise<void> }> } }).wakeLock?.request("screen") ?? null; } catch { /* not offered */ }
    const me = ++run.current;
    setOn(true); setSent(0); setStatus("Sending…");
    const canvas = document.createElement("canvas");
    let n = 0, fails = 0;
    while (run.current === me) {
      const t0 = performance.now();
      const { fps, w, q } = RATES[rateRef.current];
      if (v.videoWidth) {
        const scale = Math.min(1, w / v.videoWidth);
        canvas.width = Math.round(v.videoWidth * scale); canvas.height = Math.round(v.videoHeight * scale);
        canvas.getContext("2d")!.drawImage(v, 0, 0, canvas.width, canvas.height);
        const blob = await new Promise<Blob | null>((ok) => canvas.toBlob(ok, "image/jpeg", q));
        if (blob && run.current === me) {
          const c = pos.current;
          const qs = c ? `?lat=${c.latitude}&lng=${c.longitude}&accuracy=${Math.round(c.accuracy)}${c.heading != null && !Number.isNaN(c.heading) ? `&heading=${Math.round(c.heading)}` : ""}` : "";
          try {
            const r = await fetch(`${BASE}/cam/${encodeURIComponent(token)}/frame${qs}`, { method: "POST", body: blob, headers: { "Content-Type": "image/jpeg" } });
            if (r.status === 404) { setProblem("This camera address was changed or removed in PolyGIS. Ask for the new link."); break; }
            if (!r.ok) throw new Error(String(r.status));
            n++; fails = 0; setSent(n); setStatus("Live: people of your organization can watch now.");
          } catch {
            fails++; setStatus(`Connection lost; trying again… (${fails})`);
            await new Promise((ok) => setTimeout(ok, Math.min(5000, 800 * fails)));
          }
        }
      }
      const wait = 1000 / fps - (performance.now() - t0);
      if (wait > 0) await new Promise((ok) => setTimeout(ok, wait));
    }
    stream.getTracks().forEach((t) => t.stop());
    if (watchId != null) navigator.geolocation.clearWatch(watchId);
    void lock?.release().catch(() => undefined);
    if (run.current === me) { run.current++; }
    setOn(false); setStatus("Not sending.");
  }
  const rateRef = useRef(rate);
  rateRef.current = rate;
  const stop = () => { run.current++; };

  return (
    <main className="cam-page">
      <header className="cam-head"><Video size={20} aria-hidden="true" /><div><strong>PolyGIS camera</strong><span>{name ?? (problem ? "" : "…")}</span></div>
        {on && <span className="cam-live" aria-live="polite">● LIVE</span>}</header>
      {problem && <p className="cam-problem" role="alert">{problem}</p>}
      <div className="cam-view">
        <video ref={video} playsInline muted aria-label="What the camera sees" />
        {!on && <div className="cam-off"><CamIcon size={40} aria-hidden="true" /><p>The camera starts when you press Start.</p></div>}
      </div>
      <div className="cam-acts">
        {!on ? <button type="button" className="cam-go" onClick={() => void start()} disabled={!name}><Video size={18} aria-hidden="true" />Start sending</button>
          : <button type="button" className="cam-stop" onClick={stop}><Square size={16} aria-hidden="true" />Stop</button>}
        <button type="button" className="cam-btn" disabled={on} onClick={() => setFacing(facing === "environment" ? "user" : "environment")}>
          <RefreshCw size={16} aria-hidden="true" />{facing === "environment" ? "Back camera" : "Front camera"}</button>
      </div>
      <label className="cam-f">Pictures<select value={rate} onChange={(e) => setRate(Number(e.target.value))}>
        {RATES.map((r, i) => <option key={r.fps} value={i}>{r.label}</option>)}</select></label>
      <p className="cam-status">{status}{on ? ` ${sent} sent.` : ""}</p>
      <p className="cam-status"><MapPin size={14} aria-hidden="true" /> {where ? `Location ±${Math.round(where.accuracy)} m: shown on the map.` : "Location: allow it to show this camera on the map."}</p>
      <p className="cam-note">Keep this page open with the screen on. Only people of the organization that owns this camera can watch.
        Whoever has this link can send pictures to it, so keep it private.</p>
    </main>
  );
}
