"""Orchestration Wave A (GPT review 12 eda M1/M2/M3/M6): thu thap TOAN BO du lieu trong DUNG 1 pham vi
`db.connect()`, roi tra ve 1 dict de cac buoc sau (bang/hinh/report) khong can mo lai connection.

Notebook 01 CHI goi vao module nay (+ hien thi/ve hinh) - khong tu viet SQL, khong tu quan ly vong doi
connection qua nhieu cell.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

import matplotlib
import mysql.connector
import nbclient
import nbformat
import numpy as np
import pandas as pd
import psutil
import pyarrow
import scipy
import seaborn

import artifacts
import contracts
import db
import holidays
import metrics
import protocol_schedule as ps
import queries

REPO_ROOT = Path(__file__).resolve().parents[3]
EDA_DIR = Path(__file__).resolve().parents[1]
HPI_DIR = REPO_ROOT / "hotel-price-intelligence"

VN_HOLIDAYS_CSV = HPI_DIR / "data" / "vn_holidays.csv"
# GPT review 12 eda M3: day la default cho snapshot HIEN HANH, KHONG phai hang so bat bien - override
# qua runner (`run_wave_a.py --ownership-manifest ...`) khi build batch moi, xem docstring
# `build_input_manifest()`. `WAREHOUSE_VALIDATION_REPORT_PATH` DA XOA - suy DUOC tu `batch_id` (contract
# that cua `app.warehouse.batch.build_warehouse()`), khong con can hardcode ngay snapshot dau tien.
OWNERSHIP_MANIFEST_PATH = HPI_DIR / "data" / "warehouse" / "ownership_manifest_20260916.json"
COHORT_HISTORY_PATH = HPI_DIR / "data" / "warehouse" / "cohort_history_20260916.json"
SOURCE_MANIFEST_PATH = HPI_DIR / "data" / "warehouse" / "source_manifest_20260916.json"
PLAN_AUTHORITY_PATH = HPI_DIR / "EDA_CURATED_PLAN.md"

# GPT review 12 (eda) M5: cac path source code duoc hash khi chua commit (muc M6) - tuong doi REPO_ROOT,
# dung dinh dang voi `app.warehouse.provenance.GUARDED_PATHS`. GPT review 12 M6: bo sot `run_wave_a.py`
# o ban truoc - day la runner that su chay production, phai nam trong guard nhu moi file src/notebook.
EDA_GUARDED_PATHS = (
    "hotel-price-intelligence/eda/src",
    "hotel-price-intelligence/eda/run_wave_a.py",
    "hotel-price-intelligence/eda/notebooks/01_warehouse_full_history_eda.ipynb",
    "hotel-price-intelligence/eda/notebooks/02_curated_ml_eda.ipynb",
    "hotel-price-intelligence/eda/requirements-eda.txt",
    "hotel-price-intelligence/EDA_CURATED_PLAN.md",
)


# ============================================================================== 1. THU THAP DU LIEU
def collect_wave_a_data(
    *, pointer_path: Path | None = None, ownership_manifest_path: Path = OWNERSHIP_MANIFEST_PATH,
    cohort_history_path: Path = COHORT_HISTORY_PATH, cohort_history_base_dir: Path = REPO_ROOT,
    vn_holidays_csv: Path = VN_HOLIDAYS_CSV,
) -> dict[str, Any]:
    """Chay TOAN BO truy van can DB trong DUNG 1 pham vi `db.connect()` (GPT review 12 eda M1).

    `pointer_path=None` -> dung `db.DEFAULT_POINTER_PATH` (warehouse current that). Cho phep override
    (cung 3 tham so con lai) de chay DRY-RUN tren fixture disposable, KHONG dung warehouse full - xem
    `src/tests/test_wave_a_dry_run.py` (GPT review 12 eda: "Dry-run/fixture execution tao du bo
    artifact nho de GPT kiem lifecycle; khong dung warehouse full").

    `data["pipeline_collect_seconds"]` (GPT review 12 eda M6: "ghi peak-memory/runtime vao summary
    de biet pipeline co on cho rebuild cuoi ky lon hon nhieu hay khong") - thoi luong CHINH pha thu
    thap nay (DB query + file I/O), doc lai o `write_eda_summary()`.
    """
    _started = time.monotonic()
    data: dict[str, Any] = {}
    connect_kwargs = {"pointer_path": pointer_path} if pointer_path is not None else {}
    with db.connect(**connect_kwargs) as (conn, snapshot):
        data["snapshot"] = snapshot

        # 7.1 preflight
        data["core_counts"] = queries.run_metric("preflight_core_counts", conn, snapshot)
        non_terminal = queries.run_metric("preflight_non_terminal_runs_items", conn, snapshot)
        data["non_terminal"] = non_terminal
        if int(non_terminal["non_terminal_runs"].iloc[0]) != 0:
            raise RuntimeError("co crawl_runs chua terminal - batch chua an toan de doc, dung Wave A.")
        if int(non_terminal["non_terminal_items"].iloc[0]) != 0:
            raise RuntimeError("co crawl_run_items chua terminal (queued/running) - dung Wave A.")

        # 7.2 source/ownership/protocol coverage
        data["ownership"] = queries.run_metric("ownership_by_source_status_reason", conn, snapshot)
        data["run_item_obs_by_date"] = queries.run_metric(
            "run_item_observation_by_source_crawl_date", conn, snapshot)

        # Collision / source divergence audit (GPT review 12 eda file 11 muc 3, plan 7.2/7.11) - RAW
        # audit thuan tuy, KHONG doi MAIN. So luong item-pair bi gioi han boi collision THAT SU xay ra
        # (2 nguon cung crawl dung 1 (vn_crawl_date, hotel_id, checkin_date) - hiem theo thiet ke
        # ownership, xem CLAUDE.md muc 4.10), nen an toan fetch option-level detail cho tap nho nay
        # ngay trong cung 1 connection, KHONG can chunk/aggregate SQL rieng.
        data["collision_item_pairs"] = queries.run_metric("collision_item_pairs", conn, snapshot)
        _collision_success_pairs = data["collision_item_pairs"][
            (data["collision_item_pairs"]["status_a"] == "success")
            & (data["collision_item_pairs"]["status_b"] == "success")
        ]
        data["collision_option_detail"] = queries.collision_option_level_detail(
            conn, snapshot, _collision_success_pairs)

        # 7.3 crawl operations/capacity
        data["run_duration"] = queries.run_metric("run_duration_and_throughput", conn, snapshot)

        # 7.4 hotel/check-in coverage
        data["active_hotel_by_date"] = queries.run_metric(
            "active_hotel_by_crawl_date_source", conn, snapshot)
        # GPT review 12 eda file 11 muc 5 (plan 7.4): them chieu city, so check-in date theo doi/ngay,
        # va du lieu nguon cho heatmap crawl_date x lead_time_bucket.
        data["active_hotel_by_date_city"] = queries.run_metric(
            "active_hotel_by_crawl_date_source_city", conn, snapshot)
        data["checkin_dates_tracked"] = queries.run_metric(
            "checkin_dates_tracked_by_crawl_date_source", conn, snapshot)
        data["crawl_date_lead_time_heatmap"] = queries.run_metric(
            "crawl_date_lead_time_bucket_heatmap", conn, snapshot)

        # 7.5 protocol continuity - phan DB truoc, phan file (ownership/cohort manifest) sau khi dong conn.
        # GPT review 12 eda M1: `source_run_dates`/`protocol_complete_through_date_by_source` KHONG con
        # dung de GIOI HAN truoc expected universe (do la bug cu lam mat han loai gap "missing_run") -
        # chi dung SAU de (a) lam ranh gioi tren cua window doc lap actual, va (b) phan loai 2 tang
        # missing_source_run vs missing_item_in_existing_run trong `classify_outcomes`.
        source_run_dates = queries.actual_crawl_dates_by_source(conn, snapshot)
        protocol_complete_through_date_by_source = queries.dump_taken_at_vn_date_by_source(conn, snapshot)
        protocol_actual = queries.run_metric("protocol_continuity_actual", conn, snapshot)

        # 7.7/7.8 gia + availability
        data["main_items"] = queries.run_metric("main_item_status", conn, snapshot)
        data["price_main"] = queries.run_metric("price_observations_main", conn, snapshot)
        # GPT review 12 eda file 11 muc 5 (plan 7.7: "tach it nhat RAW va MAIN"; plan 7.8: "not_bookable
        # rate theo crawl date va hotel").
        data["price_raw"] = queries.run_metric("price_observations_raw", conn, snapshot)
        data["not_bookable_by_crawl_date_hotel"] = metrics.not_bookable_rate_by_crawl_date_hotel(data["main_items"])

        # 7.9 missingness
        data["missingness"] = queries.run_metric("missingness_available_observations", conn, snapshot)
        # GPT review 12 eda file 11 muc 5 (plan 7.9): "missingness theo scraper/selector version/crawl
        # date/city" - 3 bien the them, cung field group voi ban theo source.
        data["missingness_by_selector_version"] = queries.run_metric("missingness_by_selector_version", conn, snapshot)
        data["missingness_by_crawl_date"] = queries.run_metric("missingness_by_crawl_date", conn, snapshot)
        data["missingness_by_city"] = queries.run_metric("missingness_by_city", conn, snapshot)

        # 7.10 reference - GPT review 12 eda file 11 muc 4 (MAJOR con lai sau ban truoc): khong con
        # nap ~1,36 trieu dong observation vao Python chi de GROUP BY trong pandas. 3 query nay gio tu
        # GROUP BY THANG trong SQL (`queries.reference_observation_match_main/raw`,
        # `reference_series_exists_observation_coverage_raw[_legacy_bucket]`) - moi lan doc ve toi da
        # ~7-8 dong (1/lead_time_bucket), khong phai observation-grain. `metrics.*_coverage()` chi con
        # tinh ty le tren ket qua NHO da co san.
        data["reference_exact_key_coverage_main"] = metrics.exact_approved_key_observation_coverage(
            queries.run_metric("reference_observation_match_main", conn, snapshot))
        data["reference_exact_key_coverage_raw"] = metrics.exact_approved_key_observation_coverage(
            queries.run_metric("reference_observation_match_raw", conn, snapshot))
        data["reference_series_exists_coverage_raw"] = metrics.series_with_approved_reference_coverage(
            queries.run_metric("reference_series_exists_observation_coverage_raw", conn, snapshot))
        data["reference_series_exists_coverage_raw_legacy_bucket"] = metrics.series_with_approved_reference_coverage(
            queries.run_metric("reference_series_exists_observation_coverage_raw_legacy_bucket", conn, snapshot))

        data["ref_item_level"] = queries.run_metric(
            "reference_item_level_availability_main", conn, snapshot)
        data["ref_approval_by_city_month"] = queries.run_metric(
            "reference_approval_by_city_month", conn, snapshot)

        # 7.11 quality scalars (+ MIN1: sample key THAT cho tung check, khong de sample_keys rong)
        data["quality_scalars"] = queries.run_all_scalar_metrics(conn, snapshot)
        data["quality_samples"] = queries.quality_violation_samples(conn, snapshot)

        # 7.12 readiness
        data["series_presence"] = queries.run_metric("series_history_length", conn, snapshot)

        # Wave B preflight
        data["wave_b_readiness"] = queries.run_metric("wave_b_dataset_version_readiness", conn, snapshot)

    # GPT review 12 eda file 11 muc 6.3: "report/manifest ghi ro protocol_complete_through_date va gia
    # dinh dump cua ngay do da bao phu xong lich crawl ngay do - khong goi chung la cutoff mo ho." Luu
    # LAI de `build_input_manifest()` publish ro rang, khong chi dung noi bo trong ham nay.
    data["protocol_complete_through_date_by_source"] = {
        source: date.isoformat() for source, date in protocol_complete_through_date_by_source.items()
    }

    # Tu day tro di KHONG can connection - file I/O thuan (ownership/cohort manifest, holiday CSV).
    expected = ps.expected_schedule(
        ownership_manifest_path, cohort_history_path, base_dir=cohort_history_base_dir,
        protocol_complete_through_date_by_source=protocol_complete_through_date_by_source,
    )

    # GPT review 12 eda M2: resolve hotel_id=NULL qua source_hotel_link TRUOC khi join - khong con
    # loai am tham/double-count voi bucket "unattributed" nhu thiet ke cu.
    resolved_actual = ps.resolve_effective_hotel_id(
        protocol_actual, cohort_history_path, base_dir=cohort_history_base_dir
    )
    data["protocol_unattributed_errors"] = ps.summarize_unattributed(resolved_actual)

    actual_for_join = resolved_actual[resolved_actual["hotel_id_resolution"] != "unattributed"].copy()
    actual_for_join["hotel_id"] = actual_for_join["effective_hotel_id"]
    actual_for_join = actual_for_join[["source_code", "crawl_date", "checkin_date", "hotel_id", "outcome"]]
    data["protocol_classified"] = ps.classify_outcomes(
        expected, actual_for_join, source_run_dates=source_run_dates
    )

    holiday_csv = holidays.load_holiday_csv(vn_holidays_csv)
    data["holiday_csv"] = holiday_csv
    data["checkin_calendar"] = holidays.checkin_calendar_flags(
        holiday_csv.events, data["price_main"]["checkin_date"].unique()
    )
    # GPT review 12 eda file 11 muc 5 (plan 7.7: "price theo weekday/weekend/holiday/Tet/festival") -
    # merge 1 LAN duy nhat o day (M4: 1 nguon su that), notebook/compute_wave_a_tables() doc lai
    # `data["price_main_cal"]`, khong tu merge rieng nua.
    before_cal = len(data["price_main"])
    data["price_main_cal"] = data["price_main"].merge(
        data["checkin_calendar"], on=["checkin_date", "city"], how="left"
    )
    if len(data["price_main_cal"]) != before_cal:
        raise RuntimeError(
            f"holiday join lam nhan/mat dong: {before_cal} -> {len(data['price_main_cal'])} - "
            f"checkin_calendar_flags() phai luon 1 dong/(checkin_date, city)."
        )

    # GPT review 12 eda file 11 muc 5 (plan 7.4: "cohort attrition theo version") - doc THANG JSON,
    # khong can loader backend (chi can cohort_version/effective_from_crawl_date/size, da co san
    # trong _cohort_workbook_versions() dung cho input_manifest - o day chi can ban tho de tinh attrition).
    data["cohort_history_versions"] = json.loads(Path(cohort_history_path).read_text(encoding="utf-8"))["versions"]

    data["pipeline_collect_seconds"] = round(time.monotonic() - _started, 1)
    return data


def _peak_memory_mb() -> float | None:
    """Peak RSS (MB) cua tien trinh Python hien tai (GPT review 12 eda M6). Tren Windows,
    `memory_info().peak_wset` la peak THAT do he dieu hanh theo doi tu luc process khoi dong (khong
    can tu sampling); cac OS khac (Linux/macOS) psutil khong lo mot truong peak rieng nen fallback ve
    RSS hien tai (KHONG phai peak that - gioi han da biet, van dung duoc lam tin hieu tho de so sanh
    giua cac lan chay). Goi cang MUON trong notebook cang phan anh dung peak toan pipeline (peak_wset
    khong bao gio giam)."""
    try:
        mem_info = psutil.Process().memory_info()
        peak_bytes = getattr(mem_info, "peak_wset", None) or mem_info.rss
        return round(peak_bytes / (1024 * 1024), 1)
    except Exception:
        return None


# ============================================================================== 2. PROVENANCE + INPUT MANIFEST (M6)
def _path_for_manifest(path: Path) -> str:
    """Duong dan tuong doi REPO_ROOT khi co the (de doc/audit gon), fallback str tuyet doi neu path
    nam NGOAI repo (vd fixture dry-run dung `tmp_path` cua pytest) - khong de `relative_to()` raise."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _git_head(repo_root: Path = REPO_ROOT) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return None


def _hash_paths_content(paths: list[Path]) -> str:
    """Hash NOI DUNG (khong phai metadata) cua danh sach file, sap xep DUONG DAN de xac dinh - dung khi
    code chua commit nen git dirty-state khong du de biet CHINH XAC noi dung la gi."""
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: str(p).replace("\\", "/")):
        digest.update(str(path.relative_to(REPO_ROOT)).replace("\\", "/").encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def capture_code_provenance() -> dict[str, Any]:
    """Git HEAD + danh sach file dang do trong pham vi guard `eda/`, cong hash NOI DUNG cua toan bo
    `eda/src` + 2 notebook source + `requirements-eda.txt` + `EDA_CURATED_PLAN.md` (GPT review 12 M6:
    "khi code chua commit, them hash cua toan bo eda/src, source notebook, requirements va plan
    authority"). Tai su dung `app.warehouse.provenance` cua backend cho phan git."""
    db._ensure_backend_importable()
    from app.warehouse.provenance import dirty_guarded_files

    dirty = dirty_guarded_files(repo_root=REPO_ROOT, paths=EDA_GUARDED_PATHS)
    src_files = sorted((EDA_DIR / "src").rglob("*.py"))
    src_files = [p for p in src_files if "__pycache__" not in p.parts and "tests" not in p.parts]
    notebook_files = sorted((EDA_DIR / "notebooks").glob("*.ipynb"))
    # GPT review 12 eda M6: "code content hash hien bo sot chinh eda/run_wave_a.py" - runner la code
    # production that su chay, phai nam trong hash nhu moi file khac.
    other_files = [EDA_DIR / "run_wave_a.py", EDA_DIR / "requirements-eda.txt", PLAN_AUTHORITY_PATH]
    content_hash = _hash_paths_content(src_files + notebook_files + other_files)
    return {
        "git_head": _git_head(),
        "dirty_guarded_files": dirty,
        "is_dirty": bool(dirty),
        "eda_src_and_notebooks_and_plan_content_sha256": content_hash,
        "plan_authority_sha256": hashlib.sha256(PLAN_AUTHORITY_PATH.read_bytes()).hexdigest(),
    }


def _library_versions() -> dict[str, str]:
    return {
        "python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__,
        "matplotlib": matplotlib.__version__, "seaborn": seaborn.__version__, "scipy": scipy.__version__,
        "pyarrow": pyarrow.__version__, "mysql_connector_python": mysql.connector.__version__,
        "nbclient": nbclient.__version__, "nbformat": nbformat.__version__,
    }


def _cohort_workbook_versions(cohort_history_path: Path, *, base_dir: Path) -> list[dict[str, Any]]:
    """GPT review 12 eda M6 + file 11 muc 6.2 ("raw workbook file SHA/size khong thay the computed
    membership hash - phai ghi CA declared LAN computed members hash/size, hoac validation status ro
    rang"): 1 dong / version voi 3 loai bang chung khac nhau:

    - `workbook_file_sha256`/`workbook_file_size_bytes`: hash BYTE THO cua file .xlsx tren dia (bang
      chung "dung dung file nay", khong phu thuoc logic parse);
    - `declared_members_sha256`/`declared_size`: gia tri DA GHI SAN trong `cohort_history_path` (ket
      qua parse tai thoi diem build warehouse);
    - `computed_members_sha256`/`computed_size`: PARSE LAI workbook NGAY BAY GIO qua dung loader that
      `app.warehouse.cohort_manifest.load_cohort_manifest()` (cung logic warehouse dung) - neu file
      tren dia bi doi sau khi build (vo tinh hay co y) ma khong cap nhat history, `validation_status`
      se la 'mismatch' thay vi im lang tin gia tri declared."""
    db._ensure_backend_importable()
    from app.warehouse.cohort_manifest import load_cohort_manifest

    history = json.loads(cohort_history_path.read_text(encoding="utf-8"))
    rows = []
    for version in history["versions"]:
        workbook_path = base_dir / version["workbook_path"]
        computed = load_cohort_manifest(workbook_path)
        declared_sha256, declared_size = version["members_sha256"], version["size"]
        matches = computed.manifest_sha256 == declared_sha256 and computed.size == declared_size
        rows.append({
            "cohort_version": version["cohort_version"],
            "effective_from_crawl_date": version["effective_from_crawl_date"],
            "workbook_path": _path_for_manifest(workbook_path),
            "workbook_file_sha256": hashlib.sha256(workbook_path.read_bytes()).hexdigest(),
            "workbook_file_size_bytes": workbook_path.stat().st_size,
            "declared_members_sha256": declared_sha256, "declared_size": declared_size,
            "computed_members_sha256": computed.manifest_sha256, "computed_size": computed.size,
            "validation_status": "match" if matches else "mismatch",
        })
    return rows


def build_input_manifest(
    data: dict[str, Any], *, notebook_source_path: Path,
    warehouse_validation_report_path: Path | None = None,
    ownership_manifest_path: Path = OWNERSHIP_MANIFEST_PATH, cohort_history_path: Path = COHORT_HISTORY_PATH,
    cohort_history_base_dir: Path = REPO_ROOT, source_manifest_path: Path = SOURCE_MANIFEST_PATH,
) -> dict[str, Any]:
    """Manifest DAY DU (GPT review 12 eda M6) - doi soat voi `<warehouse_validation_report_path>`
    that TREN NHIEU BANG (khong chi `price_observations` nhu ban truoc), khong chi assert non-terminal
    roi thoi. Cac path con lai override duoc cho dry-run fixture.

    `warehouse_validation_report_path=None` (GPT review 12 eda M3: "khong duoc hard-code snapshot dau
    tien trong logic reusable") -> suy DUOC dung tu `snapshot.batch_id` dang doc, KHONG hardcode ngay
    cua snapshot dau tien: `report_dir/{batch_id}.json` la contract that cua chinh
    `app.warehouse.batch.build_warehouse()` (`atomic_write_json(Path(inputs.report_dir)/f"{batch_id}.json", ...)`),
    nen suy tu batch_id LUON dung, khong phai doan ten file theo ngay."""
    snapshot: db.WarehouseSnapshot = data["snapshot"]
    if warehouse_validation_report_path is None:
        warehouse_validation_report_path = HPI_DIR / "data" / "warehouse" / "reports" / f"{snapshot.batch_id}.json"
    warehouse_report = json.loads(warehouse_validation_report_path.read_text(encoding="utf-8"))
    if warehouse_report.get("batch_id") != snapshot.batch_id:
        raise ValueError(
            f"warehouse validation report {warehouse_validation_report_path.name} la cua batch "
            f"{warehouse_report.get('batch_id')!r}, KHAC batch dang doc {snapshot.batch_id!r}."
        )
    if warehouse_report.get("status") != "pass":
        raise ValueError(
            f"warehouse validation report {warehouse_validation_report_path.name} co status="
            f"{warehouse_report.get('status')!r}, KHONG PHAI 'pass' - warehouse nay chua an toan de "
            f"dung lam input EDA (GPT review 12 eda M6: 'doi soat ... va report status PASS')."
        )

    # GPT review 12 eda M6: doi soat It NHAT hotels/runs/items/curated/reference (ban truoc CHI kiem
    # price_observations; `report_counts['hotels']` tung duoc TINH nhung khong bao gio kiem). Gom TAT
    # CA mismatch vao 1 loi duy nhat thay vi fail-fast tung cai - de review 1 lan thay het cho lech.
    core_counts = data["core_counts"].iloc[0].to_dict()
    import_step = warehouse_report["steps"]["11_import"]
    curated_step = warehouse_report["steps"]["14_curated"]
    report_counts = {
        "hotels": import_step["hotels"]["warehouse_distinct_hotels"],
        "crawl_runs": sum(s["crawl_runs_imported"] for s in import_step["per_source"].values()),
        "crawl_run_items": sum(s["crawl_run_items_imported"] for s in import_step["per_source"].values()),
        "price_observations": curated_step["price_observations"],
        "curated_observation_keys": curated_step["curated_observation_keys"],
    }
    mismatches = {
        field: {"core_counts": int(core_counts[field]), "validation_report": int(report_counts[field])}
        for field in report_counts if int(core_counts[field]) != int(report_counts[field])
    }

    references_step = warehouse_report["steps"]["15_references"]
    ref_by_city_month = data["ref_approval_by_city_month"]
    reference_counts = {
        "series_with_reference": int(ref_by_city_month["n"].sum()),
        "approved": int(ref_by_city_month["approved"].sum()),
    }
    mismatches.update({
        f"reference_{field}": {"eda": reference_counts[field], "validation_report": references_step[field]}
        for field in reference_counts if reference_counts[field] != references_step[field]
    })
    if mismatches:
        raise ValueError(
            f"preflight counts KHONG khop validation report (warehouse co the da bi doi sau build): {mismatches}"
        )

    # GPT review 12 eda M6: pin source-manifest hash - doi soat voi `snapshot.source_manifest_sha256`
    # (da tu XAC NHAN LAI boi `db._verify_snapshot()` luc mo connection, tinh tu CHINH
    # `etl_import_sources` trong DB - manh hon ca doi voi validation report tinh, xem
    # `app.warehouse.registry.verify_source_manifest`). LUU Y: day KHONG phai hash byte tho cua file -
    # `source_manifest_sha256()` la hash CANONICAL tren cac field da parse (source_code/priority/
    # dump_sha256/dump_taken_at/schema_sha256/source_version_json cua tung nguon), nen phai load qua
    # dung loader (`app.warehouse.source_manifest.load_source_manifest`) roi lay `.manifest_sha256`,
    # KHONG hash suong byte file tren dia (2 gia tri nay LUON khac nhau - da tu bat qua test that).
    db._ensure_backend_importable()
    from app.warehouse.source_manifest import load_source_manifest

    loaded_source_manifest = load_source_manifest(source_manifest_path)
    source_manifest_canonical_sha256 = loaded_source_manifest.manifest_sha256
    if source_manifest_canonical_sha256 != snapshot.source_manifest_sha256:
        raise ValueError(
            f"source_manifest_path={source_manifest_path} co manifest_sha256="
            f"{source_manifest_canonical_sha256} KHONG khop snapshot.source_manifest_sha256 "
            f"({snapshot.source_manifest_sha256}) - dang doc SAI file source manifest cua batch nay."
        )

    price_main = data["price_main"]
    cohort_workbook_versions = _cohort_workbook_versions(cohort_history_path, base_dir=cohort_history_base_dir)
    cohort_mismatches = [v for v in cohort_workbook_versions if v["validation_status"] != "match"]
    if cohort_mismatches:
        raise ValueError(
            f"cohort workbook computed members hash/size KHONG khop declared trong "
            f"{cohort_history_path.name} (file tren dia co the da doi sau khi build warehouse, hoac "
            f"history JSON da cu): {cohort_mismatches}"
        )

    provenance = capture_code_provenance()
    return {
        **snapshot.to_manifest_dict(),
        "warehouse_validation_report_path": _path_for_manifest(warehouse_validation_report_path),
        "warehouse_validation_report_sha256": hashlib.sha256(
            warehouse_validation_report_path.read_bytes()).hexdigest(),
        "warehouse_validation_report_status": warehouse_report["status"],
        "reconciled_counts": report_counts,
        "reconciled_reference_counts": reference_counts,
        # GPT review 12 eda file 11 muc 6.3: cutoff KHONG con la khai niem mo ho - `protocol_complete_
        # through_date_by_source` la ngay VN cuoi cung ma dump cua nguon do DUOC GIA DINH da bao phu
        # xong lich crawl protocol (tu `dump_taken_at`); moi crawl_date sau ngay nay trong ownership
        # manifest CHUA duoc coi la "missing" - chi la chua toi luc dump chup lai.
        "protocol_complete_through_date_by_source": data["protocol_complete_through_date_by_source"],
        "protocol_complete_through_date_assumption": (
            "Dump cua nguon tai dump_taken_at duoc gia dinh da ghi nhan DAY DU moi run/item hoan tat "
            "TRUOC hoac TRONG ngay VN do - ngay sau protocol_complete_through_date KHONG duoc phan loai "
            "missing_source_run/missing_item_in_existing_run vi chua toi luc du lieu duoc chup."
        ),
        "source_manifest_path": _path_for_manifest(source_manifest_path),
        "source_manifest_file_sha256": hashlib.sha256(source_manifest_path.read_bytes()).hexdigest(),
        "source_manifest_canonical_sha256": source_manifest_canonical_sha256,
        "ownership_manifest_path": _path_for_manifest(ownership_manifest_path),
        "ownership_manifest_file_sha256": hashlib.sha256(ownership_manifest_path.read_bytes()).hexdigest(),
        "cohort_history_path": _path_for_manifest(cohort_history_path),
        "cohort_history_file_sha256": hashlib.sha256(cohort_history_path.read_bytes()).hexdigest(),
        "cohort_workbook_versions": cohort_workbook_versions,
        "vn_holidays_csv_path": _path_for_manifest(data["holiday_csv"].path),
        "vn_holidays_csv_sha256": data["holiday_csv"].sha256,
        "observed_date_min": str(price_main["vn_observation_date"].min()),
        "observed_date_max": str(price_main["vn_observation_date"].max()),
        "checkin_date_min": str(price_main["checkin_date"].min()),
        "checkin_date_max": str(price_main["checkin_date"].max()),
        "query_catalog_version": queries.CATALOG_VERSION,
        "query_catalog_metric_ids": sorted(queries.CATALOG),
        "timezone_convention": "UTC -> Asia/Ho_Chi_Minh bang offset co dinh +07:00 (khong DST)",
        "notebook_source_path": _path_for_manifest(notebook_source_path),
        "notebook_source_sha256": hashlib.sha256(notebook_source_path.read_bytes()).hexdigest(),
        "library_versions": _library_versions(),
        "code_provenance": provenance,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    }


# ============================================================================== 3. QUALITY FINDINGS (MIN3)
def build_quality_findings(data: dict[str, Any]) -> "pd.DataFrame":
    """1 dong / check, DU severity/scope/grain/count/denominator/rate/sample_keys/likely_cause/
    recommended_action (GPT review 12 eda MIN3) - ke ca check co count=0 van co 1 dong (chung minh da
    chay, khong phai "im lang vi khong co gi de bao"). MIN1: `sample_keys` la sample THAT (tu
    `queries.quality_violation_samples`, gioi han N) khi count>0, khong con `[]` gia."""
    scalars = data["quality_scalars"]
    samples = data["quality_samples"]
    rows: list[dict[str, Any]] = []

    def add(check_id, *, severity, scope, grain, count, denominator, sample_keys, likely_cause, action):
        rate = (count / denominator) if denominator else None
        rows.append({
            "check_id": check_id, "severity": severity, "scope": scope, "grain": grain,
            "count": int(count), "denominator": int(denominator) if denominator is not None else None,
            "rate": rate, "sample_keys": json.dumps(sample_keys, ensure_ascii=False, default=str),
            "likely_cause": likely_cause, "recommended_action": action,
        })

    def _samples(check_id: str, count: int) -> list:
        # MIN1: chi tra sample khi count>0 - count=0 thi sample ruong tu queries la DUNG (khong co gi
        # de sample), khong phai "quen lay".
        return samples.get(check_id, []) if count else []

    p = scalars["quality_price_non_positive"].iloc[0]
    add("price_non_positive", severity="high" if p["n_violations"] else "info", scope="RAW", grain="observation",
       count=p["n_violations"], denominator=p["n_total"],
       sample_keys=_samples("price_non_positive", p["n_violations"]),
       likely_cause="Parser ghi gia <=0 tren observation khong sold-out (khong duoc phep - muc 4.5 CLAUDE.md).",
       action="Neu >0: dieu tra record_id cu the, kiem tra selector/parser tai thoi diem crawl.")

    c = scalars["quality_checkout_not_after_checkin"].iloc[0]
    add("checkout_not_after_checkin", severity="high" if c["n_violations"] else "info", scope="RAW",
       grain="observation", count=c["n_violations"], denominator=int(data["core_counts"]["price_observations"].iloc[0]),
       sample_keys=_samples("checkout_not_after_checkin", c["n_violations"]),
       likely_cause="checkout_date phai luon = checkin_date + 1 dem (DATEDIFF<>1 - MIN3: sua tu <= sang dung nghiep vu).",
       action="Neu >0: kiem tra logic tinh checkout_date luc crawl/import.")

    s = scalars["quality_success_item_without_observation"].iloc[0]
    add("success_item_without_observation", severity="high" if s["n_violations"] else "info", scope="MAIN",
       grain="item", count=s["n_violations"], denominator=s["n_total"],
       sample_keys=_samples("success_item_without_observation", s["n_violations"]),
       likely_cause="Item status=success nhung khong co price_observation nao - vi pham invariant import.",
       action="Neu >0: kiem tra importer._import_observations cho dung item_id nay.")

    k = scalars["quality_canonical_key_anomalies"].iloc[0]
    k_count = int(k["n_nonsoldout_missing_canonical"]) + int(k["n_soldout_with_room_key"])
    add("canonical_key_anomalies", severity="high" if k_count else "info",
       scope="RAW", grain="observation", count=k_count, denominator=k["n_total"],
       sample_keys=_samples("canonical_key_anomalies", k_count),
       likely_cause="P-A: non-sold-out phai co canonical key, sold-out phai la EMPTY sentinel (muc canonicalize).",
       action="Neu >0: kiem tra curated.build_curated_keys cho batch nay.")

    city = scalars["quality_city_outside_scope"].iloc[0]
    add("city_outside_scope", severity="medium" if city["n_violations"] else "info", scope="RAW", grain="hotel",
       count=city["n_violations"], denominator=city["n_total"],
       sample_keys=_samples("city_outside_scope", city["n_violations"]),
       likely_cause="hotels.city ngoai 5 thanh pho scope (CLAUDE.md muc 2) - co the do merge_policy hoac cohort sai.",
       action="Neu >0: liet ke hotel_id, doi chieu cohort manifest.")

    n = scalars["quality_unexpected_nulls_by_field_group"].iloc[0]
    # MIN1: day la aggregate rollup (tong tren 24 dong (source,field) cua missingness_available_
    # observations.csv), KHONG co sample record rieng le tu nhien - ghi LY DO explicit thay vi [].
    add("unexpected_nulls_available_observations",
       severity="medium" if n["n_unexpected_null_field_value_cells"] else "info",
       # MIN2: doi ten cot/grain sang "field_value_cell" (source,field,observation), khong con la
       # "observation" - 1 observation co the dong gop nhieu cell NULL cung luc.
       scope="MAIN", grain="field_value_cell",
       count=n["n_unexpected_null_field_value_cells"], denominator=n["n_total_field_value_cells"],
       sample_keys=(
           ["aggregate rollup - xem missingness_available_observations.csv theo (source, field) de biet vi tri cu the"]
           if n["n_unexpected_null_field_value_cells"] else []
       ),
       likely_cause="NULL tren field khong sold-out (structural missing da loai qua WHERE is_sold_out=0).",
       action="Xem missingness_available_observations.csv de biet field/nguon cu the.")

    # GPT review 12 eda file 11 muc 2/5 (plan 7.11/7.8/7.7): 5 check con thieu tu vong review truoc.
    lt = scalars["quality_lead_time_mismatch"].iloc[0]
    add("lead_time_mismatch", severity="medium" if lt["n_mismatch"] else "info", scope="RAW", grain="observation",
       count=lt["n_mismatch"], denominator=lt["n_total"], sample_keys=_samples("lead_time_mismatch", lt["n_mismatch"]),
       likely_cause="lead_time luu san (luc insert) khac DATEDIFF(checkin_date, ngay VN cua observed_at) tinh lai.",
       action="Neu >0: kiem tra logic tinh lead_time luc insert (co the do offset/DST hoac gio he thong sai).")

    dup = scalars["quality_duplicate_daily_series"].iloc[0]
    add("duplicate_daily_series", severity="high" if dup["n_duplicate_groups"] else "info",
       scope="RAW", grain="item x canonical key", count=dup["n_duplicate_groups"], denominator=dup["n_total_groups"],
       sample_keys=_samples("duplicate_daily_series", dup["n_duplicate_groups"]),
       likely_cause="Cung 1 item co >1 observation trung canonical (room,rate) key - 2 raw option khac nhau "
                    "canonical hoa ve cung 1 key.",
       action="Neu >0: kiem tra logic canonicalize/dedup cho item nay, co the can them tieu chi phan biet.")

    pm = scalars["quality_parent_mismatch"].iloc[0]
    add("parent_mismatch", severity="high" if pm["n_mismatch"] else "info", scope="RAW", grain="observation",
       count=pm["n_mismatch"], denominator=pm["n_total"], sample_keys=_samples("parent_mismatch", pm["n_mismatch"]),
       likely_cause="Observation co hotel_id/checkin_date KHAC voi item cha (crawl_run_items) - vi pham invariant import.",
       action="Neu >0: kiem tra importer._import_observations, co the ghi nham observation vao item khac.")

    sentinel = scalars["quality_sold_out_sentinel_consistency"].iloc[0]
    add("sold_out_sentinel_consistency", severity="high" if sentinel["n_violations"] else "info",
       scope="MAIN", grain="item", count=sentinel["n_violations"], denominator=sentinel["n_total"],
       sample_keys=_samples("sold_out_sentinel_consistency", sentinel["n_violations"]),
       likely_cause="Item status=sold_out nhung KHONG co dung 1 observation sentinel (is_sold_out=1, canonical "
                    "room key=EMPTY_ROOM_KEY) - co the thieu, thua, hoac sai key.",
       action="Neu >0: kiem tra logic ghi sentinel sold-out luc import/curated cho item nay.")

    tpn = scalars["quality_price_total_per_night_inconsistent"].iloc[0]
    add("price_total_per_night_inconsistent", severity="medium" if tpn["n_violations"] else "info",
       scope="RAW", grain="observation", count=tpn["n_violations"], denominator=tpn["n_total"],
       sample_keys=_samples("price_total_per_night_inconsistent", tpn["n_violations"]),
       likely_cause="price_total phai LUON = price_per_night (CLAUDE.md muc 4.3: chi cao 1 dem/toi da 1 dem).",
       action="Neu >0: kiem tra parser co vo tinh tinh price_total theo so dem khac 1 hay khong.")

    unattributed_df = data["protocol_unattributed_errors"]
    unattributed_total = int(unattributed_df["n_unattributed_errors"].sum()) if len(unattributed_df) else 0
    unattributed_samples: list = []
    for raw in unattributed_df.get("sample_keys", []):
        unattributed_samples.extend(json.loads(raw))
    add("protocol_continuity_unattributed_errors", severity="info", scope="MAIN", grain="item",
       count=unattributed_total, denominator=int(data["main_items"].shape[0]),
       sample_keys=unattributed_samples[:10],
       likely_cause="Item 'error' voi hotel_id=NULL VA source_hotel_link khong resolve duoc qua extract_hotel_slug "
                    "(dead link/CAPTCHA thuc su truoc khi Booking tra property page) - GPT review 12 M2: KHAC voi "
                    "item hotel_id=NULL nhung URL van resolve duoc (nhung da duoc gan lai vao protocol_classified).",
       action="Khong can hanh dong tru khi count bat thuong tang dot bien - xem sample_keys (source_link_hash).")

    return pd.DataFrame(rows)


# ============================================================================== 4. TINH + GHI BANG (fail-if-exists, manifest cuoi cung)
# GPT review 12 eda M4: "Notebook/runner phai goi MOT artifact writer duy nhat de khong co hai danh
# sach output lech nhau" - truoc day nhieu bang duoc tinh/ghi CSV RIENG BIET ngay trong notebook, con
# `write_wave_a_tables()` lai tinh/ghi lai MOT TAP KHAC (7/23 bang) - 2 danh sach de sot/trung lap khi
# 1 ben sua ma quen ben kia (vd notebook tung tu ghi `missingness_available_observations.csv` VA
# `write_wave_a_tables()` cung ghi lai chinh no). Gio CHI 1 nguon: `compute_wave_a_tables()` tinh HET,
# notebook chi `display()` tu dict tra ve, `write_wave_a_tables()` la noi DUY NHAT ghi CSV.
_ROOT_LEVEL_TABLES = frozenset({"quality_findings", "dataset_readiness_by_horizon"})


def compute_wave_a_tables(data: dict[str, Any]) -> dict[str, "pd.DataFrame"]:
    """Tinh TOAN BO bang Wave A tu `data` (dau ra `collect_wave_a_data`) o MOT noi duy nhat.

    GPT review 12 eda M6: 3 bang `reference_exact_key_coverage_main/raw` +
    `reference_series_exists_coverage_raw` DA duoc aggregate NGAY trong `collect_wave_a_data()` (ban
    raw ~1,36 trieu dong/ban da bi xoa sau khi aggregate, KHONG con trong `data`) - o day chi doc lai,
    khong tinh lai.

    MUTATE `data["price_main"]`, `data["price_raw"]`, `data["price_main_cal"]`, `data["ref_item_level"]`,
    `data["main_items"]` bang cach them cot `lead_time_bucket`/`is_weekend`/outlier flags (cell notebook
    khac doc lai cung `data[...]` van thay cot nay, giu dung hanh vi cu)."""
    price_main = data["price_main"]
    price_main["lead_time_bucket"] = metrics.lead_time_bucket_series(price_main["lead_time"])
    price_raw = data["price_raw"]
    price_raw["lead_time_bucket"] = metrics.lead_time_bucket_series(price_raw["lead_time"])

    # GPT review 12 eda file 11 muc 5 (plan 7.7: "price theo weekday/weekend/holiday/Tet/festival").
    price_main_cal = data["price_main_cal"]
    price_main_cal["is_weekend"] = pd.to_datetime(price_main_cal["checkin_date"]).dt.dayofweek >= 5

    # GPT review 12 eda file 11 muc 5 (plan 7.11/7.7: "outlier price theo robust within-hotel rule, chi
    # flag chu khong xoa"). Flag NGAY tren `price_main` da giu san (khong tao ban sao ~1,27tr dong moi).
    price_main_flagged = metrics.robust_price_outliers(price_main)
    price_main["is_price_outlier"] = price_main_flagged["is_price_outlier"]
    price_main["price_robust_z"] = price_main_flagged["price_robust_z"]
    price_main["hotel_median_price"] = price_main_flagged["hotel_median_price"]
    del price_main_flagged

    item_level = data["ref_item_level"]
    item_level["lead_time_bucket"] = metrics.lead_time_bucket_series(item_level["lead_time"].fillna(0).astype(int))

    main_items = data["main_items"]
    main_items["lead_time_bucket"] = metrics.lead_time_bucket_series(main_items["lead_time"])
    presence = data["series_presence"]
    pairs = metrics.theoretical_horizon_pairs(presence)

    return {
        "ownership_by_source_status_reason": data["ownership"],
        "run_item_observation_by_source_crawl_date": data["run_item_obs_by_date"],
        "run_duration_and_throughput": data["run_duration"],
        "active_hotel_by_crawl_date_source": data["active_hotel_by_date"],
        "protocol_continuity_classified": data["protocol_classified"],
        "protocol_continuity_unattributed_errors": data["protocol_unattributed_errors"],
        "vn_holidays_events_audit": data["holiday_csv"].events,
        "lead_time_bucket_distribution": (
            price_main["lead_time_bucket"].value_counts()
            .reindex(metrics.LEAD_TIME_BUCKET_ORDER, fill_value=0)
            .rename_axis("lead_time_bucket").reset_index(name="n_observations")
        ),
        "checkin_weekday_distribution": (
            pd.to_datetime(price_main["checkin_date"]).dt.day_name().value_counts()
            .rename_axis("weekday").reset_index(name="n_observations")
        ),
        "checkin_month_distribution": (
            pd.to_datetime(price_main["checkin_date"]).dt.to_period("M").value_counts().sort_index()
            .rename_axis("checkin_month").reset_index(name="n_observations")
            .assign(checkin_month=lambda d: d["checkin_month"].astype(str))
        ),
        "price_distribution_overall_main": metrics.price_distribution_stats(price_main),
        "price_distribution_by_city_main": metrics.price_distribution_stats(price_main, group_cols=("city",)),
        "price_sensitivity_by_series_median": metrics.price_sensitivity_by_series(price_main, agg="median"),
        "item_availability_overall": metrics.item_availability_rates(main_items),
        "item_availability_by_city": metrics.item_availability_rates(main_items, group_cols=("city",)),
        "item_availability_by_checkin_month": metrics.item_availability_rates(
            main_items.assign(
                checkin_month=pd.to_datetime(main_items["checkin_date"]).dt.to_period("M").astype(str)
            ),
            group_cols=("checkin_month",),
        ),
        "missingness_available_observations": data["missingness"],
        "reference_exact_key_coverage_main": data["reference_exact_key_coverage_main"],
        "reference_exact_key_coverage_raw": data["reference_exact_key_coverage_raw"],
        "reference_series_exists_coverage_raw": data["reference_series_exists_coverage_raw"],
        # GPT review 12 eda file 11 muc 6.1: bang bucket LEGACY (0-3 gop) doi chieu TRUC TIEP dinh
        # dang bang lich su CLAUDE.md muc 7.2 (30,4/27,1/18,3/11,2/7,6/7,1).
        "reference_series_exists_coverage_raw_legacy_bucket": data["reference_series_exists_coverage_raw_legacy_bucket"],
        "reference_item_level_availability_by_lead_time": metrics.item_level_exact_reference_availability(
            item_level, group_cols=("lead_time_bucket",)
        ),
        "reference_approval_by_city_month": data["ref_approval_by_city_month"],
        "canonical_series_turnover": metrics.canonical_series_turnover(presence),
        "dataset_readiness_by_horizon": metrics.dataset_readiness_by_horizon(pairs),
        "quality_findings": build_quality_findings(data),

        # ============================================================== GPT review 12 eda file 11 (M5) - cac bang con thieu
        # 7.4 hotel/check-in coverage
        "active_hotel_by_crawl_date_source_city": data["active_hotel_by_date_city"],
        "checkin_dates_tracked_by_crawl_date_source": data["checkin_dates_tracked"],
        "crawl_date_lead_time_bucket_heatmap": data["crawl_date_lead_time_heatmap"],
        "cohort_attrition_by_version": metrics.cohort_attrition_table(data["cohort_history_versions"]),

        # 7.3 crawl operations - finish hour + anomaly flag (khong loc run nao)
        "finish_hour_distribution": metrics.finish_hour_distribution(data["run_duration"]),
        "run_duration_anomaly_flagged": metrics.anomalous_crawl_days(data["run_duration"]),

        # 7.6 lead-time distribution theo city/source (dem, KHAC voi 7.7 la GIA theo city/source)
        "lead_time_bucket_distribution_by_city_source": (
            price_main.groupby(["city", "source_code", "lead_time_bucket"], observed=True)
            .size().reset_index(name="n_observations")
        ),

        # 7.7 price - RAW/MAIN split, theo lead-time bucket, theo calendar, hotel dispersion
        "price_distribution_overall_raw": metrics.price_distribution_stats(price_raw),
        "price_distribution_by_lead_time_bucket_main": metrics.price_distribution_stats(
            price_main, group_cols=("lead_time_bucket",)
        ),
        "price_distribution_by_weekday_weekend_main": metrics.price_distribution_stats(
            price_main_cal.assign(weekday=lambda d: pd.to_datetime(d["checkin_date"]).dt.day_name()),
            group_cols=("is_weekend", "weekday"),
        ),
        "price_distribution_by_holiday_tet_festival_main": metrics.price_distribution_stats(
            price_main_cal, group_cols=("is_public_holiday", "is_tet", "is_festival_period", "is_major_event"),
        ),
        "hotel_price_dispersion": metrics.hotel_price_dispersion(price_main),
        "price_outlier_sample": price_main[price_main["is_price_outlier"]].head(200)[
            ["record_id", "hotel_id", "checkin_date", "vn_observation_date", "price_per_night",
             "hotel_median_price", "price_robust_z"]
        ],

        # 7.8 availability - lead-time, not_bookable theo crawl_date+hotel
        "item_availability_by_lead_time": metrics.item_availability_rates(main_items, group_cols=("lead_time_bucket",)),
        "not_bookable_rate_by_crawl_date_hotel": data["not_bookable_by_crawl_date_hotel"],

        # 7.9 missingness them chieu
        "missingness_by_selector_version": data["missingness_by_selector_version"],
        "missingness_by_crawl_date": data["missingness_by_crawl_date"],
        "missingness_by_city": data["missingness_by_city"],

        # Collision / source divergence (GPT review 12 eda file 11 muc 3) - denominator RIENG cho tung
        # bang (muc 3.2 cuoi: "khong tron lan status concordance voi canonical-option overlap"):
        # collision_item_pairs (item-pair), collision_item_status_concordance (item-pair, cung mau so),
        # collision_item_time_diff_stratification (item-pair), collision_option_detail +
        # collision_option_time_diff_stratification (shared canonical option-pair - mau so KHAC han).
        "collision_item_pairs": data["collision_item_pairs"],
        "collision_item_status_concordance": metrics.collision_item_status_concordance(data["collision_item_pairs"]),
        "collision_item_time_diff_stratification": metrics.collision_item_time_diff_stratification(
            data["collision_item_pairs"]
        ),
        "collision_option_detail": data["collision_option_detail"],
        "collision_option_time_diff_stratification": metrics.collision_option_time_diff_stratification(
            data["collision_option_detail"]
        ),
    }


def write_wave_a_tables(tables: dict[str, "pd.DataFrame"], analysis_dir: Path) -> None:
    """Ghi TOAN BO bang tu `compute_wave_a_tables()` ra CSV - NOI DUY NHAT ghi bang cua Wave A. 2 bang
    (`quality_findings`, `dataset_readiness_by_horizon`) nam o ROOT `analysis_dir` (khop quy uoc cu),
    con lai duoi `tables/`."""
    tables_dir = analysis_dir / "tables"
    for name, table in tables.items():
        target_dir = analysis_dir if name in _ROOT_LEVEL_TABLES else tables_dir
        table.to_csv(target_dir / f"{name}.csv", index=False)


def write_eda_summary(data: dict[str, Any], analysis_dir: Path) -> None:
    snapshot: db.WarehouseSnapshot = data["snapshot"]
    protocol_summary = metrics.protocol_continuity(data["protocol_classified"], group_cols=("owner_source",))
    availability = metrics.item_availability_rates(data["main_items"])
    summary = {
        "database": snapshot.database, "batch_id": snapshot.batch_id,
        "core_counts": data["core_counts"].iloc[0].to_dict(),
        "item_availability_overall": availability.iloc[0].to_dict(),
        "protocol_continuity_by_source": protocol_summary.to_dict("records"),
        "reference_approved_series": int((data["ref_approval_by_city_month"]["approved"]).sum()),
        "wave_b_ready": bool(data["wave_b_readiness"]["ready"].any()) if len(data["wave_b_readiness"]) else False,
        # GPT review 12 eda M6: "ghi peak-memory/runtime vao summary de biet pipeline co on cho
        # rebuild cuoi ky lon hon nhieu hay khong". `peak_memory_mb` lay o DAY (cuoi notebook) de
        # peak_wset (Windows, khong giam) phan anh GAN NHU toan bo pipeline, khong chi rieng collect.
        "pipeline_collect_seconds": data.get("pipeline_collect_seconds"),
        "peak_memory_mb": _peak_memory_mb(),
    }
    artifacts.atomic_write_json(analysis_dir / "eda_summary.json", summary)


def _format_cell(value: Any) -> str:
    """1 gia tri -> chuoi doc duoc trong bang Markdown. Float nguyen (vd 500000.0 tu SUM/COUNT)
    hien thi co dau phay ngan cach, KHONG duoc de pandas/`%g` tu doi sang ky hieu khoa hoc (5e+05) -
    xau cho mot bao cao ve GIA TIEN. NaN/NaT hien thi rong thay vi chuoi "nan" gay nham lan."""
    if isinstance(value, float) and pd.isna(value):
        return ""
    if value is pd.NA or value is None:
        return ""
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return f"{int(value):,}"
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def _md_table(df: "pd.DataFrame", *, max_rows: int = 15) -> str:
    """Format 1 DataFrame nho thanh bang Markdown GFM, tu viet (khong them dependency `tabulate` chi
    cho 1 viec don gian - pandas `to_markdown()` doi hoi no)."""
    if df is None or len(df) == 0:
        return "_(khong co dong nao)_"
    view = df.head(max_rows)
    header = "| " + " | ".join(str(c) for c in view.columns) + " |"
    sep = "| " + " | ".join("---" for _ in view.columns) + " |"
    lines = [header, sep]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(_format_cell(v) for v in row) + " |")
    if len(df) > max_rows:
        lines.append(f"\n_(hien {max_rows}/{len(df)} dong - xem CSV day du de biet chi tiet)_")
    return "\n".join(lines)


def _report_sections(data: dict[str, Any], tables: dict[str, "pd.DataFrame"]) -> list[str]:
    """1 phan tu / section 7.1-7.12 + Wave B - MOI con so la TINH TRUC TIEP tu `data`/`tables` cua
    CHINH lan chay nay (GPT review 12 eda M4: "render count/rate/table/figure path THAT, phan biet
    fact/inference/caveat"). Phan "Dien giai" chi la quan sat CO CHE/CAU TRUC an toan gan truc tiep voi
    con so (vd "coverage giam theo lead-time" - dung logic turnover phong/rate), KHONG phai ket luan
    nghien cuu cuoi cung - do la viec cua vong GPT review ket qua that theo dung quy trinh da dinh."""
    sections: list[str] = []

    core = data["core_counts"].iloc[0]
    sections.append(
        "## 7.1 Preflight / snapshot identity\n\n"
        f"**Fact.** `hotels`={core['hotels']}, `crawl_runs`={core['crawl_runs']}, "
        f"`crawl_run_items`={core['crawl_run_items']}, `price_observations`={core['price_observations']}, "
        f"`curated_observation_keys`={core['curated_observation_keys']}. Non-terminal runs/items = "
        f"{int(data['non_terminal']['non_terminal_runs'].iloc[0])}/{int(data['non_terminal']['non_terminal_items'].iloc[0])} "
        "(PHAI = 0, da assert truoc khi thu thap tiep).\n\n"
        "**Caveat.** Cac con so nay da duoc doi soat 1:1 voi warehouse validation report trong "
        "`input_manifest.json` (`reconciled_counts`) - lech se lam pipeline FAIL truoc khi toi day."
    )

    sections.append(
        "## 7.2 Source, ownership va protocol coverage\n\n"
        f"**Fact.** Phan bo item theo (nguon, ownership_status, exclusion_reason) - xem "
        f"`tables/ownership_by_source_status_reason.csv` ({len(data['ownership'])} dong). "
        f"Run/item/observation theo (nguon, ngay crawl VN) - "
        f"`tables/run_item_observation_by_source_crawl_date.csv` ({len(data['run_item_obs_by_date'])} dong).\n\n"
        f"{_md_table(data['ownership'])}"
    )

    dur = data["run_duration"]
    sections.append(
        "## 7.3 Crawl operations va capacity\n\n"
        f"**Fact.** {len(dur)} run (terminal, co `finished_at`). Thoi luong trung vi theo nguon "
        f"(phut):\n\n{_md_table(dur.groupby('source_code')['duration_minutes'].median().reset_index())}\n\n"
        f"So run keo qua ngay crawl ke tiep: {int(dur['crosses_next_crawl_day'].sum())}/{len(dur)}. "
        "Hinh: `figures/run_duration_by_source.png`.\n\n"
        "**Dien giai.** Run keo qua ngay ke tiep lam giam buffer truoc lich 00:30 hom sau (CLAUDE.md "
        "muc 4.8) - can theo doi neu ty le nay tang."
    )

    active = data["active_hotel_by_date"]
    sections.append(
        "## 7.4 Hotel va check-in coverage\n\n"
        f"**Fact.** So hotel active (>=1 item da OWN trong ngay) theo nguon:\n\n"
        f"{_md_table(active.groupby('source_code')['n_active_hotels'].describe().reset_index())}\n\n"
        f"Phan bo check-in theo thu trong tuan:\n\n{_md_table(tables['checkin_weekday_distribution'])}\n\n"
        "Hinh: `figures/active_hotel_by_date.png`.\n\n"
        "**Caveat.** \"Active\" dung ownership_status (owner_success/owner_failure), KHONG dung "
        "`hotels.booking_status` hien tai (se viet lai lich su - xem CLAUDE.md muc 2 ve cohort v1/v1.1/v2)."
    )

    protocol_summary = metrics.protocol_continuity(data["protocol_classified"], group_cols=("owner_source",))
    unattributed_total = int(data["protocol_unattributed_errors"]["n_unattributed_errors"].sum()) \
        if len(data["protocol_unattributed_errors"]) else 0
    sections.append(
        "## 7.5 Protocol continuity\n\n"
        f"**Fact.** Outcome theo nguon (lich EXPECTED tu ownership+cohort manifest, GPT review 12 M1):"
        f"\n\n{_md_table(protocol_summary)}\n\n"
        f"Item loi hoan toan khong resolve duoc hotel_id (that su unattributed, GPT review 12 M2): "
        f"{unattributed_total}.\n\n"
        "**Dien giai.** `missing_source_run` = ca ngay khong co run nao cua nguon do; "
        "`missing_item_in_existing_run` = ngay do co run nhung item nay khong nam trong do - 2 nguyen "
        "nhan khac nhau, khong nen gop chung khi dieu tra gap.\n\n"
        "**Caveat.** Bang nay la EXPECTED vs ACTUAL, khac voi canonical-series turnover (muc 7.12, chi "
        "mo ta ngay observed, khong suy missing)."
    )

    holiday_events = tables["vn_holidays_events_audit"]
    sections.append(
        "## 7.6 Lead time va calendar coverage\n\n"
        f"**Fact.** Phan bo observation (MAIN, khong sold-out) theo lead-time bucket:\n\n"
        f"{_md_table(tables['lead_time_bucket_distribution'])}\n\n"
        f"VN holidays/events audit: {len(holiday_events)} dong (`tables/vn_holidays_events_audit.csv`). "
        "Hinh: `figures/lead_time_bucket_distribution.png`.\n\n"
        "**Caveat.** Calendar feature dung `holiday_date = checkin_date` (ngay luu tru, KHONG phai ngay "
        "quan sat) - xem `holidays.checkin_calendar_flags`."
    )

    price_overall = tables["price_distribution_overall_main"].iloc[0]
    sections.append(
        "## 7.7 Price distribution\n\n"
        f"**Fact.** Gia (MAIN, khong sold-out, grain observation): count={price_overall['count']:.0f}, "
        f"min={price_overall['min']:.0f}, p50={price_overall['p50']:.0f}, mean={price_overall['mean']:.0f}, "
        f"p99={price_overall['p99']:.0f}, max={price_overall['max']:.0f} (VND).\n\n"
        f"Theo thanh pho:\n\n{_md_table(tables['price_distribution_by_city_main'])}\n\n"
        "Hinh: `figures/price_histogram_main.png` (thang thuong + log10), `figures/price_box_by_city.png`.\n\n"
        "**Dien giai.** Phan phoi gia thuc te lech phai manh (long-tail) - log10 histogram phu hop hon "
        "de doc duoi phan phoi, xem MIN1 file 09."
    )

    avail = tables["item_availability_overall"].iloc[0]
    sections.append(
        "## 7.8 Availability state (item grain)\n\n"
        f"**Fact.** Ty le item theo status (grain item, khong phai observation): "
        + ", ".join(
            f"{status}={avail[f'{status}_rate']:.2%}" for status in metrics.TERMINAL_ITEM_STATUSES
        ) + f". n_items={int(avail['n_items'])}.\n\n"
        f"Theo thanh pho:\n\n{_md_table(tables['item_availability_by_city'])}\n\n"
        "**Caveat.** Availability rate PHAI o grain item (GPT review 12 M2) - 1 item sold-out/"
        "not_bookable chi co 1 sentinel observation, khong phai N option nhu item success."
    )

    null_total = int(data["quality_scalars"]["quality_unexpected_nulls_by_field_group"]["n_unexpected_null_field_value_cells"].iloc[0])
    null_denom = int(data["quality_scalars"]["quality_unexpected_nulls_by_field_group"]["n_total_field_value_cells"].iloc[0])
    sections.append(
        "## 7.9 Missingness va parser completeness\n\n"
        f"**Fact.** NULL ngoai du kien tren field_value_cell (source,field,observation KHONG sold-out): "
        f"{null_total}/{null_denom} ({(null_total / null_denom if null_denom else 0):.4%}). Chi tiet theo "
        f"field/nguon: `tables/missingness_available_observations.csv`.\n\n"
        "**Caveat.** Structural missing (sold-out khong co room payload) DA LOAI qua `is_sold_out=0` o "
        "chinh SQL nguon - day chi con \"unexpected missing\" tren observation thuc su available "
        "(MIN2: mau so la field_value_cell, KHONG phai observation)."
    )

    ref_main = data["reference_exact_key_coverage_main"]
    ref_series = data["reference_series_exists_coverage_raw"]
    sections.append(
        "## 7.10 Full-history reference audit\n\n"
        f"**Fact.** Exact approved-key coverage (scope MAIN) theo lead-time bucket:\n\n{_md_table(ref_main)}\n\n"
        f"Series-with-approved-reference coverage (scope RAW, dinh nghia long hon):\n\n{_md_table(ref_series)}\n\n"
        f"Series approved (tong theo city/thang): {int(data['ref_approval_by_city_month']['approved'].sum())}.\n\n"
        "**Caveat.** Hai bang tren KHONG cung dinh nghia (GPT review 12 file 09 muc 2) - exact-key doi "
        "hoi DUNG room/rate key khop approved, series-exists chi doi hoi (hotel_id, checkin_date) do CO "
        "1 reference approved bat ky. Khong duoc gop 2 con so nay lam 1."
    )

    findings = tables["quality_findings"]
    n_flagged = int((findings["count"] > 0).sum())
    sections.append(
        "## 7.11 Data quality findings\n\n"
        f"**Fact.** {len(findings)} check da chay, {n_flagged} check co count>0 "
        f"(`quality_findings.csv`):\n\n"
        f"{_md_table(findings[['check_id', 'severity', 'scope', 'count', 'denominator', 'rate']])}\n\n"
        "**Caveat.** Check co count=0 van co 1 dong trong bang (chung minh da chay, khong phai im lang "
        "vi khong co gi de bao - xem MIN3 file 09)."
    )

    turnover = tables["canonical_series_turnover"]
    readiness = tables["dataset_readiness_by_horizon"]
    sections.append(
        "## 7.12 Readiness cho dataset/model\n\n"
        f"**Fact.** {len(turnover)} canonical series (hotel_id, checkin_date, canonical_series_id). "
        f"Theoretical horizon pairs (cap ngay quan sat cach DUNG K ngay):\n\n{_md_table(readiness)}\n\n"
        "**Caveat.** Day la CAP LY THUYET (theoretical_date_pairs) tu full-history, KHONG phai nhan "
        "causal that su cua dataset ML (`actual_status='not_available'` - Wave B dataset build lifecycle "
        "chua trien khai, xem muc Wave B ben duoi)."
    )

    wave_b = data["wave_b_readiness"]
    wave_b_ready = bool(wave_b["ready"].any()) if len(wave_b) else False
    sections.append(
        "## Wave B dataset readiness\n\n"
        f"**Fact.** {'CO' if wave_b_ready else 'CHUA CO'} dataset_version nao vua `status=\"pass\"` vua "
        f"du du lieu ca 3 bang `ml_*` cho batch dang doc.\n\n"
        + (_md_table(wave_b) if len(wave_b) else "_(khong co dataset_build_manifests nao cho batch nay)_")
    )

    return sections


def write_eda_report_and_dictionary(
    data: dict[str, Any], tables: dict[str, "pd.DataFrame"], analysis_dir: Path,
) -> None:
    """EDA_REPORT.md/DATA_DICTIONARY.md VOI NOI DUNG THAT (GPT review 12 eda M4: "khong con la "
    placeholder... du section 7.1-7.12, render count/rate/table/figure path that, phan biet
    fact/inference/caveat"). Moi con so tinh TRUC TIEP tu `data`/`tables` cua CHINH lan chay nay -
    khong bia truoc. Phan dien giai gioi han o quan sat CO CHE an toan gan voi con so, KHONG phai ket
    luan nghien cuu cuoi cung cho dataset - do la viec cua vong GPT review ket qua that (dung quy trinh
    Claude-GPT da dinh: chay EDA -> GPT review ket qua -> GPT tom tat cho nguoi dung)."""
    snapshot: db.WarehouseSnapshot = data["snapshot"]
    header = (
        f"# EDA Wave A Report - {snapshot.database} / {snapshot.batch_id}\n\n"
        f"Sinh luc {dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()} tu du lieu "
        f"THAT trong `tables/`, `quality_findings.csv`, `eda_summary.json`, "
        f"`dataset_readiness_by_horizon.csv` cua chinh lan chay nay. Moi con so trong bao cao nay lay "
        f"TRUC TIEP tu cac file do - khong co so lieu bia. Xem `input_manifest.json` cho provenance day "
        f"du (git commit, hash manifest, thoi luong/peak memory).\n\n"
        f"**Cach doc:** moi section co **Fact** (con so/bang that), doi khi **Dien giai** (quan sat co "
        f"che an toan gan truc tiep voi con so) va **Caveat** (gioi han/quy uoc can biet truoc khi dung "
        f"con so do). Day KHONG phai ket luan nghien cuu cuoi cung cho luan van - phan dien giai sau "
        f"cung se do vong GPT review ket qua that bo sung, dung quy trinh nguoi dung da dinh.\n\n---\n"
    )
    body = "\n\n".join(_report_sections(data, tables))
    (analysis_dir / "EDA_REPORT.md").write_text(header + "\n" + body + "\n", encoding="utf-8")

    dictionary_rows = [
        ("price_per_night", "price_observations", "observation", "VND", "n/a",
         "so nguyen duong (>0 khi khong sold-out)", "NULL khi is_sold_out=1 (sentinel, khong phai missing)",
         "MAIN/RAW (khong sold-out)", "target chinh cua model - CHUA loai anomaly (xem CLAUDE.md muc 4.5)"),
        ("lead_time", "price_observations", "observation", "ngay", "n/a", "so nguyen >=0",
         "khong co (luon co gia tri)", "MAIN/RAW",
         "= checkin_date - ngay VN cua observed_at; co the tinh lai qua `timezone.lead_time_days` de doi chieu (7.11)"),
        ("checkin_date", "price_observations", "observation", "ngay", "n/a", "date", "khong co", "MAIN/RAW",
         "ngay luu tru - KHONG phai ngay quan sat"),
        ("vn_observation_date", "price_observations (suy ra)", "observation", "ngay", "Asia/Ho_Chi_Minh +07:00 co dinh",
         "date", "khong co", "MAIN/RAW", "= DATE(observed_at UTC + 7h) - dung cho daily snapshot/turnover, KHONG dung timezone he thong"),
        ("hotel_id", "hotels/price_observations", "hotel", "n/a", "n/a", "slug URL Booking (vd 'serenity-...')",
         "co the NULL o item error truoc khi resolve duoc (GPT review 12 M2)", "n/a",
         "khoa on dinh xuyen suot cac lan crawl - KHONG dung `record_id` de sort/join giua cac nguon"),
        ("city", "hotels", "hotel", "n/a", "n/a", "1 trong 5: Ho Chi Minh/Ha Noi/Vung Tau/Da Lat/Phu Quoc",
         "co the NULL neu hotel chua duoc gan city", "n/a", "quality_city_outside_scope kiem tra gia tri ngoai 5 thanh pho"),
        ("is_sold_out", "price_observations", "observation", "boolean", "n/a", "0/1",
         "khong co (luon co gia tri)", "n/a", "TRUE => price_per_night PHAI NULL (sentinel demand, khong phai gia 0)"),
        ("availability_status", "price_observations", "observation", "enum", "n/a", "available/sold_out/not_listed",
         "khong co", "n/a", "tach biet voi item.status (crawl-level) - day la property-level tai thoi diem observation"),
        ("matches_approved_key", "reference_observation_match_main/raw (eda)", "observation", "boolean", "n/a", "True/False",
         "khong co (coerced non-nullable)", "MAIN hoac RAW tuy metric",
         "EXACT: canonical room/rate key CUA CHINH observation = 1 reference approved. KHONG dung chung voi series_has_approved_reference"),
        ("series_has_approved_reference", "reference_series_exists_observation_coverage_raw (eda)", "observation",
         "boolean", "n/a", "True/False", "khong co", "RAW",
         "LONG hon matches_approved_key: (hotel_id,checkin_date) CO reference approved, khong doi hoi dung key cua chinh no (GPT review 12 file 09 muc 2)"),
        ("outcome", "protocol_continuity_classified (eda)", "expected schedule slot", "n/a", "n/a",
         "owner_success/owner_failure_status_sold_out/owner_failure_status_not_bookable/"
         "owner_failure_status_error/missing_source_run/missing_item_in_existing_run", "khong co", "n/a",
         "2 gia tri missing_* phan biet 'ca ngay khong co run' vs 'co run nhung thieu item nay' (GPT review 12 M1)"),
        ("lead_time_bucket", "cac bang eda (suy ra)", "observation/item", "n/a", "n/a",
         "0/1-3/4-7/8-14/15-30/31-60/61+", "khong co", "n/a", "categorical ordered - xem metrics.LEAD_TIME_BUCKETS"),
    ]
    dict_df = pd.DataFrame(dictionary_rows, columns=[
        "field", "source_table", "grain", "unit", "timezone", "allowed_values",
        "structural_missing", "eligibility_scope", "notes_and_leakage_caveat",
    ])
    dictionary_text = (
        f"# Data Dictionary (Wave A) - {snapshot.database} / {snapshot.batch_id}\n\n"
        "Cac truong CHINH duoc publish trong `tables/*.csv` - xem `EDA_CURATED_PLAN.md` muc 9 cho khung "
        "day du va tung file CSV cho toan bo cot cua tung bang cu the.\n\n"
        f"{_md_table(dict_df, max_rows=len(dict_df))}\n"
    )
    (analysis_dir / "DATA_DICTIONARY.md").write_text(dictionary_text, encoding="utf-8")
