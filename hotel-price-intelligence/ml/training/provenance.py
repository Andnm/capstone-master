"""Kiem dau vao + provenance cua mot lan huan luyen (GPT review vong 1 TR-M2/TR-M4/TR-m2). Fail-closed.

- `verify_dataset`: hash lai cac file dataset da cong bo trong `output_checksums.json` (khong tin gia tri khai bao), yeu cau metadata bat buoc
  (file_sha256 + content_sha256 + rows), kiem so dong va `dataset_version` duy nhat == ten thu muc. KHONG tinh lai `content_sha256` o day: hash
  noi dung phu thuoc phien ban pandas (Colab pandas 2.x khac may chinh pandas 3.x) nen se bao sai; do chinh xac theo byte da co file_sha256, con
  content_sha256 duoc builder xac minh trong moi truong cua no va chi duoc GHI vao bao cao de truy vet.
- `code_provenance`: tren Colab khong co Git => doc `CODE_MANIFEST.json` (do `package_for_colab.py` tao, hash tung file + `code_sha256`) va xac minh
  tung file dang chay khop manifest; tren may chinh ghi git HEAD + trang thai dirty cua `ml/`; khong xac dinh duoc => `source='unknown'`.
- `environment_manifest`: bang phien ban thuc te cua MOI goi da cai (`name==version`) + sha256 - thay cho viec ghim cung requirements.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from importlib import metadata
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pandas as pd

ML_DIR = Path(__file__).resolve().parents[1]
CODE_MANIFEST_NAME = "CODE_MANIFEST.json"
DATASET_FILES = ("samples.parquet", "data_dictionary.csv", "coverage_report.json", "sufficiency_report.json")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class DatasetVerificationError(RuntimeError):
    pass


class ProvenanceError(RuntimeError):
    pass


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_dataset(dataset_dir: Path | str) -> dict[str, Any]:
    """Fail-closed truoc khi doc Parquet. Tra metadata da XAC MINH (hash that, khong phai hash khai bao)."""
    dataset_dir = Path(dataset_dir)
    checks_path = dataset_dir / "output_checksums.json"
    if not checks_path.exists():
        raise DatasetVerificationError(f"thieu {checks_path} - khong xac minh duoc dataset.")
    declared = json.loads(checks_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    verified: dict[str, str] = {}
    for name in DATASET_FILES:
        entry = declared.get(name)
        path = dataset_dir / name
        if not isinstance(entry, dict) or not _SHA.match(str(entry.get("file_sha256", ""))):
            problems.append(f"{name}: thieu file_sha256 hop le trong output_checksums.json")
            continue
        if not path.exists():
            problems.append(f"{name}: file khong ton tai")
            continue
        actual = file_sha256(path)
        verified[name] = actual
        if actual != entry["file_sha256"]:
            problems.append(f"{name}: file_sha256 that {actual[:16]}… khac khai bao {entry['file_sha256'][:16]}…")
    samples = declared.get("samples.parquet") or {}
    if not _SHA.match(str(samples.get("content_sha256", ""))):
        problems.append("samples.parquet: thieu content_sha256 hop le")
    if not isinstance(samples.get("rows"), int) or samples.get("rows", 0) <= 0:
        problems.append("samples.parquet: thieu rows")
    if problems:
        raise DatasetVerificationError("dataset KHONG qua xac minh: " + "; ".join(problems))
    return {"dataset_dir": str(dataset_dir), "dataset_name": dataset_dir.name, "samples_file_sha256": verified["samples.parquet"],
            "samples_content_sha256": samples["content_sha256"], "declared_rows": int(samples["rows"]),
            "verified_file_sha256": verified}


def verify_frame(frame: pd.DataFrame, meta: dict[str, Any]) -> None:
    problems: list[str] = []
    if len(frame) != meta["declared_rows"]:
        problems.append(f"so dong {len(frame)} != khai bao {meta['declared_rows']}")
    versions = set(frame["dataset_version"].dropna().astype(str)) if "dataset_version" in frame.columns else set()
    if versions != {meta["dataset_name"]}:
        problems.append(f"dataset_version trong du lieu {sorted(versions)} khac ten thu muc {meta['dataset_name']!r}")
    if problems:
        raise DatasetVerificationError("dataset KHONG qua xac minh: " + "; ".join(problems))


def _git_state() -> dict[str, Any]:
    try:
        repo = ML_DIR.parent
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all", "--", "ml"], capture_output=True,
                               text=True, timeout=20, check=True).stdout.strip()
        return {"source": "git", "head": head, "ml_dirty": bool(dirty), "ml_dirty_files": len(dirty.splitlines()) if dirty else 0}
    except Exception as exc:  # noqa: BLE001
        return {"source": "unknown", "error": f"{type(exc).__name__}: {exc}"}


def aggregate_sha256(files: dict[str, str]) -> str:
    """Cung cong thuc voi `package_for_colab.build_code_manifest` (R2-M2): sha256(JSON chuan hoa cua {duong_dan: sha})."""
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_code_manifest(manifest: Any, *, root: Path) -> dict[str, str]:
    """Kiem hinh dang + tinh toan ven cua CODE_MANIFEST (khong tin `code_sha256` tu khai bao): schema, hash 64-hex, duong dan tuong doi nam duoi `root`
    va `code_sha256` == aggregate tinh lai tu `files`. Sai => ProvenanceError. Tra `files` da hop le."""
    if not isinstance(manifest, dict):
        raise ProvenanceError(f"{CODE_MANIFEST_NAME} khong phai object JSON")
    files, declared = manifest.get("files"), manifest.get("code_sha256")
    if not isinstance(files, dict) or not files:
        raise ProvenanceError(f"{CODE_MANIFEST_NAME}: thieu/rong `files`")
    if not isinstance(declared, str) or not _SHA.match(declared):
        raise ProvenanceError(f"{CODE_MANIFEST_NAME}: `code_sha256` khong phai 64 ky tu hex")
    resolved_root = Path(root).resolve()
    for name, sha in files.items():
        if not isinstance(name, str) or not isinstance(sha, str) or not _SHA.match(sha):
            raise ProvenanceError(f"{CODE_MANIFEST_NAME}: muc {name!r} khong hop le (hash phai 64 hex)")
        path = PurePosixPath(name)
        if name.startswith("/") or PureWindowsPath(name).drive or ".." in path.parts or "\\" in name:
            raise ProvenanceError(f"{CODE_MANIFEST_NAME}: duong dan {name!r} phai tuong doi, dung '/', khong co '..'")
        if resolved_root not in (resolved_root / path).resolve().parents:
            raise ProvenanceError(f"{CODE_MANIFEST_NAME}: {name!r} nam ngoai goi {resolved_root}")
    recomputed = aggregate_sha256(files)
    if recomputed != declared:
        raise ProvenanceError(f"{CODE_MANIFEST_NAME}: code_sha256 khai bao {declared[:16]}… khac aggregate tinh lai {recomputed[:16]}…")
    return files


def code_provenance(ml_dir: Path | str = ML_DIR) -> dict[str, Any]:
    """Co CODE_MANIFEST.json => xac minh schema + aggregate + tung file (sai => ProvenanceError); khong co => git; khong co git => unknown."""
    ml_dir = Path(ml_dir)
    manifest_path = ml_dir / CODE_MANIFEST_NAME
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ProvenanceError(f"{CODE_MANIFEST_NAME} khong doc duoc JSON: {exc}") from exc
        root = ml_dir.parent
        files = validate_code_manifest(manifest, root=root)
        bad = [name for name, expected in files.items() if not (root / name).is_file() or file_sha256(root / name) != expected]
        if bad:
            raise ProvenanceError(f"code dang chay KHONG khop {CODE_MANIFEST_NAME}: {bad[:5]}")
        return {"source": "code_manifest", "code_sha256": manifest["code_sha256"], "manifest_sha256": file_sha256(manifest_path),
                "manifest_path": str(manifest_path), "verified_files": len(files), "created_at": manifest.get("created_at"),
                "packaged_from": manifest.get("git")}
    state = _git_state()
    return state


def require_known_provenance(provenance: dict[str, Any], *, official: bool) -> None:
    if not official:
        return
    source = provenance.get("source")
    if source == "code_manifest":
        return
    if source == "git" and provenance.get("head") and not provenance.get("ml_dirty"):
        return
    raise ProvenanceError(f"official: provenance code khong du (source={source}, dirty={provenance.get('ml_dirty')}, "
                          f"loi={provenance.get('error')}) - dung goi Colab co CODE_MANIFEST.json hoac commit ml/ roi chay.")


def require_lineage(provenance: dict[str, Any], colab_manifest: dict[str, Any] | None, *, official: bool) -> None:
    """R2-M2: run `official` tren goi Colab (provenance = code_manifest) phai giu COLAB_MANIFEST (hash archive code + dataset) trong lineage cua output;
    khong co => khong the chung minh ma/du lieu den tu goi nao. Git (may chinh) khong can."""
    if official and provenance.get("source") == "code_manifest" and not colab_manifest:
        raise ProvenanceError("official tren goi Colab: thieu --colab-manifest (COLAB_MANIFEST.json) - khong co hash archive trong lineage cua run.")


def environment_manifest(out_path: Path | None = None) -> dict[str, Any]:
    lines = sorted({f"{dist.metadata['Name']}=={dist.version}" for dist in metadata.distributions() if dist.metadata["Name"]}, key=str.lower)
    text = "\n".join(lines) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if out_path is not None:
        Path(out_path).write_bytes(text.encode("utf-8"))   # ghi BYTE (khong qua text mode) de hash file == hash da bao cao tren moi he dieu hanh
    return {"environment_sha256": digest, "packages": len(lines)}
