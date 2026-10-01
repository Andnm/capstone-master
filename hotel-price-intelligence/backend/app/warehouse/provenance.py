"""Provenance code cua batch: HEAD + BAT BUOC cac file quyet dinh du lieu phai trung HEAD.

GPT review 12 MAJOR 2: `git rev-parse HEAD` mot minh la provenance SAI khi working tree con file sua
hoac untracked - batch se ghi mot commit KHONG chua code da sinh ra canonical key/reference. Guard nay
fail-closed cho build chinh thuc; rehearsal disposable duoc opt-in `require_clean=False`, khi do
provenance mang hau to `+dirty` va `promote_warehouse` tu choi batch do.

Chi guard dung cac duong QUYET DINH du lieu, khong bat ca worktree sach: repo con file dang do cua
nguoi dung khong lien quan (GPT review 12: "khong can bat toan worktree sach").
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .errors import ProvenanceError

REPO_ROOT = Path(__file__).resolve().parents[4]
DIRTY_SUFFIX = "+dirty"
UNKNOWN = "unknown"
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
# Moi duong o day deu anh huong noi dung warehouse: code ETL, nguong/config, schema, va 4 module scraper
# ma warehouse dung lai (canonical key, fingerprint observation, slug hotel, git commit).
GUARDED_PATHS = (
    "hotel-price-intelligence/backend/app/warehouse",
    "hotel-price-intelligence/backend/app/core/config.py",
    "hotel-price-intelligence/backend/app/database/setup.sql",
    "hotel-price-intelligence/backend/app/scraper/reference.py",
    "hotel-price-intelligence/backend/app/scraper/anomaly_registry_lib.py",
    "hotel-price-intelligence/backend/app/scraper/url_utils.py",
    "hotel-price-intelligence/backend/app/scraper/data_contract.py",
)


def _git(repo_root: Path | str, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(repo_root), *args], check=True, capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "") or exc
        raise ProvenanceError(f"khong chay duoc `git {' '.join(args)}`: {detail}") from exc
    return done.stdout


def dirty_guarded_files(*, repo_root: Path | str = REPO_ROOT,
                        paths: tuple[str, ...] = GUARDED_PATHS) -> list[str]:
    """File trong `paths` dang khac HEAD: sua, staged, hoac untracked (`--untracked-files=all`)."""
    output = _git(repo_root, "status", "--porcelain", "--untracked-files=all", "--", *paths)
    return sorted(line[3:].strip() for line in output.splitlines() if line.strip())


def code_provenance(*, repo_root: Path | str = REPO_ROOT, paths: tuple[str, ...] = GUARDED_PATHS,
                    require_clean: bool = True) -> str:
    """Tra ve HEAD sha (40 hex) neu sach; `<sha>+dirty` neu ban va `require_clean=False`."""
    head = _git(repo_root, "rev-parse", "HEAD").strip()
    if len(head) != 40:
        raise ProvenanceError(f"`git rev-parse HEAD` tra ve {head!r} - khong phai commit sha.")
    dirty = dirty_guarded_files(repo_root=repo_root, paths=paths)
    if not dirty:
        return head
    if require_clean:
        raise ProvenanceError(
            f"{len(dirty)} file quyet dinh canonical/reference/import chua commit: {dirty[:12]}"
            f"{' ...' if len(dirty) > 12 else ''}. Batch se ghi canonicalization_git_commit={head[:12]} "
            f"KHONG chua code nay -> provenance sai. Commit truoc khi build chinh thuc, hoac chay "
            f"rehearsal voi require_clean=False (provenance mang hau to '{DIRTY_SUFFIX}', promote se tu choi)."
        )
    return f"{head}{DIRTY_SUFFIX}"


def is_dirty_provenance(value: str | None) -> bool:
    """True = khong replay duoc: rong, 'unknown', hoac co hau to '+dirty'."""
    return not value or value == UNKNOWN or value.endswith(DIRTY_SUFFIX)


def require_replayable_provenance(value: str | None, *, repo_root: Path | str = REPO_ROOT) -> str:
    """Bat buoc provenance DA GHI trong batch la commit THAT, checkout duoc (GPT review 14 MINOR).

    `is_dirty_provenance()` mot minh van cho qua mot sha 40 hex bat ky (vd 'a' * 40), tuc promote co the
    tuyen bo "replay duoc" cho mot commit khong ton tai. O day kiem them dinh dang va su TON TAI that.
    KHONG doi HEAD hien tai phai bang commit nay: promote mot batch lich su van hop le mien commit do
    con ton tai va batch qua validation/checksum.
    """
    if is_dirty_provenance(value):
        raise ProvenanceError(
            f"canonicalization_git_commit={value!r}: rong / 'unknown' / co hau to '{DIRTY_SUFFIX}' - code "
            f"quyet dinh du lieu chua commit luc build nen khong replay duoc."
        )
    if not _SHA1_RE.match(value):
        raise ProvenanceError(
            f"canonicalization_git_commit={value!r} khong phai SHA-1 40 hex chu thuong."
        )
    try:
        _git(repo_root, "cat-file", "-e", f"{value}^{{commit}}")
    except ProvenanceError as exc:
        raise ProvenanceError(
            f"commit {value[:12]} KHONG ton tai trong repo nay - provenance khong checkout/replay duoc."
        ) from exc
    return value
