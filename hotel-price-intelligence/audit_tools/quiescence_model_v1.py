"""Mo hinh THUAN (khong DB, khong import ket noi) cua kiem tra "khong con phien MySQL nao cua phase" sau khi child process cua mot phase (Ph3 clone / Ph4 init / Ph5 build) ket thuc hoac bi kill.

TRANG THAI: THIET KE + MO HINH + TEST. KHONG duoc noi vao bat ky runner/DB nao; KHONG co quyen thuc thi; KHONG phai bang chung ve server that (Q0 self-test live can duyet rieng).

HAI TAP KHAC NHAU (GPT 68 §4.1) — khong duoc nhap nhang:
  * SIEU TAP CHAN quiescence ("con co the la phien cua phase"): hop F = (a) ∪ (b)
      (a) id > baseline ∧ user == user ung dung ∧ (DB IS NULL ∨ DB ∈ db_scope)         # bat DB=NULL, ket noi lai (id moi), khong phu thuoc target ton tai
      (b) id > baseline ∧ `_pid` ∈ cay process cua phase                                 # bat phien `USE` sang DB khac / user khac
    Mot phien khop F chi CHUNG TO "co the thuoc phase" => chan VERIFIED; KHONG tu cap quyen KILL.
  * SO HUU DUONG (positive ownership) — dieu kien CAN de de cu KILL (chi la du lieu, KILL van la quyen rieng):
      user == user ung dung ∧ `_pid` ∈ cay process cua phase ∧ (DB IS NULL ∨ DB ∈ db_scope) ∧ DB ∉ protected_dbs ∧ id > baseline.
    PID trung khong tu cap quyen; khop chi theo user/DB (false alarm cung user) khong du.
  * DANH SACH CAM (protected_dbs: DB van hanh/crawler/warehouse hien hanh) THANG moi rule: phien khop F nam trong protected DB => `scope_violation` => NOT_QUIESCENT, KHONG de cu KILL nao.

QUY TAC VERIFIED (tat ca phai dung):
  S  self-test truoc phase da chung minh kenh quan sat tren CHINH server nay (phien khac cung user ke ca DB=NULL thay duoc; `_pid` thay duoc; INNODB_TRX doc duoc; phien bien mat sau khi dong).
  P  >= 2 probe SAU khi child chet, status=ok, khong cat (LIMIT 50 cham tran => UNKNOWN), cach nhau >= MIN_SPACING_S bang dong ho HUU HAN (khong NaN/Inf/bool), rolling_back doc duoc va rong.
  U  uptime server huu han, khong giam (khong khoi dong lai) tu baseline qua cac probe; thieu => UNKNOWN (gia dinh id ket noi don dieu chi tin duoc khi khong doi vong doi server).
  Moi bat dinh/loi/sai kieu => UNKNOWN co ly do (khong bao gio exception roi thanh CLEAR).
  `kill_candidates`: chi tu probe CUOI CUNG hop le (khong cat, khong loi) con khop, ownership duong; BUSY roi CLEAR => rong. Trong moi truong hop day chi la du lieu de xet, khong phai hanh dong.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

PROBE_ROW_LIMIT = 50
MIN_SPACING_S = 10.0
SELFTEST_KEYS = ("visible_other_session", "visible_db_null_session", "pid_attribute_visible", "innodb_trx_readable", "removed_after_close")
VERIFIED, NOT_QUIESCENT, UNKNOWN = "VERIFIED", "NOT_QUIESCENT", "UNKNOWN"
CLEAR, BUSY = "CLEAR", "BUSY"                                                                              # ket qua cua MOT lan probe
ROW_KEYS = ("PROCESSLIST_ID", "PROCESSLIST_USER", "PROCESSLIST_DB", "pid")
ASSUMPTIONS = ("connection_ids_monotonic_within_phase (sanity: two successive ids in self-test; NOT a proof; server restart detected only via uptime)",
               "visibility_established_only_by_selftest_on_this_server (not by this model)",
               "pid_tree_is_the_runner_responsibility (rule b is best-effort; rule a is the primary net)",
               "quiescence_means_no_further_writes_from_phase_sessions; it does not prove written data is consistent")


def _is_int(value: Any) -> bool:
    return type(value) is int


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _row_error(row: Any) -> str | None:
    if not isinstance(row, Mapping) or any(k not in row for k in ROW_KEYS):
        return "row_shape"
    if not _is_int(row["PROCESSLIST_ID"]) or row["PROCESSLIST_ID"] <= 0:
        return "row_id"
    if row["PROCESSLIST_USER"] is not None and type(row["PROCESSLIST_USER"]) is not str:
        return "row_user"
    if row["PROCESSLIST_DB"] is not None and type(row["PROCESSLIST_DB"]) is not str:
        return "row_db"
    if row["pid"] is not None and not _is_int(row["pid"]):
        return "row_pid"
    return None


def classify_probe(probe: Mapping[str, Any], *, baseline_thread_id: int, app_user: str, db_scope: Iterable[str], child_pids: Iterable[int], probe_session_id: int,
                   protected_dbs: Iterable[str] = ()) -> dict[str, Any]:
    """Phan loai MOT lan probe: CLEAR / BUSY (co phien khop F, hoac scope_violation) / UNKNOWN (loi/cat/hinh dang sai/rolling_back khong doc duoc va khong co phien khop).
    `matches[i]["owned"]` = so huu duong (xem docstring module); `violations` = id phien khop F nam trong protected DB."""
    scope, pids, protected = set(db_scope), set(child_pids), set(protected_dbs)
    reasons: list[str] = []
    if not isinstance(probe, Mapping) or probe.get("status") != "ok":
        return {"status": UNKNOWN, "matches": [], "violations": [], "reasons": [f"probe_status_{probe.get('status') if isinstance(probe, Mapping) else 'malformed'}"]}
    rows = probe.get("rows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return {"status": UNKNOWN, "matches": [], "violations": [], "reasons": ["rows_not_a_sequence"]}
    if probe.get("truncated") is not False or len(rows) >= PROBE_ROW_LIMIT:
        reasons.append("rows_truncated_or_at_limit")                                                    # cham tran hang => khong the noi "khong con gi"
    matches: list[dict[str, Any]] = []
    violations: list[int] = []
    for row in rows:
        error = _row_error(row)
        if error is not None:
            reasons.append(error)
            continue
        if row["PROCESSLIST_ID"] == probe_session_id or row["PROCESSLIST_ID"] <= baseline_thread_id:
            continue
        rule_a = row["PROCESSLIST_USER"] == app_user and (row["PROCESSLIST_DB"] is None or row["PROCESSLIST_DB"] in scope)
        rule_b = row["pid"] is not None and row["pid"] in pids
        if not (rule_a or rule_b):
            continue
        in_protected = row["PROCESSLIST_DB"] in protected
        owned = (not in_protected and row["PROCESSLIST_USER"] == app_user and row["pid"] is not None and row["pid"] in pids
                 and (row["PROCESSLIST_DB"] is None or row["PROCESSLIST_DB"] in scope))
        matches.append({"id": row["PROCESSLIST_ID"], "user": row["PROCESSLIST_USER"], "db": row["PROCESSLIST_DB"], "pid": row["pid"], "rule": "a" if rule_a else "b", "owned": owned})
        if in_protected:
            violations.append(row["PROCESSLIST_ID"])
    rolling = probe.get("rolling_back")
    if rolling is None or not isinstance(rolling, Sequence) or isinstance(rolling, (str, bytes)):
        reasons.append("rolling_back_unreadable")
    elif rolling:
        reasons.append("rolling_back_present")
    if violations:
        reasons.append("scope_violation_protected_database")
    if reasons:
        return {"status": UNKNOWN if not matches else BUSY, "matches": matches, "violations": violations, "reasons": reasons}
    return {"status": BUSY if matches else CLEAR, "matches": matches, "violations": [], "reasons": []}


def _unknown(reasons: Sequence[str]) -> dict[str, Any]:
    return {"status": UNKNOWN, "reasons": list(reasons), "kill_candidates": [], "assumptions": list(ASSUMPTIONS)}


def evaluate_quiescence(*, selftest: Mapping[str, Any] | None, probes: Sequence[Mapping[str, Any]], child_exit_monotonic: float | None, baseline_thread_id: int, baseline_uptime_s: float | None,
                        app_user: str, db_scope: Iterable[str], child_pids: Iterable[int], probe_session_id: int, protected_dbs: Iterable[str] = ()) -> dict[str, Any]:
    """Quyet dinh cuoi: VERIFIED / NOT_QUIESCENT / UNKNOWN. Chi VERIFIED mo duoc phase ke hoac don dep. Khong bao gio nem ngoai le: dau vao sai kieu => UNKNOWN co ly do."""
    try:
        return _evaluate(selftest=selftest, probes=probes, child_exit_monotonic=child_exit_monotonic, baseline_thread_id=baseline_thread_id, baseline_uptime_s=baseline_uptime_s, app_user=app_user,
                         db_scope=list(db_scope), child_pids=list(child_pids), probe_session_id=probe_session_id, protected_dbs=list(protected_dbs))
    except Exception as exc:                                                                              # noqa: BLE001
        return _unknown([f"exception: {type(exc).__name__}"])


def _evaluate(*, selftest, probes, child_exit_monotonic, baseline_thread_id, baseline_uptime_s, app_user, db_scope, child_pids, probe_session_id, protected_dbs) -> dict[str, Any]:
    if not isinstance(selftest, Mapping) or any(selftest.get(k) is not True for k in SELFTEST_KEYS):
        return _unknown(["selftest_missing_or_not_all_true"])
    if (not _is_int(baseline_thread_id) or baseline_thread_id < 0 or type(app_user) is not str or not app_user or not _is_int(probe_session_id)
            or not all(type(d) is str for d in db_scope + protected_dbs) or not all(_is_int(p) for p in child_pids) or set(db_scope) & set(protected_dbs)):
        return _unknown(["bad_scope_arguments"])
    if not _finite(child_exit_monotonic) or not _finite(baseline_uptime_s) or baseline_uptime_s < 0:
        return _unknown(["bad_clock: child_exit_or_baseline_uptime_not_finite_number"])
    if not isinstance(probes, Sequence) or isinstance(probes, (str, bytes)) or len(probes) < 2:
        return _unknown(["need_two_probes"])
    if not all(isinstance(p, Mapping) for p in probes):
        return _unknown(["probe_not_a_mapping"])
    if not all(_finite(p.get("observed_at")) for p in probes):
        return _unknown(["bad_clock: observed_at_not_finite_number"])
    uptimes = [p.get("uptime_s") for p in probes]
    if not all(_finite(u) for u in uptimes) or uptimes[0] < baseline_uptime_s or any(b < a for a, b in zip(uptimes, uptimes[1:])):
        return _unknown(["server_lifecycle_unverifiable: uptime_missing_or_decreased"])
    classified = [classify_probe(p, baseline_thread_id=baseline_thread_id, app_user=app_user, db_scope=db_scope, child_pids=child_pids, probe_session_id=probe_session_id,
                                 protected_dbs=protected_dbs) for p in probes]
    last_two = list(zip(probes[-2:], classified[-2:]))
    reasons: list[str] = []
    for index, (probe, _) in enumerate(last_two):
        if probe["observed_at"] <= child_exit_monotonic:
            reasons.append(f"probe{index}_not_after_child_exit")
    if last_two[1][0]["observed_at"] - last_two[0][0]["observed_at"] < MIN_SPACING_S:
        reasons.append("probes_closer_than_min_spacing")
    if reasons:
        return _unknown(reasons)
    statuses = [r["status"] for _, r in last_two]
    if UNKNOWN in statuses:
        return _unknown([f"probe_unknown: {r['reasons']}" for _, r in last_two if r["status"] == UNKNOWN])
    if BUSY in statuses:
        last = classified[-1]
        clean_last = last["status"] == BUSY and not last["reasons"] and not last["violations"]
        candidates = sorted({m["id"] for m in last["matches"] if m["owned"]}) if clean_last else []                  # chi probe CUOI hop le, khong fallback probe truoc
        why = ["session_of_phase_still_present"] + (["scope_violation_protected_database"] if any(c["violations"] for c in classified[-2:]) else [])
        return {"status": NOT_QUIESCENT, "reasons": why, "kill_candidates": candidates, "assumptions": list(ASSUMPTIONS)}
    return {"status": VERIFIED, "reasons": [], "kill_candidates": [], "assumptions": list(ASSUMPTIONS)}
