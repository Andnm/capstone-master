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
from pathlib import Path
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


def verify_packaged_execution(provenance: Mapping[str, Any], colab_manifest: Mapping[str, Any] | None, dataset_name: str) -> dict[str, Any]:
    """Cong rang buoc goi Colab cho CHAY DONG GOI (GPT file 16 M1), DOC LAP voi official/claim level (v3 luon official=False, khong bien dataset dev thanh official):
    khi provenance code = `code_manifest` (tuc chay tu goi giai nen):
      (1) COLAB_MANIFEST bat buoc va phai NOI mat ma voi CODE_MANIFEST dang chay + dataset dang chay (schema, hash 64-hex, archive code+dataset, created_at, code_manifest_sha256,
          code_sha256) - dung `require_lineage` nghiem ngat cua v2; manifest cua goi/dataset khac, thieu, hay created_at/hash sai => ProvenanceError (CLI thoat 3 TRUOC moi output/fit);
      (2) TAP FILE code trong `ml/` phai BANG DUNG tap file CODE_MANIFEST khai bao (khong file thua nhu `v3_stale.py` con sot trong /content/ml, khong thieu), va moi module `training.*`/
          `dataset_builder.*` dang duoc import phai la file nam trong manifest (chong code cu/khac che bong code da xac minh).
    Chay tu git (source != code_manifest) => khong ap dung (may chinh dung git HEAD + dirty)."""
    from .provenance import CODE_MANIFEST_NAME, ML_DIR, ProvenanceError, file_sha256, require_lineage, validate_code_manifest  # noqa: PLC0415

    if provenance.get("source") != "code_manifest":
        return {"packaged": False}
    try:
        require_lineage(dict(provenance), dict(colab_manifest) if colab_manifest else None, official=True, dataset_name=dataset_name)
    except ProvenanceError as exc:
        raise ProvenanceError(f"goi Colab khong noi duoc voi code/dataset dang chay (train-v3 bat buoc, doc lap official): {exc}") from exc
    ml_dir = Path(provenance["manifest_path"]).parent
    manifest = json.loads((ml_dir / CODE_MANIFEST_NAME).read_text(encoding="utf-8"))
    files = validate_code_manifest(manifest, root=ml_dir.parent)
    root = ml_dir.parent.resolve()
    present: set[str] = set()
    for path in ml_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.resolve().relative_to(root).as_posix()
        if "__pycache__" in rel.split("/") or rel.endswith(".pyc") or rel == f"ml/{CODE_MANIFEST_NAME}":
            continue
        present.add(rel)
    extra, missing = sorted(present - set(files)), sorted(set(files) - present)
    if extra or missing:
        raise ProvenanceError(f"tap file code trong ml/ khac CODE_MANIFEST: thua {extra[:8]}, thieu {missing[:8]} (giai nen sach vao thu muc moi, khong de file cu sot lai).")
    import sys  # noqa: PLC0415

    stray = []
    for name, module in list(sys.modules.items()):
        if name.split(".")[0] not in ("training", "dataset_builder") or not getattr(module, "__file__", None):
            continue
        try:
            rel = Path(module.__file__).resolve().relative_to(root).as_posix()
        except ValueError:
            stray.append(f"{name} nam NGOAI goi: {module.__file__}")
            continue
        if rel not in files:
            stray.append(f"{name} ({rel}) khong co trong CODE_MANIFEST")
    if stray:
        raise ProvenanceError("module dang chay khong thuoc code da xac minh: " + "; ".join(stray[:6]))
    return {"packaged": True, "manifest_files": len(files), "present_files": len(present), "code_sha256": provenance.get("code_sha256"),
            "manifest_sha256": file_sha256(ml_dir / CODE_MANIFEST_NAME)}


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
