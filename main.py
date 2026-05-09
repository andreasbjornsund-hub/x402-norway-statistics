"""
x402-norway-statistics — Norwegian Statistics & Economics

x402 micropayment API combining SSB (Statistics Norway) and Norges Bank
data into a single agent-friendly service.

Endpoints (free):
  GET /                       — landing page (HTML or JSON)
  GET /health                 — health check
  GET /api-status             — uptime + cache shape
  GET /currencies             — list supported currencies
  GET /municipalities         — list ~50 Norwegian municipalities + codes
  GET /services.json          — agent-readable services manifest
  GET /llms.txt               — LLMs.txt for AI crawlers
  GET /robots.txt             — robots policy
  GET /.well-known/x402.json  — x402 agent-discovery manifest

Endpoints (paid, USDC on Base):
  GET /exchange               $0.005   USD/NOK rate + history
  GET /exchange/convert       $0.005   amount conversion at latest rate
  GET /policy-rate            $0.005   Norges Bank key policy rate
  GET /population             $0.01    SSB municipality population
  GET /cpi                    $0.01    SSB consumer price index
  GET /housing                $0.01    SSB house-price index
  GET /unemployment           $0.01    SSB unemployment
  GET /gdp                    $0.01    SSB quarterly national accounts

Data: Statistics Norway (data.ssb.no) and Norges Bank (data.norges-bank.no).
Both free, no API keys. SSB cached 24 h, exchange rates 1 h, policy rate 6 h.
"""
import os
import time
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import cache
import currencies
import jsonstat
import municipalities
import norges_bank_client as nb
import ssb_client as ssb

from cdp_auth import create_cdp_auth_provider

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.schemas import Network
from x402.server import x402ResourceServer

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────

SERVICE_ID = "norway-statistics"
SERVICE_NAME = "Norwegian Statistics & Economics"
SERVICE_DESCRIPTION = (
    "Exchange rates, population, housing prices, CPI, GDP — all from official "
    "Norwegian sources (SSB and Norges Bank). Pay per query with USDC via x402."
)
SERVICE_CATEGORY = "data"

EVM_ADDRESS = os.getenv("EVM_ADDRESS")
EVM_NETWORK: Network = "eip155:8453"
FACILITATOR_URL = os.getenv("FACILITATOR_URL", "https://x402.org/facilitator")
SITE_URL = os.getenv("SITE_URL", "https://x402-norway-statistics.fly.dev")
USDC_BASE_MAINNET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

TTL_RATES = int(os.getenv("TTL_RATES", str(60 * 60)))
TTL_POLICY = int(os.getenv("TTL_POLICY", str(6 * 60 * 60)))
TTL_SSB = int(os.getenv("TTL_SSB", str(24 * 60 * 60)))

if not EVM_ADDRESS:
    raise ValueError("Set EVM_ADDRESS in .env")

# ── FastAPI app ─────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await _http.aclose()


app = FastAPI(
    title=SERVICE_NAME,
    description=SERVICE_DESCRIPTION,
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

import json as _json

cdp_auth = None
if "cdp.coinbase.com" in FACILITATOR_URL:
    cdp_auth = create_cdp_auth_provider()
facilitator_config = FacilitatorConfig(url=FACILITATOR_URL, auth_provider=cdp_auth)
facilitator = HTTPFacilitatorClient(facilitator_config)

_CAIP2_TO_V1 = {"eip155:8453": "base", "eip155:84532": "base-sepolia"}


def _v2_payload_to_v1(payload_dict: dict) -> dict:
    v1 = {"x402Version": 1}
    v1["scheme"] = payload_dict.get("scheme", "exact")
    raw_net = payload_dict.get("network", EVM_NETWORK)
    v1["network"] = _CAIP2_TO_V1.get(raw_net, raw_net)
    v1["payload"] = payload_dict.get("payload", payload_dict)
    return v1


def _v2_requirements_to_v1(req_dict: dict) -> dict:
    raw_net = req_dict.get("network", EVM_NETWORK)
    extra = req_dict.get("extra", {})
    if isinstance(extra, str):
        try:
            extra = _json.loads(extra)
        except Exception:
            extra = {}
    v1 = {
        "scheme": req_dict.get("scheme", "exact"),
        "network": _CAIP2_TO_V1.get(raw_net, raw_net),
        "maxAmountRequired": req_dict.get("amount", req_dict.get("maxAmountRequired", "0")),
        "resource": req_dict.get("resource", ""),
        "description": req_dict.get("description", ""),
        "mimeType": req_dict.get("mimeType", req_dict.get("mime_type", "application/json")),
        "asset": req_dict.get("asset", ""),
        "payTo": req_dict.get("payTo", req_dict.get("pay_to", "")),
        "maxTimeoutSeconds": req_dict.get("maxTimeoutSeconds", req_dict.get("max_timeout_seconds", 300)),
        "extra": extra,
    }
    extensions = req_dict.get("extensions", {})
    bazaar = extensions.get("bazaar", {})
    if bazaar.get("info"):
        v1["outputSchema"] = bazaar["info"]
    return v1


_orig_verify = facilitator._verify_http
_orig_settle = facilitator._settle_http


async def _v1_verify(version, payload_dict, requirements_dict):
    return await _orig_verify(1, _v2_payload_to_v1(payload_dict), _v2_requirements_to_v1(requirements_dict))


async def _v1_settle(version, payload_dict, requirements_dict):
    return await _orig_settle(1, _v2_payload_to_v1(payload_dict), _v2_requirements_to_v1(requirements_dict))


facilitator._verify_http = _v1_verify
facilitator._settle_http = _v1_settle

server = x402ResourceServer(facilitator)
server.register(EVM_NETWORK, ExactEvmServerScheme())

# ── Endpoint catalog ────────────────────────────────────────────────

ENDPOINT_CATALOG: list[dict] = [
    {
        "method": "GET",
        "path": "/exchange/convert",
        "route_pattern": "GET /exchange/convert",
        "description": "Convert an amount between two currencies using the latest Norges Bank rates.",
        "price_usd": "$0.005",
        "amount_atomic": "5000",
        "query_params": {"amount": 100, "from": "USD", "to": "NOK"},
        "path_params": {},
        "output_example": {"amount": 100, "from": "USD", "to": "NOK", "rate": 10.45, "result": 1045.00, "date": "2026-05-08"},
    },
    {
        "method": "GET",
        "path": "/exchange",
        "route_pattern": "GET /exchange",
        "description": "Current and historical CURRENCY/NOK exchange rates from Norges Bank. Optional ?days= for a longer history.",
        "price_usd": "$0.005",
        "amount_atomic": "5000",
        "query_params": {"from": "USD", "days": 30},
        "path_params": {},
        "output_example": {"pair": "USD/NOK", "rate": 10.45, "date": "2026-05-08", "history": [{"date": "2026-05-07", "rate": 10.42}]},
    },
    {
        "method": "GET",
        "path": "/policy-rate",
        "route_pattern": "GET /policy-rate",
        "description": "Norges Bank key policy rate (KPRA) — current value plus recent history.",
        "price_usd": "$0.005",
        "amount_atomic": "5000",
        "query_params": {"days": 365},
        "path_params": {},
        "output_example": {"current_rate": 4.50, "effective_date": "2024-12-19", "history": [{"date": "2024-12-19", "rate": 4.50}]},
    },
    {
        "method": "GET",
        "path": "/population",
        "route_pattern": "GET /population",
        "description": "Population for a Norwegian municipality (by name or 4-digit code) from SSB table 07459.",
        "price_usd": "$0.01",
        "amount_atomic": "10000",
        "query_params": {"municipality": "oslo"},
        "path_params": {},
        "output_example": {"municipality": "Oslo", "code": "0301", "population": 709037, "year": "2025", "history": [{"year": "2024", "population": 700637}]},
    },
    {
        "method": "GET",
        "path": "/cpi",
        "route_pattern": "GET /cpi",
        "description": "Consumer Price Index (KPI) total — current index, year-over-year inflation, monthly history.",
        "price_usd": "$0.01",
        "amount_atomic": "10000",
        "query_params": {"months": 12},
        "path_params": {},
        "output_example": {"current_index": 134.2, "month": "2026M04", "inflation_yoy_pct": 3.1, "monthly": [{"month": "2026M04", "index": 134.2}]},
    },
    {
        "method": "GET",
        "path": "/housing",
        "route_pattern": "GET /housing",
        "description": "House-price index from SSB table 07241. Quarterly.",
        "price_usd": "$0.01",
        "amount_atomic": "10000",
        "query_params": {"quarters": 8},
        "path_params": {},
        "output_example": {"region": "Hele landet", "price_index": 312.4, "change_yoy_pct": 2.8, "quarterly": [{"quarter": "2026K1", "index": 312.4}]},
    },
    {
        "method": "GET",
        "path": "/unemployment",
        "route_pattern": "GET /unemployment",
        "description": "Registered unemployment from SSB table 08517. Monthly.",
        "price_usd": "$0.01",
        "amount_atomic": "10000",
        "query_params": {"months": 12},
        "path_params": {},
        "output_example": {"region": "Hele landet", "rate_pct": 3.8, "month": "2026M04", "monthly": [{"month": "2026M04", "rate_pct": 3.8}]},
    },
    {
        "method": "GET",
        "path": "/gdp",
        "route_pattern": "GET /gdp",
        "description": "Quarterly GDP from SSB national accounts table 09190.",
        "price_usd": "$0.01",
        "amount_atomic": "10000",
        "query_params": {"quarters": 8},
        "path_params": {},
        "output_example": {"gdp_value": 1050000, "quarter": "2026K1", "growth_yoy_pct": 1.4, "quarterly": [{"quarter": "2026K1", "value": 1050000}]},
    },
    {"method": "GET", "path": "/currencies", "route_pattern": None,
     "description": "List of supported currencies (free).", "price_usd": None, "amount_atomic": None,
     "query_params": {}, "path_params": {}, "output_example": None},
    {"method": "GET", "path": "/municipalities", "route_pattern": None,
     "description": "List of supported Norwegian municipalities and 4-digit codes (free).",
     "price_usd": None, "amount_atomic": None, "query_params": {}, "path_params": {}, "output_example": None},
    {"method": "GET", "path": "/health", "route_pattern": None,
     "description": "Service health check.", "price_usd": None, "amount_atomic": None,
     "query_params": {}, "path_params": {}, "output_example": {"status": "ok"}},
    {"method": "GET", "path": "/api-status", "route_pattern": None,
     "description": "Operational status — uptime and upstream-cache shape.",
     "price_usd": None, "amount_atomic": None, "query_params": {}, "path_params": {}, "output_example": None},
]


def _bazaar_info(entry: dict) -> dict:
    inp = {"type": "http", "method": entry["method"]}
    if entry["query_params"]:
        inp["queryParams"] = entry["query_params"]
    if entry["path_params"]:
        inp["pathParams"] = entry["path_params"]
    return {
        "info": {"input": inp, "output": {"type": "json", "example": entry["output_example"]}},
        "schema": {"$schema": "https://json-schema.org/draft/2020-12/schema",
                   "type": "object",
                   "properties": {"input": {"type": "object"}, "output": {"type": "object"}}},
    }


def _build_paid_routes(catalog: list[dict]) -> dict[str, RouteConfig]:
    return {
        e["route_pattern"]: RouteConfig(
            accepts=[PaymentOption(scheme="exact", pay_to=EVM_ADDRESS, price=e["price_usd"], network=EVM_NETWORK)],
            mime_type="application/json",
            description=e["description"],
            extensions={"bazaar": _bazaar_info(e)},
        )
        for e in catalog if e["route_pattern"] is not None
    }


routes = _build_paid_routes(ENDPOINT_CATALOG)
app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)
# Outer middleware that polishes 402 responses to match the x402 spec:
# JSON payload in body, https:// in resource.url (Fly TLS proxy fix),
# CORS headers, and an x-payment-required v1 fallback. Must be
# registered AFTER PaymentMiddlewareASGI so it wraps it.
from x402_polish import X402ResponsePolish  # noqa: E402
app.add_middleware(X402ResponsePolish)

# ── Shared HTTP client ──────────────────────────────────────────────

_http = httpx.AsyncClient(timeout=30, headers={"Accept": "application/json"})

_PROCESS_START_TS = time.time()


# ── Discovery / metadata endpoints ──────────────────────────────────


@app.get("/")
async def landing(request: Request):
    accept = request.headers.get("accept", "")
    if "text/html" in accept and os.path.isfile("static/index.html"):
        return FileResponse("static/index.html")
    return {
        "service": SERVICE_NAME, "version": "0.1.0", "description": SERVICE_DESCRIPTION,
        "endpoints": {e["path"]: f"{e['description']} ({e['price_usd']} USDC)" if e["price_usd"]
                      else f"{e['description']} (free)"
                      for e in ENDPOINT_CATALOG} | {"/.well-known/x402.json": "Agent discovery"},
        "payment": "x402 protocol — USDC on Base network",
        "data_sources": ["SSB (data.ssb.no)", "Norges Bank (data.norges-bank.no)"],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_ID, "timestamp": int(time.time())}


@app.get("/api-status")
async def api_status():
    return {
        "status": "ok", "service": SERVICE_ID, "version": "0.1.0",
        "uptime_seconds": int(time.time() - _PROCESS_START_TS),
        "upstreams": ["data.ssb.no", "data.norges-bank.no"],
        "cache": cache.stats(),
    }


@app.get("/currencies")
async def list_currencies():
    return {"count": len(currencies.CURRENCIES), "currencies": currencies.CURRENCIES}


@app.get("/municipalities")
async def list_municipalities():
    rows = municipalities.all_municipalities()
    return {"count": len(rows), "municipalities": rows}


@app.get("/services.json")
async def services_manifest():
    return {
        "id": SERVICE_ID, "name": SERVICE_NAME, "description": SERVICE_DESCRIPTION,
        "category": SERVICE_CATEGORY, "x402Version": 2, "networks": [EVM_NETWORK],
        "website": SITE_URL,
        "endpoints": [{"method": e["method"], "path": e["path"], "description": e["description"],
                       "price": e["price_usd"] or "$0.00", "currency": "USDC"}
                      for e in ENDPOINT_CATALOG],
    }


@app.get("/.well-known/x402.json")
async def x402_manifest():
    return {
        "x402Version": 2,
        "service": {"id": SERVICE_ID, "name": SERVICE_NAME, "description": SERVICE_DESCRIPTION,
                    "category": SERVICE_CATEGORY, "website": SITE_URL,
                    "documentation": f"{SITE_URL}/llms.txt",
                    "servicesManifest": f"{SITE_URL}/services.json"},
        "payment": {"schemes": ["exact"], "networks": [EVM_NETWORK],
                    "asset": {"symbol": "USDC", "decimals": 6, "address": USDC_BASE_MAINNET, "chain": "Base"},
                    "payTo": EVM_ADDRESS, "facilitator": FACILITATOR_URL},
        "endpoints": [
            {"method": e["method"], "path": e["path"], "description": e["description"],
             "accepts": [{"scheme": "exact", "network": EVM_NETWORK, "asset": "USDC",
                          "amount": e["amount_atomic"], "amountDisplay": e["price_usd"], "payTo": EVM_ADDRESS}]
                        if e["amount_atomic"] else [],
             "input": {"type": "http", "method": e["method"],
                       **({"queryParams": e["query_params"]} if e["query_params"] else {}),
                       **({"pathParams": e["path_params"]} if e["path_params"] else {})},
             "output": ({"type": "json", "example": e["output_example"]}
                        if e["output_example"] is not None else {"type": "json"})}
            for e in ENDPOINT_CATALOG
        ],
    }


@app.get("/llms.txt")
async def llms_txt():
    lines = [f"# {SERVICE_NAME}", f"> {SERVICE_DESCRIPTION}", "", "## Endpoints"]
    for e in ENDPOINT_CATALOG:
        price = f"{e['price_usd']} USDC" if e["price_usd"] else "Free"
        lines.append(f"- {e['method']} {e['path']} — {price} — {e['description']}")
    lines += [
        "", "## Payment",
        "- Protocol: x402 (HTTP 402 micropayments)",
        "- Currency: USDC on Base",
        "- No API keys or accounts needed",
        "- Agent discovery: GET /.well-known/x402.json",
        "", "## Source data",
        "- SSB (Statistics Norway, data.ssb.no) — JSON-stat tables, free",
        "- Norges Bank (data.norges-bank.no) — SDMX-JSON, free",
        "- Cached: SSB 24h, exchange rates 1h, policy rate 6h",
        "", "## Coverage",
        "- ~50 Norwegian municipalities (see GET /municipalities)",
        "- ~40 currencies (see GET /currencies)",
        "", "## Links",
        f"- Website: {SITE_URL}",
        f"- Services manifest: {SITE_URL}/services.json",
        "",
    ]
    return PlainTextResponse("\n".join(lines), media_type="text/plain")


@app.get("/robots.txt")
async def robots_txt():
    return PlainTextResponse(
        "User-agent: *\nAllow: /\n\n"
        "User-agent: GPTBot\nAllow: /\n\n"
        "User-agent: ClaudeBot\nAllow: /\n\n"
        "User-agent: PerplexityBot\nAllow: /\n\n"
        "User-agent: Google-Extended\nAllow: /\n",
        media_type="text/plain",
    )


def _set_cache_header(response: Response, hit: bool) -> None:
    response.headers["X-Cache"] = "HIT" if hit else "MISS"


# ── Paid endpoints — Norges Bank ────────────────────────────────────


@app.get("/exchange")
async def exchange(
    response: Response,
    from_: str = Query(..., alias="from", min_length=3, max_length=3),
    days: int = Query(30, ge=1, le=365),
):
    if not currencies.is_supported(from_):
        raise HTTPException(400, f"Unsupported currency '{from_}'. See GET /currencies.")
    if from_.upper() == "NOK":
        raise HTTPException(400, "NOK is the quote currency — cannot quote NOK/NOK.")
    try:
        series, hit = await nb.exchange_rate(_http, from_, days=days, ttl=TTL_RATES)
    except nb.NorgesBankError as e:
        raise HTTPException(503, f"Norges Bank upstream: {e.message}")
    if not series:
        raise HTTPException(404, f"No exchange data for {from_.upper()}/NOK")
    latest_date, latest_rate = series[-1]
    _set_cache_header(response, hit)
    return {"pair": f"{from_.upper()}/NOK", "rate": latest_rate, "date": latest_date,
            "history": [{"date": d, "rate": r} for d, r in series[:-1]]}


@app.get("/exchange/convert")
async def exchange_convert(
    response: Response,
    amount: float = Query(..., gt=0),
    from_: str = Query(..., alias="from", min_length=3, max_length=3),
    to: str = Query(..., min_length=3, max_length=3),
):
    src = from_.upper()
    dst = to.upper()
    if not currencies.is_supported(src):
        raise HTTPException(400, f"Unsupported currency '{src}'. See GET /currencies.")
    if not currencies.is_supported(dst):
        raise HTTPException(400, f"Unsupported currency '{dst}'. See GET /currencies.")
    try:
        rate_src_to_nok = 1.0
        rate_dst_to_nok = 1.0
        date = ""
        hit = True
        if src != "NOK":
            s, h = await nb.exchange_rate(_http, src, days=1, ttl=TTL_RATES)
            if not s:
                raise HTTPException(404, f"No rate for {src}/NOK")
            rate_src_to_nok = s[-1][1]
            date = s[-1][0]
            hit = hit and h
        if dst != "NOK":
            t, h = await nb.exchange_rate(_http, dst, days=1, ttl=TTL_RATES)
            if not t:
                raise HTTPException(404, f"No rate for {dst}/NOK")
            rate_dst_to_nok = t[-1][1]
            date = date or t[-1][0]
            hit = hit and h
    except nb.NorgesBankError as e:
        raise HTTPException(503, f"Norges Bank upstream: {e.message}")
    cross = rate_src_to_nok / rate_dst_to_nok
    _set_cache_header(response, hit)
    return {"amount": amount, "from": src, "to": dst,
            "rate": round(cross, 6), "result": round(amount * cross, 4), "date": date}


@app.get("/policy-rate")
async def policy_rate(
    response: Response,
    days: int = Query(365, ge=1, le=1825),
):
    try:
        series, hit = await nb.policy_rate(_http, days=days, ttl=TTL_POLICY)
    except nb.NorgesBankError as e:
        raise HTTPException(503, f"Norges Bank upstream: {e.message}")
    if not series:
        raise HTTPException(503, "No policy-rate data returned")
    latest_date, latest_rate = series[-1]
    _set_cache_header(response, hit)
    return {"current_rate": latest_rate, "effective_date": latest_date,
            "history": [{"date": d, "rate": r} for d, r in series]}


# ── Paid endpoints — SSB ────────────────────────────────────────────


@app.get("/population")
async def population(
    response: Response,
    municipality: str = Query(..., min_length=1, max_length=64),
):
    resolved = municipalities.lookup(municipality)
    if resolved is None:
        raise HTTPException(404, f"Municipality '{municipality}' not found. See GET /municipalities.")
    name, code, county = resolved
    body = ssb.build_query({"Region": [code], "Tid": ["*"]})
    try:
        data, hit = await ssb.query(_http, ssb.TABLE_POPULATION, body, ttl=TTL_SSB)
    except ssb.SSBError as e:
        raise HTTPException(503, f"SSB upstream: {e.message}")
    rows = jsonstat.time_series(data, time_dim="Tid")
    if not rows:
        raise HTTPException(404, f"No population data for {name}")
    latest = rows[-6:]
    last = latest[-1]
    _set_cache_header(response, hit)
    return {"municipality": name.title(), "code": code, "county": county,
            "population": last["value"], "year": last["time"],
            "history": [{"year": r["time"], "population": r["value"]} for r in latest[:-1]]}


@app.get("/cpi")
async def cpi(
    response: Response,
    months: int = Query(12, ge=1, le=120),
):
    # KpiIndMnd = monthly index. Without this constraint SSB returns 4 series
    # (index, m/m change, y/y change, weight) — the weight series is always
    # 1000, which would silently land in `last["value"]`.
    body = ssb.build_query({"ContentsCode": ["KpiIndMnd"], "Tid": ["*"]})
    try:
        data, hit = await ssb.query(_http, ssb.TABLE_CPI, body, ttl=TTL_SSB)
    except ssb.SSBError as e:
        raise HTTPException(503, f"SSB upstream: {e.message}")
    rows = jsonstat.time_series(data, time_dim="Tid", value_filter={"ContentsCode": "KpiIndMnd"})
    if not rows:
        raise HTTPException(503, "No CPI data returned")
    series = rows[-months:]
    last = series[-1]
    yoy = ssb.yoy_change_pct(rows)
    _set_cache_header(response, hit)
    return {"current_index": last["value"], "month": last["time"],
            "inflation_yoy_pct": yoy,
            "monthly": [{"month": r["time"], "index": r["value"]} for r in series]}


@app.get("/housing")
async def housing(
    response: Response,
    quarters: int = Query(8, ge=1, le=40),
):
    # KvPris = price per square metre. The other content (Omsetninger /
    # transaction count) would otherwise leak into `last["value"]`.
    body = ssb.build_query({"ContentsCode": ["KvPris"], "Tid": ["*"]})
    try:
        data, hit = await ssb.query(_http, ssb.TABLE_HOUSING, body, ttl=TTL_SSB)
    except ssb.SSBError as e:
        raise HTTPException(503, f"SSB upstream: {e.message}")
    rows = jsonstat.time_series(data, time_dim="Tid", value_filter={"ContentsCode": "KvPris"})
    if not rows:
        raise HTTPException(503, "No housing data returned")
    series = rows[-quarters:]
    last = series[-1]
    yoy = ssb.yoy_change_pct(rows)
    _set_cache_header(response, hit)
    return {"region": "Hele landet", "price_index": last["value"], "quarter": last["time"],
            "change_yoy_pct": yoy,
            "quarterly": [{"quarter": r["time"], "index": r["value"]} for r in series]}


@app.get("/unemployment")
async def unemployment(
    response: Response,
    months: int = Query(12, ge=1, le=60),
):
    # Prosent = unemployment rate (per cent). The other content (Personer /
    # absolute count) would otherwise leak into `last["value"]`.
    body = ssb.build_query({"ContentsCode": ["Prosent"], "Tid": ["*"]})
    try:
        data, hit = await ssb.query(_http, ssb.TABLE_UNEMPLOYMENT, body, ttl=TTL_SSB)
    except ssb.SSBError as e:
        raise HTTPException(503, f"SSB upstream: {e.message}")
    rows = jsonstat.time_series(data, time_dim="Tid", value_filter={"ContentsCode": "Prosent"})
    if not rows:
        raise HTTPException(503, "No unemployment data returned")
    series = rows[-months:]
    last = series[-1]
    _set_cache_header(response, hit)
    return {"region": "Hele landet", "rate_pct": last["value"], "month": last["time"],
            "monthly": [{"month": r["time"], "rate_pct": r["value"]} for r in series]}


@app.get("/gdp")
async def gdp(
    response: Response,
    quarters: int = Query(8, ge=1, le=40),
):
    # Table 09190 has 3 dimensions. SSB rejects an unconstrained query
    # (returned 502 to clients), so pin Makrost to total GDP at market values
    # (bnpb.nr23_9) and ContentsCode to Faste (constant 2023-prices).
    body = ssb.build_query({
        "Makrost": ["bnpb.nr23_9"],
        "ContentsCode": ["Faste"],
        "Tid": ["*"],
    })
    try:
        data, hit = await ssb.query(_http, ssb.TABLE_GDP, body, ttl=TTL_SSB)
    except ssb.SSBError as e:
        raise HTTPException(503, f"SSB upstream: {e.message}")
    rows = jsonstat.time_series(data, time_dim="Tid", value_filter={"ContentsCode": "Faste"})
    if not rows:
        raise HTTPException(503, "No GDP data returned")
    series = rows[-quarters:]
    last = series[-1]
    yoy = ssb.yoy_change_pct(rows)
    _set_cache_header(response, hit)
    return {"gdp_value": last["value"], "quarter": last["time"], "growth_yoy_pct": yoy,
            "quarterly": [{"quarter": r["time"], "value": r["value"]} for r in series]}


# ── Static files ────────────────────────────────────────────────────

if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
