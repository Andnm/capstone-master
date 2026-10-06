"""Alias co lam nhieu nhan khong? So sanh bien dong gia h1/h3/h7 theo trang thai match (exact/alias) cua mau goc va mau dich. CHI DOC.

    eda/.venv/Scripts/python.exe outputs/dataset-builder-rehearsal-20261006/alias_label_noise.py --version ds_20261006_rh6
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ML = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\ml")
sys.path.insert(0, str(ML))
from dataset_builder.db import connect, fetch_all  # noqa: E402

DATASETS = Path(r"D:\MSE\CAPSTONE\outputs\datasets")
OUT = Path(r"D:\MSE\CAPSTONE\outputs\dataset-builder-rehearsal-20261006\part2")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--database", default="warehouse_dsdev_20261004_3src")
    args = parser.parse_args()
    with connect(args.database) as conn:
        status = pd.DataFrame(fetch_all(conn, "SELECT selected_record_id AS record_id, match_status FROM ml_item_reference_matches "
                                              "WHERE dataset_version=%s AND selected_record_id IS NOT NULL", (args.version,)))
        links = pd.DataFrame(fetch_all(conn, "SELECT record_id, label_source_record_id_h1 AS t1, label_source_record_id_h3 AS t3, label_source_record_id_h7 AS t7 "
                                             "FROM ml_samples WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE", (args.version,)))
        conn.commit()
    frame = pd.read_parquet(DATASETS / args.version / "samples.parquet")
    frame = frame.merge(links, left_on="warehouse_record_id", right_on="record_id", how="left")
    st = status.set_index("record_id")["match_status"]
    frame["src_status"] = frame["warehouse_record_id"].map(st)
    result: dict = {"dataset_version": args.version}
    for h, tcol in ((1, "t1"), (3, "t3"), (7, "t7")):
        usable = frame[frame[f"label_usable_h{h}"].fillna(False).astype(bool)].copy()
        usable["tgt_status"] = usable[tcol].map(st)
        usable["pair"] = usable["src_status"].astype(str) + "->" + usable["tgt_status"].astype(str)
        usable["abs_pct"] = usable[f"y_pct_change_h{h}"].astype(float).abs()
        usable["any_alias"] = (usable["src_status"] == "alias") | (usable["tgt_status"] == "alias")
        rows = {}
        for key, g in list(usable.groupby("pair")) + [("ANY_ALIAS", usable[usable["any_alias"]]), ("EXACT_ONLY", usable[~usable["any_alias"]])]:
            rows[key] = {"n": int(len(g)), "share_%": round(100 * len(g) / len(usable), 2), "changed_gt2%_share": round(100 * float((g["abs_pct"] > 0.02).mean()), 2),
                         "mean_abs_pct_%": round(100 * float(g["abs_pct"].mean()), 3), "p90_abs_pct_%": round(100 * float(g["abs_pct"].quantile(.9)), 2),
                         "p99_abs_pct_%": round(100 * float(g["abs_pct"].quantile(.99)), 2), "gt20%_share": round(100 * float((g["abs_pct"] > 0.20).mean()), 2)}
        result[f"h{h}"] = {"usable": int(len(usable)), "by_pair": rows}
    (OUT / args.version).mkdir(parents=True, exist_ok=True)
    (OUT / args.version / "alias_label_noise.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
