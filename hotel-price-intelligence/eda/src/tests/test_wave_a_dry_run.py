"""Dry-run tren fixture warehouse DISPOSABLE - chung minh vong doi artifact (GPT review 12 eda M2, va yeu cau tuong minh cua GPT: "Dry-run/
fixture execution tao du bo artifact nho de GPT kiem lifecycle; khong dung warehouse full"). KHONG dung `warehouse_current.json` that o bat ky
buoc nao.

Warehouse fixture duoc dung bang CHINH `app.warehouse.batch.build_warehouse()` that (`warehouse_fixture.build_fixture_warehouse`, spec co gia tri
biet truoc o `fixture_specs.py`) - tai su dung toan bo logic import/curated/reference/validate da test rieng cua backend, khong tu hand-insert ETL row.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import pandas as pd
import pytest

import metrics

import artifacts
import null_taxonomy
import publication
import queries
import run_wave_a
import wave_a
from warehouse_fixture import mutate_row, mutated, query_one

pytestmark = pytest.mark.mysql
D = dt.date


def _collect(fx):
    return wave_a.collect_wave_a_data(
        pointer_path=fx["pointer_path"], ownership_manifest_path=fx["ownership_manifest_path"],
        cohort_history_path=fx["cohort_history_path"], cohort_history_base_dir=fx["cohort_history_path"].parent,
        vn_holidays_csv=fx["vn_holidays_csv_path"],
    )


def _manifest(data, fx, notebook_path):
    return wave_a.build_input_manifest(
        data, notebook_source_path=notebook_path, warehouse_validation_report_path=fx["warehouse_validation_report_path"],
        ownership_manifest_path=fx["ownership_manifest_path"], cohort_history_path=fx["cohort_history_path"],
        cohort_history_base_dir=fx["cohort_history_path"].parent, source_manifest_path=fx["source_manifest_path"],
    )


def test_collected_metrics_phu_toan_bo_catalog_khong_metric_nao_bi_quen(price_wh):
    """Moi catalog metric deu duoc chay trong 1 lan collect (khong entry chet trong CATALOG, khong metric publish nao bi bo sot)."""
    data = _collect(price_wh)
    assert set(data["m"]) == set(queries.CATALOG) == set(wave_a.COLLECTED_METRIC_IDS)
    assert set(data["metric_seconds"]) >= set(data["m"])
    published_catalog_ids = {s.metric_id for s in publication.PUBLISHED_TABLES.values() if not s.metric_id.startswith("derived:")}
    assert published_catalog_ids <= set(queries.CATALOG)
    # moi catalog metric hoac duoc publish nguyen ban, hoac la nguon cua 1 bang derived (co mat trong metric_id cua no), hoac la dau vao
    # trung gian khai bao ro - khong co metric "mo coi" (chay ton thoi gian ma khong ra artifact nao).
    referenced = {mid for mid in queries.CATALOG if any(mid in spec.metric_id for spec in publication.PUBLISHED_TABLES.values())}
    assert set(queries.CATALOG) <= referenced | publication.INTERMEDIATE_METRICS


def test_dry_run_lifecycle_day_du_thanh_cong(price_wh, tmp_path):
    """Toan bo pipeline (collect -> input_manifest -> tables -> summary -> report/dictionary/matrix -> artifact_manifest) tren fixture disposable."""
    fx = price_wh
    data = _collect(fx)
    assert data["snapshot"].batch_id == fx["batch_id"]
    assert int(data["core_counts"]["price_observations"].iloc[0]) == 25  # 24 co gia + 1 sentinel sold-out (item 7)
    assert "peak" not in json.dumps(list(data.keys()))  # sanity: khong luu bang observation-level nao vao data

    fake_notebook = tmp_path / "fake_notebook.ipynb"
    fake_notebook.write_text("{}", encoding="utf-8")
    input_manifest = _manifest(data, fx, fake_notebook)
    assert input_manifest["batch_id"] == fx["batch_id"]
    assert "code_provenance" in input_manifest and "library_versions" in input_manifest
    assert input_manifest["warehouse_validation_report_status"] == "pass"
    assert input_manifest["reconciled_counts"]["crawl_run_items"] == 10 and input_manifest["reconciled_counts"]["rejections"] == 0
    assert all(row["match"] for row in input_manifest["reconciliation"])
    assert {row["check"] for row in input_manifest["reconciliation"]} >= {
        "hotels", "crawl_runs", "crawl_run_items", "price_observations", "curated_observation_keys", "rejections"}
    assert len(input_manifest["cohort_workbook_versions"]) == 1
    assert input_manifest["cohort_workbook_versions"][0]["validation_status"] == "match"
    assert input_manifest["cohort_workbook_versions"][0]["workbook_file_size_bytes"] > 0
    assert input_manifest["observed_date_min"] == "2026-09-01" and input_manifest["observed_date_max"] == "2026-09-06"
    assert input_manifest["checkin_date_min"] == "2026-09-10" and input_manifest["checkin_date_max"] == "2026-09-20"
    assert [s["source_code"] for s in input_manifest["sources"]] == ["local_primary"]
    assert "protocol_complete_through_date_by_source" in input_manifest
    assert set(input_manifest["published_tables"]) == set(publication.PUBLISHED_TABLES)

    analysis_dir = artifacts.new_analysis_dir(f"eda_test_{fx['batch_id']}", outputs_dir=tmp_path / "outputs")
    artifacts.atomic_write_json(analysis_dir / "input_manifest.json", input_manifest)
    tables = wave_a.compute_wave_a_tables(data, input_manifest)
    assert set(tables) == set(publication.PUBLISHED_TABLES)  # dung tap khoa registry, khong hon khong kem
    wave_a.write_wave_a_tables(tables, analysis_dir)
    wave_a.write_eda_summary(data, analysis_dir, tables)
    wave_a.write_eda_report_and_dictionary(data, tables, analysis_dir, input_manifest=input_manifest)

    # GPT review 12 eda M4: report/dictionary KHONG con la placeholder - phai co du 12 section 7.x va nhung con so THAT.
    report_text = (analysis_dir / "EDA_REPORT.md").read_text(encoding="utf-8")
    for heading in ("## 7.1", "## 7.2", "## 7.3", "## 7.4", "## 7.5", "## 7.6", "## 7.7", "## 7.8", "## 7.9", "## 7.10", "## 7.11",
                    "## 7.12", "## Wave B", "## Coverage matrix"):
        assert heading in report_text, f"thieu section {heading} trong EDA_REPORT.md"
    assert "protocol_complete_through_date" in report_text and "Gia dinh" in report_text
    assert "FULL WAVE A" in report_text or "PARTIAL" in report_text
    # cac yeu cau dang van xuoi (coverage matrix 'para:'): dinh nghia active hotel, khong suy chat luong gia tu thoi luong run,
    # hotels.booking_status la snapshot cuoi, cohort declared vs computed hash
    assert "Active" in report_text and "KHONG dung thoi luong run" in report_text
    assert "hotels.booking_status" in report_text and "declared" in report_text and "computed" in report_text
    # GPT file 13 M5: concordance breakfast/free-cancellation/cancellation-policy la STRUCTURAL (nam trong canonical_rate_key), khong duoc doc nhu xac nhan doc lap
    assert "STRUCTURAL" in report_text and "canonical_rate_key" in report_text and "price_includes_tax" in report_text
    dictionary_text = (analysis_dir / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    for column in ("price_per_night", "lead_time_bucket", "n_shared_options", "n_items", "missing_kind", "denominator"):
        assert f"### `{column}`" in dictionary_text, f"dictionary thieu cot {column}"

    # GPT review 12 M2: manifest CHI duoc ghi SAU KHI moi thu khac da xong.
    manifest_path = artifacts.write_artifact_manifest(analysis_dir)
    file_paths = {entry["path"] for entry in json.loads(manifest_path.read_text(encoding="utf-8"))["files"]}
    for expected in ("input_manifest.json", "eda_summary.json", "EDA_REPORT.md", "DATA_DICTIONARY.md", "EDA_COVERAGE_MATRIX.md",
                     "EDA_COVERAGE_MATRIX.csv", "TABLE_METADATA.csv", "quality_findings.csv", "dataset_readiness_by_horizon.csv"):
        assert expected in file_paths, f"thieu artifact {expected} trong manifest"
    assert "artifact_manifest.json" not in file_paths  # khong tu hash chinh no
    # GPT review 12 eda M4: TOAN BO key cua compute_wave_a_tables() thuc su ra file (khong chi 1 tap con).
    for name in tables:
        assert publication.table_artifact_path(name) in file_paths, f"thieu bang {name} trong manifest"

    metadata = pd.read_csv(analysis_dir / "TABLE_METADATA.csv")
    assert set(metadata["table"]) == set(publication.PUBLISHED_TABLES)
    assert metadata["denominator"].notna().all() and metadata["scope"].notna().all() and metadata["grain"].notna().all()
    assert (metadata["file_size_bytes"] > 0).all()


def test_dry_run_gia_tri_bang_khop_fixture_biet_truoc(price_wh, tmp_path):
    """Ket noi collect -> compute voi so lieu BIET TRUOC (fixture_specs.price_fixture_spec) o cap end-to-end (khong chi tung metric roi rac)."""
    fx = price_wh
    data = _collect(fx)
    fake_notebook = tmp_path / "fake_notebook.ipynb"
    fake_notebook.write_text("{}", encoding="utf-8")
    tables = wave_a.compute_wave_a_tables(data, _manifest(data, fx, fake_notebook))

    overall = tables["item_availability_overall"].iloc[0]
    assert (overall["n_items"], overall["n_success"], overall["n_sold_out"], overall["n_not_bookable"], overall["n_error"]) == (10, 7, 1, 1, 1)
    assert overall["n_partial"] == 0 and overall["partial_rate"] == 0.0 and overall["success_rate"] == pytest.approx(0.7)

    protocol = tables["protocol_continuity_summary"].iloc[0]
    assert protocol["owner_success"] == 7 and protocol["owner_failure_status_sold_out"] == 1
    assert protocol["owner_failure_status_not_bookable"] == 1 and protocol["owner_failure_status_error"] == 1  # item 9 resolve tu URL -> h2
    assert protocol["missing_source_run"] == 0 and protocol["missing_item_in_existing_run"] == 8
    assert tables["protocol_continuity_unattributed_errors"].empty
    assert (tables["protocol_continuity_exceptions"]["outcome"] != "owner_success").all()

    assert int(tables["price_distribution_overall_main"].iloc[0]["n_obs"]) == 24
    assert float(tables["price_distribution_overall_main"].iloc[0]["max_price"]) == 9_000_000
    assert tables["raw_vs_main_by_source"].iloc[0]["n_items_main"] == 10 and tables["raw_vs_main_by_source"].iloc[0]["n_items_raw"] == 10
    assert int(tables["price_outlier_summary_by_hotel_main"]["n_outliers"].sum()) == 1
    findings = tables["quality_findings"].set_index("check_id")
    assert findings.loc["price_outlier_robust_within_hotel", "count"] == 1
    assert findings.loc["collision_item_status_disagreement", "count"] == 0 and findings.loc["collision_option_price_divergence", "denominator"] == 0
    # Fixture khong ghi taxes_fees/price_includes_tax/review/address (24 x 5) va 3 observation run 3 khong co selector_version => 123/384 cell NULL. File 17 M1: cac cell
    # nay KHONG con gop vao mot finding 'unexpected' - tach theo lop: `address` (24) va `selector_version` (3) la required_contract (vi pham that, mau so = 24 observation cua
    # chinh field); taxes_fees/price_includes_tax/review_score/review_count (96 cell) la optional_listing (mo ta, info). Ngoai ra dung 1 outlier. MOI check con lai = 0.
    assert findings[findings["count"] > 0]["count"].to_dict() == {
        "required_null_address": 24, "required_null_selector_version": 3, "optional_listing_null_cells": 96, "price_outlier_robust_within_hotel": 1}
    assert (findings.loc["required_null_address", "denominator"], findings.loc["required_null_selector_version", "denominator"]) == (24, 24)
    assert findings.loc["optional_listing_null_cells", "denominator"] == 168 and findings.loc["optional_listing_null_cells", "severity"] == "info"
    assert "unexpected_nulls_available_observations" not in findings.index
    # bao toan: 27 (required) + 96 (optional) = 123 cell NULL, mau so 216 + 168 = 384 - dung bang so cu, chi la tach lop
    assert (int(findings.loc[["required_null_address", "required_null_selector_version"], "count"].sum()) + int(findings.loc["optional_listing_null_cells", "count"])) == 123
    assert {"collision_item_status_disagreement", "collision_option_price_divergence", "price_outlier_robust_within_hotel"} <= set(findings.index)
    assert tables["collision_item_pairs"].empty and int(tables["collision_item_summary"].iloc[0]["n_collision_item_pairs"]) == 0

    calendar = tables["observation_counts_by_calendar_flags_main"]
    assert int(calendar["n_observations"].sum()) == 24  # join holiday KHONG nhan/mat observation
    readiness = tables["dataset_readiness_by_horizon"].set_index("horizon_days")
    assert readiness.loc[1, "theoretical_date_pairs"] == 1 and (readiness.loc[[3, 7, 14], "theoretical_date_pairs"] == 0).all()
    assert tables["reference_uniqueness_per_series"]["n_approved"].max() <= 1


def test_item_grain_coverage_la_primary_va_calendar_khong_option_weighted(price_wh, tmp_path):
    data = _collect(price_wh)
    fake_notebook = tmp_path / "fake_notebook.ipynb"
    fake_notebook.write_text("{}", encoding="utf-8")
    tables = wave_a.compute_wave_a_tables(data, _manifest(data, price_wh, fake_notebook))
    # 10 items produce 24 priced options. Primary coverage must remain 10, while the explicitly
    # named observation tables remain available only as option-mix appendices.
    assert int(tables["item_checkin_month_distribution_main"]["n_items"].sum()) == 10
    assert int(tables["checkin_month_distribution_main"]["n_observations"].sum()) == 24
    assert int(tables["item_lead_time_bucket_distribution_main"]["n_items"].sum()) == 10
    assert int(tables["lead_time_bucket_distribution_main"]["n_observations"].sum()) == 24
    calendar = tables["item_calendar_coverage_main"]
    assert int(calendar["n_items"].sum()) == 10
    assert int(calendar["n_checkin_date_city_cells"].sum()) == 5


def test_effective_identity_lan_sang_bang_publish_availability_active_hotel(price_wh, tmp_path):
    """GPT file 13 M1 (acceptance review file 15): fixture item 9 co `hotel_id=NULL` nhung `source_hotel_link` resolve duoc -> h2 (Ha Noi), va h2 KHONG co
    item nao khac trong ngay 06/09. MOI bang item-grain da PUBLISH phai nhan dung hotel/city; SQL diagnostic theo `hotel_id` tho van day item 9 vao
    '(unknown)/(unattributed)' va bo no khoi active hotel (chung minh hai duong tinh khac nhau, va bang publish dung duong effective)."""
    data = _collect(price_wh)
    fake_notebook = tmp_path / "fake_notebook.ipynb"
    fake_notebook.write_text("{}", encoding="utf-8")
    tables = wave_a.compute_wave_a_tables(data, _manifest(data, price_wh, fake_notebook))

    # diagnostic (hotel_id tho) van thay item 9 la (unknown)/(unattributed)
    assert "(unknown)" in set(data["m"]["item_status_counts_by_city_main"]["city"])
    assert "(unattributed)" in set(data["m"]["item_status_counts_by_hotel_main"]["hotel_id"])

    by_city = tables["item_availability_by_city"].set_index("city")
    assert "(unknown)" not in by_city.index
    assert int(by_city["n_items"].sum()) == 10 and int(by_city.loc["Hà Nội", "n_error"]) == 1
    by_hotel = tables["item_availability_by_hotel"].set_index("hotel_id")
    assert "(unattributed)" not in by_hotel.index
    assert (int(by_hotel.loc["h2", "n_items"]), int(by_hotel.loc["h2", "n_error"])) == (3, 1)   # h2: item 5 success + 8 not_bookable + 9 error(resolved)
    by_day_hotel = tables["item_availability_by_crawl_date_hotel"]
    row = by_day_hotel[(by_day_hotel["hotel_id"] == "h2") & (by_day_hotel["crawl_date"].astype(str) == "2026-09-06")].iloc[0]
    assert (int(row["n_items"]), int(row["n_error"])) == (1, 1)

    active = tables["active_hotel_by_crawl_date_source"].assign(d=lambda f: f["vn_crawl_date"].astype(str)).set_index("d")
    diagnostic = data["m"]["active_hotel_by_crawl_date_source"].assign(d=lambda f: f["vn_crawl_date"].astype(str)).set_index("d")
    assert int(active.loc["2026-09-06", "n_active_hotels"]) == 3      # h1 (item 3), h2 (item 9 resolve tu URL), h3 (item 10)
    assert int(diagnostic.loc["2026-09-06", "n_active_hotels"]) == 2  # hotel_id tho bo item 9
    active_city = tables["active_hotel_by_crawl_date_source_city"]
    that_day = active_city[active_city["vn_crawl_date"].astype(str) == "2026-09-06"].set_index("city")["n_active_hotels"]
    assert that_day.to_dict() == {"Hà Nội": 2, "Đà Lạt": 1} and "(unknown)" not in set(active_city["city"])

    coverage = tables["item_lead_time_bucket_distribution_by_city_source_main"]
    assert "(unknown)" not in set(coverage["city"]) and int(coverage["n_items"].sum()) == 10


def _tables(fx, tmp_path):
    data = _collect(fx)
    fake_notebook = tmp_path / "fake_notebook.ipynb"
    fake_notebook.write_text("{}", encoding="utf-8")
    return data, wave_a.compute_wave_a_tables(data, _manifest(data, fx, fake_notebook))


def test_quality_findings_tach_theo_lop_null_khong_con_unexpected_gop(price_wh, tmp_path):
    """File 17 M1: (1) khong con finding gop 'unexpected_nulls'; (2) moi field required_contract 1 finding rieng voi mau so = observation cua chinh field;
    (3) NULL o field optional_listing KHONG bi tinh la loi (info) va KHONG lan vao required; (4) tong 3 lop = tong bang missingness goc; (5) sample_keys that."""
    data, tables = _tables(price_wh, tmp_path)
    findings = tables["quality_findings"].set_index("check_id")
    assert not [c for c in findings.index if "unexpected" in c]
    for field in null_taxonomy.required_fields():
        assert f"required_null_{field}" in findings.index
    assert "optional_listing_null_cells" in findings.index and "source_metadata_expected_gap_null_cells" in findings.index
    # (2)(3): address la required (24/24 NULL trong fixture) con taxes_fees la optional (NULL 24/24 nhung KHONG tao finding required)
    assert findings.loc["required_null_address", "severity"] == "medium" and "required_null_taxes_fees" not in findings.index
    assert json.loads(findings.loc["required_null_address", "sample_keys"]) == list(range(1, 11))       # sample record_id THAT (toi da 10)
    assert findings.loc["required_null_selector_version", "count"] == 3 and len(json.loads(findings.loc["required_null_selector_version", "sample_keys"])) == 3
    # (4) phan hoach: tong theo lop = tong bang goc
    raw = data["m"]["missingness_available_observations"]
    class_summary = tables["missingness_null_class_summary"]
    assert int(class_summary["n_null_cells"].sum()) == int(raw["n_null"].sum()) and int(class_summary["n_total_cells"].sum()) == int(raw["n_total"].sum())
    assert set(tables["missingness_null_taxonomy"]["null_class"]) <= set(null_taxonomy.NULL_CLASSES)
    assert tables["null_taxonomy_registry"].set_index("field").loc["git_commit", "source_overrides"] == "vps=source_metadata_expected_gap"

    # free_cancellation NULL: finding RIENG voi mau so rieng, khong lan sang optional/nhung lop khac
    record_id, = query_one(price_wh, "SELECT record_id FROM price_observations WHERE is_sold_out=0 ORDER BY record_id LIMIT 1")
    with mutate_row(price_wh, "price_observations", "record_id", record_id, {"free_cancellation": None}):
        _, mutated_tables = _tables(price_wh, tmp_path)
    mutated_findings = mutated_tables["quality_findings"].set_index("check_id")
    row = mutated_findings.loc["required_null_free_cancellation"]
    assert (row["count"], row["denominator"], row["severity"]) == (1, 24, "medium") and json.loads(row["sample_keys"]) == [record_id]
    assert row["rate"] == pytest.approx(1 / 24)
    assert mutated_findings.loc["optional_listing_null_cells", "count"] == findings.loc["optional_listing_null_cells", "count"]   # khong lan sang lop khac
    assert "rate_plan_key" in row["likely_cause"]     # nhan canonical-semantic: NULL doi ngu nghia key
    required_stats = mutated_tables["missingness_required_contract_by_field"].set_index("field")
    assert (required_stats.loc["free_cancellation", "n_null"], required_stats.loc["free_cancellation", "n_total"]) == (1, 24)
    assert required_stats.loc["free_cancellation", "canonical_key_role"] == "rate_plan_key"


def test_duplicate_series_tables_e2e_khop_metric_sql_va_audit_sample_tren_fixture(price_wh, tmp_path):
    """File 17 M3 tren pipeline that: tiem 1 nhom trung key khac gia -> 3 bang duplicate + finding co sample that + guard doi soat; roi hoan tac -> bang rong dung schema."""
    fx = price_wh
    item_id, = query_one(fx, "SELECT crawl_run_item_id FROM price_observations WHERE is_sold_out=0 GROUP BY crawl_run_item_id ORDER BY COUNT(*) DESC, crawl_run_item_id LIMIT 1")
    first, second = [r for (r,) in [query_one(fx, "SELECT record_id FROM price_observations WHERE crawl_run_item_id=%s ORDER BY record_id LIMIT 1 OFFSET %s",
                                             (item_id, offset)) for offset in (0, 1)]]
    room_key, rate_key, series_id = query_one(
        fx, "SELECT canonical_room_key, canonical_rate_key, canonical_series_id FROM curated_observation_keys WHERE record_id=%s", (first,))
    with mutate_row(fx, "curated_observation_keys", "record_id", second, {"canonical_room_key": room_key, "canonical_rate_key": rate_key, "canonical_series_id": series_id}):
        data, tables = _tables(fx, tmp_path)
        summary = tables["duplicate_series_summary_by_source_city"]
        grand = summary[(summary["scope"] == "RAW") & (summary["source_code"] == "(all)") & (summary["city"] == "(all)")].iloc[0]
        assert (grand["duplicate_groups"], grand["extra_observations"], grand["n_groups"], grand["same_price_groups"], grand["divergent_price_groups"]) == (1, 1, 23, 0, 1)
        assert grand["divergent_share"] == 1.0 and grand["max_group_size"] == 2
        spread = tables["duplicate_series_price_spread_summary"]
        absolute = spread[(spread["scope"] == "RAW") & (spread["source_code"] == "(all)") & (spread["spread_kind"] == "absolute_vnd")].iloc[0]
        assert absolute["n_divergent_groups"] == 1 and absolute["spread_max"] > 0
        audit = tables["duplicate_series_audit_sample"]
        assert len(audit) == 2 and set(audit["record_id"]) == {first, second} and audit["group_id"].nunique() == 1
        assert audit["audit_reasons"].str.contains("largest_group_size#1").all()
        finding = tables["quality_findings"].set_index("check_id").loc["duplicate_daily_series"]
        assert (finding["count"], finding["severity"]) == (1, "high")
        sample_keys = json.loads(finding["sample_keys"])
        assert sample_keys and sample_keys[0]["item_id"] == item_id and sample_keys[0]["group_size"] == 2
        assert "1/1 nhom" in finding["likely_cause"] and "100.0%" in finding["likely_cause"] and "canonicalization_version" in finding["likely_cause"]
        # guard doi soat: neu metric SQL doc lap lech bang tom tat -> pipeline raise, khong publish so lech
        broken = {**data, "m": {**data["m"], "quality_duplicate_daily_series": pd.DataFrame(
            [{"n_duplicate_groups": 5, "n_extra_observations": 1, "n_total_groups": 23}])}}
        with pytest.raises(RuntimeError, match="LECH quality_duplicate_daily_series"):
            wave_a._assert_duplicate_summary_reconciles_quality_metric(
                broken, tables["duplicate_series_summary_by_source_city"])
    _, clean_tables = _tables(fx, tmp_path)         # da hoan tac
    assert clean_tables["duplicate_series_audit_sample"].empty
    assert list(clean_tables["duplicate_series_audit_sample"].columns) == list(metrics.DUPLICATE_AUDIT_SAMPLE_COLUMNS)
    clean_finding = clean_tables["quality_findings"].set_index("check_id").loc["duplicate_daily_series"]
    assert clean_finding["count"] == 0 and json.loads(clean_finding["sample_keys"]) == []


def test_anchor_tables_e2e_tong_anchor_theo_thu_bang_tong_anchor_va_gia_theo_thu_co_n_distinct(price_wh, tmp_path):
    """File 17 M4 tren pipeline that: INVARIANT tong anchor theo thu = so anchor phan biet; bang gia theo thu/co calendar co n_distinct_checkin_dates; report co caveat anchor."""
    data, tables = _tables(price_wh, tmp_path)
    anchors = tables["checkin_anchor_dates_main"]
    weekday = tables["item_checkin_weekday_distribution_main"]
    month = tables["item_checkin_month_distribution_main"]
    assert len(anchors) == 3 and int(anchors["n_items"].sum()) == 10                       # 10/09 (Thu), 12/09 (Sat), 20/09 (Sun)
    assert int(weekday["n_distinct_checkin_dates"].sum()) == len(anchors) == int(month["n_distinct_checkin_dates"].sum())
    assert sorted(weekday["weekday"]) == ["Saturday", "Sunday", "Thursday"] and (weekday["n_distinct_checkin_dates"] == 1).all()
    assert weekday.set_index("weekday").loc["Saturday", "checkin_dates"] == "2026-09-12"
    # item nhieu (Saturday 4 item, Thursday 3) nhung MOI thu chi 1 anchor -> n_items KHONG suy ra so ngay
    assert int(weekday.set_index("weekday").loc["Saturday", "n_items"]) == 4
    price_weekday = tables["price_distribution_by_weekday_main"]
    assert int(price_weekday["n_distinct_checkin_dates"].sum()) == 3 and price_weekday["n_obs"].sum() == 24
    assert int(tables["price_distribution_by_calendar_flags_main"]["n_distinct_checkin_dates"].sum()) == 4      # (0,0,0,0) chua 2 ngay + festival 1 + le 1
    assert int(tables["observation_counts_by_calendar_flags_main"]["n_distinct_checkin_dates"].sum()) == 4
    assert tables["item_calendar_coverage_main"]["n_distinct_checkin_dates"].sum() >= 3
    saturday = anchors[anchors["weekday"] == "Saturday"].iloc[0]
    assert saturday["n_items"] == 4 and saturday["is_weekend_fri_sat"] and saturday["n_crawl_dates"] == 2
    assert anchors.set_index("weekday").loc["Thursday", "is_festival_period_any_city"]
    report_input_manifest = _manifest(data, price_wh, tmp_path / "fake_notebook.ipynb")
    analysis_dir = artifacts.new_analysis_dir(f"eda_test_anchor_{price_wh['batch_id']}", outputs_dir=tmp_path / "outputs")
    wave_a.write_eda_report_and_dictionary(data, tables, analysis_dir, input_manifest=report_input_manifest)
    report_text = (analysis_dir / "EDA_REPORT.md").read_text(encoding="utf-8")
    assert report_text.count("KHONG phai uoc luong causal weekday effect hay holiday uplift") == 2      # caveat ngay canh bang o 7.4 va 7.7
    assert "Anchor check-in (3 ngay phan biet" in report_text and "Thursday 1" in report_text and "n_distinct_checkin_dates" in report_text


_REQUIRED_FINDING_COLUMNS = ["check_id", "severity", "scope", "grain", "count", "denominator", "rate", "sample_keys", "likely_cause", "recommended_action"]
_PLAN_7_11_CHECKS = {
    "price_non_positive", "checkout_not_after_checkin", "lead_time_mismatch", "duplicate_daily_series", "canonical_key_anomalies", "city_outside_scope",
    "parent_mismatch", "success_item_without_observation", "price_outlier_robust_within_hotel",
    "collision_item_status_disagreement", "collision_option_price_divergence",
    # file 17 M1: 'unexpected NULL theo field group' = NULL theo LOP (moi field required_contract mot finding rieng + 2 lop mo ta)
    *(f"required_null_{field}" for field in null_taxonomy.required_fields()), "optional_listing_null_cells", "source_metadata_expected_gap_null_cells",
}


def test_quality_findings_du_cot_bat_buoc_va_du_12_check(price_wh, tmp_path):
    """Plan 7.11: 'Moi finding gom severity, scope, count, denominator, sample keys, likely cause va recommended action' + du cac check cua plan
    (ke ca check count = 0 van co 1 dong - chung minh da chay)."""
    fake_notebook = tmp_path / "fake_notebook.ipynb"
    fake_notebook.write_text("{}", encoding="utf-8")
    data = _collect(price_wh)
    findings = wave_a.build_quality_findings(data)
    assert list(findings.columns) == _REQUIRED_FINDING_COLUMNS
    assert _PLAN_7_11_CHECKS <= set(findings["check_id"]) and findings["check_id"].is_unique
    for column in ("severity", "scope", "grain", "likely_cause", "recommended_action", "sample_keys"):
        assert findings[column].astype(str).str.strip().ne("").all(), column
    assert set(findings["severity"]) <= {"info", "medium", "high"} and set(findings["scope"]) <= {"RAW", "MAIN"}
    flagged = findings[findings["count"] > 0]
    assert len(flagged) == 4  # required address + required selector_version + optional_listing (fixture khong ghi taxes/review/address) + 1 outlier
    assert all(json.loads(s) for s in flagged["sample_keys"]), "check co count>0 phai co sample_keys THAT (khong phai [])"
    assert all(json.loads(s) == [] for s in findings[findings["count"] == 0]["sample_keys"])  # count=0 => khong co gi de sample


def test_bang_reference_khong_dung_status_unavailable_alias_ambiguous(price_wh, tmp_path):
    """Plan 7.10: 3 status unavailable/alias/ambiguous chi ton tai sau causal matching o Wave B - bang reference cua Notebook 01 khong duoc mang chung
    (khong cot, khong gia tri)."""
    fake_notebook = tmp_path / "fake_notebook.ipynb"
    fake_notebook.write_text("{}", encoding="utf-8")
    data = _collect(price_wh)
    tables = wave_a.compute_wave_a_tables(data, _manifest(data, price_wh, fake_notebook))
    forbidden = ("unavailable", "alias", "ambiguous")
    checked = [name for name in tables if name.startswith("reference_")]
    assert len(checked) >= 8
    for name in checked:
        table = tables[name]
        assert not [c for c in table.columns if any(w in str(c).lower() for w in forbidden)], name
        for column in [c for c in table.columns if pd.api.types.is_string_dtype(table[c].dtype)]:
            values = " ".join(str(v).lower() for v in table[column].dropna().unique())
            assert not any(w in values for w in forbidden), (name, column)


def test_collect_fail_khi_snapshot_co_item_chua_terminal(price_wh):
    """Plan 7.1: 'Assert khong co run/item queued hoac running trong snapshot' - tiem 1 item queued (roi 1 run running) => collect PHAI raise."""
    fx = price_wh
    with mutated(fx, [("UPDATE crawl_run_items SET status='queued' WHERE id=1", ())], [("UPDATE crawl_run_items SET status='success' WHERE id=1", ())]):
        with pytest.raises(RuntimeError, match="queued/running"):
            _collect(fx)
    with mutated(fx, [("UPDATE crawl_runs SET status='running' WHERE id=1", ())], [("UPDATE crawl_runs SET status='completed' WHERE id=1", ())]):
        with pytest.raises(RuntimeError, match="crawl_runs chua terminal"):
            _collect(fx)
    assert _collect(fx)["non_terminal"].iloc[0].sum() == 0  # da hoan tac: thu thap lai binh thuong


def test_dry_run_collision_tables_tren_fixture_2_nguon(collision_wh, tmp_path):
    """Contract GPT file 11 muc 3 tren fixture 2 nguon: 2 collision item-pair, 1 cap success-success, 1 shared option 1-1 (X) chenh 20.000."""
    fx = collision_wh
    data = _collect(fx)
    fake_notebook = tmp_path / "fake_notebook.ipynb"
    fake_notebook.write_text("{}", encoding="utf-8")
    tables = wave_a.compute_wave_a_tables(data, _manifest(data, fx, fake_notebook))

    item_summary = tables["collision_item_summary"].iloc[0]
    assert (item_summary["n_collision_item_pairs"], item_summary["n_status_concordant"], item_summary["n_status_disagreement"],
            item_summary["n_success_success_pairs"]) == (2, 1, 1, 1)
    concordance = tables["collision_item_status_concordance"].set_index(["status_a", "status_b"])["n_pairs"]
    assert concordance[("success", "success")] == 1 and concordance[("success", "sold_out")] == 1
    stratified = tables["collision_item_time_diff_stratification"].set_index("time_diff_bucket")["n_pairs"]
    assert stratified["6-15"] == 2 and stratified["0-5"] == 0  # finished_at chenh 15 va 12 phut

    detail = tables["collision_option_detail"]
    assert len(detail) == 1 and float(detail.iloc[0]["price_abs_diff"]) == 20_000
    assert float(detail.iloc[0]["price_relative_diff"]) == pytest.approx(20_000 / 510_000)
    option_summary = tables["collision_option_summary"].iloc[0]
    assert option_summary["n_option_pairs"] == 1 and option_summary["n_exact_price_match"] == 0
    assert option_summary["cancellation_policy_concordance_rate"] == 1.0
    assert option_summary["n_taxes_both_null"] == 1 and option_summary["taxes_fees_concordance_rate"] == 1.0
    option_strat = tables["collision_option_time_diff_stratification"].set_index("time_diff_bucket")
    assert option_strat.loc["6-15", "n_option_pairs"] == 1  # observed_at chenh 15 phut
    coverage = tables["collision_option_pair_coverage"].iloc[0]
    assert (coverage["n_options_a"], coverage["n_options_b"], coverage["n_shared_options"], coverage["n_union_options"]) == (2, 2, 1, 3)
    assert coverage["option_jaccard"] == pytest.approx(1 / 3) and coverage["n_ambiguous_shared_keys"] == 0
    findings = tables["quality_findings"].set_index("check_id")
    assert findings.loc["collision_item_status_disagreement", "count"] == 1 and findings.loc["collision_item_status_disagreement", "denominator"] == 2
    assert findings.loc["collision_option_price_divergence", "count"] == 1 and findings.loc["collision_option_price_divergence", "denominator"] == 1
    # MAIN khong bi doi: 2 item vps collision la non_owner_duplicate => chi RAW; item vps thu 3 (khong co item local cung khoa) KHONG thanh cap
    raw_vs_main = tables["raw_vs_main_by_source"].set_index("source_code")
    assert raw_vs_main.loc["vps", "n_items_raw"] == 3 and raw_vs_main.loc["vps", "n_items_main"] == 0
    assert raw_vs_main.loc["local_primary", "n_items_main"] == 2
    # warehouse item id duoc gan lai theo (source_priority, source_pk): local 1-2, vps 3-5 => 2 item vps collision = {3, 4}; item vps thu 3 (= 5) KHONG xuat hien
    assert len(tables["collision_item_pairs"]) == 2 and set(tables["collision_item_pairs"]["item_id_b"]) == {3, 4}
    assert tables["collision_option_near_time_concentration"].empty      # cap X chenh 15 phut => khong co near-time (bucket 0-5)
    ownership = tables["ownership_by_source_status_reason"].set_index(["source_code", "ownership_status"])["n_items"]
    assert ownership[("vps", "non_owner_duplicate")] == 2 and ownership[("vps", "unassigned")] == 1  # non_owner_duplicate / off-plan RAW-only
    assert set(tables["collision_item_pairs"]["ownership_status_a"]) == {"owner_success"}
    assert set(tables["collision_item_pairs"]["ownership_status_b"]) == {"non_owner_duplicate"}


def test_collision_near_time_concentration_e2e_tiem_cap_option_chenh_3_phut_vao_bucket_0_5(collision_wh, tmp_path):
    """File 17 MINOR 1 tren fixture 2 nguon: co so cap X (local 10:15 / vps 10:30, chenh 15 phut) KHONG near-time; keo observed_at cua vps X ve 10:18 (chenh 3 phut, cung
    ngay VN nen lead_time khong doi) => dung 1 dong near-time (local_primary/vps, 01/09, h1) voi 1 cap non-exact lech 20.000 - toan bo chuoi SQL that ->
    `collision_option_analysis` -> `metrics.collision_near_time_concentration`; hoan tac tu dong (`mutate_row`)."""
    fx = collision_wh
    vps_x_record_id = int(query_one(fx, "SELECT record_id FROM price_observations WHERE price_per_night=520000")[0])
    fake_notebook = tmp_path / "fake_notebook.ipynb"
    fake_notebook.write_text("{}", encoding="utf-8")
    with mutate_row(fx, "price_observations", "record_id", vps_x_record_id, {"observed_at": dt.datetime(2026, 9, 1, 10, 18)}) as original:
        assert original["observed_at"] == dt.datetime(2026, 9, 1, 10, 30)
        data = _collect(fx)
        tables = wave_a.compute_wave_a_tables(data, _manifest(data, fx, fake_notebook))
    near = tables["collision_option_near_time_concentration"]
    assert list(near.columns) == list(metrics.COLLISION_NEAR_TIME_COLUMNS) and len(near) == 1
    row = near.iloc[0]
    assert (row["source_a"], row["source_b"], row["hotel_id"], str(row["vn_crawl_date"])) == ("local_primary", "vps", "h1", "2026-09-01")
    assert (row["n_option_pairs"], row["n_exact_price_match"], row["n_non_exact"]) == (1, 0, 1)
    assert row["non_exact_rate"] == 1.0 and row["share_of_near_time_pairs"] == 1.0
    assert row["median_price_abs_diff_non_exact"] == 20_000 and row["max_price_abs_diff"] == 20_000
    assert row["min_observed_at_diff_minutes"] == pytest.approx(3.0) and row["max_observed_at_diff_minutes"] == pytest.approx(3.0)
    stratified = tables["collision_option_time_diff_stratification"].set_index("time_diff_bucket")["n_option_pairs"]
    assert stratified["0-5"] == 1 and stratified["6-15"] == 0       # cap X da chuyen bucket 6-15 -> 0-5, tong khong doi
    # hoan tac that: collect lai voi observed_at goc => bang near-time rong lai (khong ban fixture session dung chung)
    data_after = _collect(fx)
    assert wave_a.compute_wave_a_tables(data_after, _manifest(data_after, fx, fake_notebook))["collision_option_near_time_concentration"].empty


def test_dry_run_that_bai_duoc_danh_dau_ro_khong_trong_nhu_pass(price_wh, tmp_path):
    """Gia lap that bai giua chung (batch_id sai) - phai bi artifacts.mark_failed(), khong duoc de lai artifact trong nhu da PASS."""
    fx = price_wh
    analysis_dir = artifacts.new_analysis_dir(f"eda_test_fail_{fx['batch_id']}", outputs_dir=tmp_path / "outputs")
    try:
        data = _collect(fx)
        # gia lap loi: doi ten report path thanh 1 file KHONG khop batch_id
        bad_report = tmp_path / "bad_report.json"
        bad_report.write_text(json.dumps({"batch_id": "khong_khop"}), encoding="utf-8")
        fake_notebook = tmp_path / "fake_notebook.ipynb"
        fake_notebook.write_text("{}", encoding="utf-8")
        wave_a.build_input_manifest(
            data, notebook_source_path=fake_notebook, warehouse_validation_report_path=bad_report,
            ownership_manifest_path=fx["ownership_manifest_path"], cohort_history_path=fx["cohort_history_path"],
        )
        raise AssertionError("le ra phai raise ValueError vi batch_id khong khop")
    except ValueError as exc:
        artifacts.mark_failed(analysis_dir, error=str(exc))

    assert (analysis_dir / "FAILED.json").exists()
    assert not (analysis_dir / "artifact_manifest.json").exists()


def test_run_wave_a_end_to_end_nbclient_that_tren_notebook_01_that(price_wh, tmp_path):
    """GPT review 12 eda M3: chay THAT `nbclient` tren CHINH source Notebook 01 (khong goi thang `wave_a.*`), qua ham runner testable
    `run_wave_a.run_wave_a()`. Assert executed notebook + TOAN BO bang/hinh/artifact co trong artifact_manifest.json, va source notebook VAN
    output-free sau khi chay (executed notebook ghi RIENG duoi executed_notebooks/, khong ghi nguoc lai source)."""
    fx = price_wh
    analysis_dir = run_wave_a.run_wave_a(
        pointer_path=fx["pointer_path"], ownership_manifest_path=fx["ownership_manifest_path"],
        cohort_history_path=fx["cohort_history_path"], cohort_history_base_dir=fx["cohort_history_path"].parent,
        vn_holidays_csv=fx["vn_holidays_csv_path"], warehouse_validation_report_path=fx["warehouse_validation_report_path"],
        source_manifest_path=fx["source_manifest_path"], outputs_dir=tmp_path / "outputs", timeout=300,
    )

    manifest_path = analysis_dir / "artifact_manifest.json"
    assert manifest_path.exists()
    file_paths = {entry["path"] for entry in json.loads(manifest_path.read_text(encoding="utf-8"))["files"]}
    # MOI artifact ma coverage matrix co the tham chieu (bang + hinh + static) phai co that trong manifest cuoi cung.
    missing = sorted(publication.known_artifact_paths() - file_paths)
    assert not missing, f"artifact khai bao nhung KHONG co trong manifest: {missing}"
    assert (analysis_dir / "figures" / "price_histogram_main.png").stat().st_size > 1000

    source_notebook = run_wave_a.NOTEBOOKS_DIR / "01_warehouse_full_history_eda.ipynb"
    source_nb = json.loads(source_notebook.read_text(encoding="utf-8"))
    assert all(
        cell.get("execution_count") is None and cell.get("outputs", []) == []
        for cell in source_nb["cells"] if cell["cell_type"] == "code"
    ), "source notebook phai van output-free - executed copy khong duoc ghi nguoc lai source"

    # env override phai duoc don sach sau khi run_wave_a() tra ve - khong leak sang test/tien trinh khac.
    for name in run_wave_a._OPTIONAL_ENV_PARAMS:
        assert name not in os.environ, f"{name} bi leak ra ngoai run_wave_a()"


def test_run_wave_a_that_bai_qua_runner_khong_de_lai_manifest_pass(price_wh, tmp_path):
    """Runner phai `mark_failed()` + re-raise khi notebook that bai giua chung (ownership manifest path sai) - khong duoc de lai
    `artifact_manifest.json` trong nhu PASS (GPT review 12 M2/M3)."""
    fx = price_wh
    with pytest.raises(Exception):
        run_wave_a.run_wave_a(
            pointer_path=fx["pointer_path"], ownership_manifest_path=tmp_path / "khong_ton_tai.json",
            cohort_history_path=fx["cohort_history_path"], cohort_history_base_dir=fx["cohort_history_path"].parent,
            vn_holidays_csv=fx["vn_holidays_csv_path"], warehouse_validation_report_path=fx["warehouse_validation_report_path"],
            outputs_dir=tmp_path / "outputs", timeout=300,
        )

    analysis_dirs = [p for p in (tmp_path / "outputs").iterdir() if p.is_dir()]
    assert len(analysis_dirs) == 1
    analysis_dir = analysis_dirs[0]
    assert (analysis_dir / "FAILED.json").exists()
    assert not (analysis_dir / "artifact_manifest.json").exists()
