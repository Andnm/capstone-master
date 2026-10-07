"""Huan luyen/danh gia v3 (hop dong C1-C18; thread discuss/model-results-improvement-20261008): 3 ho L1/robust + controls + champion A/B + routing + null + TEST khoa.

    python ml/scripts/train_models_v3.py --dataset-dir outputs/datasets/ds_20261006_dev1b --device auto [--allow-env-drift] [--colab-manifest COLAB_MANIFEST.json]

v3 KHONG bao gio la `--official` (dev research, exploratory). Ket qua -> `<output-root>/<dataset_version>/<run_id>/h{k}_report.json` (+ du doan, bundle, ledger) trong GIAO DICH nhu v2
(thu muc tam -> doi ten nguyen tu khi PASS; loi => `<run_id>.failed-...`). v2 va artifact v2 BAT BIEN; ten run da ton tai => tu choi.

Fail-closed: kiem dataset/provenance VA moi truong (phien ban thu vien) TRUOC khi tao thu muc nao; moi truong lech phien ban da ghim => thoat 4 (tru khi --allow-env-drift, ghi
`exploratory_env_drift`). `--smoke` (kiem cuc bo): thu nho pool/ho/null, run-id phai bat dau `smoke_`, config_sha256 khac, khong phai ket qua.
Ma thoat: 0 ok, 1 loi luc chay, 2 tham so/thu muc ton tai, 3 dataset/provenance, 4 moi truong lech phien ban.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ML_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML_DIR))

from training.config import ConfigError  # noqa: E402
from training.provenance import DatasetVerificationError, ProvenanceError, environment_manifest  # noqa: E402
from training.run_transaction import ArgumentError, RunExistsError, RunTransaction, parse_selection, validate_run_id  # noqa: E402
from training.runner import build_context  # noqa: E402
from training.v3_contract import DEFAULT_CONFIG_V3, TRAINING_VERSION_V3, apply_smoke_overrides, load_config_v3, validate_horizon_support  # noqa: E402
from training.v3_runner import run_horizon_v3  # noqa: E402
from training.v3_runtime import EnvironmentDriftError, check_environment, verify_packaged_execution  # noqa: E402

DEFAULT_OUTPUT_ROOT = ML_DIR.parents[1] / "outputs" / "models"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_V3)
    parser.add_argument("--horizons", default="", help="mac dinh: chi horizon trong whitelist cua dataset")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=time.strftime("run_%Y%m%d_%H%M%S"))
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto", help="thiet bi XGBoost; smoke C8 quyet dinh va ghi fallback_reason")
    parser.add_argument("--allow-env-drift", action="store_true", help="opt-in TRUOC moi fit: cho chay khi lech phien ban (exploratory_env_drift, khong dung chung identity)")
    parser.add_argument("--colab-manifest", type=Path, default=None)
    parser.add_argument("--smoke", action="store_true", help="kiem cuc bo: thu nho; run-id phai bat dau smoke_; khong phai ket qua")
    parser.add_argument("--smoke-families", default="", help="vd hgb_l1 (chi cho --smoke)")
    parser.add_argument("--smoke-n-iter", type=int, default=None)
    parser.add_argument("--smoke-null-n", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        cfg = load_config_v3(args.config)
        if args.smoke:
            fams = [f.strip() for f in args.smoke_families.split(",") if f.strip()] or None
            cfg = apply_smoke_overrides(cfg, families=fams, n_iter=args.smoke_n_iter, null_n=args.smoke_null_n)
        elif args.smoke_families or args.smoke_n_iter is not None or args.smoke_null_n is not None:
            raise ArgumentError("--smoke-* chi hop le cung --smoke")
        configured = tuple(int(h) for h in cfg["horizons"])
        explicit = bool(args.horizons.strip())
        horizons = parse_selection(args.horizons, name="horizons", allowed=configured, default=list(configured), cast=int)
        run_id = validate_run_id(args.run_id)
        if args.smoke and not run_id.startswith("smoke_"):
            raise ArgumentError("--smoke yeu cau --run-id bat dau bang 'smoke_' (khong de lan voi run that)")
        if not args.smoke and run_id.startswith("smoke_"):
            raise ArgumentError("run-id 'smoke_*' danh rieng cho --smoke")
        if explicit:
            validate_horizon_support(cfg, horizons)           # horizon chi dinh ro: kiem som (control chua co tham so da chot cho horizon, vd RF-L2 h7/h14 => tu choi truoc moi output)
    except (ArgumentError, ConfigError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    dataset_root = args.output_root / args.dataset_dir.name
    if (dataset_root / run_id).exists():
        print(f"FAIL: thu muc run da ton tai: {dataset_root / run_id} - dung --run-id moi, khong ghi de artifact cu.", file=sys.stderr)
        return 2
    try:                                                      # dataset + provenance + rang buoc goi Colab + moi truong TRUOC khi tao bat ky thu muc nao
        context = build_context(args.dataset_dir, colab_manifest=args.colab_manifest, official_run=False)        # official=False: v3 KHONG bao gio official
        packaged = verify_packaged_execution(context["provenance"], context["colab_manifest"], context["dataset_meta"]["dataset_name"])   # strict lineage + tap file code (M1)
    except (DatasetVerificationError, ProvenanceError, OSError, ValueError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    try:
        env_status = check_environment(cfg, allow_drift=args.allow_env_drift)
    except EnvironmentDriftError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 4
    whitelist = context["dataset_meta"]["contract"]["evaluation_horizons"]
    if not explicit:
        horizons = [h for h in horizons if h in whitelist]
        if not horizons:
            print(f"FAIL: khong horizon nao cua cau hinh nam trong evaluation_horizons {whitelist} cua dataset.", file=sys.stderr)
            return 2
    outside = [h for h in horizons if h not in whitelist]
    if outside:                                                # v3 khong co duong override: horizon ngoai evaluation_horizons => tu choi TRUOC moi output (GPT file 16 MIN1)
        print(f"FAIL: horizon {outside} ngoai evaluation_horizons {whitelist} cua dataset (dataset_contract.json); train-v3 khong chay horizon ngoai whitelist.", file=sys.stderr)
        return 2
    try:
        validate_horizon_support(cfg, horizons)                # sau khi loc theo whitelist cua dataset: moi horizon se chay phai co tham so control da chot
    except ConfigError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    base = {"training_version": TRAINING_VERSION_V3, "official": False, "claim_level": "smoke_not_results" if args.smoke else "dev_research_exploratory",
            "smoke": bool(args.smoke), "config_sha256": cfg["config_sha256"], "config_version": cfg["version"], "evaluation_whitelist": whitelist,
            "packaged_execution": packaged, "environment_status": env_status["status"], "environment_fingerprint": env_status["fingerprint"],
            "dataset": {k: v for k, v in context["dataset_meta"].items() if k != "verified_file_sha256"}, "provenance": context["provenance"],
            "environment": context["environment"], "colab_manifest": context["colab_manifest"]}
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
        if manifest_path:
            shutil.copyfile(manifest_path, out_dir / "CODE_MANIFEST.json")
        if args.colab_manifest is not None:
            shutil.copyfile(args.colab_manifest, out_dir / "COLAB_MANIFEST.json")
        for h in horizons:
            report = run_horizon_v3(args.dataset_dir, h, cfg, out_dir, context=context, device_request=args.device, env_status=env_status)
            line = f"h{h}: status={report['status']} rows={report['rows']}"
            if report["status"] == "ok":
                a, b = report["champions"]["A"], report["champions"]["B"]
                line += f" champion_A={a['winner']} champion_B={b['winner']} contract_complete={report['contract_complete']} fits={report['ledger']['total_fits']}"
            elif report.get("reason"):
                line += f" reason={report['reason']}"
            print(line, flush=True)
            transaction.record_horizon({"horizon": h, "status": report["status"], "champion_A": (report.get("champions") or {}).get("A", {}).get("winner"),
                                        "champion_B": (report.get("champions") or {}).get("B", {}).get("winner"), "evaluation_status": report.get("evaluation_status"),
                                        "claim_level": report.get("claim_level"), "contract_complete": report.get("contract_complete")})
        final = transaction.commit()
    except BaseException as exc:  # noqa: BLE001 - ke ca Ctrl+C: danh dau fail, giu bang chung
        failed = transaction.fail(f"{type(exc).__name__}: {exc}")
        print(f"FAIL: run loi giua chung ({type(exc).__name__}: {exc}); giu bang chung o {failed}", file=sys.stderr)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return 1
    print(f"ket qua: {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
