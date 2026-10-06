"""Dong goi toi thieu de huan luyen tren Google Colab (khong can MySQL/backend): code huan luyen + cau hinh (+ tuy chon dataset Parquet).

    python ml/scripts/package_for_colab.py [--dataset-dir outputs/datasets/<dataset_version>] [--out outputs/colab]

Tao `ml_train_pkg_<timestamp>.zip` (thu muc goc `ml/`: training/, scripts/train_models.py, configs/, requirements-train.txt, `CODE_MANIFEST.json` (hash tung file + code_sha256) va chi 3 file cua
`dataset_builder` ma `training` can: __init__, feature_spec, dictionary) va, neu co `--dataset-dir`, `dataset_<version>.zip`. Ghi `COLAB_MANIFEST.json`
(kich thuoc + SHA-256 tung zip) de doi chieu sau khi tai len Drive; tu `schema_version` 2 con ghi `code_manifest_sha256` (SHA-256 bytes cua `ml/CODE_MANIFEST.json`
trong zip) va `code_sha256` (aggregate) de `train_models.py --official` noi manifest nay mat ma voi DUNG code dang chay (R3-M2). Dataset chi gom 8 file bat buoc (5 dau ra + bang chung lich `calendar_input.json`, `inputs/vn_holidays.csv` + `dataset_contract.json` whitelist horizon), khong kem `reports/` hay `tmp/`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import zipfile
from pathlib import Path

ML_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ML_DIR.parents[1]
BUILDER_FILES = ("__init__.py", "feature_spec.py", "dictionary.py")
COLAB_MANIFEST_SCHEMA = 2
# R4-m1: TAT CA bat buoc (kiem du truoc khi tao bat ky zip nao); gom hai bang chung lich cua R3-M1. Khong kem `reports/` (khong phai input huan luyen).
DATASET_FILES = ("samples.parquet", "data_dictionary.csv", "sufficiency_report.json", "output_checksums.json", "coverage_report.json",
                 "calendar_input.json", "inputs/vn_holidays.csv", "dataset_contract.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_info() -> dict:
    import subprocess
    try:
        head = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=all", "--", "hotel-price-intelligence/ml"],
                               capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        return {"head": head, "ml_dirty": bool(dirty), "ml_dirty_files": len(dirty.splitlines()) if dirty else 0}
    except Exception as exc:  # noqa: BLE001
        return {"head": None, "error": f"{type(exc).__name__}: {exc}"}


def build_code_manifest(files: list[tuple[Path, str]], stamp: str) -> dict:
    hashes = {arcname: _sha256(path) for path, arcname in files}
    canonical = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"created_at": stamp, "files": hashes, "code_sha256": hashlib.sha256(canonical).hexdigest(), "git": _git_info()}


def package_files() -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for path in sorted((ML_DIR / "training").glob("*.py")):
        files.append((path, f"ml/training/{path.name}"))
    for name in BUILDER_FILES:
        files.append((ML_DIR / "dataset_builder" / name, f"ml/dataset_builder/{name}"))
    for path in sorted((ML_DIR / "configs").glob("*.yaml")):
        files.append((path, f"ml/configs/{path.name}"))
    files.append((ML_DIR / "scripts" / "train_models.py", "ml/scripts/train_models.py"))
    files.append((ML_DIR / "requirements-train.txt", "ml/requirements-train.txt"))
    return files


def build_package(out_dir: Path, dataset_dir: Path | None = None, *, stamp: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or time.strftime("%Y%m%d_%H%M%S")
    manifest: dict = {"schema_version": COLAB_MANIFEST_SCHEMA, "created_at": stamp, "archives": {}}
    if dataset_dir is not None:                                  # thieu bat ky file bat buoc nao => loi TRUOC khi tao zip (khong de lai goi do dang)
        missing = [name for name in DATASET_FILES if not (Path(dataset_dir) / name).is_file()]
        if missing:
            raise FileNotFoundError(f"dataset thieu {missing} o {dataset_dir} - dung dataset do builder >= 1.3.0 xuat (co bang chung lich)")
    code_zip = out_dir / f"ml_train_pkg_{stamp}.zip"
    files = package_files()
    for path, _ in files:
        if not path.exists():
            raise FileNotFoundError(f"thieu file dong goi: {path}")
    code_manifest = build_code_manifest(files, stamp)
    with zipfile.ZipFile(code_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in files:
            archive.write(path, arcname)
        # CODE_MANIFEST.json (GPT review TR-M4): tren Colab khong co Git; train_models.py xac minh tung file dang chay khop manifest nay
        # va ghi `code_sha256` vao moi bao cao/model. Khong tu liet ke chinh no.
        code_manifest_bytes = json.dumps(code_manifest, indent=2, sort_keys=True).encode("utf-8")
        archive.writestr("ml/CODE_MANIFEST.json", code_manifest_bytes)
    manifest["code_manifest_sha256"] = hashlib.sha256(code_manifest_bytes).hexdigest()      # bytes DUNG nhu trong zip
    manifest["code_sha256"] = code_manifest["code_sha256"]
    manifest["archives"][code_zip.name] = {"bytes": code_zip.stat().st_size, "sha256": _sha256(code_zip)}
    if dataset_dir is not None:
        dataset_dir = Path(dataset_dir)
        data_zip = out_dir / f"dataset_{dataset_dir.name}.zip"
        with zipfile.ZipFile(data_zip, "w", zipfile.ZIP_STORED) as archive:  # Parquet da nen san
            for name in DATASET_FILES:
                archive.write(dataset_dir / name, f"{dataset_dir.name}/{name}")
        manifest["archives"][data_zip.name] = {"bytes": data_zip.stat().st_size, "sha256": _sha256(data_zip)}
    (out_dir / "COLAB_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "outputs" / "colab")
    args = parser.parse_args()
    manifest = build_package(args.out, args.dataset_dir)
    for name, info in manifest["archives"].items():
        print(f"{name}: {info['bytes'] / 1e6:.2f} MB  sha256={info['sha256']}")
    print(f"manifest: {args.out / 'COLAB_MANIFEST.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
