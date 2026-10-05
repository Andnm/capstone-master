"""Step `validation` + gate PASS (spec muc 17, 3b buoc 7-8). Chi DOC; tra `{ok, checks, failed, numbers}`.

Gom: 8 query integrity xuyen bang (spec muc 4), cac kiem tra them (item khong training-eligible, gia/lead/checkout, daily dedup,
cong thuc split/purge, anomaly), kiem Parquet (doc lai, dem dong, hash file, hash noi dung, cot khong cam), doi chieu so label
Parquet <-> DB, va PASS gate (split hop le, label usable > 0 o cac (horizon, split) bat buoc theo cau hinh).
Moi check la mot ket qua (khong crash): batch chi PASS khi MOI check ok - khong ep PASS, khong ha chuan sau khi thay du lieu.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from . import env  # noqa: F401
from .anomaly import AnomalyReplayError, replay_registry
from .db import fetch_all, scalar
from .export import SAMPLES_FILE, content_sha256, file_sha256
from .feature_spec import FORBIDDEN_FEATURES, HORIZONS
from .features import output_columns
from .samples import CITIES

_ZERO_CHECKS: tuple[tuple[str, str], ...] = (
    # --- spec muc 4 (8 query)
    ("sample_la_observation_da_match_chon",
     "SELECT COUNT(*) FROM ml_samples s JOIN price_observations po ON po.record_id=s.record_id "
     "JOIN ml_item_reference_matches m ON m.dataset_version=s.dataset_version AND m.crawl_run_item_id=po.crawl_run_item_id "
     "WHERE s.dataset_version=%(dv)s AND (m.selected_record_id IS NULL OR m.selected_record_id<>s.record_id)"),
    ("match_selected_record_thuoc_dung_item",
     "SELECT COUNT(*) FROM ml_item_reference_matches m JOIN price_observations po ON po.record_id=m.selected_record_id "
     "WHERE m.dataset_version=%(dv)s AND m.selected_record_id IS NOT NULL AND po.crawl_run_item_id<>m.crawl_run_item_id"),
    ("approving_item_dung_run_hotel_checkin",
     "SELECT COUNT(*) FROM ml_reference_assignments a JOIN crawl_run_items cri ON cri.id=a.approving_item_warehouse_id "
     "WHERE a.dataset_version=%(dv)s AND (cri.crawl_run_id<>a.approving_run_warehouse_id OR cri.hotel_id IS NULL "
     "OR cri.hotel_id<>a.hotel_id OR cri.checkin_date<>a.checkin_date)"),
    ("sample_truoc_approved_at",
     "SELECT COUNT(*) FROM ml_samples s JOIN ml_reference_assignments a ON a.id=s.ml_reference_assignment_id AND a.dataset_version=s.dataset_version "
     "JOIN price_observations po ON po.record_id=s.record_id WHERE s.dataset_version=%(dv)s AND po.observed_at<a.approved_at"),
    ("sample_khac_hotel_checkin_assignment",
     "SELECT COUNT(*) FROM ml_samples s JOIN price_observations po ON po.record_id=s.record_id "
     "JOIN ml_reference_assignments a ON a.id=s.ml_reference_assignment_id AND a.dataset_version=s.dataset_version "
     "WHERE s.dataset_version=%(dv)s AND (po.hotel_id<>a.hotel_id OR po.checkin_date<>a.checkin_date)"),
    ("match_item_khac_hotel_checkin_assignment",
     "SELECT COUNT(*) FROM ml_item_reference_matches m JOIN crawl_run_items cri ON cri.id=m.crawl_run_item_id "
     "JOIN ml_reference_assignments a ON a.id=m.ml_reference_assignment_id AND a.dataset_version=m.dataset_version "
     "WHERE m.dataset_version=%(dv)s AND (cri.hotel_id IS NULL OR cri.hotel_id<>a.hotel_id OR cri.checkin_date<>a.checkin_date)"),
    ("assignment_version_khac_manifest",
     "SELECT COUNT(*) FROM ml_reference_assignments a JOIN dataset_build_manifests d ON d.dataset_version=a.dataset_version "
     "WHERE a.dataset_version=%(dv)s AND a.reference_algorithm_version<>d.reference_algorithm_version"),
    ("label_source_target_khong_hop_le", None),                      # query UNION ALL, dung o ham rieng
    # --- kiem tra them
    ("sample_item_khong_training_eligible",
     "SELECT COUNT(*) FROM ml_samples s JOIN price_observations po ON po.record_id=s.record_id "
     "JOIN crawl_run_items cri ON cri.id=po.crawl_run_item_id JOIN crawl_runs cr ON cr.id=cri.crawl_run_id "
     "LEFT JOIN etl_item_map im ON im.warehouse_item_id=cri.id AND im.import_batch_id=%(batch)s "
     "LEFT JOIN etl_run_map rm ON rm.warehouse_run_id=cr.id AND rm.import_batch_id=%(batch)s "
     "WHERE s.dataset_version=%(dv)s AND NOT (cr.status='completed' AND rm.include_training=TRUE AND im.include_training=TRUE AND cri.status='success')"),
    ("sample_gia_khong_hop_le",
     "SELECT COUNT(*) FROM ml_samples s JOIN price_observations po ON po.record_id=s.record_id "
     "WHERE s.dataset_version=%(dv)s AND (po.is_sold_out=1 OR po.price_per_night IS NULL OR po.price_per_night<=0)"),
    ("sample_checkout_hoac_lead_sai",
     "SELECT COUNT(*) FROM ml_samples s JOIN price_observations po ON po.record_id=s.record_id "
     "WHERE s.dataset_version=%(dv)s AND (po.checkout_date<>DATE_ADD(po.checkin_date, INTERVAL 1 DAY) "
     "OR DATEDIFF(po.checkin_date, s.vn_observation_date)<0)"),
    ("sample_ngoai_5_thanh_pho",
     "SELECT COUNT(*) FROM ml_samples s JOIN price_observations po ON po.record_id=s.record_id JOIN hotels h ON h.hotel_id=po.hotel_id "
     "WHERE s.dataset_version=%(dv)s AND (h.city IS NULL OR h.city NOT IN (%(c0)s,%(c1)s,%(c2)s,%(c3)s,%(c4)s))"),
    ("vn_date_hoac_prediction_time_lech_observation",
     "SELECT COUNT(*) FROM ml_samples s JOIN price_observations po ON po.record_id=s.record_id "
     "WHERE s.dataset_version=%(dv)s AND (s.prediction_time<>po.observed_at OR s.vn_observation_date<>DATE(DATE_ADD(po.observed_at, INTERVAL 7 HOUR)))"),
    ("nhieu_hon_1_snapshot_chon_moi_ngay",
     "SELECT COUNT(*) FROM (SELECT ml_reference_assignment_id, vn_observation_date FROM ml_samples WHERE dataset_version=%(dv)s "
     "AND is_daily_snapshot_selected=TRUE GROUP BY 1,2 HAVING COUNT(*)>1) d"),
    ("mau_bi_loai_van_co_nhan",
     "SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%(dv)s AND is_daily_snapshot_selected=FALSE AND "
     "(has_label_h1 OR has_label_h3 OR has_label_h7 OR has_label_h14)"),
    ("mau_bi_loai_thieu_ly_do",
     "SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%(dv)s AND is_daily_snapshot_selected=FALSE AND daily_snapshot_reason IS NULL"),
)


def _params(dv: str, batch: str) -> dict[str, Any]:
    return {"dv": dv, "batch": batch, **{f"c{i}": city for i, city in enumerate(CITIES)}}


def _label_union_sql() -> str:
    parts = [f"SELECT dataset_version, ml_reference_assignment_id, vn_observation_date, is_daily_snapshot_selected AS sel, "
             f"label_source_record_id_h{k} AS target_record_id, {k} AS horizon_days FROM ml_samples "
             f"WHERE dataset_version=%(dv)s AND label_source_record_id_h{k} IS NOT NULL" for k in HORIZONS]
    return ("SELECT COUNT(*) FROM (" + " UNION ALL ".join(parts) + ") labels JOIN ml_samples target "
            "ON target.dataset_version=labels.dataset_version AND target.record_id=labels.target_record_id "
            "WHERE target.ml_reference_assignment_id<>labels.ml_reference_assignment_id "
            "OR target.vn_observation_date<>DATE_ADD(labels.vn_observation_date, INTERVAL labels.horizon_days DAY) "
            "OR labels.sel<>TRUE OR target.is_daily_snapshot_selected<>TRUE")


def _split_checks(manifest: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    te, ve, purge = manifest["split_train_end"], manifest["split_validation_end"], int(manifest["purge_gap_days"])
    base = {"dv": manifest["dataset_version"], "te": te, "ve": ve, "p": purge}
    return [
        ("split_train_sai", "SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%(dv)s AND vn_observation_date<=%(te)s AND (split IS NULL OR split<>'train')", base),
        ("split_purge1_khong_null",
         "SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%(dv)s AND vn_observation_date>%(te)s AND vn_observation_date<=DATE_ADD(%(te)s, INTERVAL %(p)s DAY) AND split IS NOT NULL", base),
        ("split_validation_sai",
         "SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%(dv)s AND vn_observation_date>=DATE_ADD(%(te)s, INTERVAL %(p)s+1 DAY) AND vn_observation_date<=%(ve)s AND (split IS NULL OR split<>'validation')", base),
        ("split_purge2_khong_null",
         "SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%(dv)s AND vn_observation_date>%(ve)s AND vn_observation_date<=DATE_ADD(%(ve)s, INTERVAL %(p)s DAY) AND split IS NOT NULL", base),
        ("split_test_sai",
         "SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%(dv)s AND vn_observation_date>=DATE_ADD(%(ve)s, INTERVAL %(p)s+1 DAY) AND (split IS NULL OR split<>'test')", base),
    ]


def validate_dataset(conn, *, dataset_version: str, config: dict[str, Any], manifest: dict[str, Any], output_root: Path) -> dict[str, Any]:
    batch = config["import_batch_id"]
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: Any) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    params = _params(dataset_version, batch)
    for name, sql in _ZERO_CHECKS:
        count = int(scalar(conn, sql if sql else _label_union_sql(), params) or 0)
        add(name, count == 0, count)
    for name, sql, split_params in _split_checks(manifest):
        count = int(scalar(conn, sql, split_params) or 0)
        add(name, count == 0, count)
    # anomaly: replay lai, doi chieu checksum da luu va khong sample nao thuoc tap bi loai
    try:
        replay = replay_registry(conn, config=config)
        stored = manifest.get("anomaly_source_member_checksums")
        add("anomaly_checksum_khop_manifest", stored == json.loads(json.dumps(replay.manifest_checksums())), "so voi replay lai")
        excluded = sorted(replay.excluded_record_ids)
        leaked = 0
        for start in range(0, len(excluded), 500):
            part = excluded[start:start + 500]
            leaked += int(scalar(conn, f"SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%s AND record_id IN ({','.join(['%s'] * len(part))})",
                                 (dataset_version, *part)) or 0)
        add("sample_thuoc_tap_anomaly_bi_loai", leaked == 0, leaked)
    except AnomalyReplayError as exc:
        add("anomaly_replay", False, str(exc))
    # PASS gate: split hop le
    te, ve = manifest["split_train_end"], manifest["split_validation_end"]
    add("split_bien_hop_le", te is not None and ve is not None and te < ve, {"train_end": str(te), "validation_end": str(ve)})
    counts = {r["split"]: r["n"] for r in fetch_all(conn, "SELECT split, COUNT(*) n FROM ml_samples WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE "
                                                     "GROUP BY split", (dataset_version,)) if r["split"]}
    add("split_khong_rong", all(counts.get(s, 0) > 0 for s in ("train", "validation", "test")), counts)
    # Parquet + nhan
    numbers: dict[str, Any] = {"selected_samples_by_split": counts}
    try:
        numbers.update(_check_outputs(conn, add, dataset_version=dataset_version, config=config, manifest=manifest, output_root=output_root))
    except Exception as exc:  # noqa: BLE001 - check that bai la mot ket qua
        add("parquet_output", False, f"{type(exc).__name__}: {exc}")
    failed = [c["name"] for c in checks if not c["ok"]]
    return {"ok": not failed, "failed": failed, "checks": checks, "numbers": numbers}


def _check_outputs(conn, add, *, dataset_version: str, config: dict[str, Any], manifest: dict[str, Any], output_root: Path) -> dict[str, Any]:
    out = output_root / dataset_version
    stored = manifest.get("output_parquet_sha256_json") or {}
    add("manifest_co_checksum_output", bool(stored) and SAMPLES_FILE in stored, sorted(stored))
    path = out / SAMPLES_FILE
    add("parquet_ton_tai", path.exists(), str(path))
    if not path.exists():
        return {}
    frame = pd.read_parquet(path)
    columns = output_columns(config)
    add("parquet_cot_khop_hop_dong", list(frame.columns) == columns, f"{len(frame.columns)} cot")
    add("parquet_khong_co_feature_cam", not (set(FORBIDDEN_FEATURES) & set(frame.columns)), sorted(set(FORBIDDEN_FEATURES) & set(frame.columns)))
    selected = int(scalar(conn, "SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE", (dataset_version,)) or 0)
    add("parquet_so_dong_bang_mau_chon", len(frame) == selected, {"parquet": len(frame), "db": selected})
    for name, entry in stored.items():
        file_path = out / name
        add(f"hash_file_{name}", file_path.exists() and file_sha256(file_path) == entry["file_sha256"], entry["file_sha256"][:16])
    recomputed = content_sha256(frame)
    add("hash_noi_dung_khop", stored.get(SAMPLES_FILE, {}).get("content_sha256") == recomputed, recomputed[:16])
    numbers: dict[str, Any] = {"parquet_rows": int(len(frame))}
    required = config.get("pass_requirements", {}).get("required_label_splits", {})
    for k in HORIZONS:
        db_usable = _db_usable(conn, dataset_version, k)
        parquet_usable = {s: int((frame[f"label_usable_h{k}"] & (frame["split"] == s)).sum()) for s in ("train", "validation", "test")}
        add(f"label_usable_h{k}_khop_db", db_usable == parquet_usable, {"db": db_usable, "parquet": parquet_usable})
        numbers[f"label_usable_h{k}"] = parquet_usable
        needed = required.get(f"h{k}", [])
        empty = [s for s in needed if parquet_usable.get(s, 0) <= 0]
        add(f"label_h{k}_bat_buoc_co_mau", not empty, {"required": needed, "empty": empty})
    return numbers


def _db_usable(conn, dataset_version: str, horizon: int) -> dict[str, int]:
    rows = fetch_all(
        conn,
        f"SELECT s.split, COUNT(*) n FROM ml_samples s JOIN ml_samples t ON t.dataset_version=s.dataset_version AND t.record_id=s.label_source_record_id_h{int(horizon)} "
        f"WHERE s.dataset_version=%s AND s.is_daily_snapshot_selected=TRUE AND s.split IS NOT NULL AND t.split=s.split GROUP BY s.split", (dataset_version,))
    counts = {r["split"]: int(r["n"]) for r in rows}
    return {s: counts.get(s, 0) for s in ("train", "validation", "test")}


def verify_pass_outputs(conn, *, dataset_version: str, config: dict[str, Any], manifest: dict[str, Any], output_root: Path) -> list[str]:
    """`--apply` tren manifest PASS = no-op sau khi kiem lai file/hash (spec muc 18 buoc 7). Tra danh sach sai lech."""
    problems: list[str] = []

    def add(name: str, ok: bool, detail: Any) -> None:
        if not ok:
            problems.append(f"{name}: {detail}")

    _check_outputs(conn, add, dataset_version=dataset_version, config=config, manifest=manifest, output_root=output_root)
    return problems
