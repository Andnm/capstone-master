"""Dieu phoi huan luyen/danh gia mot horizon: baseline -> (tune) RF/XGBoost/Ridge -> chon theo VALIDATION -> test mot lan -> luu.

Quy tac chong ro ri/ban cao (CLAUDE.md muc 6.3, 10): chi dung mau `label_usable_h{k}` va co gia muc tieu; encoder/danh muc/trung vi chi hoc tu train;
CV theo thoi gian co purge tren train; chon mo hinh bang validation; **TEST CHI DUOC CHAM SAU KHI CHON** - ma tran test chi duoc ma hoa va
`predict(test)` chi duoc goi cho mo hinh DA CHON (dung mot lan) + baseline khong hoc; seed co dinh tu config.

GPT review vong 1: TR-M1 (khong predict test truoc khi chon), TR-M2 (xac minh file dataset that), TR-M3 (tap primary theo horizon + bao cao ca hai mau so),
TR-M4 (provenance code) va TR-m2 (bang moi truong thuc te) duoc xu ly o day va `provenance.py`.
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
from .provenance import DatasetVerificationError, code_provenance, environment_manifest, verify_dataset, verify_frame
from .schema import read_dictionary, select_features
from .target import to_price, to_target
from .tuning import tune

SPLITS = ("train", "validation", "test")
ID_COLUMNS = ["hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date", "split", "inference_mode", "city",
              "lead_time_bucket", "current_price"]


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


def horizon_frames(samples: pd.DataFrame, h: int, cfg: dict[str, Any]) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Mau co nhan dung duoc + gia muc tieu hop le. Val/test 'primary' = chi hotel co mau train DUNG DUOC O CHINH HORIZON NAY - tap hotel nay duoc suy
    truc tiep tu train cua horizon, KHONG tin co chung. Neu Parquet co `hotel_seen_in_train_h{h}` thi phai khop (builder va training dung cung dinh nghia).
    Tra (frames, info) - info ghi ca mau so all-hotel va primary de Wave B/bao cao dung chung denominator."""
    usable = samples[samples[f"label_usable_h{h}"].fillna(False).astype(bool) & samples[f"y_price_h{h}"].notna()
                     & (samples["current_price"] > 0) & (samples[f"y_price_h{h}"] > 0)].copy()
    usable["y_true"] = usable[f"y_price_h{h}"].astype(float)
    all_frames = {name: usable[usable["split"] == name].copy() for name in SPLITS}
    train_hotels = set(all_frames["train"]["hotel_id"])
    flag = f"hotel_seen_in_train_h{h}"
    if flag in samples.columns:
        expected = samples["hotel_id"].isin(set(samples.loc[samples[f"label_usable_h{h}"].fillna(False).astype(bool) & (samples["split"] == "train"), "hotel_id"]))
        if not (samples[flag].astype(bool) == expected).all():
            raise DatasetVerificationError(f"{flag} trong Parquet khong khop dinh nghia 'hotel co mau train label_usable_h{h}'.")
    primary_only = bool(cfg.get("primary_requires_seen_hotel", True))
    frames = dict(all_frames)
    if primary_only:
        for name in ("validation", "test"):
            frames[name] = all_frames[name][all_frames[name]["hotel_id"].isin(train_hotels)].copy()
    info = {
        "primary_requires_seen_hotel": primary_only, "train_hotels": len(train_hotels),
        "rows_all_hotels": {name: int(len(all_frames[name])) for name in SPLITS},
        "rows_primary": {name: int(len(frames[name])) for name in SPLITS},
        "excluded_unseen_hotel_rows": {name: int(len(all_frames[name]) - len(frames[name])) for name in ("validation", "test")},
        "excluded_unseen_hotels": {name: int(all_frames[name]["hotel_id"].nunique() - frames[name]["hotel_id"].nunique()) for name in ("validation", "test")},
        "rule": "validation/test chi giu mau cua hotel co >= 1 mau train label_usable cung horizon (suy tu train cua horizon nay)",
    }
    return frames, info


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


def build_context(dataset_dir: Path | str, out_dir: Path | str | None = None, *, colab_manifest: Path | str | None = None) -> dict[str, Any]:
    """Xac minh dataset + provenance code + bang moi truong MOT lan cho ca run (CLI dung truc tiep; run_horizon tu goi neu khong duoc truyen)."""
    meta = verify_dataset(dataset_dir)
    env_path = Path(out_dir) / "environment_resolved.txt" if out_dir is not None else None
    if env_path is not None:
        env_path.parent.mkdir(parents=True, exist_ok=True)
    colab: dict[str, Any] | None = None
    if colab_manifest is not None:
        from .provenance import file_sha256
        colab = {"path": str(colab_manifest), "sha256": file_sha256(colab_manifest),
                 "content": json.loads(Path(colab_manifest).read_text(encoding="utf-8"))}
    return {"dataset_meta": meta, "provenance": code_provenance(), "environment": environment_manifest(env_path), "colab_manifest": colab}


def _dataset_summary(meta: dict[str, Any], dataset_dir: Path, h: int) -> dict[str, Any]:
    summary = {k: meta[k] for k in ("dataset_dir", "dataset_name", "samples_file_sha256", "samples_content_sha256", "declared_rows", "verified_file_sha256")}
    suff = json.loads((dataset_dir / "sufficiency_report.json").read_text(encoding="utf-8")).get("horizons", {}).get(f"h{h}", {})
    summary["sufficiency_status"] = suff.get("status")
    summary["sufficiency_failed_gates"] = suff.get("failed_gates")
    return summary


def run_horizon(dataset_dir: Path | str, h: int, cfg: dict[str, Any], out_dir: Path | str, models: list[str], *,
                context: dict[str, Any] | None = None) -> dict[str, Any]:
    dataset_dir, out_dir = Path(dataset_dir), Path(out_dir)
    started = time.time()
    seed = int(cfg["seed"])
    kind = cfg["target_transform"]
    context = context or build_context(dataset_dir, out_dir)
    samples = pd.read_parquet(dataset_dir / "samples.parquet")
    verify_frame(samples, context["dataset_meta"])
    dictionary = read_dictionary(dataset_dir)
    features = select_features(dictionary, exclude_groups=cfg.get("exclude_groups") or (), exclude_columns=cfg.get("exclude_columns") or ())
    frames, info = horizon_frames(samples, h, cfg)
    dataset = _dataset_summary(context["dataset_meta"], dataset_dir, h)
    report: dict[str, Any] = {
        "training_version": TRAINING_VERSION, "config_version": cfg["version"], "config_sha256": cfg["config_sha256"], "horizon": h,
        "seed": seed, "target_transform": kind, "features": features, "dataset": dataset,
        "rows": info["rows_primary"], "rows_all_hotels": info["rows_all_hotels"], "primary_selection": info,
        "evaluation_status": dataset.get("sufficiency_status") or "unknown",
        "provenance": context["provenance"], "environment": context["environment"], "colab_manifest": context["colab_manifest"],
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
    X_train_tree, X_val_tree = encoder.transform(train), encoder.transform(val)
    X_train_raw, X_val_raw = to_raw(train, features), to_raw(val, features)

    results: dict[str, dict[str, Any]] = {}
    fitted: dict[str, Any] = {}
    val_preds: dict[str, np.ndarray] = {}

    # ---- BUOC 1: fit/tune + predict VALIDATION cho moi ung vien. TUYET DOI khong dung test o buoc nay.
    ratio_median = _ratio_median_baseline(train)
    baseline_fns = {
        "persistence": lambda f: f["current_price"].to_numpy(dtype=float),
        "ratio_median_by_city_leadtime": ratio_median,
    }
    for name, fn in baseline_fns.items():
        val_preds[name] = fn(val)
        results[name] = {"kind": "baseline", "validation": _metrics(val, val_preds[name], cfg)}

    wanted = set(models)
    if "ridge" in wanted:
        ridge = make_ridge(float(cfg["models"]["ridge"]["alpha"]), features).fit(X_train_raw, y_train)
        fitted["ridge"] = ridge
        val_preds["ridge"] = to_price(ridge.predict(X_val_raw), val["current_price"], kind)
        results["ridge"] = {"kind": "model", "params": {"alpha": float(cfg["models"]["ridge"]["alpha"])},
                            "validation": _metrics(val, val_preds["ridge"], cfg)}

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
        val_preds[name] = to_price(model.predict(X_val_tree), val["current_price"], kind)
        results[name] = {"kind": "model", "params": tuning["best_params"], "tuning": tuning,
                         "validation": _metrics(val, val_preds[name], cfg)}

    # ---- BUOC 2: chon theo VALIDATION (Accuracy@tol cao hon, hoa -> MAE thap hon). Chi xet mo hinh hoc duoc, khong xet baseline.
    candidates = [n for n, r in results.items() if r.get("kind") == "model" and "validation" in r]
    selected = None
    if candidates:
        primary = cfg["selection"]["primary_metric"]
        tie = cfg["selection"]["tie_break"]
        selected = sorted(candidates, key=lambda n: (-results[n]["validation"][primary], results[n]["validation"][tie]))[0]

    # ---- BUOC 3: TEST — tu day moi dung test. Ma hoa test + predict(test) CHI cho mo hinh DA CHON (mot lan) va baseline khong hoc.
    test_preds: dict[str, np.ndarray] = {name: fn(test) for name, fn in baseline_fns.items()}
    if selected:
        if selected == "ridge":
            test_preds[selected] = to_price(fitted[selected].predict(to_raw(test, features)), test["current_price"], kind)
        else:
            test_preds[selected] = to_price(fitted[selected].predict(encoder.transform(test)), test["current_price"], kind)
    for name, pred in test_preds.items():
        results[name]["test"] = _metrics(test, pred, cfg)
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
        for column in ("inference_mode", "city", "lead_time_bucket"):
            by = metrics_by(test, test_preds[selected], column, tol=tol, stable=stable)
            by.to_csv(out_dir / f"h{h}_test_metrics_by_{column}.csv", index=False)
        pred_frame = pd.concat([
            val[[c for c in ID_COLUMNS if c in val.columns]].assign(y_true=val["y_true"].to_numpy(), pred_price=val_preds[selected], model=selected),
            test[[c for c in ID_COLUMNS if c in test.columns]].assign(y_true=test["y_true"].to_numpy(), pred_price=test_preds[selected], model=selected),
        ], ignore_index=True)
        pred_frame.to_parquet(out_dir / f"h{h}_predictions_{selected}.parquet", index=False)
        bundle = {"model": fitted[selected], "name": selected, "features": features, "target_transform": kind,
                  "encoder_categories": encoder.categories, "horizon": h, "config_sha256": cfg["config_sha256"],
                  "dataset": dataset, "seed": seed, "training_version": TRAINING_VERSION, "provenance": context["provenance"],
                  "environment_sha256": context["environment"]["environment_sha256"]}
        joblib.dump(bundle, out_dir / f"h{h}_model_{selected}.joblib")
    return _write(report, out_dir, h, started)


def _write(report: dict[str, Any], out_dir: Path, h: int, started: float) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report["elapsed_seconds"] = round(time.time() - started, 1)
    (out_dir / f"h{h}_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_jsonable), encoding="utf-8")
    return report
