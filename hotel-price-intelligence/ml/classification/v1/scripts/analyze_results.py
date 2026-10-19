"""Phan tich lai ket qua mot hoac nhieu run da tai ve tu Colab (KHONG fit, KHONG can dataset khi khong co --datasets-root).

  python analyze_results.py --run-dir RUN [RUN ...] --snapshot-dir SNAP --out-dir OUT [--datasets-root DATASETS]

Moi run: `verify_run_dir` (COMPLETE + hash tung file) -> cau hinh da giai (`config_resolved.json`) phai tu bang hash trong identity -> report -> validator artifact du doan -> nap snapshot da ghim
(co --datasets-root: nap day du va doi chieu dataset; khong co: nap doc lap, `price_velocity` lay tu artifact cua chinh run - gioi han duoc ghi trong manifest) -> `analyze_horizon`.
Ket qua ghi vao OUT/h{K}_analysis_<run>.json (khong ghi de, khong dong vao run). Ma thoat: 0 = moi run phan tich xong; 2 = loi (dung ngay, khong phan tich dang do).
Run no_valid_model (khong champion) duoc phan tich ma KHONG can predictions/snapshot (claim none + ly do). Run incomplete/no_valid_model van duoc phan tich (so do giu lam chan doan) nhung `analyze_horizon` dat claim_level='none' + claim_block_reasons=['training_contract_incomplete'] (cung logic voi luc huan luyen).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V1_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V1_DIR))

import pandas as pd  # noqa: E402

from cls_training.analysis import analyze_horizon  # noqa: E402
from cls_training.config import canonical_sha256, dataset_pin, load_config, snapshot_pin, validate_config  # noqa: E402
from cls_training.data import load_dataset  # noqa: E402
from cls_training.snapshot import load_snapshot, load_snapshot_standalone  # noqa: E402
from cls_training.transaction import canonical_bytes, verify_run_dir  # noqa: E402


def analyze_run(run_dir: Path, snapshot_dir: Path, out_dir: Path, datasets_root: Path | None) -> Path:
    manifest = verify_run_dir(run_dir)
    ident = manifest["identity"]
    h = int(ident["horizon"])
    cfg = json.loads((run_dir / "config_resolved.json").read_text(encoding="utf-8"))
    body = {k: v for k, v in cfg.items() if k != "config_sha256"}
    if canonical_sha256(body) != cfg.get("config_sha256") or cfg["config_sha256"] != ident["config_sha256"]:
        raise RuntimeError(f"{run_dir.name}: config_resolved.json khong khop config_sha256 trong identity")
    validate_config(body)
    report = json.loads((run_dir / f"h{h}_report.json").read_text(encoding="utf-8"))
    if (report.get("status"), bool(report.get("contract_complete"))) != (manifest.get("status"), bool(manifest.get("contract_complete"))):
        raise RuntimeError(f"{run_dir.name}: manifest ({manifest.get('status')}, {manifest.get('contract_complete')}) bat dong voi report ({report.get('status')}, {report.get('contract_complete')}); khong chon ben thuan loi")
    pred = pd.read_parquet(run_dir / f"h{h}_predictions.parquet") if f"h{h}_predictions.parquet" in manifest["files"] else pd.DataFrame()
    dpin, spin = dataset_pin(cfg, h), snapshot_pin(cfg, h)
    if ident.get("dataset_version") != dpin["dataset_version"] or ident.get("output_checksums_sha256") != dpin["output_checksums_sha256"]:
        raise RuntimeError(f"{run_dir.name}: identity.dataset khong khop pin trong cau hinh da giai")
    if report.get("champion") is None:                                        # no_valid_model: không có model ⇒ không cần predictions/snapshot/velocity; claim none, ghi lý do
        snap, snap_manifest = None, {"_validated": None, "note": "no_champion: snapshot không được dùng"}
    elif datasets_root is not None:
        loaded = load_dataset(datasets_root / dpin["dataset_version"], dpin, h, frame_cfg={"primary_requires_seen_hotel": bool(cfg["primary_requires_seen_hotel"])})
        snap, snap_manifest = load_snapshot(snapshot_dir, loaded, pin=spin)
    else:
        champ = report.get("champion")
        velocity = pd.Series(dtype="float64")
        if champ is not None and "price_velocity" in pred.columns:
            rows = pred[(pred["model_id"] == champ) & pred["is_primary"] & pred["split"].isin(("validation", "test"))]
            velocity = rows.drop_duplicates("warehouse_record_id").set_index("warehouse_record_id")["price_velocity"].astype("float64")
        snap, snap_manifest = load_snapshot_standalone(snapshot_dir, h, pin=spin, dataset_pin=dpin, price_velocity_by_record=velocity)
    result = analyze_horizon(pred, report, snap, snap_manifest, cfg)
    result["claim_block_reasons"] = (result.get("claim") or {}).get("claim_block_reasons", ["no_valid_model"] if report.get("champion") is None else [])
    result["training_contract_complete"] = bool(manifest.get("contract_complete"))
    result["training_status"] = manifest.get("status")
    result["source_run"] = {"run_id": manifest["run_id"], "snapshot_validation": snap_manifest["_validated"]}
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"h{h}_analysis_{manifest['run_id']}.json"
    if target.exists():
        raise RuntimeError(f"{target} da ton tai; khong ghi de")
    target.write_bytes(canonical_bytes(result))
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", nargs="+", required=True)
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--datasets-root", default=None)
    args = ap.parse_args(argv)
    for run in args.run_dir:
        target = analyze_run(Path(run), Path(args.snapshot_dir), Path(args.out_dir), None if args.datasets_root is None else Path(args.datasets_root))
        print(f"analysis -> {target}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"ANALYZE FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
