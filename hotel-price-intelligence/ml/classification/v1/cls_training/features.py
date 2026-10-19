"""Tap feature (base36 / A1 ratio / A2 bo history) va ma tran FOLD-LOCAL cho cay / tuyen tinh (EVAL_PROTOCOL §1, §5).

- `feature_sets`: base36 = cac cot `select_features` (khong identifier/label); A1 = base36 + khoi ratio-1.0 (6 cot nhan qua tu cot co san); A2 = base36 TRU nhom `history` (14) VA `inference_mode` (1) = 21 cot.
- `TreeMatrix`: ma hoa ordinal bang MAPPING CO DINH tu domain protocol (ban copy `TreeEncoder`), thieu giu NaN (HGB/XGBoost xu ly thieu); khong hoc gi tu du lieu.
- `LinearMatrix`: one-hot bang domain co dinh + median-impute + co bao thieu (chi cho cot co NaN o fold-train) + chuan hoa; MOI thong ke chi fit tren fold-train (khong tren toan train truoc CV).
- Boolean nullable (breakfast_included, free_cancellation) khong bao gio thanh True gia: NaN giu NaN (cay) hoac impute + co bao thieu (tuyen tinh).
- Gia tri khong-null ngoai domain cua cot phan loai, hoac vo cuc o cot so => FeatureError (fail-closed, khong tu doi thanh NaN).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .upstream.encoder import TreeEncoder, categorical_in
from .upstream.feature_spec import CATEGORY_DOMAINS
from .upstream.ratio import RATIO_BLOCK_COLUMNS, add_ratio_block

FEATURE_SET_NAMES = ("base36", "a1_ratio", "a2_no_history")
A2_DROPPED_GROUPS = ("history", "mode")


class FeatureError(ValueError):
    pass


def feature_sets(base_features: list[str], dictionary: pd.DataFrame) -> dict[str, list[str]]:
    groups = dict(zip(dictionary["column"], dictionary["group"]))
    unknown = [c for c in base_features if c not in groups]
    if unknown:
        raise FeatureError(f"feature khong co trong data_dictionary: {unknown}")
    a2 = [c for c in base_features if groups[c] not in A2_DROPPED_GROUPS]
    dropped = [c for c in base_features if groups[c] in A2_DROPPED_GROUPS]
    if not dropped or "inference_mode" not in dropped or not any(groups[c] == "history" for c in dropped):
        raise FeatureError("A2 phai bo ca nhom history lan inference_mode")
    return {"base36": list(base_features), "a1_ratio": list(base_features) + list(RATIO_BLOCK_COLUMNS), "a2_no_history": a2}


def prepare_frame(frame: pd.DataFrame, feature_set: str) -> pd.DataFrame:
    """A1 can khoi ratio (tinh tu cot co san, cung hang, causal); cac tap khac giu nguyen."""
    if feature_set not in FEATURE_SET_NAMES:
        raise FeatureError(f"feature_set {feature_set!r} khong hop le")
    return add_ratio_block(frame) if feature_set == "a1_ratio" else frame


def _check_domain(tree: TreeEncoder, frame: pd.DataFrame) -> None:
    unknown = {c: n for c, n in tree.unknown_counts(frame).items() if n}
    if unknown:
        raise FeatureError(f"gia tri phan loai ngoai domain protocol: {unknown}")


class TreeMatrix:
    kind = "tree"

    def __init__(self, columns: list[str]):
        self.columns = list(columns)
        self._tree = TreeEncoder(self.columns)

    def fit(self, frame: pd.DataFrame) -> "TreeMatrix":
        _check_domain(self._tree, frame)
        return self

    @property
    def names(self) -> list[str]:
        return list(self.columns)

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        _check_domain(self._tree, frame)
        x = self._tree.transform(frame).to_numpy(dtype="float64")
        if np.isinf(x).any():
            raise FeatureError("ma tran cay co vo cuc")
        return x


class LinearMatrix:
    kind = "linear"

    def __init__(self, columns: list[str]):
        self.columns = list(columns)
        self._tree = TreeEncoder(self.columns)
        self.cat_cols = categorical_in(self.columns)
        self.num_cols = [c for c in self.columns if c not in self.cat_cols]
        self._fitted = False

    def _numeric(self, frame: pd.DataFrame) -> np.ndarray:
        _check_domain(self._tree, frame)
        coded = self._tree.transform(frame)
        num = coded[self.num_cols].to_numpy(dtype="float64")
        if np.isinf(num).any():
            raise FeatureError("cot so co vo cuc")
        return num

    def fit(self, frame: pd.DataFrame) -> "LinearMatrix":
        num = self._numeric(frame)
        with np.errstate(all="ignore"):
            median = np.nanmedian(num, axis=0)
        all_nan = np.isnan(median)
        self.median_ = np.where(all_nan, 0.0, median)
        has_nan = np.isnan(num).any(axis=0)
        imputed = np.where(np.isnan(num), self.median_, num)
        self.mean_ = imputed.mean(axis=0)
        std = imputed.std(axis=0)
        self.std_ = np.where(std > 0, std, 1.0)
        self.flag_idx_ = np.flatnonzero(has_nan)
        self._fitted = True
        return self

    @property
    def names(self) -> list[str]:
        if not self._fitted:
            raise FeatureError("LinearMatrix chua fit")
        onehot = [f"{c}={v}" for c in self.cat_cols for v in CATEGORY_DOMAINS[c]]
        return [*self.num_cols, *[f"{self.num_cols[i]}__missing" for i in self.flag_idx_], *onehot]

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        if not self._fitted:
            raise FeatureError("LinearMatrix chua fit")
        num = self._numeric(frame)
        flags = np.isnan(num[:, self.flag_idx_]).astype("float64")
        z = (np.where(np.isnan(num), self.median_, num) - self.mean_) / self.std_
        coded = self._tree.transform(frame)
        blocks = [z, flags]
        for c in self.cat_cols:
            codes = coded[c].to_numpy(dtype="float64")
            domain = CATEGORY_DOMAINS[c]
            blocks.append(np.stack([(codes == i).astype("float64") for i in range(len(domain))], axis=1))
        return np.concatenate(blocks, axis=1)


def make_matrix(kind: str, columns: list[str]) -> Any:
    if kind == "tree":
        return TreeMatrix(columns)
    if kind == "linear":
        return LinearMatrix(columns)
    raise FeatureError(f"kind {kind!r} khong hop le")


def dry_run(frames: dict[str, pd.DataFrame], sets: dict[str, list[str]]) -> dict[str, Any]:
    """Dung ma tran tren du lieu THAT, KHONG fit mo hinh va KHONG doc nhan: kiem domain/vo cuc, ti le NaN theo cot, khoi ratio-1.0 (so hang phep tinh tran so), LinearMatrix fit tren train.
    Chi doc cot feature (khong dung `y_*`), nen khong lam lo ket qua cho test; muc dich: bat loi schema TRUOC khi ton thoi gian chay tren Colab."""
    out: dict[str, Any] = {}
    for name, columns in sets.items():
        entry: dict[str, Any] = {"n_columns": len(columns)}
        prepared = {sp: prepare_frame(fr, name) for sp, fr in frames.items()}
        tree = TreeMatrix(columns).fit(prepared["train"])
        linear = LinearMatrix(columns).fit(prepared["train"])
        entry["linear_n_columns"] = len(linear.names)
        entry["splits"] = {}
        for sp, fr in prepared.items():
            xt = tree.transform(fr)
            xl = linear.transform(fr)
            if not np.isfinite(xl).all():
                raise FeatureError(f"{name}/{sp}: ma tran tuyen tinh co NaN/vo cuc")
            nan_cols = {c: int(v) for c, v in zip(columns, np.isnan(xt).sum(axis=0)) if v}
            entry["splits"][sp] = {"rows": int(len(fr)), "tree_nan_columns": nan_cols, "all_nan_columns": [c for c, v in nan_cols.items() if v == len(fr)]}
        if name == "a1_ratio":
            entry["ratio_block_invalid"] = {sp: dict(fr.attrs.get("ratio_block_invalid", {})) for sp, fr in prepared.items()}
        out[name] = entry
    return out
