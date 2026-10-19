"""Kiem moi truong chay (fail-closed): phien ban cac thu vien quyet dinh ket qua phai KHOP CHINH XAC ban da ghim trong `environment.expected_versions` cua cau hinh.

Nguon ghim = chinh phien ban ma bo test cuc bo da chay (scikit-learn/numpy/pandas) va phien ban XGBoost cua lan Colab da thanh cong truoc do; khong co co 'cho phep lech'. Lech => thoat som
(notebook cai dung phien ban roi yeu cau Restart session). Ghi them (khong chan) phien ban cua `record_only`.
"""
from __future__ import annotations

import importlib.metadata as md
from typing import Any


class EnvironmentMismatch(RuntimeError):
    pass


def _version(package: str) -> str | None:
    try:
        return md.version(package)
    except md.PackageNotFoundError:
        return None


def check_environment(cfg: dict[str, Any]) -> dict[str, Any]:
    expected = dict(cfg["environment"]["expected_versions"])
    actual = {pkg: _version(pkg) for pkg in expected}
    mismatch = {pkg: {"expected": expected[pkg], "actual": actual[pkg]} for pkg in expected if actual[pkg] != expected[pkg]}
    recorded = {pkg: _version(pkg) for pkg in cfg["environment"].get("record_only", [])}
    return {"status": "ok" if not mismatch else "mismatch", "expected": expected, "actual": actual, "mismatch": mismatch, "record_only": recorded,
            "pip_install": " ".join(f"{pkg}=={ver}" for pkg, ver in expected.items())}


def require_environment(cfg: dict[str, Any]) -> dict[str, Any]:
    info = check_environment(cfg)
    if info["mismatch"]:
        raise EnvironmentMismatch(f"MOI TRUONG LECH phien ban da ghim: {info['mismatch']}. Cai dung: pip install {info['pip_install']} roi Restart session.")
    return info
