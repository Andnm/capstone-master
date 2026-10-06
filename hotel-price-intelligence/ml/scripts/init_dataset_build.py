"""Tao `dataset_version` moi (spec muc 3b buoc 1, 19): ghi NGAY toan bo cau hinh bat bien vao `dataset_build_manifests`.

    python ml/scripts/init_dataset_build.py --database warehouse_20261004_3src --dataset-version ds_20261015_dev1 \\
        --purpose dev [--batch-id b20261004_3src] [--anomaly-mode evaluation_asof] [--anomaly-cutoff 2026-10-15T00:00:00Z] \\
        [--purge-gap-days 14] [--required-label h1:train,validation,test] [--exclude-hotel SLUG ...] [--dry-run]

Nguong reference lay tu `etl_config` da PIN cua batch (khong doc `.env` am tham). Doi bat ky tham so nao = version moi.
Chay bang `eda/.venv/Scripts/python.exe` (can pyarrow cho cac buoc sau). Khong dong vao DB van hanh.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset_builder import config as cfg  # noqa: E402
from dataset_builder import manifest  # noqa: E402
from dataset_builder.code_identity import CodeIdentityError, assert_official_clean  # noqa: E402
from dataset_builder.db import connect, fetch_all, utc_now  # noqa: E402
from dataset_builder.n1_policy import N1PolicyError, policy_descriptor  # noqa: E402


def _parse_required(items: list[str] | None) -> dict[str, list[str]] | None:
    if not items:
        return None
    out: dict[str, list[str]] = {}
    for item in items:
        horizon, _, splits = item.partition(":")
        out[horizon] = [s for s in splits.split(",") if s]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Tao dataset_version moi (init_dataset_build).")
    parser.add_argument("--database", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--purpose", required=True, choices=cfg.PURPOSES)
    parser.add_argument("--batch-id")
    parser.add_argument("--anomaly-mode", default="evaluation_asof", choices=cfg.ANOMALY_MODES)
    parser.add_argument("--anomaly-cutoff", help="ISO UTC, vd 2026-10-15T00:00:00Z (mac dinh: bay gio, bat buoc voi evaluation_asof)")
    parser.add_argument("--evaluation-horizons", default="1,3,7,14",
                        help="horizon duoc DANH GIA cua build (build rieng theo horizon: --evaluation-horizons 7 voi purge = 7); mac dinh 1,3,7,14 = build shared audit")
    parser.add_argument("--purge-gap-days", type=int, default=None, help="mac dinh = max(evaluation_horizons); nho hon => loi")
    parser.add_argument("--random-seed", type=int, default=20261005)
    parser.add_argument("--required-label", action="append", help="h1:train,validation,test (lap lai cho tung horizon)")
    parser.add_argument("--exclude-hotel", action="append", default=[])
    parser.add_argument("--n1-policy", type=Path, default=None,
                        help="policy N1 (ml/policies/n1/n1_policy_v1.json): ghim hash policy + bang chung, loai hotel cua policy qua moi regime; BAT BUOC voi --purpose official")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:                                                   # invariant horizon/purge: kiem TRUOC moi ket noi DB (khong ghi gi khi sai)
        try:                                               # CLI nhan chuoi => parse so nguyen o day; config/ham thu vien chi nhan so nguyen that
            parsed = [int(x) for x in args.evaluation_horizons.split(",") if x.strip()]
        except ValueError as exc:
            raise ValueError(f"--evaluation-horizons phai la danh sach so nguyen cach nhau boi dau phay, nhan {args.evaluation_horizons!r}") from exc
        evaluation_horizons = cfg.normalize_evaluation_horizons(parsed)
        cfg.validate_horizon_contract(evaluation_horizons, max(evaluation_horizons) if args.purge_gap_days is None else args.purge_gap_days)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    n1_descriptor = None
    try:                                                   # policy N1: doc + kiem moi hash TRUOC moi ket noi DB (thieu/lech => exit 2, khong ghi gi)
        if args.n1_policy is not None:
            n1_descriptor = policy_descriptor(args.n1_policy)
        elif args.purpose == "official":
            raise N1PolicyError("--purpose official bat buoc --n1-policy (ghim bang chung + danh sach loai tru).")
    except N1PolicyError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    with connect(args.database) as conn:
        batches = fetch_all(conn, "SELECT batch_id FROM etl_import_batches WHERE status='pass' ORDER BY started_at")
        conn.commit()
        if args.batch_id is None:
            if len(batches) != 1:
                print(f"FAIL: can --batch-id; batch PASS trong DB: {[b['batch_id'] for b in batches]}", file=sys.stderr)
                return 2
            args.batch_id = batches[0]["batch_id"]
        thresholds = manifest.resolve_pinned_thresholds(conn, args.batch_id)
        cutoff = None
        if args.anomaly_mode == "evaluation_asof":
            cutoff = (dt.datetime.fromisoformat(args.anomaly_cutoff.replace("Z", "")) if args.anomaly_cutoff else utc_now())
        config = cfg.build_config(
            import_batch_id=args.batch_id, purpose=args.purpose, anomaly_mode=args.anomaly_mode, anomaly_cutoff_at=cutoff,
            anomaly_registry_file_sha256=cfg.default_registry_sha256(), random_seed=args.random_seed,
            purge_gap_days=args.purge_gap_days, evaluation_horizons=evaluation_horizons, exclude_hotels=tuple(args.exclude_hotel), n1_policy=n1_descriptor,
            required_label_splits=_parse_required(args.required_label), **thresholds)
        try:
            assert_official_clean(config)             # official: moi file thuoc danh tinh ma phai sach (git) ngay tu luc init
        except CodeIdentityError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 2
        print(f"build_config_sha256 = {cfg.config_sha256(config)}")
        print(f"builder_code_sha256 = {config['builder_code']['code_sha256']} ({len(config['builder_code']['files'])} file)")
        print(f"purpose={config['purpose']} batch={args.batch_id} anomaly={config['anomaly']} purge={config['purge_gap_days']} evaluation_horizons={config['evaluation_horizons']} "
              f"required_labels={config['pass_requirements']['required_label_splits']}")
        if args.dry_run:
            print(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True))
            print("[dry-run] khong ghi gi.")
            return 0
        row = manifest.init_dataset_build(conn, dataset_version=args.dataset_version, config=config)
        print(f"Da tao {row['dataset_version']} (status={row['status']}, last_completed_step={row['last_completed_step']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
