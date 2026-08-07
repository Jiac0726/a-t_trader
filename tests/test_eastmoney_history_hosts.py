from providers.eastmoney import EastmoneyProvider


def test_eastmoney_rotates_history_hosts_and_sticks_to_success(monkeypatch):
    provider = EastmoneyProvider(min_interval=0)
    calls = []

    def fake(url, params):
        calls.append(url)
        if url != provider._HISTORY_URLS[1]:
            raise RuntimeError("blocked")
        return {
            "data": {
                "code": "920002",
                "name": "x",
                "klines": ["2026-08-07,10,10.1,10.2,9.9,100,100000,3,1,0.1,2"],
            }
        }

    monkeypatch.setattr(provider, "_get_json", fake)
    payload = provider._get_history_json({"secid": "0.920002"})
    assert payload["data"]["code"] == "920002"
    assert calls == [provider._HISTORY_URLS[0], provider._HISTORY_URLS[1]]

    calls.clear()
    provider._get_history_json({"secid": "0.920002"})
    assert calls == [provider._HISTORY_URLS[1]]
