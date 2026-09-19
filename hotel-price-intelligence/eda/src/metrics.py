"""Metric tinh toan THUAN pandas tren bang DA AGGREGATE (khong cham MySQL) (EDA_CURATED_PLAN.md muc 6-7).

`queries.py` GROUP BY thang trong SQL (bounded-memory, GPT review 12 eda file 11 muc 4) roi truyen bang NHO vao day de
tinh ty le/co/phan loai. Tach rieng de test nhanh, khong phu thuoc du lieu that - moi bug logic grain/denominator
(vd GPT review 12 file 03 M2/M3/M4) deu phai bat duoc bang synthetic DataFrame nho, khong can cho warehouse that.

Cac ham pandas cap OBSERVATION (price_distribution_stats, robust_price_outliers, canonical_series_turnover,
theoretical_horizon_pairs...) DA XOA: phien ban SQL tuong ung nam o `queries.py` va duoc doi chieu voi numpy/oracle trong
integration test `test_sql_aggregates.py`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Horizon du bao K (ngay) - dung cho readiness ly thuyet (plan 7.12) va nhan h1/h3/h7/h14 cua dataset sau nay.
HORIZON_DAYS: tuple[int, ...] = (1, 3, 7, 14)

# ======================================================================== 7.6 lead-time bucket
LEAD_TIME_BUCKETS: tuple[tuple[int, int | None, str], ...] = (
    (0, 0, "0"), (1, 3, "1-3"), (4, 7, "4-7"), (8, 14, "8-14"),
    (15, 30, "15-30"), (31, 60, "31-60"), (61, None, "61+"),
)
LEAD_TIME_BUCKET_ORDER = tuple(label for _lo, _hi, label in LEAD_TIME_BUCKETS)
INVALID_LEAD_TIME_BUCKET = "(invalid)"  # lead_time am - khong bao gio ky vong, nhung khong duoc rot am tham

# GPT review 12 eda file 11 muc 6.1: bang "legacy" (doi chieu truc tiep voi bang lich su CLAUDE.md muc
# 7.2: 30,4/27,1/18,3/11,2/7,6/7,1) gop "0" + "1-3" thanh "0-3" - KHAC voi bucket CHUAN o tren (giu "0"
# va "1-3" rieng). Dung rieng cho bang so sanh lich su, KHONG thay the LEAD_TIME_BUCKETS chuan.
LEGACY_LAST_MINUTE_BUCKETS: tuple[tuple[int, int | None, str], ...] = (
    (0, 3, "0-3"), (4, 7, "4-7"), (8, 14, "8-14"),
    (15, 30, "15-30"), (31, 60, "31-60"), (61, None, "61+"),
)
LEGACY_LAST_MINUTE_BUCKET_ORDER = tuple(label for _lo, _hi, label in LEGACY_LAST_MINUTE_BUCKETS)


def _bucket_sql_case(buckets: tuple[tuple[int, int | None, str], ...], column_expr: str) -> str:
    """Sinh 1 bieu thuc SQL `CASE WHEN ... END` TU CHINH danh sach bucket Python - MOT nguon su that
    duy nhat cho ranh gioi bucket (GPT review 12 eda file 11 muc 4: day aggregate xuong SQL de khong
    con nap ca trieu dong observation vao RAM chi de GROUP BY, nhung KHONG duoc duplicate dinh nghia
    ranh gioi bucket rieng o SQL - se lech neu 1 ben doi ma quen ben kia)."""
    parts = [
        (f"WHEN {column_expr} >= {lo} THEN '{label}'" if hi is None
         else f"WHEN {column_expr} BETWEEN {lo} AND {hi} THEN '{label}'")
        for lo, hi, label in buckets
    ]
    return "CASE " + " ".join(parts) + " END"


def lead_time_bucket_sql_case(column_expr: str) -> str:
    return _bucket_sql_case(LEAD_TIME_BUCKETS, column_expr)


def legacy_last_minute_bucket_sql_case(column_expr: str) -> str:
    return _bucket_sql_case(LEGACY_LAST_MINUTE_BUCKETS, column_expr)


def lead_time_bucket(lead_time: int) -> str:
    if lead_time < 0:
        raise ValueError(f"lead_time={lead_time} am - khong hop le (muc 4.2.a: lead_time>=0)")
    for lo, hi, label in LEAD_TIME_BUCKETS:
        if lead_time >= lo and (hi is None or lead_time <= hi):
            return label
    raise AssertionError(f"khong bucket duoc lead_time={lead_time} - thieu range trong LEAD_TIME_BUCKETS")


def order_lead_time_bucket_rows(df: "pd.DataFrame", *, column: str = "lead_time_bucket",
                                order: tuple[str, ...] = LEAD_TIME_BUCKET_ORDER) -> "pd.DataFrame":
    """Sap cac dong theo thu tu bucket CHUAN (0, 1-3, ..., 61+), bucket la (vd '(invalid)') xuong cuoi. KHONG bo dong nao."""
    rank = {label: index for index, label in enumerate(order)}
    keys = df[column].map(lambda value: rank.get(value, len(order)))
    return df.assign(_rank=keys).sort_values(["_rank", column], kind="stable").drop(columns="_rank").reset_index(drop=True)


# ======================================================================== 7.8 item-grain availability (GPT review 12 file 03 M2, file 11 muc 4)
# Snapshot warehouse_20260916_2src quan sat duoc 4/5 status terminal (0 'partial') - van dang ky ca 5
# de khong vo trong im lang neu batch sau co 'partial' (muc 5 quy tac cua GPT o file 05 §2).
TERMINAL_ITEM_STATUSES: tuple[str, ...] = ("success", "sold_out", "not_bookable", "partial", "error")
ITEM_STATUS_COUNT_COLUMNS: tuple[str, ...] = tuple(f"n_{status}" for status in TERMINAL_ITEM_STATUSES)


def finalize_item_status_counts(counts: "pd.DataFrame", group_cols: "list[str] | tuple[str, ...]" = ()) -> "pd.DataFrame":
    """Chuan hoa bang dem status tu SQL (SUM tren bieu thuc boolean tra Decimal/NULL) ve int64 va KIEM TRA tong 5 status terminal
    = n_items - lech nghia la co status ngoai tap terminal (queued/running/gia tri la) lot vao: raise, khong am tham bo qua
    (item chua terminal khong duoc phep co trong 1 warehouse PASS). Item-grain: moi item dung 1 lan (GPT review 12 M2)."""
    count_columns = [*ITEM_STATUS_COUNT_COLUMNS, "n_items"]
    missing = [c for c in [*group_cols, *count_columns] if c not in counts.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    frame = counts.copy()
    for column in count_columns:
        frame[column] = frame[column].fillna(0).astype("int64")
    status_total = frame[list(ITEM_STATUS_COUNT_COLUMNS)].sum(axis=1)
    bad = frame["n_items"] != status_total
    if bad.any():
        raise ValueError(
            f"{int(bad.sum())} nhom co n_items != tong 5 status terminal {TERMINAL_ITEM_STATUSES} - co item khong terminal "
            f"(queued/running) hoac status la lot vao: {frame.loc[bad].head(3).to_dict('records')}"
        )
    return frame[[*group_cols, *count_columns]]


def item_status_rates(counts: "pd.DataFrame") -> "pd.DataFrame":
    """Them `<status>_rate = n_<status> / n_items` (numerator/denominator DEU nam trong bang: n_<status>, n_items). n_items = 0
    -> NaN (khong chia 0). Khong bo bat ky status nao ke ca khi dem = 0 (plan 7.8)."""
    if "n_items" not in counts.columns:
        raise ValueError("thieu cot bat buoc 'n_items'")
    out = counts.copy()
    denominator = out["n_items"].replace(0, np.nan)
    for status in TERMINAL_ITEM_STATUSES:
        out[f"{status}_rate"] = out[f"n_{status}"] / denominator
    return out


def status_present_report(overall_counts: "pd.DataFrame") -> dict[str, int]:
    """`overall_counts`: bang `item_status_counts_overall_main` (1 dong). Bao dung status nao co mat (ke ca 0 dong) - de khong
    bao gio am tham bo qua 1 status terminal (muc 7.8: "Snapshot hien tai co partial=0 nhung van phai bao ro va code khong duoc
    am tham bo status nay khi batch sau xuat hien")."""
    row = overall_counts.iloc[0]
    return {status: int(row[f"n_{status}"]) for status in TERMINAL_ITEM_STATUSES}


def item_status_counts_from_rows(
    items: "pd.DataFrame", *, group_cols: "tuple[str, ...] | list[str]" = (),
) -> "pd.DataFrame":
    """Aggregate terminal item rows after effective-hotel resolution.

    SQL cannot safely reconstruct cohort-version-aware hotel identity from a Booking URL. Wave A
    already loads the owned item frame for protocol continuity and resolves it through
    `protocol_schedule.resolve_effective_hotel_id`; this helper reuses that exact identity for
    availability instead of silently placing resolvable errors in `(unknown)/(unattributed)`.
    """
    group_cols = list(group_cols)
    required = ["item_status", *group_cols]
    missing = [c for c in required if c not in items.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    unknown = set(items["item_status"].dropna().unique()) - set(TERMINAL_ITEM_STATUSES)
    if unknown:
        raise ValueError(f"item_status ngoai tap terminal {TERMINAL_ITEM_STATUSES}: {sorted(unknown)}")

    if group_cols:
        if items.empty:
            return pd.DataFrame(columns=[*group_cols, *ITEM_STATUS_COUNT_COLUMNS, "n_items"])
        grouped = (
            items.groupby([*group_cols, "item_status"], dropna=False).size()
            .unstack("item_status", fill_value=0)
            .reindex(columns=TERMINAL_ITEM_STATUSES, fill_value=0)
            .rename(columns={status: f"n_{status}" for status in TERMINAL_ITEM_STATUSES})
            .reset_index()
        )
    else:
        counts = items["item_status"].value_counts()
        grouped = pd.DataFrame([{
            f"n_{status}": int(counts.get(status, 0)) for status in TERMINAL_ITEM_STATUSES
        }])
    grouped["n_items"] = grouped[list(ITEM_STATUS_COUNT_COLUMNS)].sum(axis=1)
    return finalize_item_status_counts(grouped, group_cols)


def active_hotels_from_effective_items(items: "pd.DataFrame", *, by_city: bool = False) -> "pd.DataFrame":
    """Active hotel = co >=1 owned terminal item, dung effective identity da resolve theo cohort."""
    required = ["source_code", "crawl_date", "effective_hotel_id", "effective_city"]
    missing = [c for c in required if c not in items.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    frame = items[items["effective_hotel_id"].notna()].copy()
    group_cols = ["source_code", "crawl_date"] + (["effective_city"] if by_city else [])
    output_cols = ["source_code", "vn_crawl_date"] + (["city"] if by_city else []) + ["n_active_hotels"]
    if frame.empty:
        return pd.DataFrame(columns=output_cols)
    if by_city:
        frame["effective_city"] = frame["effective_city"].fillna("(unknown)")
    out = frame.groupby(group_cols, dropna=False)["effective_hotel_id"].nunique().reset_index(name="n_active_hotels")
    out = out.rename(columns={"crawl_date": "vn_crawl_date", "effective_city": "city"})
    out["n_active_hotels"] = out["n_active_hotels"].astype("int64")
    return out[output_cols].sort_values(output_cols[:-1]).reset_index(drop=True)


def item_coverage_count(items: "pd.DataFrame", *, group_cols: "tuple[str, ...] | list[str]") -> "pd.DataFrame":
    """Dem MAIN item 1:1 theo cac chieu coverage; khong bi room-option weighting."""
    group_cols = list(group_cols)
    missing = [c for c in group_cols if c not in items.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    if items.empty:
        return pd.DataFrame(columns=[*group_cols, "n_items"])
    return (
        items.groupby(group_cols, dropna=False).size().reset_index(name="n_items")
        .sort_values(group_cols).reset_index(drop=True)
    )


def collision_pairs_from_effective_items(items: "pd.DataFrame") -> "pd.DataFrame":
    """Ghep collision bang effective hotel identity, khong bo item loi co raw `hotel_id=NULL`.

    Pairing chi dua tren `(crawl_date, effective_hotel_id, checkin_date, source_a<source_b)`; khong
    dung gia hay status. Neu mot source co nhieu item/run cho cung khoa, moi cross-source item pair
    duoc giu ro bang item_id (khong tu chon dai mot retry va khong average).
    """
    required = (
        "item_id", "source_code", "crawl_date", "checkin_date", "effective_hotel_id",
        "item_status", "ownership_status", "item_finished_at",
    )
    missing = [c for c in required if c not in items.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    output = [
        "source_a", "source_b", "item_id_a", "item_id_b", "vn_crawl_date", "hotel_id",
        "checkin_date", "status_a", "status_b", "ownership_status_a", "ownership_status_b",
        "observed_finish_a", "observed_finish_b",
    ]
    base = items[items["effective_hotel_id"].notna()].copy()
    key = ["crawl_date", "effective_hotel_id", "checkin_date"]
    if base.empty:
        return pd.DataFrame(columns=output)
    selected = base[["item_id", "source_code", *key, "item_status", "ownership_status", "item_finished_at"]]
    pairs = selected.merge(selected, on=key, suffixes=("_a", "_b"))
    pairs = pairs[pairs["source_code_a"] < pairs["source_code_b"]].copy()
    if pairs.empty:
        return pd.DataFrame(columns=output)
    pairs = pairs.rename(columns={
        "source_code_a": "source_a", "source_code_b": "source_b",
        "crawl_date": "vn_crawl_date", "effective_hotel_id": "hotel_id",
        "item_status_a": "status_a", "item_status_b": "status_b",
        "item_finished_at_a": "observed_finish_a", "item_finished_at_b": "observed_finish_b",
    })
    return pairs[output].sort_values(
        ["vn_crawl_date", "hotel_id", "checkin_date", "source_a", "source_b"]
    ).reset_index(drop=True)


# ======================================================================== 7.10 full-history reference (GPT review 12 file 03 M3, file 09 muc 2, file 11 muc 4)
def _rate_from_counts(
    aggregated: "pd.DataFrame", *, n_col: str, n_true_col: str, rate_col: str,
) -> "pd.DataFrame":
    """Helper PRIVATE dung chung cho cac metric dang "ty le tu bang DA AGGREGATE SAN" (GPT review 12
    eda file 11 muc 4: khong con nap ca trieu dong observation vao RAM chi de GROUP BY trong pandas -
    `queries.py` da GROUP BY thang trong SQL, ham nay chi tinh ty le tren ket qua nho da co san). TEN
    COT/CONTRACT cong khai van phai khac nhau giua cac ham public ben duoi (GPT file 09 muc 2) de khong
    the vo tinh dan nhan sai y nghia (vd goi ket qua ban long la "exact match")."""
    missing = [c for c in (n_col, n_true_col) if c not in aggregated.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    result = aggregated.copy()
    result[rate_col] = result[n_true_col] / result[n_col].replace(0, np.nan)
    return result


def exact_approved_key_observation_coverage(aggregated: "pd.DataFrame") -> "pd.DataFrame":
    """Metric 1 (M3): OBSERVATION-grain EXACT approved-key coverage - CHI tinh ty le tren bang DA
    AGGREGATE SAN theo lead_time_bucket (GPT review 12 eda file 11 muc 4: aggregate da day xuong SQL
    trong `queries.reference_observation_match_main/raw`, khong con GROUP BY o pandas).

    `aggregated`: 1 dong / lead_time_bucket, cot bat buoc `n_observations` (tong observation KHONG
    sold-out trong bucket) va `n_matched` (so observation co canonical room/rate key CUA CHINH no khop
    DUNG 1 reference approved cua (hotel_id, checkin_date)). KHONG dung ham nay cho metric series-level
    long hon - xem `series_with_approved_reference_coverage`.
    """
    return _rate_from_counts(aggregated, n_col="n_observations", n_true_col="n_matched", rate_col="match_rate")


def series_with_approved_reference_coverage(aggregated: "pd.DataFrame") -> "pd.DataFrame":
    """Metric rieng biet voi `exact_approved_key_observation_coverage` (GPT review 12 eda file 09 muc
    2 - "Khong dung mot ham ten exact cho metric series-level"). `aggregated`: 1 dong / lead_time_bucket,
    cot bat buoc `n_observations` va `n_series_has_reference` (so observation ma (hotel_id, checkin_date)
    CO TON TAI it nhat 1 reference approved nao do - KHONG doi hoi dung key nhu ban chat). Ten cot
    khac han `matches_approved_key` de khong the nham 30% series-exists thanh 30% exact-match - day
    chinh la nguyen nhan lech so 3,5% vs ~30% lich su tung gay nham lan, xem CLAUDE.md muc 7.2.
    """
    return _rate_from_counts(
        aggregated, n_col="n_observations", n_true_col="n_series_has_reference", rate_col="series_reference_rate"
    )


def item_level_exact_reference_availability(aggregated: "pd.DataFrame") -> "pd.DataFrame":
    """Metric 2 (M3): ITEM-grain exact-reference availability, tinh ty le tren bang DA AGGREGATE theo lead_time_bucket
    (`queries.reference_item_level_availability_main`): `n_items` = success MAIN item thuoc (hotel_id, checkin_date) co reference
    approved (denominator), `n_items_matched` = so item co it nhat 1 option khop DUNG key approved (numerator)."""
    return _rate_from_counts(aggregated, n_col="n_items", n_true_col="n_items_matched", rate_col="availability_rate")


def missingness_overall(by_source: "pd.DataFrame") -> "pd.DataFrame":
    """Missingness TOAN BO (plan 7.9 'toan bo') = cong n_null/n_total cua `missingness_available_observations` (theo nguon) qua moi nguon,
    van o grain field_value_cell (moi field 1 dong); null_rate = n_null / n_total."""
    required = ("field_group", "field", "n_null", "n_total")
    missing = [c for c in required if c not in by_source.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    grouped = by_source.groupby(["field_group", "field"], as_index=False, sort=False)[["n_null", "n_total"]].sum()
    grouped["null_rate"] = grouped["n_null"] / grouped["n_total"].replace(0, np.nan)
    return grouped


def evidence_runs_share(distribution: "pd.DataFrame", *, group_cols: "tuple[str, ...]" = ()) -> "pd.DataFrame":
    """`distribution`: `series_evidence_runs_distribution` (city, evidence_runs_bucket in {'1','2','3+'}, n_series). Tra ve 1 dong/
    nhom (mac dinh: toan bo) voi n_series (denominator), n_series_ge3 (numerator) va `share_ge3` (plan 7.12: 'ty le series co it
    nhat 3 evidence runs')."""
    required = ("evidence_runs_bucket", "n_series")
    missing = [c for c in required if c not in distribution.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    frame = distribution.copy()
    keys = list(group_cols)
    if not keys:
        frame["_all"] = "all"
        keys = ["_all"]
    grouped = frame.groupby(keys).apply(
        lambda g: pd.Series({
            "n_series": int(g["n_series"].sum()),
            "n_series_ge3": int(g.loc[g["evidence_runs_bucket"] == "3+", "n_series"].sum()),
        }), include_groups=False,
    ).reset_index()
    grouped["share_ge3"] = grouped["n_series_ge3"] / grouped["n_series"].replace(0, np.nan)
    return grouped.drop(columns="_all") if "_all" in grouped.columns else grouped


# ======================================================================== 7.5/7.12 canonical-series turnover (SQL series-grain -> pandas marginals)
FACTS_JOINT_COLUMNS = ("n_observed_days", "max_gap_days", "n_series", "n_reappearance_events", "sum_span_days")
FACTS_SERIES_COLUMNS = (
    "hotel_id", "checkin_date", "canonical_series_id", "n_observed_days", "first_observed",
    "last_observed", "span_days", "max_gap_days", "median_gap_days", "n_reappearance_events",
)


def _require_facts(facts: "pd.DataFrame") -> None:
    required = (*FACTS_SERIES_COLUMNS, *(f"n_pairs_h{k}" for k in HORIZON_DAYS))
    missing = [c for c in required if c not in facts.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")


def turnover_joint(facts: "pd.DataFrame") -> "pd.DataFrame":
    """Phan phoi chung (n_observed_days x max_gap_days) tu toan bo series, khong tu sample."""
    _require_facts(facts)
    if facts.empty:
        columns = [*FACTS_JOINT_COLUMNS, *(f"n_pairs_h{k}" for k in HORIZON_DAYS),
                   *(f"n_series_pair_h{k}" for k in HORIZON_DAYS)]
        return pd.DataFrame(columns=columns)
    frame = facts.copy()
    for k in HORIZON_DAYS:
        frame[f"n_series_pair_h{k}"] = (frame[f"n_pairs_h{k}"] > 0).astype("int64")
    aggregations = {
        "n_series": ("canonical_series_id", "size"),
        "n_reappearance_events": ("n_reappearance_events", "sum"),
        "sum_span_days": ("span_days", "sum"),
    }
    for k in HORIZON_DAYS:
        aggregations[f"n_pairs_h{k}"] = (f"n_pairs_h{k}", "sum")
        aggregations[f"n_series_pair_h{k}"] = (f"n_series_pair_h{k}", "sum")
    return (
        frame.groupby(["n_observed_days", "max_gap_days"], as_index=False).agg(**aggregations)
        .sort_values(["n_observed_days", "max_gap_days"]).reset_index(drop=True)
    )


def turnover_sample_rows(facts: "pd.DataFrame", *, limit: int = 200) -> "pd.DataFrame":
    """Sample deterministic cac series co max gap lon nhat; chi de audit, khong thay metric population."""
    _require_facts(facts)
    return (
        facts.sort_values(
            ["max_gap_days", "n_observed_days", "hotel_id", "checkin_date", "canonical_series_id"],
            ascending=[False, False, True, True, True], kind="stable",
        )
        .head(limit)[list(FACTS_SERIES_COLUMNS)].reset_index(drop=True)
    )


def readiness_by_horizon(facts: "pd.DataFrame", *, horizons: tuple[int, ...] = HORIZON_DAYS) -> "pd.DataFrame":
    """Readiness LY THUYET theo horizon K tu cac dong kind='joint': `theoretical_date_pairs` = tong so ngay t sao cho t+K CUNG duoc quan sat trong cung
    canonical series (dung K ngay, KHONG phai `n_observed_days >= K`), `series_with_pair` = so series co it nhat 1 cap, `n_series` = tong series MAIN;
    `actual_causal_labels` = NULL/`not_available` cho toi Wave B (plan 7.12)."""
    _require_facts(facts)
    n_series = int(len(facts))
    rows = []
    for k in horizons:
        column = f"n_pairs_h{k}"
        if column not in facts.columns:
            raise ValueError(f"thieu cot {column}")
        rows.append({
            "horizon_days": k, "theoretical_date_pairs": int(facts[column].sum()),
            "series_with_pair": int((facts[column] > 0).sum()), "n_series": n_series,
            "actual_causal_labels": pd.NA, "actual_status": "not_available",
        })
    return pd.DataFrame(rows)


def median_gap_days_by_series(presence: "pd.DataFrame") -> "pd.DataFrame":
    """Trung vi khoang cach (ngay) giua 2 ngay quan sat LIEN TIEP cua tung canonical series - CHI cho tap nho (sample audit):
    `presence` gom `canonical_series_id, d` (`canonical_series_id` da ma hoa hotel+check-in+room+rate). Series chi 1 ngay -> NaN (caller dien 0).
    Khong tu dien ngay chua quan sat (plan 7.5: ngay vang chi la `not_observed/turnover_unknown`)."""
    missing = [c for c in ("canonical_series_id", "d") if c not in presence.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    frame = presence.copy()
    frame["d"] = pd.to_datetime(frame["d"])
    frame = frame.sort_values(["canonical_series_id", "d"])
    frame["gap"] = frame.groupby("canonical_series_id")["d"].diff().dt.days
    return frame.groupby("canonical_series_id")["gap"].median().reset_index(name="median_gap_days")


def turnover_by_observed_days(joint: "pd.DataFrame") -> "pd.DataFrame":
    """Marginal theo `n_observed_days` tu bang chung `canonical_series_turnover_joint_main`: n_series, so series co reappearance
    (max_gap_days > 1), tong su kien reappearance, span/ngay trung binh, ty le ngay quan sat/span."""
    required = ("n_observed_days", "max_gap_days", "n_series", "n_reappearance_events", "sum_span_days")
    missing = [c for c in required if c not in joint.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    frame = joint.assign(
        n_series_with_reappearance=lambda d: d["n_series"].where(d["max_gap_days"] > 1, 0),
        sum_observed_days=lambda d: d["n_observed_days"] * d["n_series"],
    )
    grouped = frame.groupby("n_observed_days", as_index=False).agg(
        n_series=("n_series", "sum"), n_series_with_reappearance=("n_series_with_reappearance", "sum"),
        n_reappearance_events=("n_reappearance_events", "sum"), sum_span_days=("sum_span_days", "sum"),
        sum_observed_days=("sum_observed_days", "sum"),
    )
    grouped["mean_span_days"] = grouped["sum_span_days"] / grouped["n_series"].replace(0, np.nan)
    grouped["observed_fraction_of_span"] = grouped["sum_observed_days"] / grouped["sum_span_days"].replace(0, np.nan)
    grouped["reappearance_rate"] = grouped["n_series_with_reappearance"] / grouped["n_series"].replace(0, np.nan)
    return grouped[["n_observed_days", "n_series", "n_series_with_reappearance", "reappearance_rate", "n_reappearance_events",
                    "mean_span_days", "observed_fraction_of_span"]]


def max_gap_distribution(joint: "pd.DataFrame") -> "pd.DataFrame":
    """Marginal theo `max_gap_days`: n_series va ty le tren tong series (denominator = tong n_series cua bang chung)."""
    required = ("max_gap_days", "n_series")
    missing = [c for c in required if c not in joint.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    grouped = joint.groupby("max_gap_days", as_index=False)["n_series"].sum()
    grouped["share_of_series"] = grouped["n_series"] / grouped["n_series"].sum() if grouped["n_series"].sum() else np.nan
    return grouped


def median_gap_distribution(facts: "pd.DataFrame") -> "pd.DataFrame":
    """Population distribution cua median consecutive-day gap (khong dung top-gap sample)."""
    _require_facts(facts)
    if facts.empty:
        return pd.DataFrame(columns=["median_gap_days", "n_series", "share_of_series"])
    grouped = facts.groupby("median_gap_days", as_index=False).size().rename(columns={"size": "n_series"})
    grouped["share_of_series"] = grouped["n_series"] / grouped["n_series"].sum()
    return grouped.sort_values("median_gap_days").reset_index(drop=True)


# ======================================================================== 7.5 protocol continuity
# Dung DUNG ten `exclusion_reason` that trong DB (`app/warehouse/ownership_manifest.py`:
# `exclusion_reason=f"owner_failure_status_{item_status}"`) - khong dat ten rieng roi phai dich qua
# lai, tranh 1 lop chuyen doi khong can thiet co the sai (GPT review 12 eda M5).
PROTOCOL_ITEM_OUTCOMES: tuple[str, ...] = (
    "owner_success", "owner_failure_status_sold_out", "owner_failure_status_not_bookable",
    "owner_failure_status_error",
    # GPT review 12 eda M1: tach "missing_run" phang thanh 2 loai gap khac nhau ve nguyen nhan -
    # missing_source_run (CA NGAY khong co run nao cua nguon do) vs missing_item_in_existing_run (co
    # run ngay do nhung item/hotel nay khong nam trong run) - xem protocol_schedule.classify_outcomes.
    "missing_source_run", "missing_item_in_existing_run",
)


def protocol_continuity(
    scheduled: "pd.DataFrame", *, group_cols: tuple[str, ...] = (),
) -> "pd.DataFrame":
    """Metric protocol continuity (M4 nhom 1) - dung LICH EXPECTED tu ownership schedule (KHAC voi
    `canonical_series_turnover` la khong tu suy expected date). `scheduled`: 1 dong / slot da len lich
    trong ownership manifest cho owner nay, cot bat buoc `outcome` in PROTOCOL_ITEM_OUTCOMES
    ('missing_source_run'/'missing_item_in_existing_run' = ngay co lich nhung khong khop item that -
    phai duoc `protocol_schedule.classify_outcomes` tu tinh bang LEFT JOIN lich voi item that, KHONG
    suy tu day)."""
    if "outcome" not in scheduled.columns:
        raise ValueError("thieu cot bat buoc 'outcome'")
    unknown = set(scheduled["outcome"].unique()) - set(PROTOCOL_ITEM_OUTCOMES)
    if unknown:
        raise ValueError(f"outcome ngoai tap da biet {PROTOCOL_ITEM_OUTCOMES}: {unknown}")
    frame = scheduled.copy()
    keys = list(group_cols)
    if not keys:
        frame["_all"] = "all"
        keys = ["_all"]
    counted = frame.groupby(keys)["outcome"].value_counts().unstack("outcome", fill_value=0)
    for outcome in PROTOCOL_ITEM_OUTCOMES:
        if outcome not in counted.columns:
            counted[outcome] = 0
    counted = counted[list(PROTOCOL_ITEM_OUTCOMES)]
    counted["n_scheduled"] = counted.sum(axis=1)
    result = counted.reset_index()
    if "_all" in result.columns:
        result = result.drop(columns=["_all"])
    return result


def protocol_outcome_rates_by_source_date(classified: "pd.DataFrame") -> "pd.DataFrame":
    """Ty le owner success/failure THEO NGAY VA NGUON (plan 7.2: 'ty le owner success/failure theo ngay va nguon'). Denominator
    = `n_scheduled` (so slot expected cua nguon trong ngay); `n_owner_failure` = 3 loai owner_failure_status_*; `n_missing` = 2 loai
    missing_* (khong co item that). Ba ty le cong lai = 1 (moi slot roi dung 1 nhom)."""
    counts = protocol_continuity(classified, group_cols=("owner_source", "crawl_date"))
    failure_cols = [c for c in PROTOCOL_ITEM_OUTCOMES if c.startswith("owner_failure_status_")]
    missing_cols = [c for c in PROTOCOL_ITEM_OUTCOMES if c.startswith("missing_")]
    out = pd.DataFrame({
        "owner_source": counts["owner_source"], "crawl_date": counts["crawl_date"], "n_scheduled": counts["n_scheduled"],
        "n_owner_success": counts["owner_success"], "n_owner_failure": counts[failure_cols].sum(axis=1),
        "n_missing": counts[missing_cols].sum(axis=1),
    })
    denominator = out["n_scheduled"].replace(0, np.nan)
    out["owner_success_rate"] = out["n_owner_success"] / denominator
    out["owner_failure_rate"] = out["n_owner_failure"] / denominator
    out["missing_rate"] = out["n_missing"] / denominator
    return out.sort_values(["owner_source", "crawl_date"]).reset_index(drop=True)


# ======================================================================== 7.3 finish-hour + ngay bat thuong (GPT review 12 file 11 muc 5, plan 7.3)
def finish_hour_distribution(run_duration: "pd.DataFrame") -> "pd.DataFrame":
    """`run_duration_and_throughput` (co san `finished_at_vn`) -> 1 dong / (source_code,
    finish_hour_vn): so run hoan thanh trong dung gio VN do (plan 7.3: "thoi diem hoan thanh theo gio
    Viet Nam")."""
    required = ("source_code", "finished_at_vn")
    missing = [c for c in required if c not in run_duration.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    frame = run_duration.copy()
    frame["finish_hour_vn"] = pd.to_datetime(frame["finished_at_vn"]).dt.hour
    return frame.groupby(["source_code", "finish_hour_vn"]).size().reset_index(name="n_runs")


_ANOMALY_RATE_COLUMNS = ("error_rate", "sold_out_rate", "not_bookable_rate")


def daily_operational_anomaly_flags(
    day_counts: "pd.DataFrame", run_duration: "pd.DataFrame", *, z_threshold: float = 2.0,
) -> "pd.DataFrame":
    """1 dong / (source_code, vn_crawl_date): duration (tong phut cac run trong ngay), ty le error/sold_out/not_bookable (item RAW) va
    co bat thuong = z-score theo CHINH phan phoi cua nguon do >= `z_threshold` (plan 7.3: 'ngay co duration/error/block/sold-out bat
    thuong'). CHI FLAG - khong loc/xoa ngay nao va khong ket luan nguyen nhan (block/CAPTCHA co bang error-code rieng:
    `run_day_error_code_counts_raw`). std = 0 hoac < 3 ngay/nguon -> khong danh dau (khong du bang chung thong ke)."""
    for name, frame, required in (
        ("day_counts", day_counts, ("source_code", "vn_crawl_date", "n_items", "n_sold_out", "n_not_bookable", "n_error")),
        ("run_duration", run_duration, ("source_code", "vn_crawl_date", "duration_minutes")),
    ):
        missing = [c for c in required if c not in frame.columns]
        if missing:
            raise ValueError(f"{name} thieu cot bat buoc {missing}")
    duration_by_day = run_duration.groupby(["source_code", "vn_crawl_date"], as_index=False).agg(
        n_runs=("duration_minutes", "size"), duration_minutes=("duration_minutes", "sum"))
    merged = day_counts.merge(duration_by_day, on=["source_code", "vn_crawl_date"], how="left")
    denominator = merged["n_items"].replace(0, np.nan)
    merged["error_rate"] = merged["n_error"] / denominator
    merged["sold_out_rate"] = merged["n_sold_out"] / denominator
    merged["not_bookable_rate"] = merged["n_not_bookable"] / denominator
    flag_sources = {"duration_minutes": "is_duration_anomalous", "error_rate": "is_error_rate_anomalous",
                    "sold_out_rate": "is_sold_out_rate_anomalous", "not_bookable_rate": "is_not_bookable_rate_anomalous"}
    for column, flag in flag_sources.items():
        grouped = merged.groupby("source_code")[column]
        std = grouped.transform("std").replace(0, np.nan)
        enough_days = grouped.transform("count") >= 3
        z = (merged[column] - grouped.transform("mean")) / std
        merged[f"{column}_z"] = z.where(enough_days)
        merged[flag] = (merged[f"{column}_z"].abs() >= z_threshold).fillna(False).astype(bool)
    merged["is_any_anomalous"] = merged[list(flag_sources.values())].any(axis=1)
    return merged.sort_values(["source_code", "vn_crawl_date"]).reset_index(drop=True)


# ======================================================================== 7.4 cohort attrition (GPT review 12 file 11 muc 5, plan 7.4)
def cohort_attrition_table(cohort_versions: list[dict]) -> "pd.DataFrame":
    """`cohort_versions`: danh sach dict tu `cohort_history_*.json["versions"]` (cot bat buoc
    `cohort_version`, `effective_from_crawl_date`, `size`) - tra ve THEM cot `size_change`/
    `change_type` so voi version LIEN TRUOC theo thu tu hieu luc (plan 7.4: "cohort attrition theo
    version; Mac Valley duoc ghi nhan dung truoc khi roi cohort" - CLAUDE.md muc 2 mo ta chinh xac day
    la attrition tu nhien, khong phai thay the). Version dau tien khong co truoc do ->
    `change_type='baseline'`."""
    if not cohort_versions:
        raise ValueError("cohort_versions rong")
    required = ("cohort_version", "effective_from_crawl_date", "size")
    missing = [c for c in required if c not in cohort_versions[0]]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    frame = pd.DataFrame(cohort_versions).sort_values("effective_from_crawl_date").reset_index(drop=True)
    frame["size_change"] = frame["size"].diff().fillna(0).astype(int)
    frame["change_type"] = "baseline"
    later = frame.index > 0
    frame.loc[later, "change_type"] = np.select(
        [frame.loc[later, "size_change"] < 0, frame.loc[later, "size_change"] > 0],
        ["attrition", "addition"], default="unchanged",
    )
    return frame


# ======================================================================== 7.2/7.11 collision / source divergence (GPT review 12 file 11 muc 3)
_TIME_DIFF_BUCKETS: tuple[tuple[float, float | None, str], ...] = (
    (0, 5, "0-5"), (6, 15, "6-15"), (16, 60, "16-60"), (61, None, "61+"),
)
TIME_DIFF_BUCKET_ORDER = tuple(label for _lo, _hi, label in _TIME_DIFF_BUCKETS)


def time_diff_minutes_bucket(minutes: float) -> str:
    """Bucket phut (GPT review 12 eda file 11 muc 3.3: "Stratify toi thieu: 0-5, 6-15, 16-60, >60
    phut" - dung cho collision/source divergence audit). Bien phut LIEN TUC: (5, 6) roi vao '6-15' khi lam tron len - dung
    quy uoc `floor`: <6 -> '0-5', <16 -> '6-15', <61 -> '16-60', con lai '61+'."""
    if minutes < 0:
        raise ValueError(f"minutes={minutes} am - khong hop le")
    if minutes < 6:
        return "0-5"
    if minutes < 16:
        return "6-15"
    if minutes < 61:
        return "16-60"
    return "61+"


def time_diff_minutes_bucket_series(minutes: "pd.Series") -> "pd.Series":
    if (minutes < 0).any():
        raise ValueError("co gia tri phut < 0 - kiem tra du lieu nguon truoc khi bucket")
    return minutes.map(time_diff_minutes_bucket).astype(
        pd.CategoricalDtype(categories=TIME_DIFF_BUCKET_ORDER, ordered=True)
    )


def collision_item_status_concordance(item_pairs: "pd.DataFrame") -> "pd.DataFrame":
    """Bang concordance/disagreement cua terminal item status giua 2 nguon (GPT review 12 eda file 11
    muc 3.2: "bang concordance/disagreement cua terminal item status"). 1 dong / (status_a, status_b) -
    duong cheo (status_a==status_b) la concordant, ngoai duong cheo la disagreement."""
    required = ("status_a", "status_b")
    missing = [c for c in required if c not in item_pairs.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    if item_pairs.empty:
        return pd.DataFrame(columns=["status_a", "status_b", "n_pairs", "is_concordant"])
    out = item_pairs.groupby(["status_a", "status_b"]).size().reset_index(name="n_pairs")
    out["is_concordant"] = out["status_a"] == out["status_b"]
    return out


def collision_item_summary(item_pairs: "pd.DataFrame") -> "pd.DataFrame":
    """1 dong: DENOMINATOR RIENG cho tung lop (GPT file 11 muc 3: khong tron lan). `n_collision_item_pairs` (mau so item-level),
    `n_status_concordant`/`n_status_disagreement` (tren cung mau so do), `n_success_success_pairs` (mau so cua option-level)."""
    required = ("status_a", "status_b")
    missing = [c for c in required if c not in item_pairs.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    n_pairs = len(item_pairs)
    concordant = int((item_pairs["status_a"] == item_pairs["status_b"]).sum()) if n_pairs else 0
    success_success = int(((item_pairs["status_a"] == "success") & (item_pairs["status_b"] == "success")).sum()) if n_pairs else 0
    return pd.DataFrame([{
        "n_collision_item_pairs": n_pairs, "n_status_concordant": concordant, "n_status_disagreement": n_pairs - concordant,
        "status_disagreement_rate": ((n_pairs - concordant) / n_pairs) if n_pairs else np.nan,
        "n_success_success_pairs": success_success,
    }])


def collision_item_time_diff_stratification(item_pairs: "pd.DataFrame") -> "pd.DataFrame":
    """Stratify CHENH LECH thoi gian hoan thanh item (`finished_at`) giua 2 nguon theo bucket phut
    (GPT review 12 eda file 11 muc 3.3: "thoi gian la bien audit bat buoc" - "gia khac nhau do thoi
    diem cao khac nhau KHONG tu dong la loi parser"). Denominator: collision item-pairs."""
    required = ("observed_finish_a", "observed_finish_b")
    missing = [c for c in required if c not in item_pairs.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    if item_pairs.empty:
        return pd.DataFrame({"time_diff_bucket": list(TIME_DIFF_BUCKET_ORDER), "n_pairs": [0] * len(TIME_DIFF_BUCKET_ORDER)})
    diff_minutes = (
        (pd.to_datetime(item_pairs["observed_finish_a"]) - pd.to_datetime(item_pairs["observed_finish_b"]))
        .abs().dt.total_seconds() / 60.0
    )
    bucketed = time_diff_minutes_bucket_series(diff_minutes)
    return (
        bucketed.value_counts().reindex(TIME_DIFF_BUCKET_ORDER, fill_value=0)
        .rename_axis("time_diff_bucket").reset_index(name="n_pairs")
    )


def collision_option_summary(option_detail: "pd.DataFrame") -> "pd.DataFrame":
    """1 dong tong hop tren SHARED CANONICAL OPTION-PAIRS (denominator RIENG `n_option_pairs`, khong phai item-pairs): exact price
    match, chenh tuyet doi/tuong doi (VND, ty le doi xung) va concordance cua currency/breakfast/free-cancellation/tax inclusion
    (GPT file 11 muc 3.2). Gia khac khi thoi diem cao khac nhau KHONG tu dong la loi parser - xem stratification theo phut."""
    required = (
        "price_abs_diff", "price_relative_diff", "currency_concordant",
        "breakfast_included_concordant", "free_cancellation_concordant",
        "cancellation_policy_concordant", "price_includes_tax_concordant",
        "taxes_fees_state", "taxes_fees_concordant", "taxes_fees_abs_diff",
    )
    missing = [c for c in required if c not in option_detail.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    n = len(option_detail)
    if n == 0:
        return pd.DataFrame([{
            "n_option_pairs": 0, "n_exact_price_match": 0, "exact_price_match_rate": np.nan,
            "median_price_abs_diff": np.nan, "mean_price_abs_diff": np.nan, "max_price_abs_diff": np.nan,
            "median_price_relative_diff": np.nan, "mean_price_relative_diff": np.nan, "max_price_relative_diff": np.nan,
            "currency_concordance_rate": np.nan, "breakfast_included_concordance_rate": np.nan,
            "free_cancellation_concordance_rate": np.nan, "cancellation_policy_concordance_rate": np.nan,
            "price_includes_tax_concordance_rate": np.nan, "taxes_fees_concordance_rate": np.nan,
            "n_taxes_both_null": 0, "n_taxes_one_null": 0, "n_taxes_both_present": 0,
            "median_taxes_fees_abs_diff": np.nan, "mean_taxes_fees_abs_diff": np.nan,
            "max_taxes_fees_abs_diff": np.nan,
        }])
    absolute = option_detail["price_abs_diff"].astype(float)
    relative = option_detail["price_relative_diff"].astype(float)
    exact = int((absolute == 0).sum())
    tax_states = option_detail["taxes_fees_state"].value_counts()
    tax_present = option_detail.loc[
        option_detail["taxes_fees_state"] == "both_present", "taxes_fees_abs_diff"
    ].astype(float)
    return pd.DataFrame([{
        "n_option_pairs": n, "n_exact_price_match": exact, "exact_price_match_rate": exact / n,
        "median_price_abs_diff": absolute.median(), "mean_price_abs_diff": absolute.mean(), "max_price_abs_diff": absolute.max(),
        "median_price_relative_diff": relative.median(), "mean_price_relative_diff": relative.mean(),
        "max_price_relative_diff": relative.max(),
        "currency_concordance_rate": float(option_detail["currency_concordant"].astype(bool).mean()),
        "breakfast_included_concordance_rate": float(option_detail["breakfast_included_concordant"].astype(bool).mean()),
        "free_cancellation_concordance_rate": float(option_detail["free_cancellation_concordant"].astype(bool).mean()),
        "cancellation_policy_concordance_rate": float(option_detail["cancellation_policy_concordant"].astype(bool).mean()),
        "price_includes_tax_concordance_rate": float(option_detail["price_includes_tax_concordant"].astype(bool).mean()),
        # Overall NULL-safe concordance is useful, but the three state counts are published as
        # its denominator audit so a high score caused by both sources omitting taxes is visible.
        "taxes_fees_concordance_rate": float(option_detail["taxes_fees_concordant"].astype(bool).mean()),
        "n_taxes_both_null": int(tax_states.get("both_null", 0)),
        "n_taxes_one_null": int(tax_states.get("one_null", 0)),
        "n_taxes_both_present": int(tax_states.get("both_present", 0)),
        "median_taxes_fees_abs_diff": tax_present.median() if len(tax_present) else np.nan,
        "mean_taxes_fees_abs_diff": tax_present.mean() if len(tax_present) else np.nan,
        "max_taxes_fees_abs_diff": tax_present.max() if len(tax_present) else np.nan,
    }])


def collision_option_time_diff_stratification(option_detail: "pd.DataFrame") -> "pd.DataFrame":
    """Nhu tren nhung o OPTION grain (`observed_at_diff_minutes` da tinh san trong
    `queries.collision_option_analysis`), kem ty le exact price match THEO tung bucket thoi gian -
    de thay ro "gia khac o bucket thoi gian xa hon" khac voi "gia khac dot ngot cung 1 thoi diem" (GPT
    review 12 eda file 11 muc 3.3). Mau so RIENG cua bang nay la shared canonical option-pairs, KHONG
    phai collision item-pairs hay success-success item-pairs (muc 3: "moi ty le cong bo DUNG mau so
    cua chinh no")."""
    required = ("observed_at_diff_minutes", "price_abs_diff")
    missing = [c for c in required if c not in option_detail.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    columns = ["time_diff_bucket", "n_option_pairs", "n_exact_price_match", "exact_price_match_rate"]
    if option_detail.empty:
        return pd.DataFrame({
            "time_diff_bucket": list(TIME_DIFF_BUCKET_ORDER), "n_option_pairs": [0] * len(TIME_DIFF_BUCKET_ORDER),
            "n_exact_price_match": [0] * len(TIME_DIFF_BUCKET_ORDER),
            "exact_price_match_rate": [np.nan] * len(TIME_DIFF_BUCKET_ORDER),
        })[columns]
    frame = option_detail.copy()
    frame["time_diff_bucket"] = time_diff_minutes_bucket_series(frame["observed_at_diff_minutes"].astype(float))
    grouped = frame.groupby("time_diff_bucket", observed=True).agg(
        n_option_pairs=("price_abs_diff", "size"),
        n_exact_price_match=("price_abs_diff", lambda s: int((s == 0).sum())),
    ).reindex(TIME_DIFF_BUCKET_ORDER, fill_value=0).rename_axis("time_diff_bucket").reset_index()
    grouped["exact_price_match_rate"] = grouped["n_exact_price_match"] / grouped["n_option_pairs"].replace(0, np.nan)
    return grouped[columns]


def collision_option_coverage_summary(pair_coverage: "pd.DataFrame") -> "pd.DataFrame":
    """1 dong tong hop intersection/union cua tap canonical key tren cap success-success (denominator: `n_success_success_pairs`):
    ty le cap trung khop hoan toan (jaccard=1), khong giao nhau (jaccard=0), jaccard trung binh/trung vi, tong so key chung bi loai
    vi lap trong item (`n_ambiguous_shared_keys`)."""
    required = ("option_jaccard", "n_shared_options", "n_union_options", "n_ambiguous_shared_keys", "n_duplicate_canonical_keys")
    missing = [c for c in required if c not in pair_coverage.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    n = len(pair_coverage)
    if n == 0:
        return pd.DataFrame([{
            "n_success_success_pairs": 0, "mean_option_jaccard": np.nan, "median_option_jaccard": np.nan,
            "n_pairs_identical_option_sets": 0, "n_pairs_disjoint_option_sets": 0,
            "total_shared_options": 0, "total_union_options": 0, "total_ambiguous_shared_keys": 0,
            "total_duplicate_canonical_keys": 0,
        }])
    jaccard = pair_coverage["option_jaccard"].astype(float)
    return pd.DataFrame([{
        "n_success_success_pairs": n, "mean_option_jaccard": jaccard.mean(), "median_option_jaccard": jaccard.median(),
        "n_pairs_identical_option_sets": int((jaccard == 1.0).sum()), "n_pairs_disjoint_option_sets": int((jaccard == 0.0).sum()),
        "total_shared_options": int(pair_coverage["n_shared_options"].sum()),
        "total_union_options": int(pair_coverage["n_union_options"].sum()),
        "total_ambiguous_shared_keys": int(pair_coverage["n_ambiguous_shared_keys"].sum()),
        "total_duplicate_canonical_keys": int(pair_coverage["n_duplicate_canonical_keys"].sum()),
    }])
