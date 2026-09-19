"""Orchestration Wave A (GPT review 12 eda M1/M2/M3/M6 + file 11 muc 4-6): thu thap TOAN BO du lieu trong DUNG 1 pham vi
`db.connect()`, roi tra ve 1 dict de cac buoc sau (bang/hinh/report) khong can mo lai connection.

BO NHO (GPT file 11 muc 4): khong con frame cap OBSERVATION nao trong Python - moi bang gia/availability/reference/turnover la ket qua
`GROUP BY` thang trong SQL (xem `queries.py`, `sql_builders.py`). Frame cap ITEM/lich chi con o protocol continuity (bi chan boi kich
thuoc lich ownership x cohort, khong phai so observation). Observation-level chi co dang sample audit co gioi han.

Notebook 01 CHI goi vao module nay (+ `plots.py` de ve) - khong tu viet SQL, khong tu quan ly vong doi connection qua nhieu cell.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

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
import coverage_matrix
import db
import holidays
import metrics
import protocol_schedule as ps
import publication
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
# Moi catalog metric KHONG can tham so, chay theo thu tu nay (metric phu thuoc nhau da dat truoc). Calendar/collision/protocol co
# buoc rieng ben duoi. `test_collected_metrics_phu_toan_bo_catalog` dam bao khong metric nao trong CATALOG bi quen.
_PLAIN_METRICS: tuple[str, ...] = (
    # 7.1
    "preflight_rejections", "preflight_import_sources", "observation_date_ranges_main",
    # 7.2
    "ownership_by_source_status_reason", "run_item_observation_by_source_crawl_date", "raw_vs_main_by_source",
    # 7.3
    "run_duration_and_throughput", "run_day_status_counts_raw", "run_day_error_code_counts_raw",
    # 7.4
    "active_hotel_by_crawl_date_source", "active_hotel_by_crawl_date_source_city", "checkin_dates_tracked_by_crawl_date_source",
    "crawl_date_lead_time_bucket_heatmap", "checkin_month_distribution_main", "checkin_weekday_distribution_main",
    "lead_time_bucket_distribution_main", "lead_time_bucket_distribution_by_city_source_main",
    "observation_counts_by_checkin_date_city_main",
    # 7.7
    "price_distribution_overall_main", "price_distribution_overall_raw", "price_distribution_by_city_main",
    "price_distribution_by_lead_time_bucket_main", "price_distribution_by_weekday_main", "price_box_stats_by_city_main",
    "price_hotel_dispersion_main", "price_sensitivity_by_series_main", "price_histogram_linear_main", "price_histogram_log10_main",
    "price_outlier_summary_by_hotel_main", "price_outlier_sample_main",
    # 7.8
    "item_status_counts_overall_main", "item_status_counts_by_city_main", "item_status_counts_by_hotel_main",
    "item_status_counts_by_checkin_month_main", "item_status_counts_by_lead_time_bucket_main",
    "item_status_counts_by_crawl_date_hotel_main",
    # 7.9
    "missingness_available_observations", "missingness_by_selector_version", "missingness_by_crawl_date", "missingness_by_city",
    "missingness_by_item_status_sold_out", "artifact_completeness_by_source_crawl_date",
    # 7.10
    "reference_observation_match_main", "reference_observation_match_raw", "reference_series_exists_observation_coverage_raw",
    "reference_series_exists_observation_coverage_raw_legacy_bucket", "reference_item_level_availability_main",
    "reference_approval_by_city_month", "reference_status_evidence_summary", "reference_uniqueness_per_series",
    "reference_candidate_coverage_summary",
    # 7.5 / 7.12
    "canonical_series_facts_main", "history_length_by_hotel_checkin_main", "series_evidence_runs_distribution",
    # Wave B guard
    "wave_b_dataset_version_readiness",
)
# Metric can tham so/ket qua trung gian nen chay rieng trong collect_wave_a_data():
_SPECIAL_METRICS: tuple[str, ...] = (
    "preflight_core_counts", "preflight_non_terminal_runs_items", "collision_item_pairs", "item_identity_actual_raw",
    "protocol_continuity_actual",
    "price_distribution_by_calendar_flags_main",
)
COLLECTED_METRIC_IDS: frozenset[str] = frozenset(_PLAIN_METRICS) | frozenset(_SPECIAL_METRICS) | frozenset(queries.QUALITY_SCALAR_IDS)


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

    `data["m"]` = `{metric_id: DataFrame}` cua MOI catalog metric da chay (bang nho da aggregate). `data["metric_seconds"]` = thoi gian
    tung metric (giup phat hien truy van nao se khong scale khi warehouse lon hon). `data["pipeline_collect_seconds"]` (GPT review 12
    eda M6) = thoi luong CHINH pha thu thap nay, doc lai o `write_eda_summary()`.
    """
    _started = time.monotonic()
    data: dict[str, Any] = {}
    metrics_out: dict[str, "pd.DataFrame"] = {}
    seconds: dict[str, float] = {}
    data["m"], data["metric_seconds"] = metrics_out, seconds
    connect_kwargs = {"pointer_path": pointer_path} if pointer_path is not None else {}
    with db.connect(**connect_kwargs) as (conn, snapshot):
        data["snapshot"] = snapshot

        def run(metric_id: str, **kwargs: Any) -> "pd.DataFrame":
            began = time.monotonic()
            frame = queries.run_metric(metric_id, conn, snapshot, **kwargs)
            seconds[metric_id] = round(time.monotonic() - began, 2)
            metrics_out[metric_id] = frame
            return frame

        # 7.1 preflight - gate truoc moi thu khac
        run("preflight_core_counts")
        non_terminal = run("preflight_non_terminal_runs_items")
        if int(non_terminal["non_terminal_runs"].iloc[0]) != 0:
            raise RuntimeError("co crawl_runs chua terminal - batch chua an toan de doc, dung Wave A.")
        if int(non_terminal["non_terminal_items"].iloc[0]) != 0:
            raise RuntimeError("co crawl_run_items chua terminal (queued/running) - dung Wave A.")

        for metric_id in _PLAIN_METRICS:
            run(metric_id)
        for metric_id in queries.QUALITY_SCALAR_IDS:
            # SUM(...) tren tap rong tra NULL (vd MAIN khong co item sold_out nao) = 0 vi pham, KHONG phai thieu du lieu: chuan hoa ve 0 o day
            # de moi noi dung sau (severity, count) khong phai tu xu ly NaN/None.
            metrics_out[metric_id] = run(metric_id).fillna(0)
        data["quality_samples"] = queries.quality_violation_samples(conn, snapshot)

        # 7.6/7.7 calendar: tap (checkin_date, city) tu counts NHO da aggregate; chi cac dong co it nhat 1 co True duoc dua vao SQL join
        # (LEFT JOIN + COALESCE cho phan con lai) - khong bao gio join observation-level trong Python.
        holiday_csv = holidays.load_holiday_csv(vn_holidays_csv)
        data["holiday_csv"] = holiday_csv
        counts = metrics_out["observation_counts_by_checkin_date_city_main"]
        data["checkin_calendar"] = holidays.checkin_calendar_flags(holiday_csv.events, counts["checkin_date"].unique())
        flag_columns = list(holidays._FLAG_COLUMNS)
        flagged = data["checkin_calendar"][data["checkin_calendar"][flag_columns].any(axis=1)]
        run("price_distribution_by_calendar_flags_main", calendar_rows=flagged.to_dict("records"))

        # 7.5/7.12: population facts da co first/last/max/median gap; sample 200 chi la
        # deterministic top-gap audit, khong phai nguon cua distribution/readiness.
        began = time.monotonic()
        data["turnover_sample"] = queries.turnover_sample(conn, snapshot, metrics_out["canonical_series_facts_main"])
        seconds["turnover_sample_median_gap"] = round(time.monotonic() - began, 2)

        # 7.5 protocol continuity/effective identity: doc DB va resolve cohort trong cung snapshot
        # collection phase; cac buoc publish sau khi dong connection khong cham DB nua.
        # GPT review 12 eda M1: `source_run_dates`/`protocol_complete_through_date_by_source` KHONG con
        # dung de GIOI HAN truoc expected universe (do la bug cu lam mat han loai gap "missing_run") -
        # chi dung SAU de (a) lam ranh gioi tren cua window doc lap actual, va (b) phan loai 2 tang
        # missing_source_run vs missing_item_in_existing_run trong `classify_outcomes`.
        source_run_dates = queries.actual_crawl_dates_by_source(conn, snapshot)
        protocol_complete_through_date_by_source = queries.dump_taken_at_vn_date_by_source(conn, snapshot)
        # Keep the old raw-id SQL pair query as an explicit diagnostic baseline/catalog metric.
        # Published collision tables below use the cohort-aware frame instead.
        run("collision_item_pairs")
        raw_identity_actual = run("item_identity_actual_raw")
        protocol_actual = run("protocol_continuity_actual")

        # Effective item identity la contract DUNG CHUNG cho protocol/active-hotel/availability/
        # collision. 623/623 item NULL hotel_id cua snapshot hien tai resolve duoc tu URL + cohort;
        # neu chi resolve trong protocol thi cac bang con lai van undercount va day loi vao unknown.
        resolved_actual = ps.resolve_effective_hotel_id(
            protocol_actual, cohort_history_path, base_dir=cohort_history_base_dir
        )
        resolved_actual["checkin_month"] = pd.to_datetime(resolved_actual["checkin_date"]).dt.strftime("%Y-%m")
        checkin_ts = pd.to_datetime(resolved_actual["checkin_date"])
        crawl_ts = pd.to_datetime(resolved_actual["crawl_date"])
        resolved_actual["weekday_number"] = checkin_ts.dt.weekday.astype("int64")
        resolved_actual["weekday"] = checkin_ts.dt.day_name()
        resolved_actual["is_weekend_fri_sat"] = resolved_actual["weekday_number"].isin((4, 5))
        resolved_actual["lead_time"] = (checkin_ts - crawl_ts).dt.days.astype("int64")
        resolved_actual["lead_time_bucket"] = resolved_actual["lead_time"].map(
            lambda value: metrics.lead_time_bucket(int(value)) if value >= 0 else metrics.INVALID_LEAD_TIME_BUCKET
        )
        data["resolved_items_main"] = resolved_actual

        resolved_raw = ps.resolve_effective_hotel_id(
            raw_identity_actual, cohort_history_path, base_dir=cohort_history_base_dir
        )
        data["resolved_items_raw"] = resolved_raw

        # SQL metric cu van duoc collect nhu audit/intermediate; artifact publish dung pair EFFECTIVE
        # de khong loai item loi co hotel_id raw NULL nhung URL resolve duoc.
        pairs = metrics.collision_pairs_from_effective_items(resolved_raw)
        data["collision_item_pairs_effective"] = pairs
        success_pairs = pairs[(pairs["status_a"] == "success") & (pairs["status_b"] == "success")]
        began = time.monotonic()
        data["collision_option_detail"], data["collision_pair_coverage"] = queries.collision_option_analysis(
            conn, snapshot, success_pairs)
        seconds["collision_option_analysis"] = round(time.monotonic() - began, 2)

    data["core_counts"] = metrics_out["preflight_core_counts"]
    data["non_terminal"] = metrics_out["preflight_non_terminal_runs_items"]

    # GPT review 12 eda file 11 muc 6.3: "report/manifest ghi ro protocol_complete_through_date va gia
    # dinh dump cua ngay do da bao phu xong lich crawl ngay do - khong goi chung la cutoff mo ho." Luu
    # LAI de `build_input_manifest()` publish ro rang, khong chi dung noi bo trong ham nay.
    data["protocol_complete_through_date_by_source"] = {
        source: date.isoformat() for source, date in protocol_complete_through_date_by_source.items()
    }

    # Tu day tro di KHONG can connection - file I/O thuan (ownership/cohort manifest).
    expected = ps.expected_schedule(
        ownership_manifest_path, cohort_history_path, base_dir=cohort_history_base_dir,
        protocol_complete_through_date_by_source=protocol_complete_through_date_by_source,
    )

    # GPT review 12 eda M2: resolve hotel_id=NULL qua source_hotel_link TRUOC khi join - khong con
    # loai am tham/double-count voi bucket "unattributed" nhu thiet ke cu.
    data["protocol_unattributed_errors"] = ps.summarize_unattributed(resolved_actual)

    actual_for_join = resolved_actual[resolved_actual["hotel_id_resolution"] != "unattributed"].copy()
    actual_for_join["hotel_id"] = actual_for_join["effective_hotel_id"]
    actual_for_join = actual_for_join[["source_code", "crawl_date", "checkin_date", "hotel_id", "outcome"]]
    data["protocol_classified"] = ps.classify_outcomes(
        expected, actual_for_join, source_run_dates=source_run_dates
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
    khong bao gio giam). Day chi la bang chung van hanh - thiet ke bounded-memory that nam o cho khong keo
    observation-level frame (xem docstring module)."""
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
    m = data["m"]
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

    # GPT review 12 eda M6 + plan 7.1 ("runs/items/observations/curated keys/rejections"): doi soat hotels/runs/items/observations/
    # curated/rejections/reference. Gom TAT CA mismatch vao 1 loi duy nhat thay vi fail-fast tung cai - de review 1 lan thay het cho lech.
    core_counts = m["preflight_core_counts"].iloc[0].to_dict()
    import_step = warehouse_report["steps"]["11_import"]
    curated_step = warehouse_report["steps"]["14_curated"]
    report_counts = {
        "hotels": import_step["hotels"]["warehouse_distinct_hotels"],
        "crawl_runs": sum(s["crawl_runs_imported"] for s in import_step["per_source"].values()),
        "crawl_run_items": sum(s["crawl_run_items_imported"] for s in import_step["per_source"].values()),
        "price_observations": curated_step["price_observations"],
        "curated_observation_keys": curated_step["curated_observation_keys"],
        # `rejections` trong report la {"source|table|scope": so_dong}; tong = so dong etl_import_rejections cua batch.
        "rejections": sum(int(v) for v in import_step.get("rejections", {}).values()),
    }
    eda_counts = {field: int(core_counts[field]) for field in report_counts if field != "rejections"}
    eda_counts["rejections"] = int(m["preflight_rejections"]["n_rejections"].iloc[0])
    references_step = warehouse_report["steps"]["15_references"]
    ref_by_city_month = m["reference_approval_by_city_month"]
    reference_counts = {
        "series_with_reference": int(ref_by_city_month["n"].sum()),
        "approved": int(ref_by_city_month["approved"].sum()),
    }
    reconciliation = [
        {"check": field, "eda_value": eda_counts[field], "validation_report_value": int(report_counts[field]),
         "match": eda_counts[field] == int(report_counts[field])} for field in report_counts
    ] + [
        {"check": f"reference_{field}", "eda_value": reference_counts[field],
         "validation_report_value": int(references_step[field]), "match": reference_counts[field] == int(references_step[field])}
        for field in reference_counts
    ]
    mismatches = {row["check"]: row for row in reconciliation if not row["match"]}
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

    cohort_workbook_versions = _cohort_workbook_versions(cohort_history_path, base_dir=cohort_history_base_dir)
    cohort_mismatches = [v for v in cohort_workbook_versions if v["validation_status"] != "match"]
    if cohort_mismatches:
        raise ValueError(
            f"cohort workbook computed members hash/size KHONG khop declared trong "
            f"{cohort_history_path.name} (file tren dia co the da doi sau khi build warehouse, hoac "
            f"history JSON da cu): {cohort_mismatches}"
        )

    ranges = m["observation_date_ranges_main"].iloc[0]
    matrix = coverage_matrix.coverage_matrix_dataframe()
    missing_rows = coverage_matrix.missing_required_rows(matrix)
    provenance = capture_code_provenance()
    return {
        **snapshot.to_manifest_dict(),
        "warehouse_validation_report_path": _path_for_manifest(warehouse_validation_report_path),
        "warehouse_validation_report_sha256": hashlib.sha256(
            warehouse_validation_report_path.read_bytes()).hexdigest(),
        "warehouse_validation_report_status": warehouse_report["status"],
        "reconciled_counts": report_counts,
        "reconciled_reference_counts": reference_counts,
        "reconciliation": reconciliation,
        "sources": [
            {k: str(v) for k, v in row.items()} for row in m["preflight_import_sources"].to_dict("records")
        ],
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
        "observed_date_min": str(ranges["observed_date_min"]), "observed_date_max": str(ranges["observed_date_max"]),
        "checkin_date_min": str(ranges["checkin_date_min"]), "checkin_date_max": str(ranges["checkin_date_max"]),
        "query_catalog_version": queries.CATALOG_VERSION,
        "query_catalog_metric_ids": sorted(queries.CATALOG),
        "published_tables": sorted(publication.PUBLISHED_TABLES),
        "published_figures": sorted(publication.PUBLISHED_FIGURES),
        "coverage_matrix": {
            "n_bullets": int(len(matrix)), "n_implemented": int(len(matrix) - len(missing_rows)),
            "n_missing": int(len(missing_rows)), "full_wave_a": bool(missing_rows.empty),
        },
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
    `queries.quality_violation_samples`, gioi han N) khi count>0, khong con `[]` gia.

    Du 12 check cua plan 7.11: 10 check du lieu + outlier price (robust within-hotel, chi flag) + collision/source divergence
    (2 dong: status disagreement va option price divergence - KHONG tu dong la loi parser)."""
    m = data["m"]
    samples = data["quality_samples"]
    rows: list[dict[str, Any]] = []

    def add(check_id, *, severity, scope, grain, count, denominator, sample_keys, likely_cause, action):
        # SUM(...) tren tap rong tra NULL (vd khong co item sold_out nao trong MAIN) - la "0 vi pham", khong phai loi thieu du lieu.
        count = 0 if count is None or pd.isna(count) else int(count)
        denominator = None if denominator is None or pd.isna(denominator) else int(denominator)
        rate = (count / denominator) if denominator else None
        rows.append({
            "check_id": check_id, "severity": severity, "scope": scope, "grain": grain,
            "count": count, "denominator": denominator,
            "rate": rate, "sample_keys": json.dumps(sample_keys, ensure_ascii=False, default=str),
            "likely_cause": likely_cause, "recommended_action": action,
        })

    def _samples(check_id: str, count: int) -> list:
        # MIN1: chi tra sample khi count>0 - count=0 thi sample ruong tu queries la DUNG (khong co gi
        # de sample), khong phai "quen lay".
        return samples.get(check_id, []) if count else []

    p = m["quality_price_non_positive"].iloc[0]
    add("price_non_positive", severity="high" if p["n_violations"] else "info", scope="RAW", grain="observation",
       count=p["n_violations"], denominator=p["n_total"],
       sample_keys=_samples("price_non_positive", p["n_violations"]),
       likely_cause="Parser ghi gia <=0 tren observation khong sold-out (khong duoc phep - muc 4.5 CLAUDE.md).",
       action="Neu >0: dieu tra record_id cu the, kiem tra selector/parser tai thoi diem crawl.")

    c = m["quality_checkout_not_after_checkin"].iloc[0]
    add("checkout_not_after_checkin", severity="high" if c["n_violations"] else "info", scope="RAW",
       grain="observation", count=c["n_violations"], denominator=int(m["preflight_core_counts"]["price_observations"].iloc[0]),
       sample_keys=_samples("checkout_not_after_checkin", c["n_violations"]),
       likely_cause="checkout_date phai luon = checkin_date + 1 dem (DATEDIFF<>1 - MIN3: sua tu <= sang dung nghiep vu).",
       action="Neu >0: kiem tra logic tinh checkout_date luc crawl/import.")

    s = m["quality_success_item_without_observation"].iloc[0]
    add("success_item_without_observation", severity="high" if s["n_violations"] else "info", scope="MAIN",
       grain="item", count=s["n_violations"], denominator=s["n_total"],
       sample_keys=_samples("success_item_without_observation", s["n_violations"]),
       likely_cause="Item status=success nhung khong co price_observation nao - vi pham invariant import.",
       action="Neu >0: kiem tra importer._import_observations cho dung item_id nay.")

    k = m["quality_canonical_key_anomalies"].iloc[0]
    k_count = int(k["n_nonsoldout_missing_canonical"]) + int(k["n_soldout_with_room_key"])
    add("canonical_key_anomalies", severity="high" if k_count else "info",
       scope="RAW", grain="observation", count=k_count, denominator=k["n_total"],
       sample_keys=_samples("canonical_key_anomalies", k_count),
       likely_cause="P-A: non-sold-out phai co canonical key, sold-out phai la EMPTY sentinel (muc canonicalize).",
       action="Neu >0: kiem tra curated.build_curated_keys cho batch nay.")

    city = m["quality_city_outside_scope"].iloc[0]
    add("city_outside_scope", severity="medium" if city["n_violations"] else "info", scope="RAW", grain="hotel",
       count=city["n_violations"], denominator=city["n_total"],
       sample_keys=_samples("city_outside_scope", city["n_violations"]),
       likely_cause="hotels.city ngoai 5 thanh pho scope (CLAUDE.md muc 2) - co the do merge_policy hoac cohort sai.",
       action="Neu >0: liet ke hotel_id, doi chieu cohort manifest.")

    n = m["quality_unexpected_nulls_by_field_group"].iloc[0]
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
    lt = m["quality_lead_time_mismatch"].iloc[0]
    add("lead_time_mismatch", severity="medium" if lt["n_mismatch"] else "info", scope="RAW", grain="observation",
       count=lt["n_mismatch"], denominator=lt["n_total"], sample_keys=_samples("lead_time_mismatch", lt["n_mismatch"]),
       likely_cause="lead_time luu san (luc insert) khac DATEDIFF(checkin_date, ngay VN cua observed_at) tinh lai.",
       action="Neu >0: kiem tra logic tinh lead_time luc insert (co the do offset/DST hoac gio he thong sai).")

    dup = m["quality_duplicate_daily_series"].iloc[0]
    add("duplicate_daily_series", severity="high" if dup["n_duplicate_groups"] else "info",
       scope="RAW", grain="item x canonical key", count=dup["n_duplicate_groups"], denominator=dup["n_total_groups"],
       sample_keys=_samples("duplicate_daily_series", dup["n_duplicate_groups"]),
       likely_cause="Cung 1 item co >1 observation trung canonical (room,rate) key - 2 raw option khac nhau "
                    "canonical hoa ve cung 1 key.",
       action="Neu >0: kiem tra logic canonicalize/dedup cho item nay, co the can them tieu chi phan biet.")

    pm = m["quality_parent_mismatch"].iloc[0]
    add("parent_mismatch", severity="high" if pm["n_mismatch"] else "info", scope="RAW", grain="observation",
       count=pm["n_mismatch"], denominator=pm["n_total"], sample_keys=_samples("parent_mismatch", pm["n_mismatch"]),
       likely_cause="Observation co hotel_id/checkin_date KHAC voi item cha (crawl_run_items) - vi pham invariant import.",
       action="Neu >0: kiem tra importer._import_observations, co the ghi nham observation vao item khac.")

    sentinel = m["quality_sold_out_sentinel_consistency"].iloc[0]
    add("sold_out_sentinel_consistency", severity="high" if sentinel["n_violations"] else "info",
       scope="MAIN", grain="item", count=sentinel["n_violations"], denominator=sentinel["n_total"],
       sample_keys=_samples("sold_out_sentinel_consistency", sentinel["n_violations"]),
       likely_cause="Item status=sold_out nhung KHONG co dung 1 observation sentinel (is_sold_out=1, canonical "
                    "room key=EMPTY_ROOM_KEY) - co the thieu, thua, hoac sai key.",
       action="Neu >0: kiem tra logic ghi sentinel sold-out luc import/curated cho item nay.")

    tpn = m["quality_price_total_per_night_inconsistent"].iloc[0]
    add("price_total_per_night_inconsistent", severity="medium" if tpn["n_violations"] else "info",
       scope="RAW", grain="observation", count=tpn["n_violations"], denominator=tpn["n_total"],
       sample_keys=_samples("price_total_per_night_inconsistent", tpn["n_violations"]),
       likely_cause="price_total phai LUON = price_per_night (CLAUDE.md muc 4.3: chi cao 1 dem/toi da 1 dem).",
       action="Neu >0: kiem tra parser co vo tinh tinh price_total theo so dem khac 1 hay khong.")

    # Plan 7.11: "outlier price theo robust within-hotel rule, chi flag chu khong xoa" - dem TU aggregate SQL (khong quet observation).
    outliers = m["price_outlier_summary_by_hotel_main"]
    n_outliers = int(outliers["n_outliers"].sum()) if len(outliers) else 0
    n_outlier_scope = int(outliers["n_obs"].sum()) if len(outliers) else 0
    outlier_sample = m["price_outlier_sample_main"].head(10)
    add("price_outlier_robust_within_hotel", severity="medium" if n_outliers else "info", scope="MAIN", grain="observation",
       count=n_outliers, denominator=n_outlier_scope,
       sample_keys=[
           {"record_id": int(r.record_id), "hotel_id": r.hotel_id, "checkin_date": str(r.checkin_date),
            "price_per_night": float(r.price_per_night), "hotel_median_price": float(r.hotel_median_price),
            "robust_z": round(float(r.price_robust_z), 2)} for r in outlier_sample.itertuples()
       ] if n_outliers else [],
       likely_cause=f"Gia lech >= {queries.OUTLIER_MAD_MULTIPLIER:g} robust-sigma (MAD) khoi median cua CHINH hotel (hotel >= "
                    f"{queries.OUTLIER_MIN_HOTEL_OBSERVATIONS} observation). Co the la phong cao cap/villa hop le trong cung hotel, HOAC gia bat "
                    "thuong that (xem anomaly registry v2, CLAUDE.md muc 4.5) - rule nay CHI FLAG, khong ket luan.",
       action="Khong xoa observation nao. Doi chieu sample_keys voi anomaly registry truoc khi coi la loi; day khong phai co is_anomaly.")

    unattributed_df = data["protocol_unattributed_errors"]
    unattributed_total = int(unattributed_df["n_unattributed_errors"].sum()) if len(unattributed_df) else 0
    unattributed_samples: list = []
    for raw in unattributed_df.get("sample_keys", []):
        unattributed_samples.extend(json.loads(raw))
    add("protocol_continuity_unattributed_errors", severity="info", scope="MAIN", grain="item",
       count=unattributed_total, denominator=int(m["item_status_counts_overall_main"]["n_items"].iloc[0]),
       sample_keys=unattributed_samples[:10],
       likely_cause="Item 'error' voi hotel_id=NULL VA source_hotel_link khong resolve duoc qua extract_hotel_slug "
                    "(dead link/CAPTCHA thuc su truoc khi Booking tra property page) - GPT review 12 M2: KHAC voi "
                    "item hotel_id=NULL nhung URL van resolve duoc (nhung da duoc gan lai vao protocol_classified).",
       action="Khong can hanh dong tru khi count bat thuong tang dot bien - xem sample_keys (source_link_hash).")

    # Collision / source divergence (GPT file 11 muc 3) - 2 dong, denominator RIENG cho tung lop; KHONG gan loi parser.
    effective_pairs = data["collision_item_pairs_effective"]
    item_summary = metrics.collision_item_summary(effective_pairs).iloc[0]
    n_pairs = int(item_summary["n_collision_item_pairs"])
    pair_sample = effective_pairs[effective_pairs["status_a"] != effective_pairs["status_b"]].head(10)
    add("collision_item_status_disagreement", severity="info" if not n_pairs else "medium" if item_summary["n_status_disagreement"] else "info",
       scope="RAW", grain="item pair", count=int(item_summary["n_status_disagreement"]), denominator=n_pairs,
       sample_keys=[
           {"item_id_a": int(r.item_id_a), "item_id_b": int(r.item_id_b), "hotel_id": r.hotel_id, "status_a": r.status_a, "status_b": r.status_b}
           for r in pair_sample.itertuples()
       ],
       likely_cause="2 nguon crawl cung (ngay VN, hotel, check-in) o thoi diem khac nhau va thu ket qua terminal khac nhau (vd 1 nguon "
                    "thay phong, nguon kia sold_out). Booking doi theo thoi diem/session - KHONG tu dong la loi parser.",
       action="Xem collision_item_time_diff_stratification: bat dong o bucket phut xa hon la divergence quan sat duoc, khong phai loi.")
    option_summary = metrics.collision_option_summary(data["collision_option_detail"]).iloc[0]
    n_option_pairs = int(option_summary["n_option_pairs"])
    diverged = n_option_pairs - int(option_summary["n_exact_price_match"])
    detail = data["collision_option_detail"]
    diverged_sample = detail[detail["price_abs_diff"] > 0].sort_values("price_abs_diff", ascending=False).head(10)
    add("collision_option_price_divergence", severity="medium" if diverged else "info", scope="RAW", grain="shared canonical option pair",
       count=diverged, denominator=n_option_pairs,
       sample_keys=[
           {"item_id_a": int(r.item_id_a), "item_id_b": int(r.item_id_b), "price_a": float(r.price_per_night_a),
            "price_b": float(r.price_per_night_b), "minutes_apart": round(float(r.observed_at_diff_minutes), 1)}
           for r in diverged_sample.itertuples()
       ],
       likely_cause="Cung canonical (room, rate) key nhung gia khac giua 2 nguon o thoi diem crawl khac nhau (dynamic pricing / inventory doi "
                    "theo phien). Mau so la shared canonical option-pairs duy nhat 1-1, khong phai item-pairs. KHONG gan loi parser neu chua co "
                    "bang chung parser (xem collision_option_time_diff_stratification).",
       action="Doc bang stratification theo phut chenh lech: gia khac o 0-5 phut moi dang nghi ngo, gia khac o >60 phut la divergence thong thuong.")

    return pd.DataFrame(rows)


# ============================================================================== 4. TINH + GHI BANG (fail-if-exists, manifest cuoi cung)
# GPT review 12 eda M4: "Notebook/runner phai goi MOT artifact writer duy nhat de khong co hai danh
# sach output lech nhau" - `compute_wave_a_tables()` tinh HET, notebook chi `display()` tu dict tra ve,
# `write_wave_a_tables()` la noi DUY NHAT ghi CSV. Tap khoa tra ve == `publication.PUBLISHED_TABLES` (test E2E kiem tra).
_ROOT_LEVEL_TABLES = frozenset(name for name, spec in publication.PUBLISHED_TABLES.items() if spec.root_level)


def _price_sensitivity_summary(sensitivity: "pd.DataFrame") -> "pd.DataFrame":
    """Phan phoi gia tri tong hop (min / median hop le) cua tung (hotel_id, checkin_date, vn_observation_date) - de hotel nhieu option
    khong lan at hotel it option (plan 7.7). Tinh tren bang series-day nho da aggregate SQL, khong phai observation."""
    columns = ["aggregation", "n_units", "min_value", "p1", "p5", "p50", "p75", "p95", "p99", "max_value", "mean_value"]
    rows = []
    for aggregation, column in (("min_price", "min_price"), ("median_price", "median_price")):
        series = sensitivity[column].astype(float)
        if series.empty:
            rows.append({"aggregation": aggregation, "n_units": 0, **{c: np.nan for c in columns[2:]}})
            continue
        quantiles = series.quantile([0.01, 0.05, 0.50, 0.75, 0.95, 0.99])
        rows.append({
            "aggregation": aggregation, "n_units": int(series.count()), "min_value": series.min(),
            "p1": quantiles[0.01], "p5": quantiles[0.05], "p50": quantiles[0.50], "p75": quantiles[0.75],
            "p95": quantiles[0.95], "p99": quantiles[0.99], "max_value": series.max(), "mean_value": series.mean(),
        })
    return pd.DataFrame(rows, columns=columns)


def _observation_counts_by_calendar_flags(counts: "pd.DataFrame", calendar: "pd.DataFrame") -> "pd.DataFrame":
    """Dem observation MAIN theo nhom co calendar cua ngay check-in. `counts` (checkin_date, city, n_observations) x `calendar`
    (1 dong duy nhat / (checkin_date, city)) - merge KHONG nhan dong (assert). city '(unknown)' khong co calendar -> co False."""
    flag_columns = list(holidays._FLAG_COLUMNS)
    merged = counts.merge(calendar, on=["checkin_date", "city"], how="left", validate="many_to_one")
    for column in flag_columns:
        merged[column] = merged[column].fillna(False).astype(bool)
    grouped = merged.groupby(flag_columns, as_index=False).agg(
        n_observations=("n_observations", "sum"), n_checkin_date_city_cells=("n_observations", "size"))
    total = grouped["n_observations"].sum()
    grouped["share_of_observations"] = grouped["n_observations"] / total if total else np.nan
    if int(total) != int(counts["n_observations"].sum()):
        raise RuntimeError(f"holiday join lam nhan/mat observation: {int(counts['n_observations'].sum())} -> {int(total)}")
    return grouped


def _cohort_attrition(data: dict[str, Any]) -> "pd.DataFrame":
    table = metrics.cohort_attrition_table(data["cohort_history_versions"])
    keep = ["cohort_version", "effective_from_crawl_date", "size", "size_change", "change_type", "members_sha256"]
    return table[[c for c in keep if c in table.columns]]


def _availability(data: dict[str, Any], metric_id: str) -> "pd.DataFrame":
    return metrics.item_status_rates(data["m"][metric_id])


def _effective_items(data: dict[str, Any]) -> "pd.DataFrame":
    """Owned terminal item frame with one cohort-aware identity contract for every item metric."""
    items = data["resolved_items_main"].copy()
    items["city"] = items["effective_city"].fillna("(unknown)")
    items["hotel_id_effective"] = items["effective_hotel_id"].fillna("(unattributed)")
    return items


def _effective_availability_tables(data: dict[str, Any]) -> dict[str, "pd.DataFrame"]:
    items = _effective_items(data)

    def rates(group_cols=()):
        return metrics.item_status_rates(metrics.item_status_counts_from_rows(items, group_cols=group_cols))

    return {
        "item_availability_overall": rates(),
        "item_availability_by_city": rates(("city",)),
        "item_availability_by_hotel": rates(("hotel_id_effective", "city")).rename(
            columns={"hotel_id_effective": "hotel_id"}),
        "item_availability_by_checkin_month": rates(("checkin_month",)),
        "item_availability_by_lead_time_bucket": metrics.order_lead_time_bucket_rows(
            rates(("lead_time_bucket",))),
        "item_availability_by_crawl_date_hotel": rates(("crawl_date", "hotel_id_effective")).rename(
            columns={"hotel_id_effective": "hotel_id"}),
    }


def _item_grain_coverage_tables(data: dict[str, Any]) -> dict[str, "pd.DataFrame"]:
    """Primary check-in/calendar coverage at owned-item grain (not room-option weighted)."""
    items = _effective_items(data)
    month = metrics.item_coverage_count(items, group_cols=("checkin_month",))
    weekday = metrics.item_coverage_count(
        items, group_cols=("weekday_number", "weekday", "is_weekend_fri_sat"))
    weekday = weekday.sort_values("weekday_number").reset_index(drop=True)
    lead = metrics.order_lead_time_bucket_rows(
        metrics.item_coverage_count(items, group_cols=("lead_time_bucket",)))
    lead_city_source = metrics.item_coverage_count(
        items, group_cols=("city", "source_code", "lead_time_bucket"))
    lead_city_source = (
        lead_city_source.assign(_rank=lead_city_source["lead_time_bucket"].map(
            {label: i for i, label in enumerate(metrics.LEAD_TIME_BUCKET_ORDER)}).fillna(999))
        .sort_values(["city", "source_code", "_rank", "lead_time_bucket"])
        .drop(columns="_rank").reset_index(drop=True)
    )

    flags = list(holidays._FLAG_COLUMNS)
    calendar = data["checkin_calendar"].rename(columns={"city": "effective_city"})
    joined = items.merge(calendar[["checkin_date", "effective_city", *flags]],
                         on=["checkin_date", "effective_city"], how="left", validate="many_to_one")
    for column in flags:
        joined[column] = joined[column].fillna(False).astype(bool)
    calendar_items = joined.groupby(flags, as_index=False).agg(
        n_items=("item_id", "size"),
        n_distinct_checkin_dates=("checkin_date", "nunique"),
    )
    # Count unique (checkin_date, city) cells per flag group without weighting by number of hotels.
    cells = joined[["checkin_date", "effective_city", *flags]].drop_duplicates()
    cell_counts = cells.groupby(flags, as_index=False).size().rename(columns={"size": "n_checkin_date_city_cells"})
    calendar_items = calendar_items.merge(
        cell_counts, on=flags, how="left", validate="one_to_one")
    return {
        "item_checkin_month_distribution_main": month,
        "item_checkin_weekday_distribution_main": weekday,
        "item_lead_time_bucket_distribution_main": lead,
        "item_lead_time_bucket_distribution_by_city_source_main": lead_city_source,
        "item_calendar_coverage_main": calendar_items,
    }


def _series_evidence_runs_share(data: dict[str, Any]) -> "pd.DataFrame":
    distribution = data["m"]["series_evidence_runs_distribution"]
    by_city = metrics.evidence_runs_share(distribution, group_cols=("city",))
    overall = metrics.evidence_runs_share(distribution).assign(city="(all)")
    return pd.concat([overall[by_city.columns], by_city], ignore_index=True)


def _reference_tables(data: dict[str, Any]) -> dict[str, "pd.DataFrame"]:
    m = data["m"]
    return {
        "reference_exact_key_coverage_main": metrics.exact_approved_key_observation_coverage(m["reference_observation_match_main"]),
        "reference_exact_key_coverage_raw": metrics.exact_approved_key_observation_coverage(m["reference_observation_match_raw"]),
        "reference_series_exists_coverage_raw": metrics.series_with_approved_reference_coverage(
            m["reference_series_exists_observation_coverage_raw"]),
        # GPT file 11 muc 6.1: bang bucket LEGACY (0-3 gop) doi chieu TRUC TIEP dinh dang bang lich su CLAUDE.md muc 7.2.
        "reference_series_exists_coverage_raw_legacy_bucket": metrics.series_with_approved_reference_coverage(
            m["reference_series_exists_observation_coverage_raw_legacy_bucket"]),
        "reference_item_level_availability_by_lead_time": metrics.item_level_exact_reference_availability(
            m["reference_item_level_availability_main"]),
    }


def _collision_tables(data: dict[str, Any]) -> dict[str, "pd.DataFrame"]:
    pairs = data["collision_item_pairs_effective"]
    detail, coverage = data["collision_option_detail"], data["collision_pair_coverage"]
    return {
        "collision_item_status_concordance": metrics.collision_item_status_concordance(pairs),
        "collision_item_summary": metrics.collision_item_summary(pairs),
        "collision_item_time_diff_stratification": metrics.collision_item_time_diff_stratification(pairs),
        "collision_option_detail": detail,
        "collision_option_pair_coverage": coverage,
        "collision_option_summary": metrics.collision_option_summary(detail),
        "collision_option_coverage_summary": metrics.collision_option_coverage_summary(coverage),
        "collision_option_time_diff_stratification": metrics.collision_option_time_diff_stratification(detail),
    }


def _reconciliation_table(input_manifest: dict[str, Any]) -> "pd.DataFrame":
    return pd.DataFrame(input_manifest["reconciliation"], columns=["check", "eda_value", "validation_report_value", "match"])


def _protocol_exceptions(data: dict[str, Any]) -> "pd.DataFrame":
    classified = data["protocol_classified"]
    exceptions = classified[classified["outcome"] != "owner_success"]
    return exceptions[["owner_source", "crawl_date", "schedule_slot", "checkin_date", "hotel_id", "outcome"]].reset_index(drop=True)


def compute_wave_a_tables(data: dict[str, Any], input_manifest: dict[str, Any]) -> dict[str, "pd.DataFrame"]:
    """Tinh TOAN BO bang Wave A tu `data` (dau ra `collect_wave_a_data`) o MOT noi duy nhat. Tra ve dung tap khoa
    `publication.PUBLISHED_TABLES`. KHONG mutate `data` va KHONG giu frame observation-level: moi bang la ket qua SQL aggregate da co san
    trong `data["m"]` hoac derived nho tu chung (pandas tren bang da aggregate)."""
    m = data["m"]
    run_duration = m["run_duration_and_throughput"]
    effective_items = _effective_items(data)
    availability_tables = _effective_availability_tables(data)
    item_coverage_tables = _item_grain_coverage_tables(data)
    tables: dict[str, "pd.DataFrame"] = {
        # 7.1
        "preflight_core_counts": m["preflight_core_counts"], "preflight_reconciliation": _reconciliation_table(input_manifest),
        "preflight_import_sources": m["preflight_import_sources"], "preflight_rejections": m["preflight_rejections"],
        "observation_date_ranges_main": m["observation_date_ranges_main"],
        # 7.2
        "ownership_by_source_status_reason": m["ownership_by_source_status_reason"],
        "run_item_observation_by_source_crawl_date": m["run_item_observation_by_source_crawl_date"],
        "raw_vs_main_by_source": m["raw_vs_main_by_source"],
        "protocol_outcome_rates_by_source_date": metrics.protocol_outcome_rates_by_source_date(data["protocol_classified"]),
        "collision_item_pairs": data["collision_item_pairs_effective"],
        **_collision_tables(data),
        # 7.3
        "run_duration_and_throughput": run_duration, "finish_hour_distribution": metrics.finish_hour_distribution(run_duration),
        "run_day_status_counts_raw": m["run_day_status_counts_raw"], "run_day_error_code_counts_raw": m["run_day_error_code_counts_raw"],
        "run_day_operational_flags": metrics.daily_operational_anomaly_flags(m["run_day_status_counts_raw"], run_duration),
        # 7.4
        "active_hotel_by_crawl_date_source": metrics.active_hotels_from_effective_items(effective_items),
        "active_hotel_by_crawl_date_source_city": metrics.active_hotels_from_effective_items(effective_items, by_city=True),
        "checkin_dates_tracked_by_crawl_date_source": m["checkin_dates_tracked_by_crawl_date_source"],
        "checkin_month_distribution_main": m["checkin_month_distribution_main"],
        "checkin_weekday_distribution_main": m["checkin_weekday_distribution_main"],
        "lead_time_bucket_distribution_main": m["lead_time_bucket_distribution_main"],
        "crawl_date_lead_time_bucket_heatmap": m["crawl_date_lead_time_bucket_heatmap"],
        "cohort_attrition_by_version": _cohort_attrition(data),
        **item_coverage_tables,
        # 7.5
        "protocol_continuity_summary": metrics.protocol_continuity(data["protocol_classified"], group_cols=("owner_source",)),
        "protocol_continuity_exceptions": _protocol_exceptions(data),
        "protocol_continuity_unattributed_errors": data["protocol_unattributed_errors"],
        "canonical_series_turnover_by_series_main": m["canonical_series_facts_main"],
        "canonical_series_turnover_joint_main": metrics.turnover_joint(m["canonical_series_facts_main"]),
        "canonical_series_turnover_by_observed_days": metrics.turnover_by_observed_days(metrics.turnover_joint(m["canonical_series_facts_main"])),
        "canonical_series_max_gap_distribution": metrics.max_gap_distribution(metrics.turnover_joint(m["canonical_series_facts_main"])),
        "canonical_series_median_gap_distribution": metrics.median_gap_distribution(m["canonical_series_facts_main"]),
        "canonical_series_turnover_sample_main": data["turnover_sample"],
        # 7.6
        "lead_time_bucket_distribution_by_city_source_main": m["lead_time_bucket_distribution_by_city_source_main"],
        "holiday_calendar_flags_by_checkin_date_city": data["checkin_calendar"],
        "observation_counts_by_calendar_flags_main": _observation_counts_by_calendar_flags(
            m["observation_counts_by_checkin_date_city_main"], data["checkin_calendar"]),
        "vn_holidays_events_audit": data["holiday_csv"].events,
        # 7.7
        "price_distribution_overall_main": m["price_distribution_overall_main"], "price_distribution_overall_raw": m["price_distribution_overall_raw"],
        "price_distribution_by_city_main": m["price_distribution_by_city_main"],
        "price_distribution_by_lead_time_bucket_main": m["price_distribution_by_lead_time_bucket_main"],
        "price_distribution_by_weekday_main": m["price_distribution_by_weekday_main"],
        "price_distribution_by_calendar_flags_main": m["price_distribution_by_calendar_flags_main"],
        "price_box_stats_by_city_main": m["price_box_stats_by_city_main"], "price_hotel_dispersion_main": m["price_hotel_dispersion_main"],
        "price_sensitivity_by_series_main": m["price_sensitivity_by_series_main"],
        "price_sensitivity_summary_main": _price_sensitivity_summary(m["price_sensitivity_by_series_main"]),
        "price_histogram_linear_main": m["price_histogram_linear_main"], "price_histogram_log10_main": m["price_histogram_log10_main"],
        "price_outlier_summary_by_hotel_main": m["price_outlier_summary_by_hotel_main"],
        "price_outlier_sample_main": m["price_outlier_sample_main"],
        # 7.8
        **availability_tables,
        # 7.9
        "missingness_overall_available_observations": metrics.missingness_overall(m["missingness_available_observations"]),
        "missingness_available_observations": m["missingness_available_observations"],
        "missingness_by_selector_version": m["missingness_by_selector_version"],
        "missingness_by_crawl_date": m["missingness_by_crawl_date"], "missingness_by_city": m["missingness_by_city"],
        "missingness_by_item_status_sold_out": m["missingness_by_item_status_sold_out"],
        "artifact_completeness_by_source_crawl_date": m["artifact_completeness_by_source_crawl_date"],
        # 7.10
        "reference_approval_by_city_month": m["reference_approval_by_city_month"],
        "reference_status_evidence_summary": m["reference_status_evidence_summary"],
        "reference_uniqueness_per_series": m["reference_uniqueness_per_series"],
        "reference_candidate_coverage_summary": m["reference_candidate_coverage_summary"],
        **_reference_tables(data),
        # 7.11 / 7.12
        "quality_findings": build_quality_findings(data),
        "dataset_readiness_by_horizon": metrics.readiness_by_horizon(m["canonical_series_facts_main"]),
        "history_length_by_hotel_checkin_main": m["history_length_by_hotel_checkin_main"],
        "series_evidence_runs_distribution": m["series_evidence_runs_distribution"],
        "series_evidence_runs_share": _series_evidence_runs_share(data),
        # Wave B guard
        "wave_b_dataset_version_readiness": m["wave_b_dataset_version_readiness"],
    }
    unexpected = set(tables) ^ set(publication.PUBLISHED_TABLES)
    if unexpected:
        raise RuntimeError(f"compute_wave_a_tables() lech publication.PUBLISHED_TABLES: {sorted(unexpected)}")
    return tables


def write_wave_a_tables(tables: dict[str, "pd.DataFrame"], analysis_dir: Path) -> None:
    """Ghi TOAN BO bang tu `compute_wave_a_tables()` ra CSV - NOI DUY NHAT ghi bang cua Wave A - kem `TABLE_METADATA.csv` (scope, grain,
    denominator, so dong/cot cua tung bang, plan 7.2: 'moi ty le phai co numerator, denominator va dinh nghia denominator trong table
    metadata'). Bang `root_level` nam o ROOT `analysis_dir`, con lai duoi `tables/`."""
    metadata_rows = []
    for name, table in tables.items():
        spec = publication.PUBLISHED_TABLES[name]
        target = analysis_dir / publication.table_artifact_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(target, index=False)
        metadata_rows.append({
            "table": name, "artifact_path": publication.table_artifact_path(name), "metric_id": spec.metric_id, "plan_section": spec.section,
            "scope": spec.scope, "grain": spec.grain, "denominator": spec.denominator,
            "n_rows": int(len(table)), "n_columns": int(table.shape[1]),
            "file_size_bytes": int(target.stat().st_size),
            "columns": ", ".join(map(str, table.columns)),
        })
    pd.DataFrame(metadata_rows).to_csv(analysis_dir / "TABLE_METADATA.csv", index=False)


def save_figure(fig: Any, figures_dir: Path, name: str, *, dpi: int = 120) -> Path:
    """Luu hinh `name` vao `figures_dir/<name>.png` - ten PHAI thuoc `publication.PUBLISHED_FIGURES` (khong hinh 'la' ngoai coverage
    matrix). Khong dong figure (notebook con `plt.show()`)."""
    path = Path(figures_dir) / publication.figure_artifact_path(name).split("/", 1)[1]
    fig.savefig(path, dpi=dpi)
    return path


def write_coverage_matrix_artifacts(analysis_dir: Path) -> "pd.DataFrame":
    """Ghi `EDA_COVERAGE_MATRIX.md` + `.csv` (GPT file 11 muc 5) - artifact THAT nam trong artifact manifest. Tra ve DataFrame de report dung."""
    matrix = coverage_matrix.coverage_matrix_dataframe()
    coverage_matrix.write_coverage_matrix_md(matrix, analysis_dir / "EDA_COVERAGE_MATRIX.md")
    matrix.to_csv(analysis_dir / "EDA_COVERAGE_MATRIX.csv", index=False)
    return matrix


def write_eda_summary(data: dict[str, Any], analysis_dir: Path, tables: dict[str, "pd.DataFrame"] | None = None) -> None:
    snapshot: db.WarehouseSnapshot = data["snapshot"]
    protocol_summary = metrics.protocol_continuity(data["protocol_classified"], group_cols=("owner_source",))
    availability = (
        tables["item_availability_overall"] if tables is not None
        else _effective_availability_tables(data)["item_availability_overall"]
    )
    slowest = sorted(data["metric_seconds"].items(), key=lambda kv: kv[1], reverse=True)[:10]
    matrix = coverage_matrix.coverage_matrix_dataframe()
    summary = {
        "database": snapshot.database, "batch_id": snapshot.batch_id,
        "core_counts": data["core_counts"].iloc[0].to_dict(),
        "item_availability_overall": availability.iloc[0].to_dict(),
        "protocol_continuity_by_source": protocol_summary.to_dict("records"),
        "reference_approved_series": int(data["m"]["reference_approval_by_city_month"]["approved"].sum()),
        "wave_b_ready": bool(data["m"]["wave_b_dataset_version_readiness"]["ready"].any())
        if len(data["m"]["wave_b_dataset_version_readiness"]) else False,
        "coverage_matrix": {"n_bullets": int(len(matrix)), "n_missing": int(len(coverage_matrix.missing_required_rows(matrix)))},
        "n_published_tables": len(tables) if tables is not None else len(publication.PUBLISHED_TABLES),
        "n_published_figures": len(publication.PUBLISHED_FIGURES),
        # GPT review 12 eda M6: "ghi peak-memory/runtime vao summary de biet pipeline co on cho
        # rebuild cuoi ky lon hon nhieu hay khong". `peak_memory_mb` lay o DAY (cuoi notebook) de
        # peak_wset (Windows, khong giam) phan anh GAN NHU toan bo pipeline, khong chi rieng collect.
        "pipeline_collect_seconds": data.get("pipeline_collect_seconds"),
        "slowest_metrics_seconds": dict(slowest),
        "peak_memory_mb": _peak_memory_mb(),
    }
    artifacts.atomic_write_json(analysis_dir / "eda_summary.json", summary)


def write_eda_report_and_dictionary(
    data: dict[str, Any], tables: dict[str, "pd.DataFrame"], analysis_dir: Path, *, input_manifest: dict[str, Any] | None = None,
) -> None:
    """EDA_REPORT.md/DATA_DICTIONARY.md/EDA_COVERAGE_MATRIX.md VOI NOI DUNG THAT - moi con so tinh TRUC TIEP tu `data`/`tables` cua CHINH
    lan chay nay (GPT review 12 eda M4). Chi goi bo output la 'full Wave A' khi coverage matrix khong con muc required nao thieu (file 11
    muc 6.5). Logic render nam o `report.py`."""
    import report

    matrix = write_coverage_matrix_artifacts(analysis_dir)
    report.write_report(data, tables, analysis_dir, matrix=matrix, input_manifest=input_manifest)
    report.write_dictionary(data, tables, analysis_dir)
