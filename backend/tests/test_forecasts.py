"""Forecasting scenarios, on series with a trend planted in them: rasters over time, and values per feature."""
import json

import numpy as np
import pytest

from test_rasters import pro  # noqa: F401  (fixture)
from test_scenarios import N, P, bands, ll, raster, run, scenario, vector  # noqa: F401


def test_raster_forecast_finds_the_trend_and_projects_it(pro, P):
    """NDVI falling by 0.02 a year in the west (clearing), rising by 0.01 in the east (regrowth), flat in the middle."""
    rng = np.random.default_rng(1)
    slope = np.zeros((N, N))
    slope[:, :15] = -0.02
    slope[:, 25:] = 0.01
    series = []
    for i, yr in enumerate(range(2015, 2024)):
        a = (0.6 + slope * (yr - 2015) + rng.normal(0, 0.003, (N, N))).astype("float32")
        series.append({"layer": raster(pro, P, f"ndvi{yr}", a)["id"], "date": str(yr)})
    s = scenario(pro, P, "raster_forecast", "NDVI trend", series=series, targets="2025, 2030", method="linear", min=-1, max=1)
    out = run(pro, s)
    x, names = bands(out["layer"])
    assert names == ["2025", "2030"]
    assert x[0, 20, 5] == pytest.approx(0.6 - 0.02 * 10, abs=0.01) and x[1, 20, 5] == pytest.approx(0.6 - 0.02 * 15, abs=0.01)
    assert x[1, 20, 35] == pytest.approx(0.6 + 0.01 * 15, abs=0.01) and x[1, 20, 20] == pytest.approx(0.6, abs=0.01)
    sm = out["scenario"]["summary"]
    cell_ha = 0.09
    assert sm["falling_ha"] == pytest.approx(15 * N * cell_ha, rel=0.02) and sm["rising_ha"] == pytest.approx(15 * N * cell_ha, rel=0.02)
    assert sm["observations"] == 9 and sm["steps"][1]["low_95"] < sm["steps"][1]["mean"] < sm["steps"][1]["high_95"]
    # robust to an outlier year (a cloudy scene), and clipped to the valid range
    series[3] = {**series[3]}
    bad = (np.full((N, N), -0.9)).astype("float32")
    series.append({"layer": raster(pro, P, "cloudy", bad)["id"], "date": "2019-07-01"})
    ts = scenario(pro, P, "raster_forecast", "Robust", series=series, targets=[2030], method="theil_sen", min=-1, max=1)
    y, _ = bands(run(pro, ts)["layer"])
    assert y[0, 20, 5] == pytest.approx(0.3, abs=0.03) and np.nanmin(y) >= -1
    # seasons: a yearly cycle is fitted, so a forecast in the dry season is lower than in the wet season
    seas = []
    for k in range(24):
        date = f"{2020 + k // 12}-{k % 12 + 1:02d}-15"
        frac = (k % 12 + 0.5) / 12
        v = 0.5 + 0.2 * np.sin(2 * np.pi * frac)
        seas.append({"layer": raster(pro, P, f"m{k}", np.full((10, 10), v, "float32"))["id"], "date": date})
    ss = scenario(pro, P, "raster_forecast", "Seasons", series=seas, targets="2023-04-01, 2023-10-01", method="seasonal")
    z, _ = bands(run(pro, ss)["layer"])
    assert z[0, 5, 5] == pytest.approx(0.5 + 0.2 * np.sin(2 * np.pi * 0.25), abs=0.03) and z[0, 5, 5] > z[1, 5, 5] + 0.3
    few = scenario(pro, P, "raster_forecast", "Too few", series=series[:2], targets=[2030])
    r = pro.post(f"/scenarios/{few['id']}/run")
    assert r.status_code == 422 and "three" in r.json()["detail"]


def everything(c, lid):
    return c.get(f"/layers/{lid}/features.geojson", params={"bbox": "-180,-90,180,90"}).json()["features"]


def test_value_forecast_for_districts(pro, P):
    """Population per district: one growing 3 % a year, one losing 100 people a year."""
    geoms = [{"type": "Point", "coordinates": ll(5, 5)}, {"type": "Point", "coordinates": ll(10, 10)}]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": geoms[0], "properties": {"name": "Ife East", "pop_2006": 10000, "pop_2011": round(10000 * 1.03 ** 5),
                                                                   "pop_2016": round(10000 * 1.03 ** 10), "pop_2021": round(10000 * 1.03 ** 15)}},
        {"type": "Feature", "geometry": geoms[1], "properties": {"name": "Ife North", "pop_2006": 5000, "pop_2011": 4500, "pop_2016": 4000, "pop_2021": 3500}}]}
    lay = pro.post(f"/projects/{P['id']}/layers/import", files={"file": ("districts.geojson", json.dumps(fc).encode())}).json()[0]
    s = scenario(pro, P, "value_forecast", "Population", layer=lay["id"], fields=["pop_2006", "pop_2011", "pop_2016", "pop_2021"],
                 targets="2031, 2041", method="exponential", prefix="pop")
    out = run(pro, s)
    l2 = out["layer"]
    assert l2["geometry_type"] == "Point" and l2["feature_count"] == 2
    names = [f["name"] for f in l2["fields"]]
    assert {"pop_2031", "pop_2041", "growth_pct_per_year", "fit_r2"} <= set(names)
    feats = everything(pro, l2["id"])
    east = next(f for f in feats if f["properties"]["name"] == "Ife East")["properties"]
    assert east["pop_2031"] == pytest.approx(10000 * 1.03 ** 25, rel=0.01) and east["growth_pct_per_year"] == pytest.approx(3, abs=0.05)
    assert east["fit_r2"] > 0.99
    st = out["scenario"]["summary"]
    assert [x["label"] for x in st["steps"]] == ["2031", "2041"] and len(st["step_styles"]) == 2
    assert l2["style"]["type"] == "graduated" and l2["style"]["field"] == "pop_2041"
    # a straight line for the shrinking district, never below zero
    s2 = scenario(pro, P, "value_forecast", "Linear", layer=lay["id"], fields=["pop_2006", "pop_2011", "pop_2016", "pop_2021"], targets=[2031, 2071])
    o2 = run(pro, s2)
    f2 = everything(pro, o2["layer"]["id"])
    north = next(f for f in f2 if f["properties"]["name"] == "Ife North")["properties"]
    assert north["forecast_2031"] == pytest.approx(2500, abs=1) and north["forecast_2071"] == 0
    # re-running replaces the features in the same layer
    again = run(pro, s2)
    assert again["layer"]["id"] == o2["layer"]["id"] and again["layer"]["feature_count"] == 2
    one = scenario(pro, P, "value_forecast", "One year", layer=lay["id"], fields=["pop_2006"], targets=[2031])
    r = pro.post(f"/scenarios/{one['id']}/run")
    assert r.status_code == 422 and "two fields" in r.json()["detail"]
