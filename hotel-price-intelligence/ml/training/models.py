"""Nha may uoc luong. RF/XGBoost nhan ma tran tu `TreeEncoder`; Ridge nhan `to_raw` (tu one-hot + impute + chuan hoa).
XGBoost va SHAP la TUY CHON (import muon) - thieu thu vien thi bo qua mo hinh do voi ly do ro rang, khong lam hong phan con lai."""
from __future__ import annotations

import importlib.util
from typing import Any

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from .encoder import categorical_in


def xgboost_available() -> bool:
    return importlib.util.find_spec("xgboost") is not None


def shap_available() -> bool:
    return importlib.util.find_spec("shap") is not None


def make_ridge(alpha: float, features: list[str]) -> Pipeline:
    cats = categorical_in(features)
    nums = [c for c in features if c not in cats]
    pre = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore"), cats),
        ("num", Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)), ("scale", StandardScaler())]), nums),
    ])
    return Pipeline([("pre", pre), ("ridge", Ridge(alpha=alpha))])


def make_tree_model(name: str, cfg: dict[str, Any], seed: int, params: dict[str, Any] | None = None):
    spec = cfg["models"][name]
    kwargs = {**(spec.get("fixed") or {}), **(params or {})}
    if name == "rf":
        return RandomForestRegressor(random_state=seed, **kwargs)
    if name == "xgb":
        from xgboost import XGBRegressor  # noqa: PLC0415 - import muon co chu dich
        return XGBRegressor(random_state=seed, **kwargs)
    raise ValueError(f"mo hinh cay khong ho tro: {name!r}")
