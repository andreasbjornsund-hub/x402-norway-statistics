"""SSB (Statistics Norway) JSON-stat client.

API: https://data.ssb.no/api/v0/
Free, no auth. Tables are queried by POST to /en/table/{id} with a JSON
body specifying which dimensions to include. Response is JSON-stat 2.0.

We use a small set of tables (constants below). For each, we expose a
helper that submits a sensible default query for our agent's use case.
"""
import asyncio

import httpx

import cache
import jsonstat


BASE = "https://data.ssb.no/api/v0/en/table"
USER_AGENT = "x402agent-norway-statistics/1.0 github.com/andreasbjornsund-hub"

# SSB doesn't document a hard rate limit. Stay polite at 10 concurrent.
_SEM = asyncio.Semaphore(10)

# Table IDs we use. Each entry holds the (table_id, default ContentsCode).
TABLE_POPULATION = "07459"
TABLE_CPI = "03013"            # KPI total — index 1979=100
TABLE_HOUSING = "07241"        # House price index, quarterly
TABLE_UNEMPLOYMENT = "08517"   # Registered unemployed by month
TABLE_GDP = "09190"            # Quarterly national accounts


class SSBError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"SSB {status_code}: {message}")


async def query(
    client: httpx.AsyncClient,
    table_id: str,
    body: dict,
    ttl: float = 24 * 3600.0,
) -> tuple[dict, bool]:
    """POST a query to an SSB table; return (json_stat_dict, cache_hit).

    Cache key is (table_id, json-of-body), so different filters on the same
    table are cached independently.
    """
    import json as _json
    cache_key = f"ssb:{table_id}:{_json.dumps(body, sort_keys=True)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, True

    url = f"{BASE}/{table_id}"
    async with _SEM:
        resp = await client.post(
            url,
            json=body,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
    if resp.status_code != 200:
        raise SSBError(resp.status_code, resp.text[:300])
    data = resp.json()
    cache.put(cache_key, data, ttl)
    return data, False


def build_query(
    selections: dict[str, list[str]],
    response_format: str = "json-stat2",
) -> dict:
    """Build an SSB query body from a dict of {dimension: [codes...]}.

    Pass `["*"]` for "all values", or the literal SSB filter shorthand `"top"`
    or `"all"` is not supported here — use explicit codes for predictability.
    """
    queries = []
    for code, values in selections.items():
        if values == ["*"]:
            queries.append({"code": code, "selection": {"filter": "all", "values": ["*"]}})
        else:
            queries.append({"code": code, "selection": {"filter": "item", "values": values}})
    return {"query": queries, "response": {"format": response_format}}


def latest_value(rows: list[dict]) -> dict | None:
    """Helper: the most-recent row from a time series produced by jsonstat.time_series."""
    return rows[-1] if rows else None


def yoy_change_pct(rows: list[dict]) -> float | None:
    """Year-over-year change percentage from a monthly/quarterly time series.

    Compares the latest row's value to the value 12 months / 4 quarters ago,
    figured out by step count rather than by parsing dates.
    """
    if not rows or len(rows) < 13:
        # Try quarterly (4 steps) if we have >= 5 rows
        if len(rows) < 5:
            return None
        prev = rows[-5]["value"]
    else:
        prev = rows[-13]["value"]
    cur = rows[-1]["value"]
    if prev in (None, 0) or cur is None:
        return None
    try:
        return round((float(cur) - float(prev)) / float(prev) * 100, 2)
    except (TypeError, ValueError):
        return None
