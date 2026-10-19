"""Giao dich ghi ket qua mot horizon (EVAL_PROTOCOL §8): staging -> kiem hash -> doi ten; resume CHI khi identity trung, moi hash dung VA (neu co) vi tu ngu nghia thanh cong thoa.

- Don vi = mot horizon. Moi file duoc ghi vao thu muc staging `.staging_<run_id>` tao bang `mkdir` DOC QUYEN (khong tu xoa staging co san: co the cua tien trinh khac hoac lan chay bo dang -> loi ro rang);
  `commit()` tinh SHA-256 tung file, ghi `run_manifest.json` (liet ke file + hash, KHONG chua hash cua chinh no), roi `COMPLETE` (chua SHA-256 cua run_manifest.json) va `os.replace` staging -> `run_<run_id>`.
  Khong co `COMPLETE` hoac hash lech => run KHONG hop le.
- An toan duong dan: `run_id` khop `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$` va khong chua `..`; staging/final phai la con TRUC TIEP cua root da resolve; root/staging/final khong duoc la symlink/junction/reparse;
  ten file (ghi va liet ke trong manifest) la basename an toan (khong separator, khong tuyet doi, khong `..`).
- `find_complete_run(root, identity, predicate)`: chi tra run co COMPLETE hop le, moi file khop hash, identity bang chinh xac VA `predicate(manifest, run_dir)` dung (vd 'huan luyen thanh cong', khong chi 'ghi xong').
  Run diagnostic (incomplete / no_valid_model) van ton tai de audit nhung KHONG la cache thanh cong. Khong tu ghi de/xoa run cu: chay lai dung run_id MOI.
- Copy len Drive mount KHONG nguyen tu: xem `drive.py`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

COMPLETE = "COMPLETE"
MANIFEST = "run_manifest.json"
EXCLUDED_FROM_LISTING = (MANIFEST, COMPLETE)
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+=,-]{0,200}$")


class TransactionError(RuntimeError):
    pass


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id) or ".." in run_id:
        raise TransactionError(f"run_id khong an toan: {run_id!r} (chi [A-Za-z0-9_.-], bat dau bang chu/so, <=64 ky tu, khong '..')")
    return run_id


def safe_file_name(name: str) -> str:
    if not isinstance(name, str) or not FILE_NAME_RE.match(name) or ".." in name or name in (".", "..") or Path(name).name != name:
        raise TransactionError(f"ten file khong an toan: {name!r}")
    return name


def is_reparse(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return path.is_symlink() or bool(getattr(st, "st_file_attributes", 0) & 0x400)          # FILE_ATTRIBUTE_REPARSE_POINT (junction/symlink Windows)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_default(o: Any) -> Any:
    import numpy as np
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"khong serialize duoc {type(o).__name__}")


def canonical_bytes(obj: Any) -> bytes:
    """JSON xac dinh (khoa sap xep); kieu numpy -> kieu Python, kieu la => TypeError (khong ep ve chuoi), NaN/inf bi tu choi (allow_nan=False)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, default=_json_default, allow_nan=False).encode("utf-8") + b"\n"


def checked_root(root: Path | str) -> Path:
    root = Path(root)
    if is_reparse(root):
        raise TransactionError(f"root {root} la symlink/junction/reparse point")
    root.mkdir(parents=True, exist_ok=True)
    if is_reparse(root):
        raise TransactionError(f"root {root} la symlink/junction/reparse point")
    return root.resolve()


def child_path(root: Path, name: str) -> Path:
    """`root/name` phai la con TRUC TIEP cua root da resolve va khong duoc la reparse point."""
    p = root / name
    if p.parent.resolve() != root.resolve() or p.name != name:
        raise TransactionError(f"{p} khong nam truc tiep duoi {root}")
    if is_reparse(p):
        raise TransactionError(f"{p} la symlink/junction/reparse point")
    return p


class RunTransaction:
    def __init__(self, root: Path | str, run_id: str):
        self.run_id = validate_run_id(run_id)
        self.root = checked_root(root)
        self.final = child_path(self.root, f"run_{self.run_id}")
        self.staging = child_path(self.root, f".staging_{self.run_id}")
        if self.final.exists():
            raise TransactionError(f"{self.final} da ton tai (khong ghi de ket qua; dung run_id moi de chay lai)")
        try:
            self.staging.mkdir()                                  # DOC QUYEN: neu da co (tien trinh khac / lan chay bo dang) => FileExistsError, khong tu xoa
        except FileExistsError as exc:
            raise TransactionError(f"{self.staging} da ton tai (co the cua tien trinh khac hoac lan chay bo dang); kiem tra va xu ly thu cong hoac dung run_id moi") from exc
        self.committed = False

    def path(self, name: str) -> Path:
        if name in EXCLUDED_FROM_LISTING:
            raise TransactionError(f"ten file khong hop le: {name!r}")
        return self.staging / safe_file_name(name)

    def write_json(self, name: str, obj: Any) -> Path:
        p = self.path(name)
        p.write_bytes(canonical_bytes(obj))
        return p

    def write_text(self, name: str, text: str) -> Path:
        p = self.path(name)
        p.write_bytes(text.encode("utf-8"))
        return p

    def commit(self, identity: dict[str, Any], extra: dict[str, Any] | None = None) -> Path:
        if self.committed:
            raise TransactionError("da commit")
        files = {p.name: {"sha256": sha256_file(p), "bytes": p.stat().st_size} for p in sorted(self.staging.iterdir()) if p.is_file() and p.name not in EXCLUDED_FROM_LISTING}
        if not files:
            raise TransactionError("khong co file nao de commit")
        for name in files:
            safe_file_name(name)
        manifest = {"run_id": self.run_id, "identity": identity, "files": files, **(extra or {})}
        (self.staging / MANIFEST).write_bytes(canonical_bytes(manifest))
        digest = sha256_file(self.staging / MANIFEST)
        (self.staging / COMPLETE).write_bytes(canonical_bytes({"run_manifest_sha256": digest, "n_files": len(files)}))
        os.replace(self.staging, self.final)
        self.committed = True
        return self.final

    def abort(self) -> None:
        """Chi xoa staging DO CHINH giao dich nay tao (da mkdir doc quyen thanh cong)."""
        if not self.committed and self.staging.exists() and not is_reparse(self.staging):
            shutil.rmtree(self.staging, ignore_errors=True)


def verify_run_dir(run_dir: Path | str) -> dict[str, Any]:
    """Kiem 1 thu muc run (sau khi chep sang Drive cung dung ham nay). Tra manifest neu hop le; loi => TransactionError."""
    run_dir = Path(run_dir)
    if is_reparse(run_dir):
        raise TransactionError(f"{run_dir.name}: la symlink/junction/reparse point")
    complete_path, manifest_path = run_dir / COMPLETE, run_dir / MANIFEST
    if not complete_path.is_file() or not manifest_path.is_file():
        raise TransactionError(f"{run_dir.name}: thieu {COMPLETE} hoac {MANIFEST} (run chua hoan tat)")
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    if complete.get("run_manifest_sha256") != sha256_file(manifest_path):
        raise TransactionError(f"{run_dir.name}: run_manifest.json khac hash trong {COMPLETE}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = []
    listed = manifest.get("files") or {}
    if complete.get("n_files") != len(listed):
        problems.append("so file trong COMPLETE khac manifest")
    for name, entry in listed.items():
        try:
            safe_file_name(name)
        except TransactionError as exc:
            problems.append(str(exc))
            continue
        p = run_dir / name
        if is_reparse(p):
            problems.append(f"{name}: la reparse point")
        elif not p.is_file():
            problems.append(f"{name}: thieu")
        elif sha256_file(p) != entry["sha256"] or p.stat().st_size != entry["bytes"]:
            problems.append(f"{name}: hash/kich thuoc khac manifest")
    extra = sorted(p.name for p in run_dir.iterdir() if p.is_file() and p.name not in listed and p.name not in EXCLUDED_FROM_LISTING)
    if extra:
        problems.append(f"file ngoai manifest: {extra}")
    if problems:
        raise TransactionError(f"{run_dir.name}: " + "; ".join(problems))
    return manifest


def find_complete_run(root: Path | str, identity: dict[str, Any], predicate: Callable[[dict[str, Any], Path], bool] | None = None) -> Path | None:
    """Run hop le (byte) cung identity VA `predicate(manifest, run_dir)` dung (neu co). Khong hop le/khac identity/khong thoa predicate => bo qua (khong xoa)."""
    root = Path(root)
    if not root.is_dir() or is_reparse(root):
        return None
    for run_dir in sorted(root.glob("run_*")):
        try:
            manifest = verify_run_dir(run_dir)
        except (TransactionError, ValueError, OSError):
            continue
        if manifest.get("identity") != identity:
            continue
        if predicate is not None and not predicate(manifest, run_dir):
            continue
        return run_dir
    return None
