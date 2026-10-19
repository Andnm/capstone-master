"""Hop dong nhan huong gia v1 (LABEL_CONTRACT.md). Nhan la hop dong cua DATASET: o day chi KIEM va ma hoa, khong dinh nghia lai.

- Phep tinh o bien LUON la `(y - c) / c` (cung cong thuc builder `features.py:185-188`), so sanh NGHIEM NGAT voi +-0.02. Khong bao gio dung `y / c - 1`
  (sai so cham dong doi lop o dung +-2%: do 19/10 co 56 hang h1 / 41 hang h3).
- Hang `label_usable_hK == True` ma du lieu khong hop le => FAIL (khong loai im lang). Hang unusable khong vao khung cua horizon do va khong bi ep nhan null.
- Khong bao gio dien nhan thieu bang `stable`.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

LABELS = ("down", "stable", "up")
CLASS_INDEX = {name: i for i, name in enumerate(LABELS)}          # down=0, stable=1, up=2 - co dinh, moi noi assert
THRESHOLD = 0.02
BOUNDARY_ATOL = 1e-9


class LabelContractError(ValueError):
    pass


def pct_change(y: Any, current: Any) -> np.ndarray:
    """Phep tinh duy nhat duoc phep: (y - c) / c."""
    y_arr, c_arr = np.asarray(y, dtype=float), np.asarray(current, dtype=float)
    return (y_arr - c_arr) / c_arr


def direction_from_pct(pct: Any, *, nan_policy: str = "raise") -> np.ndarray:
    """`up` neu pct > +0.02, `down` neu pct < -0.02, con lai `stable`. `nan_policy='raise'` cho nhan/du doan; `'stable'` chi cho momentum (NaN => stable theo dac ta)."""
    arr = np.atleast_1d(np.asarray(pct, dtype=float))
    bad = ~np.isfinite(arr)
    if bad.any():
        if nan_policy == "stable":
            arr = np.where(bad, 0.0, arr)
        else:
            raise LabelContractError(f"{int(bad.sum())} gia tri khong huu han khi tinh huong (nan_policy={nan_policy!r})")
    return np.where(arr > THRESHOLD, "up", np.where(arr < -THRESHOLD, "down", "stable")).astype(object)


def class_index(labels: Any) -> np.ndarray:
    """Nhan chuoi -> chi so lop (int8). Gia tri la/NaN => LabelContractError."""
    values = pd.Series(labels).astype(object)
    mapped = values.map(CLASS_INDEX)
    if mapped.isna().any():
        unknown = sorted({repr(v) for v in values[mapped.isna()].unique()})[:5]
        raise LabelContractError(f"nhan khong thuoc {list(LABELS)}: {unknown}")
    return mapped.to_numpy(dtype=np.int8)


def usable_mask(samples: pd.DataFrame, horizon: int) -> pd.Series:
    """`label_usable_hK` nghiem ngat: chi nhan bool / boolean nullable / 0-1. Chuoi ('False'), so khac 0/1 => LabelContractError (khong ep kieu)."""
    col = f"label_usable_h{horizon}"
    if col not in samples.columns:
        raise LabelContractError(f"thieu cot {col}")
    series = samples[col]
    filled = series.fillna(False)
    ok = filled.map(lambda v: isinstance(v, (bool, np.bool_)) or (isinstance(v, (int, np.integer)) and v in (0, 1)))
    if not bool(ok.all()):
        bad = sorted({repr(v) for v in filled[~ok].unique()})[:5]
        raise LabelContractError(f"{col} co gia tri khong phai bool/0/1: {bad}")
    return filled.astype(bool)


def validate_label_contract(samples: pd.DataFrame, horizon: int) -> dict[str, Any]:
    """Fail-closed tren 100% hang usable (LABEL_CONTRACT §2). Tra bao cao mo ta (so hang, phan bo lop, khoi luong sat bien)."""
    need = [f"y_direction_h{horizon}", f"y_price_h{horizon}", "current_price", "split", f"label_usable_h{horizon}"]
    missing = [c for c in need if c not in samples.columns]
    if missing:
        raise LabelContractError(f"thieu cot {missing}")
    mask = usable_mask(samples, horizon)
    u = samples.loc[mask]
    problems: list[str] = []
    stored = u[f"y_direction_h{horizon}"]
    not_in = ~stored.isin(LABELS)
    if not_in.any():
        problems.append(f"{int(not_in.sum())} hang usable co y_direction ngoai {list(LABELS)} (null/la)")
    y = pd.to_numeric(u[f"y_price_h{horizon}"], errors="coerce").to_numpy(float)
    c = pd.to_numeric(u["current_price"], errors="coerce").to_numpy(float)
    bad_price = ~(np.isfinite(y) & np.isfinite(c) & (y > 0) & (c > 0))
    if bad_price.any():
        problems.append(f"{int(bad_price.sum())} hang usable co y_price/current_price khong huu han hoac <= 0")
    if problems:
        raise LabelContractError("; ".join(problems))
    pct = pct_change(y, c)
    recomputed = direction_from_pct(pct)
    mismatch = recomputed != stored.to_numpy(dtype=object)
    if mismatch.any():
        idx = u.index[mismatch][:5].tolist()
        raise LabelContractError(f"{int(mismatch.sum())}/{len(u)} nhan luu tru khac phep tinh (y-c)/c voi nguong +-{THRESHOLD} (vi du chi so hang {idx})")
    off = np.abs(np.abs(pct) - THRESHOLD)
    naive = direction_from_pct(y / c - 1.0)          # chi de BAO CAO mat do sai so cham dong (khong dung lam nhan)
    per_split = {sp: {k: int(v) for k, v in stored[u["split"] == sp].value_counts().items()} for sp in ("train", "validation", "test")}
    return {"horizon": int(horizon), "n_usable": int(len(u)), "n_total": int(len(samples)), "class_counts_by_split": per_split,
            "boundary_mass": {"exact_atol_1e-9": float((off <= BOUNDARY_ATOL).mean()), "within_0.5pp": float((off <= 0.005).mean()),
                              "rows_exact_atol_1e-9": int((off <= BOUNDARY_ATOL).sum()), "rows_changed_by_naive_ratio_arithmetic": int((naive != recomputed).sum())},
            "label_rule": f"up if (y-c)/c > +{THRESHOLD}; down if < -{THRESHOLD}; else stable (strict); classes {list(LABELS)}"}


def assert_all_classes(index_array: np.ndarray, *, where: str) -> None:
    present = set(np.unique(index_array).tolist())
    if present != {0, 1, 2}:
        raise LabelContractError(f"{where}: thieu lop (co {sorted(present)}, can [0, 1, 2])")
