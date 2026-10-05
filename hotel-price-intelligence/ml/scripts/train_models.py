"""Huan luyen/danh gia hoi quy gia ngan han tren mot dataset da xuat (Phase 4).

    python ml/scripts/train_models.py --dataset-dir outputs/datasets/ds_20261006_rh1 --horizons 7 --models ridge,rf,xgb

Moi horizon -> `<output-root>/<dataset_version>/<run_id>/h{k}_report.json` (+ model/predictions/metric theo nhom). `--config` mac dinh
`ml/configs/train_v1.yaml`. Doc Parquet, KHONG doc DB. Seed va sieu tham so lay tu cau hinh.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ML_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML_DIR))

from training.config import DEFAULT_CONFIG_PATH, apply_overrides, load_config  # noqa: E402
from training.runner import run_horizon  # noqa: E402

DEFAULT_OUTPUT_ROOT = ML_DIR.parents[1] / "outputs" / "models"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--horizons", default="", help="vd 1,3,7,14 (mac dinh: theo cau hinh)")
    parser.add_argument("--models", default="ridge,rf,xgb", help="tap con cua ridge,rf,xgb (baseline luon chay)")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=time.strftime("run_%Y%m%d_%H%M%S"))
    parser.add_argument("--device", choices=["cpu", "cuda"], default=None,
                        help="thiet bi cho XGBoost (Colab GPU: cuda). Ghi vao config_sha256 cua bao cao; RF/Ridge luon chay CPU")
    args = parser.parse_args()
    cfg = apply_overrides(load_config(args.config), device=args.device)
    horizons = [int(x) for x in args.horizons.split(",") if x] or [int(h) for h in cfg["horizons"]]
    models = [m for m in args.models.split(",") if m]
    out_dir = args.output_root / args.dataset_dir.name / args.run_id
    for h in horizons:
        report = run_horizon(args.dataset_dir, h, cfg, out_dir, models)
        line = f"h{h}: status={report['status']} rows={report['rows']}"
        if report.get("selected_model"):
            test = report["results"][report["selected_model"]]["test"]
            line += f" selected={report['selected_model']} test_acc@20={test['accuracy_at_tol']:.3f} mae={test['mae']:.0f} eval={report['evaluation_status']}"
        elif report.get("reason"):
            line += f" reason={report['reason']}"
        print(line, flush=True)
    print(f"ket qua: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
