"""Resolve + xac minh 3 manifest (ownership / cohort history / source) cua warehouse hien hanh tu POINTER, fail-closed (GPT eda file 14 M1).

Truoc day `wave_a.py` hard-code ten file `*_20260916.json`: chay thieu `--ownership-manifest/...` tren warehouse moi se dung bo cu va chi lo ra o
cuoi (source manifest) hoac khong lo (ownership/cohort). Gio moi duong dan hoac (a) duoc truyen tuong minh va BI KIEM identity voi pointer,
hoac (b) duoc tim trong `data/warehouse` bang cach tinh identity cua tung file ung vien va chon DUNG MOT file khop gia tri pointer.
Khong khop/khong tim thay/nhieu hon mot ung vien => `ManifestResolutionError` (khong bao gio doan theo ten/ngay).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

KINDS: dict[str, tuple[str, str]] = {   # kind -> (glob, khoa trong pointer chua identity)
    "ownership": ("ownership_manifest_*.json", "ownership_manifest_sha256"),
    "cohort": ("cohort_history_*.json", "cohort_manifest_sha256"),
    "source": ("source_manifest_*.json", "source_manifest_sha256"),
}


class ManifestResolutionError(RuntimeError):
    pass


def default_identity_fns(repo_root: Path) -> dict[str, Callable[[Path], str]]:
    """Identity chinh thuc = `.manifest_sha256` cua loader backend (cung gia tri warehouse da pin)."""
    from app.warehouse.cohort_manifest import load_cohort_history
    from app.warehouse.ownership_manifest import load_ownership_manifest
    from app.warehouse.source_manifest import load_source_manifest

    return {
        "ownership": lambda p: load_ownership_manifest(p).manifest_sha256,
        "cohort": lambda p: load_cohort_history(p, base_dir=repo_root).manifest_sha256,
        "source": lambda p: load_source_manifest(p).manifest_sha256,
    }


def verify_identity(kind: str, path: Path, pointer: Mapping[str, Any], identity_fns: Mapping[str, Callable[[Path], str]]) -> None:
    """Duong dan truyen tuong minh cung phai khop pointer (neu pointer co gia tri) - khong tin ten file."""
    expected = pointer.get(KINDS[kind][1])
    if not expected:
        return
    actual = identity_fns[kind](Path(path))
    if actual != expected:
        raise ManifestResolutionError(f"{kind} manifest {path} co identity {actual} KHAC pointer {expected} - sai file cho warehouse nay.")


def resolve_manifest_paths(pointer: Mapping[str, Any], data_dir: Path, identity_fns: Mapping[str, Callable[[Path], str]],
                           *, explicit: Mapping[str, Path | None] | None = None, kinds: tuple[str, ...] = tuple(KINDS)) -> dict[str, Path]:
    explicit = explicit or {}
    resolved: dict[str, Path] = {}
    for kind in kinds:
        pattern, key = KINDS[kind]
        given = explicit.get(kind)
        if given is not None:
            verify_identity(kind, Path(given), pointer, identity_fns)
            resolved[kind] = Path(given)
            continue
        expected = pointer.get(key)
        if not expected:
            raise ManifestResolutionError(f"warehouse pointer khong co {key} - khong the tu resolve {kind} manifest; truyen duong dan tuong minh.")
        matches: list[Path] = []
        failures: list[str] = []
        for candidate in sorted(Path(data_dir).glob(pattern)):
            try:
                if identity_fns[kind](candidate) == expected:
                    matches.append(candidate)
            except Exception as exc:  # noqa: BLE001 - ung vien loi dinh dang chi bi bo qua co ghi nhan
                failures.append(f"{candidate.name}: {type(exc).__name__}")
        if len(matches) != 1:
            raise ManifestResolutionError(
                f"khong resolve duoc DUNG MOT {kind} manifest trong {data_dir} khop {key}={expected}: tim thay {len(matches)} "
                f"({[m.name for m in matches]}); ung vien loi: {failures[:5]}. Truyen duong dan tuong minh (--{kind}-manifest) neu file o noi khac.")
        resolved[kind] = matches[0]
    return resolved
