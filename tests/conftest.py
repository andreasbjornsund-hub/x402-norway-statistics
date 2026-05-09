"""Shared pytest fixtures for x402-norway-statistics."""
import os
import sys

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="session")
def main_module():
    os.environ.setdefault("EVM_ADDRESS", "0xTEST0000000000000000000000000000000000")
    os.chdir(REPO_ROOT)
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import main
    return main


@pytest.fixture
def jsonstat_module(main_module):
    import jsonstat
    return jsonstat


@pytest.fixture
def sdmx_module(main_module):
    import sdmx
    return sdmx


@pytest.fixture
def municipalities_module(main_module):
    import municipalities
    return municipalities


@pytest.fixture
def currencies_module(main_module):
    import currencies
    return currencies


@pytest.fixture(autouse=True)
def reset_cache(main_module):
    import cache
    cache.reset()
    yield


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json


class FakeHTTP:
    """Stub for httpx.AsyncClient — supports both .get and .post."""

    def __init__(self):
        self.responses_get: dict[str, FakeResponse] = {}
        self.responses_post: dict[str, FakeResponse] = {}
        self.calls: list[tuple[str, str, dict]] = []

    def stub_get(self, url_contains, status, json_data=None):
        self.responses_get[url_contains] = FakeResponse(status, json_data)

    def stub_post(self, url_contains, status, json_data=None):
        self.responses_post[url_contains] = FakeResponse(status, json_data)

    async def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, dict(params or {})))
        for needle, r in self.responses_get.items():
            if needle in url:
                return r
        return FakeResponse(404, {"error": f"unstubbed GET {url}"})

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json or {}))
        for needle, r in self.responses_post.items():
            if needle in url:
                return r
        return FakeResponse(404, {"error": f"unstubbed POST {url}"})

    async def aclose(self):
        pass


@pytest.fixture
def fake_http(main_module, monkeypatch):
    f = FakeHTTP()
    monkeypatch.setattr(main_module, "_http", f)
    return f
