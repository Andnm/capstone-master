"""Gate du lieu da DANG KY (discuss/canonical-key-duplicates/05 muc 1.2) - MOT dinh nghia dung chung cho (a) chon bien split (`splitter`) va (b) bao cao
sufficiency sau export (`reports`), de hai noi khong the lech nhau (GPT review vong 1 DB-M1/M2/M3).

Dinh nghia (theo TUNG horizon k, tren frame co cot `split`, `label_usable_h{k}`, `hotel_id`, `city`, `vn_observation_date`):
- `train_hotels_k` = khach san co >= 1 mau train `label_usable_hk` (tap train CUA horizon do).
- Validation/test **primary** = mau `label_usable_hk` cua split do ma hotel thuoc `train_hotels_k`. MOI chi so cua cong primary
  (ngay du bao eligible, mau co nhan, so hotel tong/theo thanh pho) deu tinh tren CHINH tap primary nay; so lieu tren moi hotel (`all_*`) chi la audit.
- Train khong loc hotel (primary == usable).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .feature_spec import CITIES, HORIZONS

SPLITS = ("train", "validation", "test")


def gate_for(gates: dict[str, Any], horizon: int) -> dict[str, Any]:
    return gates["h14"] if horizon == 14 else gates["h1_h3_h7"]


def split_stats(frame: pd.DataFrame, horizon: int, split: str, train_hotels: set[str]) -> dict[str, Any]:
    usable = frame[frame[f"label_usable_h{horizon}"].fillna(False).astype(bool) & (frame["split"] == split)]
    primary = usable if split == "train" else usable[usable["hotel_id"].isin(train_hotels)]
    per_city = primary.groupby("city")["hotel_id"].nunique().to_dict()
    stats: dict[str, Any] = {
        # --- cac chi so dung de PASS gate primary (cung mot frame `primary`)
        "eligible_prediction_dates": int(primary["vn_observation_date"].nunique()),
        "labeled_samples": int(len(primary)),
        "hotels_seen_in_train": int(primary["hotel_id"].nunique()),
        "hotels_seen_in_train_per_city": {city: int(per_city.get(city, 0)) for city in CITIES},
        # --- audit tren moi hotel (KHONG dung de PASS)
        "all_eligible_prediction_dates": int(usable["vn_observation_date"].nunique()),
        "all_labeled_samples": int(len(usable)),
        "all_hotels": int(usable["hotel_id"].nunique()),
        "excluded_unseen_hotel_samples": int(len(usable) - len(primary)),
        "series": int(primary["canonical_series_id"].nunique()) if "canonical_series_id" in primary.columns else None,
    }
    return stats


def evaluate_horizon(frame: pd.DataFrame, horizon: int, gate: dict[str, Any]) -> dict[str, Any]:
    train_usable = frame[frame[f"label_usable_h{horizon}"].fillna(False).astype(bool) & (frame["split"] == "train")]
    train_hotels = set(train_usable["hotel_id"])
    splits = {name: split_stats(frame, horizon, name, train_hotels) for name in SPLITS}
    failed: list[str] = []
    for name in SPLITS:
        stats = splits[name]
        if stats["eligible_prediction_dates"] < gate["eligible_prediction_dates"][name]:
            failed.append(f"{name}: eligible_prediction_dates {stats['eligible_prediction_dates']} < {gate['eligible_prediction_dates'][name]}")
        if stats["labeled_samples"] < gate["labeled_samples"][name]:
            failed.append(f"{name}: labeled_samples {stats['labeled_samples']} < {gate['labeled_samples'][name]}")
        if name != "train":
            if stats["hotels_seen_in_train"] < gate["hotels_per_eval_split"]["total"]:
                failed.append(f"{name}: hotels {stats['hotels_seen_in_train']} < {gate['hotels_per_eval_split']['total']}")
            weak = [c for c, n in stats["hotels_seen_in_train_per_city"].items() if n < gate["hotels_per_eval_split"]["per_city"]]
            if weak:
                failed.append(f"{name}: hotels/city < {gate['hotels_per_eval_split']['per_city']} o {weak}")
    return {"gate": gate, "splits": splits, "train_hotels": len(train_hotels), "shortfall": shortfall(splits, gate),
            "status": "primary_eligible" if not failed else "exploratory", "failed_gates": failed}


def shortfall(splits: dict[str, dict[str, Any]], gate: dict[str, Any]) -> dict[str, Any]:
    """Thieu hut XAC DINH theo tung (split, gate) = max(0, nguong - so do) - cho biet nut that la lich, mau, hotel hay city (GPT vong 2: thay cho
    mot scalar `days_until_feasible` vo nghia khi nghen khong phai lich). Chi liet ke city con thieu; `0` = dat."""
    out: dict[str, Any] = {}
    for name in SPLITS:
        stats = splits[name]
        entry: dict[str, Any] = {
            "eligible_prediction_dates_short": max(0, gate["eligible_prediction_dates"][name] - stats["eligible_prediction_dates"]),
            "labeled_samples_short": max(0, gate["labeled_samples"][name] - stats["labeled_samples"]),
        }
        if name != "train":
            per_city = {c: gate["hotels_per_eval_split"]["per_city"] - n for c, n in stats["hotels_seen_in_train_per_city"].items()
                        if n < gate["hotels_per_eval_split"]["per_city"]}
            entry["hotels_total_short"] = max(0, gate["hotels_per_eval_split"]["total"] - stats["hotels_seen_in_train"])
            entry["hotels_per_city_short"] = per_city
        out[name] = entry
    return out


NOT_EVALUATED = {"status": "not_evaluated", "failed_gates": [], "reason": "horizon nam ngoai evaluation_horizons cua build nay (chi nhan tinh de audit)"}


def sufficiency_report(frame: pd.DataFrame, gates: dict[str, Any], evaluation_horizons: Any = HORIZONS) -> dict[str, Any]:
    evaluated = set(int(h) for h in evaluation_horizons)
    return {"registered_gates_source": "discuss/canonical-key-duplicates/05 muc 1.2",
            "definition": "primary = mau label_usable_hk cua val/test thuoc hotel co mau train label_usable_hk (cung horizon); moi chi so primary tinh tren tap nay",
            "evaluation_horizons": sorted(evaluated),
            "horizons": {f"h{k}": evaluate_horizon(frame, k, gate_for(gates, k)) if k in evaluated else dict(NOT_EVALUATED) for k in HORIZONS}}


def candidate_frame(samples: pd.DataFrame, plan: Any, horizon: int) -> pd.DataFrame:
    """Frame cho viec CHON bien: tu mau chon (hotel_id, city, canonical_series_id, vn_observation_date, has_label_h{k}) + mot `plan`, suy `split`
    va `label_usable_h{k}` DUNG nhu buoc features se tinh (nhan target tai ngay + k; usable <=> target cung split voi mau va khong o vung purge)."""
    frame = samples[["hotel_id", "city", "canonical_series_id", "vn_observation_date", f"has_label_h{horizon}"]].copy()
    dates = pd.to_datetime(frame["vn_observation_date"])
    split = plan.split_series(dates)
    target_split = plan.split_series(dates + pd.Timedelta(days=horizon))
    frame["split"] = split
    frame[f"label_usable_h{horizon}"] = frame[f"has_label_h{horizon}"].astype(bool) & split.notna() & (target_split == split)
    return frame
