"""Bien doi muc tieu <-> gia (VND). Metric luon tinh tren thang gia."""
from __future__ import annotations

import numpy as np


def to_target(y_price, current_price, kind: str) -> np.ndarray:
    y = np.asarray(y_price, dtype=float)
    cur = np.asarray(current_price, dtype=float)
    if kind == "log_ratio":
        return np.log(y / cur)
    if kind == "log_price":
        return np.log(y)
    if kind == "price":
        return y
    raise ValueError(f"target_transform khong hop le: {kind!r}")


def to_price(pred, current_price, kind: str) -> np.ndarray:
    p = np.asarray(pred, dtype=float)
    cur = np.asarray(current_price, dtype=float)
    if kind == "log_ratio":
        return cur * np.exp(p)
    if kind == "log_price":
        return np.exp(p)
    if kind == "price":
        return p
    raise ValueError(f"target_transform khong hop le: {kind!r}")
