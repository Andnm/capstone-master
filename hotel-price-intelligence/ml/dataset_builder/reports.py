"""Bao cao coverage + sufficiency (E10) - TACH KHOI `status=pass` cua manifest.

`status=pass` = toan ven + gate spec muc 17 (split khong rong, label>0...). Sufficiency = bang gate da DANG KY TRUOC
(`discuss/canonical-key-duplicates/05` muc 1.2): moi horizon mot trong hai nhan `primary_eligible` / `exploratory`
(+ ly do thieu); KHONG lam fail build. Dinh nghia chi tiet (primary horizon-specific, moi chi so primary tren cung mot tap) nam o
`sufficiency.py` - dung chung voi buoc chon bien split de hai noi khong the lech nhau.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from .feature_spec import HORIZONS
from .sufficiency import CITIES, SPLITS, evaluate_horizon, gate_for  # noqa: F401 - CITIES/SPLITS re-export cho code cu
from .sufficiency import sufficiency_report as _sufficiency_report


def sufficiency_report(frame: pd.DataFrame, config: dict[str, Any]) -> dict[str, Any]:
    return _sufficiency_report(frame, config["split_selection_policy"]["gates"])


def coverage_report(frame: pd.DataFrame, split_report: dict[str, Any] | None = None) -> dict[str, Any]:
    def counts(column: str) -> dict[str, int]:
        return {str(k): int(v) for k, v in frame.groupby(column, dropna=False).size().items()}

    horizons: dict[str, Any] = {}
    for horizon in HORIZONS:
        horizons[f"h{horizon}"] = {
            "has_label": int(frame[f"has_label_h{horizon}"].sum()), "label_usable": int(frame[f"label_usable_h{horizon}"].sum()),
            "label_usable_by_split": {s: int((frame[f"label_usable_h{horizon}"] & (frame["split"] == s)).sum()) for s in SPLITS},
            "hotel_seen_in_train": counts(f"hotel_seen_in_train_h{horizon}"),
        }
    return {
        "rows": int(len(frame)), "hotels": int(frame["hotel_id"].nunique()), "series": int(frame["canonical_series_id"].nunique()),
        "vn_observation_date_min": str(frame["vn_observation_date"].min().date()),
        "vn_observation_date_max": str(frame["vn_observation_date"].max().date()),
        "by_split": counts("split"), "by_city": counts("city"), "by_lead_time_bucket": counts("lead_time_bucket"),
        "by_inference_mode": counts("inference_mode"),
        "horizons": horizons, "split": split_report,
    }
