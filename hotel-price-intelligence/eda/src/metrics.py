"""Metric tinh toan THUAN pandas, khong cham MySQL (EDA_CURATED_PLAN.md muc 6-7).

`queries.py` lay du lieu tu warehouse (dung scope/grain) roi truyen DataFrame vao day. Tach rieng de
test nhanh, khong phu thuoc du lieu that - moi bug logic grain/denominator (vd GPT review 12 file 03
M2/M3/M4) deu phai bat duoc bang synthetic DataFrame nho, khong can cho warehouse that.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ======================================================================== 7.6 lead-time bucket
LEAD_TIME_BUCKETS: tuple[tuple[int, int | None, str], ...] = (
    (0, 0, "0"), (1, 3, "1-3"), (4, 7, "4-7"), (8, 14, "8-14"),
    (15, 30, "15-30"), (31, 60, "31-60"), (61, None, "61+"),
)
LEAD_TIME_BUCKET_ORDER = tuple(label for _lo, _hi, label in LEAD_TIME_BUCKETS)

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


def lead_time_bucket_series(lead_time: "pd.Series") -> "pd.Series":
    if (lead_time < 0).any():
        bad = lead_time[lead_time < 0]
        raise ValueError(f"co {len(bad)} lead_time < 0 - kiem tra du lieu nguon truoc khi bucket")
    return lead_time.map(lead_time_bucket).astype(
        pd.CategoricalDtype(categories=LEAD_TIME_BUCKET_ORDER, ordered=True)
    )


# ======================================================================== 7.8 item-grain availability (GPT review 12 file 03 M2)
# Snapshot warehouse_20260916_2src quan sat duoc 4/5 status terminal (0 'partial') - van dang ky ca 5
# de khong vo trong im lang neu batch sau co 'partial' (muc 5 quy tac cua GPT o file 05 §2).
TERMINAL_ITEM_STATUSES: tuple[str, ...] = ("success", "sold_out", "not_bookable", "partial", "error")


def item_availability_rates(items: "pd.DataFrame", *, group_cols: tuple[str, ...] = ()) -> "pd.DataFrame":
    """Primary availability rate O GRAIN ITEM (GPT review 12 M2) - khong dung so observation lam
    denominator, vi 1 item success co the co 1-138 observation (option phong khac nhau) con 1 item
    sold_out chi co dung 1 sentinel observation; tinh o grain observation se lam sold-out rate bi pha
    loang sai.

    `items`: 1 DONG / ITEM (khong phai observation), cot bat buoc `status` in TERMINAL_ITEM_STATUSES.
    Tra ve 1 dong / nhom (`group_cols` rong -> 1 dong duy nhat) voi dem tung status, `n_items`, va
    `<status>_rate` = dem / n_items.
    """
    if "status" not in items.columns:
        raise ValueError("thieu cot bat buoc 'status'")
    unknown = set(items["status"].unique()) - set(TERMINAL_ITEM_STATUSES)
    if unknown:
        raise ValueError(
            f"status ngoai tap terminal da biet {TERMINAL_ITEM_STATUSES}: {unknown} - item chua "
            f"terminal (queued/running) lot vao day? Warehouse PASS phai co 0 item non-terminal."
        )

    frame = items.copy()
    keys = list(group_cols)
    if not keys:
        frame["_all"] = "all"
        keys = ["_all"]

    counted = frame.groupby(keys)["status"].value_counts().unstack("status", fill_value=0)
    for status in TERMINAL_ITEM_STATUSES:
        if status not in counted.columns:
            counted[status] = 0
    counted = counted[list(TERMINAL_ITEM_STATUSES)]
    counted["n_items"] = counted.sum(axis=1)
    for status in TERMINAL_ITEM_STATUSES:
        counted[f"{status}_rate"] = counted[status] / counted["n_items"]
    result = counted.reset_index()
    if "_all" in result.columns:
        result = result.drop(columns=["_all"])
    return result


def status_present_report(items: "pd.DataFrame") -> dict[str, int]:
    """Bao dung status nao co mat (ke ca 0 dong) - de khong bao gio am tham bo qua 1 status terminal
    (muc 7.8: "Snapshot hien tai co partial=0 nhung van phai bao ro va code khong duoc am tham bo status
    nay khi batch sau xuat hien")."""
    counts = items["status"].value_counts().to_dict()
    return {status: int(counts.get(status, 0)) for status in TERMINAL_ITEM_STATUSES}


# ======================================================================== 7.10 full-history reference (GPT review 12 file 03 M3, file 09 muc 2, file 11 muc 4)
def _rate_from_counts(
    aggregated: "pd.DataFrame", *, n_col: str, n_true_col: str, rate_col: str,
) -> "pd.DataFrame":
    """Helper PRIVATE dung chung cho cac metric dang "ty le tu bang DA AGGREGATE SAN" (GPT review 12
    eda file 11 muc 4: khong con nap ca trieu dong observation vao RAM chi de GROUP BY trong pandas -
    `queries.py` da GROUP BY thang trong SQL, ham nay chi tinh ty le tren ket qua nho da co san). TEN
    COT/CONTRACT cong khai van phai khac nhau giua 2 ham public ben duoi (GPT file 09 muc 2) de khong
    the vo tinh dan nhan sai y nghia (vd goi ket qua ban long la "exact match")."""
    missing = [c for c in (n_col, n_true_col) if c not in aggregated.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    result = aggregated.copy()
    result[rate_col] = result[n_true_col] / result[n_col]
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


def item_level_exact_reference_availability(
    items: "pd.DataFrame", *, group_cols: tuple[str, ...] = (),
) -> "pd.DataFrame":
    """Metric 2 (M3): ITEM-grain exact-reference availability.

    `items`: 1 dong / success MAIN item THUOC (hotel_id, checkin_date) CO reference approved (day da
    la denominator - queries.py phai loc dung dieu kien nay TRUOC khi goi ham nay). Cot bat buoc
    `has_matching_option` (bool - True neu item co it nhat 1 observation khop dung key approved).
    """
    if "has_matching_option" not in items.columns:
        raise ValueError("thieu cot bat buoc 'has_matching_option'")
    frame = items.copy()
    keys = list(group_cols)
    if not keys:
        frame["_all"] = "all"
        keys = ["_all"]
    grouped = frame.groupby(keys).agg(
        n_items=("has_matching_option", "size"),
        n_items_matched=("has_matching_option", "sum"),
    ).reset_index()
    grouped["availability_rate"] = grouped["n_items_matched"] / grouped["n_items"]
    if "_all" in grouped.columns:
        grouped = grouped.drop(columns=["_all"])
    return grouped


# ======================================================================== 7.7 price distribution
PRICE_PERCENTILES: tuple[float, ...] = (0.01, 0.05, 0.50, 0.75, 0.95, 0.99)


def price_distribution_stats(observations: "pd.DataFrame", *, group_cols: tuple[str, ...] = ()) -> "pd.DataFrame":
    """Distribution O GRAIN OBSERVATION (moi room option 1 dong) - tra loi "gia cac room option da thu
    duoc", KHONG phai "gia khach san trung binh" (muc 7.7). `observations` phai da loc is_sold_out=False
    va gia hop le (khong am/0) truoc khi goi.
    """
    if "price_per_night" not in observations.columns:
        raise ValueError("thieu cot bat buoc 'price_per_night'")
    frame = observations.copy()
    keys = list(group_cols)
    if not keys:
        frame["_all"] = "all"
        keys = ["_all"]

    def _agg(series: "pd.Series") -> "pd.Series":
        data = {"count": series.count(), "min": series.min(), "max": series.max(), "mean": series.mean()}
        for q in PRICE_PERCENTILES:
            data[f"p{int(q * 100)}"] = series.quantile(q)
        return pd.Series(data)

    result = frame.groupby(keys)["price_per_night"].apply(_agg).unstack()
    result = result.reset_index()
    if "_all" in result.columns:
        result = result.drop(columns=["_all"])
    return result


def price_sensitivity_by_series(
    observations: "pd.DataFrame", *, agg: str = "median",
) -> "pd.DataFrame":
    """Bang sensitivity O GRAIN (hotel_id, checkin_date, vn_observation_date) (muc 7.7) - 1 gia tri
    tong hop / series-ngay, de hotel co nhieu option (vd 138) khong lan at hotel co it option. KHONG
    thay the `price_distribution_stats`, chi la bang doi chieu rieng."""
    required = ("hotel_id", "checkin_date", "vn_observation_date", "price_per_night")
    missing = [c for c in required if c not in observations.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    if agg not in ("median", "min"):
        raise ValueError(f"agg={agg!r} phai la 'median' hoac 'min' (dinh nghia truoc, muc 7.7)")
    grouped = observations.groupby(["hotel_id", "checkin_date", "vn_observation_date"]).agg(
        price_per_night=("price_per_night", agg), n_options=("price_per_night", "size"),
    ).reset_index()
    return grouped


# ======================================================================== 7.5 continuity / turnover (GPT review 12 file 03 M4)
def canonical_series_turnover(
    daily_presence: "pd.DataFrame",
) -> "pd.DataFrame":
    """Metric turnover (M4 nhom 2) O GRAIN (hotel_id, checkin_date, canonical_series_id) - CHI mo ta
    ngay observed, KHONG suy missing. `daily_presence`: 1 dong / (series, vn_observation_date) da
    quan sat that su (KHONG duoc dien them ngay chua quan sat vao day - ham nay khong tao "expected
    date" nao, chi tong hop tren du lieu da co).
    """
    required = ("hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date")
    missing = [c for c in required if c not in daily_presence.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")

    def _summarize(group: "pd.DataFrame") -> "pd.Series":
        dates = sorted(group["vn_observation_date"].unique())
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        return pd.Series({
            "n_observed_days": len(dates), "first_observed": dates[0], "last_observed": dates[-1],
            "max_gap_days": max(gaps) if gaps else 0,
            "median_gap_days": float(np.median(gaps)) if gaps else 0.0,
        })

    return (
        daily_presence.groupby(["hotel_id", "checkin_date", "canonical_series_id"])
        .apply(_summarize, include_groups=False).reset_index()
    )


# Dung DUNG ten `exclusion_reason` that trong DB (`app/warehouse/ownership_manifest.py`:
# `exclusion_reason=f"owner_failure_status_{item_status}"`) - khong dat ten rieng roi phai dich qua
# lai, tranh 1 lop chuyen doi khong can thiet co the sai (GPT review 12 eda M5).
HORIZON_DAYS: tuple[int, ...] = (1, 3, 7, 14)


def theoretical_horizon_pairs(
    daily_presence: "pd.DataFrame", *, horizons: tuple[int, ...] = HORIZON_DAYS,
) -> "pd.DataFrame":
    """7.12 - GPT review 12 eda M4: sua thuat toan sai truoc do (`n_observed_days >= K`).

    "co cap ly thuyet cho horizon K" NGHIA LA co 2 ngay quan sat CACH DUNG K NGAY, khong phai "co it
    nhat K ngay quan sat bat ky". Vi du observed = {d0, d1, d5}: horizon 3 phai ra 0 cap (d0+3=d3
    khong quan sat, d1+3=d4 khong quan sat, d5+3=d8 khong quan sat) DU `n_observed_days=3 >= 3`.

    Tra ve 1 dong / (series, horizon_days): `source_pair_count` (so ngay t ma t+K CUNG duoc quan sat).
    """
    required = ("hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date")
    missing = [c for c in required if c not in daily_presence.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")

    rows = []
    for (hotel_id, checkin_date, series_id), group in daily_presence.groupby(
        ["hotel_id", "checkin_date", "canonical_series_id"]
    ):
        observed = set(group["vn_observation_date"])
        for horizon in horizons:
            delta = pd.Timedelta(days=horizon)
            pair_count = sum(1 for d in observed if (pd.Timestamp(d) + delta).date() in observed)
            rows.append({
                "hotel_id": hotel_id, "checkin_date": checkin_date, "canonical_series_id": series_id,
                "horizon_days": horizon, "source_pair_count": pair_count,
            })
    return pd.DataFrame(rows, columns=[
        "hotel_id", "checkin_date", "canonical_series_id", "horizon_days", "source_pair_count",
    ])


def dataset_readiness_by_horizon(pairs: "pd.DataFrame") -> "pd.DataFrame":
    """Tong hop `theoretical_horizon_pairs` ve 1 dong/horizon (muc 7.12: bang
    `dataset_readiness_by_horizon.csv` voi 2 cot tach biet ly thuyet/that)."""
    required = ("horizon_days", "source_pair_count", "hotel_id", "checkin_date", "canonical_series_id")
    missing = [c for c in required if c not in pairs.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    grouped = pairs.groupby("horizon_days").agg(
        theoretical_date_pairs=("source_pair_count", "sum"),
        series_with_pair=("source_pair_count", lambda s: int((s > 0).sum())),
        n_series=("source_pair_count", "size"),
    ).reset_index()
    grouped["actual_causal_labels"] = pd.NA
    grouped["actual_status"] = "not_available"
    return grouped


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


# ======================================================================== 7.8 not_bookable rate theo crawl_date + hotel (GPT review 12 file 11 muc 5, plan 7.8)
def not_bookable_rate_by_crawl_date_hotel(items: "pd.DataFrame") -> "pd.DataFrame":
    """`items`: `main_item_status` (co san cot `crawl_date`) -> 1 dong / (crawl_date, hotel_id): ty le
    item `not_bookable` trong dung ngay/hotel do (plan 7.8: "not_bookable rate theo crawl date va
    hotel")."""
    required = ("crawl_date", "hotel_id", "status")
    missing = [c for c in required if c not in items.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    grouped = items.groupby(["crawl_date", "hotel_id"]).agg(
        n_items=("status", "size"),
        n_not_bookable=("status", lambda s: int((s == "not_bookable").sum())),
    ).reset_index()
    grouped["not_bookable_rate"] = grouped["n_not_bookable"] / grouped["n_items"]
    return grouped


# ======================================================================== 7.3 finish-hour + anomaly flagging (GPT review 12 file 11 muc 5, plan 7.3)
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


def anomalous_crawl_days(run_duration: "pd.DataFrame", *, z_threshold: float = 2.0) -> "pd.DataFrame":
    """Them cot flag (KHONG loc/xoa run nao) danh dau `duration_minutes` LECH BAT THUONG so voi CHINH
    phan phoi cua nguon do (z-score tren mean/std cua chinh source - plan 7.3: "ngay co duration/error/
    block/sold-out bat thuong"). Dung cho quality_findings/report, khong anh huong cac metric khac."""
    required = ("source_code", "duration_minutes")
    missing = [c for c in required if c not in run_duration.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    frame = run_duration.copy()
    grouped = frame.groupby("source_code")["duration_minutes"]
    mean = grouped.transform("mean")
    std = grouped.transform("std").replace(0, np.nan)
    frame["duration_z_score"] = (frame["duration_minutes"] - mean) / std
    frame["is_duration_anomalous"] = (frame["duration_z_score"].abs() >= z_threshold).fillna(False)
    return frame


# ======================================================================== 7.7/7.11 robust within-hotel price outlier + dispersion (GPT review 12 file 11 muc 5, plan 7.7/7.11)
def robust_price_outliers(price_observations: "pd.DataFrame", *, mad_multiplier: float = 5.0) -> "pd.DataFrame":
    """Flag (KHONG xoa) observation co gia lech xa median CUA CHINH hotel do, dung Median Absolute
    Deviation (MAD) - ben vung hon mean/std truoc chinh outlier no dinh nghia (plan 7.11: "outlier
    price theo robust within-hotel rule, chi flag chu khong xoa").

    Cot bat buoc `hotel_id`, `price_per_night`. Tra ve DataFrame RIENG (CUNG index voi
    `price_observations` dau vao, KHONG copy toan bo cac cot khac cua no) chi gom `hotel_median_price`,
    `price_mad`, `price_robust_z`, `is_price_outlier` - caller tu gan lai dung cot can (vd
    `price_main["is_price_outlier"] = robust_price_outliers(price_main)["is_price_outlier"]`) de tranh
    nhan doi RAM tren frame lon (GPT review 12 eda file 11 muc 4: "khong keo full observation frame").
    Hotel co <5 observation KHONG du bang chung thong ke - `is_price_outlier` luon False cho nhom do
    (tranh MAD~0 lam z-score no ao)."""
    required = ("hotel_id", "price_per_night")
    missing = [c for c in required if c not in price_observations.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    grouped = price_observations.groupby("hotel_id")["price_per_night"]
    hotel_median_price = grouped.transform("median")
    abs_dev = (price_observations["price_per_night"] - hotel_median_price).abs()
    price_mad = abs_dev.groupby(price_observations["hotel_id"]).transform("median")
    n_per_hotel = grouped.transform("size")
    # He so 1.4826 quy MAD ve thang tuong duong std cho phan phoi chuan (quy uoc thong ke chuan).
    robust_std = (price_mad * 1.4826).replace(0, np.nan)
    price_robust_z = abs_dev / robust_std
    is_price_outlier = ((n_per_hotel >= 5) & (price_robust_z >= mad_multiplier)).fillna(False)
    return pd.DataFrame({
        "hotel_median_price": hotel_median_price, "price_mad": price_mad,
        "price_robust_z": price_robust_z, "is_price_outlier": is_price_outlier,
    })


def hotel_price_dispersion(price_observations: "pd.DataFrame", *, min_observations: int = 5) -> "pd.DataFrame":
    """1 dong / hotel_id CO IT NHAT `min_observations` observation (plan 7.7: "hotel-level dispersion
    cho cac hotel du so observation") - median/mean/std/coefficient of variation. Hotel qua it du lieu
    bi LOAI KHOI bang nay (khong phai bi gan gia tri 0/NaN gay hieu nham) de tranh dispersion "ao" tu
    1-2 diem."""
    required = ("hotel_id", "price_per_night")
    missing = [c for c in required if c not in price_observations.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    grouped = price_observations.groupby("hotel_id")["price_per_night"].agg(
        n_observations="size", median="median", mean="mean", std="std",
    ).reset_index()
    grouped = grouped[grouped["n_observations"] >= min_observations].copy()
    grouped["coefficient_of_variation"] = grouped["std"] / grouped["mean"]
    return grouped


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
    phut" - dung cho collision/source divergence audit)."""
    if minutes < 0:
        raise ValueError(f"minutes={minutes} am - khong hop le")
    for lo, hi, label in _TIME_DIFF_BUCKETS:
        if minutes >= lo and (hi is None or minutes <= hi):
            return label
    raise AssertionError(f"khong bucket duoc minutes={minutes}")


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
        return pd.DataFrame(columns=["status_a", "status_b", "n_pairs"])
    return item_pairs.groupby(["status_a", "status_b"]).size().reset_index(name="n_pairs")


def collision_item_time_diff_stratification(item_pairs: "pd.DataFrame") -> "pd.DataFrame":
    """Stratify CHENH LECH thoi gian hoan thanh item (`finished_at`) giua 2 nguon theo bucket phut
    (GPT review 12 eda file 11 muc 3.3: "thoi gian la bien audit bat buoc" - "gia khac nhau do thoi
    diem cao khac nhau KHONG tu dong la loi parser")."""
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


def collision_option_time_diff_stratification(option_detail: "pd.DataFrame") -> "pd.DataFrame":
    """Nhu tren nhung o OPTION grain (`observed_at_diff_minutes` da tinh san trong
    `queries.collision_option_level_detail`), kem ty le exact price match THEO tung bucket thoi gian -
    de thay ro "gia khac o bucket thoi gian xa hon" khac voi "gia khac dot ngot cung 1 thoi diem" (GPT
    review 12 eda file 11 muc 3.3). Mau so RIENG cua bang nay la shared canonical option-pairs, KHONG
    phai collision item-pairs hay success-success item-pairs (muc 3: "moi ty le cong bo DUNG mau so
    cua chinh no")."""
    required = ("observed_at_diff_minutes", "price_abs_diff")
    missing = [c for c in required if c not in option_detail.columns]
    if missing:
        raise ValueError(f"thieu cot bat buoc {missing}")
    if option_detail.empty:
        return pd.DataFrame(columns=[
            "time_diff_bucket", "n_option_pairs", "n_exact_price_match", "exact_price_match_rate"])
    frame = option_detail.copy()
    frame["time_diff_bucket"] = time_diff_minutes_bucket_series(frame["observed_at_diff_minutes"])
    grouped = frame.groupby("time_diff_bucket", observed=True).agg(
        n_option_pairs=("price_abs_diff", "size"),
        n_exact_price_match=("price_abs_diff", lambda s: int((s == 0).sum())),
    ).reindex(TIME_DIFF_BUCKET_ORDER, fill_value=0).rename_axis("time_diff_bucket").reset_index()
    grouped["exact_price_match_rate"] = grouped["n_exact_price_match"] / grouped["n_option_pairs"].replace(0, np.nan)
    return grouped
