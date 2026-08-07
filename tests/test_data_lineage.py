from __future__ import annotations

import pandas as pd

from calibration.walkforward import build_score_history
from dataset.builder import attach_minute_labels_from_store, build_daily_score_panel
from storage.duckdb_store import DuckDBStore


def bars(n: int = 70):
    dates = pd.bdate_range("2026-01-01", periods=n)
    base = pd.Series(range(n), dtype=float) + 10.0
    return pd.DataFrame(
        {
            "datetime": dates,
            "open": base,
            "high": base + 1.0,
            "low": base - 1.0,
            "close": base + 0.5,
            "volume": [1000.0] * n,
            "amount": [1_000_000.0] * n,
        }
    )


def test_duckdb_persists_row_level_provider_and_amount_lineage(tmp_path):
    store = DuckDBStore(tmp_path / "lineage.duckdb")
    frame = bars(3)
    frame.attrs.update(
        {
            "provider": "tencent-history",
            "amount_quality": "estimated_from_volume_lots_x100_x_ohlc_mean",
            "source_code": "bj920002",
            "bse_code_stitched": False,
        }
    )
    store.save_history("920002", "1d", frame, adjust="qfq")

    out = store.load_history("920002", "1d", adjust="qfq")
    assert set(out["provider"]) == {"tencent-history"}
    assert set(out["amount_quality"]) == {"estimated_from_volume_lots_x100_x_ohlc_mean"}
    assert set(out["source_code"]) == {"bj920002"}
    assert not out["bse_code_stitched"].any()
    assert set(out["adjust"]) == {"qfq"}


def test_stitched_source_code_column_is_preserved_per_row(tmp_path):
    store = DuckDBStore(tmp_path / "stitched.duckdb")
    frame = bars(4)
    frame["source_code"] = ["832000", "832000", "920000", "920000"]
    frame.attrs.update(
        {
            "provider": "bse-continuous-history",
            "amount_quality": "provider_native",
            "bse_code_stitched": True,
        }
    )
    store.save_history("920000", "1d", frame, adjust="qfq")

    out = store.load_history("920000", "1d", adjust="qfq")
    assert list(out["source_code"]) == ["832000", "832000", "920000", "920000"]
    assert out["bse_code_stitched"].all()


def test_score_history_summarizes_exact_trailing_lineage_window():
    frame = bars(65)
    frame["provider"] = ["source-a"] * 60 + ["source-b"] * 5
    frame["amount_quality"] = ["provider_native"] * 60 + ["estimated_from_volume"] * 5
    frame["adjust"] = "qfq"
    frame["source_code"] = "600519"
    frame["bse_code_stitched"] = False

    scores = build_score_history("600519", frame, lookback=60, provider="duckdb")
    last = scores.iloc[-1]
    assert last["lineage_adjust"] == "qfq"
    assert last["lineage_providers"] == "source-a|source-b"
    assert int(last["lineage_provider_count"]) == 2
    assert bool(last["lineage_has_estimated_amount"]) is True
    assert bool(last["lineage_has_unknown"]) is False


class RecordingStore:
    def __init__(self):
        self.calls = []

    def load_history(self, code, interval, start=None, end=None, adjust="qfq"):
        self.calls.append((str(code), interval, adjust))
        if interval == "1d":
            out = bars(70)
            out["provider"] = "provider-native"
            out["amount_quality"] = "provider_native"
            out["adjust"] = adjust
            out["source_code"] = str(code)
            out["bse_code_stitched"] = False
            return out
        return pd.DataFrame()


def test_dataset_daily_features_default_qfq_but_minute_labels_default_raw():
    store = RecordingStore()
    panel, failures = build_daily_score_panel(["600519"], store, lookback=60)
    assert not panel.empty
    assert not failures
    assert store.calls[0] == ("600519", "1d", "qfq")

    _, label_failures = attach_minute_labels_from_store(panel, store, codes=["600519"], horizon=1)
    assert store.calls[-1] == ("600519", "5m", "none")
    assert label_failures and "adjust=none" in label_failures[0].error
