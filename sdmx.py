"""Minimal SDMX-JSON parser for Norges Bank.

SDMX-JSON v1 (what Norges Bank serves) shape we care about:

{
  "data": {
    "dataSets": [
      {"series": {"0:0:0:0": {"observations": {"0": [10.45], "1": [10.42]}}}}
    ],
    "structure": {
      "dimensions": {
        "series":      [{"id": "FREQ", "values": [{"id": "B", "name": "Business"}]}, ...],
        "observation": [{"id": "TIME_PERIOD", "values": [{"id": "2026-05-08", "name": "..."}, ...]}]
      }
    }
  }
}

We just want [(date_str, value), ...] sorted by date for the (single) series.
"""
from typing import Iterable


def time_series(data: dict) -> list[tuple[str, float]]:
    """Return [(date, value), ...] sorted by date for the first series."""
    body = data.get("data") or {}
    datasets = body.get("dataSets") or []
    if not datasets:
        return []
    series = datasets[0].get("series") or {}
    if not series:
        return []
    # Take the first series (Norges Bank queries are typically narrow enough
    # to return one series). If the caller cares to differentiate, they can
    # filter their request.
    first_key = next(iter(series))
    observations: dict = series[first_key].get("observations") or {}

    structure = body.get("structure") or {}
    obs_dims: list = (structure.get("dimensions") or {}).get("observation") or []
    time_dim = next((d for d in obs_dims if d.get("id") == "TIME_PERIOD"), None)
    if not time_dim:
        return []
    time_values = time_dim.get("values") or []

    rows: list[tuple[str, float]] = []
    for obs_index, obs_arr in observations.items():
        try:
            idx = int(obs_index)
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(time_values):
            continue
        if not obs_arr:
            continue
        date_str = time_values[idx].get("id", "")
        try:
            value = float(obs_arr[0])
        except (TypeError, ValueError, IndexError):
            continue
        rows.append((date_str, value))
    rows.sort()
    return rows
