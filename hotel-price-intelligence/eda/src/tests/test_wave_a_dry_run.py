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

import artifacts
import publication
import queries
import run_wave_a
import wave_a
from warehouse_fixture import mutated

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
    assert protocol["owner_failure_status_not_bookable"] == 1 and protocol["owner_failure_status_error"] == 1  # item 9 resolve tu URL -> h1
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
    # Fixture khong ghi taxes_fees/price_includes_tax/review/address (24 x 5) va 3 observation run 3 khong co selector_version => 123/384 cell NULL
    # (free_cancellation them vao rate-plan group nhung fixture ghi 0 day du, nen numerator khong doi; denominator +24).
    # ngoai du kien; ngoai ra dung 1 outlier. MOI check con lai = 0 (dem tay tu fixture_specs, khong tin ket qua SQL suong).
    assert findings[findings["count"] > 0]["count"].to_dict() == {
        "unexpected_nulls_available_observations": 123, "price_outlier_robust_within_hotel": 1}
    assert findings.loc["unexpected_nulls_available_observations", "denominator"] == 384
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


_REQUIRED_FINDING_COLUMNS = ["check_id", "severity", "scope", "grain", "count", "denominator", "rate", "sample_keys", "likely_cause", "recommended_action"]
_PLAN_7_11_CHECKS = {
    "price_non_positive", "checkout_not_after_checkin", "lead_time_mismatch", "duplicate_daily_series", "canonical_key_anomalies", "city_outside_scope",
    "parent_mismatch", "success_item_without_observation", "unexpected_nulls_available_observations", "price_outlier_robust_within_hotel",
    "collision_item_status_disagreement", "collision_option_price_divergence",
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
    assert len(flagged) == 2  # unexpected NULL (fixture khong ghi taxes/review/address) + 1 outlier
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
    ownership = tables["ownership_by_source_status_reason"].set_index(["source_code", "ownership_status"])["n_items"]
    assert ownership[("vps", "non_owner_duplicate")] == 2 and ownership[("vps", "unassigned")] == 1  # non_owner_duplicate / off-plan RAW-only
    assert set(tables["collision_item_pairs"]["ownership_status_a"]) == {"owner_success"}
    assert set(tables["collision_item_pairs"]["ownership_status_b"]) == {"non_owner_duplicate"}


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
