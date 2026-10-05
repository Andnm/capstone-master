"""Bao cao coverage + sufficiency (E10) - TACH KHOI `status=pass` cua manifest.

`status=pass` = toan ven + gate spec muc 17 (split khong rong, label>0...). Sufficiency = bang gate da DANG KY TRUOC
(`discuss/canonical-key-duplicates/05` muc 1.2): moi horizon mot trong hai nhan `primary_eligible` / `exploratory`
(+ ly do thieu); KHONG lam fail build. "eligible prediction date" = ngay nguon co target t+k cung split (dem tren
`label_usable_hk`), khong phai do dai lich cua split. Hotel o val/test chi duoc dem cho primary neu da co mau
`label_usable` o train cua CUNG horizon (quy dinh primary: hotel val/test phai co trong train).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .feature_spec import HORIZONS

CITIES = ("Hồ Chí Minh", "Hà Nội", "Vũng Tàu", "Đà Lạt", "Phú Quốc")
SPLITS = ("train", "validation", "test")


def _split_stats(frame: pd.DataFrame, horizon: int, split: str, train_hotels: set[str]) -> dict[str, Any]:
    usable = frame[frame[f"label_usable_h{horizon}"] & (frame["split"] == split)]
    primary = usable if split == "train" else usable[usable["hotel_id"].isin(train_hotels)]
    per_city = primary.groupby("city")["hotel_id"].nunique().to_dict()
    return {
        "eligible_prediction_dates": int(usable["vn_observation_date"].nunique()),
        "labeled_samples": int(len(usable)),
        "hotels_total": int(usable["hotel_id"].nunique()),
        "hotels_seen_in_train": int(primary["hotel_id"].nunique()),
        "hotels_seen_in_train_per_city": {city: int(per_city.get(city, 0)) for city in CITIES},
        "series": int(usable["canonical_series_id"].nunique()),
    }


def sufficiency_report(frame: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    gates = config["split_selection_policy"]["gates"]
    report: dict[str, Any] = {"registered_gates_source": "discuss/canonical-key-duplicates/05 muc 1.2", "horizons": {}}
    for horizon in HORIZONS:
        gate = gates["h14"] if horizon == 14 else gates["h1_h3_h7"]
        train_usable = frame[frame[f"label_usable_h{horizon}"] & (frame["split"] == "train")]
        train_hotels = set(train_usable["hotel_id"])
        splits = {name: _split_stats(frame, horizon, name, train_hotels) for name in SPLITS}
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
        report["horizons"][f"h{horizon}"] = {
            "gate": gate, "splits": splits, "status": "primary_eligible" if not failed else "exploratory", "failed_gates": failed,
        }
    return report


def coverage_report(frame: pd.DataFrame, split_report: dict[str, Any] | None = None) -> dict[str, Any]:
    def counts(column: str) -> dict[str, int]:
        return {str(k): int(v) for k, v in frame.groupby(column, dropna=False).size().items()}

    horizons: dict[str, Any] = {}
    for horizon in HORIZONS:
        horizons[f"h{horizon}"] = {
            "has_label": int(frame[f"has_label_h{horizon}"].sum()), "label_usable": int(frame[f"label_usable_h{horizon}"].sum()),
            "label_usable_by_split": {s: int((frame[f"label_usable_h{horizon}"] & (frame["split"] == s)).sum()) for s in SPLITS},
        }
    return {
        "rows": int(len(frame)), "hotels": int(frame["hotel_id"].nunique()), "series": int(frame["canonical_series_id"].nunique()),
        "vn_observation_date_min": str(frame["vn_observation_date"].min().date()),
        "vn_observation_date_max": str(frame["vn_observation_date"].max().date()),
        "by_split": counts("split"), "by_city": counts("city"), "by_lead_time_bucket": counts("lead_time_bucket"),
        "by_inference_mode": counts("inference_mode"), "hotel_seen_in_train": counts("hotel_seen_in_train"),
        "horizons": horizons, "split": split_report,
    }
