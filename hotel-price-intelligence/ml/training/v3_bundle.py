"""Tai lap du doan tu bundle v3 (GPT file 16 MIN4): dung dung ma tran dau vao (raw-tree / tree_block / Ridge-raw) tu thong tin trong bundle roi predict; routing theo chinh sach da khoa.

KHONG phai serving app/loader production (ngoai pham vi) - chi la duong replay de test chung minh bundle du de tai lap `h{k}_predictions_v3.parquet`.
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from .encoder import TreeEncoder, to_raw
from .v3_contract import add_ratio_block, price_from_z


def encode_for_bundle(bundle: Mapping[str, Any], frame: pd.DataFrame):
    """Dung ma tran dau vao DUNG thu tu cot hieu dung cua estimator (kiem `effective_features`, `n_features_in`, domain danh muc)."""
    matrix = bundle["matrix"]
    features = list(bundle["features"])
    if matrix == "raw":
        X = to_raw(frame, features)
        width = X.shape[1]
        effective = features
    else:
        if matrix == "tree_block":
            frame = add_ratio_block(frame)
            effective = features + list(bundle["ratio_block"]["columns"])
        elif matrix == "tree":
            effective = features
        else:
            raise ValueError(f"matrix khong biet: {matrix!r}")
        encoder = TreeEncoder(effective)
        if encoder.categories != bundle["encoder_categories"]:
            raise ValueError("domain danh muc trong bundle khac encoder hien tai (CATEGORY_DOMAINS doi?)")
        X = encoder.transform(frame).to_numpy(float)
        width = X.shape[1]
    if list(bundle["effective_features"]) != effective or width != len(effective):
        raise ValueError("bundle: effective_features khong khop cot thuc te cua dau vao")
    if bundle.get("n_features_in") is not None and int(bundle["n_features_in"]) != width:
        raise ValueError(f"bundle: n_features_in={bundle['n_features_in']} != so cot dau vao {width}")
    return X


def predict_z(bundle: Mapping[str, Any], frame: pd.DataFrame) -> np.ndarray:
    z = np.asarray(bundle["model"].predict(encode_for_bundle(bundle, frame)), float)
    price_from_z(frame["current_price"].to_numpy(float), z, label=f"bundle {bundle['name']}")
    return z


def routed_z(bundle: Mapping[str, Any], frame: pd.DataFrame, z: np.ndarray) -> np.ndarray:
    """z da route: mode co chinh sach `use_model` = True dung z, nguoc lai (ke ca mode la/khong cau hinh) persistence (0)."""
    policy = bundle.get("routing_policy") or {}
    modes = frame["inference_mode"].astype(str).to_numpy()
    use = np.array([bool(policy.get(m, {"use_model": False}).get("use_model", False)) for m in modes])
    return np.where(use, z, 0.0)
