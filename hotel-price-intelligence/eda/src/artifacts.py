"""Ghi output versioned + manifest SHA-256 (EDA_CURATED_PLAN.md muc 4, muc 5 quy tac 3).

`artifact_manifest.json` duoc ghi SAU CUNG, chua SHA-256 + kich thuoc moi file KHAC trong thu muc
analysis - tu no KHONG tu hash chinh no. Day la bang chung GPT review dung file, output khong bi sua
tay ngoai pipeline (GPT review 12 file 03 m2).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
EDA_DIR = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = EDA_DIR / "outputs"


def analysis_id(warehouse_batch_id: str, *, now: dt.datetime | None = None, suffix: str | None = None) -> str:
    """GPT review 12 (eda) M2: chi den PHUT co the trung neu chay 2 lan/phut, va ghi de analysis cu.
    Them giay + `suffix` (mac dinh: 4 hex ngau nhien) de gan nhu khong bao gio trung."""
    now = now or dt.datetime.now(dt.timezone.utc)
    suffix = uuid.uuid4().hex[:4] if suffix is None else suffix
    return f"eda_{warehouse_batch_id}_{now:%Y%m%d_%H%M%S}_{suffix}"


def analysis_dir(analysis_id_: str, *, outputs_dir: Path = OUTPUTS_DIR) -> Path:
    return outputs_dir / analysis_id_


def new_analysis_dir(analysis_id_: str, *, outputs_dir: Path = OUTPUTS_DIR) -> Path:
    """FAIL-IF-EXISTS (GPT review 12 eda M2): khong bao gio ghi de 1 analysis da co. Tao san 3 thu
    muc con chuan (`tables/`, `figures/`, `executed_notebooks/`)."""
    directory = analysis_dir(analysis_id_, outputs_dir=outputs_dir)
    directory.mkdir(parents=True, exist_ok=False)  # exist_ok=False -> FileExistsError neu da co
    for sub in ("tables", "figures", "executed_notebooks"):
        (directory / sub).mkdir()
    return directory


def mark_failed(directory: Path, *, error: str) -> None:
    """Danh dau ro 1 analysis directory la THAT BAI (khong xoa - giu de debug) - KHONG duoc de no
    trong "hao PASS" (co du artifact nhung thieu manifest, de nguoi doc lam tuong da xong)."""
    atomic_write_json(Path(directory) / "FAILED.json", {
        "failed_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "error": error,
    })


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Ghi JSON nguyen tu (tmp file cung thu muc roi os.replace) - tranh file dang do neu tien trinh
    bi ngat giua chung, cung quy uoc voi `app.warehouse.atomic.atomic_write_json` cua backend."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_artifact_manifest(directory: Path, *, manifest_name: str = "artifact_manifest.json") -> Path:
    """Duyet MOI file trong `directory` (de quy, tru chinh manifest), ghi SHA-256 + size. Goi ham nay
    SAU CUNG, sau khi moi artifact khac (bang/hinh/report) da ghi xong."""
    directory = Path(directory)
    manifest_path = directory / manifest_name
    entries = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != manifest_name:
            entries.append({
                "path": str(path.relative_to(directory)).replace("\\", "/"),
                "sha256": sha256_file(path), "size_bytes": path.stat().st_size,
            })
    atomic_write_json(manifest_path, {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "file_count": len(entries), "files": entries,
    })
    return manifest_path
