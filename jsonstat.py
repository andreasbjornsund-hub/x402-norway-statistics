"""Minimal JSON-stat 2.0 parser.

JSON-stat is a tabular data format SSB uses. We intentionally do NOT depend
on pyjstat because it pulls in pandas (heavy on a 256 MB Fly machine).

Spec (abridged): https://json-stat.org/format/

Shape we care about:
{
  "version": "2.0",
  "class": "dataset",
  "id":   ["Region", "ContentsCode", "Tid"],
  "size": [   357,           1,         1   ],
  "value": [709037, 290000, ...],   # row-major flatten over `id`+`size`
  "dimension": {
    "Region": {"category": {"index": {"0301": 0, "1101": 1, ...}, "label": {"0301": "Oslo", ...}}},
    "Tid":    {"category": {"index": {"2025": 0}, "label": {"2025": "2025"}}},
    ...
  }
}
"""
from typing import Any


def iter_observations(ds: dict):
    """Yield (dimension_dict, value) for each observation in a JSON-stat dataset.

    `dimension_dict` is a dict {dim_id: {"code": ..., "label": ...}}.
    `value` is the cell value (number, string, or None).
    """
    if not isinstance(ds, dict):
        return
    ids: list[str] = ds.get("id") or []
    sizes: list[int] = ds.get("size") or []
    values: list = ds.get("value") or []
    if isinstance(values, dict):
        # Sparse representation: {"0": 709037, "5": 290000, ...}
        values_list: list = [None] * (max(int(k) for k in values) + 1) if values else []
        for k, v in values.items():
            values_list[int(k)] = v
        values = values_list
    if not ids or not sizes or len(ids) != len(sizes):
        return

    # Pre-compute, per dimension, the ordered (code, label) pairs at each index.
    dim_lookup: dict[str, list[tuple[str, str]]] = {}
    for dim_id in ids:
        dim = ds.get("dimension", {}).get(dim_id, {})
        cat = dim.get("category", {}) or {}
        index = cat.get("index", {}) or {}
        labels = cat.get("label", {}) or {}
        if isinstance(index, list):
            ordered = [(code, labels.get(code, code)) for code in index]
        else:
            # dict {code: position}; sort by position
            ordered = sorted(index.items(), key=lambda kv: kv[1])
            ordered = [(code, labels.get(code, code)) for code, _ in ordered]
        dim_lookup[dim_id] = ordered

    # Strides for row-major flatten
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    total = 1
    for s in sizes:
        total *= s

    for flat in range(min(total, len(values))):
        coords = []
        rest = flat
        for stride in strides:
            coords.append(rest // stride)
            rest = rest % stride
        dim_dict = {}
        for i, dim_id in enumerate(ids):
            ordered = dim_lookup.get(dim_id, [])
            if 0 <= coords[i] < len(ordered):
                code, label = ordered[coords[i]]
                dim_dict[dim_id] = {"code": code, "label": label}
            else:
                dim_dict[dim_id] = {"code": "", "label": ""}
        yield dim_dict, values[flat]


def time_series(ds: dict, time_dim: str = "Tid", value_filter: dict[str, str] | None = None) -> list[dict]:
    """Flatten a dataset into [{time, label, value, dims}, ...] sorted by time.

    `value_filter` lets the caller restrict to e.g. one ContentsCode, e.g.
    {"ContentsCode": "Folketallet1"}.
    """
    rows: list[dict] = []
    for dims, value in iter_observations(ds):
        if value_filter:
            skip = False
            for k, expected_code in value_filter.items():
                if dims.get(k, {}).get("code") != expected_code:
                    skip = True
                    break
            if skip:
                continue
        time_meta = dims.get(time_dim, {})
        rows.append({
            "time": time_meta.get("code", ""),
            "label": time_meta.get("label", ""),
            "value": value,
            "dims": dims,
        })
    rows.sort(key=lambda r: r["time"])
    return rows
