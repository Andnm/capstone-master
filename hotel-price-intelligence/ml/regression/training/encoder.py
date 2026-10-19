"""Chuan bi ma tran feature. `to_raw` giu nhan chuoi cho cot phan loai (Ridge tu one-hot); `TreeEncoder` ma hoa nhan thanh ma so bang MAPPING CO DINH tu domain
protocol (`feature_spec.CATEGORY_DOMAINS`: 5 thanh pho, 6 bucket lead time, 2 che do suy luan) - KHONG hoc danh muc tu du lieu (GPT file 52 muc 4: hoc tu train/fold la hoc
covariate tuong lai cua CV va co the doi ma hoa phan train). Gia tri ngoai domain -> NaN (ghi dem rieng), bool -> 0/1, thieu giu NaN (RF sklearn >= 1.4 va XGBoost xu ly thieu).
Cac buoc tien xu ly CO thong ke (imputer/scaler/OHE hoc danh muc, vd pipeline Ridge) van PHAI fit rieng tung fold neu duoc dua vao tim kiem."""
from __future__ import annotations

import numpy as np
import pandas as pd

from dataset_builder.feature_spec import CATEGORY_DOMAINS

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
