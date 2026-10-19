"""Self-test THU VIEN THAT tren du lieu TONG HOP (GPT 12 §3.2): chay TRUOC khi nap dataset that, tren dung moi truong Colab sap huan luyen.

Mot test fake-XGBoost o may chinh chi chung minh day noi; day la bang chung duy nhat rang `xgboost` that + thiet bi that + xu ly trong so + luu/nap bundle hoat dong:
1. xgboost import duoc, ghi phien ban + build_info; cau hinh yeu cau cuda ma Booster khong bao cuda => DeviceError (khong am tham doi CPU).
2. Fit 2 ung vien XGB dau tien cua cau hinh that (trong so none va balanced) tren du lieu 3 lop tong hop; thiet bi that doc tu `Booster.save_config()`; classes_ == [0,1,2]; xac suat hop le.
3. Luu + nap bundle (joblib) => `predict_proba` giong het tung bit tren cung ma tran.
Co loi la DUNG (khong am tham bo XGBoost, khong chuyen sang CPU). Khong dung dataset, khong dung nhan that, khong ghi vao thu muc ket qua.

`run_smoke(cfg)`: chay TOAN BO duong huan luyen (CV -> finalist -> champion -> khoa -> ablation -> mo test -> predictions/bundle -> giao dich + vi tu thanh cong) tren fixture tong hop nho,
voi cau hinh that thu nho (cung device, quota, ho, seed), UOC LUONG o thu muc tam. Day la smoke "moi truong sach" tren dung moi truong ghim cua Colab truoc khi cham dataset that.
"""
from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from .candidates import Candidate, CandidateError, fit_estimator, predict_validated, sample_candidates, verify_candidate_set


class SelfTestError(RuntimeError):
    pass


def run_smoke(cfg: dict[str, Any]) -> dict[str, Any]:
    from .runner import execute_horizon, training_success
    from .synthetic import make_loaded, smoke_config
    from .transaction import verify_run_dir
    small = smoke_config(cfg)
    loaded = make_loaded()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            run_dir = execute_horizon(small, loaded, Path(tmp), run_id="smoke")
            manifest = verify_run_dir(run_dir)
        except Exception as exc:  # noqa: BLE001
            raise SelfTestError(f"smoke tong hop that bai: {type(exc).__name__}: {exc}") from exc
        report = _read_report(run_dir)
        if not training_success(manifest, run_dir, require_analysis=False) or manifest.get("contract_complete") is not True:
            raise SelfTestError(f"smoke tong hop khong hoan tat dung hop dong: status={manifest.get('status')} reasons={report.get('incomplete_reasons')}")
        return {"status": "ok", "champion": report["champion"], "devices": report["ledger"]["devices"], "n_models": len(report["models"]), "elapsed_seconds": report["ledger"]["elapsed_seconds"],
                "files": sorted(manifest["files"]), "synthetic": report["synthetic"]}


def _read_report(run_dir: Path) -> dict[str, Any]:
    import json
    return json.loads((Path(run_dir) / "h1_report.json").read_text(encoding="utf-8"))


def _synthetic(n: int = 600, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 8))
    score = x[:, 0] + 0.5 * x[:, 1] + rng.normal(0, 0.7, n)
    y = np.where(score > 1.0, 2, np.where(score < -1.0, 0, 1)).astype(np.int64)
    y[:3] = (0, 1, 2)                                                       # chac chan du ca 3 lop
    return x, y


def run_selftest(cfg: dict[str, Any], *, n_candidates: int = 2) -> dict[str, Any]:
    try:
        xgb = importlib.import_module("xgboost")
    except ImportError as exc:
        raise SelfTestError(f"xgboost khong import duoc: {exc}") from exc
    requested = str(cfg["run"]["device"])
    info: dict[str, Any] = {"xgboost_version": getattr(xgb, "__version__", None), "requested_device": requested}
    try:
        info["build_info_use_cuda"] = bool(xgb.build_info().get("USE_CUDA"))
    except Exception as exc:  # noqa: BLE001
        info["build_info_use_cuda"] = None
        info["build_info_error"] = f"{type(exc).__name__}: {exc}"[:200]
    try:
        info["candidate_set_sha256"] = verify_candidate_set(cfg)
    except CandidateError as exc:
        raise SelfTestError(str(exc)) from exc
    cands: list[Candidate] = [c for c in sample_candidates(cfg) if c.family == "xgb"][:n_candidates]
    if len(cands) < n_candidates or {c.params["weight"] for c in cands} != {"none", "balanced"}:
        raise SelfTestError("khong lay du 2 ung vien XGB (none + balanced) tu cau hinh")
    x, y = _synthetic()
    fits = []
    for cand in cands:
        try:
            estimator, device = fit_estimator(cand, cfg, x, y)
            proba = predict_validated(estimator, cfg, x)
        except Exception as exc:  # noqa: BLE001
            raise SelfTestError(f"{cand.id}: {type(exc).__name__}: {exc}") from exc
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.joblib"
            joblib.dump({"estimator": estimator}, path)
            restored = joblib.load(path)["estimator"]
        again = predict_validated(restored, cfg, x)
        if not np.array_equal(proba, again):
            raise SelfTestError(f"{cand.id}: predict_proba sau luu/nap khac ban dau")
        fits.append({"candidate": cand.id, "weight": cand.params["weight"], "device": device, "n_rows": int(len(y))})
    return {**info, "status": "ok", "fits": fits}
