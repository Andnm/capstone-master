"""Step `validation` + gate PASS (spec muc 17, 3b buoc 7-8). Chi DOC; tra `{ok, checks, failed, numbers}`.

Gom: 8 query integrity xuyen bang (spec muc 4), cac kiem tra them (item khong training-eligible, gia/lead/checkout, daily dedup,
cong thuc split/purge, anomaly), kiem Parquet (doc lai, dem dong, hash file, hash noi dung, cot khong cam), doi chieu so label
Parquet <-> DB, va PASS gate (split hop le, label usable > 0 o cac (horizon, split) bat buoc theo cau hinh).
Moi check la mot ket qua (khong crash): batch chi PASS khi MOI check ok - khong ep PASS, khong ha chuan sau khi thay du lieu.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd

from . import env  # noqa: F401
from .anomaly import AnomalyReplayError, replay_registry
from .bundle import verify_bundle
from .db import fetch_all, scalar
from .export import CALENDAR_MANIFEST, CALENDAR_SNAPSHOT, CONTRACT_NAME, CONTRACT_VERSION, SAMPLES_FILE, content_sha256, file_sha256
from .feature_spec import AUDIT_MATCH_COLUMNS, FORBIDDEN_FEATURES, HORIZONS
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
    # GPT review vong 1 DB-m1: moi item training-eligible thuoc series co assignment PHAI co dung mot quyet dinh match (exact/alias/unavailable/ambiguous).
    # Item success khong co observation se bien mat khoi _STREAM_SQL (inner join) -> bien thanh LO HONG IM LANG; check nay bien no thanh FAIL.
    ("item_eligible_khong_co_quyet_dinh_match",
     "SELECT COUNT(*) FROM crawl_run_items cri JOIN crawl_runs cr ON cr.id=cri.crawl_run_id AND cr.status='completed' "
     "JOIN etl_run_map rm ON rm.warehouse_run_id=cr.id AND rm.import_batch_id=%(batch)s AND rm.include_training=TRUE "
     "JOIN etl_item_map im ON im.warehouse_item_id=cri.id AND im.import_batch_id=%(batch)s AND im.include_training=TRUE "
     "JOIN ml_reference_assignments a ON a.dataset_version=%(dv)s AND a.hotel_id=cri.hotel_id AND a.checkin_date=cri.checkin_date "
     "LEFT JOIN ml_item_reference_matches m ON m.dataset_version=%(dv)s AND m.crawl_run_item_id=cri.id "
     "WHERE cri.status='success' AND m.crawl_run_item_id IS NULL"),
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
    identifiers = list(config["feature_config"]["identifier_columns"])
    add("parquet_dinh_danh_khop_config_hash", list(frame.columns[:len(identifiers)]) == identifiers, identifiers)
    dictionary_path = out / "data_dictionary.csv"
    dictionary_columns = list(pd.read_csv(dictionary_path)["column"]) if dictionary_path.exists() else None
    add("data_dictionary_khop_parquet", dictionary_columns == list(frame.columns),
        {"dictionary": None if dictionary_columns is None else len(dictionary_columns), "parquet": len(frame.columns)})
    add(**_split_policy_check(out))
    # co hotel_seen_in_train_hk phai khop dinh nghia: hotel co mau train label_usable_hk
    for k in HORIZONS:
        seen = set(frame.loc[frame[f"label_usable_h{k}"] & (frame["split"] == "train"), "hotel_id"])
        add(f"hotel_seen_in_train_h{k}_khop_dinh_nghia", bool((frame[f"hotel_seen_in_train_h{k}"] == frame["hotel_id"].isin(seen)).all()), len(seen))
    add("parquet_khong_co_feature_cam", not (set(FORBIDDEN_FEATURES) & set(frame.columns)), sorted(set(FORBIDDEN_FEATURES) & set(frame.columns)))
    add(**_strata_check(conn, frame, config, dataset_version, out))
    selected = int(scalar(conn, "SELECT COUNT(*) FROM ml_samples WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE", (dataset_version,)) or 0)
    add("parquet_so_dong_bang_mau_chon", len(frame) == selected, {"parquet": len(frame), "db": selected})
    for name, entry in stored.items():
        file_path = out / name
        add(f"hash_file_{name}", file_path.exists() and file_sha256(file_path) == entry["file_sha256"], entry["file_sha256"][:16])
    add(**_calendar_check(out, config, stored))
    add(**_contract_check(out, config, stored, dataset_version=dataset_version))
    add(**_official_gate_check(out, config))
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


_CONSISTENCY_KEYS = ("eligible_prediction_dates", "labeled_samples", "hotels_seen_in_train", "hotels_seen_in_train_per_city")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except ValueError:
        return None


def _contract_check(out: Path, config: dict[str, Any], stored: dict[str, Any], *, dataset_version: str) -> dict[str, Any]:
    """`dataset_contract.json` phai co trong checksum DB va khop config da ghim (horizon, purge, ma/config) va sufficiency_report that (GPT file 50 invariant 4)."""
    contract = _read_json(out / CONTRACT_NAME)
    sufficiency = _read_json(out / "sufficiency_report.json") or {}
    problems: list[str] = []
    if CONTRACT_NAME not in stored:
        problems.append("khong co trong checksum DB")
    if not isinstance(contract, dict):
        problems.append("thieu hoac khong doc duoc")
    else:
        from .config import PURPOSES, config_sha256
        expected = {"contract_version": CONTRACT_VERSION, "dataset_version": dataset_version, "purpose": config.get("purpose"),
                    "evaluation_horizons": config.get("evaluation_horizons"), "computed_label_horizons": config.get("computed_label_horizons"),
                    "purge_gap_days": config.get("purge_gap_days"), "builder_code_sha256": (config.get("builder_code") or {}).get("code_sha256"),
                    "builder_version": config.get("builder_version"), "build_config_sha256": config_sha256(config),
                    "calendar_sha256": (config.get("calendar_input") or {}).get("sha256")}
        problems += [f"{key}={contract.get(key)!r} != config {value!r}" for key, value in expected.items() if contract.get(key) != value]
        if contract.get("purpose") not in PURPOSES:
            problems.append(f"purpose={contract.get('purpose')!r} ngoai {list(PURPOSES)}")
        plan, purge = contract.get("split_plan") or {}, int(config.get("purge_gap_days") or 0)
        try:                                                        # thu tu + khoang cach split thuc (GPT file 52 muc 3)
            d = {k: dt.date.fromisoformat(str(plan[k])) for k in ("train_start", "train_end", "validation_start", "validation_end", "test_start", "test_end")}
            if not (d["train_start"] <= d["train_end"] < d["validation_start"] <= d["validation_end"] < d["test_start"] <= d["test_end"]):
                problems.append(f"split_plan sai thu tu: {plan}")
            elif (d["validation_start"] - d["train_end"]).days - 1 < purge or (d["test_start"] - d["validation_end"]).days - 1 < purge:
                problems.append(f"khoang cach giua cac split < purge_gap_days={purge}")
        except (KeyError, ValueError) as exc:
            problems.append(f"split_plan thieu/sai ngay ISO: {exc}")
        statuses = {name: entry.get("status") for name, entry in (sufficiency.get("horizons") or {}).items()}
        if contract.get("sufficiency_status") != statuses:
            problems.append(f"sufficiency_status {contract.get('sufficiency_status')} != sufficiency_report {statuses}")
    return {"name": "hop_dong_horizon_khop_config_va_sufficiency", "ok": not problems, "detail": problems or "ok"}


_MATCH_STATUSES = ("exact", "alias")


def _strata_check(conn, frame: pd.DataFrame, config: dict[str, Any], dataset_version: str, out: Path) -> dict[str, Any]:
    """Cot audit strata (GPT file 52 muc 2): (a) chi la dinh danh, khong nam trong nhom feature nao va dictionary ghi group=identifier; (b) status hop le,
    NULL <=> khong co nhan; (c) phan bo status cua sample khop DB (join selected_record_id, khong nhan doi dong)."""
    problems: list[str] = []
    feature_config = config["feature_config"]
    if sorted(feature_config.get("audit_only_columns") or []) != sorted(AUDIT_MATCH_COLUMNS):
        problems.append(f"audit_only_columns trong config {feature_config.get('audit_only_columns')} != {list(AUDIT_MATCH_COLUMNS)}")
    if not set(AUDIT_MATCH_COLUMNS) <= set(feature_config["identifier_columns"]):
        problems.append("cot audit khong nam trong identifier_columns")
    in_features = sorted(set(AUDIT_MATCH_COLUMNS) & {c for group in feature_config["groups"].values() for c in group})
    if in_features:
        problems.append(f"cot audit nam trong nhom feature: {in_features}")
    dictionary_path = out / "data_dictionary.csv"
    if dictionary_path.exists():
        groups = dict(zip(*[pd.read_csv(dictionary_path)[c] for c in ("column", "group")]))
        wrong = {c: groups.get(c) for c in AUDIT_MATCH_COLUMNS if groups.get(c) != "identifier"}
        if wrong:
            problems.append(f"dictionary: cot audit phai group=identifier, nhan {wrong}")
    prediction = frame["prediction_match_status"]
    if not prediction.isin(_MATCH_STATUSES).all():
        problems.append(f"prediction_match_status ngoai {list(_MATCH_STATUSES)}: {sorted(map(str, set(prediction[~prediction.isin(_MATCH_STATUSES)])))}")
    for k in HORIZONS:
        status, has = frame[f"label_match_status_h{k}"], frame[f"has_label_h{k}"].astype(bool)
        if status[~has].notna().any():
            problems.append(f"h{k}: label_match_status co gia tri khi khong co nhan")
        if not status[has].isin(_MATCH_STATUSES).all():
            problems.append(f"h{k}: nhan co label_match_status ngoai {list(_MATCH_STATUSES)}")
    db = fetch_all(conn, "SELECT m.match_status AS st, COUNT(*) AS n FROM ml_samples s JOIN ml_item_reference_matches m "
                         "ON m.dataset_version=s.dataset_version AND m.selected_record_id=s.record_id "
                         "WHERE s.dataset_version=%s AND s.is_daily_snapshot_selected=TRUE GROUP BY m.match_status", (dataset_version,))
    db_counts, parquet_counts = {r["st"]: int(r["n"]) for r in db}, {k: int(v) for k, v in prediction.value_counts().items()}
    if db_counts != parquet_counts:
        problems.append(f"phan bo prediction_match_status Parquet {parquet_counts} != DB {db_counts}")
    # Phan bo chi la tong ket: hoan vi exact/alias giua cac dong van giu nguyen phan bo => doi chieu TUNG record qua warehouse_record_id (GPT file 54 C53-M2).
    label_columns = ", ".join(f"s.label_source_record_id_h{k}" for k in HORIZONS)
    sample_rows = fetch_all(conn, f"SELECT s.record_id, {label_columns} FROM ml_samples s WHERE s.dataset_version=%s AND s.is_daily_snapshot_selected=TRUE", (dataset_version,))
    match_rows = fetch_all(conn, "SELECT m.selected_record_id, m.match_status FROM ml_item_reference_matches m "
                                 "WHERE m.dataset_version=%s AND m.selected_record_id IS NOT NULL", (dataset_version,))
    conn.commit()
    per_record = strata_per_record_problems(frame, sample_rows, match_rows)
    problems += per_record
    return {"name": "strata_audit_hop_le_va_khop_db", "ok": not problems, "detail": problems or {"prediction": parquet_counts, "per_record": "ok"}}


def strata_per_record_problems(frame: pd.DataFrame, sample_rows: list[dict[str, Any]], match_rows: list[dict[str, Any]]) -> list[str]:
    """Doi chieu tung dong Parquet (dinh danh = `warehouse_record_id`, KHONG theo thu tu dong) voi DB: status cua chinh sample = match theo selected_record_id; status cua
    label hK = match cua `ml_samples.label_source_record_id_hK` (NULL khi khong co nhan). Thieu/trung/khong khop deu la loi."""
    problems: list[str] = []
    status: dict[int, str] = {}
    duplicated_matches = 0
    for row in match_rows:
        record_id = int(row["selected_record_id"])
        duplicated_matches += record_id in status
        status[record_id] = str(row["match_status"])
    if duplicated_matches:
        problems.append(f"{duplicated_matches} selected_record_id xuat hien o nhieu dong match")
    ids = frame["warehouse_record_id"].astype("int64")
    if ids.duplicated().any():
        problems.append(f"{int(ids.duplicated().sum())} warehouse_record_id trung trong Parquet")
    db_samples = {int(row["record_id"]): row for row in sample_rows}
    if set(ids) != set(db_samples):
        problems.append(f"tap warehouse_record_id Parquet != mau chon trong DB (thieu {len(set(db_samples) - set(ids))}, thua {len(set(ids) - set(db_samples))})")
        return problems
    expected_prediction = ids.map(lambda record_id: status.get(int(record_id)))
    actual_prediction = frame["prediction_match_status"].astype("object")
    missing = int(expected_prediction.isna().sum())
    if missing:
        problems.append(f"{missing} mau khong co match exact|alias theo selected_record_id")
    wrong = int((actual_prediction.fillna("~").to_numpy() != expected_prediction.fillna("~").to_numpy()).sum())
    if wrong:
        problems.append(f"prediction_match_status khac match DB o {wrong} mau")
    for k in HORIZONS:
        def expected_label(record_id: Any, k: int = k) -> Any:
            target = db_samples[int(record_id)][f"label_source_record_id_h{k}"]
            return None if target is None or pd.isna(target) else status.get(int(target), "~missing_target_match~")
        expected = ids.map(expected_label)
        if (expected == "~missing_target_match~").any():
            problems.append(f"h{k}: {int((expected == '~missing_target_match~').sum())} target nhan khong co match exact|alias")
        actual = frame[f"label_match_status_h{k}"].astype("object")
        wrong_label = int((actual.fillna("~").to_numpy() != expected.fillna("~").to_numpy()).sum())
        if wrong_label:
            problems.append(f"label_match_status_h{k} khac match cua target DB o {wrong_label} mau")
    return problems


def _official_gate_check(out: Path, config: dict[str, Any]) -> dict[str, Any]:
    """`official`: MOI evaluation horizon phai `primary_eligible` (khong co official-exploratory); rehearsal/dev khong chan."""
    statuses = {name: entry.get("status") for name, entry in ((_read_json(out / "sufficiency_report.json") or {}).get("horizons") or {}).items()}
    wanted = {f"h{k}": statuses.get(f"h{k}") for k in config.get("evaluation_horizons", [])}
    ok = config.get("purpose") != "official" or (bool(wanted) and all(s == "primary_eligible" for s in wanted.values()))
    return {"name": "official_evaluation_horizons_primary_eligible", "ok": ok, "detail": {"purpose": config.get("purpose"), "evaluation_horizon_status": wanted}}


def _calendar_check(out: Path, config: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    """R3-M1: lich da dung phai (a) co trong checksum DB, (b) snapshot bytes == hash da ghim trong config, (c) calendar_input.json khai bao cung hash."""
    pinned = (config.get("calendar_input") or {}).get("sha256")
    snapshot, manifest_path = out / CALENDAR_SNAPSHOT, out / CALENDAR_MANIFEST
    snapshot_sha = file_sha256(snapshot) if snapshot.is_file() else None
    try:
        declared = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    except ValueError:
        declared = None
    detail = {"pinned": (pinned or "")[:16], "snapshot": (snapshot_sha or "")[:16],
              "manifest": ((declared or {}).get("vn_holidays_csv_sha256") or "")[:16],
              "in_checksums": sorted({CALENDAR_MANIFEST, CALENDAR_SNAPSHOT} & set(stored))}
    ok = bool(pinned) and snapshot_sha == pinned and bool(declared) and declared.get("vn_holidays_csv_sha256") == pinned         and {CALENDAR_MANIFEST, CALENDAR_SNAPSHOT} <= set(stored)
    return {"name": "lich_snapshot_khop_config_va_checksum", "ok": ok, "detail": detail}


def split_policy_consistency(split_report: dict[str, Any] | None, sufficiency: dict[str, Any] | None) -> tuple[bool, Any]:
    """Ly do chon bien (buoc split) phai NHAT QUAN voi sufficiency chay doc lap sau export (GPT review DB-M1 muc 5):
    - gate_driven:H=k  => horizon k `primary_eligible` va cac so do tren mau that luc chon bien == so cua sufficiency;
    - fallback_ratio   => feasible_horizon rong va moi ung vien lich-kha-thi deu da bi gate loai (gate_pass=false)."""
    if not split_report or not sufficiency:
        return False, "thieu reports/split.json hoac sufficiency_report.json"
    plan = split_report.get("plan", {})
    horizon = plan.get("feasible_horizon")
    candidates = plan.get("candidates", [])
    if horizon is None:
        wrongly_rejected = [c["horizon"] for c in candidates if c.get("gate_pass") is True]
        return (plan.get("policy_path") == "fallback_ratio" and not wrongly_rejected), {"policy_path": plan.get("policy_path"), "pass_but_rejected": wrongly_rejected}
    chosen = next((c for c in candidates if c.get("horizon") == horizon and c.get("gate_pass") is True), None)
    final = sufficiency.get("horizons", {}).get(f"h{horizon}")
    if chosen is None or final is None:
        return False, {"horizon": horizon, "chosen_candidate": chosen is not None, "sufficiency_has_horizon": final is not None}
    if final.get("status") != "primary_eligible":
        return False, {"horizon": horizon, "sufficiency_status": final.get("status"), "failed": final.get("failed_gates")}
    mismatch = [(s, key) for s in ("train", "validation", "test") for key in _CONSISTENCY_KEYS
                if chosen["splits"][s][key] != final["splits"][s][key]]
    return not mismatch, {"horizon": horizon, "mismatch": mismatch}


def _split_policy_check(out: Path) -> dict[str, Any]:
    def read(path: Path) -> dict[str, Any] | None:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    ok, detail = split_policy_consistency(read(out / "reports" / "split.json"), read(out / "sufficiency_report.json"))
    return {"name": "split_policy_nhat_quan_sufficiency", "ok": ok, "detail": detail}


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
    from .config import config_sha256
    for problem in verify_bundle(out_dir=output_root / dataset_version, stored=manifest.get("output_parquet_sha256_json"),
                                 dataset_version=dataset_version, build_config_sha256=config_sha256(config),
                                 builder_code_sha256=(config.get("builder_code") or {}).get("code_sha256", "")):
        add("gioi_report", False, problem)
    return problems
