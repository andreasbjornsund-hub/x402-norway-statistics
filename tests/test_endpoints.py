"""End-to-end-ish tests for the public HTTP handlers (with upstreams stubbed)."""
import pytest
from fastapi import HTTPException, Response


def _exchange_payload(rates):
    """Build a minimal SDMX payload for given list of (date, rate) tuples."""
    obs = {str(i): [r] for i, (_, r) in enumerate(rates)}
    return {
        "data": {
            "dataSets": [{"series": {"0:0:0:0": {"observations": obs}}}],
            "structure": {"dimensions": {"observation": [{
                "id": "TIME_PERIOD",
                "values": [{"id": d} for d, _ in rates],
            }]}},
        }
    }


def _ssb_simple_series(times, values, time_dim="Tid", content_code=None):
    """Fake JSON-stat dataset.

    If `content_code` is given, adds a single-value ContentsCode dimension so
    handlers that filter by ContentsCode see the values. The handler-side
    filters were added after the production /cpi bug where SSB returned 4
    parallel series and the weight series (=1000) leaked into `current_index`.
    """
    if content_code:
        return {
            "version": "2.0", "class": "dataset",
            "id": ["ContentsCode", time_dim],
            "size": [1, len(times)],
            "value": values,
            "dimension": {
                "ContentsCode": {"category": {
                    "index": {content_code: 0},
                    "label": {content_code: content_code},
                }},
                time_dim: {"category": {
                    "index": {t: i for i, t in enumerate(times)},
                    "label": {t: t for t in times},
                }},
            },
        }
    return {
        "version": "2.0", "class": "dataset",
        "id": [time_dim], "size": [len(times)], "value": values,
        "dimension": {
            time_dim: {"category": {
                "index": {t: i for i, t in enumerate(times)},
                "label": {t: t for t in times},
            }},
        },
    }


# ── Free endpoints ──────────────────────────────────────────────────


async def test_health(main_module):
    r = await main_module.health()
    assert r["status"] == "ok"
    assert r["service"] == "norway-statistics"


async def test_currencies_list(main_module):
    r = await main_module.list_currencies()
    assert r["count"] >= 30
    assert any(c["code"] == "USD" for c in r["currencies"])


async def test_municipalities_list(main_module):
    r = await main_module.list_municipalities()
    assert r["count"] >= 30
    assert any(m["code"] == "0301" for m in r["municipalities"])


async def test_api_status_includes_cache(main_module):
    r = await main_module.api_status()
    assert "data.ssb.no" in r["upstreams"]
    for k in ("entries", "fresh", "max"):
        assert k in r["cache"]


# ── Manifest contract ───────────────────────────────────────────────


async def test_x402_manifest_paid_count(main_module):
    r = await main_module.x402_manifest()
    paid = [e for e in r["endpoints"] if e["accepts"]]
    free = [e for e in r["endpoints"] if not e["accepts"]]
    assert len(paid) == 8
    assert len(free) == 4  # /currencies, /municipalities, /health, /api-status


async def test_x402_payTo_matches_env(main_module):
    import os
    r = await main_module.x402_manifest()
    assert r["payment"]["payTo"] == os.environ["EVM_ADDRESS"]


async def test_atomic_amounts_match_price(main_module):
    for e in main_module.ENDPOINT_CATALOG:
        if e["price_usd"] is None:
            continue
        usd = float(e["price_usd"].replace("$", ""))
        expected = str(int(round(usd * 10**6)))
        assert e["amount_atomic"] == expected, (
            f"{e['path']}: ${usd} → expected {expected}, got {e['amount_atomic']}"
        )


# ── Norges Bank handlers ────────────────────────────────────────────


async def test_exchange_happy_path(main_module, fake_http):
    fake_http.stub_get("/EXR/B.USD.NOK.SP", 200, _exchange_payload([
        ("2026-05-07", 10.42), ("2026-05-08", 10.45),
    ]))
    out = await main_module.exchange(response=Response(), from_="USD", days=7)
    assert out["pair"] == "USD/NOK"
    assert out["rate"] == 10.45
    assert out["date"] == "2026-05-08"
    assert out["history"][0]["rate"] == 10.42


async def test_exchange_unsupported_currency(main_module):
    with pytest.raises(HTTPException) as exc:
        await main_module.exchange(response=Response(), from_="XYZ", days=7)
    assert exc.value.status_code == 400


async def test_exchange_rejects_nok(main_module):
    with pytest.raises(HTTPException) as exc:
        await main_module.exchange(response=Response(), from_="NOK", days=7)
    assert exc.value.status_code == 400


async def test_exchange_503_on_upstream(main_module, fake_http):
    fake_http.stub_get("/EXR/B.USD.NOK.SP", 502)
    with pytest.raises(HTTPException) as exc:
        await main_module.exchange(response=Response(), from_="USD", days=7)
    assert exc.value.status_code == 503


async def test_exchange_convert_via_nok_pivot(main_module, fake_http):
    fake_http.stub_get("/EXR/B.USD.NOK.SP", 200, _exchange_payload([("2026-05-08", 10.0)]))
    fake_http.stub_get("/EXR/B.EUR.NOK.SP", 200, _exchange_payload([("2026-05-08", 12.0)]))
    out = await main_module.exchange_convert(response=Response(), amount=120, from_="USD", to="EUR")
    assert out["from"] == "USD" and out["to"] == "EUR"
    # 120 USD * (10/12 EUR/USD) = 100 EUR
    assert abs(out["result"] - 100.0) < 0.01


async def test_exchange_convert_nok_passthrough(main_module, fake_http):
    fake_http.stub_get("/EXR/B.USD.NOK.SP", 200, _exchange_payload([("2026-05-08", 10.0)]))
    out = await main_module.exchange_convert(response=Response(), amount=10, from_="USD", to="NOK")
    assert out["result"] == 100.0


async def test_policy_rate_happy_path(main_module, fake_http):
    fake_http.stub_get("/IR/B.KPRA.", 200, _exchange_payload([
        ("2024-12-19", 4.50), ("2025-03-20", 4.50),
    ]))
    out = await main_module.policy_rate(response=Response(), days=365)
    assert out["current_rate"] == 4.50
    assert out["effective_date"] == "2025-03-20"
    assert len(out["history"]) == 2


# ── SSB handlers ────────────────────────────────────────────────────


async def test_population_happy_path(main_module, fake_http):
    fake_http.stub_post("/table/07459", 200, _ssb_simple_series(
        ["2023", "2024", "2025"], [690000, 700000, 709037]
    ))
    out = await main_module.population(response=Response(), municipality="oslo")
    assert out["municipality"] == "Oslo"
    assert out["code"] == "0301"
    assert out["population"] == 709037
    assert out["year"] == "2025"
    assert len(out["history"]) == 2


async def test_population_unknown_municipality(main_module):
    with pytest.raises(HTTPException) as exc:
        await main_module.population(response=Response(), municipality="paris")
    assert exc.value.status_code == 404


async def test_cpi_happy_path(main_module, fake_http):
    months = [f"2025M{m:02d}" for m in range(1, 13)] + [f"2026M{m:02d}" for m in range(1, 5)]
    values = [130 + i * 0.3 for i in range(len(months))]
    fake_http.stub_post("/table/03013", 200, _ssb_simple_series(months, values, content_code="KpiIndMnd"))
    out = await main_module.cpi(response=Response(), months=12)
    assert out["month"] == months[-1]
    assert out["current_index"] == values[-1]
    # YoY against month -13 (12 months back)
    assert out["inflation_yoy_pct"] is not None


async def test_housing_returns_quarterly(main_module, fake_http):
    quarters = [f"2024K{q}" for q in range(1, 5)] + [f"2025K{q}" for q in range(1, 5)]
    values = [300 + i for i in range(len(quarters))]
    fake_http.stub_post("/table/07241", 200, _ssb_simple_series(quarters, values, content_code="KvPris"))
    out = await main_module.housing(response=Response(), quarters=4)
    assert out["price_index"] == values[-1]
    assert len(out["quarterly"]) == 4


async def test_unemployment_handler(main_module, fake_http):
    months = [f"2026M{m:02d}" for m in range(1, 5)]
    values = [3.7, 3.8, 3.8, 3.9]
    fake_http.stub_post("/table/08517", 200, _ssb_simple_series(months, values, content_code="Prosent"))
    out = await main_module.unemployment(response=Response(), months=4)
    assert out["rate_pct"] == 3.9


async def test_gdp_handler(main_module, fake_http):
    quarters = [f"2025K{q}" for q in range(1, 5)] + [f"2026K{q}" for q in range(1, 3)]
    values = [1_000_000 + i * 5_000 for i in range(len(quarters))]
    fake_http.stub_post("/table/09190", 200, _ssb_simple_series(quarters, values, content_code="Faste"))
    out = await main_module.gdp(response=Response(), quarters=4)
    assert out["gdp_value"] == values[-1]
