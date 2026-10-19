"""CV purged cho candidate v3 (C4): cung mot lan fit/du doan cho CA HAI scorer (A: MAE VND that, B: MAE log); diem pooled = tong sai so / tong hang validation cua moi fold.

Khong dung validation/test. Moi candidate duoc cham tren cung hang fold, so voi persistence tren DUNG cac hang do (thang lift) => dung chung `tie_atol`.
Loi (ngoai le, du doan khong huu han, GIA khong huu han/khong duong sau nghich dao) => candidate `failed` + ly do (diem None), KHONG am tham thay bang 0.
Moi fold con do diem IN-SAMPLE tren fold-TRAIN bang CHINH estimator vua fit (khong them fit) - nhan `in_sample_fold_train` (C13'), chi de chan doan overfit, khong dung chon.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from .cv import PurgedExpandingWindowSplit
from .v3_contract import price_from_z


def make_folds(train: pd.DataFrame, horizon: int, cfg: dict[str, Any]) -> list[tuple[np.ndarray, np.ndarray]]:
    n_splits = int(cfg["cv"]["n_splits"][str(horizon)])
    splitter = PurgedExpandingWindowSplit(n_splits, gap_days=int(horizon), min_train_days=int(cfg["cv"]["min_train_days"]))
    return list(splitter.split(train["vn_observation_date"]))


def describe_folds(train: pd.DataFrame, folds: Sequence[tuple[np.ndarray, np.ndarray]]) -> list[dict[str, Any]]:
    y, cur = train["y_true"].to_numpy(float), train["current_price"].to_numpy(float)
    z = np.log(y / cur)
    out = []
    for k, (tr, va) in enumerate(folds):
        dates = train["vn_observation_date"].astype(str).to_numpy()[va]
        out.append({"fold": k, "n_train": int(len(tr)), "n_val": int(len(va)), "val_days": int(len(set(dates))), "val_start": str(dates.min()), "val_end": str(dates.max()),
                    "persistence_mae_vnd": float(np.abs(cur[va] - y[va]).mean()), "persistence_mae_log": float(np.abs(z[va]).mean())})
    return out


def pooled_lifts(y: np.ndarray, cur: np.ndarray, zhat: np.ndarray) -> dict[str, float | None]:
    price = price_from_z(cur, zhat, label="pooled_lifts")
    ep = np.abs(cur - y).sum()
    z = np.log(y / cur)
    el = np.abs(z).sum()
    return {"lift_vnd": float(1 - np.abs(price - y).sum() / ep) if ep > 0 else None,
            "lift_log": float(1 - np.abs(z - zhat).sum() / el) if el > 0 else None}


def _fold_scores(y: np.ndarray, cur: np.ndarray, z: np.ndarray, zhat: np.ndarray, *, label: str) -> dict[str, Any]:
    price = price_from_z(cur, zhat, label=label)
    lifts = pooled_lifts(y, cur, zhat)
    return {"n": int(len(y)), "mae_vnd": float(np.abs(price - y).mean()), "mae_log": float(np.abs(z - zhat).mean()),
            "persistence_mae_vnd": float(np.abs(cur - y).mean()), "persistence_mae_log": float(np.abs(z).mean()),
            "lift_vnd": lifts["lift_vnd"], "lift_log": lifts["lift_log"]}


def cv_candidate(build: Callable[[], Any], X: np.ndarray, z: np.ndarray, cur: np.ndarray, y: np.ndarray,
                 folds: Sequence[tuple[np.ndarray, np.ndarray]]) -> dict[str, Any]:
    """Fit/du doan tung fold. Tra {status, reason, per_fold[...], pooled{lift_vnd,lift_log,mae_vnd,mae_log}, seconds}. per_fold[k] co diem fold-VAL (`mae_vnd`, `mae_log`, `lift_*`)
    va `train` = diem in-sample cua fold-TRAIN (cung estimator, khong them fit). Gap giua fold (dong ho) do ledger cua runner ghi."""
    started = time.time()
    per_fold: list[dict[str, Any]] = []
    all_y: list[np.ndarray] = []
    all_cur: list[np.ndarray] = []
    all_z: list[np.ndarray] = []
    for k, (tr, va) in enumerate(folds):
        try:
            model = build()
            model.fit(X[tr], z[tr])
            zhat = np.asarray(model.predict(X[va]), float)
            zhat_train = np.asarray(model.predict(X[tr]), float)
            val_scores = _fold_scores(y[va], cur[va], z[va], zhat, label=f"cv fold {k} validation")      # InvalidPredictionError neu z/gia khong hop le
            train_scores = _fold_scores(y[tr], cur[tr], z[tr], zhat_train, label=f"cv fold {k} train")
        except Exception as exc:  # noqa: BLE001 - ghi nhan loi cua candidate (ke ca du doan khong hop le), khong dung ca run
            return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:500], "per_fold": per_fold, "pooled": None, "seconds": time.time() - started, "failed_fold": k}
        per_fold.append({"fold": k, "n_val": val_scores["n"], "mae_vnd": val_scores["mae_vnd"], "mae_log": val_scores["mae_log"], "lift_vnd": val_scores["lift_vnd"],
                         "lift_log": val_scores["lift_log"], "train": {"label": "in_sample_fold_train", **train_scores}})
        all_y.append(y[va])
        all_cur.append(cur[va])
        all_z.append(zhat)
    yy, cc, zz = np.concatenate(all_y), np.concatenate(all_cur), np.concatenate(all_z)
    pooled = pooled_lifts(yy, cc, zz)
    pooled.update(mae_vnd=float(np.abs(price_from_z(cc, zz, label="cv pooled") - yy).mean()), mae_log=float(np.abs(np.log(yy / cc) - zz).mean()), n_val=int(len(yy)))
    return {"status": "ok", "reason": None, "per_fold": per_fold, "pooled": pooled, "seconds": time.time() - started}
