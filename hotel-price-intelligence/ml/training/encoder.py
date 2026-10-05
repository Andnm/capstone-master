"""Chuan bi ma tran feature. `to_raw` giu nhan chuoi cho cot phan loai (Ridge tu one-hot); `TreeEncoder` ma hoa nhan thanh ma so,
CHI hoc danh muc tu tap train (nhan chua thay -> NaN), bool -> 0/1, thieu giu NaN (RF sklearn >= 1.4 va XGBoost xu ly thieu)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import CATEGORICAL_COLUMNS


def _to_float(series: pd.Series) -> np.ndarray:
    return pd.to_numeric(series, errors="coerce").astype("Float64").to_numpy(dtype="float64", na_value=np.nan)


def categorical_in(features: list[str]) -> list[str]:
    return [c for c in CATEGORICAL_COLUMNS if c in features]


def to_raw(frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    cats = set(categorical_in(features))
    out = {}
    for column in features:
        if column in cats:
            out[column] = frame[column].astype("string").astype(object).where(frame[column].notna(), np.nan)
        else:
            out[column] = _to_float(frame[column])
    return pd.DataFrame(out, index=frame.index)


class TreeEncoder:
    def __init__(self, features: list[str]):
        self.features = list(features)
        self.categories: dict[str, list[str]] = {}

    def fit(self, frame: pd.DataFrame) -> "TreeEncoder":
        self.categories = {c: sorted(frame[c].dropna().astype(str).unique().tolist()) for c in categorical_in(self.features)}
        return self

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        out = {}
        for column in self.features:
            if column in self.categories:
                mapping = {value: float(i) for i, value in enumerate(self.categories[column])}
                out[column] = frame[column].astype("string").map(mapping).astype("Float64").to_numpy(dtype="float64", na_value=np.nan)
            else:
                out[column] = _to_float(frame[column])
        return pd.DataFrame(out, index=frame.index)
