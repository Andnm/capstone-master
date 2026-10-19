# BAN COPY NGUYEN VAN (khong sua tay) tu `ml/regression/training/encoder.py`.
# SHA-256 (chuan hoa LF) cua nguon luc copy: 8b1c37890b217633ba2082289df07fea9cea0cbf8c68c302e23c05d6f3aebfcf
# Sinh/kiem bang `cls_training.upstream_sync` (UPSTREAM_MANIFEST.json). Sua nguon => `verify()` bao drift; muon cap nhat phai sinh lai co chu y.
# Pham vi: _to_float, categorical_in, to_raw, TreeEncoder.
from __future__ import annotations

import numpy as np
import pandas as pd

from .feature_spec import CATEGORY_DOMAINS
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
        self.categories: dict[str, list[str]] = {c: list(CATEGORY_DOMAINS[c]) for c in categorical_in(self.features)}   # co dinh, khong phu thuoc du lieu

    def fit(self, frame: pd.DataFrame | None = None) -> "TreeEncoder":
        """Khong hoc gi tu du lieu (giu chu ky de runner/tuning goi nhu truoc); mapping da co dinh o `__init__`."""
        return self

    def unknown_counts(self, frame: pd.DataFrame) -> dict[str, int]:
        """So gia tri khac null NAM NGOAI domain (se thanh NaN) - chi de bao cao, khong tham gia ma hoa."""
        return {c: int((frame[c].notna() & ~frame[c].astype(str).isin(domain)).sum()) for c, domain in self.categories.items() if c in frame.columns}

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        out = {}
        for column in self.features:
            if column in self.categories:
                mapping = {value: float(i) for i, value in enumerate(self.categories[column])}
                out[column] = frame[column].astype("string").map(mapping).astype("Float64").to_numpy(dtype="float64", na_value=np.nan)
            else:
                out[column] = _to_float(frame[column])
        return pd.DataFrame(out, index=frame.index)
