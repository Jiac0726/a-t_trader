from __future__ import annotations

from providers.eastmoney import EastmoneyProvider


def test_eastmoney_rotates_list_hosts(monkeypatch):
    provider = EastmoneyProvider(min_interval=0)
    calls = []

    def fake(url, params):
        calls.append(url)
        if len(calls) < 3:
            raise RuntimeError("blocked")
        return {"data": {"total": 1, "diff": [{"f12": "600000", "f14": "x"}]}}

    monkeypatch.setattr(provider, "_get_json", fake)
    payload = provider._get_json_any(provider._LIST_URLS, {"pn": "1"})
    assert payload["data"]["total"] == 1
    assert calls[:3] == list(provider._LIST_URLS[:3])
