"""Preflight du lieu THAT, KHONG FIT (PLAN §4 cong 1): toan ven dataset + pin, validator nhan 100%, khung primary, fold CV, feature, snapshot, panel C0-C3.

  python preflight_real_data.py [--config ...] [--datasets-root ...] [--snapshot-dir ...] [--upstream-check packaged|dev|both] [--out report.json]
- Cau hinh duoc VALIDATE (moi horizon dung 1 dataset pin + 1 snapshot pin; nhan co dinh). Khong co che do chay khong pin.
- `--upstream-check packaged` (mac dinh, di dong, khong can nguon regression/shared): ban copy == UPSTREAM_MANIFEST.json. `dev`/`both`: them doi chieu voi nguon HIEN TAI (chi o may chinh).
Exit 0 chi khi moi buoc qua; loi => exit 2 va in ly do. Khong ghi vao dataset/regression; chi ghi `--out`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

V1_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V1_DIR))

from cls_training.codeid import code_manifest, environment_summary  # noqa: E402
from cls_training.config import dataset_pin, load_config, snapshot_pin  # noqa: E402
from cls_training.data import dataset_sufficiency, describe_folds, load_dataset, make_folds  # noqa: E402
from cls_training.features import dry_run as feature_dry_run, feature_sets  # noqa: E402
from cls_training.panel import evaluate_panel  # noqa: E402
from cls_training.snapshot import load_snapshot  # noqa: E402
from cls_training import upstream_sync  # noqa: E402

ML_DIR = V1_DIR.parents[1]
WORKSPACE = ML_DIR.parents[1]


def run_horizon(cfg: dict, h: int, datasets_root: Path, snapshot_dir: Path) -> dict:
    t0 = time.time()
    pin = dataset_pin(cfg, h)
    loaded = load_dataset(datasets_root / pin["dataset_version"], pin, h, frame_cfg={"primary_requires_seen_hotel": bool(cfg["primary_requires_seen_hotel"])})
    train = loaded.frames["train"]
    folds = describe_folds(train, make_folds(train, h, cfg["cv"]), h)
    usable_dates = {sp: int(loaded.frames[sp]["vn_observation_date"].nunique()) for sp in ("train", "validation", "test")}
    sets = feature_sets(loaded.features, loaded.dictionary)
    if len(sets["a2_no_history"]) != int(cfg["ablation"]["a2_n_features"]):
        raise RuntimeError(f"A2 co {len(sets['a2_no_history'])} cot, config yeu cau {cfg['ablation']['a2_n_features']}")
    dry = feature_dry_run({sp: loaded.frames[sp] for sp in ("train", "validation", "test")}, sets)        # KHONG fit mo hinh, KHONG doc nhan
    snap, manifest = load_snapshot(snapshot_dir, loaded, pin=snapshot_pin(cfg, h))            # pin BAT BUOC
    panel = evaluate_panel(snap, manifest, tie_atol=float(cfg["panel"]["tie_atol"]), momentum_threshold=float(cfg["panel"]["momentum_threshold"]))
    return {"horizon": h, "dataset_version": pin["dataset_version"], "entries_verified": loaded.entries_verified, "n_features": len(loaded.features),
            "label_report": loaded.label_report, "rows_all_hotels": loaded.frames_info["rows_all_hotels"], "rows_primary": loaded.frames_info["rows_primary"],
            "excluded_unseen_hotel_rows": loaded.frames_info["excluded_unseen_hotel_rows"], "excluded_unseen_hotels": loaded.frames_info["excluded_unseen_hotels"],
            "eligible_prediction_dates": usable_dates, "short_window": usable_dates["validation"] < 7 or usable_dates["test"] < 7,
            "sufficiency": {k: dataset_sufficiency(loaded)[k] for k in ("status", "failed_gates", "has_failed_gates")}, "cv_folds": folds, "feature_dry_run": dry, "feature_set_sizes": {k: len(v) for k, v in sets.items()},
            "snapshot": {"rows": manifest["rows"], "pin_checked": manifest["_validated"]["pin_checked"], "champions": manifest["champions"],
                         "prediction_columns": manifest["prediction_columns"], "prediction_scored_splits": manifest["prediction_scored_splits"]},
            "panel": panel, "seconds": round(time.time() - t0, 1)}


def upstream_report(mode: str) -> dict:
    problems: list[str] = []
    if mode in ("packaged", "both"):
        problems += [f"[packaged] {p}" for p in upstream_sync.verify_packaged()]
    if mode in ("dev", "both"):
        problems += [f"[dev] {p}" for p in upstream_sync.verify_dev_sources()]
    if problems:
        raise RuntimeError("UPSTREAM LECH: " + "; ".join(problems))
    return {"mode": mode, "manifest_sha256_lf": upstream_sync.manifest_sha256(), "verified": True}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--datasets-root", default=str(WORKSPACE / "outputs" / "datasets"))
    ap.add_argument("--snapshot-dir", default=str(ML_DIR / "classification" / "results" / "v1" / "baseline_snapshot"))
    ap.add_argument("--upstream-check", choices=("packaged", "dev", "both"), default="packaged")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)                                  # validate: pin thieu/trung, nhan/nguong sai => ConfigError
    report = {"config_sha256": cfg["config_sha256"], "config_stage": cfg.get("config_stage"), "code": {k: v for k, v in code_manifest(exclude_tests=True).items() if k != "files"},
              "environment": environment_summary(), "upstream": upstream_report(args.upstream_check), "horizons": {}}
    for h in cfg["horizons"]:
        report["horizons"][f"h{h}"] = run_horizon(cfg, int(h), Path(args.datasets_root), Path(args.snapshot_dir))
    for key, r in report["horizons"].items():
        print(f"== {key} {r['dataset_version']} | entries {r['entries_verified']} | features {r['n_features']} | primary {r['rows_primary']} (all-hotels {r['rows_all_hotels']})"
              f" | dates {r['eligible_prediction_dates']} short_window={r['short_window']} | sufficiency {r['sufficiency']['status']} failed_gates={r['sufficiency']['failed_gates']}")
        for f in r["cv_folds"]:
            print(f"   fold {f['fold']}: train {f['n_train']} val {f['n_val']} ({f['val_days']} ngay {f['val_start']}..{f['val_end']}) support {f['val_support']}")
        p = r["panel"]
        print(f"   validation comparator: {p['validation_comparator']} val {p['validation_comparator_validation_macro_f1']:.4f} | cham tren test: {p['validation_comparator_scored_on_test']}"
              f" | test-available comparator: {p['test_available_comparator']} (thay the={p['is_substitute']}) val {p['test_available_comparator_validation_macro_f1']:.4f}"
              f" -> test {p['test_available_comparator_test_macro_f1']:.4f}")
        print(f"   diagnostic oracle tren test: {p['diagnostic_oracle_on_test']} ({p['oracle_test_macro_f1']:.4f}) | alias: {p['aliases']} | KHONG duoc cham tren test: {p['members_not_scored_on_test']}")
        for row in p["table"]:
            v, t = row["validation"], row["test"]
            tv = f"test F1 {t['macro_f1']:.4f} MCC {t['mcc']:.4f}" if t else "test       n/a (khong duoc cham)"
            print(f"     {row['id']:34s} val F1 {v['macro_f1']:.4f} MCC {v['mcc']:.4f} | {tv}")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=float) + "\n", encoding="utf-8", newline="\n")
        print("da ghi", out)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - preflight fail-closed, in ly do ro rang
        print(f"PREFLIGHT FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
