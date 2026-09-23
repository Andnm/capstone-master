"""Coverage matrix: 1 dong / bullet cua `EDA_CURATED_PLAN.md` muc 7.1-7.12 -> metric ID on dinh / artifact (bang, hinh) / grain / scope / denominator / test ID /
status (GPT review 12 eda file 11 muc 5: "Khong duoc chi dua vao viec report co 12 heading. Matrix nay phai nam trong artifact manifest va test phai fail neu mot
bullet bat buoc khong co mapping/artifact/test.").

Day la du lieu KHAI BAO (tuple), nhung KHONG duoc de tu tin - `src/tests/test_coverage_matrix.py` ep buoc:
  * moi bullet trong plan (parse truc tiep `EDA_CURATED_PLAN.md`) co dung 1 dong (`plan_bullet` khop dau dong), khong dong nao ngoai plan (tru dong `para:`);
  * moi `metric_id` la catalog metric / ten bang publish / ham that (`module.func`);
  * moi `artifact` thuoc `publication.known_artifact_paths()` (va co that trong artifact manifest o test E2E nbclient);
  * moi `test_id` (`file.py::test_name`) tro toi ham test THAT (AST);
  * khong bang/hinh publish nao khong duoc dong nao tham chieu;
  * `status == 'implemented'` (neu khong: report ghi PARTIAL, khong duoc goi la full Wave A).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import publication

COLUMNS = ("plan_section", "bullet_id", "plan_bullet", "metric_ids", "artifacts", "grain", "scope", "denominator", "test_ids", "status")
_JOINED = ("metric_ids", "artifacts", "test_ids")


def T(*names: str) -> tuple[str, ...]:
    """Duong dan artifact cua cac bang publish."""
    return tuple(publication.table_artifact_path(name) for name in names)


def F(*names: str) -> tuple[str, ...]:
    return tuple(publication.figure_artifact_path(name) for name in names)


_REPORT = ("EDA_REPORT.md",)
_META = ("TABLE_METADATA.csv",)
_MANIFEST = ("input_manifest.json",)
_QF = ("quality_findings.csv",)
_DIC = ("DATA_DICTIONARY.md",)

_LIFECYCLE = "test_wave_a_dry_run.py::test_dry_run_lifecycle_day_du_thanh_cong"
_VALUES = "test_wave_a_dry_run.py::test_dry_run_gia_tri_bang_khop_fixture_biet_truoc"
_COLLISION = "test_wave_a_dry_run.py::test_dry_run_collision_tables_tren_fixture_2_nguon"
_E2E = "test_wave_a_dry_run.py::test_run_wave_a_end_to_end_nbclient_that_tren_notebook_01_that"


def R(section: str, plan_bullet: str, metric_ids: tuple, artifacts: tuple, grain: str, scope: str, denominator: str, test_ids: tuple,
      status: str = "implemented") -> dict:
    return {"plan_section": section, "plan_bullet": plan_bullet, "metric_ids": tuple(metric_ids), "artifacts": tuple(artifacts), "grain": grain,
            "scope": scope, "denominator": denominator, "test_ids": tuple(test_ids), "status": status}


# `plan_bullet`: dong DAU cua bullet trong EDA_CURATED_PLAN.md (co dau, chep nguyen van; test chuan hoa bo dau/markdown roi so 40 ky tu dau). Dong 'para:' la
# yeu cau dang van xuoi (khong phai bullet) cua plan.
_ROWS: list[dict] = [
    # ================================================================== 7.1 Preflight va snapshot identity
    R("7.1", "Load pointer và xác minh đúng DB/batch PASS.", ("db.connect", "db.load_pointer"), _MANIFEST, "warehouse", "RAW", "n/a",
      ("test_db.py::test_load_pointer_doc_dung_json", "test_db.py::test_connect_thuc_su_bi_chan_ghi_khong_can_goi_verify_rieng", _LIFECYCLE)),
    R("7.1", "Recompute/verify source manifest qua code warehouse hiện có nếu có thể gọi read-only.", ("wave_a.build_input_manifest",), _MANIFEST, "warehouse",
      "RAW", "n/a", (_LIFECYCLE,)),
    R("7.1", "In row count, date range, source list, schema/canonicalization version.", ("preflight_core_counts", "preflight_import_sources", "observation_date_ranges_main"),
      T("preflight_core_counts", "preflight_import_sources", "observation_date_ranges_main") + _MANIFEST, "warehouse", "RAW", "n/a",
      (_LIFECYCLE, "test_sql_catalog_integration.py::test_preflight_rejections_va_import_sources")),
    R("7.1", "Assert các count nền khớp validation report: runs/items/observations/curated keys/rejections.", ("preflight_rejections", "preflight_reconciliation"),
      T("preflight_reconciliation", "preflight_rejections") + _MANIFEST, "warehouse", "RAW", "khong co - so sanh tuyet doi",
      (_LIFECYCLE, "test_wave_a_dry_run.py::test_dry_run_that_bai_duoc_danh_dau_ro_khong_trong_nhu_pass")),
    R("7.1", "Assert không có run/item `queued` hoặc `running` trong snapshot.", ("preflight_non_terminal_runs_items",), _REPORT, "warehouse", "RAW",
      "khong co - assert = 0", ("test_wave_a_dry_run.py::test_collect_fail_khi_snapshot_co_item_chua_terminal",)),

    # ================================================================== 7.2 Source, ownership va protocol coverage
    R("7.2", "run/item/observation theo source và crawl date;", ("run_item_observation_by_source_crawl_date",),
      T("run_item_observation_by_source_crawl_date") + F("run_item_by_source_crawl_date"), "source x vn_crawl_date", "RAW", "khong co - bang mo ta",
      ("test_sql_catalog_integration.py::test_run_item_observation_by_source_crawl_date_dem_dung_theo_ngay_va_nguon",)),
    R("7.2", "item theo `ownership_status` + `exclusion_reason`;", ("ownership_by_source_status_reason",), T("ownership_by_source_status_reason"), "item",
      "RAW", "tong item RAW cua batch", ("test_sql_catalog_integration.py::test_ownership_by_source_status_reason_toan_bo_item_la_owner",)),
    R("7.2", "RAW so với MAIN theo source;", ("raw_vs_main_by_source",), T("raw_vs_main_by_source") + F("raw_vs_main_by_source"), "source", "RAW+MAIN",
      "khong co - doi chieu tuyet doi", ("test_sql_catalog_integration.py::test_raw_vs_main_by_source_dem_item_va_observation", _COLLISION)),
    R("7.2", "`non_owner_duplicate`, off-plan, pre-protocol pilot;", ("ownership_by_source_status_reason",), T("ownership_by_source_status_reason"), "item",
      "RAW", "tong item RAW cua batch", (_COLLISION,)),
    R("7.2", "collision audit theo `(crawl_date, checkin_date, hotel_id)`;",
      ("collision_item_pairs", "collision_item_status_concordance", "collision_item_summary", "collision_item_time_diff_stratification", "collision_option_detail",
       "collision_option_pair_coverage", "collision_option_summary", "collision_option_coverage_summary", "collision_option_time_diff_stratification"),
      T("collision_item_pairs", "collision_item_status_concordance", "collision_item_summary", "collision_item_time_diff_stratification", "collision_option_detail",
        "collision_option_pair_coverage", "collision_option_summary", "collision_option_coverage_summary", "collision_option_time_diff_stratification"),
      "item pair / shared canonical option pair", "RAW", "collision item-pairs; success-success item-pairs; shared canonical option-pairs (rieng tung lop)",
      (_COLLISION, "test_metrics.py::test_collision_item_summary_mau_so_rieng_cho_tung_lop", "test_metrics.py::test_collision_option_summary_mau_so_la_shared_option_pairs",
       "test_metrics.py::test_collision_option_time_diff_stratification_ty_le_exact_match_theo_bucket",
       "test_metrics.py::test_time_diff_minutes_bucket_gia_tri_thap_phan_khong_rot_khoi_bucket_nao",
       "test_metrics.py::test_collision_option_coverage_summary_jaccard_va_key_mo_ho")),
    R("7.2", "tỷ lệ owner success/failure theo ngày và nguồn.", ("protocol_outcome_rates_by_source_date",),
      T("protocol_outcome_rates_by_source_date") + F("owner_outcome_rate_by_source_date"), "source x vn_crawl_date", "PROTOCOL", "n_scheduled = slot expected cua nguon",
      ("test_metrics.py::test_protocol_outcome_rates_by_source_date_numerator_denominator_va_ba_ty_le_cong_lai_bang_1", _VALUES)),
    R("7.2", "para: Mọi tỷ lệ phải có numerator, denominator và định nghĩa denominator trong table metadata.", ("publication.PUBLISHED_TABLES",), _META + _DIC,
      "bang", "CONFIG", "denominator ghi o TABLE_METADATA.csv", (_LIFECYCLE, "test_publication.py::test_moi_bang_publish_co_du_metadata_khong_rong")),

    # ================================================================== 7.3 Crawl operations va capacity
    R("7.3", "Run duration từ `started_at → finished_at`.", ("run_duration_and_throughput",), T("run_duration_and_throughput") + F("run_duration_by_source"), "run",
      "RAW", "khong co - bang mo ta", ("test_sql_catalog_integration.py::test_run_duration_and_throughput_dem_slot_va_gio_hoan_thanh",)),
    R("7.3", "Items/giờ và observations/giờ.", ("run_duration_and_throughput",), T("run_duration_and_throughput"), "run", "RAW", "duration_minutes/60",
      ("test_sql_catalog_integration.py::test_run_duration_and_throughput_dem_slot_va_gio_hoan_thanh",)),
    R("7.3", "Thời điểm hoàn thành theo giờ Việt Nam.", ("metrics.finish_hour_distribution",), T("finish_hour_distribution") + F("finish_hour_distribution"),
      "source x gio VN", "RAW", "khong co - bang mo ta",
      ("test_metrics.py::test_finish_hour_distribution", "test_sql_catalog_integration.py::test_run_duration_and_throughput_dem_slot_va_gio_hoan_thanh")),
    R("7.3", "Run có kéo qua ngày crawl kế tiếp hay không.", ("run_duration_and_throughput",), T("run_duration_and_throughput"), "run", "RAW", "khong co - co boolean",
      ("test_sql_catalog_integration.py::test_run_duration_and_throughput_dem_slot_va_gio_hoan_thanh",)),
    R("7.3", "Phân bố duration/throughput theo source và theo số check-in slot.", ("run_duration_and_throughput",), T("run_duration_and_throughput") + F("run_duration_by_source"),
      "run", "RAW", "khong co - bang mo ta", ("test_sql_catalog_integration.py::test_run_duration_and_throughput_dem_slot_va_gio_hoan_thanh",)),
    R("7.3", "Ngày có duration/error/block/sold-out bất thường.",
      ("metrics.daily_operational_anomaly_flags", "run_day_status_counts_raw", "run_day_error_code_counts_raw"),
      T("run_day_operational_flags", "run_day_status_counts_raw", "run_day_error_code_counts_raw"), "source x vn_crawl_date", "RAW",
      "n_items RAW trong (source, ngay); z-score tren phan phoi cua nguon",
      ("test_metrics.py::test_daily_operational_anomaly_flags_bat_ngay_duration_va_error_bat_thuong_khong_loc_ngay_nao",
       "test_metrics.py::test_daily_operational_anomaly_flags_std_0_hoac_it_hon_3_ngay_khong_flag_am_tham",
       "test_sql_catalog_integration.py::test_run_day_status_va_error_code_counts_raw")),
    R("7.3", "para: Không dùng thời lượng run để suy ra chất lượng giá; đây là quality/capacity metric.", ("report.build_report_sections",), _REPORT, "n/a", "RAW", "n/a",
      (_LIFECYCLE,)),

    # ================================================================== 7.4 Hotel va check-in coverage
    R("7.4", "Active hotel theo crawl date, city và source.",
      ("metrics.active_hotels_from_effective_items", "active_hotel_by_crawl_date_source", "active_hotel_by_crawl_date_source_city"),
      T("active_hotel_by_crawl_date_source", "active_hotel_by_crawl_date_source_city") + F("active_hotel_by_date"), "crawl day x city", "MAIN", "khong co - bang mo ta",
      ("test_metrics.py::test_effective_hotel_identity_drive_active_availability_va_collision",
       "test_wave_a_dry_run.py::test_effective_identity_lan_sang_bang_publish_availability_active_hotel", _VALUES)),
    R("7.4", "Số check-in date được theo dõi/ngày.", ("checkin_dates_tracked_by_crawl_date_source",), T("checkin_dates_tracked_by_crawl_date_source"), "crawl day", "MAIN",
      "khong co - bang mo ta", ("test_sql_catalog_integration.py::test_active_hotel_checkin_tracked_va_heatmap",)),
    R("7.4", "Check-in month, weekday/weekend, lead-time bucket coverage.",
      ("metrics.item_coverage_count", "item_checkin_month_distribution_main", "item_checkin_weekday_distribution_main",
       "item_lead_time_bucket_distribution_main", "checkin_month_distribution_main", "checkin_weekday_distribution_main",
       "lead_time_bucket_distribution_main"),
      T("item_checkin_month_distribution_main", "item_checkin_weekday_distribution_main", "item_lead_time_bucket_distribution_main",
        "checkin_month_distribution_main", "checkin_weekday_distribution_main", "lead_time_bucket_distribution_main")
      + F("checkin_coverage_weekday_month"), "item (primary) / observation (appendix)", "MAIN",
      "owned MAIN item; observation MAIN co gia chi la phu luc option-weighted",
      ("test_metrics.py::test_item_coverage_count_khong_bi_room_option_weighting", _VALUES)),
    R("7.4", "Cohort attrition theo version; Mac Valley được ghi nhận đúng trước khi rời cohort.", ("metrics.cohort_attrition_table",), T("cohort_attrition_by_version"),
      "cohort version", "CONFIG", "khong co - bang mo ta", ("test_metrics.py::test_cohort_attrition_table_danh_dau_dung_attrition",)),
    R("7.4", "Heatmap `crawl_date × checkin_date` hoặc `crawl_date × lead_time bucket`.", ("crawl_date_lead_time_bucket_heatmap",),
      T("crawl_date_lead_time_bucket_heatmap") + F("crawl_date_lead_time_heatmap"), "crawl day x lead_time bucket", "MAIN", "khong co - bang mo ta",
      ("test_sql_catalog_integration.py::test_active_hotel_checkin_tracked_va_heatmap", "test_plots.py::test_heatmap_giu_thu_tu_bucket_chuan")),
    R("7.4", "para: Định nghĩa “active hotel” phải được ghi cạnh bảng.", ("report.build_report_sections", "active_hotel_by_crawl_date_source"), _REPORT + _DIC,
      "n/a", "MAIN", "n/a", (_LIFECYCLE,)),

    # ================================================================== 7.5 Protocol continuity va room/rate turnover
    R("7.5", "1. **Item/protocol continuity:** lịch expected lấy từ ownership schedule và owned item terminal.",
      ("protocol_continuity_actual", "protocol_schedule.expected_schedule", "protocol_schedule.classify_outcomes", "protocol_continuity_summary",
       "protocol_continuity_exceptions", "protocol_continuity_unattributed_errors"),
      T("protocol_continuity_summary", "protocol_continuity_exceptions", "protocol_continuity_unattributed_errors"), "expected slot", "PROTOCOL", "n_scheduled",
      ("test_protocol_schedule.py::test_classify_outcomes_phan_biet_missing_source_run_va_missing_item_in_existing_run",
       "test_protocol_schedule.py::test_expected_schedule_khong_phu_thuoc_actual_run_ngay_hoan_toan_khong_co_run_van_co_mat",
       "test_protocol_schedule.py::test_resolve_effective_hotel_id_resolve_duoc_tu_source_hotel_link", _VALUES)),
    R("7.5", "2. **Canonical-series presence/turnover:** tại grain", ("canonical_series_facts_main", "metrics.turnover_joint", "metrics.turnover_sample_rows",
                                                                    "metrics.turnover_by_observed_days", "metrics.max_gap_distribution",
                                                                    "metrics.median_gap_distribution"),
      T("canonical_series_turnover_by_series_main", "canonical_series_turnover_joint_main", "canonical_series_turnover_by_observed_days",
        "canonical_series_max_gap_distribution", "canonical_series_median_gap_distribution", "canonical_series_turnover_sample_main")
      + F("series_turnover_distributions"), "canonical series", "MAIN", "toan bo canonical series MAIN co gia; sample chi audit",
      ("test_sql_aggregates.py::test_series_turnover_fixture_gpt_d0_d1_d5", "test_sql_aggregates.py::test_series_turnover_joint_khop_sample_va_tong_series",
       "test_metrics.py::test_turnover_by_observed_days_dem_reappearance_dung", "test_metrics.py::test_median_gap_distribution_dung_toan_bo_population")),
    R("7.5", "3. **Parser completeness:** chỉ kết luận parser missing khi item success/available nhưng payload vi",
      ("missingness_available_observations", "missingness_by_item_status_sold_out"), T("missingness_available_observations", "missingness_by_item_status_sold_out"),
      "field_value_cell", "MAIN", "n_total field_value_cell",
      ("test_sql_catalog_integration.py::test_missingness_by_item_status_sold_out_tach_structural_khoi_unexpected",)),

    # ================================================================== 7.6 Lead time va calendar coverage
    R("7.6", "Lead-time distribution toàn bộ và theo city/source.", ("item_lead_time_bucket_distribution_main",
                                                                     "item_lead_time_bucket_distribution_by_city_source_main",
                                                                     "lead_time_bucket_distribution_main",
                                                                     "lead_time_bucket_distribution_by_city_source_main"),
      T("item_lead_time_bucket_distribution_main", "item_lead_time_bucket_distribution_by_city_source_main",
        "lead_time_bucket_distribution_main", "lead_time_bucket_distribution_by_city_source_main") + F("lead_time_bucket_distribution"),
      "item (primary) / observation (appendix)", "MAIN", "owned MAIN item; observation chi la phu luc option-weighted",
      ("test_metrics.py::test_item_coverage_count_khong_bi_room_option_weighting", _VALUES)),
    R("7.6", "Bucket tối thiểu: `0`, `1–3`, `4–7`, `8–14`, `15–30`, `31–60`, `61+`.", ("metrics.lead_time_bucket_sql_case", "metrics.lead_time_bucket"),
      T("item_lead_time_bucket_distribution_main"), "item", "MAIN", "owned MAIN item",
      ("test_metrics.py::test_lead_time_bucket_ranh_gioi", "test_metrics.py::test_lead_time_bucket_sql_case_khop_dung_python_tren_moi_gia_tri_bien")),
    R("7.6", "Weekday/weekend của check-in.", ("item_checkin_weekday_distribution_main", "checkin_weekday_distribution_main"),
      T("item_checkin_weekday_distribution_main", "checkin_weekday_distribution_main") + F("checkin_coverage_weekday_month"),
      "item (primary) / observation (appendix)", "MAIN", "owned MAIN item",
      ("test_metrics.py::test_item_coverage_count_khong_bi_room_option_weighting",)),
    R("7.6", "Check-in month.", ("item_checkin_month_distribution_main", "checkin_month_distribution_main"),
      T("item_checkin_month_distribution_main", "checkin_month_distribution_main"), "item (primary) / observation (appendix)", "MAIN", "owned MAIN item",
      ("test_metrics.py::test_item_coverage_count_khong_bi_room_option_weighting",)),
    R("7.6", "Holiday/Tết/festival/major-event coverage.", ("item_calendar_coverage_main", "observation_counts_by_calendar_flags_main",
                                                               "price_distribution_by_calendar_flags_main"),
      T("item_calendar_coverage_main", "observation_counts_by_calendar_flags_main", "price_distribution_by_calendar_flags_main"),
      "item (primary) / observation (appendix) -> co calendar", "MAIN", "owned MAIN item; observation chi la phu luc",
      ("test_wave_a_dry_run.py::test_item_grain_coverage_la_primary_va_calendar_khong_option_weighted", _LIFECYCLE)),
    R("7.6", "National event áp dụng mọi city; city event chỉ áp dụng đúng city.", ("holidays.calendar_flags_by_date_city",),
      T("holiday_calendar_flags_by_checkin_date_city"), "(checkin_date, city)", "CONFIG", "khong co",
      ("test_holidays.py::test_city_event_khong_lan_sang_thanh_pho_khac", "test_holidays.py::test_2_national_2_city_cung_ngay_khong_nhan_dong")),
    *[R("7.6", f"`{flag}`{';' if flag != 'provisional_event_count' else '.'}", ("holidays.checkin_calendar_flags",), T("holiday_calendar_flags_by_checkin_date_city"),
        "(checkin_date, city)", "CONFIG", "khong co", tests)
      for flag, tests in (
          ("is_public_holiday", ("test_holidays.py::test_2_national_2_city_cung_ngay_khong_nhan_dong",)),
          ("is_tet", ("test_holidays.py::test_ngay_khong_co_event_van_co_dong_gia_tri_false_0",)),
          ("is_festival_period", ("test_holidays.py::test_city_event_khong_lan_sang_thanh_pho_khac",)),
          ("is_major_event", ("test_holidays.py::test_city_event_khong_lan_sang_thanh_pho_khac",)),
          ("holiday_event_count", ("test_holidays.py::test_2_national_2_city_cung_ngay_khong_nhan_dong",)),
          ("confirmed_event_count", ("test_holidays.py::test_provisional_va_confirmed_dem_rieng",)),
          ("provisional_event_count", ("test_holidays.py::test_provisional_va_confirmed_dem_rieng",)),
      )],
    R("7.6", "para: Notebook 01 đọc trực tiếp `data/vn_holidays.csv` và pin SHA-256.", ("holidays.load_holiday_csv",), _MANIFEST + T("vn_holidays_events_audit"), "file", "CONFIG",
      "n/a", ("test_holidays.py::test_load_that_csv_du_an_204_dong_sach", _LIFECYCLE)),
    R("7.6", "para: Holiday loader phải validate đúng 9 cột và duplicate key `(holiday_date, event_code)`.", ("holidays.load_holiday_csv",), T("vn_holidays_events_audit"),
      "event", "CONFIG", "n/a", ("test_holidays.py::test_load_sai_cot_thi_fail", "test_holidays.py::test_load_trung_khoa_thi_fail")),
    R("7.6", "para: Calendar feature chính của mục này dùng `holiday_date = checkin_date`.", ("holidays.checkin_calendar_flags", "holidays.observation_day_calendar_flags"),
      T("holiday_calendar_flags_by_checkin_date_city"), "(checkin_date, city)", "CONFIG", "n/a", ("test_holidays.py::test_checkin_va_observation_day_dung_cot_khac_nhau",)),

    # ================================================================== 7.7 Price distribution
    R("7.7", "count, min, P1, P5, median, P75, P95, P99, max;", ("price_distribution_overall_main", "price_distribution_by_city_main"),
      T("price_distribution_overall_main", "price_distribution_by_city_main"), "observation", "MAIN", "observation co gia hop le, khong sold-out",
      ("test_sql_aggregates.py::test_price_distribution_overall_khop_numpy_tren_24_observation", "test_sql_aggregates.py::test_price_distribution_by_city")),
    R("7.7", "histogram thang thường và log;", ("price_histogram_linear_main", "price_histogram_log10_main"),
      T("price_histogram_linear_main", "price_histogram_log10_main") + F("price_histogram_main"), "observation -> bin", "MAIN", "observation MAIN co gia",
      ("test_sql_aggregates.py::test_histograms_tong_bang_tong_observation", "test_plots.py::test_histogram_ve_dung_so_bin_tu_bin_count_sql")),
    R("7.7", "box/violin plot theo city;", ("price_box_stats_by_city_main",), T("price_box_stats_by_city_main") + F("price_box_by_city"), "observation -> city", "MAIN",
      "observation MAIN co gia", ("test_sql_aggregates.py::test_price_box_stats_by_city_co_p25", "test_plots.py::test_box_plot_dung_ban_quantile_p25_p75_khong_ve_outlier")),
    R("7.7", "price theo lead-time bucket;", ("price_distribution_by_lead_time_bucket_main",), T("price_distribution_by_lead_time_bucket_main") + F("price_by_lead_time_bucket"),
      "observation -> bucket", "MAIN", "observation MAIN co gia", ("test_sql_aggregates.py::test_price_distribution_by_lead_time_bucket",)),
    R("7.7", "weekday/weekend/holiday/Tết/festival;", ("price_distribution_by_weekday_main", "price_distribution_by_calendar_flags_main"),
      T("price_distribution_by_weekday_main", "price_distribution_by_calendar_flags_main"), "observation -> thu / co calendar", "MAIN", "observation MAIN co gia",
      ("test_sql_aggregates.py::test_price_distribution_by_weekday_dung_thu_va_co_weekend_fri_sat", "test_sql_aggregates.py::test_price_distribution_by_calendar_flags_join_inline")),
    R("7.7", "hotel-level dispersion cho các hotel đủ số observation;", ("price_hotel_dispersion_main",), T("price_hotel_dispersion_main"), "hotel", "MAIN",
      "observation cua hotel >= 5 observation", ("test_sql_aggregates.py::test_hotel_dispersion_chi_hotel_du_5_observation_va_khop_numpy",)),
    R("7.7", "`price_total` và `price_per_night` consistency cho stay một đêm.", ("quality_price_total_per_night_inconsistent",), _QF, "observation", "RAW",
      "observation co gia (khong sold-out)", ("test_sql_catalog_integration.py::test_quality_price_va_checkout_va_total_per_night_bat_duoc_vi_pham",)),
    R("7.7", "para: Phân tích phải tách ít nhất RAW và MAIN; kết luận chính dùng MAIN.", ("price_distribution_overall_raw", "price_distribution_overall_main"),
      T("price_distribution_overall_raw", "price_distribution_overall_main"), "observation", "RAW+MAIN", "observation co gia hop le",
      ("test_sql_aggregates.py::test_price_distribution_raw_bang_main_khi_moi_item_deu_la_owner",)),
    R("7.7", "para: Bổ sung sensitivity table tại grain `(hotel_id, checkin_date, vn_observation_date)`.", ("price_sensitivity_by_series_main", "price_sensitivity_summary_main"),
      T("price_sensitivity_by_series_main", "price_sensitivity_summary_main"), "(hotel_id, checkin_date, vn_observation_date)", "MAIN", "n_options trong nhom",
      ("test_sql_aggregates.py::test_price_sensitivity_by_series_min_va_median_moi_hotel_checkin_ngay",)),

    # ================================================================== 7.8 Availability state
    R("7.8", "Primary rates dùng owned/MAIN terminal **items**, mỗi item đúng một lần, với status `success`,",
      ("metrics.item_status_counts_from_rows", "metrics.item_status_rates"), T("item_availability_overall"), "item", "MAIN",
      "owned MAIN terminal items voi effective_hotel_id",
      ("test_sql_catalog_integration.py::test_item_status_counts_overall_dem_dung_va_khong_bo_status_nao", "test_metrics.py::test_item_status_rates_dem_dung_va_denominator_la_item",
       "test_metrics.py::test_finalize_item_status_counts_lech_tong_status_thi_fail_khong_am_tham_bo_qua")),
    R("7.8", "Sold-out rate theo city/hotel/check-in/lead time.", ("metrics.item_status_counts_from_rows", "metrics.item_status_rates"),
      T("item_availability_by_city", "item_availability_by_hotel", "item_availability_by_checkin_month", "item_availability_by_lead_time_bucket")
      + F("item_availability_by_city", "item_availability_by_lead_time"), "item", "MAIN", "n_items trong nhom",
      ("test_metrics.py::test_effective_hotel_identity_drive_active_availability_va_collision",
       "test_wave_a_dry_run.py::test_effective_identity_lan_sang_bang_publish_availability_active_hotel",
       "test_metrics.py::test_item_status_rates_dem_dung_va_denominator_la_item")),
    R("7.8", "`not_bookable` rate theo crawl date và hotel.", ("metrics.item_status_counts_from_rows",), T("item_availability_by_crawl_date_hotel"), "item", "MAIN",
      "n_items cua (crawl_date, effective hotel)",
      ("test_metrics.py::test_effective_hotel_identity_drive_active_availability_va_collision",
       "test_wave_a_dry_run.py::test_effective_identity_lan_sang_bang_publish_availability_active_hotel")),
    R("7.8", "`error`/`partial` tách riêng.", ("metrics.item_status_counts_from_rows", "metrics.status_present_report"), T("item_availability_overall"), "item", "MAIN", "n_items MAIN",
      ("test_metrics.py::test_status_present_report_bao_0_cho_status_vang_mat", "test_metrics.py::test_effective_hotel_identity_drive_active_availability_va_collision")),
    R("7.8", "Timeline booking status của hotel, nhưng ghi rõ `hotels.booking_status` là snapshot cuối; lịch sử",
      ("metrics.item_status_counts_from_rows", "run_day_error_code_counts_raw"), T("item_availability_by_crawl_date_hotel", "run_day_error_code_counts_raw") + _REPORT,
      "item -> (crawl_date, hotel)", "MAIN", "n_items cua (crawl_date, hotel)",
      ("test_metrics.py::test_effective_hotel_identity_drive_active_availability_va_collision", _LIFECYCLE)),
    R("7.8", "Không coi sold-out observation là missing price do parser.", ("missingness_by_item_status_sold_out",), T("missingness_by_item_status_sold_out"),
      "field_value_cell", "MAIN", "observation trong (item_status, is_sold_out)",
      ("test_sql_catalog_integration.py::test_missingness_by_item_status_sold_out_tach_structural_khoi_unexpected",)),
    R("7.8", "Sentinel sold-out observation chỉ dùng audit consistency (`sold_out item ↔ đúng sentinel`), không", ("quality_sold_out_sentinel_consistency",), _QF, "item", "MAIN",
      "item status=sold_out", ("test_sql_catalog_integration.py::test_quality_canonical_key_va_sentinel_bat_duoc_sentinel_sai_key",)),

    # ================================================================== 7.9 Missingness va parser completeness
    R("7.9", "room identity: `room_type_raw`, occupancy, bed, area;", ("missingness_available_observations",), T("missingness_available_observations"), "field_value_cell",
      "MAIN", "observation available", ("test_sql_catalog_integration.py::test_missingness_du_5_field_group_va_cac_field_cua_plan_7_9",)),
    R("7.9", "rate plan: breakfast, free cancellation, cancellation policy, tax inclusion;", ("missingness_available_observations",), T("missingness_available_observations"), "field_value_cell",
      "MAIN", "observation available", ("test_sql_catalog_integration.py::test_missingness_du_5_field_group_va_cac_field_cua_plan_7_9",)),
    R("7.9", "price/currency/taxes;", ("missingness_available_observations",), T("missingness_available_observations"), "field_value_cell", "MAIN", "observation available",
      ("test_sql_catalog_integration.py::test_missingness_du_5_field_group_va_cac_field_cua_plan_7_9",)),
    R("7.9", "review/hotel attributes;", ("missingness_available_observations",), T("missingness_available_observations"), "field_value_cell", "MAIN", "observation available",
      ("test_sql_catalog_integration.py::test_missingness_du_5_field_group_va_cac_field_cua_plan_7_9",)),
    R("7.9", "artifact/source metadata.", ("missingness_available_observations", "artifact_completeness_by_source_crawl_date"),
      T("missingness_available_observations", "artifact_completeness_by_source_crawl_date"), "field_value_cell / item", "MAIN",
      "observation available cho source metadata; MAIN item cho artifact path",
      ("test_sql_catalog_integration.py::test_missingness_du_5_field_group_va_cac_field_cua_plan_7_9",
       "test_sql_catalog_integration.py::test_artifact_completeness_tach_requested_va_structural_not_requested")),
    R("7.9", "toàn bộ;", ("metrics.missingness_overall",), T("missingness_overall_available_observations"), "field_value_cell", "MAIN", "observation available",
      ("test_metrics.py::test_missingness_overall_cong_qua_cac_nguon_o_grain_field_value_cell", "test_sql_catalog_integration.py::test_missingness_du_5_field_group_va_cac_field_cua_plan_7_9")),
    R("7.9", "theo source;", ("missingness_available_observations",), T("missingness_available_observations") + F("missingness_heatmap_by_source"), "field_value_cell", "MAIN",
      "observation available trong nguon", ("test_sql_catalog_integration.py::test_missingness_du_5_field_group_va_cac_field_cua_plan_7_9",)),
    R("7.9", "theo scraper/selector version;", ("missingness_by_selector_version",), T("missingness_by_selector_version"), "field_value_cell", "MAIN",
      "observation available trong selector_version", ("test_sql_catalog_integration.py::test_missingness_theo_selector_version_dung_bucket_unknown",)),
    R("7.9", "theo crawl date;", ("missingness_by_crawl_date",), T("missingness_by_crawl_date"), "field_value_cell", "MAIN", "observation available trong ngay crawl",
      ("test_sql_catalog_integration.py::test_missingness_theo_ngay_crawl_va_city_cong_lai_bang_tong",)),
    R("7.9", "theo city;", ("missingness_by_city",), T("missingness_by_city"), "field_value_cell", "MAIN", "observation available trong city",
      ("test_sql_catalog_integration.py::test_missingness_theo_ngay_crawl_va_city_cong_lai_bang_tong",)),
    R("7.9", "theo item status và sold-out.", ("missingness_by_item_status_sold_out",), T("missingness_by_item_status_sold_out"), "field_value_cell", "MAIN",
      "observation trong (item_status, is_sold_out)",
      ("test_sql_catalog_integration.py::test_missingness_by_item_status_sold_out_tach_structural_khoi_unexpected",)),

    # ================================================================== 7.10 Full-history reference audit
    R("7.10", "Approved/proposed count và approval rate theo city/check-in month.", ("reference_approval_by_city_month",), T("reference_approval_by_city_month"),
      "full-history reference series", "REFERENCE EVIDENCE", "series co candidate (n)",
      ("test_sql_aggregates.py::test_reference_approval_checkin_month_khong_con_la_hang_so_percent",
       "test_sql_catalog_integration.py::test_reference_series_a_du_3_run_thi_approved_va_item_level_availability")),
    R("7.10", "Candidate coverage, distinct run/item count và uniqueness.",
      ("reference_status_evidence_summary", "reference_uniqueness_per_series", "reference_candidate_coverage_summary"),
      T("reference_status_evidence_summary", "reference_uniqueness_per_series", "reference_candidate_coverage_summary"), "reference / candidate / series",
      "REFERENCE EVIDENCE", "n_references trong status; n_candidates; series co reference",
      ("test_sql_catalog_integration.py::test_reference_series_a_du_3_run_thi_approved_va_item_level_availability",
       "test_sql_catalog_integration.py::test_reference_candidate_coverage_summary_dem_candidate_va_series")),
    R("7.10", "**Exact approved-key observation coverage:** numerator là non-sold-out observation có canonical",
      ("reference_observation_match_main", "reference_observation_match_raw", "metrics.exact_approved_key_observation_coverage"),
      T("reference_exact_key_coverage_main", "reference_exact_key_coverage_raw") + F("reference_coverage_by_lead_time"), "observation -> lead-time bucket", "MAIN (chinh) / RAW (phu luc)",
      "observation khong sold-out trong scope", ("test_sql_catalog_integration.py::test_reference_observation_coverage_exact_key_va_series_exists_legacy",
                                                 "test_metrics.py::test_exact_approved_key_observation_coverage_tinh_ty_le_tu_bang_da_aggregate")),
    R("7.10", "**Item-level exact-reference availability:** numerator là success MAIN item có ít nhất một option",
      ("reference_item_level_availability_main", "metrics.item_level_exact_reference_availability"), T("reference_item_level_availability_by_lead_time"),
      "item -> lead-time bucket", "MAIN", "success MAIN item thuoc series co reference approved",
      ("test_sql_catalog_integration.py::test_reference_series_a_du_3_run_thi_approved_va_item_level_availability",
       "test_metrics.py::test_item_level_exact_reference_availability_tinh_ty_le_tu_bang_da_aggregate")),
    R("7.10", "Notebook 01 không dùng từ `unavailable`, `alias` hoặc `ambiguous` cho phần còn lại; ba status này chỉ",
      ("reference_status_evidence_summary",), T("reference_status_evidence_summary", "reference_exact_key_coverage_main"), "n/a", "REFERENCE EVIDENCE", "n/a",
      ("test_wave_a_dry_run.py::test_bang_reference_khong_dung_status_unavailable_alias_ambiguous",)),
    R("7.10", "Tái hiện và kiểm tra bảng bắt buộc đã ghi trong `CLAUDE.md`:",
      ("reference_series_exists_observation_coverage_raw_legacy_bucket", "metrics.series_with_approved_reference_coverage"),
      T("reference_series_exists_coverage_raw_legacy_bucket", "reference_series_exists_coverage_raw") + _REPORT, "observation -> bucket legacy 0-3", "RAW",
      "observation RAW khong sold-out trong bucket", ("test_report.py::test_report_7_10_so_sanh_bang_lich_su_va_danh_dau_lech_qua_0_5pp",
                                                      "test_sql_catalog_integration.py::test_reference_observation_coverage_exact_key_va_series_exists_legacy")),
    R("7.10", "para: Phải giải thích full-history turnover không phải causal train coverage.", ("report.build_report_sections",), _REPORT, "n/a", "n/a", "n/a",
      ("test_report.py::test_report_7_10_so_sanh_bang_lich_su_va_danh_dau_lech_qua_0_5pp",)),

    # ================================================================== 7.11 Data quality findings
    R("7.11", "price không dương trên available observation;", ("quality_price_non_positive",), _QF, "observation", "RAW", "observation co gia (khong sold-out)",
      ("test_sql_catalog_integration.py::test_quality_price_va_checkout_va_total_per_night_bat_duoc_vi_pham",)),
    R("7.11", "`checkout_date <= checkin_date`;", ("quality_checkout_not_after_checkin",), _QF, "observation", "RAW", "tat ca observation RAW",
      ("test_sql_catalog_integration.py::test_quality_price_va_checkout_va_total_per_night_bat_duoc_vi_pham",)),
    R("7.11", "lead time lưu sẵn khác lead time tính lại từ ngày Việt Nam;", ("quality_lead_time_mismatch",), _QF, "observation", "RAW", "tat ca observation RAW",
      ("test_sql_catalog_integration.py::test_quality_lead_time_va_parent_mismatch_bat_duoc_vi_pham",)),
    R("7.11", "duplicate daily series;", ("quality_duplicate_daily_series",), _QF, "item x canonical key", "RAW", "nhom (item, canonical key) khong sold-out",
      ("test_sql_catalog_integration.py::test_quality_duplicate_daily_series_bat_duoc_2_option_trung_canonical_key",)),
    R("7.11", "canonical/raw key anomalies;", ("quality_canonical_key_anomalies",), _QF, "observation", "RAW", "tat ca observation RAW",
      ("test_sql_catalog_integration.py::test_quality_canonical_key_va_sentinel_bat_duoc_sentinel_sai_key",)),
    R("7.11", "city ngoài scope;", ("quality_city_outside_scope",), _QF, "hotel", "RAW", "hotel co city",
      ("test_sql_catalog_integration.py::test_quality_success_item_khong_observation_va_city_ngoai_scope",)),
    R("7.11", "observation có hotel/check-in khác item cha;", ("quality_parent_mismatch",), _QF, "observation", "RAW", "tat ca observation RAW",
      ("test_sql_catalog_integration.py::test_quality_lead_time_va_parent_mismatch_bat_duoc_vi_pham",)),
    R("7.11", "success item không observation;", ("quality_success_item_without_observation",), _QF, "item", "MAIN", "success MAIN item",
      ("test_sql_catalog_integration.py::test_quality_success_item_khong_observation_va_city_ngoai_scope",)),
    R("7.11", "unexpected NULL theo field group;", ("quality_unexpected_nulls_by_field_group",), _QF + T("missingness_by_item_status_sold_out"), "field_value_cell", "MAIN",
      "n_total_field_value_cells", (_VALUES,)),
    R("7.11", "outlier price theo robust within-hotel rule, chỉ flag chứ không xóa;", ("price_outlier_summary_by_hotel_main", "price_outlier_sample_main"),
      T("price_outlier_summary_by_hotel_main", "price_outlier_sample_main") + _QF, "observation", "MAIN", "observation cua hotel >= 5 observation",
      ("test_sql_aggregates.py::test_price_outlier_dung_1_observation_va_khop_mad_numpy", _VALUES)),
    R("7.11", "collision/source divergence đáng chú ý.", ("collision_item_pairs", "collision_option_detail"), _QF + T("collision_item_summary", "collision_option_summary"),
      "item pair / shared canonical option pair", "RAW", "collision item-pairs / shared canonical option-pairs", (_COLLISION,)),
    R("7.11", "para: Mỗi finding gồm severity, scope, count, denominator, sample keys, likely cause và recommended action.", ("wave_a.build_quality_findings",), _QF, "check", "RAW+MAIN",
      "denominator rieng cua tung check", ("test_wave_a_dry_run.py::test_quality_findings_du_cot_bat_buoc_va_du_12_check",)),

    # ================================================================== 7.12 Readiness cho dataset/model
    R("7.12", "độ dài lịch sử theo hotel/check-in;", ("history_length_by_hotel_checkin_main",), T("history_length_by_hotel_checkin_main") + F("history_length_distribution"),
      "series (hotel_id, checkin_date)", "MAIN", "series co item success MAIN",
      ("test_sql_catalog_integration.py::test_history_length_by_hotel_checkin_dem_ngay_co_snapshot_success",)),
    R("7.12", "số observation dates khả dụng cho horizon 1/3/7/14 ở mức lý thuyết;", ("canonical_series_facts_main", "metrics.readiness_by_horizon"),
      T("dataset_readiness_by_horizon") + F("readiness_by_horizon"), "horizon_days", "MAIN", "n_series canonical series MAIN",
      ("test_sql_aggregates.py::test_dataset_readiness_dem_dung_cap_cach_k_ngay_khong_dung_n_observed_days",)),
    R("7.12", "lead-time và city coverage;", ("item_lead_time_bucket_distribution_by_city_source_main", "metrics.item_status_counts_from_rows"),
      T("item_lead_time_bucket_distribution_by_city_source_main", "item_availability_by_lead_time_bucket"), "item", "MAIN",
      "owned MAIN item trong bucket/city/source",
      ("test_wave_a_dry_run.py::test_item_grain_coverage_la_primary_va_calendar_khong_option_weighted",
       "test_metrics.py::test_effective_hotel_identity_drive_active_availability_va_collision")),
    R("7.12", "tỷ lệ series có ít nhất 3 evidence runs;", ("series_evidence_runs_distribution", "metrics.evidence_runs_share"),
      T("series_evidence_runs_distribution", "series_evidence_runs_share"), "series (hotel_id, checkin_date)", "REFERENCE EVIDENCE", "series co >= 1 evidence run",
      ("test_sql_catalog_integration.py::test_series_evidence_runs_distribution_va_share",
       "test_metrics.py::test_evidence_runs_share_ty_le_series_co_it_nhat_3_run_theo_city_va_tong")),
    R("7.12", "cảnh báo rằng label thực tế chỉ được tính sau causal freeze + item matching.", ("canonical_series_facts_main", "metrics.readiness_by_horizon"),
      T("dataset_readiness_by_horizon") + _REPORT, "horizon_days", "MAIN", "n_series canonical series MAIN",
      ("test_sql_aggregates.py::test_dataset_readiness_dem_dung_cap_cach_k_ngay_khong_dung_n_observed_days",)),
    R("7.12", "`theoretical_date_pairs`;", ("canonical_series_facts_main", "metrics.readiness_by_horizon"), T("dataset_readiness_by_horizon"), "horizon_days", "MAIN", "n_series",
      ("test_sql_aggregates.py::test_dataset_readiness_dem_dung_cap_cach_k_ngay_khong_dung_n_observed_days",)),
    R("7.12", "`actual_causal_labels` = NULL/not_available cho tới Wave B.", ("canonical_series_facts_main", "metrics.readiness_by_horizon"), T("dataset_readiness_by_horizon"), "horizon_days", "MAIN", "n_series",
      ("test_sql_aggregates.py::test_dataset_readiness_dem_dung_cap_cach_k_ngay_khong_dung_n_observed_days",)),
]


def _assign_bullet_ids(rows: list[dict]) -> list[dict]:
    counters: dict[str, int] = {}
    out = []
    for row in rows:
        counters[row["plan_section"]] = counters.get(row["plan_section"], 0) + 1
        out.append({"bullet_id": f"{row['plan_section']}.{counters[row['plan_section']]}", **row})
    return out


def coverage_matrix_rows() -> list[dict]:
    """Cac dong voi gia tri da la tuple (metric_ids/artifacts/test_ids) - dung cho test enforcement."""
    return _assign_bullet_ids(_ROWS)


def coverage_matrix_dataframe() -> pd.DataFrame:
    """Dang artifact (CSV/report): cac cot da la tuple duoc noi bang '; '."""
    rows = []
    for row in coverage_matrix_rows():
        rows.append({column: ("; ".join(row[column]) if column in _JOINED else row[column]) for column in COLUMNS})
    return pd.DataFrame(rows, columns=list(COLUMNS))


def missing_required_rows(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Cac dong CHUA `status == 'implemented'` HOAC thieu mapping/artifact/test (chuoi rong) - dung cho test enforcement va cho
    `report.write_report()` quyet dinh co duoc goi ket qua la 'full Wave A' hay khong (file 11 muc 6.5)."""
    frame = df if df is not None else coverage_matrix_dataframe()
    incomplete = (frame["status"] != "implemented") | (frame[list(_JOINED)].apply(lambda col: col.astype(str).str.strip() == "").any(axis=1))
    return frame[incomplete]


def write_coverage_matrix_md(df: pd.DataFrame, path: str | Path) -> None:
    missing = missing_required_rows(df)
    lines = [
        "# EDA Coverage Matrix - plan 7.1-7.12\n",
        "Moi dong = 1 bullet (hoac yeu cau dang van xuoi `para:`) cua `EDA_CURATED_PLAN.md` muc 7.1-7.12. `plan_bullet` la dong dau cua bullet trong plan (test parse "
        "chinh plan de bat bullet thieu/thua); `metric_ids` la catalog metric / ten bang publish / ham that; `artifacts` la bang/hinh/file publish (co that trong "
        "artifact manifest); `test_ids` (`file::test`) tro toi ham test that. `src/tests/test_coverage_matrix.py` fail neu bat ky rang buoc nao vo.\n",
        f"**Trang thai:** {len(df) - len(missing)}/{len(df)} dong `implemented`" + (f", **{len(missing)} CON THIEU/CHUA DU MAPPING**." if len(missing) else " - DAY DU (full Wave A).") + "\n",
        "| " + " | ".join(COLUMNS) + " |", "| " + " | ".join("---" for _ in COLUMNS) + " |",
    ]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(str(v).replace("\n", " ").replace("|", "/") for v in row) + " |")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
