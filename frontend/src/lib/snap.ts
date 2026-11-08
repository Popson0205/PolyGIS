/** Snapping to features already on the map, at full precision: to a vertex first, else to the nearest point on an
 *  edge, within a distance in screen pixels. */
type Pos = [number, number];
type Project = (lng: number, lat: number) => { x: number; y: number };
type Unproject = (x: number, y: number) => { lng: number; lat: number };

export function vertices(g: GeoJSON.Geometry): Pos[] {
  switch (g.type) {
    case "Point": return [g.coordinates as Pos];
    case "MultiPoint": case "LineString": return g.coordinates as Pos[];
    case "MultiLineString": case "Polygon": return (g.coordinates as Pos[][]).flat();
    case "MultiPolygon": return (g.coordinates as Pos[][][]).flat(2);
    default: return [];
  }
}

export function segments(g: GeoJSON.Geometry): [Pos, Pos][] {
  const lines: Pos[][] = g.type === "LineString" ? [g.coordinates as Pos[]] : g.type === "MultiLineString" || g.type === "Polygon" ? (g.coordinates as Pos[][])
    : g.type === "MultiPolygon" ? (g.coordinates as Pos[][][]).flat() : [];
  return lines.flatMap((l) => l.slice(1).map((p, k) => [l[k], p] as [Pos, Pos]));
}

/** Where a pointer at (x, y) on screen snaps to, or null when nothing is within `px` pixels. */
export function snapPoint(x: number, y: number, features: GeoJSON.Feature[], project: Project, unproject: Unproject, px = 10): Pos | null {
  let best: Pos | null = null;
  let bestD = px;
  for (const f of features) {
    if (!f.geometry) continue;
    for (const v of vertices(f.geometry)) {
      const p = project(v[0], v[1]);
      const d = Math.hypot(p.x - x, p.y - y);
      if (d <= bestD) { bestD = d; best = v; }
    }
  }
  if (best) return best;                    // vertices win over edges
  bestD = px;
  for (const f of features) {
    if (!f.geometry) continue;
    for (const [a, b] of segments(f.geometry)) {
      const pa = project(a[0], a[1]), pb = project(b[0], b[1]);
      const dx = pb.x - pa.x, dy = pb.y - pa.y;
      const len = dx * dx + dy * dy;
      if (!len) continue;
      const t = Math.max(0, Math.min(1, ((x - pa.x) * dx + (y - pa.y) * dy) / len));
      const qx = pa.x + t * dx, qy = pa.y + t * dy;
      const d = Math.hypot(qx - x, qy - y);
      if (d <= bestD) {
        bestD = d;
        // the point on the edge, in geographic coordinates (straight between the two vertices at map scale)
        best = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
      }
    }
  }
  void unproject;
  return best;
}
