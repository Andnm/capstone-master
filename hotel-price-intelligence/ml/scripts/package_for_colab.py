"""Dong goi toi thieu de huan luyen tren Google Colab (khong can MySQL/backend): code huan luyen + cau hinh (+ tuy chon dataset Parquet).

    python ml/scripts/package_for_colab.py [--dataset-dir outputs/datasets/<dataset_version>] [--out outputs/colab]

Tao `ml_train_pkg_<timestamp>.zip` (thu muc goc `ml/`: training/, scripts/train_models.py, configs/, requirements-train.txt va chi 3 file cua
`dataset_builder` ma `training` can: __init__, feature_spec, dictionary) va, neu co `--dataset-dir`, `dataset_<version>.zip`. Ghi `COLAB_MANIFEST.json`
(kich thuoc + SHA-256 tung zip) de doi chieu sau khi tai len Drive. Dataset chi gom 5 file dau ra can cho huan luyen, khong kem `reports/` hay `tmp/`.
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
DATASET_FILES = ("samples.parquet", "data_dictionary.csv", "sufficiency_report.json", "output_checksums.json", "coverage_report.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    manifest: dict = {"created_at": stamp, "archives": {}}
    code_zip = out_dir / f"ml_train_pkg_{stamp}.zip"
    with zipfile.ZipFile(code_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in package_files():
            if not path.exists():
                raise FileNotFoundError(f"thieu file dong goi: {path}")
            archive.write(path, arcname)
    manifest["archives"][code_zip.name] = {"bytes": code_zip.stat().st_size, "sha256": _sha256(code_zip)}
    if dataset_dir is not None:
        dataset_dir = Path(dataset_dir)
        data_zip = out_dir / f"dataset_{dataset_dir.name}.zip"
        missing = [name for name in DATASET_FILES[:2] if not (dataset_dir / name).exists()]
        if missing:
            raise FileNotFoundError(f"dataset thieu {missing} o {dataset_dir}")
        with zipfile.ZipFile(data_zip, "w", zipfile.ZIP_STORED) as archive:  # Parquet da nen san
            for name in DATASET_FILES:
                if (dataset_dir / name).exists():
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
