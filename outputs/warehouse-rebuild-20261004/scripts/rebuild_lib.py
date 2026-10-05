"""Hàm THUẦN (không DB) dùng chung cho preflight/compare/gate của rebuild 3 nguồn — tách ra để test tĩnh được (GPT 07b).

- `sweep_overlaps`: chồng giờ giữa các run CÙNG nguồn bằng sweep `max_finished_so_far` (không chỉ hai run kề nhau).
- `validate_cutoff`: cutoff do input-freeze pin (nguồn → ngày crawl VN cuối cùng nằm trong dump), phải phủ đủ nguồn và khớp ngày quan sát được.
- `compare_snapshots`: so snapshot DB vận hành trước/sau; mode strict (mặc định, no-overlap) đòi bằng tuyệt đối + zero-active hai đầu.
- `diff_batch_provenance`: provenance của hai batch (rehearsal vs official) phải bằng nhau ngoài các field theo-lần-build.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable, Mapping

# Field CHỈ ĐƯỢC KHÁC giữa hai lần build (đúng nghĩa theo-lần-build).
BATCH_PER_BUILD_FIELDS = ("batch_id", "warehouse_database", "started_at", "finished_at", "fail_reason", "notes")
BATCH_IDENTITY_FIELDS = (
    "status", "setup_sql_sha256", "source_manifest_sha256", "cohort_manifest_sha256", "ownership_manifest_sha256", "etl_config_sha256",
    "canonicalization_version", "canonicalization_git_commit", "canonicalization_config_sha256",
)
SOURCE_IDENTITY_FIELDS = ("source_code", "source_priority", "dump_sha256", "schema_sha256", "dump_taken_at", "source_version_json")


def sweep_overlaps(runs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Run có `started_at < max(finished_at)` của các run trước nó (cùng nguồn). Ghi rõ run tạo ra mốc max."""
    by_source: dict[str, list[Mapping[str, Any]]] = {}
    for run in runs:
        if run.get("started_at") and run.get("finished_at"):
            by_source.setdefault(run["source_code"], []).append(run)
    overlaps: list[dict[str, Any]] = []
    for source, items in by_source.items():
        items.sort(key=lambda r: (r["started_at"], r["source_run_id"]))
        holder = None
        for run in items:
            if holder is not None and run["started_at"] < holder["finished_at"]:
                overlaps.append({"source_code": source, "run": run["source_run_id"], "started_at": run["started_at"].isoformat(),
                                 "overlaps_run": holder["source_run_id"], "holder_finished_at": holder["finished_at"].isoformat()})
            if holder is None or run["finished_at"] > holder["finished_at"]:
                holder = run
    return overlaps


def validate_cutoff(cutoff: Mapping[str, Any], batch_sources: Iterable[str], observed_max_planned: Mapping[str, dt.date | None]) -> list[str]:
    """Trả danh sách lỗi (rỗng = hợp lệ). `cutoff['cutoff_vn_crawl_date']` = {source: 'YYYY-MM-DD'} (đã pin cùng input-freeze)."""
    problems: list[str] = []
    dates = cutoff.get("cutoff_vn_crawl_date")
    if not isinstance(dates, Mapping):
        return ["cutoff file thiếu object cutoff_vn_crawl_date"]
    sources = set(batch_sources)
    if set(dates) != sources:
        problems.append(f"cutoff phủ {sorted(dates)} nhưng batch có {sorted(sources)}")
    for source in sorted(sources & set(dates)):
        try:
            pinned = dt.date.fromisoformat(dates[source])
        except (TypeError, ValueError):
            problems.append(f"{source}: cutoff {dates[source]!r} không phải ngày ISO")
            continue
        observed = observed_max_planned.get(source)
        if observed is None:
            problems.append(f"{source}: warehouse không có run nào có planned_crawl_date")
        elif observed != pinned:
            problems.append(f"{source}: ngày crawl cuối quan sát được {observed} != cutoff đã pin {pinned}")
    return problems


def compare_snapshots(before: Mapping[str, Any], after: Mapping[str, Any], *, strict: bool = True) -> list[str]:
    """Strict (no-overlap, mặc định): zero-active hai đầu + count/max_id/latest-run BẰNG TUYỆT ĐỐI. Non-strict: chỉ đơn điệu after >= before."""
    problems: list[str] = []
    if before.get("operational_database") != after.get("operational_database"):
        problems.append("tên DB vận hành đổi")
    for table in ("crawl_runs", "crawl_run_items", "price_observations"):
        for key in ("count", "max_id"):
            b, a = before[table][key], after[table][key]
            if strict and a != b:
                problems.append(f"{table}.{key} đổi {b} -> {a} (strict: phải bằng tuyệt đối)")
            elif not strict and a < b:
                problems.append(f"{table}.{key} GIẢM {b} -> {a}")
    if strict:
        for label, snap in (("trước", before), ("sau", after)):
            if snap["runs_queued_or_running"] or snap["items_queued_or_running"]:
                problems.append(f"{label} build còn run/item active: runs={snap['runs_queued_or_running']} items={snap['items_queued_or_running']}")
        if before.get("latest_run") != after.get("latest_run"):
            problems.append(f"latest run đổi {before.get('latest_run')} -> {after.get('latest_run')}")
    return problems


def _norm_source(row: Mapping[str, Any]) -> dict[str, Any]:
    import json
    out = {k: row.get(k) for k in SOURCE_IDENTITY_FIELDS}
    out["dump_taken_at"] = str(out["dump_taken_at"])
    if isinstance(out["source_version_json"], (str, bytes)):
        out["source_version_json"] = json.loads(out["source_version_json"])
    return out


def diff_batch_provenance(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    """`a`/`b` = {'batch': {cột etl_import_batches}, 'sources': [dòng etl_import_sources]}. Trả danh sách khác biệt (rỗng = trùng)."""
    problems: list[str] = []
    for field in BATCH_IDENTITY_FIELDS:
        if a["batch"].get(field) != b["batch"].get(field):
            problems.append(f"batch.{field}: {a['batch'].get(field)!r} != {b['batch'].get(field)!r}")
    na = {r["source_code"]: _norm_source(r) for r in a["sources"]}
    nb = {r["source_code"]: _norm_source(r) for r in b["sources"]}
    if set(na) != set(nb):
        problems.append(f"tập source khác: {sorted(na)} vs {sorted(nb)}")
    for code in sorted(set(na) & set(nb)):
        for field in SOURCE_IDENTITY_FIELDS:
            if na[code][field] != nb[code][field]:
                problems.append(f"source[{code}].{field}: {na[code][field]!r} != {nb[code][field]!r}")
    return problems


def evaluate_preflight_doc(doc: Mapping[str, Any]) -> list[str]:
    """Snapshot preflight TRƯỚC build phải đi kèm các check đều OK (GPT 09 MAJOR 2): không cho bỏ qua exit code của preflight FAIL."""
    problems = [f"preflight trước build FAIL: {c['name']} ({c.get('detail')})" for c in doc.get("checks", []) if not c.get("ok")]
    if not doc.get("checks"):
        problems.append("snapshot không có danh sách checks của preflight")
    return problems


def check_code_state(before_head: str | None, now_head: str | None, now_dirty: Iterable[str]) -> list[str]:
    """HEAD phải bằng HEAD lúc preflight và các đường GUARDED_PATHS vẫn sạch SAU build (provenance chỉ pin lúc build bắt đầu)."""
    problems: list[str] = []
    if not before_head or before_head != now_head:
        problems.append(f"HEAD đổi hoặc thiếu: trước={before_head!r} sau={now_head!r}")
    dirty = list(now_dirty)
    if dirty:
        problems.append(f"GUARDED_PATHS dirty sau build: {dirty[:5]}")
    return problems


_DB = re.compile(r"^warehouse_[A-Za-z0-9_]{1,50}$")

# Field-level parity cho TOÀN BỘ cột nguồn ổn định (GPT 11 MAJOR 2). Mỗi bảng: COMPARE = (cột, kiểu) với kiểu ∈ {plain, binary, json};
# EXCLUDED = {cột: lý do} — hai tập này PHẢI phủ đúng toàn bộ cột của bảng trong setup.sql (test tĩnh kiểm), nên thêm cột mới vào schema mà
# quên cập nhật contract này sẽ làm test đỏ. `binary` = so BINARY (phân biệt hoa/thường, dấu — collation unicode_ci sẽ che mất đổi chữ hoa),
# `json` = so CAST(... AS CHAR). So NULL-safe bằng `<=>`. Không so ID kỹ thuật warehouse/imported_at/field reference rebuild/field tự cập nhật.
OBSERVATION_COMPARE_GROUPS = {
    "observation_core": (
        ("hotel_id", "binary"), ("crawl_trigger", "binary"), ("observed_at", "plain"), ("checkin_date", "plain"), ("checkout_date", "plain"),
        ("lead_time", "plain"), ("price_total", "plain"), ("price_per_night", "plain"), ("original_price", "plain"), ("discount_percent", "plain"),
        ("taxes_fees", "plain"), ("price_includes_tax", "plain"), ("is_sold_out", "plain"), ("availability_status", "binary"), ("created_at", "plain"),
    ),
    "observation_text": (
        ("room_type_raw", "binary"), ("room_type_norm", "binary"), ("room_option_key", "binary"), ("room_identity_key", "binary"),
        ("rate_plan_key", "binary"), ("bed_config", "binary"), ("room_area", "binary"), ("cancellation_policy", "binary"),
    ),
    "observation_room_flags": (
        ("room_option_index", "plain"), ("max_occupancy", "plain"), ("breakfast_included", "plain"), ("free_cancellation", "plain"), ("rooms_left", "plain"),
    ),
}
OBSERVATION_EXCLUDED = {
    "record_id": "PK remap (đã nối bằng source PK qua etl_observation_map)", "crawl_run_id": "FK remap", "crawl_run_item_id": "FK remap",
    "is_reference_room": "reset lúc import, recompute ở warehouse", "reference_definition_id": "reset lúc import",
    "reference_match_status": "reset lúc import", "reference_match_score": "reset lúc import",
    "is_anomaly": "projection operational, warehouse replay từ registry (không phải authority)",
}
ITEM_COMPARE = (
    ("status", "binary"), ("source_hotel_link", "binary"), ("requested_hotel_link", "binary"), ("source_link_hash", "binary"), ("hotel_link", "binary"),
    ("hotel_name_hint", "binary"), ("market_hint", "binary"), ("hotel_name", "binary"), ("hotel_id", "binary"), ("checkin_date", "plain"),
    ("checkout_date", "plain"), ("attempt_count", "plain"), ("claimed_at", "plain"), ("heartbeat_at", "plain"), ("finished_at", "plain"),
    ("worker_id", "binary"), ("last_error_code", "binary"), ("next_retry_at", "plain"), ("dom_room_row_count", "plain"), ("candidate_rate_count", "plain"),
    ("parsed_options_count", "plain"), ("rejected_options_count", "plain"), ("duplicate_options_count", "plain"), ("raw_options_count", "plain"),
    ("saved_options_count", "plain"), ("parse_warning_count", "plain"), ("rejected_options", "json"), ("dead_link_confirmation", "json"),
    ("driver_start_ms", "plain"), ("page_load_ms", "plain"), ("availability_wait_ms", "plain"), ("parse_ms", "plain"), ("db_write_ms", "plain"),
    ("item_total_ms", "plain"), ("error_message", "binary"), ("created_at", "plain"),
)
ITEM_EXCLUDED = {
    "id": "PK remap", "crawl_run_id": "FK remap (so source_run_id qua etl_run_map)", "reference_match_status": "reset lúc import",
    "artifact_html_path": "retention cleanup có thể xoá/đổi", "screenshot_path": "retention cleanup có thể xoá/đổi", "updated_at": "ON UPDATE tự động",
}
RUN_COMPARE = (
    ("status", "binary"), ("trigger_type", "binary"), ("source_file", "binary"), ("source_original_filename", "binary"), ("source_file_sha256", "binary"),
    ("source_file_size", "plain"), ("save_artifacts", "plain"), ("crawl_context", "json"), ("scraper_version", "binary"), ("selector_version", "binary"),
    ("git_commit", "binary"), ("storage_timezone", "binary"), ("date_mode", "binary"), ("lead_time_buckets", "binary"), ("checkin_dates", "json"),
    ("total", "plain"), ("processed", "plain"), ("success_count", "plain"), ("partial_count", "plain"), ("sold_out_count", "plain"),
    ("not_bookable_count", "plain"), ("error_count", "plain"), ("started_at", "plain"), ("finished_at", "plain"), ("error_message", "binary"),
    ("created_at", "plain"),
)
RUN_EXCLUDED = {"id": "PK remap", "retry_of_run_id": "FK nội bộ nguồn, bị remap", "updated_at": "ON UPDATE tự động"}


def _require_db(name: str) -> str:
    if not _DB.match(name):
        raise ValueError(f"tên database không hợp lệ cho truy vấn parity: {name!r}")
    return name


def _expr(alias: str, column: str, kind: str) -> str:
    if kind == "binary":
        return f"BINARY {alias}.{column}"
    if kind == "json":
        return f"CAST({alias}.{column} AS CHAR)"
    return f"{alias}.{column}"


def _field_sqls(join_sql: str, fields: tuple, o_alias: str, n_alias: str, new_key_expr: str, key_cols: str,
                extra: tuple = ()) -> dict[str, str]:
    """`fields` = ((cột, kiểu), ...); `extra` = ((tên, biểu_thức_cũ, biểu_thức_mới), ...) cho field lấy từ bảng map (vd source_run_id)."""
    pairs = [(name, _expr(o_alias, name, kind), _expr(n_alias, name, kind)) for name, kind in fields] + list(extra)
    diffs = ", ".join(f"SUM({new_key_expr} IS NOT NULL AND NOT ({o} <=> {n})) AS diff_{name}" for name, o, n in pairs)
    anydiff = " OR ".join(f"NOT ({o} <=> {n})" for _name, o, n in pairs)
    # Cột HIỂN THỊ của sample dùng giá trị gốc (string/số), KHÔNG dùng `BINARY ...` (connector trả bytes → không JSON-serialize được, GPT 13 MAJOR 1);
    # chỉ điều kiện so sánh dùng BINARY. JSON vẫn CAST AS CHAR (là string).
    shown = [(name, _expr(o_alias, name, "json" if kind == "json" else "plain"), _expr(n_alias, name, "json" if kind == "json" else "plain"))
             for name, kind in fields] + list(extra)
    sample_cols = ", ".join(f"{o} AS old_{name}, {n} AS new_{name}" for name, o, n in shown)
    return {
        "count": f"SELECT COUNT(*) AS total_old, SUM({new_key_expr} IS NULL) AS missing_in_new, {diffs} {join_sql}",
        "sample": f"SELECT {key_cols}, {sample_cols} {join_sql} AND ({new_key_expr} IS NULL OR {anydiff}) ORDER BY {key_cols} LIMIT 20",
    }


def build_parity_sqls(old_db: str, new_db: str) -> dict[str, dict[str, str]]:
    """SQL parity cấp BẢN GHI giữa Wave A và warehouse mới (cùng MySQL server). Tham số vị trí: (new_batch, old_batch).
    `observation`: fingerprint `source_record_sha256` (12 field, check nhanh/độc lập); `observation_core/text/room_flags`: field-level toàn bộ cột ổn định
    của price_observations chia 3 query (verdict cuối là AND); `item`, `run`: field-level toàn bộ cột ổn định + source run/planned crawl date từ bảng map."""
    old, new = _require_db(old_db), _require_db(new_db)
    out: dict[str, dict[str, str]] = {}
    obs_join = (f"FROM `{old}`.etl_observation_map omo JOIN `{old}`.price_observations o ON o.record_id=omo.warehouse_record_id "
                f"LEFT JOIN `{new}`.etl_observation_map omn ON omn.import_batch_id=%s AND omn.source_code=omo.source_code "
                f"AND omn.source_record_id=omo.source_record_id LEFT JOIN `{new}`.price_observations n ON n.record_id=omn.warehouse_record_id "
                f"WHERE omo.import_batch_id=%s")
    out["observation"] = {
        "count": (f"SELECT COUNT(*) AS total_old, SUM(omn.source_record_id IS NULL) AS missing_in_new, "
                  f"SUM(omn.source_record_id IS NOT NULL AND omn.source_record_sha256 <> omo.source_record_sha256) AS fingerprint_diff {obs_join}"),
        "sample": (f"SELECT omo.source_code, omo.source_record_id, omo.source_record_sha256 AS old_sha, omn.source_record_sha256 AS new_sha {obs_join} "
                   f"AND (omn.source_record_id IS NULL OR omn.source_record_sha256 <> omo.source_record_sha256) ORDER BY 1,2 LIMIT 20"),
    }
    for kind, fields in OBSERVATION_COMPARE_GROUPS.items():
        out[kind] = _field_sqls(obs_join, fields, "o", "n", "omn.source_record_id", "omo.source_code, omo.source_record_id")
    item_join = (f"FROM `{old}`.etl_item_map imo JOIN `{old}`.crawl_run_items o ON o.id=imo.warehouse_item_id "
                 f"JOIN `{old}`.etl_run_map rmo ON rmo.warehouse_run_id=o.crawl_run_id AND rmo.import_batch_id=imo.import_batch_id "
                 f"LEFT JOIN `{new}`.etl_item_map imn ON imn.import_batch_id=%s AND imn.source_code=imo.source_code AND imn.source_item_id=imo.source_item_id "
                 f"LEFT JOIN `{new}`.crawl_run_items n ON n.id=imn.warehouse_item_id "
                 f"LEFT JOIN `{new}`.etl_run_map rmn ON rmn.warehouse_run_id=n.crawl_run_id AND rmn.import_batch_id=imn.import_batch_id "
                 f"WHERE imo.import_batch_id=%s")
    out["item"] = _field_sqls(item_join, ITEM_COMPARE, "o", "n", "imn.source_item_id", "imo.source_code, imo.source_item_id",
                              extra=(("source_run_id", "rmo.source_run_id", "rmn.source_run_id"),))
    run_join = (f"FROM `{old}`.etl_run_map rmo JOIN `{old}`.crawl_runs o ON o.id=rmo.warehouse_run_id "
                f"LEFT JOIN `{new}`.etl_run_map rmn ON rmn.import_batch_id=%s AND rmn.source_code=rmo.source_code AND rmn.source_run_id=rmo.source_run_id "
                f"LEFT JOIN `{new}`.crawl_runs n ON n.id=rmn.warehouse_run_id WHERE rmo.import_batch_id=%s")
    out["run"] = _field_sqls(run_join, RUN_COMPARE, "o", "n", "rmn.source_run_id", "rmo.source_code, rmo.source_run_id",
                             extra=(("planned_crawl_date", "rmo.planned_crawl_date", "rmn.planned_crawl_date"),))
    return out


def validate_snapshot_identity(label: str, batch_row: Mapping[str, Any] | None, sources: Iterable[Mapping[str, Any]],
                               expected_sources: Iterable[str]) -> tuple[list[str], dict[str, Any]]:
    """Mỗi snapshot phải đọc được batch row + source set; fail-closed nếu thiếu, status ≠ pass hoặc tập source ≠ giá trị đã pin (GPT 11 MAJOR 1)."""
    problems: list[str] = []
    identity: dict[str, Any] = {"label": label}
    if batch_row is None:
        return [f"{label}: batch không tồn tại"], identity
    codes = sorted(str(s["source_code"]) for s in sources)
    identity.update({"batch_id": batch_row.get("batch_id"), "status": batch_row.get("status"), "sources": codes,
                     "source_manifest_sha256": batch_row.get("source_manifest_sha256"),
                     "cohort_manifest_sha256": batch_row.get("cohort_manifest_sha256"),
                     "ownership_manifest_sha256": batch_row.get("ownership_manifest_sha256"),
                     "canonicalization_version": batch_row.get("canonicalization_version"),
                     "canonicalization_git_commit": batch_row.get("canonicalization_git_commit")})
    if batch_row.get("status") != "pass":
        problems.append(f"{label}: batch {batch_row.get('batch_id')!r} status={batch_row.get('status')!r}, cần 'pass'")
    expected = sorted(set(expected_sources))
    if codes != expected:
        problems.append(f"{label}: tập source {codes} != đã pin {expected}")
    return problems, identity


def parity_verdict(report: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """`report[kind]` = dòng `count` (total_old, missing_in_new, fingerprint_diff | diff_<field>...). parity chỉ true khi không thiếu và không lệch."""
    verdict: dict[str, Any] = {}
    for kind, row in report.items():
        total = int(row.get("total_old") or 0)
        missing = int(row.get("missing_in_new") or 0)
        diffs = {k: int(v or 0) for k, v in row.items() if (k.startswith("diff_") or k == "fingerprint_diff") and int(v or 0)}
        verdict[kind] = {"total_old": total, "missing_in_new": missing, "differences": diffs, "parity": total > 0 and missing == 0 and not diffs}
    verdict["parity"] = bool(verdict) and all(v["parity"] for v in verdict.values())
    return verdict


def compare_run_sets(old: Iterable[Mapping[str, Any]], new: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """So tập run của Wave A (hai nguồn) với warehouse mới: mọi run cũ phải CÓ ở bản mới với cùng status/items/observations
    (lịch sử đã đóng băng không được đổi). Trả delta có denominator; run chỉ có ở bản mới là phần mở rộng hợp lệ (không phải lỗi)."""
    def key(r: Mapping[str, Any]) -> tuple:
        return (r["source_code"], r["source_run_id"])

    old_list = list(old)
    new_by = {key(r): r for r in new}
    missing: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for r in old_list:
        other = new_by.get(key(r))
        if other is None:
            missing.append({"source_code": r["source_code"], "source_run_id": r["source_run_id"]})
            continue
        diffs = {f: (r[f], other[f]) for f in ("status", "items", "observations") if r[f] != other[f]}
        if diffs:
            changed.append({"source_code": r["source_code"], "source_run_id": r["source_run_id"], "diff": diffs})
    old_keys = {key(r) for r in old_list}
    return {
        "old_runs": len(old_list), "new_runs": len(new_by), "old_runs_missing_in_new": missing, "old_runs_changed_in_new": changed,
        "runs_only_in_new": sum(1 for k in new_by if k not in old_keys),
        "parity": not missing and not changed,
    }


def json_default(value: Any) -> Any:
    """`default=` cho json.dumps: bytes/bytearray (BINARY của MySQL connector) → chuỗi UTF-8 (lỗi giải mã thì hex có nhãn); date/Decimal → chuỗi/số."""
    import datetime
    import decimal
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return "hex:" + bytes(value).hex()
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    raise TypeError(f"không serialize được kiểu {type(value).__name__}")


def parity_session_sql(max_ms: int) -> str:
    """Trần thời gian truy vấn cho connection parity (GPT 13 MAJOR 2): timeout → exception → artifact .failed-*, không chờ vô hạn."""
    if not isinstance(max_ms, int) or max_ms <= 0:
        raise ValueError("max_ms phải là số nguyên dương")
    return f"SET SESSION max_execution_time = {max_ms}"


# Đường dẫn manifest PHẢI do người vận hành truyền tường minh cho preflight rồi chính các đường dẫn đó cho build_warehouse (GPT 13 MAJOR 3).
PROFILE_REQUIRES_CUTOFF = {"full": True, "aux": False}


def verify_profile(doc: Mapping[str, Any], profile: str, actual: Mapping[str, Mapping[str, str]]) -> list[str]:
    """`input_provenance.json.profiles[profile]` phải ghim identity của ĐÚNG manifest sẽ dùng. `actual[kind] = {'path': repo-relative, 'sha256': ...}`
    với kind ∈ source_manifest, ownership_manifest, cohort_manifest (+ cutoff_file ở profile full). So cả hash lẫn đường dẫn."""
    if profile not in PROFILE_REQUIRES_CUTOFF:
        return [f"profile {profile!r} không hợp lệ (aux|full)"]
    pinned = (doc.get("profiles") or {}).get(profile)
    if not isinstance(pinned, Mapping):
        return [f"input_provenance thiếu profiles.{profile} (chạy lại make_input_manifests.py --write ở input-freeze)"]
    kinds = ["source_manifest", "ownership_manifest", "cohort_manifest"] + (["cutoff_file"] if PROFILE_REQUIRES_CUTOFF[profile] else [])
    hash_field = {"source_manifest": "identity_sha256"}
    problems: list[str] = []
    for kind in kinds:
        entry, got = pinned.get(kind), actual.get(kind)
        field = hash_field.get(kind, "sha256")
        if not isinstance(entry, Mapping) or field not in entry or "path" not in entry:
            problems.append(f"profiles.{profile}.{kind} thiếu path/{field}")
        elif got is None:
            problems.append(f"profile {profile}: thiếu giá trị thực tế cho {kind}")
        else:
            if entry["path"] != got["path"]:
                problems.append(f"profiles.{profile}.{kind}.path {entry['path']!r} != đường dẫn được truyền {got['path']!r}")
            if entry[field] != got["sha256"]:
                problems.append(f"profiles.{profile}.{kind}.{field} {entry[field]!r} != file thật {got['sha256']!r}")
    return problems


def compare_input_snapshots(before: Mapping[str, Mapping[str, str]] | None, after: Mapping[str, Mapping[str, str]]) -> list[str]:
    """Đường dẫn + hash input không được đổi trong suốt stage (post-compare tính lại từ đúng path đã ghi trong snapshot)."""
    if not before:
        return ["snapshot preflight không ghi inputs (path+hash)"]
    problems: list[str] = []
    for kind in sorted(set(before) | set(after)):
        if before.get(kind) != after.get(kind):
            problems.append(f"input {kind} đổi trong lúc build: {before.get(kind)} -> {after.get(kind)}")
    return problems
