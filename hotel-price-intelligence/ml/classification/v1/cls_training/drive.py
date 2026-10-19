"""Chep mot run da hoan tat sang Drive (hoac thu muc bat ky) THEO CACH KHONG GIA NGUYEN TU (EVAL_PROTOCOL §8).

Drive mount khong bao dam rename nguyen tu / flush ngay, nen: (1) chep MOI file TRU `COMPLETE` vao `.copying_<run>` (tao bang mkdir DOC QUYEN; neu da co thi LOI - khong tu xoa);
(2) tinh lai SHA-256 + kich thuoc tung file tren ban chep va doi chieu `run_manifest.json`; (3) CHI SAU DO ghi `COMPLETE` va doi ten thanh `run_<id>`; (4) `verify_run_dir` lan cuoi tren dich.
Thu muc dich khong co `COMPLETE` hop le => KHONG phai ket qua. Khong ghi de: dich da ton tai voi manifest khac => loi; da ton tai y het => tra ve (idempotent).
Duong dan an toan: ten run/file hop le, dich la con truc tiep cua root da resolve, khong reparse; ten file lay tu manifest da qua `verify_run_dir`.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .transaction import COMPLETE, MANIFEST, TransactionError, checked_root, child_path, safe_file_name, sha256_file, validate_run_id, verify_run_dir


def copy_run_verified(run_dir: Path | str, dest_root: Path | str) -> Path:
    run_dir = Path(run_dir)
    manifest = verify_run_dir(run_dir)                                      # nguon phai hop le truoc khi chep
    name = run_dir.name
    if not name.startswith("run_"):
        raise TransactionError(f"ten thu muc run khong hop le: {name!r}")
    validate_run_id(name[len("run_"):])
    dest_root = checked_root(dest_root)
    final = child_path(dest_root, name)
    if final.exists():
        try:
            existing = verify_run_dir(final)
        except TransactionError as exc:
            raise TransactionError(f"dich {final} ton tai nhung khong hop le ({exc}); khong ghi de") from exc
        if existing != manifest:
            raise TransactionError(f"dich {final} ton tai voi manifest KHAC; khong ghi de")
        return final
    tmp = child_path(dest_root, f".copying_{name}")
    try:
        tmp.mkdir()                                                          # doc quyen: da co => khong phai cua ta => khong xoa
    except FileExistsError as exc:
        raise TransactionError(f"{tmp} da ton tai (ban chep do dang hoac tien trinh khac); kiem tra va xu ly thu cong") from exc
    try:
        for fname in [*manifest["files"], MANIFEST]:
            safe_file_name(fname)
            shutil.copy2(run_dir / fname, tmp / fname)
        for fname, entry in manifest["files"].items():                       # kiem LAI tren ban chep truoc khi danh dau hoan tat
            copied = tmp / fname
            if not copied.is_file() or copied.stat().st_size != entry["bytes"] or sha256_file(copied) != entry["sha256"]:
                raise TransactionError(f"ban chep {fname} khac manifest (hash/kich thuoc)")
        if sha256_file(tmp / MANIFEST) != sha256_file(run_dir / MANIFEST):
            raise TransactionError("run_manifest.json ban chep khac nguon")
        shutil.copy2(run_dir / COMPLETE, tmp / COMPLETE)
        os.replace(tmp, final)
        verify_run_dir(final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)                               # chi thu muc do CHINH ta vua tao
        raise
    return final
