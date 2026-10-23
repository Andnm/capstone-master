"""Dựng ỨNG VIÊN registry (công cụ dev, CHỈ ĐỌC artifact đã train; chạy bằng anaconda). KHÔNG BAO GIỜ ghi `registry.json` và KHÔNG tự cấp "validated" (GPT 10 M2).

    C:\\Users\\ADMIN\\anaconda3\\python.exe -B scripts\\build_registry.py                       # kiểm lại registry đã duyệt: in kết quả, mã thoát 0 nếu KHÔNG đổi
    C:\\Users\\ADMIN\\anaconda3\\python.exe -B scripts\\build_registry.py --candidate-out <file>  # ghi ứng viên để review (không được là registry.json, không ghi đè)

Mã thoát: 0 = ứng viên trùng registry đã duyệt; 2 = lỗi toàn vẹn (hash/provenance) — chưa deserialize; 3 = có ứng viên cần review (runtime/mã/bundle mới).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.ml_serving.errors import IntegrityError  # noqa: E402
from app.ml_serving.registry import REGISTRY_PATH, WORKSPACE_ROOT, current_runtime, sha256_file  # noqa: E402
from app.ml_serving.registry_tool import BundleSpec, build_candidate  # noqa: E402

PROJECT = WORKSPACE_ROOT / "hotel-price-intelligence"
RUN = "hotel-price-intelligence/ml/regression/models/train_v3_nf"
SPECS = [
    BundleSpec("reg-h1-hgb_l1-08", "regression_primary", 1, "ds_20261010_nf1", f"{RUN}/ds_20261010_nf1/run_20261010_152240", "h1_bundle_hgb_l1-08.joblib", "h1_predictions_v3.parquet", "h1_report.json", "hgb_l1-08"),
    BundleSpec("reg-h3-hgb_l1-ratio1", "regression_primary", 3, "ds_20261010_nf3", f"{RUN}/ds_20261010_nf3/run_20261010_152421", "h3_bundle_hgb_l1-ratio1.joblib", "h3_predictions_v3.parquet", "h3_report.json", "hgb_l1-ratio1"),
]


def rel(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE_ROOT).as_posix()


def code_dependencies() -> list[dict[str, str]]:
    sys.path.insert(0, str(PROJECT / "ml" / "regression"))
    sys.path.insert(0, str(PROJECT / "ml" / "shared"))
    before = set(sys.modules)
    from training import v3_bundle  # noqa: F401,E402
    root = os.path.normcase(str(PROJECT / "ml"))
    files = sorted({Path(sys.modules[m].__file__) for m in set(sys.modules) - before if getattr(sys.modules[m], "__file__", None) and os.path.normcase(sys.modules[m].__file__).startswith(root)})
    return [{"path": rel(p), "sha256": sha256_file(p)} for p in files]


def datasets_and_nullable() -> tuple[list[dict], list[str]]:
    import pandas as pd

    datasets, nullable_sets = [], []
    for spec in SPECS:
        ds_dir = WORKSPACE_ROOT / "outputs" / "datasets" / spec.dataset
        checks = json.loads((ds_dir / "output_checksums.json").read_text(encoding="utf-8"))["samples.parquet"]
        if sha256_file(ds_dir / "samples.parquet") != checks["file_sha256"]:
            raise IntegrityError(f"samples.parquet không khớp output_checksums.json: {spec.dataset}")
        datasets.append({"id": spec.dataset, "path": rel(ds_dir), "horizon": spec.horizon, "samplesFileSha256": checks["file_sha256"], "samplesRows": checks["rows"]})
        run_dir = WORKSPACE_ROOT / spec.run_rel
        # Danh sách feature lấy từ report (đã ghim hash trong registry), KHÔNG deserialize bundle ở bước này.
        features = json.loads((run_dir / spec.report).read_text(encoding="utf-8"))["features"]["list"]
        train = pd.read_parquet(ds_dir / "samples.parquet")
        train = train[train["split"] == "train"]
        nullable_sets.append({f for f in features if train[f].isna().any()})
    if nullable_sets[0] != nullable_sets[1]:
        raise IntegrityError("nullable features h1 != h3")
    return datasets, sorted(nullable_sets[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate-out", type=Path, default=None)
    args = parser.parse_args()
    if args.candidate_out is not None and args.candidate_out.resolve() == REGISTRY_PATH.resolve():
        print("TỪ CHỐI: công cụ không bao giờ ghi registry.json (chỉ ứng viên để review)")
        return 2
    existing = json.loads(REGISTRY_PATH.read_text(encoding="utf-8")) if REGISTRY_PATH.exists() else None
    try:
        datasets, nullable = datasets_and_nullable()
        result = build_candidate(workspace_root=WORKSPACE_ROOT, existing=existing, specs=SPECS, code_dependencies=code_dependencies(), nullable_features=nullable,
                                 datasets=datasets, runtime=current_runtime())
    except IntegrityError as exc:
        print(f"LỖI TOÀN VẸN (chưa deserialize bundle): {exc}")
        return 2
    for finding in result.findings:
        print("-", finding)
    if args.candidate_out is not None:
        if args.candidate_out.exists():
            print(f"đã tồn tại, không ghi đè: {args.candidate_out}")
            return 2
        args.candidate_out.write_text(json.dumps(result.registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"đã ghi ứng viên: {args.candidate_out}")
    if result.approved and existing is not None and result.registry == existing:
        print("registry.json đã duyệt: KHÔNG ĐỔI (ứng viên trùng khớp)")
        return 0
    print("CÓ ỨNG VIÊN CẦN REVIEW (không tự cấp validated)" if not result.approved else "ứng viên khác registry đã duyệt — cần review")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
