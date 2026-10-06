"""Gate tong the PHAN 2 - ban sua (GPT file 48: P2-M1/M2/M3). CHI DOC (SELECT + Parquet); ghi vao thu muc MOI `part2_review_v2/<version>/`, khong overwrite ban cu.

    eda/.venv/Scripts/python.exe outputs/dataset-builder-rehearsal-20261006/gate_part2_analysis_v2.py --version ds_20261006_rh6 \
        --smoke-run outputs/models/ds_20261006_rh6/rehearsal_rh6_h1_ridge

Khac ban v1: (1) thoi diem = observed_at UTC cua quan sat (exact/alias: quan sat duoc chon; unavailable/ambiguous: MIN observed_at cua item), post-approval = event >= approved_at (UTC),
chi doi sang gio VN de tinh ngay; (2) ambiguous: item va NGAY-SERIES DISTINCT, goi 'ambiguous-associated', khong 'lost only'; (3) Accuracy@20% bang `training.metrics.regression_metrics`
(mau so y), bao ca all-usable va primary; (4) churn dung thu tu xac dinh + so ngay quan sat distinct; (5) alias: bao association, so cap trong cung hotel/strata; (6) so sanh thuoc tinh
reference o phan giao causal/full-history; (7) confusion matrix 3 lop cho smoke Ridge; (8) mo phong so ngay can thiet theo tung policy chia split.
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
from analysis import gate_part2 as g  # noqa: E402
from dataset_builder.config import split_selection_policy  # noqa: E402
from dataset_builder.db import connect, fetch_all  # noqa: E402
from dataset_builder.splitter import required_windows  # noqa: E402

DATASETS = Path(r"D:\MSE\CAPSTONE\outputs\datasets")
OUT_ROOT = Path(r"D:\MSE\CAPSTONE\outputs\dataset-builder-rehearsal-20261006\part2_review_v2")
LEAD_BINS = [-1, 2, 6, 13, 29, 59, 100_000]
LEAD_LABELS = ["lt3", "3-7", "7-14", "14-30", "30-60", "gt60"]
SINCE_BINS = [-1, 0, 7, 14, 30, 100_000]
SINCE_LABELS = ["0d", "1-7d", "8-14d", "15-30d", "31d+"]
STATUS_ORDER = ["exact", "alias", "unavailable", "ambiguous"]


def pct(n, d):
    return round(100.0 * n / d, 2) if d else None


def status_table(frame: pd.DataFrame, by: str) -> pd.DataFrame:
    t = frame.groupby([by, "status"], observed=True).size().unstack(fill_value=0).reindex(columns=STATUS_ORDER, fill_value=0)
    t["items"] = t.sum(axis=1)
    for s in STATUS_ORDER:
        t[f"{s}_%"] = (100.0 * t[s] / t["items"]).round(2)
    return t


def calendar_policies(first_day: pd.Timestamp) -> dict:
    policy = split_selection_policy()

    def last_date(days: int) -> str:
        return str((first_day + pd.Timedelta(days=days - 1)).date())

    shared_p14 = {f"h{k}": sum((required_windows(policy, k)[0], 14, required_windows(policy, k)[1], 14, required_windows(policy, k)[2])) for k in (1, 3, 7, 14)}
    train_for_all = max(policy["gates"]["h14" if k == 14 else "h1_h3_h7"]["eligible_prediction_dates"]["train"] + k for k in (1, 3, 7, 14))      # train eligible cua K = cua so - K
    h14_train, h14_val, h14_test = required_windows(policy, 14)
    shared_all = train_for_all + 14 + h14_val + 14 + h14_test
    per_horizon_pk = {f"h{k}": sum((required_windows(policy, k)[0], k, required_windows(policy, k)[1], k, required_windows(policy, k)[2])) for k in (1, 3, 7, 14)}
    return {
        "first_observation_date": str(first_day.date()),
        "shared_split_P14_horizon_is_the_largest_feasible": {k: {"needed_days": v, "last_observation_date": last_date(v)} for k, v in shared_p14.items()},
        "shared_split_P14_ALL_horizons_primary": {"train_window_needed": train_for_all, "needed_days": shared_all, "last_observation_date": last_date(shared_all),
                                                 "note": "tren split cua H=14 (train 28 ngay) h1/h3/h7 chi co 27/25/21 ngay train eligible < gate 28 => exploratory; muon ca bon primary tren MOT split can train >= 35 ngay"},
        "per_horizon_build_purge_K": {k: {"needed_days": v, "last_observation_date": last_date(v)} for k, v in per_horizon_pk.items()},
        "per_horizon_build_purge_14": {k: {"needed_days": v, "last_observation_date": last_date(v)} for k, v in shared_p14.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--database", default="warehouse_dsdev_20261004_3src")
    parser.add_argument("--source-database", default="warehouse_20261004_3src")
    parser.add_argument("--smoke-run", default=None)
    args = parser.parse_args()
    out = OUT_ROOT / args.version
    out.mkdir(parents=True, exist_ok=True)
    result: dict = {"dataset_version": args.version, "database": args.database, "script": "gate_part2_analysis_v2.py"}

    with connect(args.database) as conn:
        matches = pd.DataFrame(fetch_all(conn, """
            SELECT m.crawl_run_item_id AS item_id, m.match_status AS status, m.selected_record_id, a.id AS aid, a.hotel_id, a.checkin_date, a.approved_at,
                   h.city, im.source_code, cri.crawl_run_id AS run_id, sel.observed_at AS sel_observed_at
            FROM ml_item_reference_matches m
            JOIN ml_reference_assignments a ON a.id = m.ml_reference_assignment_id AND a.dataset_version = m.dataset_version
            JOIN crawl_run_items cri ON cri.id = m.crawl_run_item_id
            JOIN hotels h ON h.hotel_id = a.hotel_id
            JOIN etl_item_map im ON im.warehouse_item_id = cri.id
            LEFT JOIN price_observations sel ON sel.record_id = m.selected_record_id
            WHERE m.dataset_version = %s""", (args.version,)))
        bounds = pd.DataFrame(fetch_all(conn, """
            SELECT m.crawl_run_item_id AS item_id, MIN(po.observed_at) AS obs_min, MAX(po.observed_at) AS obs_max, COUNT(*) AS n_obs
            FROM ml_item_reference_matches m JOIN price_observations po ON po.crawl_run_item_id = m.crawl_run_item_id
            WHERE m.dataset_version = %s GROUP BY m.crawl_run_item_id""", (args.version,)))
        assignments = pd.DataFrame(fetch_all(conn, "SELECT id AS aid, hotel_id, checkin_date, approved_at, room_type_anchor_raw, max_occupancy, breakfast_included, free_cancellation, "
                                                   "evidence_run_count, coverage FROM ml_reference_assignments WHERE dataset_version=%s", (args.version,)))
        sample_days_df = pd.DataFrame(fetch_all(conn, "SELECT ml_reference_assignment_id AS aid, vn_observation_date AS d FROM ml_samples "
                                                      "WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE", (args.version,)))
        dup = pd.DataFrame(fetch_all(conn, """
            SELECT m.crawl_run_item_id AS item_id, COUNT(*) AS n_exact_rows, MIN(po.price_per_night) AS pmin, MAX(po.price_per_night) AS pmax
            FROM ml_item_reference_matches m
            JOIN ml_reference_assignments a ON a.id = m.ml_reference_assignment_id AND a.dataset_version = m.dataset_version
            JOIN price_observations po ON po.crawl_run_item_id = m.crawl_run_item_id AND po.is_sold_out = 0
            JOIN curated_observation_keys k ON k.record_id = po.record_id AND k.canonical_room_key = a.canonical_room_key AND k.canonical_rate_key = a.canonical_rate_key
            WHERE m.dataset_version = %s AND m.match_status = 'ambiguous' GROUP BY m.crawl_run_item_id""", (args.version,)))
        status_of = pd.DataFrame(fetch_all(conn, "SELECT selected_record_id AS record_id, match_status FROM ml_item_reference_matches "
                                                 "WHERE dataset_version=%s AND selected_record_id IS NOT NULL", (args.version,)))
        links = pd.DataFrame(fetch_all(conn, "SELECT record_id, label_source_record_id_h1 AS t1 FROM ml_samples WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE", (args.version,)))
        conn.commit()
    with connect(args.source_database) as src:
        full = pd.DataFrame(fetch_all(src, "SELECT hotel_id, checkin_date, room_type_anchor_raw, max_occupancy, breakfast_included, free_cancellation "
                                           "FROM hotel_reference_rooms WHERE status='approved'"))
        full_series = int(fetch_all(src, "SELECT COUNT(*) n FROM hotel_reference_rooms")[0]["n"])
        src.commit()

    # ------------------------------------------------------------ P2-M1: thoi diem quan sat dung
    matches = matches.merge(bounds, on="item_id", how="left")
    result["item_time_audit"] = g.item_time_audit(matches)
    allm = g.add_time_columns(matches)
    allm["lead_bucket"] = pd.cut(allm["lead"], LEAD_BINS, labels=LEAD_LABELS)
    post = allm[allm["post_approval"]].copy()
    post["since_bucket"] = pd.cut(post["since_days"], SINCE_BINS, labels=SINCE_LABELS)
    pre = allm[~allm["post_approval"]]
    ea = allm["status"].isin(g.EXACT_ALIAS)
    funnel = json.loads((DATASETS / "_reports" / args.version / "samples_labels.json").read_text(encoding="utf-8"))["funnel"]
    result["time_definition"] = ("exact/alias: observed_at UTC cua quan sat duoc chon; unavailable/ambiguous: MIN(observed_at) cua item; post_approval = event_utc >= approved_at (UTC); "
                                 "ngay/lead/since tinh tu gio VN (+7h) cua CA event va approved_at")
    result["all_matched_items"] = int(len(allm))
    result["pre_approval_items"] = {"items": int(len(pre)), "share_%": pct(len(pre), len(allm)), "status": {k: int(v) for k, v in pre["status"].value_counts().items()}}
    result["post_approval_items"] = int(len(post))
    overall = post["status"].value_counts().reindex(STATUS_ORDER, fill_value=0)
    result["post_approval_status"] = {k: int(v) for k, v in overall.items()}
    result["post_approval_status_pct"] = {k: pct(int(v), int(overall.sum())) for k, v in overall.items()}
    result["self_check_exact_alias_post_approval_equals_funnel_not_before_approval"] = {
        "post_approval_exact_alias_by_selected_observed_at": int((post["status"].isin(g.EXACT_ALIAS)).sum()), "funnel_after_not_before_approval": funnel["after_not_before_approval"],
        "funnel_after_price_ok": funnel["after_price_ok"], "exact_alias_items_total": int(ea.sum()),
        "equal": int((post["status"].isin(g.EXACT_ALIAS)).sum()) == funnel["after_not_before_approval"]}
    for by in ("since_bucket", "lead_bucket", "city", "source_code"):
        table = status_table(post if by != "lead_bucket" else post, by)
        table.to_csv(out / f"post_approval_status_by_{by}.csv", encoding="utf-8-sig")
        result[f"post_approval_by_{by}"] = json.loads(table.to_json(orient="index"))

    # ------------------------------------------------------------ churn + ambiguous (P2-M1/M2)
    result["churn_reference_not_found_at_tail"] = g.churn_tail(post, k=3)
    sample_days = {(int(a), pd.Timestamp(d).date()) for a, d in zip(sample_days_df["aid"], sample_days_df["d"])}
    result["ambiguous_associated_missing_days"] = g.ambiguous_missing_days(post, sample_days)
    amb = post[post["status"] == "ambiguous"].merge(dup, on="item_id", how="left")
    amb["n_exact_rows"] = amb["n_exact_rows"].fillna(0).astype(int)
    amb["spread_%"] = 100.0 * (amb["pmax"].astype(float) - amb["pmin"].astype(float)) / amb["pmin"].astype(float)
    multi = amb[amb["n_exact_rows"] >= 2]
    result["ambiguous_detail"] = {
        "items_post_approval": int(len(amb)), "share_of_post_approval_items_%": pct(len(amb), len(post)),
        "by_exact_key_rows_with_price": {"0": int((amb["n_exact_rows"] == 0).sum()), "1": int((amb["n_exact_rows"] == 1).sum()), ">=2": int((amb["n_exact_rows"] >= 2).sum())},
        "items_with_2plus_exact_rows_diverging_price_%": pct(int((multi["spread_%"] > 0).sum()), len(multi)),
        "relative_spread_%_quantiles_among_2plus_exact_rows": multi["spread_%"].dropna().quantile([.5, .75, .9, .99]).round(2).to_dict(),
        "by_city_items": amb["city"].value_counts().to_dict(), "by_source_items": amb["source_code"].value_counts().to_dict(),
        "hotels": int(amb["hotel_id"].nunique()), "assignments": int(amb["aid"].nunique()),
        "wording": "ambiguity den tu trung exact-key (>=2 dong) HOAC tu tang alias (0/1 dong exact); khong gan nguyen nhan duy nhat",
    }

    # ------------------------------------------------------------ reference: grain hotel/check-in + thuoc tinh o phan giao
    assignments["key"] = list(zip(assignments["hotel_id"], pd.to_datetime(assignments["checkin_date"]).dt.date))
    full["key"] = list(zip(full["hotel_id"], pd.to_datetime(full["checkin_date"]).dt.date))
    inter = assignments.merge(full, on="key", suffixes=("_causal", "_full"))
    attr = {c: pct(int((inter[f"{c}_causal"].fillna(-1).astype(str) == inter[f"{c}_full"].fillna(-1).astype(str)).sum()), len(inter))
            for c in ("room_type_anchor_raw", "max_occupancy", "breakfast_included", "free_cancellation")}
    all_same = ((inter["room_type_anchor_raw_causal"] == inter["room_type_anchor_raw_full"]) & (inter["breakfast_included_causal"].fillna(-1) == inter["breakfast_included_full"].fillna(-1))
                & (inter["free_cancellation_causal"].fillna(-1) == inter["free_cancellation_full"].fillna(-1)))
    result["reference"] = {
        "grain": "so sanh MEMBERSHIP (hotel, check-in); thuoc tinh o phan giao ben duoi chi la bang chung gian tiep ve cung phong/rate (khoa canonical hai phia khac thuat toan nen khong so thang)",
        "causal_assignments": int(len(assignments)), "full_history_approved": int(len(set(full["key"]))), "full_history_series_with_candidate": full_series,
        "intersection": int(len(inter)), "causal_only": int(len(set(assignments["key"]) - set(full["key"]))), "full_only": int(len(set(full["key"]) - set(assignments["key"]))),
        "intersection_attribute_agreement_%": attr, "intersection_anchor_breakfast_cancellation_all_equal_%": pct(int(all_same.sum()), len(inter)),
        "evidence_run_count_median": float(assignments["evidence_run_count"].median()), "coverage_at_approval_mean": round(float(assignments["coverage"].astype(float).mean()), 4),
    }

    # ------------------------------------------------------------ Parquet: Accuracy@20 dung mau so, phan phoi, alias association
    frame = pd.read_parquet(DATASETS / args.version / "samples.parquet")
    frame["split"] = frame["split"].fillna("purge")
    result["rows"], result["hotels"], result["series"] = int(len(frame)), int(frame["hotel_id"].nunique()), int(frame["canonical_series_id"].nunique())
    result["persistence_accuracy_at_20pct_via_training_metrics"] = {
        f"h{h}": {"all_usable": g.persistence_accuracy(frame, h), "primary": g.persistence_accuracy(frame, h, primary_only=True)} for h in (1, 3, 7, 14)}

    merged = frame.merge(links, left_on="warehouse_record_id", right_on="record_id", how="left")
    st = status_of.set_index("record_id")["match_status"]
    usable = merged[merged["label_usable_h1"].fillna(False).astype(bool)].copy()
    usable["src"], usable["tgt"] = usable["warehouse_record_id"].map(st), usable["t1"].map(st)
    usable["alias_involved"] = (usable["src"] == "alias") | (usable["tgt"] == "alias")
    usable["abs_pct"] = usable["y_pct_change_h1"].astype(float).abs()
    gb = usable.groupby(["hotel_id", "alias_involved"]).agg(n=("abs_pct", "size"), mean_abs=("abs_pct", "mean")).unstack("alias_involved")
    gb.columns = [f"{a}_{'alias' if b else 'exact'}" for a, b in gb.columns]
    paired = gb.dropna()
    paired = paired[(paired["n_alias"] >= 20) & (paired["n_exact"] >= 20)]
    diff = (paired["mean_abs_alias"] - paired["mean_abs_exact"]) * 100
    strata = usable.groupby(["city", "lead_time_bucket", "source_code"] if "source_code" in usable.columns else ["city", "lead_time_bucket"]).agg(
        n_a=("alias_involved", "sum"), n=("alias_involved", "size"))
    cell = usable.assign(c=list(zip(usable["city"], usable["lead_time_bucket"])))
    num = den = 0.0
    for _, part in cell.groupby("c"):
        a, e = part[part["alias_involved"]], part[~part["alias_involved"]]
        if len(a) >= 20 and len(e) >= 20:
            weight = min(len(a), len(e))
            num += weight * (a["abs_pct"].mean() - e["abs_pct"].mean()) * 100
            den += weight
    result["alias_association_h1"] = {
        "wording": "ASSOCIATION, khong chung minh alias gay nhieu (alias co same_rate_key; khac biet co the do phan phoi hotel/lead/nguon/thoi gian hoac volatility that)",
        "usable": int(len(usable)), "alias_involved_share_%": pct(int(usable["alias_involved"].sum()), len(usable)),
        "overall_mean_abs_pct_%": {"exact_only": round(100 * float(usable[~usable["alias_involved"]]["abs_pct"].mean()), 3), "alias_involved": round(100 * float(usable[usable["alias_involved"]]["abs_pct"].mean()), 3)},
        "within_hotel_paired(>=20 moi nhom)": {"hotels": int(len(diff)), "mean_diff_pp": round(float(diff.mean()), 3) if len(diff) else None,
                                               "median_diff_pp": round(float(diff.median()), 3) if len(diff) else None, "share_hotels_alias_higher_%": pct(int((diff > 0).sum()), len(diff))},
        "within_city_x_lead_bucket_weighted_diff_pp": round(num / den, 3) if den else None,
    }

    # ------------------------------------------------------------ smoke Ridge: 3 lop
    if args.smoke_run:
        pred = pd.read_parquet(Path(args.smoke_run) / "h1_predictions_ridge.parquet")
        smoke = {}
        for split, part in pred.groupby("split"):
            true_cls = g.classify_pct(((part["y_true"] - part["current_price"]) / part["current_price"]).to_numpy())
            pred_cls = g.classify_pct(((part["pred_price"] - part["current_price"]) / part["current_price"]).to_numpy())
            smoke[split] = {"ridge": g.class_metrics(true_cls, pred_cls),
                            "persistence_equals_constant_stable": g.class_metrics(true_cls, np.array(["stable"] * len(true_cls)))}
        result["ridge_smoke_three_class_h1"] = smoke

    # ------------------------------------------------------------ lich/policy chia split
    result["calendar_policies"] = calendar_policies(pd.to_datetime(frame["vn_observation_date"]).min())
    result["sufficiency_status"] = {h: v["status"] for h, v in json.loads((DATASETS / args.version / "sufficiency_report.json").read_text(encoding="utf-8"))["horizons"].items()}

    (out / "gate_part2_summary_v2.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str)[:15000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
