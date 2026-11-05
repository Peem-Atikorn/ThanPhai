import geopandas as gpd
import pytest


def test_dmr_inventory_utm_zone_47_reaches_the_east():
    # DMR writes zone 47N coordinates even for Trat (zone 48 by longitude); the first inventory row is in Bo Rai, Trat.
    point = gpd.GeoSeries(gpd.points_from_xy([894434], [1385348]), crs="EPSG:32647").to_crs("EPSG:4326")[0]
    assert point.y == pytest.approx(12.5, abs=0.2) and point.x == pytest.approx(102.6, abs=0.2)
