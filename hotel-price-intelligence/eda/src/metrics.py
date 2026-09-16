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


# ======================================================================== 7.10 full-history reference (GPT review 12 file 03 M3, file 09 muc 2)
def _boolean_rate_by_group(
    frame: "pd.DataFrame", *, bool_col: str, group_cols: tuple[str, ...],
    n_col: str, n_true_col: str, rate_col: str,
) -> "pd.DataFrame":
    """Helper PRIVATE dung chung cho cac metric dang "ty le boolean theo nhom" (GPT review 12 eda file
    09 muc 2: 2 ham public co the goi chung 1 helper, nhung TEN COT/CONTRACT cong khai phai khac nhau
    de khong the vo tinh dan nhan sai y nghia - vd goi ket qua cua ban long la "exact match")."""
    if bool_col not in frame.columns:
        raise ValueError(f"thieu cot bat buoc {bool_col!r}")
    work = frame.copy()
    keys = list(group_cols)
    if not keys:
        work["_all"] = "all"
        keys = ["_all"]
    grouped = work.groupby(keys).agg(**{
        n_col: (bool_col, "size"), n_true_col: (bool_col, "sum"),
    }).reset_index()
    grouped[rate_col] = grouped[n_true_col] / grouped[n_col]
    if "_all" in grouped.columns:
        grouped = grouped.drop(columns=["_all"])
    return grouped


def exact_approved_key_observation_coverage(
    observations: "pd.DataFrame", *, group_cols: tuple[str, ...] = (),
) -> "pd.DataFrame":
    """Metric 1 (M3): OBSERVATION-grain EXACT approved-key coverage.

    `observations`: 1 dong / observation KHONG sold-out, cot bat buoc `matches_approved_key` (bool -
    True neu canonical room/rate key CUA CHINH observation nay khop DUNG 1 reference approved cua
    (hotel_id, checkin_date)). numerator = so observation match; denominator = TOAN BO observation
    trong DataFrame nay (da loc dung scope MAIN/RAW tu truoc boi `queries.py`, ham nay khong tu loc
    scope). KHONG dung ham nay cho metric series-level long hon - xem
    `series_with_approved_reference_coverage`.
    """
    return _boolean_rate_by_group(
        observations, bool_col="matches_approved_key", group_cols=group_cols,
        n_col="n_observations", n_true_col="n_matched", rate_col="match_rate",
    )


def series_with_approved_reference_coverage(
    observations: "pd.DataFrame", *, group_cols: tuple[str, ...] = (),
) -> "pd.DataFrame":
    """Metric rieng biet voi `exact_approved_key_observation_coverage` (GPT review 12 eda file 09 muc
    2 - "Khong dung mot ham ten exact cho metric series-level"). OBSERVATION-grain nhung dieu kien LONG
    hon: True khi (hotel_id, checkin_date) cua observation CO TON TAI it nhat 1 reference approved nao
    do - KHONG doi hoi dung key nhu ban chat. Doi hoi cot `series_has_approved_reference` (ten cot
    khac han `matches_approved_key` de khong the nham 30% series-exists thanh 30% exact-match - day
    chinh la nguyen nhan lech so 3,5% vs ~30% lich su tung gay nham lan, xem CLAUDE.md muc 7.2).
    """
    return _boolean_rate_by_group(
        observations, bool_col="series_has_approved_reference", group_cols=group_cols,
        n_col="n_observations", n_true_col="n_series_has_reference", rate_col="series_reference_rate",
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
