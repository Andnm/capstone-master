"""Dieu phoi huan luyen/danh gia v3 cho MOT horizon (hop dong C1-C18, thread discuss/model-results-improvement-20261008; GPT PASS file 14).

Luong (TEST chi cham SAU KHI khoa, cho danh sach PRESPECIFY - M2):
  1. frames primary (tai dung `horizon_frames` cua v2) -> sap thu tu on dinh -> ma tran (TreeEncoder co dinh, NaN giu nguyen) -> fold purged CV tren TRAIN
  2. smoke XGB (muc tieu x thiet bi) -> 3 ho (HGB-L1, XGB-abs, XGB-pH): moi ho 12 candidate lay mau MOT LAN, cham CA HAI scorer tren CUNG du doan CV
  3. winner A/B moi ho (tie persistence-first khong ap o muc ho; thu tu pool) -> refit TRAIN (dedup) -> controls co dinh -> ablation khoi ty le (HGB-L1, theo tung horizon)
  4. VALIDATION: champion_A (lift_vnd), champion_B (lift_log) trong {finalists, controls, persistence}; persistence thang khi hoa (C5'/C17)
  5. routing theo mode tu VALIDATION (C12); null hoan vi (C11) cho cau hinh champion_A
  6. KHOA -> TEST: persistence, champion_A/B, routed_A/B, controls co dinh (Ridge chi khi la champion) -> bao cao + du doan + bundle
`run_from_frames` thuan (khong doc dataset/khong provenance) de test bang du lieu tong hop; `run_horizon_v3` bao gom xac minh dataset.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import joblib
import numpy as np
import pandas as pd

from .cv import CVError
from .encoder import TreeEncoder, to_raw
from .runner import _dataset_summary, horizon_frames, sanitize
from .schema import read_dictionary, select_features
from .target import to_target
from .v3_contract import (CONFIG_VERSION_V3, PERSISTENCE, RATIO_BLOCK_COLUMNS, RATIO_BLOCK_DEFINITION, RATIO_BLOCK_SHA256, TRAINING_VERSION_V3, add_ratio_block,
                          feature_list_sha256, stable_sort)
from .v3_cv import cv_candidate, describe_folds, make_folds
from .v3_metrics import EvalSet
from .v3_models import build_control, build_estimator, resolve_xgb_device, sample_pool
from .v3_runtime import FitLedger
from .v3_select import ablation_decision, champion, mode_policy, null_summary, pick_family_winner


class TestAccessError(RuntimeError):
    """TEST bi truy cap truoc khi khoa hoac ngoai danh sach prespecify (M2)."""
    __test__ = False


class TestGate:
    """Cong khoa TEST: chi sau `lock(allowed)` moi co the `predict(name, fn)`, va CHI cho `name` thuoc danh sach prespecify. Ghi lai tung lan predict."""
    __test__ = False

    def __init__(self) -> None:
        self.locked = False
        self.allowed: set[str] = set()
        self.predicted: list[str] = []

    def lock(self, allowed: Iterable[str]) -> None:
        self.allowed, self.locked = set(allowed), True

    def predict(self, name: str, fn: Callable[[], np.ndarray]) -> np.ndarray:
        if not self.locked:
            raise TestAccessError(f"TEST chua khoa nhung bi predict cho {name!r}")
        if name not in self.allowed:
            raise TestAccessError(f"{name!r} khong nam trong danh sach TEST prespecify {sorted(self.allowed)}")
        result = fn()
        self.predicted.append(name)
        return result


def permute_within_dates(z: np.ndarray, dates: np.ndarray, seed: int) -> np.ndarray:
    """Hoan vi nhan TRONG tung ngay quan sat (hang da sap thu tu on dinh, ngay tang dan): rng = default_rng(seed) dung chung, duyet ngay tang dan, perm = rng.permutation(n_d)."""
    dates = np.asarray(dates).astype(str)
    if not (np.array(sorted(dates)) == dates).all():
        raise ValueError("hang phai sap theo ngay quan sat tang dan truoc khi hoan vi")
    starts = np.r_[0, np.flatnonzero(dates[1:] != dates[:-1]) + 1, len(dates)]
    rng = np.random.default_rng(int(seed))
    out = np.array(z, float, copy=True)
    for a, b in zip(starts[:-1], starts[1:]):
        out[a:b] = z[a:b][rng.permutation(b - a)]
    return out


def acc_at_tol(price: np.ndarray, y: np.ndarray, tol: float) -> float:
    return float(np.mean(np.abs(price - y) / y <= tol))


class _Model:
    """Dac ta mot ung vien/control: cach dung estimator, ma tran dau vao, vai tro."""

    def __init__(self, name: str, family: str, build: Callable[[], Any], matrix: str, params: Mapping[str, Any], role: str):
        self.name, self.family, self.build, self.matrix, self.params, self.role = name, family, build, matrix, dict(params), role
        self.estimator: Any = None
        self.z_train: np.ndarray | None = None
        self.z_val: np.ndarray | None = None
        self.status = "pending"
        self.reason: str | None = None
        self.roles: list[str] = []


def _fit_model(m: _Model, mats: Mapping[str, tuple[Any, Any]], ztr: np.ndarray, ledger: FitLedger, kind: str) -> None:
    started = time.time()
    try:
        est = m.build()
        Xtr, Xva = mats[m.matrix]
        est.fit(Xtr, ztr)
        z_tr, z_va = np.asarray(est.predict(Xtr), float), np.asarray(est.predict(Xva), float)
        if not (np.isfinite(z_tr).all() and np.isfinite(z_va).all()):
            raise FloatingPointError("non_finite_prediction")
        m.estimator, m.z_train, m.z_val, m.status = est, z_tr, z_va, "ok"
        ledger.add(kind, m.name, seconds=time.time() - started, status="ok", fits=1)
    except Exception as exc:  # noqa: BLE001
        m.status, m.reason = "failed", f"{type(exc).__name__}: {exc}"[:500]
        ledger.add(kind, m.name, seconds=time.time() - started, status="failed", reason=m.reason, fits=1)


def run_from_frames(frames: Mapping[str, pd.DataFrame], features: list[str], horizon: int, cfg: Mapping[str, Any], *, device_request: str = "auto",
                    env_status: Mapping[str, Any] | None = None, dataset: Mapping[str, Any] | None = None, info: Mapping[str, Any] | None = None,
                    provenance: Mapping[str, Any] | None = None, colab_manifest: Mapping[str, Any] | None = None, out_dir: Path | None = None,
                    ledger: FitLedger | None = None, gate: TestGate | None = None) -> dict[str, Any]:
    started = time.time()
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    seed, tie = int(cfg["seed"]), float(cfg["tie_atol"])
    n_boot, boot_seed = int(cfg["bootstrap"]["n"]), int(cfg["bootstrap"]["seed"])
    stable = float(cfg["stable_threshold"])
    ledger = ledger or FitLedger(float(cfg["budget"]["time_budget_seconds"]))
    gate = gate or TestGate()
    tr_df, va_df, te_df = (stable_sort(frames[name]) for name in ("train", "validation", "test"))
    rows = {"train": int(len(tr_df)), "validation": int(len(va_df)), "test": int(len(te_df))}
    report: dict[str, Any] = {
        "training_version": TRAINING_VERSION_V3, "config_version": cfg.get("version", CONFIG_VERSION_V3), "config_sha256": cfg.get("config_sha256"), "horizon": int(horizon),
        "seed": seed, "smoke": bool(cfg.get("smoke", False)),
        "claim_level": "dev_research_exploratory" if not cfg.get("smoke", False) else "smoke_not_results",
        "features": {"list": list(features), "sha256": feature_list_sha256(features), "n": len(features)},
        "ratio_block": {"version": cfg["ablation"]["block_version"], "sha256": RATIO_BLOCK_SHA256, "definition": RATIO_BLOCK_DEFINITION, "columns": list(RATIO_BLOCK_COLUMNS)},
        "dataset": dict(dataset) if dataset else None, "rows": rows, "primary_selection": dict(info) if info else None,
        "provenance": dict(provenance) if provenance else None, "colab_manifest": dict(colab_manifest) if colab_manifest else None,
        "environment": dict(env_status) if env_status else None,
        "evaluation_status": (dataset or {}).get("sufficiency_status") or "unknown",
        "test_policy": "TEST chi cham sau khi khoa champion/routing, chi cho danh sach prespecify; dev test da lo => tham do (M2/C14)",
    }
    short = [f"{k}={rows[k]}<{cfg['min_rows'][k]}" for k in ("train", "validation", "test") if rows[k] < int(cfg["min_rows"][k])]
    if short:
        report.update(status="skipped", reason=f"khong du mau toi thieu: {short}", ledger=ledger.summary(), elapsed_seconds=round(time.time() - started, 1))
        return _finish(report, out_dir, horizon, None)
    try:
        folds = make_folds(tr_df, horizon, cfg)
    except CVError as exc:
        report.update(status="skipped", reason=f"cv khong kha thi: {exc}", ledger=ledger.summary(), elapsed_seconds=round(time.time() - started, 1))
        return _finish(report, out_dir, horizon, None)
    report["folds"] = describe_folds(tr_df, folds)

    ev = {"train": EvalSet(tr_df, n_boot=n_boot, seed=boot_seed, stable_threshold=stable), "validation": EvalSet(va_df, n_boot=n_boot, seed=boot_seed, stable_threshold=stable)}
    ytr, ctr = tr_df["y_true"].to_numpy(float), tr_df["current_price"].to_numpy(float)
    ztr = to_target(tr_df["y_true"], tr_df["current_price"], "log_ratio")
    enc = TreeEncoder(features)
    block_features = list(features) + list(RATIO_BLOCK_COLUMNS)
    enc_block = TreeEncoder(block_features)
    trb, vab = add_ratio_block(tr_df), add_ratio_block(va_df)
    mats: dict[str, tuple[Any, Any]] = {"tree": (enc.transform(tr_df).to_numpy(float), enc.transform(va_df).to_numpy(float)),
                                        "tree_block": (enc_block.transform(trb).to_numpy(float), enc_block.transform(vab).to_numpy(float))}
    report["matrix_guards"] = {"train": {"nan": int(np.isnan(mats["tree"][0]).sum()), "inf": int(np.isinf(mats["tree"][0]).sum())},
                               "validation": {"nan": int(np.isnan(mats["tree"][1]).sum()), "inf": int(np.isinf(mats["tree"][1]).sum())},
                               "unknown_domain": {"train": enc.unknown_counts(tr_df), "validation": enc.unknown_counts(va_df)}}
    controls_cfg = cfg["controls"]
    needs_raw = "ridge" in controls_cfg
    if needs_raw:
        mats["raw"] = (to_raw(tr_df, features), to_raw(va_df, features))

    # ---- 2. thiet bi XGB (C8) ----
    families_cfg = cfg["tuning"]["families"]
    any_xgb = any(spec["kind"] == "xgb" for spec in families_cfg.values()) or any(s["kind"] == "xgb" for s in controls_cfg.values())
    device_info: dict[str, Any] = {"requested": device_request, "device": None, "fallback_reason": "not_needed", "smokes": []}
    if any_xgb:
        device_info = {"requested": device_request, **resolve_xgb_device(device_request, seed, ledger)}
    xgb_device = device_info["device"]
    report["device"] = device_info

    # ---- 3. ho + CV (C2-C5) ----
    families_report: dict[str, Any] = {}
    pools: dict[str, list[dict[str, Any]]] = {}
    finalists: dict[str, _Model] = {}
    n_iter = int(cfg["tuning"]["n_iter"])
    for family, spec in families_cfg.items():
        entry: dict[str, Any] = {"status": "pending", "reason": None, "complete": False, "candidates": [], "winner_A": None, "winner_B": None}
        families_report[family] = entry
        if spec["kind"] == "xgb" and xgb_device is None:
            entry.update(status="skipped", reason=device_info.get("fallback_reason") or "xgboost_unavailable")
            ledger.add("family", family, seconds=0.0, status="skipped", reason=entry["reason"], fits=0)
            continue
        pool = sample_pool(family, spec, n_iter, int(cfg["tuning"]["sampler_seed"]))
        pools[family] = pool
        family_started = time.time()
        for index, cand in enumerate(pool):
            if ledger.over_budget():
                entry.update(status="incomplete", reason="time_budget_exceeded")
                break
            build = (lambda c=cand, s=spec: build_estimator(s, c["params"], seed=seed, device=xgb_device or "cpu"))
            t0 = time.time()
            res = cv_candidate(build, mats["tree"][0], ztr, ctr, ytr, folds)
            ledger.add("pilot" if index == 0 else "cv", cand["candidate_id"], seconds=time.time() - t0, status="ok" if res["status"] == "ok" else "failed",
                       reason=res["reason"], fits=len(res["per_fold"]) + (0 if res["status"] == "ok" else 1), family=family)
            entry["candidates"].append({"candidate_id": cand["candidate_id"], "params": cand["params"], "status": res["status"], "reason": res["reason"],
                                        "per_fold": res["per_fold"], "pooled": res["pooled"], "seconds": round(res["seconds"], 3)})
            if index == 0:                                     # pilot: du phong tong thoi gian ho; vuot budget con lai => bo ho (khong lay interim best)
                projected = (time.time() - family_started) * n_iter
                if projected > ledger.remaining():
                    entry.update(status="incomplete", reason=f"projected_over_budget ({projected:.0f}s > remaining {ledger.remaining():.0f}s)")
                    break
        if entry["status"] == "pending":
            if len(entry["candidates"]) == len(pool):
                entry["complete"], entry["status"] = True, "complete"
            else:
                entry["status"], entry["reason"] = "incomplete", entry["reason"] or "not_all_candidates_evaluated"
        if not entry["complete"]:
            continue
        order = [c["candidate_id"] for c in pool]
        for arm, key in (("A", "lift_vnd"), ("B", "lift_log")):
            scores = {c["candidate_id"]: (c["pooled"][key] if c["pooled"] else None) for c in entry["candidates"]}
            pick = pick_family_winner(scores, order, tie)
            entry[f"winner_{arm}"] = {"candidate_id": pick["winner"], "reason": pick["reason"], "best_cv_lift": pick["best"], "tie_group": pick["tie_group"]}
        for arm in ("A", "B"):
            win = entry[f"winner_{arm}"]["candidate_id"]
            if win is None:
                continue
            cand = next(c for c in pool if c["candidate_id"] == win)
            if win not in finalists:
                finalists[win] = _Model(win, family, (lambda c=cand, s=spec: build_estimator(s, c["params"], seed=seed, device=xgb_device or "cpu")), "tree", cand["params"], "finalist")
            finalists[win].roles.append(f"{family}_winner_{arm}")
    for m in finalists.values():                              # refit TRAIN (dedup A=B)
        _fit_model(m, mats, ztr, ledger, "refit")

    # ---- controls (C6) ----
    controls: dict[str, _Model] = {}
    for name in cfg["tuning"]["control_order"]:
        if name not in controls_cfg:
            continue
        spec = controls_cfg[name]
        if spec["kind"] == "xgb" and xgb_device is None:
            ledger.add("control", name, seconds=0.0, status="skipped", reason=device_info.get("fallback_reason") or "xgboost_unavailable", fits=0)
            controls[name] = _Model(name, name, lambda: None, "tree", {}, "control")
            controls[name].status, controls[name].reason = "skipped", device_info.get("fallback_reason") or "xgboost_unavailable"
            continue
        est_factory = (lambda n=name: build_control(n, cfg, horizon, seed=seed, device=xgb_device or "cpu", features=features)[0])
        matrix = "raw" if spec["kind"] == "ridge" else "tree"
        params = spec.get("params_by_horizon", {}).get(str(horizon)) or spec.get("params") or {}
        controls[name] = _Model(name, name, est_factory, matrix, params, "control")
        _fit_model(controls[name], mats, ztr, ledger, "control")

    # ---- ablation (C10') ----
    ablation: dict[str, Any] = {"enabled": bool(cfg["ablation"]["enabled"]), "status": "not_run", "reason": None}
    ab_family = cfg["ablation"]["family"]
    ablation_model: _Model | None = None
    if ablation["enabled"]:
        fam_entry = families_report.get(ab_family)
        winner_id = ((fam_entry or {}).get("winner_A") or {}).get("candidate_id")
        if not fam_entry or not fam_entry["complete"] or winner_id is None or finalists.get(winner_id) is None or finalists[winner_id].status != "ok":
            ablation.update(status="skipped", reason="family_incomplete_or_no_winner")
        else:
            raw_fin = finalists[winner_id]
            t0 = time.time()
            res = cv_candidate(raw_fin.build, mats["tree_block"][0], ztr, ctr, ytr, folds)
            ledger.add("ablation", f"{winner_id}+{cfg['ablation']['block_version']}", seconds=time.time() - t0, status="ok" if res["status"] == "ok" else "failed",
                       reason=res["reason"], fits=len(res["per_fold"]) + (0 if res["status"] == "ok" else 1))
            raw_cv = next(c for c in families_report[ab_family]["candidates"] if c["candidate_id"] == winner_id)
            if res["status"] != "ok":
                ablation.update(status="failed", reason=res["reason"])
            else:
                ablation_model = _Model(f"{ab_family}-ratio1", ab_family, raw_fin.build, "tree_block", raw_fin.params, "ablation")
                _fit_model(ablation_model, mats, ztr, ledger, "ablation")
                if ablation_model.status == "ok":
                    incr = ev["validation"].incr_lift(raw_fin.z_val, ablation_model.z_val)
                    decision = ablation_decision(incr_lift=incr["incr_lift_vnd"], incr_ci_lower=(incr["ci95_hotel"] or [None])[0],
                                                 per_fold_block_mae=[f["mae_vnd"] for f in res["per_fold"]], per_fold_raw_mae=[f["mae_vnd"] for f in raw_cv["per_fold"]],
                                                 cfg=cfg["ablation"])
                    ablation.update(status="evaluated", raw_candidate=winner_id, block_version=cfg["ablation"]["block_version"], decision=decision,
                                    validation_incr=incr, cv_block=res["per_fold"], cv_raw=raw_cv["per_fold"])
                    if not decision["accepted"]:
                        ablation_model = None
                else:
                    ablation.update(status="failed", reason=ablation_model.reason)
                    ablation_model = None
    report["ablation"] = ablation

    # ---- 4. VALIDATION + champion (C5') ----
    pool_models: dict[str, _Model] = {**{k: v for k, v in finalists.items()}, **({ablation_model.name: ablation_model} if ablation_model else {}),
                                      **{k: v for k, v in controls.items() if v.status == "ok"}}
    pool_models = {k: v for k, v in pool_models.items() if v.status == "ok"}
    order: list[str] = []
    for family in families_cfg:
        order += sorted(n for n, m in pool_models.items() if m.family == family and m.role in ("finalist", "ablation"))
    order += [n for n in cfg["tuning"]["control_order"] if n in pool_models]
    table: dict[str, Any] = {}
    for name, m in pool_models.items():
        s_val = ev["validation"].summary(m.z_val)
        s_val["accuracy20"] = acc_at_tol(ev["validation"].price(m.z_val), ev["validation"].y, float(cfg["accuracy_tolerance"]))
        s_tr = ev["train"].summary(m.z_train, with_ci=False)
        table[name] = {"family": m.family, "role": m.role, "roles": m.roles, "params": m.params, "matrix": m.matrix, "train_in_sample": s_tr, "validation": s_val}
    p_val = ev["validation"].summary(np.zeros(len(va_df)))
    p_val["accuracy20"] = acc_at_tol(ev["validation"].cur, ev["validation"].y, float(cfg["accuracy_tolerance"]))
    scores = {n: {"lift_vnd": r["validation"]["lift_vnd"], "lift_log": r["validation"]["lift_log"]} for n, r in table.items()}
    champs = {arm: champion(arm, scores, order, tie) for arm in ("A", "B")}
    report["finalists"] = {n: r for n, r in table.items() if r["role"] in ("finalist", "ablation")}
    report["controls"] = {n: ({**table[n]} if n in table else {"status": controls[n].status, "reason": controls[n].reason}) for n in controls}
    report["persistence"] = {"validation": p_val}
    report["families"] = families_report
    report["champions"] = {arm: {k: v for k, v in c.items()} for arm, c in champs.items()}

    # ---- 5. routing (C12) tu VALIDATION ----
    routing: dict[str, Any] = {}
    routed_val: dict[str, np.ndarray] = {}
    va_modes = va_df["inference_mode"].astype(str).to_numpy()
    for arm, c in champs.items():
        name = c["winner"]
        if name == PERSISTENCE or name in routing:
            continue
        policy = {}
        for mode in cfg["routing"]["modes"]:
            mask = va_modes == mode
            stats = ev["validation"].mask_lift(pool_models[name].z_val, mask) if mask.any() else {"n": 0, "n_hotels_active": 0, "n_dates": 0, "lift_vnd": None,
                                                                                                    "ci95_hotel_full_split_population": None}
            policy[mode] = {**mode_policy(stats, cfg["routing"], tie), "validation_stats": stats}
        unknown = sorted(set(va_modes) - set(cfg["routing"]["modes"]))
        for mode in unknown:
            policy[mode] = {"use_model": False, "reason": "mode_not_configured"}
        routing[name] = policy
        use = np.array([policy.get(m, {"use_model": False})["use_model"] for m in va_modes])
        routed_val[name] = np.where(use, pool_models[name].z_val, 0.0)
    report["routing"] = {"policy_by_model": routing, "source": "VALIDATION only; ordinary khong tham gia; persistence mac dinh khi khong du bang chung"}

    # ---- 5b. null hoan vi (C11) cho champion_A ----
    null_cfg = cfg["null_test"]
    champ_a = champs["A"]["winner"]
    if not null_cfg["enabled"]:
        report["null_test"] = {"status": "disabled"}
    elif champ_a == PERSISTENCE:
        report["null_test"] = {"status": "not_applicable", "reason": "champion_A la persistence (khong co estimator de hoan vi)"}
    else:
        m = pool_models[champ_a]
        dates_tr = tr_df["vn_observation_date"].astype(str).to_numpy()
        nulls = []
        for i in range(int(null_cfg["n"])):
            zp = permute_within_dates(ztr, dates_tr, int(null_cfg["seed_base"]) + i)
            t0 = time.time()
            try:
                est = m.build()
                est.fit(mats[m.matrix][0], zp)
                zv = np.asarray(est.predict(mats[m.matrix][1]), float)
                lifts = ev["validation"].lifts(zv)
                nulls.append({"i": i, "perm_seed": int(null_cfg["seed_base"]) + i, **lifts})
                ledger.add("null", f"{champ_a}#perm{i}", seconds=time.time() - t0, status="ok", fits=1)
            except Exception as exc:  # noqa: BLE001
                nulls.append({"i": i, "perm_seed": int(null_cfg["seed_base"]) + i, "lift_vnd": None, "lift_log": None, "error": f"{type(exc).__name__}: {exc}"[:300]})
                ledger.add("null", f"{champ_a}#perm{i}", seconds=time.time() - t0, status="failed", reason=str(exc)[:200], fits=1)
        real = ev["validation"].lifts(m.z_val)
        report["null_test"] = {"status": "ok", "model": champ_a, "nulls": nulls, **null_summary(real, nulls)}

    # ---- 6. KHOA -> TEST ----
    test_models: list[str] = [PERSISTENCE]
    for arm in ("A", "B"):
        n = champs[arm]["winner"]
        if n != PERSISTENCE and n not in test_models:
            test_models.append(n)
    for n in routing:
        if f"routed:{n}" not in test_models:
            test_models.append(f"routed:{n}")
    for n in ("hgb_l2", "xgb_l2", "rf_l2"):
        if n in pool_models and n not in test_models:
            test_models.append(n)
    gate.lock(test_models)
    report["test"] = {"locked": True, "prespecified_models": list(test_models)}
    ev["test"] = EvalSet(te_df, n_boot=n_boot, seed=boot_seed, stable_threshold=stable)
    te_mats = {"tree": enc.transform(te_df).to_numpy(float)}
    te_block = add_ratio_block(te_df)
    if any(pool_models[n].matrix == "tree_block" for n in test_models if n in pool_models):
        te_mats["tree_block"] = enc_block.transform(te_block).to_numpy(float)
    if any(pool_models[n].matrix == "raw" for n in test_models if n in pool_models):
        te_mats["raw"] = to_raw(te_df, features)
    z_test: dict[str, np.ndarray] = {PERSISTENCE: np.zeros(len(te_df))}
    for n in test_models:                                     # chi estimator that su dung predict(TEST), qua cong khoa
        if n == PERSISTENCE or n.startswith("routed:"):
            continue
        m = pool_models[n]
        z_test[n] = np.asarray(gate.predict(n, lambda mm=m: mm.estimator.predict(te_mats[mm.matrix])), float)
        if not np.isfinite(z_test[n]).all():
            raise FloatingPointError(f"du doan TEST khong huu han: {n}")
    te_modes = te_df["inference_mode"].astype(str).to_numpy()
    for n in test_models:                                     # routed = tinh tu du doan da co (khong predict them), chinh sach khoa tu VALIDATION
        if n.startswith("routed:"):
            base = n.split(":", 1)[1]
            use = np.array([routing[base].get(m, {"use_model": False})["use_model"] for m in te_modes])
            z_test[n] = np.where(use, z_test[base], 0.0)
    report["test"]["predicted_models"] = list(gate.predicted)
    report["test"]["table"] = {}
    report["test"]["strata"], report["test"]["breakdowns"] = {}, {}
    ev_te = ev["test"]
    for n in test_models:
        s = ev_te.summary(z_test[n])
        s["accuracy20"] = acc_at_tol(ev_te.price(z_test[n]), ev_te.y, float(cfg["accuracy_tolerance"]))
        s["persistence_accuracy20"] = acc_at_tol(ev_te.cur, ev_te.y, float(cfg["accuracy_tolerance"]))
        s["median_pred_over_current_minus1_on_exact_unchanged"] = ev_te.exact_unchanged_bias(z_test[n])
        report["test"]["table"][n] = s
        if n != PERSISTENCE:
            report["test"]["strata"][n] = ev_te.strata(z_test[n])
    for arm in ("A", "B"):
        n = champs[arm]["winner"]
        if n == PERSISTENCE:
            continue
        for key, zt, zv in ((n, z_test[n], pool_models[n].z_val), (f"routed:{n}", z_test[f"routed:{n}"], routed_val[n])):
            report["test"]["breakdowns"][key] = {
                "test": {col: ev_te.breakdown(zt, col) for col in ("vn_observation_date", "inference_mode", "lead_time_bucket", "city")},
                "validation": {col: ev["validation"].breakdown(zv, col) for col in ("vn_observation_date", "inference_mode", "lead_time_bucket", "city")}}
        report["test"].setdefault("paired_vs_controls", {})[n] = {
            c: ev_te.incr_lift(z_test[c], z_test[n]) for c in ("hgb_l2", "xgb_l2", "rf_l2") if c in z_test}
    report["validation_table"] = {PERSISTENCE: {"validation": p_val}, **table}
    report["claims"] = _claims(report, champs, cfg)
    n_folds = len(folds)
    nominal_upper_bound = {"cv_fits": len(families_cfg) * n_iter * n_folds, "refit_max": 2 * len(families_cfg), "controls": len(controls_cfg),
                           "ablation": (n_folds + 1) if cfg["ablation"]["enabled"] else 0, "smoke_max": 4 if any_xgb else 0,
                           "null": int(null_cfg["n"]) if null_cfg["enabled"] else 0, "seed_stability": 0 if not cfg["budget"].get("seed_stability") else None}
    nominal_upper_bound["total"] = sum(v for v in nominal_upper_bound.values() if isinstance(v, int))
    ledger_summary = ledger.summary(nominal_upper_bound)
    ledger_summary["total_fits"] = int(sum(int(e.get("fits", 1)) for e in ledger.entries))
    report["ledger"] = ledger_summary
    incomplete = [f for f, e in families_report.items() if not e["complete"]]
    report["contract_complete"] = bool(not incomplete and all(controls[c].status == "ok" for c in controls))
    report["warnings"] = ([f"ho khong day du: {incomplete}"] if incomplete else []) + (
        ["moi truong lech phien ban da ghim (exploratory_env_drift)"] if (env_status or {}).get("status") == "exploratory_env_drift" else []) + (
        ["run smoke: khong phai ket qua"] if cfg.get("smoke") else [])
    report["status"] = "ok"
    report["elapsed_seconds"] = round(time.time() - started, 1)
    bundle_inputs = {"pool_models": pool_models, "champs": champs, "routing": routing, "z_val": routed_val, "z_test": z_test, "test_models": test_models}
    _write_predictions(out_dir, horizon, va_df, te_df, pool_models, z_test, ev, bundle_inputs)
    _write_bundles(out_dir, horizon, cfg, report, features, enc, enc_block, pool_models, champs, routing, env_status)
    return _finish(report, out_dir, horizon, ledger)


def _claims(report: Mapping[str, Any], champs: Mapping[str, Any], cfg: Mapping[str, Any]) -> dict[str, Any]:
    thr = float(cfg["claims"]["ordinary_warning_threshold"])
    out: dict[str, Any] = {"level": "dev_champion_exploratory", "claim_ready": False,
                           "why_not_claim": "dev test da lo; claim/serving chi tren holdout moi dong bang truoc (C18); mau 4-7 ngay quan sat; CI theo khach san co dieu kien tren ngay da thay",
                           "ordinary_warning_threshold": thr, "ordinary_warning": {}}
    for arm, c in champs.items():
        n = c["winner"]
        if n == PERSISTENCE:
            out["ordinary_warning"][arm] = {"model": PERSISTENCE, "note": "persistence thang tren VALIDATION; khong co tuyen bo ky nang hoc duoc"}
            continue
        for key in (n, f"routed:{n}"):
            strata = report["test"]["strata"].get(key) or {}
            ordinary = (strata.get("ordinary") or {}).get("lift_vnd")
            warn = bool(ordinary is not None and ordinary < thr)
            out["ordinary_warning"][f"{arm}:{key}"] = {"ordinary_lift_vnd_test": ordinary, "below_threshold": warn,
                                                         "wording": "khong goi 'cai thien rong': pooled gain di cung ordinary loss" if warn else "khong co canh bao pham vi (khong phai bang chung ky nang)"}
    return out


def _write_predictions(out_dir: Path | None, horizon: int, va_df: pd.DataFrame, te_df: pd.DataFrame, pool_models: Mapping[str, _Model], z_test: Mapping[str, np.ndarray],
                       ev: Mapping[str, EvalSet], bundle_inputs: Mapping[str, Any]) -> None:
    if out_dir is None:
        return
    keys = ["hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date", "inference_mode", "city", "lead_time_bucket", "current_price", "y_true"]
    frames = []
    for split, df, evs in (("validation", va_df, ev["validation"]), ("test", te_df, ev["test"])):
        part = df[[c for c in keys if c in df.columns]].copy()
        part.insert(4, "split", split)
        part["pred_persistence"] = df["current_price"].to_numpy(float)
        if split == "validation":
            for n, m in pool_models.items():
                part[f"pred_{n}"] = evs.price(m.z_val)
            for n, zv in bundle_inputs["z_val"].items():
                part[f"pred_routed:{n}"] = evs.price(zv)
        else:
            for n, zt in z_test.items():
                if n != PERSISTENCE:
                    part[f"pred_{n}"] = evs.price(zt)
        frames.append(part)
    pd.concat(frames, ignore_index=True).to_parquet(Path(out_dir) / f"h{horizon}_predictions_v3.parquet", index=False)


def _write_bundles(out_dir: Path | None, horizon: int, cfg: Mapping[str, Any], report: Mapping[str, Any], features: list[str], enc: TreeEncoder, enc_block: TreeEncoder,
                   pool_models: Mapping[str, _Model], champs: Mapping[str, Any], routing: Mapping[str, Any], env_status: Mapping[str, Any] | None) -> None:
    """Payload serving du de tai lap: estimator + feature list/encoder/khoi ty le + routing + phien ban/fingerprint moi truong + dinh danh config (khong phai 'da phuc vu production')."""
    if out_dir is None:
        return
    chosen: dict[str, list[str]] = {}
    for arm, c in champs.items():
        if c["winner"] != PERSISTENCE:
            chosen.setdefault(c["winner"], []).append(f"champion_{arm}")
    for name, roles in chosen.items():
        m = pool_models[name]
        bundle = {"model": m.estimator, "name": name, "roles": roles, "family": m.family, "params": m.params, "matrix": m.matrix,
                  "features": list(features), "feature_list_sha256": feature_list_sha256(features),
                  "ratio_block": ({"version": cfg["ablation"]["block_version"], "sha256": RATIO_BLOCK_SHA256, "columns": list(RATIO_BLOCK_COLUMNS)} if m.matrix == "tree_block" else None),
                  "encoder_categories": (enc_block if m.matrix == "tree_block" else enc).categories, "target_transform": "log_ratio", "horizon": int(horizon),
                  "routing_policy": routing.get(name), "config_sha256": cfg.get("config_sha256"), "training_version": TRAINING_VERSION_V3,
                  "dataset": report.get("dataset"), "provenance": report.get("provenance"), "environment_fingerprint": (env_status or {}).get("fingerprint"),
                  "environment_status": (env_status or {}).get("status"), "environment_actual": (env_status or {}).get("actual"), "claim_level": report["claim_level"],
                  "note": "bundle dev; KHONG phai package da phuc vu production"}
        joblib.dump(bundle, Path(out_dir) / f"h{horizon}_bundle_{name.replace(':', '_')}.joblib")


def _finish(report: dict[str, Any], out_dir: Path | None, horizon: int, ledger: FitLedger | None) -> dict[str, Any]:
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"h{horizon}_report.json").write_text(json.dumps(sanitize(report), ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")
    return report


def run_horizon_v3(dataset_dir: Path | str, horizon: int, cfg: Mapping[str, Any], out_dir: Path | str, *, context: Mapping[str, Any], device_request: str = "auto",
                   env_status: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from .provenance import verify_frame  # noqa: PLC0415

    dataset_dir, out_dir = Path(dataset_dir), Path(out_dir)
    samples = pd.read_parquet(dataset_dir / "samples.parquet")
    verify_frame(samples, context["dataset_meta"])
    features = select_features(read_dictionary(dataset_dir))
    frames, info = horizon_frames(samples, horizon, cfg)
    dataset = _dataset_summary(context["dataset_meta"], dataset_dir, horizon)
    report = run_from_frames(frames, features, horizon, cfg, device_request=device_request, env_status=env_status, dataset=dataset, info=info,
                             provenance=context["provenance"], colab_manifest=context["colab_manifest"], out_dir=out_dir)
    report["outside_evaluation_whitelist"] = int(horizon) not in dataset["evaluation_horizons"]
    return report
