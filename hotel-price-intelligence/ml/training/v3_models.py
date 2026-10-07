"""Uoc luong v3 (C2/C3/C6/C8): pool candidate lay mau MOT LAN, nha may HGB/XGB/RF/Ridge, smoke tuong thich muc tieu/thiet bi XGBoost.

XGBoost import MUON (may local khong cai; Colab co): thieu/khong tuong thich => ho XGB bi bo qua voi ly do ro rang (khong lam hong phan con lai). RF/HGB/Ridge luon CPU.
"""
from __future__ import annotations

import importlib.util
import time
from typing import Any, Mapping

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.model_selection import ParameterSampler

from .models import make_ridge

XGB_OBJECTIVES = ("reg:absoluteerror", "reg:pseudohubererror")


def xgboost_importable() -> bool:
    return importlib.util.find_spec("xgboost") is not None


def sample_pool(family: str, spec: Mapping[str, Any], n_iter: int, seed: int) -> list[dict[str, Any]]:
    """12 candidate/ho lay mau MOT LAN (ParameterSampler, seed co dinh), id on dinh `family-NN`; cung pool cho scorer A va B."""
    draws = list(ParameterSampler(spec["space"], n_iter=int(n_iter), random_state=int(seed)))
    pool = [{"candidate_id": f"{family}-{i:02d}", "family": family, "params": {k: _plain(v) for k, v in sorted(p.items())}} for i, p in enumerate(draws)]
    keys = {tuple(sorted(c["params"].items())) for c in pool}
    if len(keys) != len(pool):
        raise ValueError(f"pool {family} co candidate trung nhau")
    return pool


def _plain(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


def build_estimator(spec: Mapping[str, Any], params: Mapping[str, Any], *, seed: int, device: str = "cpu"):
    """`spec`: kind in {hgb, xgb, rf}; hgb dung `loss`, xgb dung `objective`. Tham so = fixed (neu co) + params (params thang)."""
    kind = spec["kind"]
    merged = {**(spec.get("fixed") or {}), **dict(params)}
    if kind == "hgb":
        return HistGradientBoostingRegressor(loss=spec["loss"], random_state=int(seed), **merged)
    if kind == "rf":
        return RandomForestRegressor(random_state=int(seed), n_jobs=-1, **merged)
    if kind == "xgb":
        import xgboost  # noqa: PLC0415 - import muon co chu dich
        return xgboost.XGBRegressor(objective=spec["objective"], random_state=int(seed), device=device, n_jobs=-1, **merged)
    raise ValueError(f"kind khong ho tro: {kind!r}")


def build_control(name: str, cfg: Mapping[str, Any], horizon: int, *, seed: int, device: str, features: list[str]):
    """Tra (estimator, input_kind) voi input_kind 'tree' (ma tran TreeEncoder) hoac 'raw' (Ridge: one-hot + impute + scale)."""
    spec = cfg["controls"][name]
    if spec["kind"] == "ridge":
        return make_ridge(float(spec["params"]["alpha"]), features), "raw"
    params = spec["params_by_horizon"][str(horizon)] if "params_by_horizon" in spec else spec["params"]
    return build_estimator(spec, params, seed=seed, device=device), "tree"


def xgb_smoke(objective: str, device: str, seed: int) -> dict[str, Any]:
    """C8: fit nho de kiem muc tieu/thiet bi (khong thay chung minh training that). Loi => ok=False + error (de fallback CPU hoac bo ho)."""
    started = time.time()
    try:
        import xgboost  # noqa: PLC0415
        rng = np.random.default_rng(int(seed))
        X = rng.normal(size=(400, 6))
        y = rng.normal(scale=0.02, size=400)
        extra = {"huber_slope": 0.05} if objective == "reg:pseudohubererror" else {}
        model = xgboost.XGBRegressor(objective=objective, tree_method="hist", device=device, n_estimators=5, max_depth=2, random_state=int(seed), n_jobs=1, **extra)
        model.fit(X, y)
        pred = np.asarray(model.predict(X), float)
        if not np.isfinite(pred).all():
            return {"objective": objective, "device": device, "ok": False, "error": "non_finite_prediction", "seconds": time.time() - started}
        return {"objective": objective, "device": device, "ok": True, "error": None, "seconds": time.time() - started}
    except Exception as exc:  # noqa: BLE001 - bat moi loi tuong thich (thieu thu vien, CUDA khong co, muc tieu khong ho tro)
        return {"objective": objective, "device": device, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:500], "seconds": time.time() - started}


def resolve_xgb_device(requested: str, seed: int, ledger) -> dict[str, Any]:
    """requested in {cuda, cpu, auto}. Thu `cuda` (neu yeu cau/auto) cho CA HAI muc tieu; loi => ghi fallback_reason va thu `cpu`; cpu loi => ho XGB bi bo qua.
    Moi lan thu duoc ghi vao ledger (ke ca lan loi). Tra {device|None, fallback_reason, smokes}."""
    smokes: list[dict[str, Any]] = []
    if not xgboost_importable():
        ledger.add("smoke", "xgboost_import", seconds=0.0, status="failed", reason="xgboost_not_installed")
        return {"device": None, "fallback_reason": "xgboost_not_installed", "smokes": smokes}
    order = ["cuda", "cpu"] if requested in ("cuda", "auto") else ["cpu"]
    fallback_reason = None
    for device in order:
        results = [xgb_smoke(objective, device, seed) for objective in XGB_OBJECTIVES]
        for r in results:
            ledger.add("smoke", f"xgb_smoke:{r['objective']}:{device}", seconds=r["seconds"], status="ok" if r["ok"] else "failed", reason=r["error"], device=device)
        smokes.extend(results)
        if all(r["ok"] for r in results):
            return {"device": device, "fallback_reason": fallback_reason, "smokes": smokes}
        if device == "cuda":
            fallback_reason = "; ".join(f"{r['objective']}: {r['error']}" for r in results if not r["ok"])[:500]
    return {"device": None, "fallback_reason": fallback_reason or "xgb_smoke_failed_on_cpu", "smokes": smokes}
