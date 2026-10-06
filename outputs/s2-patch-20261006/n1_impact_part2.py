import json
from pathlib import Path

import pandas as pd

HERE = Path(r"D:\MSE\CAPSTONE\outputs\s2-patch-20261006")
slugs = ["chez-mimosa-ho-chi-minh", "chez-van", "ks-huy-hoang-airport", "mila-homestay", "starview-villa"]     # tu n1_impact.py (item->hotel, quet 24/09)
sample = pd.read_parquet(r"D:\MSE\CAPSTONE\outputs\datasets\ds_20261006_rh6\samples.parquet",
                         columns=["hotel_id", "canonical_series_id", "split", "breakfast_included", "label_usable_h1", "label_usable_h3", "label_usable_h7", "has_label_h14"])
mine = sample[sample["hotel_id"].isin(slugs)]
per_hotel = mine.groupby("hotel_id").agg(samples=("hotel_id", "size"), series=("canonical_series_id", "nunique"), breakfast_true_share=("breakfast_included", "mean"),
                                         usable_h1=("label_usable_h1", "sum")).round(3)
summary = {
    "hotels": slugs, "hotels_in_rh6": int(mine["hotel_id"].nunique()), "rh6_samples": int(len(mine)), "rh6_samples_share_%": round(100 * len(mine) / len(sample), 3),
    "series": int(mine["canonical_series_id"].nunique()), "series_share_%": round(100 * mine["canonical_series_id"].nunique() / sample["canonical_series_id"].nunique(), 3),
    "usable_labels": {c: int(mine[c].sum()) for c in ("label_usable_h1", "label_usable_h3", "label_usable_h7")},
    "usable_labels_share_%": {c: round(100 * float(mine[c].sum()) / float(sample[c].sum()), 3) for c in ("label_usable_h1", "label_usable_h3", "label_usable_h7")},
    "breakfast_included_true_share_%": {"these_hotels": round(100 * float(mine["breakfast_included"].mean()), 2), "all_samples": round(100 * float(sample["breakfast_included"].mean()), 2)},
    "samples_by_split": {k: int(v) for k, v in mine["split"].fillna("purge").value_counts().items()},
    "per_hotel": json.loads(per_hotel.to_json(orient="index")),
}
(HERE / "n1_impact_rh6.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=1))
