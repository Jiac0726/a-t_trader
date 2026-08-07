from __future__ import annotations

from providers.bse_code_mapping import BseCodeMappingProvider


HTML = """
<html><body><table>
<tr><th>序号</th><th>证券简称</th><th>上市日期</th><th>旧代码</th><th>新代码</th></tr>
<tr><td>1</td><td>芭薇股份</td><td>2024/3/29</td><td>837023</td><td>920123</td></tr>
<tr><td>2</td><td>许昌智能</td><td>2024/1/26</td><td>831396</td><td>920496</td></tr>
<tr><td>3</td><td>安徽凤凰</td><td>2020/12/23</td><td>832000</td><td>920000</td></tr>
</table></body></html>
"""


def test_bse_mapping_parser_keeps_non_algorithmic_pairs_exactly():
    out = BseCodeMappingProvider.parse_html(HTML)
    pairs = dict(zip(out["new_code"], out["old_code"]))
    assert pairs["920123"] == "837023"
    assert pairs["920496"] == "831396"
    assert pairs["920000"] == "832000"
    assert out["new_code"].str.startswith("920").all()


def test_bse_mapping_does_not_infer_missing_pair():
    class Response:
        text = HTML
        apparent_encoding = "utf-8"
        encoding = "utf-8"
        def raise_for_status(self):
            return None

    class Session:
        def __init__(self):
            self.headers = {}
        def get(self, *args, **kwargs):
            return Response()

    provider = BseCodeMappingProvider(session=Session())
    # Bypass the production completeness floor for this tiny fixture by using
    # the parser output as the cached authoritative table.
    provider._cache = BseCodeMappingProvider.parse_html(HTML)
    assert provider.old_code_for("920123") == "837023"
    assert provider.new_code_for("832000") == "920000"
    assert provider.old_code_for("920999") is None
