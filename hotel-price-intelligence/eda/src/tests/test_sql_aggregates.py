"""Integration test (MySQL that, disposable warehouse tu `build_warehouse()`) cho cac aggregate SQL bounded-memory
(GPT review 12 eda file 11 muc 4). So hoc duoc doi chieu voi numpy (oracle) tren CHINH du lieu spec, khong tin ket qua SQL suong.
Du lieu + ky vong nam o `fixture_specs.price_fixture_spec()`."""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd
import pytest

import fixture_specs as fs
import holidays
import metrics
import queries

pytestmark = pytest.mark.mysql
D = dt.date


@pytest.fixture()
def wh(price_db):
    """(conn, snapshot, fx) READ-ONLY tren warehouse fixture dung chung (conftest.price_db)."""
    return price_db


def _run(wh, metric_id, **kwargs):
    conn, snapshot, _ = wh
    return queries.run_metric(metric_id, conn, snapshot, **kwargs)


def _expected_stats(prices):
    arr = np.array(prices, dtype=float)
    out = {"n_obs": len(arr), "min_price": arr.min(), "max_price": arr.max(), "mean_price": arr.mean()}
    for name, q in {"p1": .01, "p5": .05, "p50": .5, "p75": .75, "p95": .95, "p99": .99}.items():
        out[name] = np.quantile(arr, q)  # linear = pandas default
    return out


def _assert_stats_row(row, prices):
    for key, expected in _expected_stats(prices).items():
        assert float(row[key]) == pytest.approx(float(expected), rel=1e-9), key


# ---------------------------------------------------------------- price distributions
def test_price_distribution_overall_khop_numpy_tren_24_observation(wh):
    out = _run(wh, "price_distribution_overall_main")
    assert len(out) == 1
    _assert_stats_row(out.iloc[0], fs.ALL_PRICES_MAIN)
    assert int(out.iloc[0]["n_obs"]) == 24  # sentinel sold-out khong tinh


def test_price_distribution_raw_bang_main_khi_moi_item_deu_la_owner(wh):
    main, raw = _run(wh, "price_distribution_overall_main"), _run(wh, "price_distribution_overall_raw")
    assert main.iloc[0].to_dict() == raw.iloc[0].to_dict()


def test_price_distribution_by_city(wh):
    out = _run(wh, "price_distribution_by_city_main").set_index("city")
    _assert_stats_row(out.loc["Hà Nội"], [fs.SERIES_A_PRICE] * 3 + fs.H1_ITEM4_PRICES + fs.H2_ITEM5_PRICES)
    _assert_stats_row(out.loc["Đà Lạt"], fs.H3_ITEM6_PRICES + fs.H3_ITEM10_PRICES)


def test_price_distribution_by_lead_time_bucket(wh):
    out = _run(wh, "price_distribution_by_lead_time_bucket_main").set_index("lead_time_bucket")
    # series A: lead 19 (01/09->20/09), 18 (02/09), 14 (06/09); item 4/5/6: lead 9; item 10: lead 6.
    assert int(out.loc["15-30", "n_obs"]) == 2
    _assert_stats_row(out.loc["8-14"], [fs.SERIES_A_PRICE] + fs.H1_ITEM4_PRICES + fs.H2_ITEM5_PRICES + fs.H3_ITEM6_PRICES)
    _assert_stats_row(out.loc["4-7"], fs.H3_ITEM10_PRICES)


def test_price_distribution_by_weekday_dung_thu_va_co_weekend_fri_sat(wh):
    out = _run(wh, "price_distribution_by_weekday_main").set_index("weekday")
    # 10/09/2026 = Thursday, 12/09 = Saturday, 20/09 = Sunday
    assert D(2026, 9, 10).strftime("%A") == "Thursday" and D(2026, 9, 12).strftime("%A") == "Saturday"
    assert int(out.loc["Thursday", "n_obs"]) == 19 and int(out.loc["Saturday", "n_obs"]) == 2
    assert int(out.loc["Sunday", "n_obs"]) == 3
    assert int(out.loc["Saturday", "is_weekend_fri_sat"]) == 1
    assert int(out.loc["Thursday", "is_weekend_fri_sat"]) == 0 and int(out.loc["Sunday", "is_weekend_fri_sat"]) == 0
    # File 17 M4: moi thu chi la MOT ngay check-in (anchor) du co 19 / 2 / 3 observation -> n_distinct_checkin_dates la so ngay, KHONG phai so observation
    assert int(out.loc["Thursday", "n_distinct_checkin_dates"]) == 1 and int(out.loc["Saturday", "n_distinct_checkin_dates"]) == 1
    assert int(out["n_distinct_checkin_dates"].sum()) == 3


def _calendar_rows(wh):
    conn, snapshot, fx = wh
    events = holidays.load_holiday_csv(fx["vn_holidays_csv_path"]).events
    dates = queries.run_metric("observation_counts_by_checkin_date_city_main", conn, snapshot)["checkin_date"].unique()
    flags = holidays.checkin_calendar_flags(events, dates)
    flagged = flags[flags[["is_public_holiday", "is_tet", "is_festival_period", "is_major_event"]].any(axis=1)]
    return flagged.to_dict("records")


def test_price_distribution_by_calendar_flags_join_inline(wh):
    out = _run(wh, "price_distribution_by_calendar_flags_main", calendar_rows=_calendar_rows(wh))
    key = lambda r: (int(r.is_public_holiday), int(r.is_tet), int(r.is_festival_period), int(r.is_major_event))
    by_flags = {key(r): int(r.n_obs) for r in out.itertuples()}
    # 10/09 le hoi CHI o Ha Noi (h1 10 + h2 3 = 13); 20/09 nghi le quoc gia (series A 3); con lai khong co (h3: 6 + 2 = 8)
    assert by_flags == {(0, 0, 0, 0): 8, (0, 0, 1, 0): 13, (1, 0, 0, 0): 3}
    anchors = {key(r): int(r.n_distinct_checkin_dates) for r in out.itertuples()}
    assert anchors == {(0, 0, 0, 0): 2, (0, 0, 1, 0): 1, (1, 0, 0, 0): 1}   # 10/09 + 12/09 (h3) khong co ; 10/09 festival Ha Noi ; 20/09 le quoc gia


def test_price_distribution_by_calendar_flags_calendar_rong_khong_loi(wh):
    out = _run(wh, "price_distribution_by_calendar_flags_main", calendar_rows=[])
    assert len(out) == 1 and int(out.iloc[0]["n_obs"]) == 24


def test_price_box_stats_by_city_co_p25(wh):
    out = _run(wh, "price_box_stats_by_city_main").set_index("city")
    hn = [fs.SERIES_A_PRICE] * 3 + fs.H1_ITEM4_PRICES + fs.H2_ITEM5_PRICES
    assert float(out.loc["Hà Nội", "p25"]) == pytest.approx(np.quantile(hn, 0.25), rel=1e-9)


# ---------------------------------------------------------------- hotel dispersion / sensitivity / outlier
def test_hotel_dispersion_chi_hotel_du_5_observation_va_khop_numpy(wh):
    out = _run(wh, "price_hotel_dispersion_main").set_index("hotel_id")
    assert set(out.index) == {"h1", "h3"}  # h2 chi co 3 observation
    h1 = np.array([fs.SERIES_A_PRICE] * 3 + fs.H1_ITEM4_PRICES, dtype=float)
    assert int(out.loc["h1", "n_obs"]) == 13 and float(out.loc["h1", "p50"]) == pytest.approx(np.median(h1))
    assert float(out.loc["h1", "std_price"]) == pytest.approx(h1.std(ddof=1), rel=1e-9)
    assert float(out.loc["h1", "coefficient_of_variation"]) == pytest.approx(h1.std(ddof=1) / h1.mean(), rel=1e-9)


def test_price_sensitivity_by_series_min_va_median_moi_hotel_checkin_ngay(wh):
    out = _run(wh, "price_sensitivity_by_series_main")
    row = out[(out["hotel_id"] == "h1") & (out["checkin_date"] == D(2026, 9, 10))].iloc[0]
    assert int(row["n_options"]) == 10 and float(row["min_price"]) == 100_000
    assert float(row["median_price"]) == pytest.approx(np.median(fs.H1_ITEM4_PRICES))
    a_rows = out[(out["hotel_id"] == "h1") & (out["checkin_date"] == D(2026, 9, 20))]
    assert len(a_rows) == 3 and (a_rows["n_options"] == 1).all()  # series A: 3 ngay quan sat, moi ngay 1 option


def test_price_outlier_dung_1_observation_va_khop_mad_numpy(wh):
    summary = _run(wh, "price_outlier_summary_by_hotel_main").set_index("hotel_id")
    h1 = np.array([fs.SERIES_A_PRICE] * 3 + fs.H1_ITEM4_PRICES, dtype=float)
    median = np.median(h1)
    mad = np.median(np.abs(h1 - median))
    assert float(summary.loc["h1", "hotel_median_price"]) == pytest.approx(median)
    assert float(summary.loc["h1", "price_mad"]) == pytest.approx(mad)
    assert int(summary.loc["h1", "n_outliers"]) == 1 and int(summary.loc["h2", "n_outliers"]) == 0
    assert int(summary.loc["h3", "n_outliers"]) == 0
    expected_z = abs(9_000_000 - median) / (1.4826 * mad)
    assert float(summary.loc["h1", "max_robust_z"]) == pytest.approx(expected_z, rel=1e-6)
    sample = _run(wh, "price_outlier_sample_main")
    assert len(sample) == 1 and float(sample.iloc[0]["price_per_night"]) == 9_000_000


# ---------------------------------------------------------------- histograms
def test_histograms_tong_bang_tong_observation(wh):
    linear, log10 = _run(wh, "price_histogram_linear_main"), _run(wh, "price_histogram_log10_main")
    assert int(linear["n_obs"].sum()) == 24 and int(log10["n_obs"].sum()) == 24
    assert linear["bin_index"].between(0, 79).all()
    assert int(linear[linear["bin_index"] == 79]["n_obs"].sum()) == 1  # gia max (9.000.000) vao bin cuoi, khong tran
    assert (log10["price_lo"] < log10["price_hi"]).all()


# ---------------------------------------------------------------- lead-time / calendar counts
def test_lead_time_bucket_distribution_du_7_bucket_va_dung_thu_tu(wh):
    out = _run(wh, "lead_time_bucket_distribution_main")
    assert list(out["lead_time_bucket"]) == list(metrics.LEAD_TIME_BUCKET_ORDER)
    assert out.set_index("lead_time_bucket")["n_observations"].to_dict() == {
        "0": 0, "1-3": 0, "4-7": 2, "8-14": 20, "15-30": 2, "31-60": 0, "61+": 0}


def test_lead_time_by_city_source_va_weekday_month_va_ranges(wh):
    by_city = _run(wh, "lead_time_bucket_distribution_by_city_source_main")
    assert int(by_city["n_observations"].sum()) == 24 and set(by_city["source_code"]) == {"local_primary"}
    assert _run(wh, "checkin_weekday_distribution_main")["n_observations"].sum() == 24
    month = _run(wh, "checkin_month_distribution_main")
    assert list(month["checkin_month"]) == ["2026-09"] and int(month.iloc[0]["n_observations"]) == 24
    ranges = _run(wh, "observation_date_ranges_main").iloc[0]
    assert ranges["observed_date_min"] == D(2026, 9, 1) and ranges["observed_date_max"] == D(2026, 9, 6)
    assert ranges["checkin_date_min"] == D(2026, 9, 10) and ranges["checkin_date_max"] == D(2026, 9, 20)


def test_reference_approval_checkin_month_khong_con_la_hang_so_percent(wh):
    out = _run(wh, "reference_approval_by_city_month")
    assert len(out) > 0
    assert out["checkin_month"].map(lambda v: bool(re.fullmatch(r"\d{4}-\d{2}", str(v)))).all(), out["checkin_month"].tolist()


# ---------------------------------------------------------------- series turnover / readiness (1 truy van ham cua so - canonical_series_facts_main)
def _facts(wh, **kwargs):
    return _run(wh, "canonical_series_facts_main", **kwargs)


def test_series_turnover_fixture_gpt_d0_d1_d5(wh):
    """Fixture GPT (review 12 file 07): series A quan sat 01/09, 02/09, 06/09 -> n=3, gap toi da 4, median gap 2.5.
    Sample (limit mac dinh 200 >= 22 series) tra du 22 series voi day du cot; series A dung dau (max_gap lon nhat)."""
    conn, snapshot, _ = wh
    out = queries.turnover_sample(conn, snapshot, _facts(wh))
    assert len(out) == 22
    a = out.iloc[0]
    assert (a["hotel_id"], a["checkin_date"]) == ("h1", D(2026, 9, 20))
    assert int(a["n_observed_days"]) == 3 and a["first_observed"] == D(2026, 9, 1) and a["last_observed"] == D(2026, 9, 6)
    assert int(a["max_gap_days"]) == 4 and float(a["median_gap_days"]) == pytest.approx(2.5)
    assert int(a["n_reappearance_events"]) == 1  # 1 khoang trong > 1 ngay (02/09 -> 06/09)
    others = out.iloc[1:]
    assert (others["n_observed_days"] == 1).all() and (others["max_gap_days"] == 0).all()
    assert (others["median_gap_days"] == 0).all() and (others["n_reappearance_events"] == 0).all()
    # id series di qua UNHEX -> LOWER(HEX()) (so sanh nhi phan cho nhanh ~25x): phai KHOI PHUC DUNG chuoi hex goc cua curated_observation_keys
    assert set(out["canonical_series_id"].str.len()) == {64} and out["canonical_series_id"].is_unique
    import db
    known = db.read_sql(conn, "SELECT DISTINCT canonical_series_id FROM curated_observation_keys")["canonical_series_id"]
    assert set(out["canonical_series_id"]) <= set(known)


def test_series_turnover_sample_bi_chan_boi_limit_va_giu_series_gap_lon_nhat(wh):
    conn, snapshot, _ = wh
    facts = queries.canonical_series_facts_main(conn, snapshot)
    out = metrics.turnover_sample_rows(facts, limit=1)
    assert len(out) == 1 and int(out.iloc[0]["max_gap_days"]) == 4 and float(out.iloc[0]["median_gap_days"]) == pytest.approx(2.5)
    assert int(metrics.turnover_joint(facts)["n_series"].sum()) == 22  # limit chi cat sample, KHONG anh huong phan phoi chung


def test_series_turnover_joint_khop_sample_va_tong_series(wh):
    joint = metrics.turnover_joint(_facts(wh))
    rows = {(int(r.n_observed_days), int(r.max_gap_days)): r for r in joint.itertuples()}
    assert set(rows) == {(1, 0), (3, 4)}
    assert int(rows[(1, 0)].n_series) == 21 and int(rows[(1, 0)].n_reappearance_events) == 0 and int(rows[(1, 0)].sum_span_days) == 21
    assert int(rows[(3, 4)].n_series) == 1 and int(rows[(3, 4)].n_reappearance_events) == 1
    assert int(rows[(3, 4)].sum_span_days) == 6  # 01/09 -> 06/09 = 6 ngay
    assert int(joint["n_series"].sum()) == 22
    by_days = metrics.turnover_by_observed_days(joint).set_index("n_observed_days")
    assert int(by_days.loc[3, "n_series_with_reappearance"]) == 1 and int(by_days.loc[1, "n_series_with_reappearance"]) == 0


def test_dataset_readiness_dem_dung_cap_cach_k_ngay_khong_dung_n_observed_days(wh):
    """d0/d1/d5: K=1 -> 1 cap (01/09->02/09); K=3 -> 0 (KHONG phai 1 du n_observed_days=3>=3); K=7,14 -> 0."""
    out = metrics.readiness_by_horizon(_facts(wh)).set_index("horizon_days")
    assert list(out.index) == list(metrics.HORIZON_DAYS)
    assert out.loc[1, "theoretical_date_pairs"] == 1 and out.loc[1, "series_with_pair"] == 1
    assert (out.loc[[3, 7, 14], "theoretical_date_pairs"] == 0).all()
    assert (out["n_series"] == 22).all()
    assert (out["actual_status"] == "not_available").all() and out["actual_causal_labels"].isna().all()


def test_ham_cua_so_dem_cap_ngay_cach_dung_k_tren_series_5_ngay(turnover_db):
    """Series 1 chuoi ngay 01,02,04,05,08: K=1 -> 2 cap, K=3 -> 3, K=7 -> 1, K=14 -> 0 (dem tay). Kiem chung RANGE INTERVAL K DAY FOLLOWING (thay tu-join)."""
    conn, snapshot, _ = turnover_db
    facts = queries.canonical_series_facts_main(conn, snapshot)
    readiness = metrics.readiness_by_horizon(facts).set_index("horizon_days")
    assert readiness["theoretical_date_pairs"].to_dict() == {1: 2, 3: 3, 7: 1, 14: 0}
    assert readiness["series_with_pair"].to_dict() == {1: 1, 3: 1, 7: 1, 14: 0} and (readiness["n_series"] == 1).all()
    joint = metrics.turnover_joint(facts).iloc[0]
    assert (int(joint["n_observed_days"]), int(joint["max_gap_days"]), int(joint["n_reappearance_events"]), int(joint["sum_span_days"])) == (5, 3, 2, 8)
    sample = queries.turnover_sample(conn, snapshot, facts).iloc[0]
    assert (sample["first_observed"], sample["last_observed"]) == (D(2026, 9, 1), D(2026, 9, 8))
    assert float(sample["median_gap_days"]) == pytest.approx(1.5) and int(sample["max_gap_days"]) == 3
