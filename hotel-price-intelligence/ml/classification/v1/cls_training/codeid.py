"""Danh tinh ma cua RIENG thu muc `v1/` (khong lay Git root cua cay regression): hash LF cua tung file + code_sha256 tong. Dung cho report/CODE_MANIFEST."""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path
from typing import Any

V1_DIR = Path(__file__).resolve().parents[1]
INCLUDE_SUFFIXES = (".py", ".yaml", ".yml", ".json", ".md", ".txt", ".ipynb")
EXCLUDE_PARTS = ("__pycache__", ".pytest_cache")


def _lf_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    if path.suffix in (".py", ".yaml", ".yml", ".json", ".md", ".txt"):
        raw = raw.replace(b"\r\n", b"\n")
    return raw


def code_manifest(v1_dir: Path | None = None, *, exclude_tests: bool = False, suffixes: tuple[str, ...] | None = None) -> dict[str, Any]:
    root = Path(v1_dir) if v1_dir else V1_DIR
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (suffixes or INCLUDE_SUFFIXES) or any(p in path.parts for p in EXCLUDE_PARTS):
            continue
        rel = path.relative_to(root).as_posix()
        if exclude_tests and rel.startswith("tests/"):
            continue
        files[rel] = hashlib.sha256(_lf_bytes(path)).hexdigest()
    code_sha = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"root_name": root.name, "n_files": len(files), "code_sha256": code_sha, "files": files}


def environment_summary() -> dict[str, Any]:
    out: dict[str, Any] = {"python": sys.version.split()[0], "executable": sys.executable, "platform": platform.platform()}
    for mod in ("numpy", "pandas", "pyarrow", "sklearn", "scipy", "yaml", "joblib", "xgboost"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception:  # noqa: BLE001 - thieu goi => ghi 'absent'
            out[mod] = "absent"
    return out
