from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from calibration.cross_sectional import (
    assess_cross_sectional_promotion_gate,
    cross_sectional_quantile_summary,
    summarize_cross_sectional,
    walk_forward_cross_sectional_optimize,
)


def _load_panel(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"OOS panel not found: {path}")
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def main() -> None:
    p = argparse.ArgumentParser(description="正式T Score横截面OOS校准：仅接受通过数据质量门禁的研究数据集")
    p.add_argument("--dataset-dir", default="output/dataset")
    p.add_argument("--min-assets", type=int, default=5)
    p.add_argument("--min-train-dates", type=int, default=60)
    p.add_argument("--test-dates", type=int, default=20)
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--candidates", type=int, default=256)
    p.add_argument("--random-trials", type=int, default=200)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--allow-nonpromotion-dataset", action="store_true", help="仅用于诊断；允许读取未使用精确点时快照的数据集，但最终不会批准权重晋级")
    p.add_argument("--json-out", default="")
    args = p.parse_args()

    root = Path(args.dataset_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    quality = manifest.get("quality_gate", {})
    dataset_ready = bool(quality.get("promotion_ready_dataset", False))
    if not dataset_ready and not args.allow_nonpromotion_dataset:
        raise SystemExit(
            "dataset is not promotion-ready: build it with strict lineage quality and --exact-snapshots; "
            "use --allow-nonpromotion-dataset only for diagnostics"
        )

    panel_name = manifest.get("oos_eligible_panel_file")
    if not panel_name:
        raise SystemExit("manifest has no oos_eligible_panel_file; rebuild the dataset with the Stage 16 quality gate")
    panel = _load_panel(root / panel_name)
    if panel.empty:
        raise SystemExit("oos_eligible_panel is empty")
    if "oos_eligible" not in panel.columns:
        raise SystemExit("panel is missing oos_eligible")
    eligible_text = panel["oos_eligible"].fillna(False).astype(str).str.strip().str.lower()
    if not eligible_text.isin({"true", "1", "yes"}).all():
        raise SystemExit("oos_eligible_panel contains excluded rows")
    panel["forward_opportunity_pct"] = pd.to_numeric(panel.get("forward_opportunity_pct"), errors="coerce")
    panel = panel.dropna(subset=["forward_opportunity_pct"]).reset_index(drop=True)
    if panel.empty:
        raise SystemExit("no labeled eligible OOS rows")

    fixed_summary = summarize_cross_sectional(panel, min_assets=args.min_assets)
    quantiles = cross_sectional_quantile_summary(panel, min_assets=args.min_assets)
    metrics, weights = walk_forward_cross_sectional_optimize(
        panel,
        min_train_dates=args.min_train_dates,
        test_dates=args.test_dates,
        gap_dates=args.horizon,
        min_assets=args.min_assets,
        n_candidates=args.candidates,
        random_trials=args.random_trials,
        seed=args.seed,
    )
    statistical_gate = assess_cross_sectional_promotion_gate(metrics)
    formal_promote = bool(dataset_ready and statistical_gate.get("promote", False))

    report = {
        "dataset_manifest": str(manifest_path),
        "dataset_promotion_ready": dataset_ready,
        "diagnostic_override_used": bool(args.allow_nonpromotion_dataset and not dataset_ready),
        "rows": int(len(panel)),
        "dates": int(pd.to_datetime(panel["date"], errors="coerce").nunique()) if "date" in panel.columns else 0,
        "codes": int(panel["code"].astype(str).nunique()) if "code" in panel.columns else 0,
        "fixed_v01_summary": fixed_summary.to_dict(),
        "quantiles": quantiles.to_dict(orient="records"),
        "outer_oos_metrics": metrics.to_dict(orient="records"),
        "training_fold_weights": weights.to_dict(orient="records"),
        "statistical_promotion_gate": statistical_gate,
        "formal_promote_v02_weights": formal_promote,
        "formal_promotion_reason": (
            "dataset quality + point-in-time + statistical gates passed"
            if formal_promote
            else ("dataset quality gate not promotion-ready" if not dataset_ready else statistical_gate.get("reason", "statistical gate failed"))
        ),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    out = Path(args.json_out) if args.json_out else root / "formal_oos_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
