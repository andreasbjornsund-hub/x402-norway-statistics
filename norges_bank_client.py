"""Norges Bank SDMX REST client.

API: https://data.norges-bank.no/api/data/
Free, no auth, no documented rate limit (we still throttle politely).

We use it for two things:
  - Exchange rates (EXR/B.{CURRENCY}.NOK.SP)
  - Policy rate (IR/B.KPRA.)
"""
import asyncio

import httpx

import cache
import sdmx


BASE = "https://data.norges-bank.no/api/data"
USER_AGENT = "x402agent-norway-statistics/1.0 github.com/andreasbjornsund-hub"

# 10 concurrent requests max — Norges Bank doesn't publish a limit but
# politeness is free.
_SEM = asyncio.Semaphore(10)


class NorgesBankError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"NorgesBank {status_code}: {message}")


async def _fetch(client: httpx.AsyncClient, path: str, ttl: float) -> tuple[list[tuple[str, float]], bool]:
    cache_key = f"nb:{path}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, True
    url = f"{BASE}/{path}"
    async with _SEM:
        resp = await client.get(
            url,
            params={"format": "sdmx-json"},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
    if resp.status_code != 200:
        raise NorgesBankError(resp.status_code, resp.text[:200])
    data = sdmx.time_series(resp.json())
    cache.put(cache_key, data, ttl)
    return data, False


async def exchange_rate(
    client: httpx.AsyncClient,
    currency: str,
    days: int = 30,
    ttl: float = 3600.0,
) -> tuple[list[tuple[str, float]], bool]:
    """Last `days` of CURRENCY/NOK spot rates, oldest first.

    Returns the time series as [(date, rate), ...] plus cache_hit flag.
    """
    currency = currency.upper().strip()
    if not currency.isalpha() or len(currency) != 3:
        raise NorgesBankError(400, f"invalid currency code: {currency!r}")
    path = f"EXR/B.{currency}.NOK.SP?lastNObservations={max(1, min(days, 365))}"
    return await _fetch(client, path, ttl)


async def policy_rate(
    client: httpx.AsyncClient,
    days: int = 365,
    ttl: float = 6 * 3600.0,
) -> tuple[list[tuple[str, float]], bool]:
    """Norges Bank key policy rate (KPRA) time series."""
    path = f"IR/B.KPRA.?lastNObservations={max(1, min(days, 1825))}"
    return await _fetch(client, path, ttl)
