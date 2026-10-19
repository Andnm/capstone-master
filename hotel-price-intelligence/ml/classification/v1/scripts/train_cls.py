"""Huan luyen + danh gia classification v1 tren Colab (nguoi dung chay). CHAN fit that o may chinh.

  python train_cls.py --datasets-root /content/datasets --snapshot-dir /content/snapshot --out-root /content/out --drive-dir /content/drive/MyDrive/.../models/v1 [--horizons 1 3] [--device cuda]

- Cau hinh duoc VALIDATE; moi horizon can dung 1 dataset pin + 1 snapshot pin (khong co che do khong pin). Dataset qua kiem toan ven + pin + validator nhan; snapshot qua kiem pin/authority/bitwise.
- Fit that CHI chay tren Colab (co bien moi truong COLAB_*/module google.colab); may khac thoat 2 truoc khi doc dataset. Day la guard van hanh, khong phai ranh gioi bao mat; khong co co ep.
- TRUOC khi nap dataset: smoke toan bo duong huan luyen tren fixture tong hop (cung device/moi truong ghim) + kiem moi truong (phien ban scikit-learn/numpy/pandas/xgboost == ban ghim trong config, fail-closed) + van tay tap ung vien; roi self-test XGBoost THAT tren du lieu tong hop (thiet bi that tu Booster, trong so, luu/nap). Loi => dung, khong am tham bo XGBoost.
- `--device` doi `run.device` (cuda|cpu) va TINH LAI config_sha256 (doi thiet bi la doi danh tinh cau hinh).
- Resume NGU NGHIA: horizon da co run THANH CONG (complete + contract_complete + analysis + artifact hop le) cung identity o out-root hoac Drive thi duoc dung lai/khoi phuc (khong fit).
  Ket qua chep len --drive-dir co kiem lai hash roi moi danh dau hoan tat.
- Ma thoat: 0 = moi horizon contract_complete; 3 = co horizon incomplete / no_valid_model (CHUA phai ket qua nghien cuu hoan tat; xem dong TRAIN_STATUS); 2 = loi/fail-closed.
  Dong cuoi luon la `TRAIN_STATUS {json}` (cung ghi vao `<out-root>/train_status_<run-id>.json` neu thu muc ghi duoc).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

V1_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V1_DIR))

from cls_training.config import dataset_pin, load_config, override_device, snapshot_pin  # noqa: E402
from cls_training.data import load_dataset  # noqa: E402
from cls_training.envcheck import check_environment  # noqa: E402
from cls_training.pipeline import train_horizon  # noqa: E402
from cls_training.selftest import run_selftest, run_smoke  # noqa: E402
from cls_training.snapshot import load_snapshot  # noqa: E402
from cls_training.transaction import validate_run_id  # noqa: E402
from cls_training.upstream_sync import verify_packaged  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_INCOMPLETE = 0, 2, 3


def on_colab() -> bool:
    if any(k.startswith("COLAB_") for k in os.environ):
        return True
    try:
        import google.colab  # noqa: F401
        return True
    except ImportError:
        return False


def emit_status(status: dict, out_root: str | None, run_id: str | None) -> None:
    line = json.dumps(status, sort_keys=True, ensure_ascii=False)
    if out_root and run_id:
        try:
            Path(out_root).mkdir(parents=True, exist_ok=True)
            (Path(out_root) / f"train_status_{run_id}.json").write_text(line + "\n", encoding="utf-8")
        except OSError:
            pass
    print(f"TRAIN_STATUS {line}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None)
    ap.add_argument("--datasets-root", required=True)
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--drive-dir", default=None)
    ap.add_argument("--horizons", nargs="+", type=int, default=None)
    ap.add_argument("--device", choices=("cpu", "cuda"), default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--selftest-only", action="store_true", help="chi chay self-test XGBoost that tren du lieu tong hop roi thoat")
    args = ap.parse_args(argv)
    if not on_colab():
        print("TU CHOI: fit that chi chay tren Colab (khong thay COLAB_* / google.colab). May chinh khong train that; dung preflight_real_data.py.", file=sys.stderr)
        return EXIT_ERROR
    cfg = load_config(args.config)
    if args.device:
        cfg = override_device(cfg, args.device)
    problems = verify_packaged()
    if problems:
        print("UPSTREAM LECH:", *problems, sep="\n  ", file=sys.stderr)
        return EXIT_ERROR
    run_id = validate_run_id(args.run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    horizons = args.horizons or list(cfg["horizons"])
    unknown = [h for h in horizons if h not in cfg["horizons"]]
    if unknown:
        print(f"horizon {unknown} khong nam trong cau hinh {cfg['horizons']}", file=sys.stderr)
        return EXIT_ERROR
    env = check_environment(cfg)                                              # lech phien ban da ghim => dung (notebook cai dung roi Restart session); khong co co cho phep lech
    if env["mismatch"]:
        print(f"MOI TRUONG LECH phien ban da ghim: {env['mismatch']}", file=sys.stderr)
        print(f"  cai dung: pip install {env['pip_install']}  roi Runtime -> Restart session", file=sys.stderr)
        return EXIT_ERROR
    selftest = run_selftest(cfg)                                              # loi => TRAIN FAIL (exit 2) truoc khi cham dataset that
    print(f"selftest xgboost {selftest['xgboost_version']}: {selftest['fits']}", flush=True)
    smoke = run_smoke(cfg)                                                    # toan bo duong huan luyen tren fixture tong hop, cung device/moi truong ghim; loi => dung truoc khi cham dataset that
    print(f"smoke tong hop: champion={smoke['champion']} models={smoke['n_models']} devices={smoke['devices']} {smoke['elapsed_seconds']}s", flush=True)
    selftest = {**selftest, "smoke": smoke}
    if args.selftest_only:
        emit_status({"run_id": run_id, "outcome": "selftest_ok", "selftest": selftest}, None, None)
        return EXIT_OK
    results = []
    for h in horizons:
        pin = dataset_pin(cfg, h)
        loaded = load_dataset(Path(args.datasets_root) / pin["dataset_version"], pin, h, frame_cfg={"primary_requires_seen_hotel": bool(cfg["primary_requires_seen_hotel"])})
        snap = load_snapshot(Path(args.snapshot_dir), loaded, pin=snapshot_pin(cfg, h))
        print(f"== h{h} {pin['dataset_version']}: primary {loaded.frames_info['rows_primary']} | device {cfg['run']['device']} | config {cfg['config_sha256'][:12]}", flush=True)
        res = train_horizon(cfg, loaded, snap, args.out_root, run_id=run_id, drive_root=args.drive_dir)
        results.append({k: res[k] for k in ("horizon", "dataset_version", "status", "contract_complete", "resumed_from", "run_dir", "drive_run_dir")})
        print(f"   status={res['status']} contract_complete={res['contract_complete']} resumed_from={res['resumed_from']} run={res['run_dir']} drive={res['drive_run_dir']}", flush=True)
    incomplete = [r["horizon"] for r in results if not r["contract_complete"]]
    outcome = "complete" if not incomplete else "incomplete"
    emit_status({"run_id": run_id, "outcome": outcome, "config_sha256": cfg["config_sha256"], "device": cfg["run"]["device"], "incomplete_horizons": incomplete, "horizons": results,
                 "selftest": selftest}, args.out_root, run_id)
    if incomplete:
        print("CHUA HOAN TAT: horizon", incomplete, "- xem incomplete_reasons trong report; KHONG coi la ket qua nghien cuu hoan tat.", file=sys.stderr)
        return EXIT_INCOMPLETE
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - fail-closed, in ly do ro rang
        print(f"TRAIN FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR)
