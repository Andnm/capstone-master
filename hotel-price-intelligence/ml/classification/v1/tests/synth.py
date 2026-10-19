"""Du lieu TONG HOP cho test ma huan luyen (khong dung dataset that). Du lieu nam o `cls_training.synthetic` (dung chung voi smoke tren Colab); o day chi con cau hinh nho cho test."""
from __future__ import annotations

import copy

from cls_training.candidates import candidate_set_sha256
from cls_training.config import load_config
from cls_training.synthetic import BUCKETS, CITIES, SYNTH_COLUMNS, SYNTH_GROUPS, make_frame, make_loaded, synth_dictionary  # noqa: F401  (re-export cho cac test)


def small_config(device: str = "cpu", *, fast: bool = True) -> dict:
    """Cau hinh that, chi doi thiet bi va (tuy chon) lam nhe cac ho cay de test nhanh. Van tay tap ung vien duoc tinh lai cho khong gian nho hon."""
    cfg = copy.deepcopy(load_config())
    cfg["run"]["device"] = device
    if fast:
        cfg["tuning"]["families"]["hgb"]["space"]["max_iter"] = [20, 30]
        cfg["tuning"]["families"]["hgb"]["space"]["max_depth"] = [2, 3]
        cfg["tuning"]["families"]["xgb"]["space"]["n_estimators"] = [10, 20]
    cfg["tuning"]["candidate_set_sha256"] = candidate_set_sha256(cfg)
    return cfg


def runner_config(device: str = "cpu") -> dict:
    cfg = small_config(device)
    cfg["cv"] = {"min_train_days": 8, "n_splits": {"1": 3, "3": 2}}
    cfg["ablation"] = {**cfg["ablation"], "a2_n_features": 6}
    return cfg
