from types import SimpleNamespace

from fastapi.testclient import TestClient

import api_backend.main as main
from geo_integration import Area, Gazetteer


def test_search_suggests_partial_names_without_boundary_files(monkeypatch):
    gazetteer = Gazetteer([
        Area("TH50", "province", "เชียงใหม่", "Chiang Mai", None, 18.8, 99.0, 1000),
        Area("TH5001", "amphoe", "เมืองเชียงใหม่", "Mueang Chiang Mai", "TH50", 18.8, 99.0, 100),
        Area("TH57", "province", "เชียงราย", "Chiang Rai", None, 19.9, 99.8, 1000),
    ])
    monkeypatch.setattr(main, "service", SimpleNamespace(gazetteer=gazetteer))
    client = TestClient(main.app)
    hits = client.get("/v1/areas/search", params={"q": "เชียงให"}).json()
    assert any(a["name_th"] == "เชียงใหม่" and a["level"] == "province" for a in hits)
    provinces = client.get("/v1/areas/search", params={"q": "จังหวัดเชียง"}).json()
    assert provinces and {a["code"] for a in provinces} == {"TH50", "TH57"}
    assert all(a["level"] == "province" for a in provinces)
