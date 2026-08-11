from __future__ import annotations

from providers.eastmoney import EastmoneyProvider


class FakePagedEastmoney(EastmoneyProvider):
    def __init__(self, total: int = 3105):
        super().__init__(timeout=0.1, min_interval=0)
        self.total = total
        self.pages: list[int] = []

    def _get_json(self, url, params):
        page = int(params["pn"])
        page_size = int(params["pz"])
        self.pages.append(page)
        start = (page - 1) * page_size
        end = min(start + page_size, self.total)
        diff = []
        for i in range(start, end):
            code = f"{600000 + i:06d}"
            diff.append({
                "f12": code,
                "f14": f"S{i}",
                "f13": 1,
                "f2": 10.0,
                "f3": 0.1,
                "f5": 100,
                "f6": 1000,
                "f8": 1.0,
                "f15": 10.2,
                "f16": 9.8,
            })
        return {"data": {"total": self.total, "diff": diff}}


def test_stock_list_fetches_all_pages_when_upstream_caps_page_size():
    provider = FakePagedEastmoney(total=3105)
    out = provider.stock_list()
    assert len(out) == 3105
    assert provider.pages[0] == 1
    assert provider.pages[-1] == 32
    assert len(provider.pages) == 32
    assert out["code"].nunique() == 3105
