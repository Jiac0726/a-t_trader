from __future__ import annotations

import pandas as pd

from providers.security_master import looks_like_a_share_stock, normalize_exchange_code, point_in_time_from_lifecycle


def test_normalize_exchange_code():
    assert normalize_exchange_code("sh.600000") == ("SH", "600000")
    assert normalize_exchange_code("sz.000001") == ("SZ", "000001")
    assert normalize_exchange_code("bj.920001") == ("BJ", "920001")


def test_stock_code_filter_excludes_indexes_etfs_and_b_shares():
    assert looks_like_a_share_stock("SH", "600000")
    assert looks_like_a_share_stock("SH", "688001")
    assert not looks_like_a_share_stock("SH", "000001")
    assert not looks_like_a_share_stock("SH", "510300")
    assert not looks_like_a_share_stock("SH", "900901")
    assert looks_like_a_share_stock("SZ", "000001")
    assert looks_like_a_share_stock("SZ", "300750")
    assert not looks_like_a_share_stock("SZ", "200001")
    assert looks_like_a_share_stock("BJ", "920001")
    assert not looks_like_a_share_stock("BJ", "899050")


def test_lifecycle_point_in_time_includes_delisted_name_before_delist_and_excludes_future_ipo():
    master = pd.DataFrame([
        {"code": "600001", "exchange": "SH", "name": "old", "ipo_date": "1998-01-01", "delist_date": "2005-12-31", "security_type": "stock"},
        {"code": "600002", "exchange": "SH", "name": "active", "ipo_date": "2000-01-01", "delist_date": "", "security_type": "stock"},
        {"code": "600003", "exchange": "SH", "name": "future", "ipo_date": "2010-01-01", "delist_date": "", "security_type": "stock"},
        {"code": "000001", "exchange": "SH", "name": "index", "ipo_date": "1990-01-01", "delist_date": "", "security_type": "index"},
    ])
    assert set(point_in_time_from_lifecycle(master, "2004-06-01")["code"]) == {"600001", "600002"}
    assert set(point_in_time_from_lifecycle(master, "2006-06-01")["code"]) == {"600002"}


def test_delist_date_is_inclusive_for_lifecycle_fallback():
    master = pd.DataFrame([{"code": "600001", "exchange": "SH", "ipo_date": "2000-01-01", "delist_date": "2005-12-31", "security_type": "stock"}])
    assert len(point_in_time_from_lifecycle(master, "2005-12-31")) == 1
    assert point_in_time_from_lifecycle(master, "2006-01-01").empty


def test_filter_panel_by_lifecycle_removes_future_ipo_post_delist_and_unknown():
    from providers.security_master import filter_panel_by_lifecycle
    panel = pd.DataFrame([
        {"date": "2004-06-01", "code": "600001", "score": 1},
        {"date": "2006-06-01", "code": "600001", "score": 2},
        {"date": "2004-06-01", "code": "600002", "score": 3},
        {"date": "2004-06-01", "code": "600003", "score": 4},
        {"date": "2004-06-01", "code": "609999", "score": 5},
    ])
    master = pd.DataFrame([
        {"code": "600001", "ipo_date": "1998-01-01", "delist_date": "2005-12-31", "security_type": "stock"},
        {"code": "600002", "ipo_date": "2000-01-01", "delist_date": "", "security_type": "stock"},
        {"code": "600003", "ipo_date": "2010-01-01", "delist_date": "", "security_type": "stock"},
    ])
    out = filter_panel_by_lifecycle(panel, master)
    assert list(zip(out["date"].dt.strftime("%Y-%m-%d"), out["code"])) == [("2004-06-01", "600001"), ("2004-06-01", "600002")]


def test_filter_panel_by_lifecycle_can_keep_unknown_for_exploration():
    from providers.security_master import filter_panel_by_lifecycle
    panel = pd.DataFrame([{"date": "2020-01-01", "code": "609999"}])
    master = pd.DataFrame([{"code": "600001", "ipo_date": "2000-01-01", "delist_date": "", "security_type": "stock"}])
    assert filter_panel_by_lifecycle(panel, master, unknown="drop").empty
    assert len(filter_panel_by_lifecycle(panel, master, unknown="keep")) == 1


def test_exact_snapshot_filter_uses_date_code_membership_and_suspension_policy():
    from providers.security_master import filter_panel_by_snapshots
    panel = pd.DataFrame([
        {"date": "2026-01-05", "code": "600001", "score": 1},
        {"date": "2026-01-05", "code": "600002", "score": 2},
        {"date": "2026-01-06", "code": "600001", "score": 3},
    ])
    snapshots = pd.DataFrame([
        {"as_of": "2026-01-05", "code": "600001", "trade_status": 1},
        {"as_of": "2026-01-05", "code": "600002", "trade_status": 0},
        {"as_of": "2026-01-06", "code": "600001", "trade_status": 1},
    ])
    assert len(filter_panel_by_snapshots(panel, snapshots, include_suspended=True)) == 3
    tradable = filter_panel_by_snapshots(panel, snapshots, include_suspended=False)
    assert list(tradable["score"]) == [1, 3]
