"""Huan luyen/danh gia hoi quy gia ngan han tren mot dataset da xuat (Phase 4).

    python ml/scripts/train_models.py --dataset-dir outputs/datasets/ds_20261006_rh1 --horizons 7 --models ridge,rf,xgb [--device cuda] [--official]

Moi horizon -> `<output-root>/<dataset_version>/<run_id>/h{k}_report.json` (+ model/predictions/metric theo nhom) va mot `run_manifest.json` +
`environment_resolved.txt` + ban sao `CODE_MANIFEST.json`/`COLAB_MANIFEST.json` (neu co) cho ca run. `--config` mac dinh `ml/configs/train_v1.yaml`. Doc Parquet,
KHONG doc DB. Seed va sieu tham so lay tu cau hinh.

Fail-closed (GPT review vong 1-2): hash lai cac file dataset theo `output_checksums.json` truoc khi doc va TRUOC khi tao thu muc nao; kiem `CODE_MANIFEST.json`
(goi Colab, tinh lai aggregate) hoac ghi git HEAD/dirty; `--official` doi provenance code xac dinh va sach. Run la GIAO DICH (training/run_transaction.py): xay trong
thu muc tam, loi -> `<run_id>.failed-...` (state fail), chi khi moi horizon xong + checksum tung file moi doi ten nguyen tu thanh `<run_id>`; ten da ton tai => tu choi.
Ma thoat: 0 ok, 1 loi trong luc chay (run bi danh dau fail), 2 tham so/thu muc da ton tai, 3 dataset/provenance khong qua xac minh.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ML_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML_DIR))

from training.config import DEFAULT_CONFIG_PATH, apply_overrides, load_config  # noqa: E402
from training.provenance import (  # noqa: E402
    DatasetVerificationError, ProvenanceError, environment_manifest, require_known_provenance, require_lineage,
)
from training.run_transaction import ALLOWED_MODELS, ArgumentError, RunExistsError, RunTransaction, parse_selection, validate_run_id  # noqa: E402
from training.runner import build_context, run_horizon  # noqa: E402

DEFAULT_OUTPUT_ROOT = ML_DIR.parents[1] / "outputs" / "models"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--horizons", default="", help="vd 1,3,7,14 (mac dinh: theo cau hinh; phai thuoc horizon cua cau hinh)")
    parser.add_argument("--models", default="ridge,rf,xgb", help="tap con khong rong cua ridge,rf,xgb (baseline luon chay)")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=time.strftime("run_%Y%m%d_%H%M%S"))
    parser.add_argument("--device", choices=["cpu", "cuda"], default=None,
                        help="thiet bi cho XGBoost (Colab GPU: cuda). Ghi vao config_sha256 cua bao cao; RF/Ridge luon chay CPU")
    parser.add_argument("--official", action="store_true", help="run chinh thuc: doi provenance code xac dinh (goi Colab co CODE_MANIFEST hoac git sach)")
    parser.add_argument("--colab-manifest", type=Path, default=None, help="COLAB_MANIFEST.json cua goi (ghi hash + noi dung vao moi bao cao, copy vao run)")
    args = parser.parse_args()
    cfg = apply_overrides(load_config(args.config), device=args.device)
    try:
        configured = tuple(int(h) for h in cfg["horizons"])
        horizons = parse_selection(args.horizons, name="horizons", allowed=configured, default=list(configured), cast=int)
        models = parse_selection(args.models, name="models", allowed=ALLOWED_MODELS, default=None)
        run_id = validate_run_id(args.run_id)
    except ArgumentError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    dataset_root = args.output_root / args.dataset_dir.name
    if (dataset_root / run_id).exists():
        print(f"FAIL: thu muc run da ton tai: {dataset_root / run_id} - dung --run-id moi, khong ghi de artifact cu.", file=sys.stderr)
        return 2
    try:                                                      # xac minh dataset + provenance TRUOC khi tao bat ky thu muc nao
        context = build_context(args.dataset_dir, colab_manifest=args.colab_manifest)
        require_known_provenance(context["provenance"], official=args.official)
        require_lineage(context["provenance"], context["colab_manifest"], official=args.official)
    except (DatasetVerificationError, ProvenanceError, OSError, ValueError) as exc:    # ke ca manifest/dataset khong doc duoc
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    base ={"official": bool(args.official), "config_sha256": cfg["config_sha256"], "models": models,
            "dataset": {k: v for k, v in context["dataset_meta"].items() if k != "verified_file_sha256"},
            "provenance": context["provenance"], "environment": context["environment"], "colab_manifest": context["colab_manifest"]}
    transaction = RunTransaction(dataset_root, run_id, base, horizons)
    try:
        out_dir = transaction.start()
    except RunExistsError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    try:
        resolved = environment_manifest(out_dir / "environment_resolved.txt")
        if resolved["environment_sha256"] != context["environment"]["environment_sha256"]:
            raise ProvenanceError("bang moi truong doi trong luc khoi tao run (hash khac).")
        manifest_path = context["provenance"].get("manifest_path")
        if manifest_path:                                     # artifact tu chua dinh danh ma (R2-M2): copy NGUYEN byte CODE_MANIFEST.json
            shutil.copyfile(manifest_path, out_dir / "CODE_MANIFEST.json")
        if args.colab_manifest is not None:
            shutil.copyfile(args.colab_manifest, out_dir / "COLAB_MANIFEST.json")
        for h in horizons:
            report = run_horizon(args.dataset_dir, h, cfg, out_dir, models, context=context)
            line = f"h{h}: status={report['status']} rows={report['rows']}"
            if report.get("selected_model"):
                test = report["results"][report["selected_model"]]["test"]
                line += f" selected={report['selected_model']} test_acc@20={test['accuracy_at_tol']:.3f} mae={test['mae']:.0f} eval={report['evaluation_status']}"
            elif report.get("reason"):
                line += f" reason={report['reason']}"
            print(line, flush=True)
            transaction.record_horizon({"horizon": h, "status": report["status"], "selected_model": report.get("selected_model"),
                                        "evaluation_status": report["evaluation_status"]})
        final = transaction.commit()
    except BaseException as exc:  # noqa: BLE001 - ke ca Ctrl+C: danh dau fail, giu bang chung, khong de lai thu muc trong ten cuoi cung
        failed = transaction.fail(f"{type(exc).__name__}: {exc}")
        print(f"FAIL: run loi giua chung ({type(exc).__name__}: {exc}); giu bang chung o {failed}", file=sys.stderr)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return 1
    print(f"ket qua: {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
