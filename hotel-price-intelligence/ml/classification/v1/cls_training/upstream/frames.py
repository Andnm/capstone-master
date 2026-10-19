# BAN COPY NGUYEN VAN (khong sua tay) tu `ml/regression/training/runner.py`.
# SHA-256 (chuan hoa LF) cua nguon luc copy: 6017447147f58e7ead64d228422e16f2eb09270f4b3a91c88ef451bd0d0d981c
# Sinh/kiem bang `cls_training.upstream_sync` (UPSTREAM_MANIFEST.json). Sua nguon => `verify()` bao drift; muon cap nhat phai sinh lai co chu y.
# Pham vi: SPLITS, horizon_frames.
from __future__ import annotations

from typing import Any

import pandas as pd

from .provenance import DatasetVerificationError


SPLITS = ("train", "validation", "test")


def horizon_frames(samples: pd.DataFrame, h: int, cfg: dict[str, Any]) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Mau co nhan dung duoc + gia muc tieu hop le. Val/test 'primary' = chi hotel co mau train DUNG DUOC O CHINH HORIZON NAY - tap hotel nay duoc suy
    truc tiep tu train cua horizon, KHONG tin co chung. Neu Parquet co `hotel_seen_in_train_h{h}` thi phai khop (builder va training dung cung dinh nghia).
    Tra (frames, info) - info ghi ca mau so all-hotel va primary de Wave B/bao cao dung chung denominator."""
    usable = samples[samples[f"label_usable_h{h}"].fillna(False).astype(bool) & samples[f"y_price_h{h}"].notna()
                     & (samples["current_price"] > 0) & (samples[f"y_price_h{h}"] > 0)].copy()
    usable["y_true"] = usable[f"y_price_h{h}"].astype(float)
    all_frames = {name: usable[usable["split"] == name].copy() for name in SPLITS}
    train_hotels = set(all_frames["train"]["hotel_id"])
    flag = f"hotel_seen_in_train_h{h}"
    if flag in samples.columns:
        expected = samples["hotel_id"].isin(set(samples.loc[samples[f"label_usable_h{h}"].fillna(False).astype(bool) & (samples["split"] == "train"), "hotel_id"]))
        if not (samples[flag].astype(bool) == expected).all():
            raise DatasetVerificationError(f"{flag} trong Parquet khong khop dinh nghia 'hotel co mau train label_usable_h{h}'.")
    primary_only = bool(cfg.get("primary_requires_seen_hotel", True))
    frames = dict(all_frames)
    if primary_only:
        for name in ("validation", "test"):
            frames[name] = all_frames[name][all_frames[name]["hotel_id"].isin(train_hotels)].copy()
    strata_all: dict[str, Any] | None = None
    if "prediction_match_status" in samples.columns and f"label_match_status_h{h}" in samples.columns:
        strata_all = {name: {"n_all_hotels": int(len(all_frames[name])),
                             "n_exact_only": int(((all_frames[name]["prediction_match_status"] == "exact") & (all_frames[name][f"label_match_status_h{h}"] == "exact")).sum()),
                             "n_any_alias": int(((all_frames[name]["prediction_match_status"] == "alias") | (all_frames[name][f"label_match_status_h{h}"] == "alias")).sum())}
                      for name in SPLITS}
    info = {
        "strata_all_hotels": strata_all,
        "primary_requires_seen_hotel": primary_only, "train_hotels": len(train_hotels),
        "rows_all_hotels": {name: int(len(all_frames[name])) for name in SPLITS},
        "rows_primary": {name: int(len(frames[name])) for name in SPLITS},
        "excluded_unseen_hotel_rows": {name: int(len(all_frames[name]) - len(frames[name])) for name in ("validation", "test")},
        "excluded_unseen_hotels": {name: int(all_frames[name]["hotel_id"].nunique() - frames[name]["hotel_id"].nunique()) for name in ("validation", "test")},
        "rule": "validation/test chi giu mau cua hotel co >= 1 mau train label_usable cung horizon (suy tu train cua horizon nay)",
    }
    return frames, info
