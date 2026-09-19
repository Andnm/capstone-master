"""Integration test (MySQL that, disposable warehouse tu `build_warehouse()`) cho phan con lai cua query catalog: item status counts, RAW vs MAIN,
run/ngay bat thuong, coverage 7.4, missingness, reference/evidence, history length, quality check (KEM tiem vi pham vao ban sao de chung minh
check THUC SU phat hien duoc, khong chi tra 0 tren du lieu sach). So lieu ky vong DEM TAY tu `fixture_specs.price_fixture_spec()` (xem docstring cua no).

Fixture (1 nguon local_primary, tat ca 10 item deu OWNER): item 1-3 = (h1, 20/09, crawl 01/09, 02/09, 06/09); 4 = (h1, 10/09, 01/09);
5 = (h2, 10/09, 01/09); 6 = (h3, 10/09, 01/09); 7 = h3 sold_out (12/09, crawl 02/09); 8 = h2 not_bookable (12/09, 02/09); 9 = error hotel NULL (resolve ->
h1, 12/09, crawl 06/09); 10 = (h3, 12/09, 06/09). h1/h2 = Ha Noi, h3 = Da Lat.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import db
import metrics
import queries
from warehouse_fixture import mutate_row, query_one

pytestmark = pytest.mark.mysql
D = dt.date


def _run(price_db, metric_id, **kwargs):
    conn, snapshot, _ = price_db
    return queries.run_metric(metric_id, conn, snapshot, **kwargs)


def _by(df: pd.DataFrame, key: str) -> pd.DataFrame:
    return df.set_index(key)


# ---------------------------------------------------------------- 7.1 preflight
def test_preflight_rejections_va_import_sources(price_db):
    rejections = _run(price_db, "preflight_rejections").iloc[0]
    assert int(rejections["n_rejections"]) == 0 and int(rejections["n_unwaived"]) == 0
    sources = _run(price_db, "preflight_import_sources")
    assert list(sources["source_code"]) == ["local_primary"] and int(sources.iloc[0]["source_priority"]) == 0


def test_run_metric_tu_choi_schema_lech(price_db, monkeypatch):
    """Contract catalog: metric tra sai cot thi raise MetricSchemaError (khong am tham tra ve DataFrame sai hop dong)."""
    conn, snapshot, _ = price_db
    original = queries.CATALOG["preflight_rejections"]
    monkeypatch.setitem(queries.CATALOG, "preflight_rejections", original.__class__(**{**original.__dict__, "output_schema": ("khac",)}))
    with pytest.raises(queries.MetricSchemaError, match="KHONG khop"):
        queries.run_metric("preflight_rejections", conn, snapshot)


# ---------------------------------------------------------------- 7.8 item status counts (item grain, aggregate SQL)
def test_item_status_counts_overall_dem_dung_va_khong_bo_status_nao(price_db):
    row = _run(price_db, "item_status_counts_overall_main").iloc[0]
    assert (int(row["n_items"]), int(row["n_success"]), int(row["n_sold_out"]), int(row["n_not_bookable"]),
            int(row["n_partial"]), int(row["n_error"])) == (10, 7, 1, 1, 0, 1)
    assert metrics.status_present_report(_run(price_db, "item_status_counts_overall_main")) == {
        "success": 7, "sold_out": 1, "not_bookable": 1, "partial": 0, "error": 1}


def test_item_status_counts_by_city_hotel_va_month(price_db):
    by_city = _by(_run(price_db, "item_status_counts_by_city_main"), "city")
    assert (int(by_city.loc["Hà Nội", "n_items"]), int(by_city.loc["Hà Nội", "n_success"]), int(by_city.loc["Hà Nội", "n_not_bookable"])) == (6, 5, 1)
    assert (int(by_city.loc["Đà Lạt", "n_items"]), int(by_city.loc["Đà Lạt", "n_sold_out"])) == (3, 1)
    assert int(by_city.loc["(unknown)", "n_error"]) == 1  # item 9: hotel_id NULL => city '(unknown)'
    assert int(by_city["n_items"].sum()) == 10

    by_hotel = _by(_run(price_db, "item_status_counts_by_hotel_main"), "hotel_id")
    assert int(by_hotel.loc["h1", "n_items"]) == 4 and int(by_hotel.loc["h1", "n_success"]) == 4
    assert int(by_hotel.loc["h2", "n_not_bookable"]) == 1 and int(by_hotel.loc["h3", "n_sold_out"]) == 1
    assert int(by_hotel.loc["(unattributed)", "n_error"]) == 1

    by_month = _run(price_db, "item_status_counts_by_checkin_month_main")
    assert list(by_month["checkin_month"]) == ["2026-09"] and int(by_month.iloc[0]["n_items"]) == 10


def test_item_status_counts_by_lead_time_bucket_thu_tu_bucket_va_item_lead_time(price_db):
    """Lead time cua ITEM = checkin_date - ngay crawl VN: item 1,2 = 19,18 (15-30); 3..8 = 14,9,9,9,10,10 (8-14); 9,10 = 6 (4-7)."""
    out = _run(price_db, "item_status_counts_by_lead_time_bucket_main")
    assert list(out["lead_time_bucket"]) == ["4-7", "8-14", "15-30"]  # thu tu bucket chuan, khong theo chu cai
    rows = _by(out, "lead_time_bucket")
    assert (int(rows.loc["15-30", "n_items"]), int(rows.loc["8-14", "n_items"]), int(rows.loc["4-7", "n_items"])) == (2, 6, 2)
    assert (int(rows.loc["8-14", "n_success"]), int(rows.loc["8-14", "n_sold_out"]), int(rows.loc["8-14", "n_not_bookable"])) == (4, 1, 1)
    assert int(rows.loc["4-7", "n_error"]) == 1 and int(rows.loc["4-7", "n_success"]) == 1


def test_item_status_counts_by_crawl_date_hotel_co_not_bookable_rate_theo_ngay_va_hotel(price_db):
    out = metrics.item_status_rates(_run(price_db, "item_status_counts_by_crawl_date_hotel_main"))
    assert len(out) == 9 and int(out["n_items"].sum()) == 10
    row = out[(out["crawl_date"] == D(2026, 9, 2)) & (out["hotel_id"] == "h2")].iloc[0]
    assert int(row["n_items"]) == 1 and row["not_bookable_rate"] == 1.0
    assert out[(out["crawl_date"] == D(2026, 9, 1)) & (out["hotel_id"] == "h1")].iloc[0]["n_items"] == 2


def test_run_day_status_va_error_code_counts_raw(price_db):
    days = _by(_run(price_db, "run_day_status_counts_raw").assign(d=lambda f: f["vn_crawl_date"].astype(str)), "d")
    assert (int(days.loc["2026-09-01", "n_items"]), int(days.loc["2026-09-01", "n_success"])) == (4, 4)
    assert (int(days.loc["2026-09-02", "n_sold_out"]), int(days.loc["2026-09-02", "n_not_bookable"])) == (1, 1)
    assert (int(days.loc["2026-09-06", "n_error"]), int(days.loc["2026-09-06", "n_success"])) == (1, 2)
    codes = _run(price_db, "run_day_error_code_counts_raw")
    assert sorted(zip(codes["status"], codes["last_error_code"], codes["n_items"])) == [("error", "(none)", 1), ("not_bookable", "(none)", 1)]


# ---------------------------------------------------------------- 7.2 RAW vs MAIN, ownership
def test_raw_vs_main_by_source_dem_item_va_observation(price_db):
    row = _run(price_db, "raw_vs_main_by_source").iloc[0]
    assert row["source_code"] == "local_primary"
    assert (int(row["n_items_raw"]), int(row["n_items_main"])) == (10, 10)
    assert (int(row["n_observations_raw"]), int(row["n_observations_main"])) == (25, 25)          # 24 co gia + 1 sentinel sold-out
    assert (int(row["n_priced_observations_raw"]), int(row["n_priced_observations_main"])) == (24, 24)


def test_ownership_by_source_status_reason_toan_bo_item_la_owner(price_db):
    out = _run(price_db, "ownership_by_source_status_reason")
    counts = out.groupby("ownership_status")["n_items"].sum().to_dict()
    assert counts == {"owner_success": 7, "owner_failure": 3}


# ---------------------------------------------------------------- 7.3/7.4 coverage
def test_run_duration_and_throughput_dem_slot_va_gio_hoan_thanh(price_db):
    out = _run(price_db, "run_duration_and_throughput").sort_values("run_id").reset_index(drop=True)
    assert list(out["n_items"]) == [4, 3, 3] and list(out["n_observations"]) == [20, 2, 3]
    assert list(out["n_checkin_slots"]) == [2, 2, 2]
    assert (out["duration_minutes"] == 30).all() and not out["crosses_next_crawl_day"].any()
    assert (pd.to_datetime(out["finished_at_vn"]).dt.hour == 17).all()  # 10:30 UTC = 17:30 VN
    assert (out["items_per_hour"] > 0).all()


def test_active_hotel_checkin_tracked_va_heatmap(price_db):
    active = _run(price_db, "active_hotel_by_crawl_date_source").assign(d=lambda f: f["vn_crawl_date"].astype(str)).set_index("d")
    assert [int(active.loc[d, "n_active_hotels"]) for d in ("2026-09-01", "2026-09-02", "2026-09-06")] == [3, 3, 2]  # 09-06: item 9 hotel NULL khong dem
    by_city = _run(price_db, "active_hotel_by_crawl_date_source_city")
    assert int(by_city["n_active_hotels"].sum()) == 3 + 3 + 2 and "(unknown)" not in set(by_city["city"])
    tracked = _run(price_db, "checkin_dates_tracked_by_crawl_date_source")
    assert (tracked["n_checkin_dates_tracked"] == 2).all() and len(tracked) == 3
    heat = _run(price_db, "crawl_date_lead_time_bucket_heatmap")
    assert int(heat["n_items"].sum()) == 10
    cell = heat[(heat["vn_crawl_date"] == D(2026, 9, 6)) & (heat["lead_time_bucket"] == "4-7")].iloc[0]
    assert int(cell["n_items"]) == 2


def test_checkin_weekday_distribution_co_co_weekend_fri_sat(price_db):
    out = _by(_run(price_db, "checkin_weekday_distribution_main"), "weekday")
    assert (int(out.loc["Thursday", "n_observations"]), int(out.loc["Saturday", "n_observations"]), int(out.loc["Sunday", "n_observations"])) == (19, 2, 3)
    assert bool(out.loc["Saturday", "is_weekend_fri_sat"]) and not bool(out.loc["Thursday", "is_weekend_fri_sat"])
    assert int(out["n_observations"].sum()) == 24 and list(out["weekday_number"]) == sorted(out["weekday_number"])


# ---------------------------------------------------------------- 7.9 missingness theo item status / sold-out (structural vs unexpected)
def test_missingness_by_item_status_sold_out_tach_structural_khoi_unexpected(price_db):
    out = _run(price_db, "missingness_by_item_status_sold_out")
    sentinel = out[(out["item_status"] == "sold_out") & out["is_sold_out"]]
    assert set(sentinel["n_total"]) == {1}
    payload = sentinel[sentinel["field_group"].isin(["room_identity", "rate_plan", "price"])]
    assert (payload["missing_kind"] == "structural_expected").all() and (payload["n_null"] == 1).all()  # sentinel khong co payload phong/gia
    metadata = sentinel[~sentinel["field_group"].isin(["room_identity", "rate_plan", "price"])]
    assert (metadata["missing_kind"] == "unexpected_if_null").all()
    scraper_version = metadata[metadata["field"] == "scraper_version"].iloc[0]
    assert int(scraper_version["n_null"]) == 0  # run metadata van co gia tri ke ca tren sentinel
    available = out[(out["item_status"] == "success") & ~out["is_sold_out"]]
    assert set(available["n_total"]) == {24} and (available["missing_kind"] == "unexpected_if_null").all()
    price = available[available["field"] == "price_per_night"].iloc[0]
    assert int(price["n_null"]) == 0  # observation available khong bao gio NULL gia
    assert int(available[available["field"] == "taxes_fees"].iloc[0]["n_null"]) == 24  # fixture khong ghi taxes_fees


def test_missingness_theo_selector_version_dung_bucket_unknown(price_db):
    out = _by(_run(price_db, "missingness_by_selector_version").query("field == 'price_per_night'"), "selector_version")
    assert int(out.loc["sel-1", "n_total"]) == 21 and int(out.loc["(unknown)", "n_total"]) == 3  # run 3 khong co selector_version (3 observation)


# ---------------------------------------------------------------- 7.10/7.12 reference, evidence, history
def test_reference_series_a_du_3_run_thi_approved_va_item_level_availability(price_db):
    """Series A (h1, 20/09) co 3 run completed, coverage 100% => approved; cac series con lai chi 1 run => proposed."""
    status = _by(_run(price_db, "reference_status_evidence_summary"), "status")
    assert int(status.loc["approved", "n_references"]) == 1 and int(status.loc["approved", "min_runs"]) == 3
    assert int(status.loc["approved", "n_with_ge3_runs"]) == 1 and float(status.loc["approved", "min_coverage"]) >= 0.8
    assert (int(status.loc["proposed", "max_runs"]) == 1) and int(status.loc["proposed", "n_with_ge3_runs"]) == 0
    uniq = _by(_run(price_db, "reference_uniqueness_per_series"), "n_approved")
    assert int(uniq.loc[1, "n_series"]) == 1 and 0 in uniq.index and 2 not in uniq.index  # khong series nao co > 1 approved
    item_level = metrics.item_level_exact_reference_availability(_run(price_db, "reference_item_level_availability_main")).set_index("lead_time_bucket")
    assert (int(item_level.loc["15-30", "n_items"]), int(item_level.loc["15-30", "n_items_matched"])) == (2, 2)
    assert (int(item_level.loc["8-14", "n_items"]), int(item_level.loc["8-14", "n_items_matched"])) == (1, 1)
    assert (item_level["availability_rate"] == 1.0).all()


def test_reference_observation_coverage_exact_key_va_series_exists_legacy(price_db):
    exact = metrics.exact_approved_key_observation_coverage(_run(price_db, "reference_observation_match_main")).set_index("lead_time_bucket")
    assert int(exact["n_observations"].sum()) == 24 and int(exact["n_matched"].sum()) == 3  # chi 3 observation cua series A khop dung key approved
    assert list(exact.index) == ["4-7", "8-14", "15-30"]  # thu tu bucket CHUAN (khong theo chu cai: '15-30' khong dung truoc '4-7')
    legacy = metrics.series_with_approved_reference_coverage(
        _run(price_db, "reference_series_exists_observation_coverage_raw_legacy_bucket")).set_index("lead_time_bucket")
    assert list(legacy.index) == ["4-7", "8-14", "15-30"]  # lead time fixture: 6 (2 obs), 9/14 (20), 18/19 (2); bucket legacy 0-3 gop 0+1-3 khong co; dung thu tu
    assert int(legacy["n_series_has_reference"].sum()) == 3 and int(legacy["n_observations"].sum()) == 24
    standard = _run(price_db, "reference_series_exists_observation_coverage_raw")
    assert "0-3" not in set(standard["lead_time_bucket"]) and int(standard["n_observations"].sum()) == 24


def test_series_evidence_runs_distribution_va_share(price_db):
    dist = _run(price_db, "series_evidence_runs_distribution")
    assert dist.groupby("evidence_runs_bucket")["n_series"].sum().to_dict() == {"1": 4, "3+": 1}
    share = metrics.evidence_runs_share(dist).iloc[0]
    assert int(share["n_series"]) == 5 and int(share["n_series_ge3"]) == 1 and share["share_ge3"] == pytest.approx(0.2)
    by_city = _by(metrics.evidence_runs_share(dist, group_cols=("city",)), "city")
    assert by_city.loc["Hà Nội", "share_ge3"] == pytest.approx(1 / 3) and by_city.loc["Đà Lạt", "share_ge3"] == 0.0


def test_history_length_by_hotel_checkin_dem_ngay_co_snapshot_success(price_db):
    out = _run(price_db, "history_length_by_hotel_checkin_main")
    assert int(out["n_series"].sum()) == 5  # (h1,20/09) (h1,10/09) (h2,10/09) (h3,10/09) (h3,12/09) - item 7 sold_out KHONG tinh snapshot
    long = out[out["history_days_bucket"] == "3-6"].iloc[0]
    assert (long["city"], int(long["n_series"]), int(long["sum_span_days"])) == ("Hà Nội", 1, 6)  # (h1, 20/09): 3 ngay trong span 6 ngay
    assert int(out[out["history_days_bucket"] == "1"]["n_series"].sum()) == 4


def test_reference_candidate_coverage_summary_dem_candidate_va_series(price_db):
    """Moi option la 1 candidate rieng: 1 (series A) + 10 (h1,10/09) + 3 (h2,10/09) + 6 (h3,10/09) + 2 (h3,12/09) = 22 candidate / 5 series."""
    row = _run(price_db, "reference_candidate_coverage_summary").iloc[0]
    assert (int(row["n_candidates"]), int(row["n_series_with_candidates"]), int(row["max_candidates_per_series"])) == (22, 5, 10)
    assert float(row["mean_candidates_per_series"]) == pytest.approx(22 / 5)
    assert int(row["max_distinct_run_count"]) == 3 and int(row["n_candidates_ge3_runs"]) == 1  # chi candidate cua series A co du 3 run
    assert float(row["mean_item_coverage"]) > 0


def test_run_item_observation_by_source_crawl_date_dem_dung_theo_ngay_va_nguon(price_db):
    out = _run(price_db, "run_item_observation_by_source_crawl_date").assign(d=lambda f: f["vn_crawl_date"].astype(str)).set_index("d")
    assert [(int(out.loc[d, "runs"]), int(out.loc[d, "items"]), int(out.loc[d, "observations"])) for d in ("2026-09-01", "2026-09-02", "2026-09-06")] == [
        (1, 4, 20), (1, 3, 2), (1, 3, 3)]
    assert set(out["source_code"]) == {"local_primary"}


def test_missingness_du_5_field_group_va_cac_field_cua_plan_7_9(price_db):
    """Plan 7.9: room identity / rate plan / price+taxes / review+hotel attributes / artifact+source metadata (currency khong luu - ep VND qua URL)."""
    out = _run(price_db, "missingness_available_observations")
    groups = out.groupby("field_group")["field"].apply(set).to_dict()
    assert groups == {
        "room_identity": {"room_type_raw", "max_occupancy", "bed_config", "room_area"},
        "rate_plan": {"breakfast_included", "free_cancellation", "cancellation_policy", "price_includes_tax"},
        "price": {"price_per_night", "taxes_fees"},
        "hotel_attributes": {"review_score", "review_count", "address"},
        "artifact_source_metadata": {"scraper_version", "selector_version", "git_commit"},
    }
    assert set(out["n_total"]) == {24} and int(out[out["field"] == "taxes_fees"].iloc[0]["n_null"]) == 24
    overall = metrics.missingness_overall(out).set_index("field")
    assert int(overall.loc["taxes_fees", "n_total"]) == 24 and overall.loc["taxes_fees", "null_rate"] == 1.0


def test_artifact_completeness_tach_requested_va_structural_not_requested(price_wh):
    fx = price_wh
    run_id = query_one(fx, "SELECT id FROM crawl_runs ORDER BY started_at LIMIT 1")[0]
    item_id = query_one(fx, "SELECT id FROM crawl_run_items WHERE crawl_run_id=%s ORDER BY id LIMIT 1", (run_id,))[0]
    with mutate_row(fx, "crawl_runs", "id", run_id, {"save_artifacts": 1}):
        with mutate_row(fx, "crawl_run_items", "id", item_id, {
            "artifact_html_path": "artifacts/one.html.gz", "screenshot_path": "artifacts/one.png",
        }):
            with db.connect(pointer_path=fx["pointer_path"]) as (conn, snapshot):
                out = queries.run_metric("artifact_completeness_by_source_crawl_date", conn, snapshot)
    requested = out[out["save_artifacts"]].iloc[0]
    structural = out[~out["save_artifacts"]]
    assert requested["missing_kind"] == "unexpected_if_missing"
    assert int(requested["n_items"]) == 4 and int(requested["n_html_present"]) == 1
    assert int(requested["n_html_missing"]) == 3 and requested["html_coverage_rate"] == pytest.approx(0.25)
    assert len(structural) == 2 and set(structural["missing_kind"]) == {"structural_not_requested"}


def test_missingness_theo_ngay_crawl_va_city_cong_lai_bang_tong(price_db):
    """Cac lat cat (theo ngay crawl, city) cua CUNG 1 field phai cong lai dung 24 observation available - khong nhan/mat dong."""
    by_date = _run(price_db, "missingness_by_crawl_date").query("field == 'price_per_night'")
    assert int(by_date["n_total"].sum()) == 24 and len(by_date) == 3
    assert dict(zip(by_date["vn_crawl_date"].astype(str), by_date["n_total"])) == {"2026-09-01": 20, "2026-09-02": 1, "2026-09-06": 3}
    by_city = _by(_run(price_db, "missingness_by_city").query("field == 'price_per_night'"), "city")
    assert (int(by_city.loc["Hà Nội", "n_total"]), int(by_city.loc["Đà Lạt", "n_total"])) == (16, 8)


# ---------------------------------------------------------------- 7.11 quality checks: TIEM vi pham vao ban sao roi chung minh check phat hien duoc
# Khoa THAT trong warehouse (id duoc gan lai khi build, KHONG trung id nguon) tim bang cau hoi nghiep vu; gia tri goc duoc doc truoc va tu khoi phuc.
def _fresh(fx, metric_id):
    """Ket noi DOC MOI (REPEATABLE READ snapshot moi) - ket noi session dung chung se khong thay UPDATE vua commit."""
    with db.connect(pointer_path=fx["pointer_path"]) as (conn, snapshot):
        return queries.run_metric(metric_id, conn, snapshot), queries.quality_violation_samples(conn, snapshot)


def _one(frame: pd.DataFrame, column: str) -> int:
    return int(frame.iloc[0][column])


def _first_priced_record(fx) -> tuple[int, int]:
    """(record_id, crawl_run_item_id) cua observation co gia dau tien."""
    return query_one(fx, "SELECT record_id, crawl_run_item_id FROM price_observations WHERE is_sold_out=0 ORDER BY record_id LIMIT 1")


def test_quality_price_va_checkout_va_total_per_night_bat_duoc_vi_pham(price_wh):
    fx = price_wh
    record_id, _ = _first_priced_record(fx)
    with mutate_row(fx, "price_observations", "record_id", record_id, {"price_per_night": 0}):
        frame, samples = _fresh(fx, "quality_price_non_positive")
        assert _one(frame, "n_violations") == 1 and samples["price_non_positive"] == [record_id]
    with mutate_row(fx, "price_observations", "record_id", record_id, {"checkout_date": lambda row: row["checkout_date"] - dt.timedelta(days=1)}):
        frame, samples = _fresh(fx, "quality_checkout_not_after_checkin")
        assert _one(frame, "n_violations") == 1 and samples["checkout_not_after_checkin"] == [record_id]
    with mutate_row(fx, "price_observations", "record_id", record_id, {"price_total": lambda row: row["price_total"] + 1}):
        frame, samples = _fresh(fx, "quality_price_total_per_night_inconsistent")
        assert _one(frame, "n_violations") == 1 and samples["price_total_per_night_inconsistent"] == [record_id]
    frame, _ = _fresh(fx, "quality_price_non_positive")  # da hoan tac (gia tri GOC): sach tro lai
    assert _one(frame, "n_violations") == 0
    assert _one(_fresh(fx, "quality_price_total_per_night_inconsistent")[0], "n_violations") == 0


def test_quality_lead_time_va_parent_mismatch_bat_duoc_vi_pham(price_wh):
    fx = price_wh
    record_id, _ = _first_priced_record(fx)
    with mutate_row(fx, "price_observations", "record_id", record_id, {"lead_time": lambda row: row["lead_time"] + 1}):
        frame, samples = _fresh(fx, "quality_lead_time_mismatch")
        assert (_one(frame, "n_mismatch"), _one(frame, "n_total")) == (1, 25) and samples["lead_time_mismatch"] == [record_id]
    with mutate_row(fx, "price_observations", "record_id", record_id, {"checkin_date": lambda row: row["checkin_date"] + dt.timedelta(days=1)}):
        frame, samples = _fresh(fx, "quality_parent_mismatch")
        assert _one(frame, "n_mismatch") == 1 and samples["parent_mismatch"] == [record_id]
    assert _one(_fresh(fx, "quality_lead_time_mismatch")[0], "n_mismatch") == 0 and _one(_fresh(fx, "quality_parent_mismatch")[0], "n_mismatch") == 0


def test_quality_duplicate_daily_series_bat_duoc_2_option_trung_canonical_key(price_wh):
    """2 option CUNG item co cung canonical (room, rate) key => 1 nhom trung, 1 observation du."""
    fx = price_wh
    item_id, = query_one(fx, "SELECT crawl_run_item_id FROM price_observations WHERE is_sold_out=0 GROUP BY crawl_run_item_id "
                             "ORDER BY COUNT(*) DESC, crawl_run_item_id LIMIT 1")
    first, second = [r for (r,) in [query_one(fx, "SELECT record_id FROM price_observations WHERE crawl_run_item_id=%s ORDER BY record_id LIMIT 1 OFFSET %s",
                                             (item_id, offset)) for offset in (0, 1)]]
    room_key, rate_key, series_id = query_one(
        fx, "SELECT canonical_room_key, canonical_rate_key, canonical_series_id FROM curated_observation_keys WHERE record_id=%s", (first,))
    with mutate_row(fx, "curated_observation_keys", "record_id", second,
                    {"canonical_room_key": room_key, "canonical_rate_key": rate_key, "canonical_series_id": series_id}):
        frame, samples = _fresh(fx, "quality_duplicate_daily_series")
        assert (_one(frame, "n_duplicate_groups"), _one(frame, "n_extra_observations")) == (1, 1) and samples["duplicate_daily_series"] == [item_id]
    assert _one(_fresh(fx, "quality_duplicate_daily_series")[0], "n_duplicate_groups") == 0


def test_quality_canonical_key_va_sentinel_bat_duoc_sentinel_sai_key(price_wh):
    """Sentinel sold-out bi gan canonical room key that => vua canonical_key_anomalies vua sold_out_sentinel_consistency."""
    fx = price_wh
    sentinel_record, sold_out_item = query_one(fx, "SELECT record_id, crawl_run_item_id FROM price_observations WHERE is_sold_out=1")
    with mutate_row(fx, "curated_observation_keys", "record_id", sentinel_record, {"canonical_room_key": "a" * 64}):
        frame, samples = _fresh(fx, "quality_canonical_key_anomalies")
        assert _one(frame, "n_soldout_with_room_key") == 1 and _one(frame, "n_nonsoldout_missing_canonical") == 0
        assert samples["canonical_key_anomalies"] == [sentinel_record]
        frame, samples = _fresh(fx, "quality_sold_out_sentinel_consistency")
        assert (_one(frame, "n_violations"), _one(frame, "n_total")) == (1, 1) and samples["sold_out_sentinel_consistency"] == [sold_out_item]
    assert _one(_fresh(fx, "quality_sold_out_sentinel_consistency")[0], "n_violations") == 0


def test_quality_success_item_khong_observation_va_city_ngoai_scope(price_wh):
    fx = price_wh
    not_bookable_item, = query_one(fx, "SELECT id FROM crawl_run_items WHERE status='not_bookable'")  # item khong co observation
    with mutate_row(fx, "crawl_run_items", "id", not_bookable_item, {"status": "success"}):
        frame, samples = _fresh(fx, "quality_success_item_without_observation")
        assert (_one(frame, "n_violations"), _one(frame, "n_total")) == (1, 8) and samples["success_item_without_observation"] == [not_bookable_item]
    with mutate_row(fx, "hotels", "hotel_id", "h2", {"city": "Hải Phòng"}):
        frame, samples = _fresh(fx, "quality_city_outside_scope")
        assert (_one(frame, "n_violations"), _one(frame, "n_total")) == (1, 3) and samples["city_outside_scope"] == ["h2"]
    assert _one(_fresh(fx, "quality_success_item_without_observation")[0], "n_violations") == 0
    assert _one(_fresh(fx, "quality_city_outside_scope")[0], "n_violations") == 0


def test_quality_findings_sach_tren_fixture_khong_tiem(price_db):
    """Doi chung voi cac test tiem o tren: fixture goc SACH (moi check = 0) - de biet khang dinh 'bat duoc vi pham' khong phai do check luon > 0."""
    for metric_id in queries.QUALITY_SCALAR_IDS:
        if metric_id == "quality_unexpected_nulls_by_field_group":
            continue  # fixture co cot NULL ngoai du kien co chu y (123/360) - kiem o test dry-run
        frame = _run(price_db, metric_id).fillna(0)
        violations = [c for c in frame.columns if c.startswith(("n_violations", "n_mismatch", "n_duplicate_groups", "n_nonsoldout", "n_soldout"))]
        assert violations, metric_id
        assert all(int(frame.iloc[0][c]) == 0 for c in violations), (metric_id, frame.to_dict("records"))
