from __future__ import annotations

from datetime import date

import pandas as pd

from validation.live import run_live_validation


def bars(n=20, freq="D", kind=None):
    dt = pd.date_range("2026-01-01", periods=n, freq=freq)
    df = pd.DataFrame(
        {
            "datetime": dt,
            "open": range(1, n + 1),
            "high": range(2, n + 2),
            "low": [max(0, x - 1) for x in range(1, n + 1)],
            "close": range(1, n + 1),
            "volume": [100] * n,
            "amount": [1000] * n,
        }
    )
    if kind:
        df.attrs["kind"] = kind
    return df


class Market:
    def stock_list(self):
        rows = []
        markets = ["SH", "SZ", "BJ"]
        for i in range(3300):
            rows.append({"code": f"{i:06d}", "name": f"S{i}", "market": markets[i % 3]})
        return pd.DataFrame(rows)

    def history(self, code, start, end, interval="1d", adjust="qfq"):
        return bars(30 if interval == "1d" else 50, "D" if interval == "1d" else "5min")


class Bench:
    def history(self, asset, *args, **kwargs):
        return bars(30, kind="etf" if "etf" in asset else "index")


class Master:
    name = "master"

    def snapshot(self, day):
        rows = []
        markets = ["SH", "SZ", "BJ"]
        for i in range(3300):
            market = markets[i % 3]
            rows.append(
                {
                    "code": f"{i:06d}",
                    "name": f"S{i}",
                    "exchange": market,
                    "as_of": pd.Timestamp(day),
                    "trade_status": 1,
                }
            )
        return pd.DataFrame(rows)


def test_live_validation_happy_path():
    report = run_live_validation(
        Market(),
        Bench(),
        security_master_provider=Master(),
        persistence_probe=lambda: ("ok", {}),
        end=date(2026, 2, 1),
    )
    assert report.ok
    assert all(x.status == "PASS" for x in report.checks)
    assert any(x.name == "reference_etf_history" for x in report.checks)
    assert any(x.name == "point_in_time_overlap_shsz" for x in report.checks)


def test_live_validation_flags_small_universe():
    class Small(Market):
        def stock_list(self):
            return super().stock_list().head(10)

    report = run_live_validation(Small(), Bench(), end=date(2026, 2, 1))
    assert not report.ok
    assert any(x.name == "market_universe" and x.status == "FAIL" for x in report.checks)


def test_live_validation_requires_bj_by_default_but_can_relax():
    class NoBJ(Market):
        def stock_list(self):
            df = super().stock_list()
            return df[df["market"] != "BJ"].reset_index(drop=True)

    strict = run_live_validation(NoBJ(), Bench(), universe_floor=2000, end=date(2026, 2, 1))
    assert not strict.ok
    relaxed = run_live_validation(NoBJ(), Bench(), universe_floor=2000, require_markets=("SH", "SZ"), end=date(2026, 2, 1))
    assert relaxed.ok


def test_live_validation_fails_low_shsz_membership_overlap():
    class ThinMaster(Master):
        def snapshot(self, day):
            return super().snapshot(day).head(2600)

    report = run_live_validation(
        Market(),
        Bench(),
        security_master_provider=ThinMaster(),
        membership_overlap_floor=0.95,
        end=date(2026, 2, 1),
    )
    assert not report.ok
    assert any(x.name == "point_in_time_overlap_shsz" and x.status == "FAIL" for x in report.checks)


def test_market_representative_probe_skips_unusable_first_candidate():
    class FirstBjBroken(Market):
        def history(self, code, start, end, interval="1d", adjust="qfq"):
            if interval == "1d" and str(code).zfill(6) == "000002":
                return bars(1)
            return super().history(code, start, end, interval=interval, adjust=adjust)

    report = run_live_validation(FirstBjBroken(), Bench(), end=date(2026, 2, 1))
    assert report.ok
    bj = next(x for x in report.checks if x.name == "daily_market_BJ")
    assert bj.status == "PASS"
    assert bj.data["code"] == "000005"
    assert bj.data["attempted_before_success"]
