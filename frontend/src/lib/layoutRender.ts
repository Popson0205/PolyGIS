/** Draws a layout's operations: on a canvas (preview and PNG) or into a PDF (vector, with the map as a picture). */
import type { jsPDF as JsPDF } from "jspdf";
import { ptToMm, type Measure, type Op, type Rect } from "./layout";

const FONT = "Helvetica, Arial, sans-serif";

/** Measures text with a canvas in the PDF's own face (Helvetica), in millimetres. */
export function canvasMeasure(): Measure {
  const c = document.createElement("canvas").getContext("2d");
  if (!c) return (s, pt) => s.length * pt * 0.3528 * 0.52;
  return (s, pt, bold) => { c.font = `${bold ? "bold " : ""}100px ${FONT}`; return (c.measureText(s).width / 100) * ptToMm(pt); };
}

/** The map picture; while a newer one is drawn, the last is shown moved by (dx, dy) mm and enlarged by `zoom`. */
export interface MapPicture { image: CanvasImageSource | null; dx?: number; dy?: number; zoom?: number }

export function drawCanvas(ctx: CanvasRenderingContext2D, ops: Op[], pxPerMm: number, map: MapPicture) {
  const k = pxPerMm;
  const clip = (r: Rect) => { ctx.beginPath(); ctx.rect(r.x * k, r.y * k, r.w * k, r.h * k); ctx.clip(); };
  for (const o of ops) {
    ctx.save();
    switch (o.k) {
      case "rect":
        if (o.fill) { ctx.fillStyle = o.fill; ctx.fillRect(o.x * k, o.y * k, o.w * k, o.h * k); }
        if (o.stroke) { ctx.strokeStyle = o.stroke; ctx.lineWidth = (o.lw ?? 0.2) * k; ctx.strokeRect(o.x * k, o.y * k, o.w * k, o.h * k); }
        break;
      case "map":
        ctx.fillStyle = "#eef1f5"; ctx.fillRect(o.x * k, o.y * k, o.w * k, o.h * k);
        if (map.image) {
          clip(o);
          const z = map.zoom ?? 1, w = o.w * z, h = o.h * z;
          ctx.drawImage(map.image, (o.x + o.w / 2 - w / 2 + (map.dx ?? 0)) * k, (o.y + o.h / 2 - h / 2 + (map.dy ?? 0)) * k, w * k, h * k);
        }
        break;
      case "line":
        if (o.clip) clip(o.clip);
        ctx.strokeStyle = o.stroke; ctx.lineWidth = o.lw * k; ctx.lineCap = "round"; ctx.lineJoin = "round";
        ctx.beginPath(); o.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x * k, y * k) : ctx.moveTo(x * k, y * k))); ctx.stroke();
        break;
      case "poly":
        ctx.beginPath(); o.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x * k, y * k) : ctx.moveTo(x * k, y * k))); ctx.closePath();
        ctx.fillStyle = o.fill; ctx.fill();
        if (o.stroke) { ctx.strokeStyle = o.stroke; ctx.lineWidth = (o.lw ?? 0.2) * k; ctx.stroke(); }
        break;
      case "circle":
        ctx.beginPath(); ctx.arc(o.x * k, o.y * k, o.r * k, 0, Math.PI * 2);
        if (o.fill) { ctx.fillStyle = o.fill; ctx.fill(); }
        if (o.stroke) { ctx.strokeStyle = o.stroke; ctx.lineWidth = (o.lw ?? 0.2) * k; ctx.stroke(); }
        break;
      case "gradient": {
        const g = o.vertical ? ctx.createLinearGradient(0, o.y * k, 0, (o.y + o.h) * k) : ctx.createLinearGradient(o.x * k, 0, (o.x + o.w) * k, 0);
        o.colors.forEach((c, i) => g.addColorStop(o.colors.length > 1 ? i / (o.colors.length - 1) : 0, c));
        ctx.fillStyle = g; ctx.fillRect(o.x * k, o.y * k, o.w * k, o.h * k);
        break;
      }
      case "text":
        ctx.fillStyle = o.color;
        ctx.font = `${o.bold ? "bold " : ""}${ptToMm(o.pt) * k}px ${FONT}`;
        ctx.textAlign = o.align ?? "left";
        ctx.textBaseline = o.base === "top" ? "top" : o.base === "middle" ? "middle" : o.base === "bottom" ? "bottom" : "alphabetic";
        ctx.translate(o.x * k, o.y * k);
        if (o.angle) ctx.rotate((-o.angle * Math.PI) / 180);
        ctx.fillText(o.s, 0, 0);
        break;
    }
    ctx.restore();
  }
}

const rgb = (hex: string): [number, number, number] => {
  const h = hex.replace("#", "");
  const f = h.length === 3 ? h.split("").map((c) => c + c).join("") : h.slice(0, 6);
  return [0, 2, 4].map((i) => parseInt(f.slice(i, i + 2), 16) || 0) as [number, number, number];
};
const mix = (a: string, b: string, t: number) => { const x = rgb(a), y = rgb(b); return x.map((v, i) => Math.round(v + (y[i] - v) * t)) as [number, number, number]; };

/** Writes the operations into a jsPDF page, in millimetres; the map is `mapJpeg` (a data URL). */
export function drawPdf(doc: JsPDF, ops: Op[], mapJpeg: string | null) {
  for (const o of ops) {
    switch (o.k) {
      case "rect": {
        if (o.fill) doc.setFillColor(...rgb(o.fill));
        if (o.stroke) { doc.setDrawColor(...rgb(o.stroke)); doc.setLineWidth(o.lw ?? 0.2); }
        const style = o.fill && o.stroke ? "FD" : o.fill ? "F" : "S";
        doc.rect(o.x, o.y, o.w, o.h, style);
        break;
      }
      case "map":
        doc.setFillColor(238, 241, 245); doc.rect(o.x, o.y, o.w, o.h, "F");
        if (mapJpeg) doc.addImage(mapJpeg, "JPEG", o.x, o.y, o.w, o.h, undefined, "FAST");
        break;
      case "line": {
        if (o.pts.length < 2) break;
        doc.saveGraphicsState();
        if (o.clip) { doc.rect(o.clip.x, o.clip.y, o.clip.w, o.clip.h, null); doc.clip(); doc.discardPath(); }
        doc.setDrawColor(...rgb(o.stroke)); doc.setLineWidth(o.lw); doc.setLineCap("round"); doc.setLineJoin("round");
        const d = o.pts.slice(1).map(([x, y], i) => [x - o.pts[i][0], y - o.pts[i][1]]);
        doc.lines(d, o.pts[0][0], o.pts[0][1], [1, 1], "S", false);
        doc.restoreGraphicsState();
        break;
      }
      case "poly": {
        const d = o.pts.slice(1).map(([x, y], i) => [x - o.pts[i][0], y - o.pts[i][1]]);
        doc.setFillColor(...rgb(o.fill));
        if (o.stroke) { doc.setDrawColor(...rgb(o.stroke)); doc.setLineWidth(o.lw ?? 0.2); }
        doc.lines(d, o.pts[0][0], o.pts[0][1], [1, 1], o.stroke ? "FD" : "F", true);
        break;
      }
      case "circle":
        if (o.fill) doc.setFillColor(...rgb(o.fill));
        if (o.stroke) { doc.setDrawColor(...rgb(o.stroke)); doc.setLineWidth(o.lw ?? 0.2); }
        doc.circle(o.x, o.y, o.r, o.fill && o.stroke ? "FD" : o.fill ? "F" : "S");
        break;
      case "gradient": {
        const n = 48;
        for (let i = 0; i < n; i++) {
          const t = i / (n - 1), seg = t * (o.colors.length - 1), a = Math.min(o.colors.length - 1, Math.floor(seg));
          const c = o.colors.length > 1 ? mix(o.colors[a], o.colors[Math.min(o.colors.length - 1, a + 1)], seg - a) : rgb(o.colors[0]);
          doc.setFillColor(...c);
          if (o.vertical) doc.rect(o.x, o.y + (o.h * i) / n, o.w, o.h / n + 0.05, "F");
          else doc.rect(o.x + (o.w * i) / n, o.y, o.w / n + 0.05, o.h, "F");
        }
        break;
      }
      case "text":
        doc.setFont("helvetica", o.bold ? "bold" : "normal");
        doc.setFontSize(o.pt);
        doc.setTextColor(...rgb(o.color));
        doc.text(pdfSafe(o.s), o.x, o.y, { align: o.align ?? "left", baseline: o.base ?? "alphabetic", angle: o.angle ?? 0 });
        break;
    }
  }
}

/** The PDF's built-in Helvetica covers Western European letters only: anything else is spelt without its marks, or
 *  replaced, so it is never dropped silently. */
export function pdfSafe(s: string) {
  const ok = /[\x20-\x7e -ÿ–—‘’“”•…]/;
  return Array.from(s.normalize("NFC")).map((ch) => {
    if (ok.test(ch)) return ch;
    if (/[\u0300-\u036f]/.test(ch)) return "";            // a mark left over on a letter that has no single form
    const base = ch.normalize("NFD").replace(/[̀-ͯ]/g, "");
    return Array.from(base).every((c) => ok.test(c)) && base ? base : "?";
  }).join("");
}
