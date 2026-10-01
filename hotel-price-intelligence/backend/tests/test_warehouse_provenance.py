"""Provenance guard (GPT review 12 MAJOR 2) va hash `etl_config` da pin (MAJOR 1) - pure.

Khong mock git: tao repo THAT trong tmp_path roi lam ban dung 1 file. Loi can chan la "batch ghi commit
khong chua code da sinh ra du lieu", chi thu duoc bang git that chu khong bang mock.
"""
from __future__ import annotations

import subprocess

import pytest

from app.core.config import settings
from app.warehouse.errors import ProvenanceError
from app.warehouse.etl_config import (
    canonicalization_config,
    canonicalization_config_sha256,
    etl_config,
    etl_config_sha256,
)
from app.warehouse.provenance import (
    DIRTY_SUFFIX,
    code_provenance,
    dirty_guarded_files,
    is_dirty_provenance,
    require_replayable_provenance,
)

GUARDED = ("pkg",)


def git(repo, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "test")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "canon.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("y = 1\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_worktree_sach_thi_tra_ve_dung_head(repo):
    head = git(repo, "rev-parse", "HEAD").strip()
    assert code_provenance(repo_root=repo, paths=GUARDED) == head
    assert dirty_guarded_files(repo_root=repo, paths=GUARDED) == []
    assert not is_dirty_provenance(head)


def test_file_guard_bi_sua_thi_strict_fail(repo):
    (repo / "pkg" / "canon.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(ProvenanceError, match="chua commit"):
        code_provenance(repo_root=repo, paths=GUARDED)


def test_file_guard_untracked_cung_tinh_la_ban(repo):
    (repo / "pkg" / "moi.py").write_text("z = 1\n", encoding="utf-8")
    assert dirty_guarded_files(repo_root=repo, paths=GUARDED) == ["pkg/moi.py"]
    with pytest.raises(ProvenanceError):
        code_provenance(repo_root=repo, paths=GUARDED)


def test_require_clean_false_tra_ve_hau_to_dirty(repo):
    (repo / "pkg" / "canon.py").write_text("x = 3\n", encoding="utf-8")
    value = code_provenance(repo_root=repo, paths=GUARDED, require_clean=False)
    assert value.endswith(DIRTY_SUFFIX) and is_dirty_provenance(value)
    assert len(value) <= 64  # cot canonicalization_git_commit la VARCHAR(64)


def test_file_ngoai_pham_vi_guard_khong_chan_build(repo):
    """Repo that con file dang do cua nguoi dung khong lien quan - khong duoc chan build (GPT review 12)."""
    (repo / "other.py").write_text("y = 2\n", encoding="utf-8")
    (repo / "rac.txt").write_text("tmp\n", encoding="utf-8")
    assert code_provenance(repo_root=repo, paths=GUARDED) == git(repo, "rev-parse", "HEAD").strip()


def test_khong_phai_git_repo_thi_fail(tmp_path):
    with pytest.raises(ProvenanceError):
        code_provenance(repo_root=tmp_path, paths=GUARDED)


@pytest.mark.parametrize("value", [None, "", "unknown", "a" * 40 + DIRTY_SUFFIX])
def test_is_dirty_provenance_bat_moi_dang_khong_replay_duoc(value):
    assert is_dirty_provenance(value)


def test_etl_config_sha256_doi_khi_doi_nguong(monkeypatch):
    """Co so cua guard MAJOR 1: doi threshold -> hash lech -> rebuild reference bi tu choi truoc moi write."""
    before = etl_config_sha256()
    monkeypatch.setattr(settings, "REFERENCE_MIN_COVERAGE", settings.REFERENCE_MIN_COVERAGE / 2)
    assert etl_config()["reference_min_coverage"] == settings.REFERENCE_MIN_COVERAGE
    assert etl_config_sha256() != before


def test_canonicalization_config_hash_on_dinh_va_dung_dinh_dang():
    config = canonicalization_config()
    assert config["sold_out_policy"] == "P-A" and config["version"].startswith("warehouse-canon-")
    digest = canonicalization_config_sha256()
    assert len(digest) == 64 and digest == canonicalization_config_sha256()


# --- verifier cho provenance DA GHI trong batch (GPT review 14 MINOR): phai la commit that, ton tai
def test_verifier_chap_nhan_commit_that(repo):
    head = git(repo, "rev-parse", "HEAD").strip()
    assert require_replayable_provenance(head, repo_root=repo) == head


@pytest.mark.parametrize("value,match", [
    ("a" * 40, "KHONG ton tai"),      # dung dinh dang nhung khong co trong git
    ("A" * 40, "40 hex"),             # chu hoa
    ("abc123", "40 hex"),
    ("unknown", "khong replay duoc"),
    ("", "khong replay duoc"),
])
def test_verifier_tu_choi_provenance_khong_replay_duoc(repo, value, match):
    with pytest.raises(ProvenanceError, match=match):
        require_replayable_provenance(value, repo_root=repo)


def test_verifier_tu_choi_hau_to_dirty(repo):
    head = git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(ProvenanceError, match="khong replay duoc"):
        require_replayable_provenance(head + DIRTY_SUFFIX, repo_root=repo)
