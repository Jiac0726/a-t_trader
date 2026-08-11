from __future__ import annotations

import json
import sys

import pytest

from app import research_oos_cli


def test_formal_oos_cli_rejects_nonpromotion_dataset_by_default(tmp_path, monkeypatch):
    manifest = {
        "quality_gate": {
            "promotion_ready_dataset": False,
            "exact_point_in_time_snapshots_used": False,
        },
        "oos_eligible_panel_file": "oos_eligible_panel.csv",
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["research_oos_cli", "--dataset-dir", str(tmp_path)])
    with pytest.raises(SystemExit, match="dataset is not promotion-ready"):
        research_oos_cli.main()
