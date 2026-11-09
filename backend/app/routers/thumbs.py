"""A small picture of a project's map, for the projects page: its layers drawn in their own colours over their
combined extent (Web Mercator). Vector layers come straight from PostGIS as SVG paths, simplified to the picture's
size; rasters are drawn from their overviews."""
import base64
import html
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Response

from .. import rasters
from ..db import get_conn
from ..identity import need
from .projects import project_of

router = APIRouter(tags=["projects"])
W, H = 400, 250
MOST = 4000            # features drawn per layer


def _colour(style: dict) -> tuple[str, str]:
    """The fill and outline a layer's features are drawn with (the first class of a classified style)."""
    c = style.get("color") or "#2f6db5"
    if style.get("type") == "categorized" and style.get("categories"):
        c = style["categories"][0].get("color", c)
    if style.get("type") == "graduated" and style.get("classes"):
        c = style["classes"][len(style["classes"]) // 2].get("color", c)
    return c, style.get("stroke") or c


def _ok(c: str) -> str:
    return c if isinstance(c, str) and len(c) == 7 and c.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in c[1:]) else "#888888"


@router.get("/projects/{project_id}/thumbnail.svg", summary="A small picture of the project's map", response_class=Response,
            responses={200: {"content": {"image/svg+xml": {}}}})
async def thumbnail(project_id: UUID, v: str | None = None, user=Depends(need()), conn=Depends(get_conn)):
    await project_of(conn, project_id, user)
    cur = await conn.execute("SELECT id, geometry_type, style, raster, visible FROM layers WHERE project_id = %s ORDER BY position, created_at", (project_id,))
    layers = [r for r in await cur.fetchall() if r["visible"]]
    # the extent of everything, in Web Mercator
    cur = await conn.execute(
        "SELECT ST_XMin(e) AS x1, ST_YMin(e) AS y1, ST_XMax(e) AS x2, ST_YMax(e) AS y2 FROM (SELECT ST_Extent(ST_Transform(geom, 3857)) AS e "
        "FROM features f JOIN layers l ON l.id = f.layer_id WHERE l.project_id = %s AND l.visible) q", (project_id,))
    e = await cur.fetchone()
    box = [e["x1"], e["y1"], e["x2"], e["y2"]] if e and e["x1"] is not None else None
    for lay in layers:
        if lay["geometry_type"] == "Raster" and lay["raster"]:
            cur = await conn.execute("SELECT ST_XMin(b) AS x1, ST_YMin(b) AS y1, ST_XMax(b) AS x2, ST_YMax(b) AS y2 FROM "
                                     "(SELECT ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 4326), 3857) AS b) q", tuple(lay["raster"]["bbox"]))
            r = await cur.fetchone()
            box = [r["x1"], r["y1"], r["x2"], r["y2"]] if box is None else [min(box[0], r["x1"]), min(box[1], r["y1"]), max(box[2], r["x2"]), max(box[3], r["y2"])]
    headers = {"Cache-Control": "private, max-age=86400" if v else "no-cache"}
    if box is None:
        return Response(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"><rect width="{W}" height="{H}" fill="#eef1ea"/></svg>',
                        media_type="image/svg+xml", headers=headers)
    # fit the extent into the picture, with a margin, keeping its shape
    x1, y1, x2, y2 = box
    w, h = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
    if w / h > W / H:
        h2 = w * H / W
        y1, y2 = y1 - (h2 - h) / 2, y2 + (h2 - h) / 2
    else:
        w2 = h * W / H
        x1, x2 = x1 - (w2 - w) / 2, x2 + (w2 - w) / 2
    pad = (x2 - x1) * 0.07
    x1, y1, x2, y2 = x1 - pad, y1 - pad, x2 + pad, y2 + pad
    px = (x2 - x1) / W                       # metres per picture pixel
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x1:.1f} {-y2:.1f} {x2 - x1:.1f} {y2 - y1:.1f}" preserveAspectRatio="xMidYMid slice">',
             f'<rect x="{x1:.1f}" y="{-y2:.1f}" width="{x2 - x1:.1f}" height="{y2 - y1:.1f}" fill="#eef1ea"/>']
    for lay in layers:
        st = lay["style"] or {}
        if lay["geometry_type"] == "Raster":
            got = await anyio.to_thread.run_sync(lambda m=lay["raster"], s=st: rasters.preview(m, s))
            if got:
                png, (bx1, by1, bx2, by2) = got
                parts.append(f'<image href="data:image/png;base64,{base64.b64encode(png).decode()}" x="{bx1:.1f}" y="{-by2:.1f}" '
                             f'width="{bx2 - bx1:.1f}" height="{by2 - by1:.1f}" preserveAspectRatio="none" opacity="{float(st.get("opacity", 1)):.2f}"/>')
            continue
        fill, stroke = (_ok(c) for c in _colour(st))
        op = min(1.0, max(0.15, float(st.get("opacity", 0.8))))
        if lay["geometry_type"] == "Point":
            cur = await conn.execute("SELECT ST_X(p) AS x, ST_Y(p) AS y FROM (SELECT (ST_DumpPoints(ST_Transform(geom, 3857))).geom AS p "
                                     "FROM features WHERE layer_id = %s ORDER BY id LIMIT %s) q", (lay["id"], MOST))
            rr = px * 2.6
            dots = "".join(f'<circle cx="{r["x"]:.1f}" cy="{-r["y"]:.1f}" r="{rr:.1f}"/>' for r in await cur.fetchall())
            parts.append(f'<g fill="{fill}" stroke="#ffffff" stroke-width="{px * 0.8:.1f}">{dots}</g>')
            continue
        cur = await conn.execute(
            "SELECT ST_AsSVG(ST_SimplifyPreserveTopology(ST_Transform(geom, 3857), %s), 0, 1) AS d FROM features WHERE layer_id = %s "
            "ORDER BY id LIMIT %s", (px * 0.7, lay["id"], MOST))
        rows = [r["d"] for r in await cur.fetchall() if r["d"]]
        if lay["geometry_type"] == "LineString":
            parts.append(f'<g fill="none" stroke="{fill}" stroke-width="{px * 1.6:.1f}" stroke-linejoin="round" stroke-linecap="round" opacity="{max(op, .7):.2f}">'
                         + "".join(f'<path d="{html.escape(d)}"/>' for d in rows) + "</g>")
        else:
            parts.append(f'<g fill="{fill}" fill-opacity="{op:.2f}" stroke="{stroke}" stroke-width="{px * 1:.1f}" stroke-linejoin="round" fill-rule="evenodd">'
                         + "".join(f'<path d="{html.escape(d)}"/>' for d in rows) + "</g>")
    parts.append("</svg>")
    return Response("".join(parts), media_type="image/svg+xml", headers=headers)
