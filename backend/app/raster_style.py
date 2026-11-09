"""Colouring a raster tile: a colour ramp stretched between two values, red-green-blue from three bands, or a colour
for each class."""
import numpy as np

CATEGORY = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
            "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5", "#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5"]
# ColorBrewer, viridis/magma (matplotlib, CC0) and a hypsometric terrain ramp
RAMPS = {
    "Gray": ["#000000", "#ffffff"],
    "Terrain": ["#33a02c", "#b2df8a", "#ffffbf", "#d9b38c", "#a6754d", "#ffffff"],
    "Greens": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
    "Blues": ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
    "Reds": ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
    "Viridis": ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
    "Magma": ["#000004", "#51127c", "#b73779", "#fc8961", "#fcfdbf"],
    "Red to green": ["#d7191c", "#fdae61", "#ffffbf", "#a6d96a", "#1a9641"],
    "Spectral": ["#2b83ba", "#abdda4", "#ffffbf", "#fdae61", "#d7191c"],
    "Blue to red": ["#2166ac", "#67a9cf", "#f7f7f7", "#ef8a62", "#b2182b"],
}


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def lut(name: str, invert: bool = False) -> np.ndarray:
    """256 colours along a ramp."""
    stops = np.array([hex_rgb(c) for c in RAMPS.get(name, RAMPS["Terrain"])], dtype=float)
    if invert:
        stops = stops[::-1]
    t = np.linspace(0, len(stops) - 1, 256)
    i = np.minimum(t.astype(int), len(stops) - 2)
    w = (t - i)[:, None]
    return (stops[i] * (1 - w) + stops[i + 1] * w).round().astype(np.uint8)


def _stretch(a: np.ndarray, lo: float, hi: float) -> np.ndarray:
    span = (hi - lo) or 1.0
    return np.clip((a - lo) / span, 0, 1)


def colourise(data: np.ndarray, valid: np.ndarray, style: dict, meta: dict) -> np.ndarray:
    h, w = data.shape[1:]
    out = np.zeros((h, w, 4), np.uint8)
    mode = style.get("mode", "ramp")
    if mode == "rgb":
        mins, maxs = style.get("mins") or [0, 0, 0], style.get("maxs") or [255, 255, 255]
        for k in range(3):
            out[..., k] = (_stretch(data[min(k, len(data) - 1)], mins[k], maxs[k]) * 255).round()
    elif mode == "classes":
        a = data[0]
        out[..., :3] = (184, 191, 204)
        for c in style.get("categories") or []:
            try:
                v = float(c["value"])
            except (TypeError, ValueError, KeyError):
                continue
            out[a == v, :3] = hex_rgb(c.get("color", "#888888"))
            if c.get("hidden"):
                valid = valid & (a != v)
    else:
        b = (meta.get("bands") or [{}])[int(style.get("band", 1)) - 1] if meta.get("bands") else {}
        lo = style.get("min", b.get("min", 0))
        hi = style.get("max", b.get("max", 1))
        idx = (_stretch(data[0], lo, hi) * 255).round().astype(int)
        out[..., :3] = lut(style.get("ramp", "Gray") if mode == "ramp" else "Gray", bool(style.get("invert")))[idx]
    out[..., 3] = np.where(valid, 255, 0)
    return out
