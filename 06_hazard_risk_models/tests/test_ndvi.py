import geopandas as gpd
import numpy as np
from shapely.geometry import box

from risk_models.drought.ndvi import PIXEL_M, SINU, district_index, district_means, tile_transform, vci


def test_tile_transform_matches_modis_grid():
    t = tile_transform("h27v07")
    # h27v07 upper-left corner in metres (MODIS sinusoidal), pixel ~926.6 m.
    assert round(t.c) == 10007555 and round(t.f) == 2223901 and abs(t.a - 926.625) < 0.001


def test_district_index_puts_a_district_on_the_right_pixels():
    # A small box in Bangkok, drawn in lon/lat, must land inside tile h27v07.
    amphoe = gpd.GeoDataFrame({"code": ["TH1001"]}, geometry=[box(100.49, 13.74, 100.52, 13.77)], crs="EPSG:4326")
    idx = district_index(amphoe, "h27v07")
    hit = (idx == 0).sum()
    # ~3.3 km x 3.3 km box -> roughly 10-14 pixels of 0.93 km.
    assert 6 <= hit <= 20 and set(np.unique(idx)) == {-1, 0}
    assert (district_index(amphoe, "h28v08") == 0).sum() == 0  # not in a far-away tile


def test_district_means_ignore_cloud_and_need_enough_pixels():
    idx = np.full((4, 10), -1)
    idx[0, :] = 0          # 10 pixels for district 0
    idx[1, :5] = 1         # 5 pixels for district 1 (too few with min 20)
    ndvi = np.full((4, 10), 0.5, dtype=np.float32)
    ndvi[0, :2] = np.nan   # cloudy
    import risk_models.drought.ndvi as mod

    old = mod.MIN_GOOD_PIXELS
    mod.MIN_GOOD_PIXELS = 6
    try:
        means = district_means({"t": ndvi}, {"t": idx}, 2)
    finally:
        mod.MIN_GOOD_PIXELS = old
    assert means[0] == np.float32(0.5) and np.isnan(means[1])


def test_vci_scales_between_past_min_and_max():
    history = np.array([[0.4, 0.6], [0.6, 0.6], [0.5, 0.6], [0.45, 0.6], [0.55, 0.6]])
    r = vci(np.array([0.45, 0.6]), history)
    assert abs(r.vci[0] - 25.0) < 1e-6
    assert np.isnan(r.vci[1])  # no variation in history -> not meaningful


def test_deficit_shows_whether_the_drop_is_real():
    # Rainy-season case: green land, tiny range. VCI says 0 but the deficit is only ~0.02.
    history = np.array([[0.812], [0.844], [0.83], [0.835], [0.84]])
    r = vci(np.array([0.811]), history)
    assert r.vci[0] == 0.0 and abs(r.deficit[0] - 0.0212) < 0.001  # mean 0.8322 - 0.811


def test_vci_is_clipped_and_needs_enough_years():
    history = np.array([[0.4], [0.6], [0.5], [0.45], [0.55]])
    assert vci(np.array([0.3]), history).vci[0] == 0.0
    assert vci(np.array([0.9]), history).vci[0] == 100.0
    assert np.isnan(vci(np.array([0.5]), history[:3]).vci[0])  # only 3 years


def test_sinusoidal_definition_is_modis():
    assert "+R=6371007.181" in SINU and PIXEL_M > 926
