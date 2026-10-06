"""Gate tong the PHAN 2 (muc 4/6/8/9 cua canonical-key-duplicates/11 sec.4) tren dataset rehearsal da PASS. CHI DOC (SELECT + Parquet), khong ghi DB.

    eda/.venv/Scripts/python.exe outputs/dataset-builder-rehearsal-20261006/gate_part2_analysis.py --version ds_20261006_rh2

Nguon: DB ban sao dev `warehouse_dsdev_20261004_3src` (SELECT, timeout 30 phut) + `outputs/datasets/<version>/samples.parquet`. Ket qua: JSON + CSV trong `part2/<version>/`.
Phan khong do duoc o day (khong co trong warehouse): N1 (bua sang), single-guest/Basic - xem gate Phan 1 muc 3.5.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ML = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\ml")
sys.path.insert(0, str(ML))
from dataset_builder.db import connect, fetch_all  # noqa: E402

OUT_ROOT = Path(r"D:\MSE\CAPSTONE\outputs\dataset-builder-rehearsal-20261006\part2")
DATASETS = Path(r"D:\MSE\CAPSTONE\outputs\datasets")
LEAD_BINS = [-1, 2, 6, 13, 29, 59, 10_000]
LEAD_LABELS = ["lt3", "3-7", "7-14", "14-30", "30-60", "gt60"]
SINCE_BINS = [-1, 0, 7, 14, 30, 10_000]
SINCE_LABELS = ["0d", "1-7d", "8-14d", "15-30d", "31d+"]


def pct(n, d):
    return round(100.0 * n / d, 2) if d else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--database", default="warehouse_dsdev_20261004_3src")
    parser.add_argument("--source-database", default="warehouse_20261004_3src")
    args = parser.parse_args()
    out = OUT_ROOT / args.version
    out.mkdir(parents=True, exist_ok=True)
    result: dict = {"dataset_version": args.version, "database": args.database}
    with connect(args.database) as conn:
        matches = pd.DataFrame(fetch_all(conn, """
            SELECT m.crawl_run_item_id AS item_id, m.match_status AS status, a.id AS aid, a.hotel_id, h.city, a.checkin_date, a.approved_at,
                   DATE(DATE_ADD(r.started_at, INTERVAL 7 HOUR)) AS vn_date, im.source_code, im.ownership_status
            FROM ml_item_reference_matches m
            JOIN ml_reference_assignments a ON a.id = m.ml_reference_assignment_id AND a.dataset_version = m.dataset_version
            JOIN crawl_run_items cri ON cri.id = m.crawl_run_item_id
            JOIN crawl_runs r ON r.id = cri.crawl_run_id
            JOIN hotels h ON h.hotel_id = a.hotel_id
            JOIN etl_item_map im ON im.warehouse_item_id = cri.id
            WHERE m.dataset_version = %s""", (args.version,)))
        assignments = pd.DataFrame(fetch_all(conn, "SELECT id AS aid, hotel_id, checkin_date, approved_at, evidence_run_count, evidence_item_count, "
                                                   "eligible_item_count, coverage FROM ml_reference_assignments WHERE dataset_version=%s", (args.version,)))
        sample_days = pd.DataFrame(fetch_all(conn, "SELECT ml_reference_assignment_id AS aid, vn_observation_date AS vn_date FROM ml_samples "
                                                   "WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE", (args.version,)))
        ambiguous_detail = pd.DataFrame(fetch_all(conn, """
            SELECT m.crawl_run_item_id AS item_id, COUNT(*) AS n_candidates, MIN(po.price_per_night) AS pmin, MAX(po.price_per_night) AS pmax
            FROM ml_item_reference_matches m
            JOIN ml_reference_assignments a ON a.id = m.ml_reference_assignment_id AND a.dataset_version = m.dataset_version
            JOIN price_observations po ON po.crawl_run_item_id = m.crawl_run_item_id AND po.is_sold_out = 0
            JOIN curated_observation_keys k ON k.record_id = po.record_id AND k.canonical_room_key = a.canonical_room_key AND k.canonical_rate_key = a.canonical_rate_key
            WHERE m.dataset_version = %s AND m.match_status = 'ambiguous'
            GROUP BY m.crawl_run_item_id""", (args.version,)))
        sample_sources = pd.DataFrame(fetch_all(conn, "SELECT s.record_id AS warehouse_record_id, om.source_code FROM ml_samples s "
                                                      "JOIN etl_observation_map om ON om.warehouse_record_id = s.record_id "
                                                      "WHERE s.dataset_version=%s AND s.is_daily_snapshot_selected=TRUE", (args.version,)))
        conn.commit()

    # full-history reference nam o warehouse NGUON (ban clone dev khong sao chep `hotel_reference_rooms`); chi SELECT
    with connect(args.source_database) as src:
        full = pd.DataFrame(fetch_all(src, "SELECT hotel_id, checkin_date FROM hotel_reference_rooms WHERE status='approved'"))
        series_total = int(fetch_all(src, "SELECT COUNT(*) n FROM hotel_reference_rooms")[0]["n"])
        src.commit()
    result["source_database"] = args.source_database

    # ---------------------------------------------------------------- muc 6: reference approval/freeze, post-approval coverage/churn
    matches["lead"] = (pd.to_datetime(matches["checkin_date"]) - pd.to_datetime(matches["vn_date"])).dt.days
    matches["lead_bucket"] = pd.cut(matches["lead"], LEAD_BINS, labels=LEAD_LABELS)
    matches["since_approval"] = (pd.to_datetime(matches["vn_date"]) - pd.to_datetime(matches["approved_at"]).dt.normalize()).dt.days
    matches["post_approval"] = matches["since_approval"] >= 0
    all_matches = matches                                            # gom ca item truoc ngay duyet (matcher cover ca hai, spec E4)
    matches = all_matches[all_matches["post_approval"]].copy()       # moi bang/ty le duoi day: CHI item tu ngay duyet tro di (cung tap voi dieu kien lay mau)
    matches["since_bucket"] = pd.cut(matches["since_approval"], SINCE_BINS, labels=SINCE_LABELS)
    status_order = ["exact", "alias", "unavailable", "ambiguous"]
    result["pre_approval_items"] = {"items": int((~all_matches["post_approval"]).sum()), "share_of_all_matched_items_%": pct(int((~all_matches["post_approval"]).sum()), len(all_matches)),
                                    "status": {k: int(v) for k, v in all_matches[~all_matches["post_approval"]]["status"].value_counts().items()}}
    result["all_matched_items"] = int(len(all_matches))

    def status_table(by: str) -> pd.DataFrame:
        t = matches.groupby([by, "status"], observed=True).size().unstack(fill_value=0).reindex(columns=status_order, fill_value=0)
        t["items"] = t.sum(axis=1)
        for s in status_order:
            t[f"{s}_%"] = (100.0 * t[s] / t["items"]).round(2)
        return t

    overall = matches["status"].value_counts().reindex(status_order, fill_value=0)
    result["matches_overall_post_approval"] = {k: int(v) for k, v in overall.items()} | {"items": int(overall.sum())}
    result["matches_overall_post_approval_pct"] = {k: pct(int(v), int(overall.sum())) for k, v in overall.items()}
    for by in ("lead_bucket", "since_bucket", "city", "source_code"):
        table = status_table(by)
        table.to_csv(out / f"matches_post_approval_by_{by}.csv", encoding="utf-8-sig")
        result[f"matches_post_approval_by_{by}"] = json.loads(table.to_json(orient="index"))
    result["post_approval_items"] = int(len(matches))

    assignments["approved_day"] = pd.to_datetime(assignments["approved_at"]).dt.date
    full["key"] = list(zip(full["hotel_id"], pd.to_datetime(full["checkin_date"]).dt.date))
    causal_keys = set(zip(assignments["hotel_id"], pd.to_datetime(assignments["checkin_date"]).dt.date))
    full_keys = set(full["key"])
    result["reference"] = {
        "causal_assignments": int(len(assignments)), "full_history_approved": int(len(full_keys)), "full_history_series_with_candidate": series_total,
        "causal_and_full": len(causal_keys & full_keys), "causal_only": len(causal_keys - full_keys), "full_only": len(full_keys - causal_keys),
        "evidence_run_count": assignments["evidence_run_count"].describe().round(2).to_dict(),
        "coverage_at_approval": assignments["coverage"].astype(float).describe().round(4).to_dict(),
        "approved_on_first_day_share_%": pct(int((assignments["approved_day"] == assignments["approved_day"].min()).sum()), len(assignments)),
    }

    # churn: moi assignment, phan tram item sau duyet bi unavailable theo thoi gian; assignment co >=1 item unavailable; assignment "chet" (>=3 item cuoi cung deu unavailable)
    by_assign = matches.sort_values(["aid", "vn_date"]).groupby("aid")
    n_items = by_assign.size()
    unavail = matches.assign(u=(matches["status"] == "unavailable")).groupby("aid")["u"].mean()
    last3 = by_assign["status"].apply(lambda s: bool(len(s) >= 3 and (s.tail(3) == "unavailable").all()))
    result["churn"] = {
        "assignments_with_items": int(len(n_items)), "items_per_assignment_median": float(n_items.median()),
        "assignments_with_any_unavailable_%": pct(int((unavail > 0).sum()), len(unavail)),
        "assignments_mostly_unavailable_gt50%": pct(int((unavail > 0.5).sum()), len(unavail)),
        "assignments_last3_all_unavailable_%": pct(int(last3.sum()), len(last3)),
        "per_assignment_unavailable_rate_quantiles": unavail.quantile([.1, .25, .5, .75, .9]).round(3).to_dict(),
    }
    # luat vao series moi (unavailable chu yeu o check-in xa) da co o since_bucket/lead_bucket CSV

    # ---------------------------------------------------------------- muc 4: target-policy impact (ambiguous)
    amb_all = all_matches[all_matches["status"] == "ambiguous"]
    amb = matches[matches["status"] == "ambiguous"].merge(ambiguous_detail, on="item_id", how="left")
    amb["n_exact_rows"] = amb["n_candidates"].fillna(0).astype(int)
    amb["spread_%"] = 100.0 * (amb["pmax"].astype(float) - amb["pmin"].astype(float)) / amb["pmin"].astype(float)
    day_has_sample = set(zip(sample_days["aid"], pd.to_datetime(sample_days["vn_date"]).dt.date))
    amb["day_has_sample"] = [(a, d) in day_has_sample for a, d in zip(amb["aid"], pd.to_datetime(amb["vn_date"]).dt.date)]
    result["ambiguous"] = {
        "items_all_incl_pre_approval": int(len(amb_all)), "items_post_approval": int(len(amb)), "share_of_post_approval_items_%": pct(len(amb), len(matches)),
        "by_exact_key_rows_with_price": {"0 (ambiguity khong den tu trung exact-key)": int((amb["n_exact_rows"] == 0).sum()), "1": int((amb["n_exact_rows"] == 1).sum()),
                                         ">=2 (trung exact-key)": int((amb["n_exact_rows"] >= 2).sum())},
        "candidates_per_item_when_present": amb["n_candidates"].dropna().describe().round(2).to_dict(),
        "items_with_diverging_prices_%_of_those_with_2plus_exact_rows": pct(int((amb["spread_%"] > 0).sum()), int((amb["n_exact_rows"] >= 2).sum())),
        "relative_spread_%_quantiles": amb["spread_%"].dropna().quantile([.5, .75, .9, .99]).round(2).to_dict(),
        "series_days_lost_only_because_ambiguous": int((~amb["day_has_sample"]).sum()),
        "by_city": amb["city"].value_counts().to_dict(), "by_source": amb["source_code"].value_counts().to_dict(),
        "hotels_affected": int(amb["hotel_id"].nunique()), "assignments_affected": int(amb["aid"].nunique()),
    }
    amb_by_assign = amb.groupby("aid").size()
    result["ambiguous"]["assignments_with_ge3_ambiguous_items"] = int((amb_by_assign >= 3).sum())

    # ---------------------------------------------------------------- Parquet: muc 8/9 (coverage, nhan theo split, phan phoi)
    frame = pd.read_parquet(DATASETS / args.version / "samples.parquet")
    frame = frame.merge(sample_sources, on="warehouse_record_id", how="left")
    frame["split"] = frame["split"].fillna("purge")
    result["rows"] = int(len(frame))
    result["hotels"] = int(frame["hotel_id"].nunique())
    result["series"] = int(frame["canonical_series_id"].nunique())
    split_stats = frame.groupby("split").agg(rows=("hotel_id", "size"), hotels=("hotel_id", "nunique"), series=("canonical_series_id", "nunique"),
                                             days=("vn_observation_date", "nunique"), first=("vn_observation_date", "min"), last=("vn_observation_date", "max"))
    result["by_split"] = json.loads(split_stats.astype({"first": str, "last": str}).to_json(orient="index"))
    for column in ("city", "lead_time_bucket", "inference_mode", "source_code"):
        shares = (frame[frame["split"] != "purge"].groupby(["split", column]).size().unstack(fill_value=0))
        shares_pct = (100.0 * shares.div(shares.sum(axis=1), axis=0)).round(2)
        shares_pct.to_csv(out / f"split_share_by_{column}.csv", encoding="utf-8-sig")
        result[f"split_share_pct_by_{column}"] = json.loads(shares_pct.to_json(orient="index"))
    labels = {}
    for h in (1, 3, 7, 14):
        usable = frame[frame[f"label_usable_h{h}"].fillna(False).astype(bool)]
        entry = {"has_label": int(frame[f"has_label_h{h}"].sum()), "label_usable": int(len(usable)),
                 "usable_by_split": usable["split"].value_counts().to_dict(),
                 "hotels_by_split": usable.groupby("split")["hotel_id"].nunique().to_dict()}
        if len(usable):
            pc = usable[f"y_pct_change_h{h}"].astype(float)
            entry["persistence_accuracy_at_20pct_%"] = {"all_usable": pct(int((pc.abs() <= 0.20).sum()), len(pc)),
                                                        **{sp: pct(int((g[f"y_pct_change_h{h}"].astype(float).abs() <= 0.20).sum()), len(g)) for sp, g in usable.groupby("split")}}
            entry["pct_change"] = {"stable_within_2%_share": pct(int((pc.abs() <= 0.02).sum()), len(pc)), "up_gt2%": pct(int((pc > 0.02).sum()), len(pc)),
                                   "down_lt-2%": pct(int((pc < -0.02).sum()), len(pc)), "abs_quantiles_%": (100 * pc.abs().quantile([.5, .9, .99])).round(2).to_dict()}
            if h == 1:
                entry["pct_change_by_source"] = {s: {"n": int(len(g)), "stable_%": pct(int((g[f"y_pct_change_h{h}"].astype(float).abs() <= 0.02).sum()), len(g)),
                                                      "median_abs_%": round(100 * float(g[f"y_pct_change_h{h}"].astype(float).abs().median()), 3)}
                                                 for s, g in usable.groupby("source_code")}
                entry["pct_change_by_split"] = {s: {"n": int(len(g)), "stable_%": pct(int((g[f"y_pct_change_h{h}"].astype(float).abs() <= 0.02).sum()), len(g)),
                                                     "median_abs_%": round(100 * float(g[f"y_pct_change_h{h}"].astype(float).abs().median()), 3)}
                                                for s, g in usable.groupby("split")}
        labels[f"h{h}"] = entry
    result["labels"] = labels
    # phan phoi lead time/city giua train va test (dich chuyen phan phoi)
    sufficiency = json.loads((DATASETS / args.version / "sufficiency_report.json").read_text(encoding="utf-8"))
    result["sufficiency"] = {h: {"status": v["status"], "failed_gates": v["failed_gates"], "shortfall": v["shortfall"]} for h, v in sufficiency["horizons"].items()}
    plan = json.loads((DATASETS / "_reports" / args.version / "split.json").read_text(encoding="utf-8"))["plan"]
    first = pd.to_datetime(frame["vn_observation_date"]).min().date()
    result["projection"] = {
        "first_observation_date": str(first), "last_observation_date": str(pd.to_datetime(frame["vn_observation_date"]).max().date()),
        "needed_days_by_horizon": {f"h{c['horizon']}": c["needed_days"] for c in plan["candidates"]},
        "earliest_last_observation_date_for_gate": {f"h{c['horizon']}": str(first + pd.Timedelta(days=c["needed_days"] - 1)) for c in plan["candidates"]},
    }
    purge_share = pct(int((frame["split"] == "purge").sum()), len(frame))
    result["purge_zone_share_%"] = purge_share

    (out / "gate_part2_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str)[:12000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
