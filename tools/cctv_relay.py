#!/usr/bin/env python3
"""Send a CCTV / IP camera to PolyGIS as a live camera.

Run this on any computer on the same network as the camera (a laptop or a small PC at home or on site). It reads the
camera's RTSP stream with ffmpeg and sends a picture every half second to the camera's PolyGIS link, the same way the
phone page does, so the camera shows up in Defence > Cameras, on the video wall and (with --lat/--lng) on the map.
Nothing needs opening on the router: the computer only makes outgoing HTTPS requests.

Needs Python 3.9+ and ffmpeg (https://ffmpeg.org/download.html; on Windows `winget install ffmpeg`).

    python cctv_relay.py "rtsp://admin:PASSWORD@192.168.1.64:554/Streaming/Channels/102" \
        "https://<your-polygis>/cam/<token>" --lat 6.5244 --lng 3.3792

The second argument is the camera's link from Defence > Cameras (the one the phone would open). Stop with Ctrl+C.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 600_000          # PolyGIS refuses larger pictures (app/cameras.py)
SOI, EOI = b"\xff\xd8", b"\xff\xd9"


def frame_url(link: str) -> str:
    """https://host/cam/TOKEN -> https://host/api/cam/TOKEN/frame (the API is served under /api)."""
    u = urllib.parse.urlsplit(link.strip())
    parts = [p for p in u.path.split("/") if p]
    if "cam" not in parts or parts.index("cam") == len(parts) - 1:
        sys.exit("The second argument must be the camera's link from Defence > Cameras, like https://<polygis>/cam/<token>")
    token = parts[parts.index("cam") + 1]
    return urllib.parse.urlunsplit((u.scheme or "https", u.netloc, f"/api/cam/{token}/frame", "", ""))


class Latest:
    """The newest picture from ffmpeg; older ones are dropped when sending falls behind."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.frame: bytes | None = None
        self.seq = 0

    def put(self, f: bytes) -> None:
        with self.cond:
            self.frame, self.seq = f, self.seq + 1
            self.cond.notify_all()

    def wait(self, after: int, timeout: float) -> tuple[int, bytes | None]:
        with self.cond:
            self.cond.wait_for(lambda: self.seq != after, timeout)
            return self.seq, self.frame


def read_camera(rtsp: str, fps: float, width: int, quality: list[int], latest: Latest, stop: threading.Event) -> None:
    """Keep ffmpeg running against the camera, restarting it when the camera or the network drops."""
    backoff = 2.0
    while not stop.is_set():
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
        if rtsp.lower().startswith("rtsp"):
            cmd += ["-rtsp_transport", "tcp", "-timeout", "10000000"]   # 10 s without data counts as lost
        cmd += ["-i", rtsp, "-an", "-vf", f"fps={fps},scale='min({width},iw)':-2",
                "-q:v", str(quality[0]), "-f", "image2pipe", "-c:v", "mjpeg", "-"]
        started = time.monotonic()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        buf = b""
        got_any = False
        while not stop.is_set():
            chunk = p.stdout.read1(65536) if p.stdout else b""   # read1: whatever has arrived, not a full 64 KB
            if not chunk:
                break
            buf += chunk
            while True:                       # ffmpeg's MJPEG pictures carry no thumbnails, so FFD9 ends a picture
                s = buf.find(SOI)
                e = buf.find(EOI, s + 2) if s >= 0 else -1
                if s < 0 or e < 0:
                    buf = buf[s:] if s >= 0 else b""
                    break
                latest.put(buf[s:e + 2])
                got_any = True
                buf = buf[e + 2:]
        p.kill()
        err = (p.stderr.read().decode(errors="replace").strip().splitlines() or [""])[-1] if p.stderr else ""
        if stop.is_set():
            return
        if got_any and time.monotonic() - started > 30:
            backoff = 2.0
        print(f"[camera] stream stopped{': ' + err if err else ''}. Reconnecting in {backoff:.0f} s…", flush=True)
        stop.wait(backoff)
        backoff = min(60.0, backoff * 2)


def main() -> None:
    ap = argparse.ArgumentParser(description="Send a CCTV/IP camera (RTSP) to a PolyGIS camera link.")
    ap.add_argument("source", help="The camera's RTSP address (rtsp://user:pass@ip:554/...), or any address ffmpeg opens")
    ap.add_argument("link", help="The camera's link from PolyGIS Defence > Cameras (https://<polygis>/cam/<token>)")
    ap.add_argument("--fps", type=float, default=2, help="Pictures per second (default 2)")
    ap.add_argument("--width", type=int, default=960, help="Largest picture width in pixels (default 960)")
    ap.add_argument("--lat", type=float, help="Where the camera is, to show it on the map")
    ap.add_argument("--lng", type=float)
    ap.add_argument("--heading", type=float, help="Direction the camera faces, degrees from north")
    a = ap.parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is not installed or not on PATH. Install it (Windows: winget install ffmpeg) and open a new terminal.")
    if (a.lat is None) != (a.lng is None):
        sys.exit("Give both --lat and --lng, or neither.")

    url = frame_url(a.link)
    q = {}
    if a.lat is not None:
        q.update(lat=a.lat, lng=a.lng, accuracy=5)
        if a.heading is not None:
            q["heading"] = round(a.heading) % 360
    full = url + ("?" + urllib.parse.urlencode(q) if q else "")

    latest, stop, quality = Latest(), threading.Event(), [5]   # ffmpeg -q:v 2 (best) .. 31; raised if pictures are too big
    threading.Thread(target=read_camera, args=(a.source, a.fps, a.width, quality, latest, stop), daemon=True).start()
    print(f"Reading the camera and sending to {url.rsplit('/', 2)[0]}/… (Ctrl+C to stop)", flush=True)

    seq, sent, fails, last_note = 0, 0, 0, 0.0
    try:
        while True:
            new, f = latest.wait(seq, timeout=15)
            if new == seq:                             # nothing new from the camera for 15 s
                if f is None:
                    print("[camera] no picture yet: check the RTSP address, user name and password.", flush=True)
                continue
            seq = new
            if len(f) > MAX_BYTES:
                quality[0] = min(31, quality[0] + 3)   # takes effect at the next reconnect; skip this picture
                print("[camera] pictures too large; lowering quality (or use the camera's sub-stream / a smaller --width).", flush=True)
                continue
            req = urllib.request.Request(full, data=f, method="POST", headers={"Content-Type": "image/jpeg"})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    r.read()
                sent, fails = sent + 1, 0
                if time.monotonic() - last_note > 30:
                    print(f"[polygis] live: {sent} pictures sent", flush=True)
                    last_note = time.monotonic()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    sys.exit("PolyGIS no longer knows this camera link (it was changed or the camera removed). Copy the new link.")
                fails += 1
                print(f"[polygis] refused the picture ({e.code}); retrying.", flush=True)
                time.sleep(min(10, fails))
            except Exception as e:  # noqa: BLE001  (network down, DNS, timeouts: keep trying)
                fails += 1
                print(f"[polygis] can't reach PolyGIS ({e.__class__.__name__}); retrying…", flush=True)
                time.sleep(min(10, fails))
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop.set()


if __name__ == "__main__":
    main()
