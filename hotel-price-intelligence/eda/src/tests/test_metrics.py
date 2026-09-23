"""Test cho `metrics.py` - dung synthetic DataFrame, khong can MySQL (EDA_CURATED_PLAN.md muc 10.1).

Cac ham pandas cap observation (price_distribution_stats, robust_price_outliers, turnover/horizon-pairs...) DA XOA khoi
`metrics.py` (aggregate SQL thay the, GPT review 12 eda file 11 muc 4); hanh vi tuong ung duoc doi chieu voi numpy tren
MySQL that trong `test_sql_aggregates.py`."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from metrics import (
    HORIZON_DAYS,
    cohort_attrition_table,
    collision_item_status_concordance,
    collision_item_summary,
    collision_item_time_diff_stratification,
    collision_pairs_from_effective_items,
    collision_option_coverage_summary,
    collision_option_summary,
    collision_option_time_diff_stratification,
    daily_operational_anomaly_flags,
    evidence_runs_share,
    exact_approved_key_observation_coverage,
    finalize_item_status_counts,
    finish_hour_distribution,
    active_hotels_from_effective_items,
    item_coverage_count,
    item_status_counts_from_rows,
    item_level_exact_reference_availability,
    item_status_rates,
    lead_time_bucket,
    lead_time_bucket_sql_case,
    legacy_last_minute_bucket_sql_case,
    max_gap_distribution,
    median_gap_distribution,
    median_gap_days_by_series,
    missingness_overall,
    readiness_by_horizon,
    order_lead_time_bucket_rows,
    protocol_continuity,
    protocol_outcome_rates_by_source_date,
    series_with_approved_reference_coverage,
    status_present_report,
    time_diff_minutes_bucket,
    turnover_by_observed_days,
    turnover_joint,
    turnover_sample_rows,
)

D = dt.date


# ======================================================================== lead-time bucket
@pytest.mark.parametrize("value,expected", [
    (0, "0"), (1, "1-3"), (3, "1-3"), (4, "4-7"), (7, "4-7"), (8, "8-14"), (14, "8-14"),
    (15, "15-30"), (30, "15-30"), (31, "31-60"), (60, "31-60"), (61, "61+"), (200, "61+"),
])
def test_lead_time_bucket_ranh_gioi(value, expected):
    assert lead_time_bucket(value) == expected


def test_lead_time_bucket_am_thi_fail():
    with pytest.raises(ValueError, match="am"):
        lead_time_bucket(-1)


def test_order_lead_time_bucket_rows_dung_thu_tu_va_dat_bucket_la_cuoi():
    df = pd.DataFrame({"lead_time_bucket": ["61+", "(invalid)", "0", "8-14", "1-3"], "n": [1, 2, 3, 4, 5]})
    out = order_lead_time_bucket_rows(df)
    assert list(out["lead_time_bucket"]) == ["0", "1-3", "8-14", "61+", "(invalid)"]
    assert len(out) == len(df)  # khong bo dong nao


# ======================================================================== 7.8 item-grain status counts (GPT M2, file 11 muc 4)
def _counts(**overrides) -> pd.DataFrame:
    base = {"n_success": 0, "n_sold_out": 0, "n_not_bookable": 0, "n_partial": 0, "n_error": 0}
    base.update(overrides)
    base["n_items"] = sum(base.values())
    return pd.DataFrame([base])


def test_finalize_item_status_counts_giu_dung_cot_va_int64():
    raw = pd.DataFrame({"city": ["a"], "n_success": [9.0], "n_sold_out": [1.0], "n_not_bookable": [None],
                        "n_partial": [0], "n_error": [0], "n_items": [10]})
    out = finalize_item_status_counts(raw, ["city"])
    assert list(out.columns) == ["city", "n_success", "n_sold_out", "n_not_bookable", "n_partial", "n_error", "n_items"]
    assert out["n_not_bookable"].iloc[0] == 0 and str(out["n_success"].dtype) == "int64"


def test_finalize_item_status_counts_lech_tong_status_thi_fail_khong_am_tham_bo_qua():
    """n_items = 10 nhung chi 9 item thuoc 5 status terminal -> co 1 item queued/running/la lot vao: raise."""
    with pytest.raises(ValueError, match="5 status terminal"):
        finalize_item_status_counts(_counts(n_success=9).assign(n_items=10))


def test_item_status_rates_dem_dung_va_denominator_la_item():
    # 9 success + 1 sold_out (moi item 1 lan - GPT M2: khong dung so observation lam denominator)
    out = item_status_rates(_counts(n_success=9, n_sold_out=1))
    row = out.iloc[0]
    assert row["n_items"] == 10 and row["success_rate"] == pytest.approx(0.9) and row["sold_out_rate"] == pytest.approx(0.1)
    assert row["not_bookable_rate"] == 0.0 and row["partial_rate"] == 0.0  # co dang ky nhung = 0, khong bien mat


def test_item_status_rates_n_items_0_tra_nan_khong_chia_0():
    out = item_status_rates(_counts())
    assert out["success_rate"].isna().all()


def test_status_present_report_bao_0_cho_status_vang_mat():
    assert status_present_report(_counts(n_success=2)) == {
        "success": 2, "sold_out": 0, "not_bookable": 0, "partial": 0, "error": 0}


def _effective_items_fixture() -> pd.DataFrame:
    return pd.DataFrame({
        "item_id": [1, 2, 3, 4],
        "source_code": ["local", "local", "vps", "vps"],
        "crawl_date": [D(2026, 9, 1)] * 4,
        "checkin_date": [D(2026, 9, 20), D(2026, 9, 21), D(2026, 9, 20), D(2026, 9, 22)],
        # Items 2/4 model the real crawler case: raw hotel_id was NULL but URL resolution supplied
        # an effective identity. Every downstream item metric must keep them.
        "effective_hotel_id": ["h1", "h2", "h1", "h3"],
        "effective_city": ["Ha Noi", "Da Lat", "Ha Noi", "Phu Quoc"],
        "item_status": ["success", "error", "sold_out", "not_bookable"],
        "ownership_status": ["owner_success", "owner_failure", "owner_failure", "owner_failure"],
        "item_finished_at": pd.to_datetime(["2026-09-01 01:00", "2026-09-01 01:10", "2026-09-01 01:05", "2026-09-01 01:20"]),
    })


def test_effective_hotel_identity_drive_active_availability_va_collision():
    items = _effective_items_fixture()
    active = active_hotels_from_effective_items(items).set_index("source_code")
    assert active.loc["local", "n_active_hotels"] == 2 and active.loc["vps", "n_active_hotels"] == 2
    by_city = item_status_counts_from_rows(items.assign(city=items["effective_city"]), group_cols=("city",))
    assert int(by_city["n_items"].sum()) == 4 and set(by_city["city"]) == {"Ha Noi", "Da Lat", "Phu Quoc"}
    pairs = collision_pairs_from_effective_items(items)
    assert len(pairs) == 1 and pairs.iloc[0]["hotel_id"] == "h1"
    assert (pairs.iloc[0]["item_id_a"], pairs.iloc[0]["item_id_b"]) == (1, 3)


def test_item_coverage_count_khong_bi_room_option_weighting():
    # Two items stay two regardless of how many room options an item might later produce.
    items = pd.DataFrame({"checkin_month": ["2026-09", "2026-09"], "lead_time_bucket": ["8-14", "8-14"]})
    assert item_coverage_count(items, group_cols=("checkin_month",)).iloc[0]["n_items"] == 2
    assert item_coverage_count(items, group_cols=("lead_time_bucket",)).iloc[0]["n_items"] == 2


# ======================================================================== 7.10 reference metrics (GPT M3, file 11 muc 4: aggregate SAN tu SQL)
def test_exact_approved_key_observation_coverage_tinh_ty_le_tu_bang_da_aggregate():
    aggregated = pd.DataFrame({
        "lead_time_bucket": ["0-3", "61+"], "n_observations": [4, 1], "n_matched": [2, 0],
    })
    out = exact_approved_key_observation_coverage(aggregated)
    early = out[out["lead_time_bucket"] == "0-3"].iloc[0]
    assert early["n_observations"] == 4 and early["n_matched"] == 2
    assert early["match_rate"] == pytest.approx(0.5)


def test_item_level_exact_reference_availability_tinh_ty_le_tu_bang_da_aggregate():
    aggregated = pd.DataFrame({"lead_time_bucket": ["0", "61+"], "n_items": [4, 2], "n_items_matched": [3, 0]})
    out = item_level_exact_reference_availability(aggregated)
    row = out[out["lead_time_bucket"] == "0"].iloc[0]
    assert row["n_items"] == 4 and row["n_items_matched"] == 3 and row["availability_rate"] == pytest.approx(0.75)
    assert out[out["lead_time_bucket"] == "61+"].iloc[0]["availability_rate"] == 0.0


def test_reference_metrics_thieu_cot_thi_fail():
    with pytest.raises(ValueError, match="n_matched"):
        exact_approved_key_observation_coverage(pd.DataFrame({"n_observations": [1]}))
    with pytest.raises(ValueError, match="n_items_matched"):
        item_level_exact_reference_availability(pd.DataFrame({"n_items": [1]}))


def test_series_with_approved_reference_coverage_dung_cot_rieng_khong_phai_matches_approved_key():
    """Ham/cot PHAI khac han `exact_approved_key_observation_coverage`/`matches_approved_key` (GPT file
    09 muc 2) - khong the vo tinh dan nhan ket qua long thanh exact-match."""
    aggregated = pd.DataFrame({
        "lead_time_bucket": ["0-3"], "n_observations": [4], "n_series_has_reference": [3],
    })
    out = series_with_approved_reference_coverage(aggregated)
    row = out.iloc[0]
    assert row["n_observations"] == 4 and row["n_series_has_reference"] == 3
    assert row["series_reference_rate"] == pytest.approx(0.75)
    assert "match_rate" not in out.columns and "n_matched" not in out.columns


def test_series_with_approved_reference_coverage_thieu_cot_thi_fail():
    with pytest.raises(ValueError, match="n_series_has_reference"):
        series_with_approved_reference_coverage(pd.DataFrame({"n_observations": [1]}))


def test_missingness_overall_cong_qua_cac_nguon_o_grain_field_value_cell():
    by_source = pd.DataFrame({
        "source_code": ["a", "a", "b", "b"], "field_group": ["price", "rate_plan", "price", "rate_plan"],
        "field": ["taxes_fees", "breakfast_included", "taxes_fees", "breakfast_included"],
        "n_null": [10, 0, 5, 1], "n_total": [100, 100, 50, 50],
    })
    out = missingness_overall(by_source).set_index("field")
    assert (out.loc["taxes_fees", "n_null"], out.loc["taxes_fees", "n_total"]) == (15, 150)
    assert out.loc["taxes_fees", "null_rate"] == pytest.approx(0.1) and out.loc["breakfast_included", "null_rate"] == pytest.approx(1 / 150)
    with pytest.raises(ValueError, match="thieu cot"):
        missingness_overall(pd.DataFrame({"field": ["x"]}))


def test_evidence_runs_share_ty_le_series_co_it_nhat_3_run_theo_city_va_tong():
    dist = pd.DataFrame({
        "city": ["a", "a", "a", "b"], "evidence_runs_bucket": ["1", "2", "3+", "3+"], "n_series": [5, 3, 2, 10],
    })
    overall = evidence_runs_share(dist).iloc[0]
    assert overall["n_series"] == 20 and overall["n_series_ge3"] == 12 and overall["share_ge3"] == pytest.approx(0.6)
    by_city = evidence_runs_share(dist, group_cols=("city",)).set_index("city")
    assert by_city.loc["a", "share_ge3"] == pytest.approx(0.2) and by_city.loc["b", "share_ge3"] == pytest.approx(1.0)


# ======================================================================== SQL bucket case generator (GPT review 12 file 11 muc 4)
def test_lead_time_bucket_sql_case_khop_dung_python_tren_moi_gia_tri_bien():
    """Sinh CASE tu CHINH LEAD_TIME_BUCKETS - test nay chi kiem cau truc SQL dung cu phap, khong chay
    duoc tren MySQL that o day (test rieng o test_wave_a_dry_run.py xac nhan qua ket qua truy van
    that). Kiem moi bien (ranh gioi tung bucket) deu co mat dung 1 lan trong CASE."""
    sql_case = lead_time_bucket_sql_case("po.lead_time")
    assert sql_case.startswith("CASE ") and sql_case.endswith(" END")
    for boundary_value, expected_label in (
        (0, "0"), (1, "1-3"), (3, "1-3"), (4, "4-7"), (61, "61+"),
    ):
        assert f"THEN '{expected_label}'" in sql_case


def test_legacy_last_minute_bucket_sql_case_gop_0_va_1_3():
    sql_case = legacy_last_minute_bucket_sql_case("po.lead_time")
    assert "THEN '0-3'" in sql_case
    assert "THEN '0'" not in sql_case and "THEN '1-3'" not in sql_case


# ======================================================================== 7.5/7.12 turnover marginals + median gap (SQL joint -> pandas)
def test_median_gap_days_by_series_khong_tu_bia_ngay_thieu():
    """Chi 3 ngay quan sat that su (khoang trong 6 ngay o giua) - ham KHONG duoc tu dien ngay vao."""
    presence = pd.DataFrame({
        "canonical_series_id": ["s1"] * 3 + ["s2"],
        "d": [D(2026, 8, 18), D(2026, 8, 19), D(2026, 8, 25), D(2026, 8, 18)],
    })
    out = median_gap_days_by_series(presence).set_index("canonical_series_id")
    assert out.loc["s1", "median_gap_days"] == pytest.approx((1 + 6) / 2)
    assert pd.isna(out.loc["s2", "median_gap_days"])  # 1 ngay -> khong co gap (caller dien 0)


def test_median_gap_days_by_series_thieu_cot_thi_fail():
    with pytest.raises(ValueError, match="thieu cot"):
        median_gap_days_by_series(pd.DataFrame({"canonical_series_id": ["h"]}))


def _facts() -> pd.DataFrame:
    """Ba dong population series-grain; khong tron joint/sample trong cung query contract."""
    rows = [
        {"hotel_id": "h0", "checkin_date": D(2026, 9, 10), "canonical_series_id": "s-0",
         "n_observed_days": 1, "first_observed": D(2026, 9, 1), "last_observed": D(2026, 9, 1),
         "span_days": 1, "max_gap_days": 0, "median_gap_days": 0.0, "n_reappearance_events": 0,
         "n_pairs_h1": 0, "n_pairs_h3": 0, "n_pairs_h7": 0, "n_pairs_h14": 0},
        {"hotel_id": "h1", "checkin_date": D(2026, 9, 20), "canonical_series_id": "s-a",
         "n_observed_days": 3, "first_observed": D(2026, 9, 1), "last_observed": D(2026, 9, 6),
         "span_days": 6, "max_gap_days": 4, "median_gap_days": 2.5, "n_reappearance_events": 1,
         "n_pairs_h1": 1, "n_pairs_h3": 0, "n_pairs_h7": 0, "n_pairs_h14": 0},
        {"hotel_id": "h2", "checkin_date": D(2026, 9, 21), "canonical_series_id": "s-b",
         "n_observed_days": 3, "first_observed": D(2026, 9, 1), "last_observed": D(2026, 9, 3),
         "span_days": 3, "max_gap_days": 1, "median_gap_days": 1.0, "n_reappearance_events": 0,
         "n_pairs_h1": 2, "n_pairs_h3": 0, "n_pairs_h7": 0, "n_pairs_h14": 0},
    ]
    return pd.DataFrame(rows)


def test_turnover_joint_aggregate_tu_population_series_grain():
    out = turnover_joint(_facts())
    assert len(out) == 3 and int(out["n_series"].sum()) == 3
    assert all(f"n_pairs_h{k}" in out for k in HORIZON_DAYS)


def test_turnover_sample_rows_la_top_gap_audit_khong_anh_huong_population():
    out = turnover_sample_rows(_facts())
    assert len(out) == 3 and out.iloc[0]["canonical_series_id"] == "s-a" and out.iloc[0]["max_gap_days"] == 4
    assert list(out.columns) == ["hotel_id", "checkin_date", "canonical_series_id", "n_observed_days", "first_observed", "last_observed",
                                 "span_days", "max_gap_days", "median_gap_days", "n_reappearance_events"]


def test_readiness_by_horizon_cong_dung_tu_population_series():
    out = readiness_by_horizon(_facts()).set_index("horizon_days")
    assert list(out.index) == [1, 3, 7, 14]
    assert out.loc[1, "theoretical_date_pairs"] == 3 and out.loc[1, "series_with_pair"] == 2
    assert (out.loc[[3, 7, 14], "theoretical_date_pairs"] == 0).all() and (out["n_series"] == 3).all()
    assert (out["actual_status"] == "not_available").all() and out["actual_causal_labels"].isna().all()


def test_facts_helpers_tu_choi_thieu_cot():
    with pytest.raises(ValueError, match="thieu cot"):
        turnover_joint(pd.DataFrame({"canonical_series_id": ["s"]}))
    with pytest.raises(ValueError, match="n_pairs_h1"):
        readiness_by_horizon(_facts().drop(columns=["n_pairs_h1"]))


def test_median_gap_distribution_dung_toan_bo_population():
    out = median_gap_distribution(_facts()).set_index("median_gap_days")
    assert out["n_series"].to_dict() == {0.0: 1, 1.0: 1, 2.5: 1}
    assert out["share_of_series"].sum() == pytest.approx(1.0)


def _joint() -> pd.DataFrame:
    # 10 series 1 ngay (max_gap 0); 4 series 3 ngay lien tuc (gap 1); 2 series 3 ngay co khoang trong (gap 4, 1 su kien)
    return pd.DataFrame({
        "n_observed_days": [1, 3, 3], "max_gap_days": [0, 1, 4], "n_series": [10, 4, 2],
        "n_reappearance_events": [0, 0, 2], "sum_span_days": [10, 12, 12],
    })


def test_turnover_by_observed_days_dem_reappearance_dung():
    out = turnover_by_observed_days(_joint()).set_index("n_observed_days")
    assert out.loc[1, "n_series"] == 10 and out.loc[1, "n_series_with_reappearance"] == 0
    assert out.loc[3, "n_series"] == 6 and out.loc[3, "n_series_with_reappearance"] == 2  # chi nhom max_gap 4 (>1)
    assert out.loc[3, "n_reappearance_events"] == 2 and out.loc[3, "reappearance_rate"] == pytest.approx(2 / 6)
    assert out.loc[3, "mean_span_days"] == pytest.approx(24 / 6)
    assert out.loc[3, "observed_fraction_of_span"] == pytest.approx((3 * 6) / 24)


def test_max_gap_distribution_tong_share_bang_1():
    out = max_gap_distribution(_joint()).set_index("max_gap_days")
    assert out.loc[0, "n_series"] == 10 and out.loc[1, "n_series"] == 4 and out.loc[4, "n_series"] == 2
    assert out["share_of_series"].sum() == pytest.approx(1.0)


def test_turnover_marginals_thieu_cot_thi_fail():
    with pytest.raises(ValueError, match="thieu cot"):
        turnover_by_observed_days(pd.DataFrame({"n_observed_days": [1]}))
    with pytest.raises(ValueError, match="thieu cot"):
        max_gap_distribution(pd.DataFrame({"n_series": [1]}))


# ======================================================================== 7.5/7.2 protocol continuity
def test_protocol_continuity_dem_dung_outcome():
    # GPT review 12 eda M1: "missing_run" phang da tach thanh missing_source_run/
    # missing_item_in_existing_run - PROTOCOL_ITEM_OUTCOMES khong con nhan "missing_run".
    scheduled = pd.DataFrame({"outcome": [
        "owner_success", "owner_success", "owner_failure_status_sold_out",
        "missing_source_run", "missing_item_in_existing_run",
    ]})
    out = protocol_continuity(scheduled)
    row = out.iloc[0]
    assert row["owner_success"] == 2 and row["missing_source_run"] == 1 and row["n_scheduled"] == 5
    assert row["missing_item_in_existing_run"] == 1
    assert row["owner_failure_status_not_bookable"] == 0  # co dang ky nhung = 0, khong bien mat


def test_protocol_continuity_outcome_la_thi_fail():
    with pytest.raises(ValueError, match="ngoai tap da biet"):
        protocol_continuity(pd.DataFrame({"outcome": ["khong_hop_le"]}))


def test_protocol_outcome_rates_by_source_date_numerator_denominator_va_ba_ty_le_cong_lai_bang_1():
    classified = pd.DataFrame({
        "owner_source": ["local"] * 4 + ["vps"] * 2,
        "crawl_date": [D(2026, 9, 1)] * 4 + [D(2026, 9, 1)] * 2,
        "outcome": ["owner_success", "owner_success", "owner_failure_status_error", "missing_item_in_existing_run",
                    "owner_success", "missing_source_run"],
    })
    out = protocol_outcome_rates_by_source_date(classified).set_index("owner_source")
    local = out.loc["local"]
    assert local["n_scheduled"] == 4 and local["n_owner_success"] == 2 and local["n_owner_failure"] == 1
    assert local["n_missing"] == 1 and local["owner_success_rate"] == pytest.approx(0.5)
    for source in ("local", "vps"):
        row = out.loc[source]
        assert row["owner_success_rate"] + row["owner_failure_rate"] + row["missing_rate"] == pytest.approx(1.0)
    assert out.loc["vps", "missing_rate"] == pytest.approx(0.5)


# ======================================================================== 7.3 finish-hour + ngay bat thuong (GPT review 12 file 11 muc 5)
def test_finish_hour_distribution():
    run_duration = pd.DataFrame({
        "source_code": ["local_primary", "local_primary", "local_primary"],
        "finished_at_vn": [pd.Timestamp("2026-09-01 17:20"), pd.Timestamp("2026-09-01 17:45"), pd.Timestamp("2026-08-10 09:05")],
        "is_protocol_run": [True, True, False],
    })
    out = finish_hour_distribution(run_duration)
    # file 17 M2: run pilot van co mat (RAW) nhung tach rieng theo is_protocol_run
    protocol = out[(out["is_protocol_run"]) & (out["finish_hour_vn"] == 17)].iloc[0]
    pilot = out[~out["is_protocol_run"]].iloc[0]
    assert protocol["n_runs"] == 2 and pilot["finish_hour_vn"] == 9 and pilot["n_runs"] == 1
    with pytest.raises(ValueError, match="is_protocol_run"):
        finish_hour_distribution(run_duration.drop(columns="is_protocol_run"))


def _day_counts(errors: list[int], n_items: int = 100) -> pd.DataFrame:
    days = [D(2026, 9, 1) + dt.timedelta(days=i) for i in range(len(errors))]
    return pd.DataFrame({
        "source_code": "s", "vn_crawl_date": days, "n_items": n_items, "n_success": [n_items - e for e in errors],
        "n_sold_out": 0, "n_not_bookable": 0, "n_partial": 0, "n_error": errors,
    })


def _durations(minutes: list[float], *, protocol: bool = True) -> pd.DataFrame:
    days = [D(2026, 9, 1) + dt.timedelta(days=i) for i in range(len(minutes))]
    return pd.DataFrame({"source_code": "s", "vn_crawl_date": days, "duration_minutes": minutes, "is_protocol_run": protocol})


def test_daily_operational_anomaly_flags_bat_ngay_duration_va_error_bat_thuong_khong_loc_ngay_nao():
    counts = _day_counts([1, 1, 2, 1, 2, 60])                      # ngay cuoi error_rate 60%
    durations = _durations([60.0, 62.0, 58.0, 61.0, 59.0, 500.0])  # ngay cuoi duration 500 phut
    out = daily_operational_anomaly_flags(counts, durations, z_threshold=2.0)
    assert len(out) == 6  # khong loc, chi flag
    last, first = out.iloc[-1], out.iloc[0]
    assert last["is_duration_anomalous"] and last["is_error_rate_anomalous"] and last["is_any_anomalous"]
    assert not first["is_any_anomalous"]
    assert last["error_rate"] == pytest.approx(0.6)


def test_daily_operational_anomaly_flags_std_0_hoac_it_hon_3_ngay_khong_flag_am_tham():
    flat = daily_operational_anomaly_flags(_day_counts([1, 1, 1]), _durations([60.0, 60.0, 60.0]))
    assert not flat["is_any_anomalous"].any()
    short = daily_operational_anomaly_flags(_day_counts([1, 90]), _durations([60.0, 900.0]))
    assert not short["is_any_anomalous"].any()  # 2 ngay < 3 -> khong du bang chung


def test_daily_operational_anomaly_flags_thieu_cot_thi_fail():
    with pytest.raises(ValueError, match="thieu cot"):
        daily_operational_anomaly_flags(pd.DataFrame({"source_code": ["s"]}), _durations([1.0]))
    with pytest.raises(ValueError, match="is_protocol_run"):
        daily_operational_anomaly_flags(_day_counts([1, 1, 1]), _durations([60.0, 60.0, 60.0]).drop(columns="is_protocol_run"))


def _with_pilot():
    """5 source-day production (duration ~60 phut, error 1-2/100 item) + 1 source-day production bat thuong (500 phut, 60% error) + 1 pilot cuc ngan
    (5 phut, 50 item, khong loi) o ngay dau tien (2026-08-10)."""
    prod_counts, prod_durations = _day_counts([1, 1, 2, 1, 2, 60]), _durations([60.0, 62.0, 58.0, 61.0, 59.0, 500.0])
    pilot_day = D(2026, 8, 10)
    pilot_counts = pd.DataFrame({"source_code": ["s"], "vn_crawl_date": [pilot_day], "n_items": [50], "n_success": [50], "n_sold_out": [0],
                                 "n_not_bookable": [0], "n_partial": [0], "n_error": [0]})
    pilot_durations = pd.DataFrame({"source_code": ["s"], "vn_crawl_date": [pilot_day], "duration_minutes": [5.0], "is_protocol_run": [False]})
    return (prod_counts, prod_durations), (pd.concat([pilot_counts, prod_counts], ignore_index=True),
                                          pd.concat([pilot_durations, prod_durations], ignore_index=True))


def test_pilot_cuc_ngan_khong_lam_thay_doi_co_anomaly_production_file_17_m2():
    """Bang CHINH (protocol_only=True): them 1 pilot (50 item, 5 phut) KHONG duoc doi z-score/co cua bat ky source-day production nao va khong xuat hien
    trong bang chinh. Doi chung: bang phu luc RAW (protocol_only=False) THI BI keo baseline - chinh la loi cu ma M2 sua."""
    (prod_counts, prod_durations), (all_counts, all_durations) = _with_pilot()
    baseline = daily_operational_anomaly_flags(prod_counts, prod_durations)
    with_pilot = daily_operational_anomaly_flags(all_counts, all_durations)
    assert len(with_pilot) == len(baseline) == 6 and D(2026, 8, 10) not in set(with_pilot["vn_crawl_date"])
    assert with_pilot["is_protocol_source_day"].all()
    columns = [c for c in baseline.columns if c.endswith("_z") or c.startswith("is_") or c in ("duration_minutes", "error_rate")]
    pd.testing.assert_frame_equal(with_pilot[columns].reset_index(drop=True), baseline[columns].reset_index(drop=True))
    # phu luc RAW: pilot vao baseline -> z-score duration cua ngay production thay doi (bang chung test co y nghia, khong pass vo dieu kien)
    appendix = daily_operational_anomaly_flags(all_counts, all_durations, protocol_only=False)
    assert len(appendix) == 7 and int((~appendix["is_protocol_source_day"]).sum()) == 1
    changed = appendix[appendix["is_protocol_source_day"]]["duration_minutes_z"].reset_index(drop=True)
    assert not changed.equals(baseline["duration_minutes_z"].reset_index(drop=True))


def test_anomaly_flags_source_day_co_run_production_va_pilot_cung_ngay_van_la_production():
    """1 source-day co CA run pilot lan run production (khong xay ra tren snapshot nay) -> tinh la source-day production (n_protocol_runs >= 1), n_runs dem du."""
    counts = _day_counts([1, 1, 1, 1])
    durations = _durations([60.0, 61.0, 59.0, 60.0])
    extra = durations.iloc[[0]].assign(duration_minutes=5.0, is_protocol_run=False)
    out = daily_operational_anomaly_flags(counts, pd.concat([durations, extra], ignore_index=True))
    first = out.iloc[0]
    assert len(out) == 4 and first["n_runs"] == 2 and first["n_protocol_runs"] == 1 and first["is_protocol_source_day"]


# ======================================================================== 7.4 cohort attrition (GPT review 12 file 11 muc 5)
def test_cohort_attrition_table_danh_dau_dung_attrition():
    versions = [
        {"cohort_version": "v1.0", "effective_from_crawl_date": "2026-08-18", "size": 355},
        {"cohort_version": "v1.1", "effective_from_crawl_date": "2026-08-19", "size": 355},
        {"cohort_version": "v2", "effective_from_crawl_date": "2026-09-03", "size": 354},
    ]
    out = cohort_attrition_table(versions)
    assert out.iloc[0]["change_type"] == "baseline"
    assert out.iloc[1]["change_type"] == "unchanged" and out.iloc[1]["size_change"] == 0
    assert out.iloc[2]["change_type"] == "attrition" and out.iloc[2]["size_change"] == -1


def test_cohort_attrition_table_rong_thi_fail():
    with pytest.raises(ValueError, match="rong"):
        cohort_attrition_table([])


# ======================================================================== 7.2/7.11 collision / source divergence (GPT review 12 file 11 muc 3)
@pytest.mark.parametrize("minutes,expected", [(0, "0-5"), (5, "0-5"), (6, "6-15"), (15, "6-15"),
                                              (16, "16-60"), (60, "16-60"), (61, "61+"), (500, "61+")])
def test_time_diff_minutes_bucket_ranh_gioi(minutes, expected):
    assert time_diff_minutes_bucket(minutes) == expected


@pytest.mark.parametrize("minutes,expected", [(5.4, "0-5"), (5.99, "0-5"), (6.0, "6-15"), (15.5, "6-15"),
                                              (15.99, "6-15"), (60.5, "16-60"), (60.99, "16-60"), (61.0, "61+")])
def test_time_diff_minutes_bucket_gia_tri_thap_phan_khong_rot_khoi_bucket_nao(minutes, expected):
    """Chenh lech thoi gian la bien LIEN TUC (vd 92.27 phut o du lieu that) - ban cu dung bien nguyen [lo,hi] nen 5.4 phut
    khong thuoc bucket nao va nem AssertionError."""
    assert time_diff_minutes_bucket(minutes) == expected


def test_time_diff_minutes_bucket_am_thi_fail():
    with pytest.raises(ValueError, match="am"):
        time_diff_minutes_bucket(-1)


def _item_pairs() -> pd.DataFrame:
    return pd.DataFrame({
        "status_a": ["success", "success", "sold_out"], "status_b": ["success", "not_bookable", "sold_out"],
        "observed_finish_a": [pd.Timestamp("2026-09-01 10:00")] * 3,
        "observed_finish_b": [pd.Timestamp("2026-09-01 10:03"), pd.Timestamp("2026-09-01 11:30"),
                              pd.Timestamp("2026-09-01 10:10")],
    })


def test_collision_item_status_concordance_dem_dung_cap():
    out = collision_item_status_concordance(_item_pairs())
    concordant = out[out["is_concordant"]]
    assert concordant["n_pairs"].sum() == 2  # (success,success) + (sold_out,sold_out)
    assert out[~out["is_concordant"]]["n_pairs"].sum() == 1  # (success, not_bookable)


def test_collision_item_summary_mau_so_rieng_cho_tung_lop():
    row = collision_item_summary(_item_pairs()).iloc[0]
    assert row["n_collision_item_pairs"] == 3 and row["n_status_concordant"] == 2 and row["n_status_disagreement"] == 1
    assert row["n_success_success_pairs"] == 1  # mau so cua option-level, KHAC voi 3 o tren
    assert row["status_disagreement_rate"] == pytest.approx(1 / 3)


def test_collision_item_summary_rong_khong_chia_0():
    row = collision_item_summary(pd.DataFrame({"status_a": [], "status_b": []})).iloc[0]
    assert row["n_collision_item_pairs"] == 0 and pd.isna(row["status_disagreement_rate"])


def test_collision_item_time_diff_stratification():
    out = collision_item_time_diff_stratification(_item_pairs())
    by_bucket = out.set_index("time_diff_bucket")["n_pairs"]
    assert by_bucket["0-5"] == 1 and by_bucket["61+"] == 1 and by_bucket["6-15"] == 1 and by_bucket["16-60"] == 0


def test_collision_item_time_diff_stratification_rong_tra_ve_du_bucket_0():
    out = collision_item_time_diff_stratification(pd.DataFrame({"observed_finish_a": [], "observed_finish_b": []}))
    assert len(out) == 4 and (out["n_pairs"] == 0).all()


def _option_detail() -> pd.DataFrame:
    return pd.DataFrame({
        "price_abs_diff": [0.0, 20_000.0, 0.0], "price_relative_diff": [0.0, 0.04, 0.0],
        "observed_at_diff_minutes": [1.0, 15.0, 70.0],
        "currency_concordant": [True, True, True], "breakfast_included_concordant": [True, False, True],
        "free_cancellation_concordant": [True, True, True], "cancellation_policy_concordant": [True, True, False],
        "price_includes_tax_concordant": [True, True, False],
        "taxes_fees_state": ["both_null", "one_null", "both_present"],
        "taxes_fees_concordant": [True, False, True], "taxes_fees_abs_diff": [np.nan, np.nan, 500.0],
    })


def test_collision_option_summary_mau_so_la_shared_option_pairs():
    row = collision_option_summary(_option_detail()).iloc[0]
    assert row["n_option_pairs"] == 3 and row["n_exact_price_match"] == 2
    assert row["exact_price_match_rate"] == pytest.approx(2 / 3)
    assert row["max_price_abs_diff"] == 20_000 and row["median_price_abs_diff"] == 0
    assert row["breakfast_included_concordance_rate"] == pytest.approx(2 / 3)
    assert row["price_includes_tax_concordance_rate"] == pytest.approx(2 / 3)
    assert row["cancellation_policy_concordance_rate"] == pytest.approx(2 / 3)
    assert row["taxes_fees_concordance_rate"] == pytest.approx(2 / 3)
    assert (row["n_taxes_both_null"], row["n_taxes_one_null"], row["n_taxes_both_present"]) == (1, 1, 1)
    assert row["median_taxes_fees_abs_diff"] == pytest.approx(500.0)
    assert row["currency_concordance_rate"] == 1.0


def test_collision_option_summary_rong_tra_0_va_nan():
    row = collision_option_summary(_option_detail().iloc[0:0]).iloc[0]
    assert row["n_option_pairs"] == 0 and pd.isna(row["exact_price_match_rate"])


def test_collision_option_time_diff_stratification_ty_le_exact_match_theo_bucket():
    option_detail = pd.DataFrame({
        "observed_at_diff_minutes": [1.0, 1.0, 70.0], "price_abs_diff": [0.0, 500.0, 0.0],
    })
    out = collision_option_time_diff_stratification(option_detail)
    by_bucket = out.set_index("time_diff_bucket")
    assert by_bucket.loc["0-5", "n_option_pairs"] == 2 and by_bucket.loc["0-5", "n_exact_price_match"] == 1
    assert by_bucket.loc["0-5", "exact_price_match_rate"] == pytest.approx(0.5)
    assert by_bucket.loc["61+", "n_exact_price_match"] == 1


def test_collision_option_time_diff_stratification_rong_van_du_4_bucket():
    out = collision_option_time_diff_stratification(pd.DataFrame({"observed_at_diff_minutes": [], "price_abs_diff": []}))
    assert list(out["time_diff_bucket"]) == ["0-5", "6-15", "16-60", "61+"] and (out["n_option_pairs"] == 0).all()


def test_collision_option_coverage_summary_jaccard_va_key_mo_ho():
    pair_coverage = pd.DataFrame({
        "option_jaccard": [1.0, 0.0, 0.5], "n_shared_options": [3, 0, 2], "n_union_options": [3, 4, 4],
        "n_ambiguous_shared_keys": [0, 0, 1], "n_duplicate_canonical_keys": [0, 2, 1],
    })
    row = collision_option_coverage_summary(pair_coverage).iloc[0]
    assert row["n_success_success_pairs"] == 3 and row["n_pairs_identical_option_sets"] == 1
    assert row["n_pairs_disjoint_option_sets"] == 1 and row["mean_option_jaccard"] == pytest.approx(0.5)
    assert row["total_shared_options"] == 5 and row["total_union_options"] == 11
    assert row["total_ambiguous_shared_keys"] == 1 and row["total_duplicate_canonical_keys"] == 3
    empty = collision_option_coverage_summary(pair_coverage.iloc[0:0]).iloc[0]
    assert empty["n_success_success_pairs"] == 0 and np.isnan(empty["mean_option_jaccard"])
