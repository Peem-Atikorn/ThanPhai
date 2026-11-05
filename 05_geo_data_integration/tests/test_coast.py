import geopandas as gpd
from shapely.geometry import box

from geo_integration.coast import offshore_points


def test_offshore_points_only_outside_the_country():
    # Three districts in a row: the middle one is surrounded on two sides by its neighbours.
    areas = gpd.GeoDataFrame({"code": ["A", "B", "C"]},
                             geometry=[box(100.0, 13.0, 100.3, 13.3), box(100.3, 13.0, 100.6, 13.3), box(100.6, 13.0, 100.9, 13.3)],
                             crs="EPSG:4326")
    points = offshore_points(areas)
    country = areas.geometry.union_all()
    assert {code for code, _, _ in points} == {"A", "B", "C"}
    for code, lat, lon in points:
        assert not country.contains(gpd.points_from_xy([lon], [lat])[0])
        assert 13.0 - 0.25 < lat < 13.3 + 0.25 and 100.0 - 0.25 < lon < 100.9 + 0.25  # within ~20 km
    assert all(sum(1 for c, _, _ in points if c == code) <= 3 for code in "ABC")
