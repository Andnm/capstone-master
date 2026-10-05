"""Dieu phoi huan luyen/danh gia mot horizon: baseline -> (tune) RF/XGBoost/Ridge -> chon theo VALIDATION -> test mot lan -> luu.

Quy tac chong ro ri/ban cao (CLAUDE.md muc 6.3, 10): chi dung mau `label_usable_h{k}` va co gia muc tieu; encoder/danh muc/trung vi chi hoc tu train;
CV theo thoi gian co purge tren train; chon mo hinh bang validation; test chi tinh mot lan cho mo hinh da chon + baseline; seed co dinh tu config.
"""
from __future__ import annotations

import json
import platform
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn

from . import TRAINING_VERSION
from .cv import CVError, PurgedExpandingWindowSplit
from .encoder import TreeEncoder, to_raw
from .metrics import metrics_by, regression_metrics
from .models import make_ridge, make_tree_model, xgboost_available
from .schema import read_dictionary, select_features
from .target import to_price, to_target
from .tuning import tune

SPLITS = ("train", "validation", "test")
ID_COLUMNS = ["hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date", "split", "inference_mode", "city",
              "lead_time_bucket", "current_price", "hotel_seen_in_train"]


def _library_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {"python": platform.python_version(), "sklearn": sklearn.__version__, "pandas": pd.__version__,
                                       "numpy": np.__version__}
    try:
        import xgboost  # noqa: PLC0415
        versions["xgboost"] = xgboost.__version__
    except ImportError:
        versions["xgboost"] = None
    return versions


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    raise TypeError(f"khong serialize duoc {type(obj)}")


def horizon_frames(samples: pd.DataFrame, h: int, cfg: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """Chi mau co nhan dung duoc va gia muc tieu hop le; val/test 'primary' chi gom khach san da thay o train (neu cau hinh)."""
    usable = samples[samples[f"label_usable_h{h}"].fillna(False).astype(bool) & samples[f"y_price_h{h}"].notna()
                     & (samples["current_price"] > 0) & (samples[f"y_price_h{h}"] > 0)].copy()
    usable["y_true"] = usable[f"y_price_h{h}"].astype(float)
    out = {name: usable[usable["split"] == name].copy() for name in SPLITS}
    if cfg.get("primary_requires_seen_hotel", True):
        for name in ("validation", "test"):
            out[name] = out[name][out[name]["hotel_seen_in_train"].fillna(False).astype(bool)].copy()
    return out


def _metrics(frame: pd.DataFrame, pred_price, cfg) -> dict[str, Any]:
    return regression_metrics(frame["y_true"], pred_price, frame["current_price"], tol=float(cfg["accuracy_tolerance"]),
                              stable=float(cfg["stable_threshold"]))


def _ratio_median_baseline(train: pd.DataFrame):
    ratio = (train["y_true"] / train["current_price"]).astype(float)
    keyed = ratio.groupby([train["city"].astype(str), train["lead_time_bucket"].astype(str)]).median()
    overall = float(ratio.median())

    def predict(frame: pd.DataFrame) -> np.ndarray:
        idx = pd.MultiIndex.from_arrays([frame["city"].astype(str), frame["lead_time_bucket"].astype(str)])
        return frame["current_price"].to_numpy(dtype=float) * keyed.reindex(idx).fillna(overall).to_numpy(dtype=float)
    return predict


def _dataset_meta(dataset_dir: Path, h: int) -> dict[str, Any]:
    meta: dict[str, Any] = {"dataset_dir": str(dataset_dir)}
    checks = dataset_dir / "output_checksums.json"
    if checks.exists():
        data = json.loads(checks.read_text(encoding="utf-8"))
        entry = data.get("samples.parquet", data)
        meta["samples_content_sha256"] = entry.get("content_sha256") if isinstance(entry, dict) else None
        meta["samples_file_sha256"] = entry.get("file_sha256") if isinstance(entry, dict) else None
    suff = dataset_dir / "sufficiency_report.json"
    if suff.exists():
        horizon = json.loads(suff.read_text(encoding="utf-8")).get("horizons", {}).get(f"h{h}", {})
        meta["sufficiency_status"] = horizon.get("status")
        meta["sufficiency_failed_gates"] = horizon.get("failed_gates")
    else:
        meta["sufficiency_status"] = None
    return meta


def run_horizon(dataset_dir: Path | str, h: int, cfg: dict[str, Any], out_dir: Path | str, models: list[str]) -> dict[str, Any]:
    dataset_dir, out_dir = Path(dataset_dir), Path(out_dir)
    started = time.time()
    seed = int(cfg["seed"])
    kind = cfg["target_transform"]
    samples = pd.read_parquet(dataset_dir / "samples.parquet")
    dictionary = read_dictionary(dataset_dir)
    features = select_features(dictionary, exclude_groups=cfg.get("exclude_groups") or (), exclude_columns=cfg.get("exclude_columns") or ())
    frames = horizon_frames(samples, h, cfg)
    meta = _dataset_meta(dataset_dir, h)
    report: dict[str, Any] = {
        "training_version": TRAINING_VERSION, "config_version": cfg["version"], "config_sha256": cfg["config_sha256"], "horizon": h,
        "seed": seed, "target_transform": kind, "features": features, "dataset": meta,
        "rows": {name: int(len(frame)) for name, frame in frames.items()},
        "evaluation_status": meta.get("sufficiency_status") or "unknown",
        "library_versions": _library_versions(),
        "xgb_device": (cfg["models"]["xgb"].get("fixed") or {}).get("device", "cpu"),
    }
    short = [f"{name}={report['rows'][name]}<{cfg['min_rows'][name]}" for name in SPLITS if report["rows"][name] < int(cfg["min_rows"][name])]
    if short:
        report.update(status="skipped", reason=f"khong du mau toi thieu: {short}")
        return _write(report, out_dir, h, started)

    train, val, test = frames["train"], frames["validation"], frames["test"]
    y_train = to_target(train["y_true"], train["current_price"], kind)
    encoder = TreeEncoder(features).fit(train)
    X_train_tree, X_val_tree, X_test_tree = (encoder.transform(f) for f in (train, val, test))
    X_train_raw, X_val_raw, X_test_raw = (to_raw(f, features) for f in (train, val, test))

    results: dict[str, dict[str, Any]] = {}
    fitted: dict[str, Any] = {}
    preds: dict[str, dict[str, np.ndarray]] = {}

    # Baseline (khong hoc tham so tren val/test).
    ratio_median = _ratio_median_baseline(train)
    baseline_preds = {
        "persistence": lambda f: f["current_price"].to_numpy(dtype=float),
        "ratio_median_by_city_leadtime": ratio_median,
    }
    for name, fn in baseline_preds.items():
        preds[name] = {"validation": fn(val), "test": fn(test)}
        results[name] = {"kind": "baseline", "validation": _metrics(val, preds[name]["validation"], cfg)}

    wanted = set(models)
    if "ridge" in wanted:
        ridge = make_ridge(float(cfg["models"]["ridge"]["alpha"]), features).fit(X_train_raw, y_train)
        fitted["ridge"] = ridge
        preds["ridge"] = {"validation": to_price(ridge.predict(X_val_raw), val["current_price"], kind),
                          "test": to_price(ridge.predict(X_test_raw), test["current_price"], kind)}
        results["ridge"] = {"kind": "model", "params": {"alpha": float(cfg["models"]["ridge"]["alpha"])},
                            "validation": _metrics(val, preds["ridge"]["validation"], cfg)}

    tree_names = [m for m in ("rf", "xgb") if m in wanted]
    folds = None
    if tree_names:
        try:
            cv = PurgedExpandingWindowSplit(int(cfg["cv"]["n_splits"]), gap_days=h, min_train_days=int(cfg["cv"]["min_train_days"]))
            folds = list(cv.split(train["vn_observation_date"]))
        except CVError as exc:
            for name in tree_names:
                results[name] = {"kind": "model", "status": "skipped", "reason": f"cv khong kha thi: {exc}"}
            tree_names = []
    for name in tree_names:
        if name == "xgb" and not xgboost_available():
            results[name] = {"kind": "model", "status": "skipped", "reason": "chua cai xgboost"}
            continue
        tuning = tune(name, X_train_tree, y_train, folds, cfg, seed)
        model = make_tree_model(name, cfg, seed, tuning["best_params"]).fit(X_train_tree, y_train)
        fitted[name] = model
        preds[name] = {"validation": to_price(model.predict(X_val_tree), val["current_price"], kind),
                       "test": to_price(model.predict(X_test_tree), test["current_price"], kind)}
        results[name] = {"kind": "model", "params": tuning["best_params"], "tuning": tuning,
                         "validation": _metrics(val, preds[name]["validation"], cfg)}

    # Chon theo VALIDATION (Accuracy@tol cao hon, hoa -> MAE thap hon). Chi xet mo hinh hoc duoc, khong xet baseline.
    candidates = [n for n, r in results.items() if r.get("kind") == "model" and "validation" in r]
    selected = None
    if candidates:
        primary = cfg["selection"]["primary_metric"]
        tie = cfg["selection"]["tie_break"]
        selected = sorted(candidates, key=lambda n: (-results[n]["validation"][primary], results[n]["validation"][tie]))[0]

    # TEST: tinh MOT LAN cho mo hinh da chon + baseline.
    test_targets = [n for n in baseline_preds] + ([selected] if selected else [])
    for name in test_targets:
        results[name]["test"] = _metrics(test, preds[name]["test"], cfg)
    report["results"] = {name: results[name] for name in results}
    report["selected_model"] = selected
    report["beats_persistence"] = (None if not selected else {
        "validation_accuracy": results[selected]["validation"]["accuracy_at_tol"] > results["persistence"]["validation"]["accuracy_at_tol"],
        "test_accuracy": results[selected]["test"]["accuracy_at_tol"] > results["persistence"]["test"]["accuracy_at_tol"],
        "test_mae": results[selected]["test"]["mae"] < results["persistence"]["test"]["mae"]})
    report["meets_project_target_accuracy_at_20pct"] = (None if not selected else
                                                        bool(results[selected]["test"]["accuracy_at_tol"] >= 0.80))
    report["status"] = "ok" if selected else "baselines_only"
    if report["evaluation_status"] != "primary_eligible":
        report["warning"] = ("dataset/horizon KHONG dat gate primary_eligible - ket qua chi mang tinh tham do (exploratory), "
                             "khong dung lam ket qua cuoi cua luan van.")

    out_dir.mkdir(parents=True, exist_ok=True)
    if selected:
        tol, stable = float(cfg["accuracy_tolerance"]), float(cfg["stable_threshold"])
        eval_frame = test.copy()
        eval_pred = preds[selected]["test"]
        for column in ("inference_mode", "city", "lead_time_bucket"):
            by = metrics_by(eval_frame, eval_pred, column, tol=tol, stable=stable)
            by.to_csv(out_dir / f"h{h}_test_metrics_by_{column}.csv", index=False)
        pred_frame = pd.concat([
            val[[c for c in ID_COLUMNS if c in val.columns]].assign(y_true=val["y_true"].to_numpy(), pred_price=preds[selected]["validation"], model=selected),
            test[[c for c in ID_COLUMNS if c in test.columns]].assign(y_true=test["y_true"].to_numpy(), pred_price=eval_pred, model=selected),
        ], ignore_index=True)
        pred_frame.to_parquet(out_dir / f"h{h}_predictions_{selected}.parquet", index=False)
        bundle = {"model": fitted[selected], "name": selected, "features": features, "target_transform": kind,
                  "encoder_categories": encoder.categories, "horizon": h, "config_sha256": cfg["config_sha256"],
                  "dataset": meta, "seed": seed, "training_version": TRAINING_VERSION}
        joblib.dump(bundle, out_dir / f"h{h}_model_{selected}.joblib")
    return _write(report, out_dir, h, started)


def _write(report: dict[str, Any], out_dir: Path, h: int, started: float) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report["elapsed_seconds"] = round(time.time() - started, 1)
    (out_dir / f"h{h}_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_jsonable), encoding="utf-8")
    return report
