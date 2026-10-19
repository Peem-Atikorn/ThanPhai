from datetime import datetime, timezone

import httpx
import pytest
import respx

from external_data.adapters.gistda import BASE, GistdaFloodCells

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
URL = f"{BASE}/flood/7days"


def matched(n):
    # Shape of a real reply, including the link that echoes the key (must never leak out of the adapter).
    return httpx.Response(200, json={"features": [], "numberMatched": n,
                                     "links": [{"href": f"{URL}?api_key=SECRET", "rel": "self"}]})


@respx.mock
async def test_counts_only_districts_in_flooded_provinces():
    def reply(request):
        p = request.url.params
        if "pv_idn" in p:
            return matched(11053 if p["pv_idn"] == "14" else 0)
        return matched({"1412": 1558, "1401": 0}[p["ap_idn"]])

    route = respx.get(URL).mock(side_effect=reply)
    adapter = GistdaFloodCells(["TH14", "TH50"], ["TH1412", "TH1401", "TH5001"], api_key="k")
    async with httpx.AsyncClient() as client:
        raw = await adapter.fetch(client)
    assert raw["counts"] == {"5001": 0, "1412": 1558, "1401": 0}
    assert route.call_count == 4  # 2 provinces + 2 districts in the flooded province only
    assert all(c.request.headers["api-key"] == "k" for c in route.calls)
    assert all(c.request.url.params["limit"] == "1" for c in route.calls)

    records = adapter.parse(raw, NOW)
    sena = next(r for r in records if r.extra["area_code"] == "TH1412")
    assert sena.value == 1558 and sena.extra["approx_km2"] == pytest.approx(194.0, abs=0.1)
    assert "SECRET" not in repr(raw) + repr(records)


@respx.mock
async def test_one_failing_province_leaves_its_districts_unknown_not_zero():
    def reply(request):
        p = request.url.params
        if p.get("pv_idn") == "50":
            return httpx.Response(504)
        return matched(0)

    respx.get(URL).mock(side_effect=reply)
    async with httpx.AsyncClient() as client:
        raw = await GistdaFloodCells(["TH14", "TH50"], ["TH1412", "TH5001"], api_key="k").fetch(client)
    assert raw["counts"] == {"1412": 0}  # Chiang Mai district absent = unknown, not "no flood"
    assert raw["failed"] == ["5001"]


@respx.mock
async def test_rejected_key():
    respx.get(URL).mock(return_value=httpx.Response(407, json={"status": 407}))
    with pytest.raises(PermissionError):
        async with httpx.AsyncClient() as client:
            await GistdaFloodCells(["TH14"], ["TH1412"], api_key="bad").fetch(client)


async def test_missing_key_and_bad_window():
    with pytest.raises(ValueError):
        GistdaFloodCells([], [], window="2days")
    with pytest.raises(ValueError):
        async with httpx.AsyncClient() as client:
            await GistdaFloodCells(["TH14"], [], api_key="").fetch(client)
