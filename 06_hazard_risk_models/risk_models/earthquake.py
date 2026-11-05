"""Expected shaking (Modified Mercalli Intensity) at a place from an earthquake's magnitude, depth and distance.

Allen, Wald & Worden (2012), "Intensity attenuation for active crustal regions", J. Seismology 16: 409-433,
hypocentral-distance form with the coefficients OpenQuake uses (gsim AllenEtAl2012Rhypo).

Limits: a point source, so very large quakes (M 7+, rupture hundreds of km long) are underestimated near the
fault; no site effect, so soft-soil basins (Bangkok clay) and tall buildings can feel one or two levels more.
"""

import math

C0, C1, C2, C4, M1, M2 = 2.085, 1.428, -1.402, 0.078, -0.209, 2.042
EARTH_RADIUS_KM = 6371.0

# What people feel at each intensity (USGS MMI scale), short Thai.
MMI_TH = {
    1: "ไม่รู้สึก", 2: "รู้สึกได้เล็กน้อย", 3: "รู้สึกได้ในอาคาร", 4: "รู้สึกได้ชัด ของในบ้านสั่น",
    5: "รู้สึกได้แทบทุกคน ของเล็กล้ม", 6: "สั่นแรง อาคารเสียหายเล็กน้อย", 7: "สั่นแรงมาก อาคารเสียหายปานกลาง",
    8: "อาคารเสียหายมาก", 9: "อาคารพังเสียหายรุนแรง", 10: "เสียหายรุนแรงมาก",
}


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle (haversine) distance."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def mmi(magnitude: float, hypocentral_km: float) -> float:
    """Mean MMI; values below 1 are clipped to 1 (not felt)."""
    r_m = M1 + M2 * math.exp(magnitude - 5.0)
    value = C0 + C1 * magnitude + C2 * math.log(math.sqrt(hypocentral_km ** 2 + r_m ** 2))
    if hypocentral_km > 50.0:
        value += C4 * math.log(hypocentral_km / 50.0)
    return max(1.0, value)


def shaking_at(lat: float, lon: float, quake_lat: float, quake_lon: float, magnitude: float, depth_km: float | None) -> tuple[float, float]:
    """(MMI, epicentral distance in km) at a place. Unknown depth counts as 10 km, the usual shallow default."""
    epicentral = distance_km(lat, lon, quake_lat, quake_lon)
    depth = depth_km if depth_km is not None and depth_km >= 0 else 10.0
    return mmi(magnitude, math.hypot(epicentral, depth)), epicentral


def mmi_label_th(value: float) -> str:
    return MMI_TH[min(10, max(1, round(value)))]
