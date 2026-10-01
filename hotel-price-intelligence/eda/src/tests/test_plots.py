"""Test `plots.py` (thuan, backend Agg): moi ham ve duoc tu bang aggregate NHO va tu bang RONG (khong vo), tra ve Figure; hinh ve tu bin/quantile SQL phai
dung so cot/hop (khong keo observation-level)."""
from __future__ import annotations

import datetime as dt

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

import plots

D = dt.date


def _dates(n: int = 4) -> list:
    return [D(2026, 9, 1) + dt.timedelta(days=i) for i in range(n)]


def _run_item():
    return pd.DataFrame({"source_code": ["a", "a", "b"], "vn_crawl_date": _dates(3), "runs": 1, "items": [10, 12, 5], "observations": 30})


def _raw_main():
    return pd.DataFrame({"source_code": ["a", "b"], "n_items_raw": [10, 5], "n_items_main": [9, 0]})


def _owner_rate():
    return pd.DataFrame({"owner_source": ["a", "a"], "crawl_date": _dates(2), "owner_success_rate": [0.9, 0.8], "missing_rate": [0.0, 0.1]})


def _duration():
    return pd.DataFrame({"source_code": ["a", "a", "b"], "duration_minutes": [30.0, 45.0, 50.0]})


def _finish_hour():
    return pd.DataFrame({"source_code": ["a", "b"], "finish_hour_vn": [17, 3], "n_runs": [2, 1]})


def _active():
    return pd.DataFrame({"source_code": ["a", "a"], "vn_crawl_date": _dates(2), "n_active_hotels": [300, 310]})


def _heatmap():
    return pd.DataFrame({"vn_crawl_date": _dates(2) * 2, "lead_time_bucket": ["0", "0", "8-14", "8-14"], "n_items": [1, 2, 3, 4]})


def _weekday():
    return pd.DataFrame({"weekday": ["Friday", "Monday"], "weekday_number": [4, 0], "is_weekend_fri_sat": [True, False], "n_items": [5, 3],
                         "n_distinct_checkin_dates": [1, 2]})


def _month():
    return pd.DataFrame({"checkin_month": ["2026-09", "2026-10"], "n_items": [7, 4], "n_distinct_checkin_dates": [5, 1]})


def _lead():
    return pd.DataFrame({"lead_time_bucket": ["0", "1-3"], "n_items": [4, 9]})


def _hist_linear():
    return pd.DataFrame({"bin_index": [0, 1], "bin_lo": [100.0, 200.0], "bin_hi": [200.0, 300.0], "n_obs": [10, 5]})


def _hist_log():
    return pd.DataFrame({"bin_index": [40, 41], "log10_lo": [2.0, 2.05], "log10_hi": [2.05, 2.1], "price_lo": [100.0, 112.0], "price_hi": [112.0, 126.0], "n_obs": [3, 4]})


def _box():
    return pd.DataFrame({"city": ["A", "B"], "n_obs": [10, 20], "min_price": [1, 1], "max_price": [9, 9], "mean_price": [5.0, 6.0],
                         "p5": [1.0, 1.0], "p25": [2.0, 3.0], "p50": [4.0, 5.0], "p75": [6.0, 7.0], "p95": [8.0, 9.0]})


def _price_bucket():
    return pd.DataFrame({"lead_time_bucket": ["0", "1-3"], "n_obs": [5, 5], "p5": [1.0, 2.0], "p50": [3.0, 4.0], "p75": [5.0, 6.0], "p95": [7.0, 8.0]})


def _availability():
    row = {"n_success": 8, "n_sold_out": 1, "n_not_bookable": 1, "n_partial": 0, "n_error": 0, "n_items": 10,
           "success_rate": 0.8, "sold_out_rate": 0.1, "not_bookable_rate": 0.1, "partial_rate": 0.0, "error_rate": 0.0}
    return pd.DataFrame([{"city": "A", **row}, {"city": "B", **row}])


def _missingness():
    return pd.DataFrame({"source_code": ["a", "a", "b"], "field_group": ["price", "price", "price"], "field": ["x", "y", "x"], "null_rate": [0.0, 0.1, 0.0]})


def _ref_exact():
    return pd.DataFrame({"lead_time_bucket": ["0", "1-3"], "n_observations": [10, 10], "n_matched": [1, 2], "match_rate": [0.1, 0.2]})


def _ref_legacy():
    return pd.DataFrame({"lead_time_bucket": ["0-3"], "n_observations": [10], "n_series_has_reference": [3], "series_reference_rate": [0.3]})


def _by_days():
    return pd.DataFrame({"n_observed_days": [1, 3], "n_series": [10, 2]})


def _max_gap():
    return pd.DataFrame({"max_gap_days": [0, 4], "n_series": [10, 2]})


def _median_gap():
    return pd.DataFrame({"median_gap_days": [0.0, 2.5], "n_series": [10, 2]})


def _history():
    return pd.DataFrame({"city": ["A", "A"], "history_days_bucket": ["1", "3-6"], "n_series": [4, 1], "sum_span_days": [4, 6]})


def _readiness():
    return pd.DataFrame({"horizon_days": [1, 3, 7, 14], "theoretical_date_pairs": [100, 50, 10, 0]})


CASES = [
    ("run_item", plots.plot_run_item_by_source_crawl_date, lambda: (_run_item(),)),
    ("raw_main", plots.plot_raw_vs_main_by_source, lambda: (_raw_main(),)),
    ("owner_rate", plots.plot_owner_outcome_rate_by_source_date, lambda: (_owner_rate(),)),
    ("duration", plots.plot_run_duration_by_source, lambda: (_duration(),)),
    ("finish_hour", plots.plot_finish_hour_distribution, lambda: (_finish_hour(),)),
    ("active", plots.plot_active_hotel_by_date, lambda: (_active(),)),
    ("heatmap", plots.plot_crawl_date_lead_time_heatmap, lambda: (_heatmap(),)),
    ("weekday_month", plots.plot_checkin_coverage_weekday_month, lambda: (_weekday(), _month())),
    ("lead", plots.plot_lead_time_bucket_distribution, lambda: (_lead(),)),
    ("hist", plots.plot_price_histograms, lambda: (_hist_linear(), _hist_log())),
    ("box", plots.plot_price_box_by_city, lambda: (_box(),)),
    ("price_bucket", plots.plot_price_by_lead_time_bucket, lambda: (_price_bucket(),)),
    ("missingness", plots.plot_missingness_heatmap, lambda: (_missingness(),)),
    ("ref", plots.plot_reference_coverage_by_lead_time, lambda: (_ref_exact(), _ref_legacy())),
    ("turnover", plots.plot_series_turnover, lambda: (_by_days(), _max_gap(), _median_gap())),
    ("history", plots.plot_history_length, lambda: (_history(),)),
    ("readiness", plots.plot_readiness_by_horizon, lambda: (_readiness(),)),
]


@pytest.mark.parametrize("label,func,args", CASES, ids=[c[0] for c in CASES])
def test_ve_duoc_tu_bang_nho(label, func, args):
    fig = func(*args())
    assert isinstance(fig, plt.Figure) and len(fig.axes) >= 1
    plt.close(fig)


@pytest.mark.parametrize("label,func,args", CASES, ids=[c[0] for c in CASES])
def test_ve_duoc_tu_bang_rong_khong_vo(label, func, args):
    empties = tuple(frame.iloc[0:0] for frame in args())
    fig = func(*empties)
    assert isinstance(fig, plt.Figure)
    plt.close(fig)


def test_availability_stacked_ve_du_5_status_ke_ca_status_0():
    fig = plots.plot_item_availability_stacked(_availability(), group_col="city", title="t")
    ax = fig.axes[0]
    assert len(ax.patches) == 2 * 5  # 2 city x 5 status (partial/error = 0 van co thanh)
    plt.close(fig)
    empty = plots.plot_item_availability_stacked(_availability().iloc[0:0], group_col="city", title="t")
    plt.close(empty)


def test_availability_stacked_legend_nam_ngoai_khung_khong_che_cot():
    """Official run #1 (file 16): legend trong khung che phan tren cot ben phai (sold_out/not_bookable/error) - legend phai nam NGOAI truc."""
    fig = plots.plot_item_availability_stacked(_availability(), group_col="city", title="t")
    fig.canvas.draw()
    ax = fig.axes[0]
    assert ax.get_legend().get_window_extent().x0 >= ax.get_window_extent().x1 - 1e-6
    plt.close(fig)


def test_series_turnover_truc_so_that_khong_chong_nhan_khi_co_nhieu_gia_tri_median_gap():
    """Official run #1: median_gap_days co ~40 gia tri (0, 0.5, ... 28) - ban cu ve 1 nhan chuoi/cot nen chu de len nhau. Truc so that: so tick bi chan,
    do rong cot phan biet buoc 0.5, gia tri x van dung (khong bi ep ve thu tu chuoi)."""
    median_gap = pd.DataFrame({"median_gap_days": [i / 2 for i in range(0, 57)], "n_series": list(range(1, 58))})
    fig = plots.plot_series_turnover(_by_days(), _max_gap(), median_gap)
    third = fig.axes[2]
    fig.canvas.draw()
    visible = [t for t in third.get_xticklabels() if t.get_text() and third.get_xlim()[0] <= t.get_position()[0] <= third.get_xlim()[1]]
    assert 0 < len(visible) <= 12
    centers = sorted(float(p.get_x() + p.get_width() / 2) for p in third.patches)
    assert centers[:3] == [0.0, 0.5, 1.0] and len(third.patches) == 57
    assert "0 = series 1 ngay" in third.get_xlabel() and "0 = series 1 ngay" in fig.axes[1].get_xlabel()
    plt.close(fig)


def test_weekday_month_plot_ghi_so_ngay_anchor_len_moi_cot_file_17_m4():
    fig = plots.plot_checkin_coverage_weekday_month(_weekday(), _month())
    assert sorted(t.get_text() for t in fig.axes[0].texts) == ["1 ngay", "2 ngay"]
    assert sorted(t.get_text() for t in fig.axes[1].texts) == ["1 ngay", "5 ngay"]
    assert "anchor" in fig.axes[0].get_title() and "anchor" in fig.axes[1].get_title()
    plt.close(fig)
    legacy = plots.plot_checkin_coverage_weekday_month(_weekday().drop(columns="n_distinct_checkin_dates"), _month().drop(columns="n_distinct_checkin_dates"))
    assert not legacy.axes[0].texts   # khong co cot anchor -> khong ghi nhan (khong bia so)
    plt.close(legacy)


def test_active_hotel_plot_chu_thich_cohort_transition_file_17_minor_2():
    cohort = pd.DataFrame({"cohort_version": ["v1.0", "v2"], "effective_from_crawl_date": ["2026-09-01", "2026-09-02"], "size": [355, 354],
                           "change_type": ["baseline", "attrition"]})
    fig = plots.plot_active_hotel_by_date(_active(), cohort)
    ax = fig.axes[0]
    labels = [t.get_text() for t in ax.texts]
    assert any("cohort v2: 355 -> 354" in text and "attrition" in text for text in labels) and len(labels) == 1   # baseline khong phai transition
    assert len(ax.lines) == 2       # 1 duong du lieu + 1 vach cohort
    assert "khong bat dau tu 0" in ax.get_ylabel()
    plt.close(fig)
    plain = plots.plot_active_hotel_by_date(_active())
    assert not plain.axes[0].texts
    plt.close(plain)


def test_finish_hour_plot_chi_ve_run_production_khong_ve_pilot_file_17_m2():
    frame = pd.DataFrame({"source_code": ["a", "a"], "is_protocol_run": [True, False], "finish_hour_vn": [17, 9], "n_runs": [3, 1]})
    fig = plots.plot_finish_hour_distribution(frame)
    assert sum(patch.get_height() for patch in fig.axes[0].patches) == 3   # 1 run pilot (gio 9) khong duoc ve
    plt.close(fig)
    only_pilot = plots.plot_finish_hour_distribution(frame.assign(is_protocol_run=False))
    assert not only_pilot.axes[0].patches
    plt.close(only_pilot)


def test_histogram_ve_dung_so_bin_tu_bin_count_sql():
    fig = plots.plot_price_histograms(_hist_linear(), _hist_log())
    assert [len(ax.patches) for ax in fig.axes] == [2, 2]
    heights = [p.get_height() for p in fig.axes[0].patches]
    assert heights == [10, 5]
    plt.close(fig)


def test_box_plot_dung_ban_quantile_p25_p75_khong_ve_outlier():
    fig = plots.plot_price_box_by_city(_box())
    ax = fig.axes[0]
    boxes = [line for line in ax.lines if len(line.get_ydata()) == 2]
    assert len(ax.get_xticklabels()) == 2 and len(boxes) > 0
    medians = sorted(float(line.get_ydata()[0]) for line in ax.lines if len(line.get_ydata()) == 2 and line.get_ydata()[0] == line.get_ydata()[1]
                     and line.get_ydata()[0] in (4.0, 5.0))
    assert medians == [4.0, 5.0]
    plt.close(fig)


def test_heatmap_giu_thu_tu_bucket_chuan():
    frame = pd.DataFrame({"vn_crawl_date": _dates(1) * 3, "lead_time_bucket": ["61+", "0", "8-14"], "n_items": [1, 1, 1]})
    fig = plots.plot_crawl_date_lead_time_heatmap(frame)
    labels = [t.get_text() for t in fig.axes[0].get_xticklabels()]
    assert labels == ["0", "8-14", "61+"]
    plt.close(fig)
    assert np.isfinite(_hist_linear()["n_obs"]).all()
