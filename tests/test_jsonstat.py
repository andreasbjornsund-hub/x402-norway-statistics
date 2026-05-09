"""Tests for the minimal JSON-stat parser."""


def _ssb_population():
    """SSB-shaped JSON-stat 2.0 dataset for Oslo population over 3 years."""
    return {
        "version": "2.0", "class": "dataset",
        "id": ["Region", "ContentsCode", "Tid"],
        "size": [1, 1, 3],
        "value": [690000, 700000, 709037],
        "dimension": {
            "Region": {"category": {"index": {"0301": 0}, "label": {"0301": "Oslo"}}},
            "ContentsCode": {"category": {"index": {"Folketallet1": 0}, "label": {"Folketallet1": "Population"}}},
            "Tid": {"category": {
                "index": {"2023": 0, "2024": 1, "2025": 2},
                "label": {"2023": "2023", "2024": "2024", "2025": "2025"},
            }},
        },
    }


def test_iter_observations_count(jsonstat_module):
    rows = list(jsonstat_module.iter_observations(_ssb_population()))
    assert len(rows) == 3


def test_time_series_sorted(jsonstat_module):
    rows = jsonstat_module.time_series(_ssb_population(), time_dim="Tid")
    assert [r["time"] for r in rows] == ["2023", "2024", "2025"]
    assert rows[-1]["value"] == 709037


def test_time_series_with_filter(jsonstat_module):
    """If a content filter doesn't match, no rows are returned."""
    ds = _ssb_population()
    rows = jsonstat_module.time_series(ds, time_dim="Tid", value_filter={"ContentsCode": "Nope"})
    assert rows == []


def test_empty_dataset(jsonstat_module):
    assert list(jsonstat_module.iter_observations({})) == []
    assert jsonstat_module.time_series({}) == []


def test_index_as_list_form(jsonstat_module):
    """JSON-stat 2.0 also allows category.index as a list of codes."""
    ds = {
        "version": "2.0", "class": "dataset",
        "id": ["Tid"], "size": [2], "value": [10, 20],
        "dimension": {
            "Tid": {"category": {"index": ["A", "B"], "label": {"A": "Alpha", "B": "Beta"}}},
        },
    }
    rows = jsonstat_module.time_series(ds)
    assert [r["time"] for r in rows] == ["A", "B"]
    assert rows[0]["label"] == "Alpha"
