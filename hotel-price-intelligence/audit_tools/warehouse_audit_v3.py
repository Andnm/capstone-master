"""Audit warehouse DAN XUAT (doc-chi) cho dataset train-v3 dev1b/dev3b (GPT file 30 muc 4). CHUA DUOC DUYET CHAY Q1-Q4 - chi Buoc 0 duoc duyet hep.

    python warehouse_audit_v3.py --step0                       # Buoc 0: toi da 2 dong manifest tren DB clone (khong dong nao khac)
    python warehouse_audit_v3.py --explain q1,q2,q3,q4         # CHI EXPLAIN (khong ANALYZE) mot batch dai dien; in plan + verdict guard
    python warehouse_audit_v3.py --run q1,q2,q3,q4             # chay that theo batch <=1000 (chi sau khi GPT duyet query thuc te)

Guard bat buoc (kiem tra TRUOC khi mo ket noi du lieu): (1) ten DB phai la `warehouse_dsdev_*` (KHONG BAO GIO DB van hanh); (2) cong crawler: lan chay moi nhat o trang thai `completed` (qua chinh script
`backend/scripts/check_crawl_progress.py` cua du an - read-only) VA gio VN ngoai khung 00:30-17:00; (3) phien READ ONLY, MAX_EXECUTION_TIME <= 10 s, mot query tai mot thoi diem, khong retry, khong UPDATE/ANALYZE/DDL;
(4) EXPLAIN moi query truoc khi chay: tu choi neu bang khong-dan-dat bi quet toan bo (`type` ALL/index) hoac bang dan dat khong dung range/ref. Moi bat thuong => dung va ghi UNVERIFIED, khong sua DB.
Cac ham danh gia la HAM THUAN (khong DB) de test duoc voi negative control (ml/tests/test_warehouse_audit_v3.py).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ROOT = REPO.parent
DB_RE = re.compile(r"^warehouse_dsdev_[A-Za-z0-9_]{1,60}$")
BATCH_ID = "b20261004_3src"
DATASETS = {
    "ds_20261006_dev1b": {"build_config_sha256": "6494c1d4ece098e12855644f26780eecfe6706be32baee8a8f486b8b10069928", "horizon": 1},
    "ds_20261006_dev3b": {"build_config_sha256": "45f2fc6ab177505de3b5a1590f6cab2988bb9e05ac72a1996127c2760289cbd8", "horizon": 3},
}
EXPECTED_SELECTED = 146431
EXPECTED_LABELLED = {1: 128583, 3: 111515, 7: 83077, 14: 45049}
EXPECTED_BURN_IN = 43662
BATCH = 1000
MAX_EXECUTION_MS = 10_000
VN = dt.timezone(dt.timedelta(hours=7))


# --------------------------------------------------------------------------- guard thuan
def database_allowed(name: str) -> bool:
    return bool(DB_RE.match(name or ""))


def time_gate_ok(now_utc: dt.datetime) -> bool:
    """Ngoai khung crawl 00:30-17:00 gio VN."""
    local = now_utc.astimezone(VN)
    minutes = local.hour * 60 + local.minute
    return minutes >= 17 * 60 or minutes < 30


def crawler_gate_ok(progress_text: str) -> tuple[bool, str]:
    """Dong tien do cua check_crawl_progress.py co dang `Run #N: a/b (x%) — <status>`; chi `completed` va a == b la terminal."""
    match = re.search(r"Run #(\d+): (\d+)/(\d+) \([\d.]+%\)\s+[—-]\s+(\w+)", progress_text or "")
    if not match:
        return False, "khong doc duoc dong tien do (khong xac nhan duoc crawler terminal)"
    run_id, done, total, status = int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4)
    if status != "completed" or done != total:
        return False, f"run #{run_id} chua terminal: {done}/{total} status={status}"
    return True, f"run #{run_id} completed {done}/{total}"


DRIVING_OK = ("range", "ref", "eq_ref", "const")
JOIN_OK = ("eq_ref", "ref", "const")


def explain_ok(plan: Sequence[Mapping[str, Any]], *, driving: str, allow_index_scan: Sequence[str] = ()) -> tuple[bool, list[str]]:
    """Plan EXPLAIN (list dict): bang dan dat chi `range|ref|eq_ref|const`; bang noi chi `eq_ref|ref|const`; `index` (quet toan index) chi cho bang duoc cho phep (Q3 index-only); ALL/khac => tu choi."""
    problems: list[str] = []
    if not plan:
        return False, ["EXPLAIN khong tra ket qua"]
    for row in plan:
        table, kind = str(row.get("table")), str(row.get("type"))
        allowed = DRIVING_OK if table == driving else JOIN_OK
        if table in allow_index_scan:
            allowed = tuple(allowed) + ("index",)
        if kind not in allowed:
            problems.append(f"{table}: type={kind} khong duoc phep ({'bang dan dat' if table == driving else 'bang noi'}: chi {'/'.join(allowed)})")
    return not problems, problems


# --------------------------------------------------------------------------- danh gia thuan (Q1..Q4)
def q1_violations(row: Mapping[str, Any]) -> list[str]:
    """Moi selected sample: assignment/observation phai ton tai (LEFT JOIN, khong de INNER JOIN che mat), moc thoi gian khong NULL, observed_at >= approved_at, prediction_time == observed_at, series khop."""
    out = []
    if row.get("a_id") is None:
        out.append("missing_assignment")
    if row.get("po_id") is None:
        out.append("missing_observation")
    if row.get("approved_at") is None:
        out.append("approved_at_null")
    if row.get("observed_at") is None:
        out.append("observed_at_null")
    if row.get("approved_at") is not None and row.get("observed_at") is not None and row["observed_at"] < row["approved_at"]:
        out.append("observed_before_approval")
    if row.get("observed_at") is not None and row.get("prediction_time") != row["observed_at"]:
        out.append("prediction_time_ne_observed_at")
    if row.get("a_id") is not None and row.get("po_id") is not None and (row.get("po_hotel") != row.get("a_hotel") or row.get("po_checkin") != row.get("a_checkin")):
        out.append("observation_series_ne_assignment_series")
    return out


def q2_violations(row: Mapping[str, Any], k: int) -> list[str]:
    """Nhan h{k}: has_label <=> id <=> target; target la daily-selected sample CUNG dataset + CUNG assignment, ngay VN = ngay nguon + k, cung hotel/check-in; target observation phai ton tai."""
    out = []
    has, tgt = bool(row.get("has_label")), row.get("tgt_record")
    if has != (tgt is not None):
        out.append("has_label_ne_target_id")
    if not has:
        return out
    if row.get("t_sample_id") is None:
        out.append("target_sample_missing")
        return out
    if not row.get("t_selected"):
        out.append("target_not_daily_selected")
    if row.get("t_assignment") != row.get("assignment"):
        out.append("target_other_assignment")
    if row.get("t_date") is None or (row["t_date"] - row["s_date"]).days != k:
        out.append("target_date_not_source_plus_k")
    if row.get("tp_id") is None:
        out.append("target_observation_missing")
    elif row.get("tp_hotel") != row.get("s_hotel") or row.get("tp_checkin") != row.get("s_checkin"):
        out.append("target_observation_other_series")
    return out


def q2_sensitivity_count(rows: Sequence[Mapping[str, Any]], k: int) -> int:
    """Control: cung vi tu nhung voi +k+1 phai thay vi pham tren cac dong co nhan (chung minh predicate co the dương)."""
    return sum(1 for r in rows if r.get("has_label") and r.get("t_date") is not None and (r["t_date"] - r["s_date"]).days != k + 1)


def q3_group_verdict(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Mot nhom (assignment, ngay VN): dung MOT selected; selected phai co khoa nho nhat theo (owner_success DESC, prediction_time ASC, source_code, source_run_id, source_item_id)."""
    def key(r):
        return (0 if r.get("ownership_status") == "owner_success" else 1, r["prediction_time"], str(r.get("source_code")), int(r.get("source_run_id") or 0), int(r.get("source_item_id") or 0))
    selected = [r for r in group if r.get("is_daily_snapshot_selected")]
    keys = sorted(key(r) for r in group)
    strict = len(set(keys)) == len(keys)
    verdict = {"n": len(group), "n_selected": len(selected), "exactly_one_selected": len(selected) == 1, "strict_order": strict}
    verdict["selected_is_min_key"] = bool(len(selected) == 1 and key(selected[0]) == keys[0])
    verdict["reverse_order_would_pick_other"] = bool(strict and len(selected) == 1 and len(group) > 1 and key(selected[0]) != keys[-1])
    return verdict


def q4_fidelity_violations(row: Mapping[str, Any], parquet: Mapping[str, Any], parse_area) -> list[str]:
    """Assignment -> Parquet: city, max_occupancy, room_area(m2 theo parse), breakfast, cancellation phai khop (NULL semantics: NULL == NaN/None)."""
    def eq(a, b):
        a_null = a is None or (isinstance(a, float) and a != a)
        b_null = b is None or (isinstance(b, float) and b != b)
        return a_null and b_null or (not a_null and not b_null and a == b)
    out = []
    if not eq(row.get("city"), parquet.get("city")):
        out.append("city")
    if not eq(row.get("a_max_occupancy"), parquet.get("max_occupancy")):
        out.append("max_occupancy")
    if not eq(parse_area(row.get("a_room_area")), parquet.get("room_area_m2")):
        out.append("room_area_m2")
    for column in ("breakfast_included", "free_cancellation"):
        a_val = row.get(f"a_{column}")
        if not eq(None if a_val is None else bool(a_val), None if parquet.get(column) is None or parquet.get(column) != parquet.get(column) else bool(parquet.get(column))):
            out.append(column)
    return out


def q4_drift(row: Mapping[str, Any]) -> list[str]:
    """Assignment (dong bang) vs thuoc tinh cua CHINH snapshot: chi thong ke lech (ngu nghia khop), KHONG phai loi."""
    out = []
    for name, a, p in (("max_occupancy", row.get("a_max_occupancy"), row.get("po_max_occupancy")), ("room_area", row.get("a_room_area"), row.get("po_room_area")),
                       ("breakfast_included", row.get("a_breakfast_included"), row.get("po_breakfast_included")), ("free_cancellation", row.get("a_free_cancellation"), row.get("po_free_cancellation"))):
        if (a is None) != (p is None) or (a is not None and a != p):
            out.append(name)
    return out


# --------------------------------------------------------------------------- SQL (xem file 31 de GPT review truc tiep)
SQL_STEP0 = ("SELECT dataset_version, import_batch_id, status, last_completed_step, active_step, build_config_sha256, output_parquet_sha256_json, anomaly_registry_cutoff_at, anomaly_registry_file_sha256 "
             "FROM dataset_build_manifests WHERE dataset_version IN (%s, %s)")
SQL_Q1 = ("SELECT s.record_id, s.ml_reference_assignment_id, s.prediction_time, a.id AS a_id, a.approved_at, a.hotel_id AS a_hotel, a.checkin_date AS a_checkin, "
          "po.record_id AS po_id, po.observed_at, po.hotel_id AS po_hotel, po.checkin_date AS po_checkin "
          "FROM ml_samples s LEFT JOIN ml_reference_assignments a ON a.id = s.ml_reference_assignment_id AND a.dataset_version = s.dataset_version "
          "LEFT JOIN price_observations po ON po.record_id = s.record_id "
          "WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = 1 AND s.record_id > %s ORDER BY s.record_id LIMIT " + str(BATCH))
SQL_Q1_CONTROL = ("SELECT m.crawl_run_item_id, SUM(po.observed_at < a.approved_at) AS before_approval, COUNT(*) AS n "
                  "FROM ml_item_reference_matches m JOIN ml_reference_assignments a ON a.id = m.ml_reference_assignment_id AND a.dataset_version = m.dataset_version "
                  "JOIN price_observations po ON po.record_id = m.selected_record_id "
                  "WHERE m.dataset_version = %s AND m.match_status IN ('exact','alias') AND po.is_sold_out = 0 AND po.price_per_night > 0 AND m.crawl_run_item_id > %s "
                  "GROUP BY m.crawl_run_item_id ORDER BY m.crawl_run_item_id LIMIT " + str(BATCH))
SQL_Q2 = ("SELECT s.record_id, s.ml_reference_assignment_id AS assignment, s.vn_observation_date AS s_date, s.has_label_h{k} AS has_label, s.label_source_record_id_h{k} AS tgt_record, "
          "t.id AS t_sample_id, t.is_daily_snapshot_selected AS t_selected, t.ml_reference_assignment_id AS t_assignment, t.vn_observation_date AS t_date, "
          "tp.record_id AS tp_id, tp.hotel_id AS tp_hotel, tp.checkin_date AS tp_checkin, tp.price_per_night AS tp_price, sp.hotel_id AS s_hotel, sp.checkin_date AS s_checkin "
          "FROM ml_samples s LEFT JOIN ml_samples t ON t.dataset_version = s.dataset_version AND t.record_id = s.label_source_record_id_h{k} "
          "LEFT JOIN price_observations tp ON tp.record_id = t.record_id LEFT JOIN price_observations sp ON sp.record_id = s.record_id "
          "WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = 1 AND s.record_id > %s ORDER BY s.record_id LIMIT " + str(BATCH))
SQL_Q3_GROUPS = ("SELECT s.ml_reference_assignment_id AS assignment, s.vn_observation_date AS vn_date, COUNT(*) AS n, SUM(s.is_daily_snapshot_selected) AS n_selected "
                 "FROM ml_samples s WHERE s.dataset_version = %s GROUP BY s.ml_reference_assignment_id, s.vn_observation_date HAVING COUNT(*) > 1 "
                 "ORDER BY s.ml_reference_assignment_id, s.vn_observation_date")
SQL_Q3_ROWS = ("SELECT s.record_id, s.ml_reference_assignment_id AS assignment, s.vn_observation_date AS vn_date, s.is_daily_snapshot_selected, s.prediction_time, "
               "im.ownership_status, rm.source_code, rm.source_run_id, im.source_item_id "
               "FROM ml_samples s JOIN price_observations po ON po.record_id = s.record_id "
               "LEFT JOIN etl_item_map im ON im.import_batch_id = %s AND im.warehouse_item_id = po.crawl_run_item_id "
               "LEFT JOIN etl_run_map rm ON rm.import_batch_id = %s AND rm.warehouse_run_id = po.crawl_run_id "
               "WHERE s.dataset_version = %s AND s.ml_reference_assignment_id IN ({marks}) ORDER BY s.ml_reference_assignment_id, s.vn_observation_date, s.record_id")
SQL_Q4 = ("SELECT s.record_id, h.city, a.max_occupancy AS a_max_occupancy, a.room_area AS a_room_area, a.breakfast_included AS a_breakfast_included, a.free_cancellation AS a_free_cancellation, "
          "po.max_occupancy AS po_max_occupancy, po.room_area AS po_room_area, po.breakfast_included AS po_breakfast_included, po.free_cancellation AS po_free_cancellation "
          "FROM ml_samples s JOIN ml_reference_assignments a ON a.id = s.ml_reference_assignment_id AND a.dataset_version = s.dataset_version JOIN hotels h ON h.hotel_id = a.hotel_id "
          "LEFT JOIN price_observations po ON po.record_id = s.record_id "
          "WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = 1 AND s.record_id > %s ORDER BY s.record_id LIMIT " + str(BATCH))
SQL_COUNT_SELECTED = "SELECT COUNT(*) AS n FROM ml_samples WHERE dataset_version = %s AND is_daily_snapshot_selected = 1"


def parse_json_text_contains(raw: Any, needle: str) -> bool:
    text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    return needle in (text or "")


def step0_verdict(rows: Sequence[Mapping[str, Any]], samples_sha: Mapping[str, str]) -> dict[str, Any]:
    """Bao cao Buoc 0: moi dataset phai co dung 1 dong, status=pass, last_completed_step=validation, active_step NULL, batch dung, build SHA khop contract, hash parquet khop artifact."""
    by = {r["dataset_version"]: r for r in rows}
    out: dict[str, Any] = {"rows": len(rows), "datasets": {}}
    ok = len(rows) == len(DATASETS) and set(by) == set(DATASETS)
    for name, meta in DATASETS.items():
        r = by.get(name)
        checks = {}
        if r is not None:
            checks = {"status_pass": r["status"] == "pass", "last_completed_step_validation": r["last_completed_step"] == "validation", "active_step_null": r["active_step"] is None,
                      "batch": r["import_batch_id"] == BATCH_ID, "build_config_sha256": r["build_config_sha256"] == meta["build_config_sha256"],
                      "parquet_sha_in_manifest": parse_json_text_contains(r.get("output_parquet_sha256_json"), samples_sha.get(name, "<none>"))}
        out["datasets"][name] = checks
        ok = ok and bool(checks) and all(checks.values())
    out["all_ok"] = bool(ok)
    return out


# --------------------------------------------------------------------------- thuc thi (DB)
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_artifacts() -> dict[str, Any]:
    out: dict[str, Any] = {"samples_sha": {}, "contract_build_sha": {}}
    for name in DATASETS:
        base = ROOT / "outputs" / "datasets" / name
        out["samples_sha"][name] = sha256_file(base / "samples.parquet")
        out["contract_build_sha"][name] = json.loads((base / "dataset_contract.json").read_text(encoding="utf-8"))["build_config_sha256"]
        if out["contract_build_sha"][name] != DATASETS[name]["build_config_sha256"]:
            raise SystemExit(f"build_config_sha256 trong contract artifact != hang so da doi chieu ({name})")
    return out


def check_gates(args: argparse.Namespace) -> list[str]:
    problems: list[str] = []
    if not database_allowed(args.db):
        problems.append(f"ten DB khong hop le ({args.db!r}): chi `warehouse_dsdev_*`, khong bao gio DB van hanh")
    if not args.skip_time_gate and not time_gate_ok(dt.datetime.now(dt.timezone.utc)):
        problems.append("dang trong khung crawl 00:30-17:00 gio VN")
    if not args.skip_crawler_gate:
        done = subprocess.run([sys.executable, str(REPO / "backend" / "scripts" / "check_crawl_progress.py")], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        ok, why = crawler_gate_ok(done.stdout)
        if not ok:
            problems.append(f"cong crawler: {why}")
    return problems


def connect(db: str):
    sys.path.insert(0, str(REPO / "backend"))
    import mysql.connector  # noqa: PLC0415
    from app.core.config import settings  # noqa: PLC0415
    conn = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, database=db, time_zone="+00:00", autocommit=False, connection_timeout=10)
    cursor = conn.cursor()
    cursor.execute("SET SESSION TRANSACTION READ ONLY")
    cursor.execute(f"SET SESSION MAX_EXECUTION_TIME={MAX_EXECUTION_MS}")
    cursor.close()
    return conn


def fetch(conn, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, tuple(params))
        return cursor.fetchall()
    finally:
        cursor.close()


def explain(conn, sql: str, params: Sequence[Any], driving: str, allow_index_scan: Sequence[str] = ()) -> dict[str, Any]:
    plan = fetch(conn, "EXPLAIN " + sql, params)                       # KHONG ANALYZE: khong thuc thi query
    ok, problems = explain_ok(plan, driving=driving, allow_index_scan=allow_index_scan)
    return {"ok": ok, "problems": problems, "plan": [{k: (str(v) if v is not None else None) for k, v in row.items()} for row in plan]}


# --------------------------------------------------------------------------- vong batch (keyset <=1000, mot query mot luc, khong retry)
def _batched(conn, sql, fixed, driving, cursor_col, budget, max_batches, started):
    """Lap keyset tren cot sap xep duy nhat; dung khi het du lieu, het budget thoi gian, het so batch hoac loi/timeout (ghi nhan, KHONG retry)."""
    last, batches, rows_total, error = 0, 0, 0, None
    while batches < max_batches and time.time() - started < budget:
        try:
            rows = fetch(conn, sql, [*fixed, last])
        except Exception as exc:  # noqa: BLE001 - timeout/loi: dung, ghi nhan, khong retry
            error = f"{type(exc).__name__}: {exc}"
            break
        if not rows:
            break
        yield rows
        batches += 1
        rows_total += len(rows)
        last = rows[-1][cursor_col]
        if len(rows) < BATCH:
            break
        time.sleep(0.5)
    yield {"_done": True, "batches": batches, "rows": rows_total, "error": error}


def _count(counter, items):
    for item in items:
        counter[item] = counter.get(item, 0) + 1


def run_queries(conn, which: list[str], explain_only: bool, budget_s: int, max_batches: int) -> dict[str, Any]:
    import pandas as pd  # noqa: PLC0415
    sys.path.insert(0, str(REPO / "ml"))
    from dataset_builder.features import parse_room_area_m2  # noqa: PLC0415   (oracle = ham parse cua builder: ghi ro trong bao cao)
    report: dict[str, Any] = {"queries": {}}
    started = time.time()
    for name in DATASETS:
        dataset = ROOT / "outputs" / "datasets" / name / "samples.parquet"
        parquet = pd.read_parquet(dataset)
        if parquet["warehouse_record_id"].duplicated().any():
            raise SystemExit("Parquet co warehouse_record_id trung: khong the join one-to-one")
        by_record = parquet.set_index("warehouse_record_id")
        entry: dict[str, Any] = {}
        report["queries"][name] = entry
        count = fetch(conn, SQL_COUNT_SELECTED, [name])[0]["n"]
        entry["selected_in_db"], entry["selected_in_parquet"] = int(count), int(len(parquet))
        entry["denominator_ok"] = bool(count == len(parquet) == EXPECTED_SELECTED)
        if "q1" in which:
            plan = explain(conn, SQL_Q1, [name, 0], driving="s")
            entry["q1_explain"] = plan
            if plan["ok"] and not explain_only:
                counts: dict[str, int] = {}
                seen = 0
                for chunk in _batched(conn, SQL_Q1, [name], "s", "record_id", budget_s, max_batches, started):
                    if isinstance(chunk, dict):
                        entry["q1_run"] = chunk
                        break
                    seen += len(chunk)
                    for row in chunk:
                        _count(counts, q1_violations(row) or ["ok"])
                entry["q1_violation_counts"], entry["q1_rows_seen"] = counts, seen
        if "q2" in which:
            for k in (1, 3, 7, 14):
                sql = SQL_Q2.format(k=k)
                plan = explain(conn, sql, [name, 0], driving="s")
                entry[f"q2_h{k}_explain"] = plan
                if plan["ok"] and not explain_only:
                    counts, labelled, price_bad, flag_bad, shifted, seen = {}, 0, 0, 0, 0, 0
                    for chunk in _batched(conn, sql, [name], "s", "record_id", budget_s, max_batches, started):
                        if isinstance(chunk, dict):
                            entry[f"q2_h{k}_run"] = chunk
                            break
                        seen += len(chunk)
                        shifted += q2_sensitivity_count(chunk, k)
                        for row in chunk:
                            _count(counts, q2_violations(row, k) or ["ok"])
                            pr = by_record.loc[row["record_id"]] if row["record_id"] in by_record.index else None
                            if pr is None:
                                counts["source_missing_in_parquet"] = counts.get("source_missing_in_parquet", 0) + 1
                                continue
                            if bool(pr[f"has_label_h{k}"]) != bool(row["has_label"]):
                                flag_bad += 1
                            if row["has_label"]:
                                labelled += 1
                                if row["tp_price"] is None or float(pr[f"y_price_h{k}"]) != float(row["tp_price"]):
                                    price_bad += 1
                    entry[f"q2_h{k}"] = {"violation_counts": counts, "rows_seen": seen, "labelled_rows": labelled, "expected_labelled": EXPECTED_LABELLED[k], "price_vs_parquet_mismatch": price_bad,
                                         "has_label_vs_parquet_mismatch": flag_bad, "control_shifted_k_plus_1_violations": shifted}
        if "q3" in which:
            plan = explain(conn, SQL_Q3_GROUPS, [name], driving="s", allow_index_scan=("s",))
            entry["q3_groups_explain"] = plan
            if plan["ok"] and not explain_only:
                groups = fetch(conn, SQL_Q3_GROUPS, [name])
                ids = sorted({g["assignment"] for g in groups})
                verdicts = []
                for i in range(0, len(ids), 100):
                    chunk_ids = ids[i:i + 100]
                    sql = SQL_Q3_ROWS.format(marks=",".join(["%s"] * len(chunk_ids)))
                    rows = fetch(conn, sql, [BATCH_ID, BATCH_ID, name, *chunk_ids])
                    wanted = {(g["assignment"], g["vn_date"]) for g in groups}
                    bucket: dict[Any, list] = {}
                    for r in rows:
                        if (r["assignment"], r["vn_date"]) in wanted:
                            bucket.setdefault((r["assignment"], r["vn_date"]), []).append(r)
                    verdicts.extend(q3_group_verdict(g) for g in bucket.values())
                    time.sleep(0.5)
                entry["q3"] = {"groups": len(groups), "candidate_rows": int(sum(g["n"] for g in groups)), "selected_rows_in_groups": int(sum(int(g["n_selected"]) for g in groups)),
                               "superseded_rows": int(sum(g["n"] - int(g["n_selected"]) for g in groups)), "exactly_one_selected_violations": sum(1 for v in verdicts if not v["exactly_one_selected"]),
                               "selected_not_min_key_violations": sum(1 for v in verdicts if not v["selected_is_min_key"]), "strict_order_groups": sum(1 for v in verdicts if v["strict_order"]),
                               "control_reverse_order_would_pick_other": sum(1 for v in verdicts if v["reverse_order_would_pick_other"]), "groups_evaluated": len(verdicts)}
        if "q4" in which:
            plan = explain(conn, SQL_Q4, [name, 0], driving="s")
            entry["q4_explain"] = plan
            if plan["ok"] and not explain_only:
                counts, drift, seen = {}, {}, 0
                for chunk in _batched(conn, SQL_Q4, [name], "s", "record_id", budget_s, max_batches, started):
                    if isinstance(chunk, dict):
                        entry["q4_run"] = chunk
                        break
                    seen += len(chunk)
                    for row in chunk:
                        pr = by_record.loc[row["record_id"]] if row["record_id"] in by_record.index else None
                        if pr is None:
                            counts["source_missing_in_parquet"] = counts.get("source_missing_in_parquet", 0) + 1
                            continue
                        _count(counts, q4_fidelity_violations(row, pr.to_dict(), parse_room_area_m2) or ["ok"])
                        _count(drift, q4_drift(row) or ["no_drift"])
                entry["q4"] = {"fidelity_violation_counts": counts, "assignment_vs_observation_drift_counts": drift, "rows_seen": seen, "oracle_for_room_area": "dataset_builder.features.parse_room_area_m2"}
    report["elapsed_s"] = round(time.time() - started, 1)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="warehouse_dsdev_20261004_3src")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--step0", action="store_true")
    mode.add_argument("--explain", default="")
    mode.add_argument("--run", default="")
    parser.add_argument("--gpt-approval", default="", help="tham chieu file GPT da duyet query thuc te (bat buoc voi --run)")
    parser.add_argument("--budget-seconds", type=int, default=1200)
    parser.add_argument("--max-batches", type=int, default=200)
    parser.add_argument("--skip-time-gate", action="store_true", help="CHI cho kiem thu cuc bo; bao cao ghi ro")
    parser.add_argument("--skip-crawler-gate", action="store_true", help="CHI cho kiem thu cuc bo; bao cao ghi ro")
    args = parser.parse_args(argv)
    problems = check_gates(args)
    if problems:
        print("GATE FAIL (khong mo ket noi du lieu):", *problems, sep="\n  - ")
        return 3
    if args.run and not args.gpt_approval:
        print("--run can --gpt-approval <tham chieu file GPT da duyet query thuc te>; chua co thi chi dung --step0/--explain (khong mo ket noi truoc khi co duyet).")
        return 2
    artifacts = load_artifacts()
    report: dict[str, Any] = {"db": args.db, "created_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "gates_skipped": {"time": args.skip_time_gate, "crawler": args.skip_crawler_gate},
                              "max_execution_ms": MAX_EXECUTION_MS, "batch": BATCH}
    conn = connect(args.db)
    try:
        if args.step0:
            rows = fetch(conn, SQL_STEP0, list(DATASETS))
            report["step0"] = step0_verdict(rows, artifacts["samples_sha"])
            print(json.dumps(report["step0"], ensure_ascii=False, indent=1, default=str))
        else:
            which = [w for w in (args.explain or args.run).split(",") if w]
            if not set(which) <= {"q1", "q2", "q3", "q4"}:
                raise SystemExit(f"query khong biet: {which}")
            report["approval_reference"] = args.gpt_approval
            report["run"] = run_queries(conn, which, explain_only=bool(args.explain), budget_s=args.budget_seconds, max_batches=args.max_batches)
            print(json.dumps(report["run"], ensure_ascii=False, indent=1, default=str)[:6000])
    finally:
        conn.close()
    out_dir = ROOT / "outputs" / "model-diagnosis-20261008" / "warehouse_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{'step0' if args.step0 else ('explain' if args.explain else 'run')}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("bao cao:", path)
    return 0 if (report.get("step0", {}).get("all_ok") or "run" in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
