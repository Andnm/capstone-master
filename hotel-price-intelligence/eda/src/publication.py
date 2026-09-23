"""Registry DUY NHAT cho moi artifact Wave A duoc publish (bang CSV + hinh PNG): metric ID on dinh, scope, grain, denominator
va muc plan 7.x (GPT review 12 eda file 11 muc 5-6: "metric ID on dinh; table/figure output; grain; scope; denominator").

Day la DU LIEU KHAI BAO, khong tinh toan. `wave_a.compute_wave_a_tables()` phai tra ve DUNG tap khoa cua `PUBLISHED_TABLES`
(test E2E kiem tra), notebook chi luu hinh qua `wave_a.save_figure()` voi ten thuoc `PUBLISHED_FIGURES`, va
`coverage_matrix.py` chi duoc tham chieu artifact/metric ton tai o day. Nho vay 'metadata' (denominator, scope...) khong the lech khoi
code that.

`metric_id`: ten metric trong `queries.CATALOG` neu bang la ket qua SQL nguyen ban; nguoc lai `derived:<modul.ham>` (tinh tu bang da co,
cung mot noi duy nhat trong `wave_a`).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableSpec:
    name: str
    metric_id: str
    section: str        # muc plan: "7.1".."7.12" hoac "wave_b"
    scope: str          # RAW | MAIN | RAW+MAIN | REFERENCE EVIDENCE | PROTOCOL | CONFIG | ML CURATED
    grain: str
    denominator: str    # dinh nghia mau so cua moi ty le trong bang (hoac "khong co - bang mo ta")
    root_level: bool = False  # ghi o goc analysis_dir thay vi tables/


@dataclass(frozen=True)
class FigureSpec:
    name: str
    section: str
    tables: tuple[str, ...]   # bang nguon (ten trong PUBLISHED_TABLES)
    description: str


def _t(name, metric_id, section, scope, grain, denominator, *, root=False) -> tuple[str, TableSpec]:
    return name, TableSpec(name, metric_id, section, scope, grain, denominator, root)


def _c(name, section, scope, grain, denominator, *, root=False) -> tuple[str, TableSpec]:
    """Bang SQL nguyen ban: metric_id = ten bang (dung ten catalog)."""
    return _t(name, name, section, scope, grain, denominator, root=root)


_NA = "khong co - bang mo ta (khong phai ty le)"

PUBLISHED_TABLES: dict[str, TableSpec] = dict([
    # ------------------------------------------------------------------ 7.1 preflight / snapshot identity
    _c("preflight_core_counts", "7.1", "RAW", "warehouse (1 dong)", _NA),
    _t("preflight_reconciliation", "derived:wave_a.build_input_manifest", "7.1", "RAW", "count nen (hotels/runs/items/observations/curated/rejections/reference)",
       "khong co - so sanh tuyet doi eda_value vs validation_report_value"),
    _c("preflight_import_sources", "7.1", "RAW", "source", _NA),
    _c("preflight_rejections", "7.1", "RAW", "warehouse (1 dong)", _NA),
    _c("observation_date_ranges_main", "7.1", "MAIN", "warehouse (1 dong)", _NA),
    # ------------------------------------------------------------------ 7.2 source / ownership / protocol coverage + collision
    _c("ownership_by_source_status_reason", "7.2", "RAW", "item", "tong item RAW cua batch (khong co cot ty le)"),
    _c("run_item_observation_by_source_crawl_date", "7.2", "RAW", "source x vn_crawl_date", _NA),
    _c("raw_vs_main_by_source", "7.2", "RAW+MAIN", "source", _NA),
    _t("protocol_outcome_rates_by_source_date", "derived:metrics.protocol_outcome_rates_by_source_date", "7.2", "PROTOCOL",
       "source x vn_crawl_date", "n_scheduled = so slot expected cua nguon trong ngay (ownership manifest x cohort hieu luc)"),
    _t("collision_item_pairs", "derived:metrics.collision_pairs_from_effective_items", "7.2", "RAW",
       "item pair (source_a, source_b, item_id_a, item_id_b)",
       "collision item-pairs (2 nguon cung vn_crawl_date, effective_hotel_id, checkin_date)"),
    _t("collision_item_status_concordance", "derived:metrics.collision_item_status_concordance", "7.2", "RAW", "item pair (status_a, status_b)",
       "collision item-pairs (n_pairs cong lai = n_collision_item_pairs)"),
    _t("collision_item_summary", "derived:metrics.collision_item_summary", "7.2", "RAW", "collision item pairs (1 dong)",
       "collision item-pairs; n_success_success_pairs la mau so RIENG cua option-level"),
    _t("collision_item_time_diff_stratification", "derived:metrics.collision_item_time_diff_stratification", "7.2", "RAW",
       "item pair x bucket phut (chenh finished_at)", "collision item-pairs"),
    _t("collision_option_detail", "derived:queries.collision_option_analysis", "7.2", "RAW", "shared canonical option-pair (1-1 o ca 2 nguon)",
       "shared canonical option-pairs (khong duplicate key)"),
    _t("collision_option_pair_coverage", "derived:queries.collision_option_analysis", "7.2", "RAW", "success-success item pair",
       "success-success item-pairs"),
    _t("collision_option_summary", "derived:metrics.collision_option_summary", "7.2", "RAW", "shared canonical option pairs (1 dong)",
       "shared canonical option-pairs"),
    _t("collision_option_coverage_summary", "derived:metrics.collision_option_coverage_summary", "7.2", "RAW", "success-success item pairs (1 dong)",
       "success-success item-pairs"),
    _t("collision_option_time_diff_stratification", "derived:metrics.collision_option_time_diff_stratification", "7.2", "RAW",
       "shared canonical option-pair x bucket phut (chenh observed_at)", "shared canonical option-pairs"),
    # ------------------------------------------------------------------ 7.3 crawl operations / capacity
    _c("run_duration_and_throughput", "7.3", "RAW", "run", _NA),
    _t("finish_hour_distribution", "derived:metrics.finish_hour_distribution", "7.3", "RAW", "source x gio VN hoan thanh", _NA),
    _c("run_day_status_counts_raw", "7.3", "RAW", "item -> source x vn_crawl_date", "n_items = item RAW trong (source, ngay)"),
    _c("run_day_error_code_counts_raw", "7.3", "RAW", "item -> source x ngay x status x last_error_code", _NA),
    _t("run_day_operational_flags", "derived:metrics.daily_operational_anomaly_flags", "7.3", "RAW", "source x vn_crawl_date",
       "n_items = item RAW trong (source, ngay); z-score tren phan phoi cua chinh nguon"),
    # ------------------------------------------------------------------ 7.4 hotel / check-in coverage
    _t("active_hotel_by_crawl_date_source", "derived:metrics.active_hotels_from_effective_items", "7.4", "MAIN",
       "source x crawl day", "owned terminal items co effective_hotel_id"),
    _t("active_hotel_by_crawl_date_source_city", "derived:metrics.active_hotels_from_effective_items", "7.4", "MAIN",
       "source x crawl day x effective city", "owned terminal items co effective_hotel_id"),
    _c("checkin_dates_tracked_by_crawl_date_source", "7.4", "MAIN", "crawl day", _NA),
    _t("item_checkin_month_distribution_main", "derived:metrics.item_coverage_count", "7.4", "MAIN",
       "owned item -> thang check-in", "owned terminal MAIN items; moi item dung 1 lan"),
    _t("item_checkin_weekday_distribution_main", "derived:metrics.item_coverage_count", "7.4", "MAIN",
       "owned item -> thu check-in", "owned terminal MAIN items; moi item dung 1 lan"),
    _c("checkin_month_distribution_main", "7.4", "MAIN", "observation -> thang check-in (phu luc)", "observation MAIN co gia"),
    _c("checkin_weekday_distribution_main", "7.4", "MAIN", "observation -> thu check-in (phu luc)", "observation MAIN co gia"),
    _c("lead_time_bucket_distribution_main", "7.4", "MAIN", "observation -> lead-time bucket (phu luc)", "observation MAIN co gia"),
    _c("crawl_date_lead_time_bucket_heatmap", "7.4", "MAIN", "item -> crawl day x lead_time bucket", _NA),
    _t("cohort_attrition_by_version", "derived:metrics.cohort_attrition_table", "7.4", "CONFIG", "cohort version", _NA),
    # ------------------------------------------------------------------ 7.5 protocol continuity + turnover + parser completeness
    _t("protocol_continuity_summary", "derived:metrics.protocol_continuity", "7.5", "PROTOCOL", "owner_source (outcome theo item)",
       "n_scheduled = slot expected cua nguon"),
    _t("protocol_continuity_exceptions", "derived:protocol_schedule.classify_outcomes", "7.5", "PROTOCOL", "expected slot (chi outcome != owner_success)",
       "khong co - liet ke ngoai le; mau so nam o protocol_continuity_summary.n_scheduled"),
    _t("protocol_continuity_unattributed_errors", "derived:protocol_schedule.summarize_unattributed", "7.5", "MAIN", "source x crawl_date",
       "item error owned voi hotel_id=NULL khong resolve duoc tu source_hotel_link"),
    _t("canonical_series_turnover_by_series_main", "derived:queries.canonical_series_facts_main", "7.5", "MAIN",
       "canonical series", "canonical series MAIN co it nhat 1 observation co gia"),
    _t("canonical_series_turnover_joint_main", "derived:metrics.turnover_joint", "7.5", "MAIN", "canonical series -> (n_observed_days, max_gap_days)",
       "canonical series MAIN co it nhat 1 observation co gia"),
    _t("canonical_series_turnover_by_observed_days", "derived:metrics.turnover_by_observed_days", "7.5", "MAIN", "canonical series -> n_observed_days",
       "canonical series MAIN trong nhom n_observed_days"),
    _t("canonical_series_max_gap_distribution", "derived:metrics.max_gap_distribution", "7.5", "MAIN", "canonical series -> max_gap_days",
       "tong canonical series MAIN"),
    _t("canonical_series_median_gap_distribution", "derived:metrics.median_gap_distribution", "7.5", "MAIN",
       "canonical series -> median_gap_days", "tong canonical series MAIN"),
    _t("canonical_series_turnover_sample_main", "derived:metrics.turnover_sample_rows", "7.5", "MAIN",
       "canonical series (top-gap sample co gioi han; chi audit)", _NA),
    # ------------------------------------------------------------------ 7.6 lead time + calendar
    _t("item_lead_time_bucket_distribution_main", "derived:metrics.item_coverage_count", "7.6", "MAIN",
       "owned item -> lead-time bucket", "owned terminal MAIN items; moi item dung 1 lan"),
    _t("item_lead_time_bucket_distribution_by_city_source_main", "derived:metrics.item_coverage_count", "7.6", "MAIN",
       "owned item -> effective city x source x bucket", "owned terminal MAIN items trong nhom"),
    _t("item_calendar_coverage_main", "derived:wave_a._item_grain_coverage_tables x holidays.checkin_calendar_flags", "7.6", "MAIN",
       "owned item -> calendar-flag group", "owned terminal MAIN items; kem so ngay va (ngay,city) cell distinct"),
    _c("lead_time_bucket_distribution_by_city_source_main", "7.6", "MAIN", "observation -> city x source x bucket (phu luc)", "observation MAIN co gia"),
    _t("holiday_calendar_flags_by_checkin_date_city", "derived:holidays.checkin_calendar_flags", "7.6", "CONFIG",
       "(checkin_date, city) - 1 dong duy nhat sau aggregate truoc join", _NA),
    _t("observation_counts_by_calendar_flags_main", "derived:observation_counts_by_checkin_date_city_main x holidays.checkin_calendar_flags", "7.6",
       "MAIN", "observation -> nhom co calendar cua ngay check-in", "observation MAIN co gia"),
    _t("vn_holidays_events_audit", "derived:holidays.load_holiday_csv", "7.6", "CONFIG", "event (holiday_date, event_code)", _NA),
    # ------------------------------------------------------------------ 7.7 price distribution
    _c("price_distribution_overall_main", "7.7", "MAIN", "observation", "observation co gia hop le, khong sold-out (n_obs)"),
    _c("price_distribution_overall_raw", "7.7", "RAW", "observation", "observation co gia hop le, khong sold-out (n_obs)"),
    _c("price_distribution_by_city_main", "7.7", "MAIN", "observation -> city", "n_obs trong city"),
    _c("price_distribution_by_lead_time_bucket_main", "7.7", "MAIN", "observation -> lead-time bucket", "n_obs trong bucket"),
    _c("price_distribution_by_weekday_main", "7.7", "MAIN", "observation -> thu check-in", "n_obs trong thu"),
    _c("price_distribution_by_calendar_flags_main", "7.7", "MAIN", "observation -> co holiday/tet/festival/major_event", "n_obs trong nhom co"),
    _c("price_box_stats_by_city_main", "7.7", "MAIN", "observation -> city", "n_obs trong city"),
    _c("price_hotel_dispersion_main", "7.7", "MAIN", "hotel (>= 5 observation)", "observation cua hotel co >= 5 observation gia"),
    _c("price_sensitivity_by_series_main", "7.7", "MAIN", "(hotel_id, checkin_date, vn_observation_date)", "n_options trong nhom"),
    _t("price_sensitivity_summary_main", "derived:price_sensitivity_by_series_main", "7.7", "MAIN",
       "(hotel_id, checkin_date, vn_observation_date) -> min/median hop le", "so (hotel, check-in, ngay quan sat) co it nhat 1 option"),
    _c("price_histogram_linear_main", "7.7", "MAIN", "observation -> bin gia (thang thuong)", "observation MAIN co gia"),
    _c("price_histogram_log10_main", "7.7", "MAIN", "observation -> bin log10(gia)", "observation MAIN co gia"),
    _c("price_outlier_summary_by_hotel_main", "7.7", "MAIN", "hotel (>= 5 observation)", "observation cua hotel co >= 5 observation gia"),
    _c("price_outlier_sample_main", "7.7", "MAIN", "observation (sample audit co gioi han)", _NA),
    # ------------------------------------------------------------------ 7.8 availability (item grain)
    # 7.8: 6 bang nay tinh tu item frame MAIN da resolve EFFECTIVE hotel/city (`wave_a._effective_availability_tables` -> `metrics.item_status_counts_from_rows`
    # + `metrics.item_status_rates`), KHONG tu cac SQL `item_status_counts_*_main` (hotel_id tho, giu lam diagnostic doi soat - xem INTERMEDIATE_METRICS).
    _t("item_availability_overall", "derived:wave_a._effective_availability_tables + metrics.item_status_rates", "7.8", "MAIN", "item",
       "n_items = item MAIN terminal"),
    _t("item_availability_by_city", "derived:wave_a._effective_availability_tables + metrics.item_status_rates", "7.8", "MAIN", "item -> effective city",
       "n_items trong city"),
    _t("item_availability_by_hotel", "derived:wave_a._effective_availability_tables + metrics.item_status_rates", "7.8", "MAIN", "item -> effective hotel",
       "n_items cua hotel"),
    _t("item_availability_by_checkin_month", "derived:wave_a._effective_availability_tables + metrics.item_status_rates", "7.8", "MAIN",
       "item -> thang check-in", "n_items trong thang"),
    _t("item_availability_by_lead_time_bucket", "derived:wave_a._effective_availability_tables + metrics.item_status_rates", "7.8", "MAIN",
       "item -> lead-time bucket (checkin_date - ngay crawl VN)", "n_items trong bucket"),
    _t("item_availability_by_crawl_date_hotel", "derived:wave_a._effective_availability_tables + metrics.item_status_rates", "7.8", "MAIN",
       "item -> (ngay crawl VN, effective hotel) - gom not_bookable rate theo crawl date va hotel", "n_items cua (crawl_date, hotel)"),
    # ------------------------------------------------------------------ 7.9 missingness
    _t("missingness_overall_available_observations", "derived:metrics.missingness_overall", "7.9", "MAIN", "field_value_cell -> field (moi nguon)",
       "n_total = observation available (khong sold-out) cua moi nguon cong lai"),
    _c("missingness_available_observations", "7.9", "MAIN", "field_value_cell -> source x field", "n_total = observation available (khong sold-out) trong nhom"),
    _c("missingness_by_selector_version", "7.9", "MAIN", "field_value_cell -> selector_version x field", "n_total = observation available trong nhom"),
    _c("missingness_by_crawl_date", "7.9", "MAIN", "field_value_cell -> vn_crawl_date x field", "n_total = observation available trong nhom"),
    _c("missingness_by_city", "7.9", "MAIN", "field_value_cell -> city x field", "n_total = observation available trong nhom"),
    _c("missingness_by_item_status_sold_out", "7.9", "MAIN", "field_value_cell -> (item_status, is_sold_out) x field",
       "n_total = observation trong (item_status, is_sold_out)"),
    _c("artifact_completeness_by_source_crawl_date", "7.9", "MAIN",
       "item -> source x vn_crawl_date x save_artifacts", "n_items MAIN trong nhom; structural-not-requested tach unexpected missing"),
    # ------------------------------------------------------------------ 7.10 full-history reference audit
    _c("reference_approval_by_city_month", "7.10", "REFERENCE EVIDENCE", "full-history reference series (hotel_id, checkin_date) -> city x thang",
       "series co candidate (n)"),
    _c("reference_status_evidence_summary", "7.10", "REFERENCE EVIDENCE", "reference row -> status", "n_references trong status"),
    _c("reference_uniqueness_per_series", "7.10", "REFERENCE EVIDENCE", "series -> so reference approved", "series co it nhat 1 reference row"),
    _c("reference_candidate_coverage_summary", "7.10", "REFERENCE EVIDENCE", "candidate (1 dong)", "n_candidates"),
    _t("reference_exact_key_coverage_main", "derived:reference_observation_match_main + metrics.exact_approved_key_observation_coverage", "7.10",
       "MAIN", "observation -> lead-time bucket", "n_observations = observation MAIN khong sold-out trong bucket"),
    _t("reference_exact_key_coverage_raw", "derived:reference_observation_match_raw + metrics.exact_approved_key_observation_coverage", "7.10",
       "RAW", "observation -> lead-time bucket", "n_observations = observation RAW khong sold-out trong bucket"),
    _t("reference_series_exists_coverage_raw",
       "derived:reference_series_exists_observation_coverage_raw + metrics.series_with_approved_reference_coverage", "7.10", "RAW",
       "observation -> lead-time bucket", "n_observations = observation RAW khong sold-out trong bucket"),
    _t("reference_series_exists_coverage_raw_legacy_bucket",
       "derived:reference_series_exists_observation_coverage_raw_legacy_bucket + metrics.series_with_approved_reference_coverage", "7.10", "RAW",
       "observation -> legacy bucket 0-3/4-7/...", "n_observations = observation RAW khong sold-out trong bucket legacy"),
    _t("reference_item_level_availability_by_lead_time",
       "derived:reference_item_level_availability_main + metrics.item_level_exact_reference_availability", "7.10", "MAIN",
       "item -> lead-time bucket", "n_items = success MAIN item thuoc (hotel, check-in) co reference approved"),
    # ------------------------------------------------------------------ 7.11 data quality
    _t("quality_findings", "derived:wave_a.build_quality_findings", "7.11", "RAW+MAIN", "1 dong / check", "denominator rieng cua tung check (cot denominator)",
       root=True),
    # ------------------------------------------------------------------ 7.12 readiness
    _t("dataset_readiness_by_horizon", "derived:metrics.readiness_by_horizon", "7.12", "MAIN", "horizon_days", "n_series = canonical series MAIN",
       root=True),
    _c("history_length_by_hotel_checkin_main", "7.12", "MAIN", "series (hotel_id, checkin_date) -> city x bucket so ngay co snapshot",
       "series co it nhat 1 item success MAIN"),
    _c("series_evidence_runs_distribution", "7.12", "REFERENCE EVIDENCE", "series (hotel_id, checkin_date) -> city x so evidence run", "series co >= 1 evidence run"),
    _t("series_evidence_runs_share", "derived:metrics.evidence_runs_share", "7.12", "REFERENCE EVIDENCE", "series -> city (+ tong)",
       "n_series co >= 1 evidence run trong nhom"),
    # ------------------------------------------------------------------ Wave B guard
    _c("wave_b_dataset_version_readiness", "wave_b", "ML CURATED", "dataset_version", _NA),
])

# `protocol_continuity_actual` la dau vao trung gian (item-grain, KHONG publish); cac metric duoc CHAY nhung khong publish nguyen ban:
INTERMEDIATE_METRICS: frozenset[str] = frozenset({
    "preflight_non_terminal_runs_items", "protocol_continuity_actual", "item_identity_actual_raw",
    # Raw-hotel-id SQL variants are retained only as diagnostic baselines. Published item metrics
    # use the cohort-aware effective identity resolved from source_hotel_link.
    "active_hotel_by_crawl_date_source", "active_hotel_by_crawl_date_source_city", "collision_item_pairs",
    # quality_* scalar: phuc vu quality_findings (root), khong publish rieng
    "quality_price_non_positive", "quality_checkout_not_after_checkin", "quality_success_item_without_observation",
    "quality_canonical_key_anomalies", "quality_city_outside_scope", "quality_unexpected_nulls_by_field_group",
    "quality_lead_time_mismatch", "quality_duplicate_daily_series", "quality_parent_mismatch",
    "quality_sold_out_sentinel_consistency", "quality_price_total_per_night_inconsistent",
    # SQL item-grain theo `crawl_run_items.hotel_id` THO: chi la diagnostic doi soat (moi lan chay `wave_a.compute_wave_a_tables` assert
    # tong overall khop item frame effective); KHONG phai nguon cua 6 bang `item_availability_*` (nguon la item frame effective, xem tren).
    "item_status_counts_overall_main", "item_status_counts_by_city_main", "item_status_counts_by_hotel_main",
    "item_status_counts_by_checkin_month_main", "item_status_counts_by_lead_time_bucket_main",
    "item_status_counts_by_crawl_date_hotel_main", "canonical_series_facts_main", "reference_observation_match_main", "reference_observation_match_raw",
    "reference_series_exists_observation_coverage_raw", "reference_series_exists_observation_coverage_raw_legacy_bucket",
    "reference_item_level_availability_main", "observation_counts_by_checkin_date_city_main",
})

PUBLISHED_FIGURES: dict[str, FigureSpec] = {spec.name: spec for spec in (
    FigureSpec("run_item_by_source_crawl_date", "7.2", ("run_item_observation_by_source_crawl_date",), "Item RAW theo ngay crawl va nguon"),
    FigureSpec("raw_vs_main_by_source", "7.2", ("raw_vs_main_by_source",), "So item RAW vs MAIN theo nguon"),
    FigureSpec("owner_outcome_rate_by_source_date", "7.2", ("protocol_outcome_rates_by_source_date",), "Ty le owner success/failure/missing theo ngay va nguon"),
    FigureSpec("run_duration_by_source", "7.3", ("run_duration_and_throughput",), "Phan bo thoi luong run theo nguon"),
    FigureSpec("finish_hour_distribution", "7.3", ("finish_hour_distribution",), "Gio VN hoan thanh run theo nguon"),
    FigureSpec("active_hotel_by_date", "7.4", ("active_hotel_by_crawl_date_source",), "Hotel active theo ngay crawl va nguon"),
    FigureSpec("crawl_date_lead_time_heatmap", "7.4", ("crawl_date_lead_time_bucket_heatmap",), "Heatmap ngay crawl x lead-time bucket (item)"),
    FigureSpec("checkin_coverage_weekday_month", "7.4", ("item_checkin_weekday_distribution_main", "item_checkin_month_distribution_main"),
               "Phan bo owned item theo thu va thang check-in"),
    FigureSpec("lead_time_bucket_distribution", "7.6", ("item_lead_time_bucket_distribution_main",), "Phan bo owned item theo lead-time bucket"),
    FigureSpec("price_histogram_main", "7.7", ("price_histogram_linear_main", "price_histogram_log10_main"), "Histogram gia (thuong + log10), MAIN"),
    FigureSpec("price_box_by_city", "7.7", ("price_box_stats_by_city_main",), "Box plot gia theo city (P5/P25/P50/P75/P95), MAIN"),
    FigureSpec("price_by_lead_time_bucket", "7.7", ("price_distribution_by_lead_time_bucket_main",), "Gia theo lead-time bucket (P5-P95, median)"),
    FigureSpec("item_availability_by_city", "7.8", ("item_availability_by_city",), "Ty le status item theo city (stacked)"),
    FigureSpec("item_availability_by_lead_time", "7.8", ("item_availability_by_lead_time_bucket",), "Ty le status item theo lead-time bucket (stacked)"),
    FigureSpec("missingness_heatmap_by_source", "7.9", ("missingness_available_observations",), "Ty le NULL theo field x nguon (available observation)"),
    FigureSpec("reference_coverage_by_lead_time", "7.10", ("reference_exact_key_coverage_main", "reference_series_exists_coverage_raw_legacy_bucket"),
               "Exact approved-key (MAIN) vs series-exists legacy (RAW) theo lead-time"),
    FigureSpec("series_turnover_distributions", "7.5", ("canonical_series_turnover_by_observed_days", "canonical_series_max_gap_distribution",
                                                        "canonical_series_median_gap_distribution"),
               "Turnover population canonical series: so ngay observed, max gap va median gap"),
    FigureSpec("history_length_distribution", "7.12", ("history_length_by_hotel_checkin_main",), "Do dai lich su theo (hotel, check-in)"),
    FigureSpec("readiness_by_horizon", "7.12", ("dataset_readiness_by_horizon",), "So cap ngay quan sat ly thuyet theo horizon"),
)}

# Artifact khong phai bang/hinh nhung PHAI co mat trong artifact manifest (coverage matrix tham chieu).
STATIC_ARTIFACTS: frozenset[str] = frozenset({
    "input_manifest.json", "eda_summary.json", "EDA_REPORT.md", "DATA_DICTIONARY.md", "EDA_COVERAGE_MATRIX.md",
    "EDA_COVERAGE_MATRIX.csv", "TABLE_METADATA.csv", "executed_notebooks/01_warehouse_full_history_eda.ipynb",
})


def table_artifact_path(name: str) -> str:
    """Duong dan tuong doi trong analysis_dir cua bang `name` (goc hoac tables/)."""
    spec = PUBLISHED_TABLES[name]
    return f"{name}.csv" if spec.root_level else f"tables/{name}.csv"


def figure_artifact_path(name: str) -> str:
    if name not in PUBLISHED_FIGURES:
        raise KeyError(f"figure {name!r} khong co trong PUBLISHED_FIGURES")
    return f"figures/{name}.png"


def known_artifact_paths() -> set[str]:
    """Moi duong dan artifact hop le ma coverage matrix duoc phep tham chieu."""
    return (
        {table_artifact_path(n) for n in PUBLISHED_TABLES}
        | {figure_artifact_path(n) for n in PUBLISHED_FIGURES}
        | set(STATIC_ARTIFACTS)
    )
