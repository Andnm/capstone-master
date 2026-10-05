"""Danh tinh MA cua mot dataset build (GPT review vong 2 R2-M1): manifest SHA-256 cua moi file ma builder dung de QUYET DINH ket qua.

Gom: moi file `ml/dataset_builder/*.py`, hai CLI (`init_dataset_build.py`, `build_dataset.py`), file DDL loi (`setup.sql`) va **dong dong import tinh** (AST, ke ca import
trong ham/tuong doi) tu cac file tren sang `backend/app/**` (canonicalize/hashing/etl_config/etl_ddl/reference/anomaly registry...). Khong dua vao `sys.modules`
(phu thuoc tien trinh); chi phu thuoc NOI DUNG file nen `init` o mot tien trinh va `apply` o tien trinh khac cho CUNG manifest. `app.core.*` bi loai co chu dich
(chi cap cau hinh ket noi/phien ban scraper; nguong reference lay tu batch da pin, khong tu settings) va danh sach loai tru duoc ghi trong manifest.

`init_dataset_build` ghi manifest vao `build_config_json` (bat bien); `runner._session` verify TRUOC moi step/cleanup/ghi va `_execute_step` verify lai ngay
truoc `mark_pass`. Code doi => phai tao dataset_version moi; khong rebuild cung version bang ma khac. `official` con doi cac file nay SACH (git) va co HEAD.
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import env

ML_DIR = env.ML_DIR
REPO_ROOT = env.REPO_ROOT
BACKEND_DIR = env.BACKEND_DIR
EXCLUDED_BACKEND_PREFIXES = ("app.core",)
EXTRA_FILES = (
    "hotel-price-intelligence/ml/scripts/init_dataset_build.py",
    "hotel-price-intelligence/ml/scripts/build_dataset.py",
    "hotel-price-intelligence/backend/app/database/setup.sql",
    "hotel-price-intelligence/backend/app/warehouse/etl_ddl.py",      # DDL cac bang ml_* ma builder ghi (khong duoc import tinh)
)


class CodeIdentityError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_file(module: str, backend_dir: Path) -> Path | None:
    parts = module.split(".")
    base = backend_dir.joinpath(*parts)
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _excluded(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in EXCLUDED_BACKEND_PREFIXES)


def _imports_of(path: Path, *, module_name: str | None, is_package: bool) -> set[str]:
    """Ten module `app.*` duoc import (tuyet doi hoac tuong doi, o bat ky cap nao) trong file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and module_name:
                package = module_name.split(".") if is_package else module_name.split(".")[:-1]
                package = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                base = ".".join(package + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if base:
                found.add(base)
                found.update(f"{base}.{alias.name}" for alias in node.names)   # `from app.x import y` co the la module con
    return {name for name in found if name == "app" or name.startswith("app.")}


def backend_closure(seed_files: list[Path], *, backend_dir: Path = BACKEND_DIR) -> dict[str, Path]:
    """Dong dong import tinh `app.*` tu cac file hat giong (khong theo sys.modules)."""
    closure: dict[str, Path] = {}
    queue: list[tuple[Path, str | None, bool]] = [(p, None, False) for p in seed_files]
    while queue:
        path, module_name, is_package = queue.pop()
        for name in _imports_of(path, module_name=module_name, is_package=is_package):
            if _excluded(name) or name in closure:
                continue
            file = _module_file(name, backend_dir)
            if file is None:
                continue
            closure[name] = file
            queue.append((file, name, file.name == "__init__.py"))
            # import mot module con thi goi `__init__` cua cac goi cha cung duoc thuc thi => cung la ma anh huong
            parent = name.rsplit(".", 1)[0] if "." in name else None
            while parent and parent not in closure and not _excluded(parent):
                pfile = _module_file(parent, backend_dir)
                if pfile is not None:
                    closure[parent] = pfile
                    queue.append((pfile, parent, pfile.name == "__init__.py"))
                parent = parent.rsplit(".", 1)[0] if "." in parent else None
    return closure


def builder_code_manifest(*, ml_dir: Path = ML_DIR, repo_root: Path = REPO_ROOT, backend_dir: Path = BACKEND_DIR,
                          extra_files: tuple[str, ...] = EXTRA_FILES) -> dict[str, Any]:
    builder_files = sorted((ml_dir / "dataset_builder").glob("*.py"))
    seeds = builder_files + [repo_root / rel for rel in extra_files if rel.endswith(".py")]   # CLI + etl_ddl cung duoc lan theo import
    files: dict[str, str] = {}
    for path in [*builder_files, *(repo_root / rel for rel in extra_files)]:
        if not path.is_file():
            raise CodeIdentityError(f"thieu file thuoc danh tinh ma: {path}")
        files[path.relative_to(repo_root).as_posix()] = _sha256(path)
    for name, path in sorted(backend_closure(seeds, backend_dir=backend_dir).items()):
        files[path.relative_to(repo_root).as_posix()] = _sha256(path)
    canonical = json.dumps({"files": files, "excluded_backend_prefixes": list(EXCLUDED_BACKEND_PREFIXES)}, sort_keys=True, separators=(",", ":"))
    return {"files": dict(sorted(files.items())), "excluded_backend_prefixes": list(EXCLUDED_BACKEND_PREFIXES),
            "code_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def diff_manifests(pinned: dict[str, str], current: dict[str, str]) -> dict[str, list[str]]:
    return {"changed": sorted(k for k in pinned.keys() & current.keys() if pinned[k] != current[k]),
            "added": sorted(current.keys() - pinned.keys()), "removed": sorted(pinned.keys() - current.keys())}


def verify_code_identity(config: dict[str, Any], *, current: Callable[[], dict[str, Any]] = builder_code_manifest) -> dict[str, Any]:
    """So manifest da ghim trong config voi manifest hien tai. Lech => CodeIdentityError (liet ke file doi). Config khong co `builder_code` => loi."""
    pinned = config.get("builder_code")
    if not pinned or not pinned.get("code_sha256"):
        raise CodeIdentityError("config khong ghim `builder_code` - dataset tao bang ban builder cu, khong xac minh duoc danh tinh ma.")
    now = current()
    if now["code_sha256"] != pinned["code_sha256"]:
        diff = diff_manifests(pinned["files"], now["files"])
        raise CodeIdentityError(
            f"ma builder/dependency DA DOI so voi luc init (pinned {pinned['code_sha256'][:12]}… != hien tai {now['code_sha256'][:12]}…): "
            f"changed={diff['changed'][:6]} added={diff['added'][:3]} removed={diff['removed'][:3]}. Tao dataset_version MOI, khong rebuild cung version bang ma khac.")
    return now


def dirty_paths(files: list[str], *, repo_root: Path = REPO_ROOT) -> list[str]:
    """Trong `files` (tuong doi repo_root), nhung file co thay doi/chua theo doi theo git; loi git => nem CodeIdentityError."""
    try:
        out = subprocess.run(["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=all", "--", *files], capture_output=True,
                             text=True, timeout=60, check=True).stdout
    except Exception as exc:  # noqa: BLE001
        raise CodeIdentityError(f"khong chay duoc git status: {type(exc).__name__}: {exc}") from exc
    return sorted({line[3:].strip().replace("\\", "/") for line in out.splitlines() if line.strip()})


def assert_official_clean(config: dict[str, Any], *, git_dirty: Callable[[list[str]], list[str]] = dirty_paths,
                          head: Callable[[], str | None] | None = None) -> None:
    """`official`: moi file thuoc danh tinh ma phai sach (git) va co HEAD. rehearsal/dev: khong chan (hash van duoc ghim)."""
    if config.get("purpose") != "official":
        return
    pinned = config.get("builder_code") or {}
    get_head = head or _git_head
    if not get_head():
        raise CodeIdentityError("official: khong xac dinh duoc git HEAD - khong the tai lap dataset.")
    dirty = git_dirty(list(pinned.get("files", {})))
    if dirty:
        raise CodeIdentityError(f"official: {len(dirty)} file thuoc danh tinh ma chua commit: {dirty[:6]}")


def _git_head() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30, check=True).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None
