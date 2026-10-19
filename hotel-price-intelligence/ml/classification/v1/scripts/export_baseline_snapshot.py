"""Xuat baseline snapshot bat bien tu ket qua regression (CHI DOC nguon; khong sua/retrain). Chay mot lan o may chinh; in pin de dan vao configs/train.yaml.

  python export_baseline_snapshot.py [--datasets-root ...] [--regression-root ...] [--out ...] [--horizons 1 3]
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

V1_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V1_DIR))

from cls_training.config import dataset_pin, load_config  # noqa: E402
from cls_training.data import load_dataset  # noqa: E402
from cls_training.snapshot import export_snapshot, snapshot_paths  # noqa: E402
from cls_training.upstream.provenance import file_sha256  # noqa: E402

ML_DIR = V1_DIR.parents[1]
WORKSPACE = ML_DIR.parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--datasets-root", default=str(WORKSPACE / "outputs" / "datasets"))
    ap.add_argument("--regression-root", default=str(ML_DIR / "regression" / "models" / "train_v3_nf"))
    ap.add_argument("--out", default=str(ML_DIR / "classification" / "results" / "v1" / "baseline_snapshot"))
    ap.add_argument("--horizons", nargs="+", type=int, default=[1, 3])
    args = ap.parse_args()
    cfg = load_config(args.config)
    out_dir = Path(args.out)
    pins = []
    for h in args.horizons:
        pin = dataset_pin(cfg, h)
        loaded = load_dataset(Path(args.datasets_root) / pin["dataset_version"], pin, h, frame_cfg={"primary_requires_seen_hotel": bool(cfg["primary_requires_seen_hotel"])})
        runs = sorted(glob.glob(str(Path(args.regression_root) / pin["dataset_version"] / "run_*" / f"h{h}_predictions_v3.parquet")))
        if len(runs) != 1:
            print(f"h{h}: can dung 1 file du doan regression, co {len(runs)}", file=sys.stderr)
            return 2
        manifest = export_snapshot(loaded, Path(runs[0]).parent, out_dir, export_code_files={
            "cls_training/snapshot.py": V1_DIR / "cls_training" / "snapshot.py", "scripts/export_baseline_snapshot.py": Path(__file__).resolve()})
        parquet_path, manifest_path = snapshot_paths(out_dir, h)
        pins.append({"horizon": h, "snapshot_file_sha256": file_sha256(parquet_path), "manifest_sha256": file_sha256(manifest_path)})
        print(f"h{h}: {manifest['rows']} hang {manifest['rows_by_split']} | cot du doan {len(manifest['prediction_columns'])} | champions {manifest['champions']} | "
              f"snapshot {manifest['snapshot_file_sha256'][:16]}...")
    print("\nDAN VAO configs/train.yaml (baseline_snapshot_pins):")
    print(json.dumps(pins, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
