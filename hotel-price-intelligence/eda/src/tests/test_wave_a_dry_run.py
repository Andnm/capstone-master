"""Dry-run tren fixture warehouse DISPOSABLE - chung minh vong doi artifact (GPT review 12 eda M2, va
yeu cau tuong minh cua GPT: "Dry-run/fixture execution tao du bo artifact nho de GPT kiem lifecycle;
khong dung warehouse full"). KHONG dung `warehouse_current.json` that o bat ky buoc nao.

Warehouse fixture duoc dung bang CHINH `app.warehouse.batch.build_warehouse()` that (tai su dung toan
bo logic import/curated/reference/validate da test rieng cua backend, khong tu hand-insert ETL row -
tranh vi pham invariant ma khong biet).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

import openpyxl
import pytest

import artifacts
import db
import run_wave_a
import wave_a

D, T = dt.date, dt.datetime


def _root(sql, params=()):
    with db._connect_raw(None) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        conn.commit()
        cursor.close()


@pytest.fixture()
def fixture_warehouse(tmp_path):
    """1 warehouse DISPOSABLE, 1 nguon, 2 hotel, du lieu toi gian nhung THAT (qua build_warehouse
    that). Yield (pointer_path, warehouse_validation_report_path, ownership_manifest_path,
    cohort_history_path, vn_holidays_csv_path). DROP database trong finally."""
    db._ensure_backend_importable()
    from app.core.config import settings
    from app.warehouse.batch import BuildInputs, build_warehouse
    from app.warehouse.bootstrap import apply_setup_sql, create_warehouse_database
    from app.warehouse.cohort_manifest import VALID_CITIES
    from app.warehouse.connection import warehouse_connection
    from app.warehouse.hashing import file_sha256
    from app.warehouse.ownership_manifest import OwnershipRow, write_ownership_manifest
    from app.warehouse.source_manifest import compute_schema_sha256_from_dump
    from app.warehouse.staging import _client_defaults_file

    tag = uuid.uuid4().hex[:8]
    source_db = f"warehouse_edafx{tag}"
    warehouse_db = f"warehouse_edafxwh{tag}"
    created = [source_db, warehouse_db]
    try:
        create_warehouse_database(source_db)
        apply_setup_sql(source_db)
        with warehouse_connection(source_db) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO hotels (hotel_id,name,hotel_link,city,attributes_updated_at,created_at) "
                "VALUES ('h1','H1','https://x/h1','Sai',%s,%s),('h2','H2','https://x/h2','Sai',%s,%s)",
                (T(2026, 9, 1),) * 2 + (T(2026, 9, 1),) * 2,
            )
            # LUU Y: started_at la UTC (session pin time_zone='+00:00'). 17:35 UTC = 00:35 VN NGAY HOM
            # SAU - tung tu bat loi nay khi viet fixture nay lan dau (crawl_date resolve thanh 02/09
            # thay vi 01/09, item bi 'post_protocol_window'). Dung 10:00 UTC = 17:00 VN, an toan trong
            # dung ngay 01/09 VN (xem memory project_mysql_timestamp_tz_pitfall).
            cursor.execute(
                "INSERT INTO crawl_runs (id,status,started_at,finished_at) VALUES (1,'completed',%s,%s)",
                (T(2026, 9, 1, 10, 0), T(2026, 9, 1, 10, 30)),
            )
            for item, hotel, checkin, status in (
                (1, "h1", D(2026, 9, 5), "success"), (2, "h2", D(2026, 9, 5), "sold_out"),
            ):
                cursor.execute(
                    "INSERT INTO crawl_run_items (id,crawl_run_id,source_hotel_link,source_link_hash,"
                    "hotel_link,hotel_id,checkin_date,checkout_date,status) VALUES (%s,1,'u',%s,'u',%s,%s,%s,%s)",
                    (item, hashlib.sha256(hotel.encode()).hexdigest(), hotel, checkin,
                     checkin + dt.timedelta(days=1), status),
                )
            room = {"room_type_raw": "Deluxe", "max_occupancy": 2, "bed_config": "1 giuong",
                    "room_area": "20 m2", "breakfast_included": 1, "free_cancellation": 0,
                    "cancellation_policy": "Khong hoan"}
            from app.scraper.reference import rate_plan_key, room_identity_key
            cursor.execute(
                "INSERT INTO price_observations (record_id,hotel_id,crawl_run_id,crawl_run_item_id,"
                "observed_at,checkin_date,checkout_date,lead_time,room_option_index,room_option_key,"
                "room_identity_key,rate_plan_key,price_total,price_per_night,is_sold_out,"
                "availability_status,room_type_raw,max_occupancy,bed_config,room_area,"
                "breakfast_included,free_cancellation,cancellation_policy) VALUES "
                "(1,'h1',1,1,%s,%s,%s,4,0,%s,%s,%s,500000,500000,0,'available',%s,%s,%s,%s,%s,%s,%s)",
                (T(2026, 9, 1, 10, 15), D(2026, 9, 5), D(2026, 9, 6), "k1",
                 room_identity_key(room), rate_plan_key(room), room["room_type_raw"],
                 room["max_occupancy"], room["bed_config"], room["room_area"],
                 room["breakfast_included"], room["free_cancellation"], room["cancellation_policy"]),
            )
            cursor.execute(
                "INSERT INTO price_observations (record_id,hotel_id,crawl_run_id,crawl_run_item_id,"
                "observed_at,checkin_date,checkout_date,lead_time,room_option_index,room_option_key,"
                "is_sold_out,availability_status) VALUES (2,'h2',1,2,%s,%s,%s,4,0,'k2',1,'sold_out')",
                (T(2026, 9, 1, 10, 15), D(2026, 9, 5), D(2026, 9, 6)),
            )
            conn.commit()
            cursor.close()
        dump_path = tmp_path / "src.sql"
        with _client_defaults_file(settings.DB_USER, settings.DB_PASSWORD) as cnf, open(dump_path, "wb") as out:
            subprocess.run(["mysqldump", f"--defaults-extra-file={cnf}", "--single-transaction",
                            "--no-tablespaces", "--set-charset", "--complete-insert", source_db,
                            "hotels", "crawl_runs", "crawl_run_items", "price_observations"],
                           stdout=out, check=True)

        source_manifest = {"manifest_version": 1, "sources": [{
            "source_code": "local_primary", "source_priority": 0, "dump_path": str(dump_path),
            "dump_sha256": file_sha256(dump_path), "dump_taken_at": "2026-09-01T00:00:00Z",
            "schema_sha256": compute_schema_sha256_from_dump(dump_path)[0],
            "source_version_json": {"fixture": True},
        }]}
        (tmp_path / "source_manifest.json").write_text(json.dumps(source_manifest), encoding="utf-8")

        cohort_path = tmp_path / "cohort.xlsx"
        workbook = openpyxl.Workbook()
        workbook.remove(workbook.active)
        for city in VALID_CITIES:
            sheet = workbook.create_sheet(city)
            sheet.append(["Tên khách sạn", "Link"])
        workbook["Hà Nội"].append(["H1", "https://www.booking.com/hotel/vn/h1.vi.html"])
        workbook["Hà Nội"].append(["H2", "https://www.booking.com/hotel/vn/h2.vi.html"])
        workbook.save(cohort_path)

        ownership_path = tmp_path / "ownership.json"
        write_ownership_manifest(
            [OwnershipRow("local_primary", D(2026, 9, 1), "N1", D(2026, 9, 5))], ownership_path
        )

        cohort_history_path = tmp_path / "cohort_history.json"
        cohort_history_path.write_text(json.dumps({
            "cohort_history_version": 1,
            "versions": [{"cohort_version": "v1", "effective_from_crawl_date": "2026-09-01",
                         "workbook_path": "cohort.xlsx",
                         "members_sha256": _cohort_sha256(cohort_path), "size": 2}],
        }), encoding="utf-8")

        vn_holidays_path = tmp_path / "vn_holidays.csv"
        vn_holidays_path.write_text(
            "holiday_date,event_code,name,event_type,scope,city,is_tet,status,source_url\n"
            "2026-09-02,national_day,Quoc khanh,public_holiday,national,,0,confirmed,https://example.test\n",
            encoding="utf-8",
        )

        create_warehouse_database(warehouse_db)
        apply_setup_sql(warehouse_db)
        from app.warehouse.bootstrap import create_etl_tables
        create_etl_tables(warehouse_db)

        inputs = BuildInputs(
            warehouse_database=warehouse_db, source_manifest_path=tmp_path / "source_manifest.json",
            cohort_manifest_path=cohort_path, ownership_manifest_path=ownership_path,
            base_dir=tmp_path, batch_id=f"fx{tag}", report_dir=tmp_path / "reports",
            require_clean_provenance=False,
        )
        report = build_warehouse(inputs)
        assert report["status"] == "pass", report.get("failed_checks")

        report_path = tmp_path / "reports" / f"fx{tag}.json"
        pointer_path = tmp_path / "pointer.json"
        pointer_path.write_text(json.dumps({
            "warehouse_database": warehouse_db, "batch_id": f"fx{tag}",
            "source_manifest_sha256": report["steps"]["16_source_manifest_sha256"],
            "cohort_manifest_sha256": None, "ownership_manifest_sha256": None,
        }), encoding="utf-8")

        yield {
            "pointer_path": pointer_path, "warehouse_validation_report_path": report_path,
            "ownership_manifest_path": ownership_path, "cohort_history_path": cohort_history_path,
            "vn_holidays_csv_path": vn_holidays_path, "batch_id": f"fx{tag}",
            "source_manifest_path": tmp_path / "source_manifest.json",
        }
    finally:
        for database in created:
            _root(f"DROP DATABASE IF EXISTS `{database}`")


def _cohort_sha256(path: Path) -> str:
    db._ensure_backend_importable()
    from app.warehouse.cohort_manifest import load_cohort_manifest

    return load_cohort_manifest(path).manifest_sha256


@pytest.mark.mysql
def test_dry_run_lifecycle_day_du_thanh_cong(fixture_warehouse, tmp_path):
    """Toan bo pipeline (collect -> input_manifest -> tables -> summary -> artifact_manifest) tren
    fixture disposable - KHONG dung warehouse_current.json that."""
    fx = fixture_warehouse
    data = wave_a.collect_wave_a_data(
        pointer_path=fx["pointer_path"], ownership_manifest_path=fx["ownership_manifest_path"],
        cohort_history_path=fx["cohort_history_path"], cohort_history_base_dir=fx["cohort_history_path"].parent,
        vn_holidays_csv=fx["vn_holidays_csv_path"],
    )
    assert data["snapshot"].batch_id == fx["batch_id"]
    assert int(data["core_counts"]["price_observations"].iloc[0]) == 2

    fake_notebook = tmp_path / "fake_notebook.ipynb"
    fake_notebook.write_text("{}", encoding="utf-8")
    input_manifest = wave_a.build_input_manifest(
        data, notebook_source_path=fake_notebook,
        warehouse_validation_report_path=fx["warehouse_validation_report_path"],
        ownership_manifest_path=fx["ownership_manifest_path"],
        cohort_history_path=fx["cohort_history_path"], cohort_history_base_dir=fx["cohort_history_path"].parent,
        source_manifest_path=fx["source_manifest_path"],
    )
    assert input_manifest["batch_id"] == fx["batch_id"]
    assert "code_provenance" in input_manifest and "library_versions" in input_manifest
    assert input_manifest["warehouse_validation_report_status"] == "pass"
    assert input_manifest["reconciled_counts"]["crawl_run_items"] == 2
    assert len(input_manifest["cohort_workbook_versions"]) == 1
    assert input_manifest["cohort_workbook_versions"][0]["workbook_file_size_bytes"] > 0

    analysis_dir = artifacts.new_analysis_dir(f"eda_test_{fx['batch_id']}", outputs_dir=tmp_path / "outputs")
    artifacts.atomic_write_json(analysis_dir / "input_manifest.json", input_manifest)
    tables = wave_a.compute_wave_a_tables(data)
    wave_a.write_wave_a_tables(tables, analysis_dir)
    wave_a.write_eda_summary(data, analysis_dir)
    wave_a.write_eda_report_and_dictionary(data, tables, analysis_dir)

    # GPT review 12 eda M4: report/dictionary KHONG con la placeholder - phai co du 12 section 7.x
    # va nhung con so THAT (vd core_counts.hotels) trong noi dung, khong chi 2 cau "se bo sung sau".
    report_text = (analysis_dir / "EDA_REPORT.md").read_text(encoding="utf-8")
    for section_heading in (
        "## 7.1", "## 7.2", "## 7.3", "## 7.4", "## 7.5", "## 7.6",
        "## 7.7", "## 7.8", "## 7.9", "## 7.10", "## 7.11", "## 7.12", "## Wave B",
    ):
        assert section_heading in report_text, f"thieu section {section_heading} trong EDA_REPORT.md"
    assert str(int(data["core_counts"]["hotels"].iloc[0])) in report_text
    dictionary_text = (analysis_dir / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    assert "price_per_night" in dictionary_text and "lead_time" in dictionary_text

    # GPT review 12 M2: manifest CHI duoc ghi SAU KHI moi thu khac da xong.
    manifest_path = artifacts.write_artifact_manifest(analysis_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    file_paths = {entry["path"] for entry in manifest["files"]}
    for expected in ("input_manifest.json", "eda_summary.json", "EDA_REPORT.md", "DATA_DICTIONARY.md",
                     "quality_findings.csv", "dataset_readiness_by_horizon.csv",
                     "tables/ownership_by_source_status_reason.csv"):
        assert expected in file_paths, f"thieu artifact {expected} trong manifest"
    assert "artifact_manifest.json" not in file_paths  # khong tu hash chinh no

    # GPT review 12 eda M4: "notebook khong goi write_wave_a_tables() nen nhieu bang bi thieu" - dam
    # bao TOAN BO key cua compute_wave_a_tables() thuc su ra file, khong chi 1 tap con nhu truoc.
    for name in tables:
        expected_path = f"{name}.csv" if name in wave_a._ROOT_LEVEL_TABLES else f"tables/{name}.csv"
        assert expected_path in file_paths, f"thieu bang {expected_path} trong manifest"

    quality_findings_path = analysis_dir / "quality_findings.csv"
    assert quality_findings_path.exists() and quality_findings_path.stat().st_size > 0


@pytest.mark.mysql
def test_dry_run_that_bai_duoc_danh_dau_ro_khong_trong_nhu_pass(fixture_warehouse, tmp_path):
    """Gia lap that bai giua chung (batch_id sai) - phai bi artifacts.mark_failed(), khong duoc de lai
    artifact trong nhu da PASS (khong co artifact_manifest.json)."""
    fx = fixture_warehouse
    analysis_dir = artifacts.new_analysis_dir(f"eda_test_fail_{fx['batch_id']}", outputs_dir=tmp_path / "outputs")
    try:
        data = wave_a.collect_wave_a_data(
            pointer_path=fx["pointer_path"], ownership_manifest_path=fx["ownership_manifest_path"],
            cohort_history_path=fx["cohort_history_path"], cohort_history_base_dir=fx["cohort_history_path"].parent,
            vn_holidays_csv=fx["vn_holidays_csv_path"],
        )
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


@pytest.mark.mysql
def test_run_wave_a_end_to_end_nbclient_that_tren_notebook_01_that(fixture_warehouse, tmp_path):
    """GPT review 12 eda M3: chay THAT `nbclient` tren CHINH source Notebook 01 (khong goi thang
    `wave_a.*` nhu cac test dry-run o tren), qua ham runner testable `run_wave_a.run_wave_a()`. Assert
    executed notebook + TOAN BO bang co trong artifact_manifest.json, va source notebook VAN
    output-free sau khi chay (nbclient chi thao tac ban doc trong bo nho, executed notebook ghi RIENG
    duoi executed_notebooks/, khong ghi nguoc lai source)."""
    fx = fixture_warehouse
    analysis_dir = run_wave_a.run_wave_a(
        pointer_path=fx["pointer_path"], ownership_manifest_path=fx["ownership_manifest_path"],
        cohort_history_path=fx["cohort_history_path"], cohort_history_base_dir=fx["cohort_history_path"].parent,
        vn_holidays_csv=fx["vn_holidays_csv_path"],
        warehouse_validation_report_path=fx["warehouse_validation_report_path"],
        source_manifest_path=fx["source_manifest_path"],
        outputs_dir=tmp_path / "outputs", timeout=120,
    )

    manifest_path = analysis_dir / "artifact_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    file_paths = {entry["path"] for entry in manifest["files"]}

    assert "executed_notebooks/01_warehouse_full_history_eda.ipynb" in file_paths
    for expected in ("input_manifest.json", "eda_summary.json", "EDA_REPORT.md", "DATA_DICTIONARY.md",
                     "quality_findings.csv", "dataset_readiness_by_horizon.csv",
                     "tables/ownership_by_source_status_reason.csv",
                     "tables/protocol_continuity_classified.csv",
                     "tables/run_duration_and_throughput.csv", "tables/active_hotel_by_crawl_date_source.csv"):
        assert expected in file_paths, f"thieu artifact {expected} trong manifest"

    source_notebook = run_wave_a.NOTEBOOKS_DIR / "01_warehouse_full_history_eda.ipynb"
    source_nb = json.loads(source_notebook.read_text(encoding="utf-8"))
    assert all(
        cell.get("execution_count") is None and cell.get("outputs", []) == []
        for cell in source_nb["cells"] if cell["cell_type"] == "code"
    ), "source notebook phai van output-free - executed copy khong duoc ghi nguoc lai source"

    # env override phai duoc don sach sau khi run_wave_a() tra ve - khong leak sang test/tien trinh khac.
    for name in run_wave_a._OPTIONAL_ENV_PARAMS:
        assert name not in os.environ, f"{name} bi leak ra ngoai run_wave_a()"


@pytest.mark.mysql
def test_run_wave_a_that_bai_qua_runner_khong_de_lai_manifest_pass(fixture_warehouse, tmp_path):
    """Runner phai `mark_failed()` + re-raise khi notebook that bai giua chung (ownership manifest
    path sai) - khong duoc de lai `artifact_manifest.json` trong nhu PASS (GPT review 12 M2/M3)."""
    fx = fixture_warehouse
    bad_ownership_path = tmp_path / "khong_ton_tai.json"
    with pytest.raises(Exception):
        run_wave_a.run_wave_a(
            pointer_path=fx["pointer_path"], ownership_manifest_path=bad_ownership_path,
            cohort_history_path=fx["cohort_history_path"], cohort_history_base_dir=fx["cohort_history_path"].parent,
            vn_holidays_csv=fx["vn_holidays_csv_path"],
            warehouse_validation_report_path=fx["warehouse_validation_report_path"],
            outputs_dir=tmp_path / "outputs", timeout=120,
        )

    outputs_dir = tmp_path / "outputs"
    analysis_dirs = [p for p in outputs_dir.iterdir() if p.is_dir()]
    assert len(analysis_dirs) == 1
    analysis_dir = analysis_dirs[0]
    assert (analysis_dir / "FAILED.json").exists()
    assert not (analysis_dir / "artifact_manifest.json").exists()
    assert not (analysis_dir / "artifact_manifest.json").exists()
