"""Tests for municipality lookup."""


def test_lookup_by_name(municipalities_module):
    out = municipalities_module.lookup("oslo")
    assert out is not None
    name, code, county = out
    assert name == "oslo" and code == "0301"


def test_lookup_case_insensitive(municipalities_module):
    assert municipalities_module.lookup("OSLO") is not None
    assert municipalities_module.lookup("Oslo") is not None


def test_lookup_by_code(municipalities_module):
    out = municipalities_module.lookup("0301")
    assert out is not None and out[0] == "oslo"


def test_lookup_ascii_fallback(municipalities_module):
    out = municipalities_module.lookup("tromso")
    assert out is not None and out[0] == "tromsø"
    out = municipalities_module.lookup("alesund")
    assert out is not None and out[0] == "ålesund"


def test_lookup_fuzzy(municipalities_module):
    """Small typos should still resolve to the closest municipality."""
    out = municipalities_module.lookup("trondhem")
    assert out is not None and out[0] == "trondheim"


def test_lookup_unknown(municipalities_module):
    assert municipalities_module.lookup("paris") is None
    assert municipalities_module.lookup("") is None


def test_all_municipalities_list(municipalities_module):
    rows = municipalities_module.all_municipalities()
    assert len(rows) >= 30
    assert all("name" in r and "code" in r and "county" in r for r in rows)
    # All codes are 4 digits
    assert all(len(r["code"]) == 4 and r["code"].isdigit() for r in rows)
