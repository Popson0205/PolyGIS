"""Image correction and enhancement tools, checked against numbers worked out by hand on small synthetic scenes."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from test_rasters import CELL, X0, Y0, R, pro, run, tif  # noqa: F401  (fixtures)


def read_all(layer):
    from app import rasters
    with rasterio.open(rasters.local(layer["raster"]["key"])) as s:
        return s.read(masked=True).astype(float).filled(np.nan), s.profile


def up(c, R, name, arr, **kw):
    r = c.post(f"/projects/{R['p']['id']}/layers/import", files={"file": (name, tif(arr, **kw))})
    assert r.status_code == 201, r.text
    R[name.split(".")[0]] = r.json()[0]
    return R[name.split(".")[0]]


N = 60
rng = np.random.default_rng(7)


def scene():
    """A 4-band Sentinel-2-like scene (Red, Green, Blue, NIR, scaled by 10 000): vegetation with a haze offset of 300,
    a bright white cloud and its dark shadow to the south."""
    a = np.stack([np.full((N, N), 800.0), np.full((N, N), 900.0), np.full((N, N), 700.0), np.full((N, N), 3500.0)])
    a[:, 10:20, 10:20] = np.array([6500, 6600, 6800, 6900])[:, None, None]      # cloud
    a[:, 24:30, 12:20] = np.array([350, 350, 330, 450])[:, None, None]          # its shadow
    a[:, 40:50, 40:50] = np.array([500, 600, 400, 300])[:, None, None]          # dark water, far away: not a shadow
    return a


def test_to_reflectance_and_dark_object_subtraction(pro, R):
    up(pro, R, "s2.tif", scene().astype("uint16"))
    lay = run(pro, R, "to_reflectance", input="s2", preset="s2_offset")
    a, prof = read_all(lay)
    assert prof["count"] == 4 and prof["dtype"] == "float32"
    assert a[0, 0, 0] == pytest.approx(0.0, abs=1e-6)                                # (800 − 1000) / 10 000 is below 0: clipped
    assert a[3, 0, 0] == pytest.approx(0.25, abs=1e-6)                               # (3500 − 1000) / 10 000
    lay = run(pro, R, "to_reflectance", input="s2", preset="custom", gain=2, offset=1)
    assert read_all(lay)[0][3, 0, 0] == pytest.approx(7001)
    # every band lowered by its darkest value, plus 1 % reflectance (100 in scaled units)
    lay = run(pro, R, "dark_object_subtraction", input="s2", percentile=0)
    a, _ = read_all(lay)
    assert a[0, 0, 0] == pytest.approx(800 - 350 + 100) and a[3, 0, 0] == pytest.approx(3500 - 300 + 100)
    assert lay["source"]["summary"]["dark_values"] == [350, 350, 330, 300]
    a2, _ = read_all(run(pro, R, "dark_object_subtraction", input="s2", percentile=0, dos1=False))
    assert np.nanmin(a2) == 0


def test_cloud_mask_finds_the_cloud_and_its_shadow_but_not_water(pro, R):
    up(pro, R, "s2.tif", scene().astype("uint16"))
    lay = run(pro, R, "cloud_mask", input="s2", buffer=0, output="mask")
    m, _ = read_all(lay)
    assert (m[0, 10:20, 10:20] == 2).all()                                           # cloud
    assert (m[0, 24:30, 12:20] == 3).all()                                           # shadow
    assert (m[0, 40:50, 40:50] == 1).all() and m[0, 0, 59] == 1                        # water and vegetation stay clear
    s = lay["source"]["summary"]
    assert s["cloud_percent"] == pytest.approx(100 * 100 / N ** 2, abs=0.01) and s["shadow_percent"] == pytest.approx(100 * 48 / N ** 2, abs=0.01)
    a, _ = read_all(run(pro, R, "cloud_mask", input="s2", buffer=1))
    assert np.isnan(a[:, 9:21, 9:21]).all() and np.isfinite(a[:, 0, 0]).all()          # removed, with a one-cell margin


def test_cloud_mask_from_a_sentinel2_scl_band(pro, R):
    up(pro, R, "s2.tif", scene().astype("uint16"))
    scl = np.full((N, N), 4, "uint8")                                                 # vegetation
    scl[0:5, :] = 9                                                                   # high-probability cloud
    scl[5:8, :] = 3                                                                   # cloud shadow
    up(pro, R, "classification.tif", scl)
    m, _ = read_all(run(pro, R, "cloud_mask", input="s2", method="scl", mask="classification", buffer=0, output="mask"))
    assert (m[0, 0:5] == 2).all() and (m[0, 5:8] == 3).all() and (m[0, 8:] == 1).all()
    r = pro.post(f"/projects/{R['p']['id']}/raster-tools/cloud_mask", json={"params": {"input": R["s2"]["id"], "method": "scl"}})
    assert r.status_code == 422 and "SCL" in r.json()["detail"]


def test_topographic_correction_evens_out_slopes(pro, R):
    """A ridge: the east face lit by a morning sun from the east, the west face in its lee. Same land cover on both, so
    the corrected brightness should be (nearly) the same."""
    cols = np.arange(N)[None, :].repeat(N, 0)
    dem = (500 - 0.5 * np.abs(cols - 30) * CELL).astype("float32")                   # 26.6° slopes facing east and west
    slope, zen = np.arctan(0.5), np.radians(90 - 40)
    cos_i = np.where(cols >= 30, np.cos(zen) * np.cos(slope) + np.sin(zen) * np.sin(slope),   # facing the sun (east)
                     np.cos(zen) * np.cos(slope) - np.sin(zen) * np.sin(slope))
    img = (0.3 * cos_i)[None].astype("float32")                                         # Lambertian: brightness ∝ illumination
    up(pro, R, "ridge.tif", dem)
    up(pro, R, "lit.tif", img)
    a, _ = read_all(run(pro, R, "topographic_correction", input="lit", dem="ridge", azimuth=90, elevation=40, method="cosine"))
    west, east = a[0, 30, 10], a[0, 30, 50]
    assert img[0, 30, 50] / img[0, 30, 10] > 3                                         # very uneven before
    assert east == pytest.approx(west, rel=0.02) and east == pytest.approx(0.3 * np.cos(zen), rel=0.02)
    c, _ = read_all(run(pro, R, "topographic_correction", input="lit", dem="ridge", azimuth=90, elevation=40))
    assert c[0, 30, 50] == pytest.approx(c[0, 30, 10], rel=0.05)


def test_pan_sharpening_takes_the_pans_detail(pro, R):
    ms = np.stack([np.full((20, 20), v, "float32") for v in (100, 200, 300)])
    pan = np.full((40, 40), 200, "float32")
    pan[:, 20:] = 400                                                                  # an edge sharper than the colour cells
    up(pro, R, "ms.tif", ms, transform=from_origin(X0, Y0, 30, 30))
    up(pro, R, "pan.tif", pan, transform=from_origin(X0, Y0, 15, 15))
    lay = run(pro, R, "pan_sharpen", input="ms", pan="pan")
    a, prof = read_all(lay)
    assert (prof["width"], prof["height"], prof["count"]) == (40, 40, 3)               # on the pan's 15 m grid
    assert a[0, 5, 30] / a[0, 5, 5] == pytest.approx(2, rel=0.01)                      # brighter where the pan is, ×2
    assert a[2, 5, 5] / a[0, 5, 5] == pytest.approx(3, rel=0.01)                       # colour ratios kept (Brovey)
    b, _ = read_all(run(pro, R, "pan_sharpen", input="ms", pan="pan", method="ihs"))
    assert b[2, 5, 5] - b[0, 5, 5] == pytest.approx(200, rel=0.01)                     # differences kept (intensity substitution)


def test_fill_gaps(pro, R):
    a = np.full((N, N), 50.0, "float32")
    a[20:25, 20:25] = -9999                                                            # a hole
    a[:, 59] = -9999
    up(pro, R, "holes.tif", a, nodata=-9999)
    lay = run(pro, R, "fill_nodata", input="holes", distance=10)
    out, _ = read_all(lay)
    assert np.isfinite(out).all() and out[0, 22, 22] == pytest.approx(50)
    assert lay["source"]["summary"]["cells_filled"] == 25 + N
    far = np.full((N, N), -9999, "float32")
    far[0, 0] = 10
    up(pro, R, "far.tif", far, nodata=-9999)
    o2, _ = read_all(run(pro, R, "fill_nodata", input="far", distance=5))
    assert np.isfinite(o2[0, 3, 3]) and np.isnan(o2[0, 40, 40])                        # beyond the search distance: left empty


def test_brightness_contrast_gamma_and_byte_no_data(pro, R):
    g = np.tile(np.linspace(0, 1000, N, dtype="float32"), (N, 1))
    g[0, 0] = -1
    up(pro, R, "grad.tif", g, nodata=-1)
    plain, prof = read_all(run(pro, R, "brightness_contrast", input="grad"))
    assert prof["dtype"] == "uint8" and prof["nodata"] == 0 and np.isnan(plain[0, 0, 0])   # no data kept apart from black
    assert np.nanmin(plain) >= 1 and np.nanmax(plain) == 255
    bright, _ = read_all(run(pro, R, "brightness_contrast", input="grad", brightness=30))
    lifted, _ = read_all(run(pro, R, "brightness_contrast", input="grad", gamma=2))
    flat, _ = read_all(run(pro, R, "brightness_contrast", input="grad", contrast=-80))
    mid = (5, 30)
    assert bright[0][mid] > plain[0][mid] + 50 and lifted[0][mid] > plain[0][mid] + 30
    assert np.nanstd(flat) < np.nanstd(plain) / 2


def test_sharpen_local_contrast_dehaze_match_and_denoise(pro, R):
    edge = np.zeros((N, N), "float32")
    edge[:, 30:] = 100
    up(pro, R, "edge.tif", edge)
    s, _ = read_all(run(pro, R, "unsharp_mask", input="edge", radius=2, amount=100))
    assert s[0, 30, 30] > 100 and s[0, 30, 29] < 0 and s[0, 30, 5] == pytest.approx(0) and s[0, 30, 55] == pytest.approx(100)   # overshoot at the edge only
    # local contrast: a faint pattern in a dark corner becomes visible
    low = np.full((N, N), 10, "float32") + rng.normal(0, 1, (N, N)).astype("float32")
    low[:, 30:] += 500
    up(pro, R, "low.tif", low)
    c, prof = read_all(run(pro, R, "clahe", input="low", tile=16, clip=0.03))
    assert prof["dtype"] == "uint8" and np.nanstd(c[0, :, :30]) > 3 * np.nanstd(low[:, :30] / 510 * 255)
    # haze: a veil over the west half (transmission 0.4 towards a pale sky), none over the east
    tex = rng.uniform(0, 0.8, (3, N, N))
    tex[:, ::4, ::4] = 0.01                                                            # dark objects in every patch, as in real scenes
    t = np.where(np.arange(N)[None, None, :] < 30, 0.4, 1.0)
    hazy = (tex * t + 0.9 * (1 - t)).astype("float32")
    up(pro, R, "hazy.tif", hazy)
    lay = run(pro, R, "dehaze", input="hazy", patch=7)
    d, _ = read_all(lay)
    ratio = lambda x: np.std(x[:, 5:55, 5:25]) / np.std(x[:, 5:55, 35:55])            # noqa: E731  (west contrast over east)
    assert ratio(hazy) < 0.45 and ratio(d) > 0.75
    assert lay["source"]["summary"]["haze_percent"] > 15
    # matching: the darker copy takes on the reference's levels
    up(pro, R, "dim.tif", (edge * 0.5 + 10).astype("float32"))
    m, _ = read_all(run(pro, R, "histogram_match", input="dim", reference="edge"))
    assert m[0, 0, 0] == pytest.approx(0, abs=1) and m[0, 0, 59] == pytest.approx(100, abs=1)
    # denoising: specks gone, edge kept
    noisy = edge.copy()
    noisy[10, 10], noisy[40, 45] = 100, 0
    up(pro, R, "noisy.tif", noisy)
    n, _ = read_all(run(pro, R, "denoise", input="noisy", method="median"))
    assert n[0, 10, 10] == 0 and n[0, 40, 45] == 100 and n[0, 20, 29] == 0 and n[0, 20, 30] == 100
    sar = (np.where(np.arange(N)[None, :] < 30, 0.1, 0.5) * rng.gamma(4, 0.25, (N, N))).astype("float32")
    up(pro, R, "sar.tif", sar)
    lee, _ = read_all(run(pro, R, "denoise", input="sar", method="lee", size=7))
    assert np.std(lee[0, 5:55, 5:25]) < 0.6 * np.std(sar[5:55, 5:25])
    tv, _ = read_all(run(pro, R, "denoise", input="sar", method="tv", weight=0.2))
    assert np.std(tv[0, 5:55, 5:25]) < np.std(sar[5:55, 5:25])
