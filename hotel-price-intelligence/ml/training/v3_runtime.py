"""Runtime v3 (C7'/C9'): so fit (ledger), ngan sach mem, kiem tra moi truong fail-closed + dinh danh moi truong.

- `FitLedger` dem MOI lan thu (ke ca smoke, pilot, CV, refit, control, ablation, null, lan loi/fallback CUDA->CPU): khong che retry, khong lay interim best.
- `check_environment`: lech `expected_versions` => `EnvironmentDriftError` (mac dinh). Opt-in `allow_drift=True` PHAI duoc quyet TRUOC moi fit: status `exploratory_env_drift`,
  fingerprint moi truong thuc nam trong manifest + model identity (hau to run-id don thuan khong du).
"""
from __future__ import annotations

import json
import platform
import time
from importlib import metadata
from typing import Any, Callable, Mapping

from .v3_contract import sha256_bytes

TRACKED_PACKAGES = ("scikit-learn", "xgboost", "numpy", "pandas", "pyarrow", "joblib", "pyyaml")


class EnvironmentDriftError(RuntimeError):
    pass


def collect_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {"python": platform.python_version()}
    for package in TRACKED_PACKAGES:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def check_environment(cfg: Mapping[str, Any], *, allow_drift: bool, actual: Mapping[str, str | None] | None = None) -> dict[str, Any]:
    """Kiem phien ban thuc te voi `environment.expected_versions`. Lech va khong allow_drift => EnvironmentDriftError (fail-closed, khong tu cai/nang cap giua cac fit)."""
    actual = dict(actual if actual is not None else collect_versions())
    expected = dict(cfg["environment"]["expected_versions"])
    mismatch = {pkg: {"expected": want, "actual": actual.get(pkg)} for pkg, want in expected.items() if actual.get(pkg) != want}
    fingerprint = sha256_bytes(json.dumps({"actual": actual, "expected": expected}, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    result = {"status": "pinned" if not mismatch else "exploratory_env_drift", "mismatch": mismatch, "actual": actual, "expected": expected,
              "fingerprint": fingerprint, "allow_drift": bool(allow_drift), "official_eligible": False}
    if mismatch and not allow_drift:
        detail = "; ".join(f"{p}: can {m['expected']}, co {m['actual']}" for p, m in mismatch.items())
        raise EnvironmentDriftError(f"moi truong lech phien ban da ghim ({detail}). Chay lai o cell setup (cai dung phien ban, restart runtime, verify) "
                                    f"hoac dat ALLOW_ENV_DRIFT=True TRUOC khi chay (ket qua se ghi exploratory_env_drift, khong dung chung identity).")
    return result


class FitLedger:
    def __init__(self, time_budget_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.budget = float(time_budget_seconds)
        self._clock = clock
        self._start = clock()
        self.entries: list[dict[str, Any]] = []

    def elapsed(self) -> float:
        return float(self._clock() - self._start)

    def remaining(self) -> float:
        return self.budget - self.elapsed()

    def over_budget(self) -> bool:
        return self.elapsed() > self.budget

    def add(self, kind: str, name: str, *, seconds: float, status: str = "ok", reason: str | None = None, **extra: Any) -> None:
        self.entries.append({"seq": len(self.entries), "kind": kind, "name": name, "seconds": round(float(seconds), 4), "status": status, "reason": reason, **extra})

    def summary(self, nominal: Mapping[str, Any] | None = None) -> dict[str, Any]:
        by_kind: dict[str, dict[str, int]] = {}
        for entry in self.entries:
            slot = by_kind.setdefault(entry["kind"], {"attempts": 0, "ok": 0, "failed": 0, "skipped": 0})
            slot["attempts"] += 1
            if entry["status"] in slot:
                slot[entry["status"]] += 1
        return {"time_budget_seconds": self.budget, "elapsed_seconds": round(self.elapsed(), 3), "over_budget": self.over_budget(),
                "total_attempts": len(self.entries), "total_fit_seconds": round(sum(e["seconds"] for e in self.entries), 3),
                "by_kind": by_kind, "nominal": dict(nominal) if nominal else None, "entries": self.entries}
