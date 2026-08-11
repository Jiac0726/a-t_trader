import pytest

from providers.base import NoMarketData
from providers.tencent_history import TencentHistoryProvider


class Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "data": {
                "bj920002": {
                    "qfqday": [["2026-08-07", "10", "10.2", "10.3", "9.9", "1000"]]
                }
            }
        }


class Session:
    def __init__(self):
        self.headers = {}

    def get(self, *args, **kwargs):
        return Response()


def test_tencent_rejects_one_row_bse_payload_for_long_history_window():
    provider = TencentHistoryProvider(session=Session())
    with pytest.raises(NoMarketData, match="suspiciously short"):
        provider.history("920002", "2026-06-01", "2026-08-07", interval="1d")
