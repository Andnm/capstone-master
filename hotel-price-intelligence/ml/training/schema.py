"""Chon cot feature tu `data_dictionary.csv` cua dataset (nguon su that), chan feature cam va nhom bi loai (ablation)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from dataset_builder.feature_spec import AUDIT_MATCH_COLUMNS, FORBIDDEN_FEATURES

NON_FEATURE_GROUPS = ("identifier", "label")
CATEGORICAL_COLUMNS = ("city", "lead_time_bucket", "inference_mode")


class SchemaError(ValueError):
    pass


def read_dictionary(dataset_dir: Path | str) -> pd.DataFrame:
    path = Path(dataset_dir) / "data_dictionary.csv"
    if not path.exists():
        raise SchemaError(f"khong tim thay {path}")
    dictionary = pd.read_csv(path)
    needed = {"column", "group"}
    if not needed.issubset(dictionary.columns):
        raise SchemaError(f"data_dictionary.csv thieu cot {needed - set(dictionary.columns)}")
    return dictionary


def select_features(dictionary: pd.DataFrame, *, exclude_groups=(), exclude_columns=()) -> list[str]:
    """Feature = moi cot khong thuoc identifier/label; loai nhom/cot do cau hinh chi dinh. Co feature bi cam -> raise (khong am tham bo)."""
    candidates = dictionary.loc[~dictionary["group"].isin(NON_FEATURE_GROUPS), "column"].tolist()
    banned = sorted(set(candidates) & (set(FORBIDDEN_FEATURES) | set(AUDIT_MATCH_COLUMNS)))      # audit strata nam trong Parquet nhung KHONG duoc la feature
    if banned:
        raise SchemaError(f"dataset co feature bi cam theo hop dong v1: {banned}")
    drop_groups = set(exclude_groups)
    dropped = set(dictionary.loc[dictionary["group"].isin(drop_groups), "column"]) | set(exclude_columns)
    features = [c for c in candidates if c not in dropped]
    if not features:
        raise SchemaError("khong con feature nao sau khi loai nhom/cot")
    return features
