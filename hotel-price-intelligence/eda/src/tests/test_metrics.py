"""Test cho `metrics.py` - dung synthetic DataFrame, khong can MySQL (EDA_CURATED_PLAN.md muc 10.1)."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from metrics import (
    canonical_series_turnover,
    dataset_readiness_by_horizon,
    exact_approved_key_observation_coverage,
    item_availability_rates,
    item_level_exact_reference_availability,
    lead_time_bucket,
    lead_time_bucket_series,
    price_distribution_stats,
    price_sensitivity_by_series,
    protocol_continuity,
    series_with_approved_reference_coverage,
    status_present_report,
    theoretical_horizon_pairs,
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


def test_lead_time_bucket_series():
    result = lead_time_bucket_series(pd.Series([0, 3, 4, 61]))
    assert list(result) == ["0", "1-3", "4-7", "61+"]


def test_lead_time_bucket_series_am_thi_fail():
    with pytest.raises(ValueError, match="lead_time < 0"):
        lead_time_bucket_series(pd.Series([1, -5]))


# ======================================================================== 7.8 item_availability_rates (GPT M2)
def _items(statuses: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"item_id": range(len(statuses)), "status": statuses})


def test_item_availability_rates_dem_dung_va_denominator_dung():
    # 9 success (1 item, du 138 room option KHONG anh huong o day vi grain la item) + 1 sold_out
    items = _items(["success"] * 9 + ["sold_out"])
    out = item_availability_rates(items)
    row = out.iloc[0]
    assert row["n_items"] == 10
    assert row["success"] == 9 and row["sold_out"] == 1
    assert row["sold_out_rate"] == pytest.approx(0.1)
    assert row["not_bookable"] == 0 and row["not_bookable_rate"] == 0.0  # khong am tham bo qua


def test_item_availability_rates_khong_dung_observation_lam_denominator():
    """Dung evidence cua GPT (file 03): 140.571 success item -> 1.276.337 observation (~9.08/item),
    14.677 sold_out item -> 14.677 observation (1/item). Neu tinh nham o grain observation, sold-out
    rate se bi pha loang. Kiem tra tai grain item khong bi anh huong boi so option/item."""
    items = pd.DataFrame({
        "item_id": range(4),
        "status": ["success", "success", "sold_out", "sold_out"],
        "n_observations_of_item": [138, 1, 1, 1],  # gia lap: item nay co 138 option, kia chi 1
    })
    out = item_availability_rates(items)
    row = out.iloc[0]
    assert row["success_rate"] == 0.5 and row["sold_out_rate"] == 0.5  # 2/4 moi ben, KHONG bi 138 chi phoi
    assert row["n_items"] == 4


def test_item_availability_rates_theo_nhom():
    items = pd.DataFrame({
        "city": ["Hà Nội", "Hà Nội", "Đà Lạt"], "status": ["success", "sold_out", "success"],
    })
    out = item_availability_rates(items, group_cols=("city",))
    ha_noi = out[out["city"] == "Hà Nội"].iloc[0]
    assert ha_noi["n_items"] == 2 and ha_noi["success"] == 1 and ha_noi["sold_out"] == 1


def test_item_availability_rates_status_la_khong_terminal_thi_fail():
    with pytest.raises(ValueError, match="ngoai tap terminal"):
        item_availability_rates(_items(["success", "queued"]))


def test_status_present_report_bao_0_cho_status_vang_mat():
    report = status_present_report(_items(["success", "success"]))
    assert report == {"success": 2, "sold_out": 0, "not_bookable": 0, "partial": 0, "error": 0}


# ======================================================================== 7.10 reference metrics (GPT M3)
def test_exact_approved_key_observation_coverage():
    observations = pd.DataFrame({"record_id": range(5), "matches_approved_key": [True, True, False, False, False]})
    out = exact_approved_key_observation_coverage(observations)
    row = out.iloc[0]
    assert row["n_observations"] == 5 and row["n_matched"] == 2
    assert row["match_rate"] == pytest.approx(0.4)


def test_exact_approved_key_observation_coverage_theo_bucket():
    observations = pd.DataFrame({
        "lead_time_bucket": ["0-3", "0-3", "61+"], "matches_approved_key": [True, False, False],
    })
    out = exact_approved_key_observation_coverage(observations, group_cols=("lead_time_bucket",))
    early = out[out["lead_time_bucket"] == "0-3"].iloc[0]
    assert early["n_observations"] == 2 and early["match_rate"] == pytest.approx(0.5)


def test_item_level_exact_reference_availability():
    items = pd.DataFrame({"item_id": range(4), "has_matching_option": [True, True, True, False]})
    out = item_level_exact_reference_availability(items)
    row = out.iloc[0]
    assert row["n_items"] == 4 and row["n_items_matched"] == 3
    assert row["availability_rate"] == pytest.approx(0.75)


def test_reference_metrics_thieu_cot_thi_fail():
    with pytest.raises(ValueError, match="matches_approved_key"):
        exact_approved_key_observation_coverage(pd.DataFrame({"x": [1]}))
    with pytest.raises(ValueError, match="has_matching_option"):
        item_level_exact_reference_availability(pd.DataFrame({"x": [1]}))


# ======================================================================== 7.10 series-exists (GPT review 12 file 09 muc 2)
def test_series_with_approved_reference_coverage_dung_cot_rieng_khong_phai_matches_approved_key():
    """Ham/cot PHAI khac han `exact_approved_key_observation_coverage`/`matches_approved_key` (GPT file
    09 muc 2) - khong the vo tinh dan nhan ket qua long thanh exact-match."""
    observations = pd.DataFrame({
        "record_id": range(4), "series_has_approved_reference": [True, True, True, False],
    })
    out = series_with_approved_reference_coverage(observations)
    row = out.iloc[0]
    assert row["n_observations"] == 4 and row["n_series_has_reference"] == 3
    assert row["series_reference_rate"] == pytest.approx(0.75)
    assert "match_rate" not in out.columns and "n_matched" not in out.columns


def test_series_with_approved_reference_coverage_thieu_cot_thi_fail():
    with pytest.raises(ValueError, match="series_has_approved_reference"):
        series_with_approved_reference_coverage(pd.DataFrame({"x": [1]}))


# ======================================================================== 7.7 price distribution
def test_price_distribution_stats_gia_tri_dung():
    observations = pd.DataFrame({"price_per_night": [100, 200, 300, 400, 500]})
    out = price_distribution_stats(observations)
    row = out.iloc[0]
    assert row["count"] == 5 and row["min"] == 100 and row["max"] == 500 and row["p50"] == 300


def test_price_sensitivity_by_series_median_khong_bi_hotel_nhieu_option_lan_at():
    hotel_138_options = pd.DataFrame({
        "hotel_id": ["big"] * 3, "checkin_date": [D(2026, 9, 20)] * 3,
        "vn_observation_date": [D(2026, 9, 1)] * 3, "price_per_night": [1_000_000, 1_100_000, 50_000_000],
    })
    hotel_1_option = pd.DataFrame({
        "hotel_id": ["small"], "checkin_date": [D(2026, 9, 20)],
        "vn_observation_date": [D(2026, 9, 1)], "price_per_night": [900_000],
    })
    observations = pd.concat([hotel_138_options, hotel_1_option], ignore_index=True)
    out = price_sensitivity_by_series(observations, agg="median")
    big = out[out["hotel_id"] == "big"].iloc[0]
    small = out[out["hotel_id"] == "small"].iloc[0]
    assert big["price_per_night"] == 1_100_000 and big["n_options"] == 3  # median, khong bi outlier 50tr keo
    assert small["price_per_night"] == 900_000 and small["n_options"] == 1


def test_price_sensitivity_agg_sai_thi_fail():
    with pytest.raises(ValueError, match="agg"):
        price_sensitivity_by_series(pd.DataFrame({
            "hotel_id": ["h"], "checkin_date": [D(2026, 1, 1)], "vn_observation_date": [D(2026, 1, 1)],
            "price_per_night": [1],
        }), agg="mean")


# ======================================================================== 7.5 continuity/turnover (GPT M4)
def test_canonical_series_turnover_khong_tu_bia_ngay_thieu():
    """Chi 3 ngay quan sat that su (bo qua 1 khoang o giua) - ham KHONG duoc tu dien ngay vao."""
    presence = pd.DataFrame({
        "hotel_id": ["h1"] * 3, "checkin_date": [D(2026, 9, 20)] * 3, "canonical_series_id": ["s1"] * 3,
        "vn_observation_date": [D(2026, 8, 18), D(2026, 8, 19), D(2026, 8, 25)],  # gap 6 ngay
    })
    out = canonical_series_turnover(presence)
    row = out.iloc[0]
    assert row["n_observed_days"] == 3  # KHONG phai 8 (neu tu dien het khoang trong)
    assert row["max_gap_days"] == 6 and row["median_gap_days"] == pytest.approx((1 + 6) / 2)
    assert row["first_observed"] == D(2026, 8, 18) and row["last_observed"] == D(2026, 8, 25)


def test_canonical_series_turnover_nhieu_series_doc_lap():
    presence = pd.DataFrame({
        "hotel_id": ["h1", "h1", "h2"], "checkin_date": [D(2026, 9, 20)] * 3,
        "canonical_series_id": ["s1", "s1", "s2"],
        "vn_observation_date": [D(2026, 8, 18), D(2026, 8, 19), D(2026, 8, 18)],
    })
    out = canonical_series_turnover(presence)
    assert len(out) == 2
    s2 = out[out["canonical_series_id"] == "s2"].iloc[0]
    assert s2["n_observed_days"] == 1 and s2["max_gap_days"] == 0


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


# ======================================================================== 7.12 theoretical_horizon_pairs (GPT M4)
def test_theoretical_horizon_pairs_khong_dung_n_observed_days_bua_bai():
    """Fixture DUNG cua GPT review 12: {d0,d1,d5} co n_observed_days=3 >=3 nhung KHONG co cap cach
    dung 3 ngay nao (d0+3=d3, d1+3=d4, d5+3=d8 - khong ngay nao duoc quan sat). Thuat toan cu
    (n_observed_days>=K) se BAO SAI la co du lieu cho horizon 3; thuat toan dung phai ra 0."""
    presence = pd.DataFrame({
        "hotel_id": ["h1"] * 3, "checkin_date": [D(2026, 9, 20)] * 3, "canonical_series_id": ["s1"] * 3,
        "vn_observation_date": [D(2026, 8, 18), D(2026, 8, 19), D(2026, 8, 23)],
    })
    out = theoretical_horizon_pairs(presence, horizons=(1, 3))
    h1 = out[out["horizon_days"] == 1].iloc[0]
    h3 = out[out["horizon_days"] == 3].iloc[0]
    assert h1["source_pair_count"] == 1  # dung d0->d1 (18/08 -> 19/08)
    assert h3["source_pair_count"] == 0  # KHONG co cap nao cach dung 3 ngay


def test_theoretical_horizon_pairs_co_cap_dung():
    presence = pd.DataFrame({
        "hotel_id": ["h1"] * 3, "checkin_date": [D(2026, 9, 20)] * 3, "canonical_series_id": ["s1"] * 3,
        "vn_observation_date": [D(2026, 8, 18), D(2026, 8, 21), D(2026, 8, 25)],
    })
    out = theoretical_horizon_pairs(presence, horizons=(3, 7))
    h3 = out[out["horizon_days"] == 3].iloc[0]
    h7 = out[out["horizon_days"] == 7].iloc[0]
    assert h3["source_pair_count"] == 1  # 18/08 -> 21/08 (cach dung 3 ngay)
    assert h7["source_pair_count"] == 1  # 18/08 -> 25/08 (cach dung 7 ngay); 21/08+7=28/08 khong co


def test_theoretical_horizon_pairs_7_ngay_dung_tinh():
    presence = pd.DataFrame({
        "hotel_id": ["h1"], "checkin_date": [D(2026, 9, 20)], "canonical_series_id": ["s1"],
        "vn_observation_date": [D(2026, 8, 18)],
    })
    presence2 = pd.concat([presence, pd.DataFrame({
        "hotel_id": ["h1"], "checkin_date": [D(2026, 9, 20)], "canonical_series_id": ["s1"],
        "vn_observation_date": [D(2026, 8, 25)],  # dung 7 ngay sau
    })], ignore_index=True)
    out = theoretical_horizon_pairs(presence2, horizons=(7,))
    assert out.iloc[0]["source_pair_count"] == 1


def test_theoretical_horizon_pairs_nhieu_series_doc_lap():
    presence = pd.DataFrame({
        "hotel_id": ["h1", "h1", "h2", "h2"], "checkin_date": [D(2026, 9, 20)] * 4,
        "canonical_series_id": ["s1", "s1", "s2", "s2"],
        "vn_observation_date": [D(2026, 8, 18), D(2026, 8, 19), D(2026, 8, 18), D(2026, 8, 20)],
    })
    out = theoretical_horizon_pairs(presence, horizons=(1,))
    assert len(out) == 2
    s2 = out[out["canonical_series_id"] == "s2"].iloc[0]
    assert s2["source_pair_count"] == 0  # h2 cach 2 ngay, khong phai 1


def test_dataset_readiness_by_horizon_tong_hop_dung():
    pairs = pd.DataFrame({
        "hotel_id": ["h1", "h2"], "checkin_date": [D(2026, 9, 20)] * 2,
        "canonical_series_id": ["s1", "s2"], "horizon_days": [1, 1], "source_pair_count": [2, 0],
    })
    out = dataset_readiness_by_horizon(pairs)
    row = out.iloc[0]
    assert row["theoretical_date_pairs"] == 2 and row["series_with_pair"] == 1 and row["n_series"] == 2
    assert row["actual_status"] == "not_available" and pd.isna(row["actual_causal_labels"])


def test_protocol_continuity_outcome_la_thi_fail():
    with pytest.raises(ValueError, match="ngoai tap da biet"):
        protocol_continuity(pd.DataFrame({"outcome": ["khong_hop_le"]}))
