"""Gioi report cua dataset (GPT review vong 2 R2-m3): artifact PASS tu chua du report cua CA 6 step + checksum, khong phu thuoc `_reports/` ngoai dataset.

`features_labels` chi copy duoc report cua 4 step truoc no (report cua chinh no va cua `validation` chi ton tai SAU khi step ket thuc). Vi vay gioi
duoc publish o cuoi step `validation` (runner, ngay truoc `mark_pass`): copy 6 report vao `reports/`, ghi `reports/REPORTS_MANIFEST.json` (sha tung
report + dataset_version + build_config_sha256 + builder_code_sha256), roi them cac muc `reports/<ten>` vao `output_parquet_sha256_json` de
`verify_pass_outputs`/`hash_file_*` kiem chung cung co che voi Parquet.

`cleanup_from('validation')` chi go phan do validation tao ra (validation.json + REPORTS_MANIFEST.json + cac muc checksum `reports/*` cua gioi),
KHONG xoa Parquet/hash do `features_labels` tao (truoc day xoa ca thu muc nen retry validation khong the chay).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .manifest import STEPS

REPORTS_DIR = "reports"
MANIFEST_NAME = "REPORTS_MANIFEST.json"
PREFIX = REPORTS_DIR + "/"
VALIDATION_OWNED = (f"{STEPS[-1]}.json", MANIFEST_NAME)          # tep chi do step validation tao


class BundleError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str).encode("utf-8")


def build_bundle(*, out_dir: Path, report_dir: Path, dataset_version: str, build_config_sha256: str,
                 builder_code_sha256: str) -> dict[str, dict[str, str]]:
    """Publish gioi vao `<out_dir>/reports` (atomic theo thu muc); tra ve cac muc checksum `reports/<ten>` can ghi vao manifest.
    Thieu bat ky report nao trong 6 step => BundleError (khong publish gioi thieu)."""
    missing = [step for step in STEPS if not (report_dir / f"{step}.json").is_file()]
    if missing:
        raise BundleError(f"thieu report cua step {missing} trong {report_dir} - khong publish gioi report.")
    if not out_dir.is_dir():
        raise BundleError(f"khong co thu muc output {out_dir}.")
    tmp = out_dir / f"{REPORTS_DIR}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    report_hashes: dict[str, str] = {}
    for step in STEPS:
        name = f"{step}.json"
        data = (report_dir / name).read_bytes()
        (tmp / name).write_bytes(data)
        report_hashes[name] = hashlib.sha256(data).hexdigest()
    manifest = {"dataset_version": dataset_version, "build_config_sha256": build_config_sha256, "builder_code_sha256": builder_code_sha256,
                "steps": list(STEPS), "reports": {name: {"file_sha256": sha} for name, sha in sorted(report_hashes.items())}}
    (tmp / MANIFEST_NAME).write_bytes(_json_bytes(manifest))
    final = out_dir / REPORTS_DIR
    if final.exists():
        shutil.rmtree(final)
    os.replace(tmp, final)
    entries = {f"{PREFIX}{name}": {"file_sha256": sha} for name, sha in report_hashes.items()}
    entries[f"{PREFIX}{MANIFEST_NAME}"] = {"file_sha256": _sha256(final / MANIFEST_NAME)}
    return entries


def merge_checksums(stored: dict[str, Any] | None, entries: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Bo cac muc `reports/*` cu (neu co) roi them `entries`; cac muc khac (Parquet, dictionary...) giu nguyen."""
    merged = {name: value for name, value in (stored or {}).items() if not name.startswith(PREFIX)}
    merged.update(entries)
    return merged


def strip_checksums(stored: dict[str, Any] | None) -> dict[str, Any]:
    return {name: value for name, value in (stored or {}).items() if not name.startswith(PREFIX)}


def verify_bundle(*, out_dir: Path, stored: dict[str, Any] | None, dataset_version: str, build_config_sha256: str,
                  builder_code_sha256: str) -> list[str]:
    """Danh sach sai lech cua gioi report (rong = khop): du 6 report + manifest, hash khop manifest va khop checksum DB, metadata dung."""
    problems: list[str] = []
    stored = stored or {}
    manifest_path = out_dir / REPORTS_DIR / MANIFEST_NAME
    if not manifest_path.is_file():
        return [f"thieu {REPORTS_DIR}/{MANIFEST_NAME}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"{MANIFEST_NAME} khong doc duoc JSON: {exc}"]
    for key, expected in (("dataset_version", dataset_version), ("build_config_sha256", build_config_sha256),
                          ("builder_code_sha256", builder_code_sha256)):
        if manifest.get(key) != expected:
            problems.append(f"{MANIFEST_NAME}.{key}={manifest.get(key)!r} != {expected!r}")
    listed = manifest.get("reports", {})
    for step in STEPS:
        name = f"{step}.json"
        path = out_dir / REPORTS_DIR / name
        if name not in listed:
            problems.append(f"{MANIFEST_NAME} khong liet ke {name}")
        elif not path.is_file():
            problems.append(f"thieu {REPORTS_DIR}/{name}")
        elif _sha256(path) != listed[name].get("file_sha256"):
            problems.append(f"{REPORTS_DIR}/{name} lech hash trong {MANIFEST_NAME}")
        entry = stored.get(f"{PREFIX}{name}")
        if entry is None or entry.get("file_sha256") != listed.get(name, {}).get("file_sha256"):
            problems.append(f"checksum DB cua {PREFIX}{name} thieu hoac lech {MANIFEST_NAME}")
    entry = stored.get(f"{PREFIX}{MANIFEST_NAME}")
    if entry is None or entry.get("file_sha256") != _sha256(manifest_path):
        problems.append(f"checksum DB cua {PREFIX}{MANIFEST_NAME} thieu hoac lech file")
    return problems


def remove_validation_artifacts(out_dir: Path) -> None:
    """Go phan do step validation tao (idempotent); khong dong vao Parquet/dictionary/sufficiency/report cac step khac."""
    for name in VALIDATION_OWNED:
        (out_dir / REPORTS_DIR / name).unlink(missing_ok=True)
