"""Metric danh gia hoi quy (CLAUDE.md muc 9): Accuracy@tol, MAE, RMSE, MAPE/sMAPE/median APE, R2, bias va do chinh xac huong.

Huong suy ra tu hoi quy: pct = (gia du bao hoac gia that - gia hien tai) / gia hien tai; 'up' > +stable, 'down' < -stable, con lai 'stable'.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _direction(pct: np.ndarray, stable: float) -> np.ndarray:
    return np.where(pct > stable, 2, np.where(pct < -stable, 0, 1))


def regression_metrics(y_true, y_pred, current_price, *, tol: float = 0.20, stable: float = 0.02) -> dict[str, Any]:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    cur = np.asarray(current_price, dtype=float)
    n = int(len(y))
    if n == 0:
        return {"n": 0}
    err = p - y
    ape = np.abs(err) / y
    denom = (np.abs(y) + np.abs(p)) / 2
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return {
        "n": n,
        "accuracy_at_tol": float(np.mean(ape <= tol)),
        "tol": tol,
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "mape": float(np.mean(ape)),
        "median_ape": float(np.median(ape)),
        "smape": float(np.mean(np.abs(err) / denom)),
        "r2": float(1 - np.sum(err ** 2) / ss_tot) if ss_tot > 0 else None,
        "bias": float(np.mean(err)),
        "directional_accuracy": float(np.mean(_direction((p - cur) / cur, stable) == _direction((y - cur) / cur, stable))),
        "stable_threshold": stable,
    }


def metrics_by(frame: pd.DataFrame, y_pred, group_col: str, *, tol: float, stable: float) -> pd.DataFrame:
    """`frame` phai co y_true, current_price, group_col; tra bang metric theo tung nhom (nhom rong -> bo)."""
    rows = []
    pred = pd.Series(np.asarray(y_pred, dtype=float), index=frame.index)
    for key, sub in frame.groupby(group_col, dropna=False, observed=True):
        m = regression_metrics(sub["y_true"], pred.loc[sub.index], sub["current_price"], tol=tol, stable=stable)
        rows.append({group_col: key, **m})
    return pd.DataFrame(rows)
