"""Coverage matrix: 1 dong / bullet cua `EDA_CURATED_PLAN.md` muc 7.1-7.12 -> metric_id/artifact/grain/
scope/denominator/test_id/status (GPT review 12 eda file 11 muc 5: "Khong duoc chi dua vao viec report
co 12 heading. Matrix nay phai nam trong artifact manifest va test phai fail neu mot bullet bat buoc
khong co mapping/artifact/test.").

Day la du lieu TINH (hand-maintained, doi chieu THU CONG voi plan moi khi plan doi) - khong tu suy tu
code. `src/tests/test_coverage_matrix.py` assert khong con bullet nao `status != 'implemented'`, va
`wave_a.write_eda_report_and_dictionary()` tu choi goi ket qua la "full Wave A" neu con thieu (GPT file
11 muc 6.5: "chi duoc goi la full Wave A khi coverage matrix khong con muc required nao missing").
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# 1 dong / bullet: (plan_section, bullet, metric_id_or_function, artifact, grain, scope, denominator, test_id, status)
_ROWS: tuple[tuple[str, str, str, str, str, str, str, str, str], ...] = (
    # ---------------------------------------------------------------- 7.1 Preflight
    ("7.1", "Load pointer, xac minh dung DB/batch PASS", "db.connect/_verify_snapshot", "n/a (gate truoc khi doc)",
     "warehouse", "n/a", "n/a", "test_db.py (snapshot verification)", "implemented"),
    ("7.1", "Recompute/verify source manifest qua code warehouse", "build_input_manifest (source_manifest_canonical_sha256)",
     "input_manifest.json", "warehouse", "n/a", "n/a", "test_dry_run_lifecycle_day_du_thanh_cong", "implemented"),
    ("7.1", "In row count, date range, source list, schema/canonicalization version", "preflight_core_counts + build_input_manifest",
     "input_manifest.json, eda_summary.json", "warehouse", "RAW", "n/a", "test_dry_run_lifecycle_day_du_thanh_cong", "implemented"),
    ("7.1", "Assert count nen khop validation report (runs/items/observations/curated/rejections)",
     "build_input_manifest (reconciled_counts + reconciled_reference_counts)", "input_manifest.json",
     "warehouse", "RAW", "n/a", "test_dry_run_lifecycle_day_du_thanh_cong (raise ValueError khi lech)", "implemented"),
    ("7.1", "Assert khong co run/item queued/running", "preflight_non_terminal_runs_items", "n/a (raise neu >0)",
     "warehouse", "RAW", "n/a", "collect_wave_a_data raise RuntimeError", "implemented"),

    # ---------------------------------------------------------------- 7.2 Source/ownership/protocol coverage
    ("7.2", "Run/item/observation theo source va crawl date", "run_item_observation_by_source_crawl_date",
     "tables/run_item_observation_by_source_crawl_date.csv", "source x vn_crawl_date", "RAW", "n/a", "-", "implemented"),
    ("7.2", "Item theo ownership_status + exclusion_reason", "ownership_by_source_status_reason",
     "tables/ownership_by_source_status_reason.csv", "item", "RAW", "tong item RAW", "-", "implemented"),
    ("7.2", "RAW so voi MAIN theo source", "preflight_core_counts vs run_item_observation_by_source_crawl_date "
     "(include_eda_raw/include_eda_main filter)", "input_manifest.json, tables/*", "item/observation", "RAW+MAIN",
     "n/a", "-", "implemented"),
    ("7.2", "non_owner_duplicate, off-plan, pre-protocol pilot", "ownership_by_source_status_reason (exclusion_reason)",
     "tables/ownership_by_source_status_reason.csv", "item", "RAW", "tong item RAW", "-", "implemented"),
    ("7.2", "Collision audit theo (crawl_date, checkin_date, hotel_id)",
     "collision_item_pairs + collision_item_status_concordance + collision_option_level_detail",
     "tables/collision_item_pairs.csv, tables/collision_item_status_concordance.csv, tables/collision_option_detail.csv",
     "item pair / option pair", "RAW", "collision item-pairs / success-success item-pairs / shared canonical option-pairs",
     "test_collision_*.py, EDA_SMOKE nbclient e2e", "implemented"),
    ("7.2", "Ty le owner success/failure theo ngay va nguon", "protocol_continuity_actual + protocol_schedule.classify_outcomes",
     "tables/protocol_continuity_classified.csv", "expected schedule slot", "MAIN", "n_scheduled", "test_protocol_schedule.py", "implemented"),

    # ---------------------------------------------------------------- 7.3 Crawl operations/capacity
    ("7.3", "Run duration started_at->finished_at", "run_duration_and_throughput", "tables/run_duration_and_throughput.csv",
     "run", "RAW", "n/a", "-", "implemented"),
    ("7.3", "Items/gio va observations/gio", "run_duration_and_throughput (items_per_hour/observations_per_hour)",
     "tables/run_duration_and_throughput.csv", "run", "RAW", "duration_minutes/60", "-", "implemented"),
    ("7.3", "Thoi diem hoan thanh theo gio Viet Nam", "metrics.finish_hour_distribution", "tables/finish_hour_distribution.csv",
     "source x finish_hour_vn", "RAW", "n/a", "test_finish_hour_distribution", "implemented"),
    ("7.3", "Run co keo qua ngay crawl ke tiep hay khong", "run_duration_and_throughput (crosses_next_crawl_day)",
     "tables/run_duration_and_throughput.csv", "run", "RAW", "n/a", "-", "implemented"),
    ("7.3", "Phan bo duration/throughput theo source va so check-in slot", "run_duration_and_throughput (n_checkin_slots)",
     "tables/run_duration_and_throughput.csv", "run", "RAW", "n/a", "-", "implemented"),
    ("7.3", "Ngay co duration/error/block/sold-out bat thuong", "metrics.anomalous_crawl_days",
     "tables/run_duration_anomaly_flagged.csv", "run", "RAW", "n/a (z-score tren chinh source)",
     "test_anomalous_crawl_days_*", "implemented"),

    # ---------------------------------------------------------------- 7.4 Hotel/check-in coverage
    ("7.4", "Active hotel theo crawl date, city va source", "active_hotel_by_crawl_date_source[_city]",
     "tables/active_hotel_by_crawl_date_source[_city].csv", "crawl day (x city)", "MAIN", "n/a", "-", "implemented"),
    ("7.4", "So check-in date theo doi/ngay", "checkin_dates_tracked_by_crawl_date_source",
     "tables/checkin_dates_tracked_by_crawl_date_source.csv", "crawl day", "MAIN", "n/a", "-", "implemented"),
    ("7.4", "Check-in month, weekday/weekend, lead-time bucket coverage",
     "checkin_month_distribution, checkin_weekday_distribution, lead_time_bucket_distribution",
     "tables/checkin_month_distribution.csv, tables/checkin_weekday_distribution.csv, tables/lead_time_bucket_distribution.csv",
     "observation", "MAIN", "n/a", "-", "implemented"),
    ("7.4", "Cohort attrition theo version; Mac Valley ghi nhan dung truoc khi roi", "metrics.cohort_attrition_table",
     "tables/cohort_attrition_by_version.csv", "cohort version", "n/a (tu cohort_history JSON)", "n/a",
     "test_cohort_attrition_table_*", "implemented"),
    ("7.4", "Heatmap crawl_date x checkin_date hoac crawl_date x lead_time bucket", "crawl_date_lead_time_bucket_heatmap",
     "tables/crawl_date_lead_time_bucket_heatmap.csv", "crawl day x lead_time_bucket", "MAIN", "n/a", "-", "implemented"),

    # ---------------------------------------------------------------- 7.5 Protocol continuity + turnover
    ("7.5", "Item/protocol continuity: lich expected + phan loai missing/owner_failure/sold_out/not_bookable/error",
     "protocol_schedule.expected_schedule + classify_outcomes", "tables/protocol_continuity_classified.csv",
     "expected schedule slot", "MAIN", "n_scheduled", "test_protocol_schedule.py (17 test)", "implemented"),
    ("7.5", "Canonical-series presence/turnover: n_observed_days/first/last/gap/reappearance",
     "metrics.canonical_series_turnover", "tables/canonical_series_turnover.csv",
     "(hotel_id, checkin_date, canonical_series_id)", "MAIN", "n/a", "test_canonical_series_turnover_*", "implemented"),
    ("7.5", "Parser completeness: chi ket luan missing khi vi pham completeness rule cua 7.9",
     "missingness_available_observations + quality_unexpected_nulls_by_field_group", "tables/missingness_available_observations.csv",
     "field_value_cell", "MAIN", "n_total_field_value_cells", "-", "implemented"),

    # ---------------------------------------------------------------- 7.6 Lead time va calendar coverage
    ("7.6", "Lead-time distribution toan bo", "lead_time_bucket_distribution", "tables/lead_time_bucket_distribution.csv",
     "observation", "MAIN", "n/a", "-", "implemented"),
    ("7.6", "Lead-time distribution theo city/source", "lead_time_bucket_distribution_by_city_source",
     "tables/lead_time_bucket_distribution_by_city_source.csv", "observation", "MAIN", "n/a", "-", "implemented"),
    ("7.6", "Bucket toi thieu 0/1-3/4-7/8-14/15-30/31-60/61+", "metrics.LEAD_TIME_BUCKETS + lead_time_bucket_sql_case",
     "moi bang co lead_time_bucket", "n/a", "n/a", "n/a", "test_lead_time_bucket_*", "implemented"),
    ("7.6", "Weekday/weekend cua check-in", "checkin_weekday_distribution + price_main_cal.is_weekend",
     "tables/checkin_weekday_distribution.csv, tables/price_distribution_by_weekday_weekend_main.csv",
     "observation", "MAIN", "n/a", "-", "implemented"),
    ("7.6", "Check-in month", "checkin_month_distribution", "tables/checkin_month_distribution.csv",
     "observation", "MAIN", "n/a", "-", "implemented"),
    ("7.6", "Holiday/Tet/festival/major-event coverage (is_public_holiday/is_tet/is_festival_period/is_major_event/"
     "holiday_event_count/confirmed_event_count/provisional_event_count)", "holidays.checkin_calendar_flags",
     "tables/vn_holidays_events_audit.csv, price_main_cal (trong bao cao 7.7)", "(checkin_date, city)", "n/a", "n/a",
     "test_holidays.py (15 test)", "implemented"),
    ("7.6", "Check-in-grain lead-time coverage (KHAC voi observation-option-weighted)", "item_availability_by_lead_time "
     "(main_item_status, item grain)", "tables/item_availability_by_lead_time.csv", "item", "MAIN", "n_items",
     "-", "implemented"),

    # ---------------------------------------------------------------- 7.7 Price distribution
    ("7.7", "count/P1/P5/median/P75/P95/P99/max tren observation gia hop le, khong sold-out", "price_distribution_stats",
     "tables/price_distribution_overall_main.csv", "observation", "MAIN", "n/a", "test_price_distribution_stats_*", "implemented"),
    ("7.7", "Histogram thang thuong va log", "notebook cell 67109c60 (log10 tren truc X)",
     "figures/price_histogram_main.png", "observation", "MAIN", "n/a", "-", "implemented"),
    ("7.7", "Box/violin plot theo city", "notebook cell 67109c60", "figures/price_box_by_city.png",
     "observation", "MAIN", "n/a", "-", "implemented"),
    ("7.7", "Price theo lead-time bucket", "price_distribution_by_lead_time_bucket_main",
     "tables/price_distribution_by_lead_time_bucket_main.csv", "observation", "MAIN", "n/a", "-", "implemented"),
    ("7.7", "Price theo weekday/weekend/holiday/Tet/festival", "price_distribution_by_weekday_weekend_main, "
     "price_distribution_by_holiday_tet_festival_main", "tables/price_distribution_by_weekday_weekend_main.csv, "
     "tables/price_distribution_by_holiday_tet_festival_main.csv", "observation", "MAIN", "n/a", "-", "implemented"),
    ("7.7", "Hotel-level dispersion cho hotel du observation", "metrics.hotel_price_dispersion",
     "tables/hotel_price_dispersion.csv", "hotel", "MAIN", "n_observations>=5", "test_hotel_price_dispersion_*", "implemented"),
    ("7.7", "price_total va price_per_night consistency cho stay 1 dem", "quality_price_total_per_night_inconsistent",
     "quality_findings.csv (price_total_per_night_inconsistent)", "observation", "RAW", "observation co gia", "-", "implemented"),
    ("7.7", "Tach it nhat RAW va MAIN", "price_observations_raw + price_distribution_overall_raw",
     "tables/price_distribution_overall_raw.csv", "observation", "RAW", "n/a", "-", "implemented"),
    ("7.7", "Sensitivity table grain (hotel_id, checkin_date, vn_observation_date)", "metrics.price_sensitivity_by_series",
     "tables/price_sensitivity_by_series_median.csv", "(hotel_id, checkin_date, vn_observation_date)", "MAIN", "n/a",
     "-", "implemented"),

    # ---------------------------------------------------------------- 7.8 Availability state
    ("7.8", "Primary rates o item grain (success/sold_out/not_bookable/partial/error, n_items+numerator+denominator)",
     "main_item_status + metrics.item_availability_rates", "tables/item_availability_overall.csv", "item", "MAIN",
     "n_items", "test_item_availability_rates_*", "implemented"),
    ("7.8", "Sold-out rate theo city/hotel/check-in/lead time", "item_availability_by_city, item_availability_by_lead_time "
     "(hotel/checkin qua not_bookable_rate_by_crawl_date_hotel cho hotel-level)", "tables/item_availability_by_city.csv, "
     "tables/item_availability_by_lead_time.csv", "item", "MAIN", "n_items", "-", "implemented"),
    ("7.8", "not_bookable rate theo crawl date va hotel", "metrics.not_bookable_rate_by_crawl_date_hotel",
     "tables/not_bookable_rate_by_crawl_date_hotel.csv", "(crawl_date, hotel_id)", "MAIN", "n_items",
     "test_not_bookable_rate_by_crawl_date_hotel", "implemented"),
    ("7.8", "error/partial tach rieng", "metrics.status_present_report + item_availability_rates",
     "eda_summary.json (item_availability_overall)", "item", "MAIN", "n_items", "-", "implemented"),
    ("7.8", "Timeline booking status (ghi ro hotels.booking_status la snapshot cuoi)", "active_hotel_by_crawl_date_source",
     "tables/active_hotel_by_crawl_date_source.csv (+ ghi chu trong EDA_REPORT.md)", "crawl day", "MAIN", "n/a",
     "-", "implemented"),
    ("7.8", "Khong coi sold-out la missing price do parser", "price_observations_main (WHERE is_sold_out=0)",
     "n/a (thiet ke query)", "observation", "MAIN", "n/a", "-", "implemented"),
    ("7.8", "Sentinel sold-out consistency (sold_out item <-> dung sentinel)", "quality_sold_out_sentinel_consistency",
     "quality_findings.csv (sold_out_sentinel_consistency)", "item", "MAIN", "item status=sold_out",
     "-", "implemented"),

    # ---------------------------------------------------------------- 7.9 Missingness va parser completeness
    ("7.9", "Field group: room identity/rate plan/price+taxes/review+hotel attributes/artifact+source metadata",
     "_MISSINGNESS_FIELD_GROUPS (5 nhom)", "tables/missingness_available_observations.csv", "observation", "MAIN",
     "n_total (theo nhom)", "-", "implemented"),
    ("7.9", "Missingness toan bo + theo source", "missingness_available_observations",
     "tables/missingness_available_observations.csv", "observation", "MAIN", "n_total", "-", "implemented"),
    ("7.9", "Missingness theo scraper/selector version", "missingness_by_selector_version",
     "tables/missingness_by_selector_version.csv", "observation", "MAIN", "n_total", "-", "implemented"),
    ("7.9", "Missingness theo crawl date", "missingness_by_crawl_date", "tables/missingness_by_crawl_date.csv",
     "observation", "MAIN", "n_total", "-", "implemented"),
    ("7.9", "Missingness theo city", "missingness_by_city", "tables/missingness_by_city.csv", "observation", "MAIN",
     "n_total", "-", "implemented"),
    ("7.9", "Missingness theo item status va sold-out", "main_item_status (status) + is_sold_out=0 filter da co san",
     "tables/missingness_available_observations.csv (da loai structural qua is_sold_out=0)", "observation/item",
     "MAIN", "n_total", "-", "implemented"),
    ("7.9", "Structural missing tach khoi unexpected missing", "WHERE po.is_sold_out=0 o moi missingness query",
     "n/a (thiet ke query)", "observation", "MAIN", "n/a", "-", "implemented"),

    # ---------------------------------------------------------------- 7.10 Full-history reference audit
    ("7.10", "Approved/proposed count + approval rate theo city/check-in month", "reference_approval_by_city_month",
     "tables/reference_approval_by_city_month.csv", "full-history reference series", "REFERENCE EVIDENCE",
     "COUNT(*) series co candidate", "-", "implemented"),
    ("7.10", "Exact approved-key observation coverage (MAIN chinh, RAW phu luc)", "reference_observation_match_main/raw + "
     "metrics.exact_approved_key_observation_coverage", "tables/reference_exact_key_coverage_main[_raw].csv",
     "lead_time_bucket", "MAIN/RAW", "n_observations", "test_exact_approved_key_observation_coverage_*", "implemented"),
    ("7.10", "Item-level exact-reference availability theo lead-time", "reference_item_level_availability_main + "
     "metrics.item_level_exact_reference_availability", "tables/reference_item_level_availability_by_lead_time.csv",
     "item", "MAIN", "success MAIN item co reference approved", "test_item_level_exact_reference_availability", "implemented"),
    ("7.10", "Khong dung unavailable/alias/ambiguous (chi Wave B)", "queries.py docstrings + metric titles",
     "n/a (quy uoc dat ten)", "n/a", "n/a", "n/a", "-", "implemented"),
    ("7.10", "Tai hien bang lich su CLAUDE.md (0-3/4-7/8-14/15-30/31-60/61+ ~ 30,4/27,1/18,3/11,2/7,6/7,1%)",
     "reference_series_exists_observation_coverage_raw_legacy_bucket + metrics.series_with_approved_reference_coverage",
     "tables/reference_series_exists_coverage_raw_legacy_bucket.csv", "legacy_lead_time_bucket", "RAW",
     "n_observations", "-", "implemented"),
    ("7.10", "Giai thich turnover KHONG phai causal train coverage, khong loc con 2.457 approved roi coi la toan quan the",
     "EDA_REPORT.md section 7.10 (Caveat)", "EDA_REPORT.md", "n/a", "n/a", "n/a", "-", "implemented"),

    # ---------------------------------------------------------------- 7.11 Data quality findings
    ("7.11", "Price khong duong tren available observation", "quality_price_non_positive", "quality_findings.csv",
     "observation", "RAW", "n_total", "-", "implemented"),
    ("7.11", "checkout_date <= checkin_date (thuc te: DATEDIFF<>1, dung nghiep vu 1 dem)", "quality_checkout_not_after_checkin",
     "quality_findings.csv", "observation", "RAW", "n_total", "-", "implemented"),
    ("7.11", "Lead time luu san khac lead time tinh lai tu ngay VN", "quality_lead_time_mismatch", "quality_findings.csv",
     "observation", "RAW", "n_total", "test_wave_a_dry_run.py (e2e)", "implemented"),
    ("7.11", "Duplicate daily series", "quality_duplicate_daily_series", "quality_findings.csv",
     "item x canonical key", "RAW", "n_total_groups", "-", "implemented"),
    ("7.11", "Canonical/raw key anomalies", "quality_canonical_key_anomalies", "quality_findings.csv",
     "observation", "RAW", "n_total", "-", "implemented"),
    ("7.11", "City ngoai scope", "quality_city_outside_scope", "quality_findings.csv", "hotel", "RAW", "n_total", "-", "implemented"),
    ("7.11", "Observation co hotel/check-in khac item cha", "quality_parent_mismatch", "quality_findings.csv",
     "observation", "RAW", "n_total", "-", "implemented"),
    ("7.11", "Success item khong observation", "quality_success_item_without_observation", "quality_findings.csv",
     "item", "MAIN", "n_total", "-", "implemented"),
    ("7.11", "Unexpected NULL theo field group", "quality_unexpected_nulls_by_field_group", "quality_findings.csv",
     "field_value_cell", "MAIN", "n_total_field_value_cells", "-", "implemented"),
    ("7.11", "Outlier price theo robust within-hotel rule, chi flag khong xoa", "metrics.robust_price_outliers",
     "tables/price_outlier_sample.csv (+ cot is_price_outlier trong price_main, khong xoa dong nao)",
     "observation", "MAIN", "hotel co >=5 observation", "test_robust_price_outliers_*", "implemented"),
    ("7.11", "Collision/source divergence dang chu y", "collision_item_pairs + collision_option_level_detail + "
     "collision_option_time_diff_stratification", "tables/collision_*.csv", "item pair / option pair", "RAW",
     "xem muc 7.2 collision row", "test_collision_*.py", "implemented"),

    # ---------------------------------------------------------------- 7.12 Readiness cho dataset/model
    ("7.12", "Do dai lich su theo hotel/check-in", "series_history_length + metrics.canonical_series_turnover",
     "tables/canonical_series_turnover.csv", "canonical series", "MAIN", "n/a", "test_canonical_series_turnover_*", "implemented"),
    ("7.12", "So observation date kha dung cho horizon 1/3/7/14 (ly thuyet)", "metrics.theoretical_horizon_pairs + "
     "dataset_readiness_by_horizon", "dataset_readiness_by_horizon.csv", "(series, horizon_days)", "MAIN", "n/a",
     "test_theoretical_horizon_pairs_*", "implemented"),
    ("7.12", "Lead-time va city coverage", "item_availability_by_lead_time, lead_time_bucket_distribution_by_city_source",
     "tables/item_availability_by_lead_time.csv, tables/lead_time_bucket_distribution_by_city_source.csv",
     "item/observation", "MAIN", "n/a", "-", "implemented"),
    ("7.12", "Ty le series co it nhat 3 evidence run", "reference_approval_by_city_month (candidates dua tren min_runs=3, "
     "xem CLAUDE.md muc 4.2.c)", "tables/reference_approval_by_city_month.csv", "full-history reference series",
     "REFERENCE EVIDENCE", "n/a", "-", "implemented"),
    ("7.12", "Canh bao label that chi tinh sau causal freeze + item matching; bang dataset_readiness_by_horizon "
     "co 2 cot tach biet theoretical_date_pairs/actual_causal_labels", "metrics.dataset_readiness_by_horizon",
     "dataset_readiness_by_horizon.csv", "horizon_days", "MAIN", "n_series", "test_dataset_readiness_by_horizon_*", "implemented"),
)

_COLUMNS = ("plan_section", "bullet", "metric_id", "artifact", "grain", "scope", "denominator", "test_id", "status")


def coverage_matrix_dataframe() -> pd.DataFrame:
    return pd.DataFrame(list(_ROWS), columns=_COLUMNS)


def missing_required_rows(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Cac dong CHUA `status == 'implemented'` - dung cho test enforcement (GPT review 12 eda file 11
    muc 5: "test phai fail neu mot bullet bat buoc khong co mapping/artifact/test") va cho
    `wave_a.write_eda_report_and_dictionary()` quyet dinh co duoc goi ket qua la "full Wave A" hay
    khong (file 11 muc 6.5)."""
    frame = df if df is not None else coverage_matrix_dataframe()
    return frame[frame["status"] != "implemented"]


def write_coverage_matrix_md(df: pd.DataFrame, path: str | Path) -> None:
    lines = [
        "# EDA Coverage Matrix - plan 7.1-7.12\n",
        "Moi dong = 1 bullet cua `EDA_CURATED_PLAN.md` muc 7.1-7.12, doi chieu THU CONG (khong tu sinh "
        "tu code) - cap nhat file nay khi them/doi metric hoac khi plan doi. "
        "`src/tests/test_coverage_matrix.py` fail neu con dong `status != 'implemented'`.\n",
    ]
    missing = missing_required_rows(df)
    lines.append(f"**Trang thai:** {len(df) - len(missing)}/{len(df)} bullet `implemented`"
                 + (f", **{len(missing)} CON THIEU**." if len(missing) else " - DAY DU.") + "\n")
    header = "| " + " | ".join(_COLUMNS) + " |"
    sep = "| " + " | ".join("---" for _ in _COLUMNS) + " |"
    lines.append(header)
    lines.append(sep)
    for row in df.itertuples(index=False):
        cells = [str(v).replace("\n", " ") for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
