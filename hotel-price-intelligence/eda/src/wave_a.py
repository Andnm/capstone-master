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
from pathlib import Path
from typing import Any

import matplotlib
import mysql.connector
import nbclient
import nbformat
import numpy as np
import pandas as pd
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
OWNERSHIP_MANIFEST_PATH = HPI_DIR / "data" / "warehouse" / "ownership_manifest_20260916.json"
COHORT_HISTORY_PATH = HPI_DIR / "data" / "warehouse" / "cohort_history_20260916.json"
WAREHOUSE_VALIDATION_REPORT_PATH = HPI_DIR / "data" / "warehouse" / "reports" / "b20260916_2src.json"
PLAN_AUTHORITY_PATH = HPI_DIR / "EDA_CURATED_PLAN.md"

# GPT review 12 (eda) M5: cac path source code duoc hash khi chua commit (muc M6) - tuong doi REPO_ROOT,
# dung dinh dang voi `app.warehouse.provenance.GUARDED_PATHS`.
EDA_GUARDED_PATHS = (
    "hotel-price-intelligence/eda/src",
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
    """
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

        # 7.3 crawl operations/capacity
        data["run_duration"] = queries.run_metric("run_duration_and_throughput", conn, snapshot)

        # 7.4 hotel/check-in coverage
        data["active_hotel_by_date"] = queries.run_metric(
            "active_hotel_by_crawl_date_source", conn, snapshot)

        # 7.5 protocol continuity - phan DB truoc, phan file (ownership/cohort manifest) sau khi dong conn.
        # GPT review 12 eda M1: `source_run_dates`/`cutoff_date_by_source` KHONG con dung de GIOI HAN
        # truoc expected universe (do la bug cu lam mat han loai gap "missing_run") - chi dung SAU de
        # (a) lam cutoff tren cua window doc lap actual, va (b) phan loai 2 tang missing_source_run vs
        # missing_item_in_existing_run trong `classify_outcomes`.
        source_run_dates = queries.actual_crawl_dates_by_source(conn, snapshot)
        cutoff_date_by_source = queries.dump_taken_at_vn_date_by_source(conn, snapshot)
        protocol_actual = queries.run_metric("protocol_continuity_actual", conn, snapshot)

        # 7.7/7.8 gia + availability
        data["main_items"] = queries.run_metric("main_item_status", conn, snapshot)
        data["price_main"] = queries.run_metric("price_observations_main", conn, snapshot)

        # 7.9 missingness
        data["missingness"] = queries.run_metric("missingness_available_observations", conn, snapshot)

        # 7.10 reference
        data["ref_main"] = queries.run_metric("reference_observation_match_main", conn, snapshot)
        data["ref_raw"] = queries.run_metric("reference_observation_match_raw", conn, snapshot)
        data["ref_series_exists_raw"] = queries.run_metric(
            "reference_series_exists_observation_coverage_raw", conn, snapshot)
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

    # Tu day tro di KHONG can connection - file I/O thuan (ownership/cohort manifest, holiday CSV).
    expected = ps.expected_schedule(
        ownership_manifest_path, cohort_history_path, base_dir=cohort_history_base_dir,
        cutoff_date_by_source=cutoff_date_by_source,
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

    return data


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
    other_files = [EDA_DIR / "requirements-eda.txt", PLAN_AUTHORITY_PATH]
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


def build_input_manifest(
    data: dict[str, Any], *, notebook_source_path: Path,
    warehouse_validation_report_path: Path = WAREHOUSE_VALIDATION_REPORT_PATH,
    ownership_manifest_path: Path = OWNERSHIP_MANIFEST_PATH, cohort_history_path: Path = COHORT_HISTORY_PATH,
) -> dict[str, Any]:
    """Manifest DAY DU (GPT review 12 eda M6) - doi soat voi `<warehouse_validation_report_path>`
    that, khong chi assert non-terminal roi thoi. 3 path con lai override duoc cho dry-run fixture."""
    snapshot: db.WarehouseSnapshot = data["snapshot"]
    warehouse_report = json.loads(warehouse_validation_report_path.read_text(encoding="utf-8"))
    if warehouse_report.get("batch_id") != snapshot.batch_id:
        raise ValueError(
            f"warehouse validation report {warehouse_validation_report_path.name} la cua batch "
            f"{warehouse_report.get('batch_id')!r}, KHAC batch dang doc {snapshot.batch_id!r}."
        )
    core_counts = data["core_counts"].iloc[0].to_dict()
    report_counts = {
        "hotels": warehouse_report["steps"]["11_import"]["hotels"]["warehouse_distinct_hotels"],
        "price_observations": warehouse_report["steps"]["14_curated"]["price_observations"],
    }
    if int(core_counts["price_observations"]) != int(report_counts["price_observations"]):
        raise ValueError(
            f"preflight_core_counts.price_observations={core_counts['price_observations']} KHONG khop "
            f"validation report ({report_counts['price_observations']}) - warehouse co the da bi doi sau build."
        )

    price_main = data["price_main"]
    provenance = capture_code_provenance()
    return {
        **snapshot.to_manifest_dict(),
        "warehouse_validation_report_path": _path_for_manifest(warehouse_validation_report_path),
        "warehouse_validation_report_sha256": hashlib.sha256(
            warehouse_validation_report_path.read_bytes()).hexdigest(),
        "ownership_manifest_path": _path_for_manifest(ownership_manifest_path),
        "ownership_manifest_file_sha256": hashlib.sha256(ownership_manifest_path.read_bytes()).hexdigest(),
        "cohort_history_path": _path_for_manifest(cohort_history_path),
        "cohort_history_file_sha256": hashlib.sha256(cohort_history_path.read_bytes()).hexdigest(),
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


# ============================================================================== 4. GHI ARTIFACT (fail-if-exists, manifest cuoi cung)
def write_wave_a_tables(data: dict[str, Any], analysis_dir: Path) -> None:
    tables_dir = analysis_dir / "tables"
    data["ownership"].to_csv(tables_dir / "ownership_by_source_status_reason.csv", index=False)
    data["run_item_obs_by_date"].to_csv(tables_dir / "run_item_observation_by_source_crawl_date.csv", index=False)
    data["run_duration"].to_csv(tables_dir / "run_duration_and_throughput.csv", index=False)
    data["active_hotel_by_date"].to_csv(tables_dir / "active_hotel_by_crawl_date_source.csv", index=False)
    data["protocol_classified"].to_csv(tables_dir / "protocol_continuity_classified.csv", index=False)
    data["protocol_unattributed_errors"].to_csv(tables_dir / "protocol_continuity_unattributed_errors.csv", index=False)
    data["missingness"].to_csv(tables_dir / "missingness_available_observations.csv", index=False)
    data["ref_approval_by_city_month"].to_csv(tables_dir / "reference_approval_by_city_month.csv", index=False)

    quality_findings = build_quality_findings(data)
    quality_findings.to_csv(analysis_dir / "quality_findings.csv", index=False)

    presence = data["series_presence"]
    turnover = metrics.canonical_series_turnover(presence)
    turnover.to_csv(tables_dir / "canonical_series_turnover.csv", index=False)
    pairs = metrics.theoretical_horizon_pairs(presence)
    readiness = metrics.dataset_readiness_by_horizon(pairs)
    readiness.to_csv(analysis_dir / "dataset_readiness_by_horizon.csv", index=False)


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
    }
    artifacts.atomic_write_json(analysis_dir / "eda_summary.json", summary)


def write_eda_report_and_dictionary(data: dict[str, Any], analysis_dir: Path) -> None:
    """Bo khung EDA_REPORT.md/DATA_DICTIONARY.md voi cac bang/con so THAT - phan dien giai van ban se
    duoc GPT/nguoi review bo sung sau full run (khong tu bia narrative)."""
    snapshot: db.WarehouseSnapshot = data["snapshot"]
    (analysis_dir / "EDA_REPORT.md").write_text(
        f"# EDA Wave A Report - {snapshot.database} / {snapshot.batch_id}\n\n"
        f"Sinh tu doi mo bang du lieu that trong `tables/`, `quality_findings.csv`, `eda_summary.json`, "
        f"`dataset_readiness_by_horizon.csv`. Phan dien giai van ban se bo sung o vong review ke tiep.\n",
        encoding="utf-8",
    )
    (analysis_dir / "DATA_DICTIONARY.md").write_text(
        "# Data Dictionary (Wave A)\n\nXem `EDA_CURATED_PLAN.md` muc 9 cho khung bat buoc. "
        "Chi tiet semantic tung bien se dien day du o vong ke tiep.\n",
        encoding="utf-8",
    )
