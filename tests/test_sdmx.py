"""Tests for the minimal SDMX-JSON parser."""


def _norges_bank_exchange():
    return {
        "data": {
            "dataSets": [{
                "series": {
                    "0:0:0:0": {
                        "observations": {"0": [10.39], "1": [10.41], "2": [10.45]},
                    }
                }
            }],
            "structure": {
                "dimensions": {
                    "observation": [{
                        "id": "TIME_PERIOD",
                        "values": [
                            {"id": "2026-05-01"},
                            {"id": "2026-05-02"},
                            {"id": "2026-05-08"},
                        ],
                    }]
                }
            },
        }
    }


def test_time_series_basic(sdmx_module):
    rows = sdmx_module.time_series(_norges_bank_exchange())
    assert rows == [("2026-05-01", 10.39), ("2026-05-02", 10.41), ("2026-05-08", 10.45)]


def test_empty_payload(sdmx_module):
    assert sdmx_module.time_series({}) == []
    assert sdmx_module.time_series({"data": {}}) == []


def test_missing_observations(sdmx_module):
    payload = {"data": {"dataSets": [{"series": {}}], "structure": {"dimensions": {"observation": []}}}}
    assert sdmx_module.time_series(payload) == []
