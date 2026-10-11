"""Audit warehouse DAN XUAT (doc-chi) cho dataset train-v3 dev1b/dev3b (GPT file 30 muc 4, file 32 W1-W4).

    python warehouse_audit_v3.py --step0   --gpt-approval <file> --crawl-evidence <file>
    python warehouse_audit_v3.py --explain all|q1,q2,...  ...     # CHI Buoc 0 + EXPLAIN (khong ANALYZE, khong chay COUNT/Q1-Q4)
    python warehouse_audit_v3.py --run     all|q1,q2,...  ...     # chay that theo batch <=1000 (them COUNT mau so bat buoc)

Moi che do chay DB deu: (1) kiem TRUOC khi mo ket noi: ten DB `warehouse_dsdev_*`; file duyet cua GPT gan voi DUNG sha256 cua controller nay + bo SQL + che do; khung gio VN ngoai 00:30-17:00; bang chung
crawler-idle con han (do Claude tao bang `crawl_idle_evidence.py`, controller KHONG cham DB van hanh/.env); (2) phien READ ONLY, MAX_EXECUTION_TIME <= 10 s, mot query mot luc, khong retry; (3) xac minh Buoc 0
(manifest dataset trong DB khop artifact: status/step/batch/build SHA/`output_parquet_sha256_json["samples.parquet"]["file_sha256"]` chinh xac/rows) TRUOC moi truy van khac; (4) moi truy van, ke ca COUNT, Q1 control,
Q3 rows, di qua cung mot wrapper: EXPLAIN (plan bi tu choi neu quet toan bo) -> thuc thi -> gioi han so dong -> ngan sach thoi gian/so query. Khong co co bo qua gate cho DB that.
Cong crawler/gio duoc KIEM LAI o MOI ranh gioi query/batch (doc lai tep bang chung, margin truoc het han va truoc 00:30); mat cong => dung, UNVERIFIED (khong phai FAIL khoa hoc), ghi chuoi sha bang chung da dung.
Plan duoc cache THEO (dataset, nhan, sha SQL); --run bat buoc --budget-seconds va --max-queries tuong minh. Ket qua la verdict may doc duoc: PASS_FULL / PARTIAL_PASS / FAIL / UNVERIFIED / REFUSED (+ma thoat 0/4/1/4/3);
chi PASS_FULL (dung tap dataset x 9 nhan, moi status == PASS) moi la audit day du dat. Cac ham danh gia la HAM THUAN de test voi negative control.
"""
from __future__ import annotations

import argparse
import datetime as dt
import decimal
import hashlib
import json
import numbers
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ROOT = REPO.parent
CONTROLLER_PATH = Path(__file__).resolve()
APPROVALS_DIR = ROOT / "discuss" / "model-results-improvement-20261008"
AUDIT_OUT_DIR = ROOT / "outputs" / "model-diagnosis-20261008" / "warehouse_audit"
DB_RE = re.compile(r"^warehouse_dsdev_[A-Za-z0-9_]{1,60}$")
BATCH_ID = "b20261004_3src"
SAMPLES_FILE = "samples.parquet"
DATASETS = {
    "ds_20261006_dev1b": {"build_config_sha256": "6494c1d4ece098e12855644f26780eecfe6706be32baee8a8f486b8b10069928", "horizon": 1},
    "ds_20261006_dev3b": {"build_config_sha256": "45f2fc6ab177505de3b5a1590f6cab2988bb9e05ac72a1996127c2760289cbd8", "horizon": 3},
}
EXPECTED_SELECTED = 146431
EXPECTED_LABELLED = {1: 128583, 3: 111515, 7: 83077, 14: 45049}
EXPECTED_BURN_IN = 43662
EXPECTED_MATCHED = 192590            # exact/alias matches tai prefix cua phieu (price_ok khong mat dong nao)
EXPECTED_SUPERSEDED = 1335           # ROWS (khong phai groups) bi daily-dedup loai
BATCH = 1000
MAX_EXECUTION_MS = 10_000
MAX_PACKED_GROUPS = 100
DEFAULT_PREFLIGHT_BUDGET_S = 120
DEFAULT_PREFLIGHT_MAX_QUERIES = 200
PAUSE_S = 0.4
EVIDENCE_SCHEMA = "crawl_idle_evidence_v2"
EVIDENCE_MAX_AGE_S = 120
DEFAULT_MAX_BATCHES = 200
EVIDENCE_MAX_VALIDITY_S = 600
FAMILIES = ("count", "q1", "q1_control", "q2", "q3", "q4")
PREFIX41_IDS_SHA256 = "70ffa3df2a324662c915875202f4df7b78ed21fce5d3a05639721b481a919caa"       # sha256(json.dumps(3000 id nho nhat cua Parquet)) da ghim o manifest cua lan pilot (file 41)
PREFIX41_EXPECTED = {"free_cancellation_mismatches": 25, "max_occupancy_parquet_na": 28}      # prereg tu GPT file 42 muc 4 (chi co nghia khi prefix id == PREFIX41_IDS_SHA256)
EXIT_CODES = {"PASS_FULL": 0, "PILOT_PARTIAL": 4, "EXPLAIN_OK": 0, "STEP0_OK": 0, "FAIL": 1, "REFUSED": 3, "UNVERIFIED": 4, "PARTIAL_PASS": 4, "EXPLAIN_REFUSED": 4}
VN = dt.timezone(dt.timedelta(hours=7))
UTC = dt.timezone.utc
_sleep = time.sleep
_monotonic = time.monotonic                 # dong ho don dieu cho ngan sach/elapsed (khong bi chinh dong ho he thong)
CONNECT_SET_STATEMENTS = 2                 # SET READ ONLY + SET MAX_EXECUTION_TIME trong connect(): TINH vao mau so lenh SQL, cung Buoc 0 SELECT, EXPLAIN va SELECT du lieu


def _now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


# --------------------------------------------------------------------------- guard thuan
def database_allowed(name: str) -> bool:
    return bool(DB_RE.match(name or ""))


def time_gate_ok(now_utc: dt.datetime) -> bool:
    """Ngoai khung crawl 00:30-17:00 gio VN."""
    local = now_utc.astimezone(VN)
    minutes = local.hour * 60 + local.minute
    return minutes >= 17 * 60 or minutes < 30


def _parse_utc(text: Any) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


class PlanRefused(Exception):
    def __init__(self, label: str, problems: Sequence[str]):
        super().__init__(f"{label}: {'; '.join(problems)}")
        self.label, self.problems = label, list(problems)


class Incomplete(Exception):
    pass


class GateLost(Incomplete):
    """Mat cong gio/crawler giua chung: dung, UNVERIFIED (khong phai ket luan khoa hoc)."""


TIME_MARGIN = dt.timedelta(minutes=10)
GATE_INTERVAL_S = 0.0                       # doc lai tep bang chung o MOI ranh gioi query (rat re); tham so chi de test


def gate_time_ok(now_utc: dt.datetime) -> bool:
    """Ngoai khung crawl 00:30-17:00 VN VA con it nhat TIME_MARGIN truoc khi khung crawl bat dau lai."""
    return time_gate_ok(now_utc) and time_gate_ok(now_utc + TIME_MARGIN)


def validate_crawl_evidence(evidence: Any, now_utc: dt.datetime, *, margin_s: float = 0.0, require_read_only: bool = True, max_age_s: float | None = None) -> tuple[bool, str]:
    """Bang chung crawler-idle (tu `crawl_idle_evidence.py`, schema v2): con han (+margin), tuoi (tinh tu luc BAT DAU doc `observed_at_utc`) <= max_age_s, phien READ ONLY da xac minh,
    KHONG co run/item queued|running nao. Khong co bang chung hop le => khong chay."""
    if not isinstance(evidence, dict) or evidence.get("schema") != EVIDENCE_SCHEMA:
        return False, "bang chung crawler khong dung schema"
    observed, generated, expires = _parse_utc(evidence.get("observed_at_utc")), _parse_utc(evidence.get("generated_at_utc")), _parse_utc(evidence.get("expires_at_utc"))
    if observed is None or generated is None or expires is None:
        return False, "bang chung crawler thieu moc thoi gian hop le (observed/generated/expires)"
    if max(observed, generated) - dt.timedelta(seconds=120) > now_utc:
        return False, "bang chung crawler tu tuong lai"
    remaining = (expires - now_utc).total_seconds()
    if remaining < 0:
        return False, "bang chung crawler da het han"
    if remaining < margin_s:
        return False, "bang chung crawler con it han hon bien an toan cho mot query"
    if (expires - observed).total_seconds() > EVIDENCE_MAX_VALIDITY_S:
        return False, "bang chung crawler tu khai han qua dai"
    if max_age_s is not None and (now_utc - observed).total_seconds() > max_age_s:
        return False, f"bang chung crawler qua cu (tuoi {(now_utc - observed).total_seconds():.0f}s > {max_age_s:.0f}s tinh tu luc bat dau doc)"
    session = evidence.get("session")
    if require_read_only and not (isinstance(session, dict) and session.get("read_only_verified") is True):
        return False, "bang chung crawler khong chung minh phien READ ONLY"
    for key in ("active_runs", "active_items"):
        value = evidence.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return False, f"bang chung crawler: {key} khong phai so nguyen"
        if value != 0:
            return False, f"crawler dang co {key}={value} (phai = 0)"
    if isinstance(evidence.get("latest_run_id"), bool) or not isinstance(evidence.get("latest_run_id"), int):
        return False, "bang chung crawler thieu latest_run_id"
    return True, f"zero run/item queued|running; latest_run_id={evidence['latest_run_id']}; het han {evidence['expires_at_utc']}"


class EvidenceGate:
    """Doc lai tep bang chung (do nguon van hanh co tham quyen GIA HAN; controller khong tu gia han / tang TTL / cham DB van hanh) + gio o moi ranh gioi query; sai => GateLost.
    Ghi `sha_chain` va `receipts` (moi ban doi SHA: observed/generated/expires, tuoi luc dung)."""

    def __init__(self, path: Any, *, interval_s: float | None = None):
        self.path = Path(path)
        self.interval_s = GATE_INTERVAL_S if interval_s is None else interval_s
        self.checked_at: float | None = None
        self.sha_chain: list[str] = []
        self.receipts: list[dict[str, Any]] = []
        self.checks = 0
        self.last_verdict = ""
        self.last_content: Any = None

    def check(self, force: bool = False) -> None:
        mono = _monotonic()
        if not force and self.checked_at is not None and mono - self.checked_at < self.interval_s:
            return
        now = _now_utc()
        if not gate_time_ok(now):
            raise GateLost("cong gio: dang/sap trong khung crawl 00:30-17:00 gio VN (margin 10 phut)")
        try:
            raw = self.path.read_bytes()
            evidence = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise GateLost(f"cong crawler: khong doc duoc bang chung ({type(exc).__name__}: {exc})") from exc
        ok, why = validate_crawl_evidence(evidence, now, margin_s=MAX_EXECUTION_MS / 1000 + 5, max_age_s=EVIDENCE_MAX_AGE_S)
        if not ok:
            raise GateLost(f"cong crawler: {why}")
        sha = hashlib.sha256(raw).hexdigest()
        if not self.sha_chain or self.sha_chain[-1] != sha:
            self.sha_chain.append(sha)
            self.receipts.append({"sha256": sha, "observed_at_utc": evidence["observed_at_utc"], "generated_at_utc": evidence["generated_at_utc"], "expires_at_utc": evidence["expires_at_utc"],
                                  "age_s_at_first_use": round((now - _parse_utc(evidence["observed_at_utc"])).total_seconds(), 1)})
        self.checked_at, self.checks, self.last_verdict, self.last_content = mono, self.checks + 1, why, evidence


DRIVING_OK = ("range", "ref", "eq_ref", "const")
JOIN_OK = ("eq_ref", "ref", "const")


def explain_ok(plan: Sequence[Mapping[str, Any]], *, driving: str, allow_index_scan: Sequence[str] = ()) -> tuple[bool, list[str]]:
    """Plan EXPLAIN (list dict): bang dan dat chi `range|ref|eq_ref|const`; bang noi chi `eq_ref|ref|const`; `index` (quet toan index) chi cho bang duoc cho phep; ALL/khac => tu choi."""
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
    """Nhan h{k}: has_label <=> id <=> target; target la daily-selected sample CUNG dataset + CUNG assignment, ngay VN cua sample = nguon + k, cung hotel/check-in; dong ho QUAN SAT nguon/target
    (DATE(observed_at+7h)) phai khop ngay VN cua sample tuong ung va cach nhau dung k ngay; target observation ton tai, khong sold-out, gia > 0."""
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
    if row.get("sp_id") is None:
        out.append("source_observation_missing")
    elif row.get("sp_vn_date") != row.get("s_date"):
        out.append("source_observation_vn_date_ne_sample_date")
    if row.get("tp_id") is None:
        out.append("target_observation_missing")
    else:
        if row.get("tp_hotel") != row.get("s_hotel") or row.get("tp_checkin") != row.get("s_checkin"):
            out.append("target_observation_other_series")
        if row.get("tp_vn_date") != row.get("t_date"):
            out.append("target_observation_vn_date_ne_target_sample_date")
        if row.get("sp_vn_date") is not None and row.get("tp_vn_date") is not None and (row["tp_vn_date"] - row["sp_vn_date"]).days != k:
            out.append("observation_clock_gap_ne_k")
        if row.get("tp_sold_out") or row.get("tp_price") is None or float(row["tp_price"]) <= 0:
            out.append("target_observation_sold_out_or_no_price")
    return out


def q2_sensitivity_count(rows: Sequence[Mapping[str, Any]], k: int) -> dict[str, int]:
    """Control: cung vi tu nhung voi +k+1 phai thay vi pham tren MOI dong co nhan (chung minh predicate co the duong): theo ngay sample VA theo dong ho quan sat."""
    by_sample = sum(1 for r in rows if r.get("has_label") and r.get("t_date") is not None and (r["t_date"] - r["s_date"]).days != k + 1)
    by_clock = sum(1 for r in rows if r.get("has_label") and r.get("tp_vn_date") is not None and r.get("sp_vn_date") is not None and (r["tp_vn_date"] - r["sp_vn_date"]).days != k + 1)
    return {"sample_date": by_sample, "observation_clock": by_clock}


def q3_lineage_problems(row: Mapping[str, Any]) -> list[str]:
    """Moi dong cua nhom phai co du observation + item map + run map; thieu => loi (khong doi thanh 0 roi van cham min-key)."""
    out = []
    if row.get("po_id") is None:
        out.append("missing_observation")
    if row.get("source_item_id") is None or row.get("ownership_status") is None:
        out.append("missing_item_map")
    if row.get("source_run_id") is None or row.get("run_source_code") is None:
        out.append("missing_run_map")
    if row.get("item_source_code") is not None and row.get("run_source_code") is not None and row["item_source_code"] != row["run_source_code"]:
        out.append("item_run_source_mismatch")
    return out


def q3_group_verdict(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Mot nhom (assignment, ngay VN): dung MOT selected; selected phai co khoa nho nhat theo (owner_success DESC, prediction_time ASC, source_code, source_run_id, source_item_id).
    Neu thieu lineage => `evaluable=False` (khong cham min-key) va lineage_problems liet ke ro."""
    lineage = sorted({p for r in group for p in q3_lineage_problems(r)})
    selected = [r for r in group if r.get("is_daily_snapshot_selected")]
    verdict: dict[str, Any] = {"n": len(group), "n_selected": len(selected), "exactly_one_selected": len(selected) == 1, "lineage_problems": lineage, "evaluable": not lineage,
                               "strict_order": None, "selected_is_min_key": None, "reverse_order_would_pick_other": False}
    if lineage:
        return verdict

    def key(r):
        return (0 if r.get("ownership_status") == "owner_success" else 1, r["prediction_time"], str(r["run_source_code"]), int(r["source_run_id"]), int(r["source_item_id"]))
    keys = sorted(key(r) for r in group)
    strict = len(set(keys)) == len(keys)
    verdict["strict_order"] = strict
    verdict["selected_is_min_key"] = bool(len(selected) == 1 and key(selected[0]) == keys[0])
    verdict["reverse_order_would_pick_other"] = bool(strict and len(selected) == 1 and len(group) > 1 and key(selected[0]) != keys[-1])
    return verdict


_NULL_TYPE_NAMES = ("NAType", "NaTType")


def is_null_scalar(value: Any) -> bool:
    """Null scalar: None, pandas NA / NaT, float/numpy NaN. Gia tri hop le (so, bool, chuoi) khong null. Mang/list/dict/tap hop => TypeError (tu choi ro, khong coercion mu)."""
    if value is None or type(value).__name__ in _NULL_TYPE_NAMES:
        return True
    if isinstance(value, (str, bytes)):
        return False
    if hasattr(value, "__len__") or hasattr(value, "__iter__"):
        raise TypeError(f"khong phai scalar: {type(value).__name__}")
    if isinstance(value, (bool, numbers.Real)):
        return bool(value != value)
    return False


def flag_state(value: Any) -> str:
    """'NULL' | '0' | '1'. Gia tri ngoai {None/NA/NaN, 0, 1, False, True} => ValueError (mien gia tri la), khong ep kieu."""
    if is_null_scalar(value):
        return "NULL"
    if value in (0, 1, False, True):
        return "1" if bool(value) else "0"
    raise ValueError(f"mien gia tri khong hop le cho co: {value!r}")


def occupancy_state(value: Any) -> tuple[str, Any]:
    """('NULL', None) | ('VAL', int). Chi nhan so NGUYEN (int/numpy integer; float/Decimal co gia tri nguyen vd 2.0 -> 2, ghi ro la chuan hoa hop le); phan le (2.5), bool, chuoi, mien la => ValueError.
    KHONG cat phan le (int(2.5) = 2 lam mat bat dong that)."""
    if is_null_scalar(value):
        return "NULL", None
    if isinstance(value, bool):
        raise ValueError(f"max_occupancy khong the la bool: {value!r}")
    if isinstance(value, numbers.Integral):
        return "VAL", int(value)
    if isinstance(value, numbers.Real) and float(value).is_integer():
        return "VAL", int(value)
    if isinstance(value, decimal.Decimal) and value == value.to_integral_value():
        return "VAL", int(value)
    raise ValueError(f"max_occupancy ngoai mien so nguyen: {value!r}")


def q4_column_states(row: Mapping[str, Any], parquet: Mapping[str, Any]) -> dict[str, tuple[str, str, bool]]:
    """Theo tung cot chan doan: (trang thai DB tho truoc moi coercion cua DataFrame, trang thai Parquet, lech?)."""
    out: dict[str, Any] = {}
    for column in ("free_cancellation", "breakfast_included"):
        db_s, pq_s = flag_state(row.get(f"a_{column}")), flag_state(parquet.get(column))
        out[column] = (db_s, pq_s, db_s != pq_s)
    (db_s, db_v), (pq_s, pq_v) = occupancy_state(row.get("a_max_occupancy")), occupancy_state(parquet.get("max_occupancy"))
    out["max_occupancy"] = (db_s, pq_s, db_s != pq_s or (db_s == "VAL" and db_v != pq_v))
    out["_max_occupancy_values"] = (db_v, pq_v)                                                 # type: ignore[assignment]
    return out


def q4_fidelity_violations(row: Mapping[str, Any], parquet: Mapping[str, Any], parse_area) -> list[str]:
    """Assignment -> Parquet: city, max_occupancy, room_area(m2 theo parse), breakfast, cancellation phai khop. Null duoc nhan dang truoc moi phep so sanh/ep kieu (None, pd.NA, NaN, NaT cung la null);
    'biet' khong bao gio bi coi la null/True; mien gia tri la => `domain_error:<cot>` (FAIL), khong coercion mu. Orphan (assignment/hotel/observation thieu) la loi."""
    if row.get("a_id") is None:
        return ["missing_assignment"]
    if row.get("h_id") is None:
        return ["missing_hotel"]

    def eq(a, b):
        a_null, b_null = is_null_scalar(a), is_null_scalar(b)
        return a_null and b_null or (not a_null and not b_null and a == b)
    out: list[str] = []
    checks = (("city", lambda: eq(row.get("city"), parquet.get("city"))),
              ("max_occupancy", lambda: occupancy_state(row.get("a_max_occupancy")) == occupancy_state(parquet.get("max_occupancy"))),     # cung ham voi chan doan: nhat quan, khong cat phan le
              ("room_area_m2", lambda: eq(parse_area(row.get("a_room_area")), parquet.get("room_area_m2"))),
              ("breakfast_included", lambda: flag_state(row.get("a_breakfast_included")) == flag_state(parquet.get("breakfast_included"))),
              ("free_cancellation", lambda: flag_state(row.get("a_free_cancellation")) == flag_state(parquet.get("free_cancellation"))))
    for column, check in checks:
        try:
            if not check():
                out.append(column)
        except (TypeError, ValueError):
            out.append(f"domain_error:{column}")
    return out


class Q4Diagnosis:
    """Chan doan ROW-LEVEL tren prefix da ghim: bang cheo (DB tho -> Parquet), record_id THEO TUNG O, id null/lech, cap gia tri khong-null lech; moi id duoc ghi (classified / domain error / skipped)."""

    def __init__(self) -> None:
        self.cols: dict[str, dict[str, Any]] = {c: {"crosstab": {}, "cells": {}, "mismatch_ids": [], "db_null_ids": [], "parquet_null_ids": [], "value_pairs": []}
                                                for c in ("free_cancellation", "breakfast_included", "max_occupancy")}
        self.domain_errors: list[dict[str, Any]] = []
        self.skipped: list[dict[str, Any]] = []
        self.ids: list[int] = []                 # MOI id da duoc xu ly (classified + domain error + skipped)
        self.classified_ids: list[int] = []
        self.rows = 0

    def skip(self, record_id: int, reason: str) -> None:
        """Hang khong the phan loai (orphan, thieu trong Parquet...): ghi lai, KHONG de bien mat khoi ledger."""
        self.ids.append(record_id)
        self.skipped.append({"record_id": record_id, "reason": reason})

    def add(self, record_id: int, row: Mapping[str, Any], parquet: Mapping[str, Any]) -> None:
        self.rows += 1
        self.ids.append(record_id)
        try:
            states = q4_column_states(row, parquet)
        except (TypeError, ValueError) as exc:
            self.domain_errors.append({"record_id": record_id, "error": f"{type(exc).__name__}: {exc}"})
            return
        self.classified_ids.append(record_id)
        occupancy_values = states.pop("_max_occupancy_values")
        for column, (db_s, pq_s, mismatch) in states.items():
            slot = self.cols[column]
            key = f"{db_s}|{pq_s}"
            slot["crosstab"][key] = slot["crosstab"].get(key, 0) + 1
            slot["cells"].setdefault(key, []).append(record_id)
            if db_s == "NULL":
                slot["db_null_ids"].append(record_id)
            if pq_s == "NULL":
                slot["parquet_null_ids"].append(record_id)
            if mismatch:
                slot["mismatch_ids"].append(record_id)
                if column == "max_occupancy" and db_s == "VAL" and pq_s == "VAL":
                    slot["value_pairs"].append({"record_id": record_id, "db": occupancy_values[0], "parquet": occupancy_values[1]})


def diag_integrity(diag: Q4Diagnosis, expected_ids: set[int] | None = None) -> dict[str, Any]:
    """Dieu kien du de phat hanh verdict DUONG: 0 domain error, 0 skipped/orphan, moi id da xu ly duoc phan loai, khong trung; neu co `expected_ids` thi bang dung tap (khong thieu/thua)."""
    out: dict[str, Any] = {"rows_seen": len(diag.ids), "classified": len(diag.classified_ids), "domain_errors": len(diag.domain_errors), "skipped": len(diag.skipped),
                           "duplicates": len(diag.ids) - len(set(diag.ids))}
    if expected_ids is not None:
        out.update({"expected": len(expected_ids), "missing_ids": len(expected_ids - set(diag.ids)), "extra_ids": len(set(diag.ids) - expected_ids)})
    out["eligible"] = bool(out["domain_errors"] == 0 and out["skipped"] == 0 and out["duplicates"] == 0
                           and (expected_ids is None or (out["missing_ids"] == 0 and out["extra_ids"] == 0)))
    return out


def evaluate_null_hypotheses(diag: Q4Diagnosis, prefix_ids_sha256: str | None = None, expected_ids: set[int] | None = None) -> dict[str, Any]:
    """H_NULL (moi flag): ung ho NULL->True CHI KHI moi DB 0->Parquet False, DB 1->True, moi DB NULL quan sat duoc->True va khong co o khac; khong co DB NULL => NOT_EXERCISED.
    H_controller (max_occupancy): dung CHI KHI tap DB NULL == tap Parquet NA (theo record_id) va 0 lech gia tri khong-null. Khong dung tong/marginal.
    Neu ledger KHONG day du/hop le (domain error, orphan/skipped, thieu/thua id) => moi verdict duong la UNVERIFIED va matches_prereg = false (khong goi mot tap con la toan prefix)."""
    integrity = diag_integrity(diag, expected_ids)
    eligible = integrity["eligible"]
    out: dict[str, Any] = {"prefix_ids_sha256": prefix_ids_sha256, "prefix_ids_equal_pinned": prefix_ids_sha256 == PREFIX41_IDS_SHA256, "domain_errors": len(diag.domain_errors), "integrity": integrity}
    allowed = {"0|0", "1|1", "NULL|1"}
    for column in ("free_cancellation", "breakfast_included"):
        crosstab = diag.cols[column]["crosstab"]
        db_null = sum(v for k, v in crosstab.items() if k.startswith("NULL|"))
        null_true = crosstab.get("NULL|1", 0)
        mismatches = len(diag.cols[column]["mismatch_ids"])
        verdict = "NOT_EXERCISED" if db_null == 0 else ("SUPPORTED" if set(crosstab) <= allowed and null_true == db_null else "REJECTED")
        if not eligible:
            verdict = "UNVERIFIED"
        out[column] = {"null_to_true": verdict, "db_null": db_null, "db_null_to_parquet_true": null_true, "mismatching_rows": mismatches, "explains_all_mismatches": mismatches == null_true,
                       "other_cells": sorted(set(crosstab) - allowed), "crosstab": crosstab}
    occupancy = diag.cols["max_occupancy"]
    db_null_ids, pq_null_ids = set(occupancy["db_null_ids"]), set(occupancy["parquet_null_ids"])
    non_null_mismatch = [i for i in occupancy["mismatch_ids"] if i not in db_null_ids and i not in pq_null_ids]
    controller = "NOT_EXERCISED" if not db_null_ids and not pq_null_ids else ("CONFIRMED" if db_null_ids == pq_null_ids and not non_null_mismatch else "REJECTED")
    if not eligible:
        controller = "UNVERIFIED"
    out["max_occupancy"] = {"controller_false_positive": controller, "db_null": len(db_null_ids), "parquet_na": len(pq_null_ids), "null_id_sets_equal": db_null_ids == pq_null_ids,
                            "non_null_value_mismatches": len(non_null_mismatch), "crosstab": occupancy["crosstab"]}
    fc = out["free_cancellation"]
    out["matches_prereg_expectations"] = bool(eligible and out["prefix_ids_equal_pinned"] and fc["null_to_true"] == "SUPPORTED"
                                              and fc["db_null_to_parquet_true"] == PREFIX41_EXPECTED["free_cancellation_mismatches"] and controller == "CONFIRMED"
                                              and len(pq_null_ids) == PREFIX41_EXPECTED["max_occupancy_parquet_na"])
    return out


def q4_drift(row: Mapping[str, Any]) -> list[str]:
    """Assignment (dong bang) vs thuoc tinh cua CHINH snapshot: chi thong ke lech (ngu nghia khop), KHONG phai loi. Thieu observation => khong tinh drift (da la loi o Q1)."""
    if row.get("po_id") is None:
        return []
    out = []
    for name, a, p in (("max_occupancy", row.get("a_max_occupancy"), row.get("po_max_occupancy")), ("room_area", row.get("a_room_area"), row.get("po_room_area")),
                       ("breakfast_included", row.get("a_breakfast_included"), row.get("po_breakfast_included")), ("free_cancellation", row.get("a_free_cancellation"), row.get("po_free_cancellation"))):
        if (a is None) != (p is None) or (a is not None and a != p):
            out.append(name)
    return out


def key_set_report(seen: set, expected: set, rows_seen: int) -> dict[str, Any]:
    return {"expected": len(expected), "seen_distinct": len(seen), "rows_seen": rows_seen, "missing_in_db": len(expected - seen), "extra_in_db": len(seen - expected),
            "duplicates": rows_seen - len(seen), "ok": seen == expected and rows_seen == len(seen)}


# --------------------------------------------------------------------------- SQL (xem file 31/33 de GPT review truc tiep)
SQL_STEP0 = ("SELECT dataset_version, import_batch_id, status, last_completed_step, active_step, build_config_sha256, output_parquet_sha256_json, anomaly_registry_cutoff_at, anomaly_registry_file_sha256 "
             "FROM dataset_build_manifests WHERE dataset_version IN (%s, %s)")
SQL_COUNT_SELECTED = "SELECT COUNT(*) AS n FROM ml_samples s WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = 1"
SQL_Q1 = ("SELECT s.record_id, s.ml_reference_assignment_id, s.prediction_time, a.id AS a_id, a.approved_at, a.hotel_id AS a_hotel, a.checkin_date AS a_checkin, "
          "po.record_id AS po_id, po.observed_at, po.hotel_id AS po_hotel, po.checkin_date AS po_checkin "
          "FROM ml_samples s LEFT JOIN ml_reference_assignments a ON a.id = s.ml_reference_assignment_id AND a.dataset_version = s.dataset_version "
          "LEFT JOIN price_observations po ON po.record_id = s.record_id "
          "WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = 1 AND s.record_id > %s ORDER BY s.record_id LIMIT " + str(BATCH))
SQL_Q1_CONTROL = ("SELECT m.crawl_run_item_id, (a.id IS NULL) AS missing_assignment, (po.record_id IS NULL) AS missing_observation, "
                  "(po.is_sold_out = 0 AND po.price_per_night > 0) AS price_ok, (po.is_sold_out = 0 AND po.price_per_night > 0 AND po.observed_at < a.approved_at) AS before_approval "
                  "FROM ml_item_reference_matches m LEFT JOIN ml_reference_assignments a ON a.id = m.ml_reference_assignment_id AND a.dataset_version = m.dataset_version "
                  "LEFT JOIN price_observations po ON po.record_id = m.selected_record_id "
                  "WHERE m.dataset_version = %s AND m.match_status IN ('exact','alias') AND m.crawl_run_item_id > %s "
                  "ORDER BY m.crawl_run_item_id LIMIT " + str(BATCH))
SQL_Q2 = ("SELECT s.record_id, s.ml_reference_assignment_id AS assignment, s.vn_observation_date AS s_date, s.has_label_h{k} AS has_label, s.label_source_record_id_h{k} AS tgt_record, "
          "t.id AS t_sample_id, t.is_daily_snapshot_selected AS t_selected, t.ml_reference_assignment_id AS t_assignment, t.vn_observation_date AS t_date, "
          "tp.record_id AS tp_id, tp.hotel_id AS tp_hotel, tp.checkin_date AS tp_checkin, tp.price_per_night AS tp_price, tp.is_sold_out AS tp_sold_out, "
          "DATE(DATE_ADD(tp.observed_at, INTERVAL 7 HOUR)) AS tp_vn_date, "
          "sp.record_id AS sp_id, sp.hotel_id AS s_hotel, sp.checkin_date AS s_checkin, DATE(DATE_ADD(sp.observed_at, INTERVAL 7 HOUR)) AS sp_vn_date "
          "FROM ml_samples s LEFT JOIN ml_samples t ON t.dataset_version = s.dataset_version AND t.record_id = s.label_source_record_id_h{k} "
          "LEFT JOIN price_observations tp ON tp.record_id = t.record_id LEFT JOIN price_observations sp ON sp.record_id = s.record_id "
          "WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = 1 AND s.record_id > %s ORDER BY s.record_id LIMIT " + str(BATCH))
SQL_Q3_GROUPS = ("SELECT s.ml_reference_assignment_id AS assignment, s.vn_observation_date AS vn_date, COUNT(*) AS n, SUM(s.is_daily_snapshot_selected) AS n_selected "
                 "FROM ml_samples s WHERE s.dataset_version = %s AND (s.ml_reference_assignment_id > %s OR (s.ml_reference_assignment_id = %s AND s.vn_observation_date > %s)) "
                 "GROUP BY s.ml_reference_assignment_id, s.vn_observation_date HAVING COUNT(*) > 1 OR SUM(s.is_daily_snapshot_selected) <> 1 "
                 "ORDER BY s.ml_reference_assignment_id, s.vn_observation_date LIMIT " + str(BATCH))
SQL_Q3_ROWS = ("SELECT s.record_id, s.ml_reference_assignment_id AS assignment, s.vn_observation_date AS vn_date, s.is_daily_snapshot_selected, s.prediction_time, "
               "po.record_id AS po_id, im.ownership_status, im.source_code AS item_source_code, im.source_item_id, rm.source_code AS run_source_code, rm.source_run_id "
               "FROM ml_samples s LEFT JOIN price_observations po ON po.record_id = s.record_id "
               "LEFT JOIN etl_item_map im ON im.import_batch_id = %s AND im.warehouse_item_id = po.crawl_run_item_id "
               "LEFT JOIN etl_run_map rm ON rm.import_batch_id = %s AND rm.warehouse_run_id = po.crawl_run_id "
               "WHERE s.dataset_version = %s AND s.ml_reference_assignment_id = %s AND s.vn_observation_date = %s ORDER BY s.record_id LIMIT " + str(BATCH + 1))
SQL_Q3_ROWS_PACKED = ("SELECT s.record_id, s.ml_reference_assignment_id AS assignment, s.vn_observation_date AS vn_date, s.is_daily_snapshot_selected, s.prediction_time, "
                      "po.record_id AS po_id, im.ownership_status, im.source_code AS item_source_code, im.source_item_id, rm.source_code AS run_source_code, rm.source_run_id "
                      "FROM ml_samples s LEFT JOIN price_observations po ON po.record_id = s.record_id "
                      "LEFT JOIN etl_item_map im ON im.import_batch_id = %s AND im.warehouse_item_id = po.crawl_run_item_id "
                      "LEFT JOIN etl_run_map rm ON rm.import_batch_id = %s AND rm.warehouse_run_id = po.crawl_run_id "
                      "WHERE s.dataset_version = %s AND ({predicates}) ORDER BY s.ml_reference_assignment_id, s.vn_observation_date, s.record_id LIMIT " + str(BATCH + 1))
Q3_PREDICATE_SQL = "(s.ml_reference_assignment_id = %s AND s.vn_observation_date = %s)"
SQL_Q4 = ("SELECT s.record_id, a.id AS a_id, h.hotel_id AS h_id, h.city, a.max_occupancy AS a_max_occupancy, a.room_area AS a_room_area, a.breakfast_included AS a_breakfast_included, "
          "a.free_cancellation AS a_free_cancellation, po.record_id AS po_id, po.max_occupancy AS po_max_occupancy, po.room_area AS po_room_area, "
          "po.breakfast_included AS po_breakfast_included, po.free_cancellation AS po_free_cancellation "
          "FROM ml_samples s LEFT JOIN ml_reference_assignments a ON a.id = s.ml_reference_assignment_id AND a.dataset_version = s.dataset_version LEFT JOIN hotels h ON h.hotel_id = a.hotel_id "
          "LEFT JOIN price_observations po ON po.record_id = s.record_id "
          "WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = 1 AND s.record_id > %s ORDER BY s.record_id LIMIT " + str(BATCH))


def sql_statements() -> dict[str, str]:
    return {name: value for name, value in sorted(globals().items()) if name.startswith("SQL_") and isinstance(value, str)}


def sql_set_sha256() -> str:
    return hashlib.sha256(json.dumps({**sql_statements(), "Q3_PREDICATE_SQL": Q3_PREDICATE_SQL}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def controller_sha256() -> str:
    return hashlib.sha256(CONTROLLER_PATH.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- duyet cua GPT + Buoc 0
APPROVAL_NAME_RE = re.compile(r"^\d+[a-z]?-gpt-[A-Za-z0-9_.\-]+\.md$")
_APPROVAL_KV = re.compile(r"^[ \t>*\-]*`?(APPROVED_CONTROLLER_SHA256|APPROVED_SQL_SET_SHA256|APPROVED_MODES)`?[ \t]*:[ \t]*`?([^`\r\n]*?)`?[ \t]*\r?$", re.MULTILINE)


def check_approval(path_text: str, requested: set[str], *, approvals_dir: Path, controller_sha: str, sql_sha: str) -> tuple[bool, str, dict[str, Any]]:
    """File duyet cua GPT: nam dung trong thu muc thread, ten `<so>-gpt-*.md`, chua dung MOT dong cho moi khoa: APPROVED_CONTROLLER_SHA256 (== sha256 controller HIEN TAI), APPROVED_SQL_SET_SHA256 (== bo SQL
    hien tai), APPROVED_MODES (phai bao gom moi che do/ho truy van dang yeu cau). Gan duyet voi dung ma da review; khong xac thuc danh tinh tac gia."""
    info: dict[str, Any] = {"path": str(path_text)}
    try:
        path = Path(path_text).resolve(strict=True)
        base = Path(approvals_dir).resolve(strict=True)
    except OSError:
        return False, "file duyet khong ton tai", info
    if not path.is_file() or path.parent != base or not APPROVAL_NAME_RE.match(path.name):
        return False, f"file duyet phai la `<so>-gpt-*.md` nam truc tiep trong {base.name}", info
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False, "file duyet khong phai utf-8", info
    found: dict[str, str] = {}
    for key, value in _APPROVAL_KV.findall(text):
        if key in found:
            return False, f"{key} xuat hien nhieu lan trong file duyet", info
        found[key] = value.strip()
    missing = [k for k in ("APPROVED_CONTROLLER_SHA256", "APPROVED_SQL_SET_SHA256", "APPROVED_MODES") if k not in found]
    if missing:
        return False, f"file duyet thieu: {', '.join(missing)}", info
    if found["APPROVED_CONTROLLER_SHA256"] != controller_sha or found["APPROVED_SQL_SET_SHA256"] != sql_sha:
        return False, "file duyet khong gan voi controller/bo SQL hien tai (sha khong khop)", info
    modes = {m for m in re.split(r"[,\s]+", found["APPROVED_MODES"]) if m}
    not_allowed = sorted(requested - modes)
    if not_allowed:
        return False, f"file duyet khong cho phep: {', '.join(not_allowed)}", info
    info.update({"sha256": hashlib.sha256(raw).hexdigest(), "modes": sorted(modes)})
    return True, "ok", info


def parquet_entry(raw: Any) -> dict[str, Any] | None:
    """`output_parquet_sha256_json` that: {"samples.parquet": {"file_sha256": ..., "content_sha256": ..., "rows": ...}, ...}. Parse JSON dung schema; khong tim chuoi o cho nao khac."""
    obj = raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(obj, dict):
        return None
    entry = obj.get(SAMPLES_FILE)
    return entry if isinstance(entry, dict) else None


def step0_verdict(rows: Sequence[Mapping[str, Any]], artifacts: Mapping[str, Any]) -> dict[str, Any]:
    """Moi dataset phai co dung 1 dong manifest: status=pass, last_completed_step=validation, active_step NULL, batch dung, build SHA khop contract, `samples.parquet.file_sha256` BANG CHINH XAC hash artifact, rows khop.
    Tra ca DANH TINH doc tu DB (khong chi boolean) de bao cao."""
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["dataset_version"], []).append(r)
    out: dict[str, Any] = {"rows": len(rows), "datasets": {}, "identity": {}}
    ok = len(rows) == len(DATASETS) and set(by) == set(DATASETS)
    for name, meta in DATASETS.items():
        matches = by.get(name, [])
        checks: dict[str, bool] = {}
        if len(matches) == 1:
            r = matches[0]
            entry = parquet_entry(r.get("output_parquet_sha256_json"))
            expected_sha = artifacts["samples_sha"].get(name)
            expected_rows = artifacts["samples_rows"].get(name)
            checks = {"status_pass": r["status"] == "pass", "last_completed_step_validation": r["last_completed_step"] == "validation", "active_step_null": r["active_step"] is None,
                      "batch": r["import_batch_id"] == BATCH_ID, "build_config_sha256": r["build_config_sha256"] == meta["build_config_sha256"],
                      "parquet_file_sha256_exact": bool(entry is not None and expected_sha and entry.get("file_sha256") == expected_sha),
                      "parquet_rows": bool(entry is not None and expected_rows is not None and entry.get("rows") == expected_rows)}
            out["identity"][name] = {"import_batch_id": r["import_batch_id"], "status": r["status"], "last_completed_step": r["last_completed_step"], "active_step": r["active_step"],
                                     "build_config_sha256": r["build_config_sha256"], "samples_file_sha256_in_db": None if entry is None else entry.get("file_sha256"),
                                     "samples_rows_in_db": None if entry is None else entry.get("rows"), "samples_file_sha256_artifact": expected_sha, "samples_rows_artifact": expected_rows,
                                     "anomaly_registry_cutoff_at": str(r.get("anomaly_registry_cutoff_at")), "anomaly_registry_file_sha256": r.get("anomaly_registry_file_sha256")}
        out["datasets"][name] = checks
        ok = ok and bool(checks) and all(checks.values())
    out["all_ok"] = bool(ok)
    return out


# --------------------------------------------------------------------------- ha tang thuc thi (DB)
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


FRAME_COLUMNS = ["warehouse_record_id", "city", "max_occupancy", "room_area_m2", "breakfast_included", "free_cancellation",
                 *[f"has_label_h{k}" for k in (1, 3, 7, 14)], *[f"y_price_h{k}" for k in (1, 3, 7, 14)]]


def load_artifacts(need_frames: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"samples_sha": {}, "samples_rows": {}, "contract_build_sha": {}, "frames": {}}
    for name in DATASETS:
        base = ROOT / "outputs" / "datasets" / name
        path = base / SAMPLES_FILE
        out["samples_sha"][name] = sha256_file(path)
        import pyarrow.parquet as pq  # noqa: PLC0415
        out["samples_rows"][name] = int(pq.ParquetFile(path).metadata.num_rows)
        out["contract_build_sha"][name] = json.loads((base / "dataset_contract.json").read_text(encoding="utf-8"))["build_config_sha256"]
        if out["contract_build_sha"][name] != DATASETS[name]["build_config_sha256"]:
            raise RuntimeError(f"build_config_sha256 trong contract artifact != hang so da doi chieu ({name})")
        if need_frames:
            import pandas as pd  # noqa: PLC0415
            frame = pd.read_parquet(path, columns=FRAME_COLUMNS)
            if frame["warehouse_record_id"].duplicated().any():
                raise RuntimeError(f"Parquet {name} co warehouse_record_id trung: khong the join one-to-one")
            out["frames"][name] = frame
    return out


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
    try:
        plan = fetch(conn, "EXPLAIN " + sql, params)                      # KHONG ANALYZE: khong thuc thi query
    except Exception as exc:  # noqa: BLE001 - SQL loi cu phap/plan: tu choi, khong chay
        return {"ok": False, "problems": [f"EXPLAIN loi: {type(exc).__name__}: {exc}"], "plan": []}
    ok, problems = explain_ok(plan, driving=driving, allow_index_scan=allow_index_scan)
    return {"ok": ok, "problems": problems, "plan": [{k: (str(v) if v is not None else None) for k, v in row.items()} for row in plan]}


def write_unique_json(stem: str, payload: Any, run_id: str) -> tuple[Path, str]:
    """Ghi JSON vao thu muc audit voi ten DUY NHAT (thoi gian + run_id) va tu choi ghi de ('xb'); tra (duong dan, sha256 cua byte da ghi)."""
    AUDIT_OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    path = AUDIT_OUT_DIR / f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}_{run_id}.json"
    with open(path, "xb") as handle:
        handle.write(data)
    return path, hashlib.sha256(data).hexdigest()


class Ctx:
    """Mot lan chay: ngan sach thoi gian / so lenh SQL CHUNG (EXPLAIN + query + metadata Buoc 0), cache plan theo (dataset, nhan, sha SQL), che do explain-only, cong gio+crawler kiem lai o moi query."""

    def __init__(self, conn, *, explain_only: bool, budget_s: float, max_batches: int, max_queries: int, gate: EvidenceGate, q3_mode: str = "point", pilot_groups: int | None = None,
                 pilot: bool = False, run_id: str = "norun", metadata_statements: int = 0, started: float | None = None):
        self.conn, self.explain_only, self.budget_s, self.max_batches, self.max_queries = conn, explain_only, budget_s, max_batches, max_queries
        self.gate, self.q3_mode, self.pilot_groups, self.pilot = gate, q3_mode, pilot_groups, pilot
        self.run_id, self.metadata_statements = run_id, metadata_statements
        self.started = _monotonic() if started is None else started          # dong ho CHUNG: bat dau truoc connect/Buoc 0, khong reset theo dataset/strategy
        self.plans: dict[str, dict[str, Any]] = {}
        self.manifests: list[dict[str, Any]] = []
        self.queries_run = 0
        self.explains_run = 0
        self.stopped: str | None = None

    def elapsed(self) -> float:
        return _monotonic() - self.started

    def statements(self) -> int:
        return self.queries_run + self.explains_run + self.metadata_statements

    def before_statement(self) -> None:
        """Goi NGAY TRUOC tung lenh SQL (Buoc 0, EXPLAIN, SELECT du lieu): da dung lai? ngan sach thoi gian (con du margin 1 query)? tran lenh? cong crawler + gio (doc lai tep)? Sai => khong khoi dong lenh."""
        if self.stopped:
            raise Incomplete(self.stopped)
        if self.elapsed() + MAX_EXECUTION_MS / 1000 > self.budget_s:
            raise Incomplete("het ngan sach thoi gian (khong du margin cho mot query)")
        if self.statements() >= self.max_queries:
            raise Incomplete("het gioi han so query")
        try:
            self.gate.check()
        except GateLost as exc:
            self.stopped = str(exc)
            raise


def pin_manifest(ctx: Ctx, kind: str, dataset: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Ghim manifest pilot (tep rieng, ten duy nhat, khong ghi de) TRUOC khi tinh verdict tuong ung."""
    full = {**payload, "kind": kind, "dataset": dataset, "run_id": ctx.run_id, "controller_sha256": controller_sha256(), "sql_set_sha256": sql_set_sha256()}
    path, sha = write_unique_json(f"pilot_manifest_{kind}_{dataset}", full, ctx.run_id)
    entry = {"kind": kind, "dataset": dataset, "path": str(path), "sha256": sha}
    ctx.manifests.append(entry)
    return entry


def plan_key(dataset: str, label: str, sql: str) -> str:
    return f"{dataset}|{label}|{hashlib.sha256(sql.encode('utf-8')).hexdigest()[:12]}"


def guarded(ctx: Ctx, dataset: str, label: str, sql: str, params: Sequence[Any], *, driving: str, allow_index_scan: Sequence[str] = (), cap: int = BATCH,
            representative: bool = True) -> list[dict[str, Any]] | None:
    """MOI truy van (ke ca COUNT, Q1 control, Q3 rows) di qua day. `ctx.before_statement()` (dung lai / ngan sach+margin / tran lenh / cong crawler+gio) chay NGAY TRUOC EXPLAIN va LAI ngay truoc SELECT du lieu,
    nen EXPLAIN mat nhieu giay hoac vua thu hoi bang chung thi khong co SELECT nao khoi dong tiep. EXPLAIN cache theo (dataset, nhan, sha SQL); plan xau => tu choi; explain-only dung o day; khong retry; gioi han so dong."""
    key = plan_key(dataset, label, sql)
    plan = ctx.plans.get(key)
    if plan is None:
        ctx.before_statement()
        ctx.explains_run += 1
        plan = explain(ctx.conn, sql, params, driving, allow_index_scan)
        plan.update({"dataset": dataset, "label": label, "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(), "params": [str(x) for x in params], "representative": representative})
        ctx.plans[key] = plan
    if not plan["ok"]:
        raise PlanRefused(f"{dataset}/{label}", plan["problems"])
    if ctx.explain_only:
        return None
    ctx.before_statement()
    ctx.queries_run += 1
    try:
        rows = fetch(ctx.conn, sql, params)
    except Exception as exc:  # noqa: BLE001 - timeout/loi: dung, ghi nhan UNVERIFIED, KHONG retry
        raise Incomplete(f"{dataset}/{label}: {type(exc).__name__}: {exc}") from exc
    if len(rows) > cap:
        raise Incomplete(f"{dataset}/{label}: tra {len(rows)} dong > gioi han {cap}")
    return rows


def iter_keyset(ctx: Ctx, dataset: str, label: str, sql: str, fixed: Sequence[Any], cursor0: Sequence[Any], on_rows: Callable[[list], None], next_cursor: Callable[[list], Sequence[Any]],
                *, driving: str, allow_index_scan: Sequence[str] = (), stop_after_rows: int | None = None) -> dict[str, Any]:
    """Phan trang keyset <= BATCH dong/batch; `complete` chi dung khi het du lieu that (batch rong hoac ngan), khong phai khi dung vi budget/max-batches."""
    cursor, batches, total = list(cursor0), 0, 0
    while True:
        if batches >= ctx.max_batches:
            return {"complete": False, "stopped": "max_batches", "batches": batches, "rows": total}
        rows = guarded(ctx, dataset, label, sql, [*fixed, *cursor], driving=driving, allow_index_scan=allow_index_scan)
        if rows is None:
            return {"complete": None, "explain_only": True, "batches": 0, "rows": 0}
        if not rows:
            return {"complete": True, "batches": batches, "rows": total}
        on_rows(rows)
        batches += 1
        total += len(rows)
        cursor = list(next_cursor(rows))
        if stop_after_rows is not None and total >= stop_after_rows:
            return {"complete": False, "stopped": "pilot_rows", "batches": batches, "rows": total}
        if len(rows) < BATCH:
            return {"complete": True, "batches": batches, "rows": total}
        _sleep(PAUSE_S)


def scoped_coverage(seen: set, sorted_ids: Sequence[int], rows_seen: int, complete: bool) -> dict[str, Any]:
    """Phu key: day du => so voi TOAN BO key Parquet; chua day du (pilot/prefix) => so voi dung PREFIX cung do dai cua Parquet (key nho nhat theo record_id), khong bao gio so voi 128.583… cua toan cohort."""
    expected = set(sorted_ids) if complete else set(sorted_ids[:len(seen)])
    report = key_set_report(seen, expected, rows_seen)
    report["scope"] = "full" if complete else "prefix"
    return report


def _count(counter: dict, items) -> None:
    for item in items:
        counter[item] = counter.get(item, 0) + 1


def _violation_total(counts: Mapping[str, int]) -> int:
    return sum(v for key, v in counts.items() if key != "ok")


def _status(violations: int, complete: bool | None, extra_fail: Sequence[str] = ()) -> str:
    if violations or extra_fail:
        return "FAIL"
    return "PASS" if complete else "UNVERIFIED"


# --------------------------------------------------------------------------- ho truy van
def family_count(ctx: Ctx, name: str, frame) -> dict[str, Any]:
    rows = guarded(ctx, name, "count", SQL_COUNT_SELECTED, [name], driving="s")
    if rows is None:
        return {"status": "PLAN_OK"}
    n = int(rows[0]["n"])
    checks = {"db_equals_parquet": n == len(frame), "equals_expected_selected": n == EXPECTED_SELECTED}
    return {"status": "PASS" if all(checks.values()) else "FAIL", "selected_in_db": n, "selected_in_parquet": int(len(frame)), "expected": EXPECTED_SELECTED, "checks": checks}


def family_q1(ctx: Ctx, name: str, frame) -> dict[str, Any]:
    counts: dict[str, int] = {}
    seen: set[int] = set()
    state = {"rows_seen": 0}

    def on_rows(rows):
        state["rows_seen"] += len(rows)
        for row in rows:
            seen.add(int(row["record_id"]))
            _count(counts, q1_violations(row) or ["ok"])
    info = iter_keyset(ctx, name, "q1", SQL_Q1, [name], [0], on_rows, lambda rows: [rows[-1]["record_id"]], driving="s")
    if info.get("explain_only"):
        return {"status": "PLAN_OK"}
    coverage = scoped_coverage(seen, sorted(int(x) for x in frame["warehouse_record_id"]), state["rows_seen"], bool(info["complete"]))
    fail = [] if coverage["ok"] else ["key_set_mismatch"]
    return {"status": _status(_violation_total(counts), info["complete"], fail), "violation_counts": counts, "coverage": coverage, "iteration": info}


def family_q1_control(ctx: Ctx, name: str, _frame) -> dict[str, Any]:
    totals = {"matches": 0, "missing_assignment": 0, "missing_observation": 0, "price_ok": 0, "before_approval": 0}

    def on_rows(rows):
        for row in rows:
            totals["matches"] += 1
            totals["missing_assignment"] += int(bool(row["missing_assignment"]))
            totals["missing_observation"] += int(bool(row["missing_observation"]))
            totals["price_ok"] += int(bool(row["price_ok"]))
            totals["before_approval"] += int(bool(row["before_approval"]))
    info = iter_keyset(ctx, name, "q1_control", SQL_Q1_CONTROL, [name], [0], on_rows, lambda rows: [rows[-1]["crawl_run_item_id"]], driving="m")
    if info.get("explain_only"):
        return {"status": "PLAN_OK"}
    exercised = totals["before_approval"] > 0
    checks = {"no_price_loss": totals["price_ok"] == totals["matches"], "no_orphans": totals["missing_assignment"] == 0 and totals["missing_observation"] == 0}
    if info["complete"]:                                   # hang so toan cohort chi ap dung khi chay day du; prefix pilot khong bi doi 192.590 / 43.662
        checks.update({"matches_equal_expected": totals["matches"] == EXPECTED_MATCHED, "burn_in_equals_expected": totals["before_approval"] == EXPECTED_BURN_IN, "control_is_positive": exercised})
    fail = [key for key, value in checks.items() if not value]
    return {"status": _status(0, info["complete"], fail), "totals": totals, "expected": {"matches": EXPECTED_MATCHED, "before_approval": EXPECTED_BURN_IN}, "checks": checks, "iteration": info,
            "control_status": "EXERCISED" if exercised else "CONTROL_NOT_EXERCISED",
            "note": "quy uoc dong ho nhan qua (observed >= approved_at), khong phai persisted-availability; prefix khong duoc coi zero la bang chung predicate tot"}


def family_q2(ctx: Ctx, name: str, frame, k: int) -> dict[str, Any]:
    sql = SQL_Q2.format(k=k)
    label = f"q2_h{k}"
    if ctx.explain_only:
        guarded(ctx, name, label, sql, [name, 0], driving="s")
        return {"status": "PLAN_OK"}
    position = {int(r): i for i, r in enumerate(frame["warehouse_record_id"])}
    has_label = frame[f"has_label_h{k}"].tolist()
    y_price = frame[f"y_price_h{k}"].tolist()
    counts: dict[str, int] = {}
    seen: set[int] = set()
    state = {"rows_seen": 0, "labelled": 0, "price_bad": 0, "flag_bad": 0, "max_abs_price_diff": 0.0, "control_sample": 0, "control_clock": 0}

    def on_rows(rows):
        state["rows_seen"] += len(rows)
        control = q2_sensitivity_count(rows, k)
        state["control_sample"] += control["sample_date"]
        state["control_clock"] += control["observation_clock"]
        for row in rows:
            record = int(row["record_id"])
            seen.add(record)
            _count(counts, q2_violations(row, k) or ["ok"])
            i = position.get(record)
            if i is None:
                _count(counts, ["source_missing_in_parquet"])
                continue
            if bool(has_label[i]) != bool(row["has_label"]):
                state["flag_bad"] += 1
            if row["has_label"]:
                state["labelled"] += 1
                if row.get("tp_price") is None:
                    state["price_bad"] += 1
                else:
                    diff = abs(float(y_price[i]) - float(row["tp_price"]))
                    state["max_abs_price_diff"] = max(state["max_abs_price_diff"], diff)
                    if diff > 1e-6 or diff != diff:
                        state["price_bad"] += 1
    info = iter_keyset(ctx, name, label, sql, [name], [0], on_rows, lambda rows: [rows[-1]["record_id"]], driving="s")
    if info.get("explain_only"):
        return {"status": "PLAN_OK"}
    ordered_ids = sorted(position)
    coverage = scoped_coverage(seen, ordered_ids, state["rows_seen"], bool(info["complete"]))
    scope_ids = set(ordered_ids) if info["complete"] else set(ordered_ids[:len(seen)])
    parquet_labelled = int(sum(1 for rid in scope_ids if bool(has_label[position[rid]])))        # nhan trong DUNG pham vi (full hoac prefix), lay tu Parquet theo key
    checks = {"labelled_equals_parquet": state["labelled"] == parquet_labelled,
              "price_matches_parquet": state["price_bad"] == 0, "flag_matches_parquet": state["flag_bad"] == 0,
              "control_sample_date_sensitive": state["control_sample"] == state["labelled"], "control_observation_clock_sensitive": state["control_clock"] == state["labelled"]}
    if info["complete"]:
        checks["labelled_equals_expected"] = state["labelled"] == EXPECTED_LABELLED[k]
    fail = [key for key, value in checks.items() if not value]
    if not coverage["ok"]:
        fail.append("key_set_mismatch")
    return {"status": _status(_violation_total(counts), info["complete"], fail), "violation_counts": counts, "coverage": coverage, "labelled_rows": state["labelled"], "expected_labelled": EXPECTED_LABELLED[k],
            "parquet_labelled": parquet_labelled, "price_mismatch": state["price_bad"], "max_abs_price_diff": state["max_abs_price_diff"], "has_label_mismatch": state["flag_bad"],
            "control_shifted_k_plus_1": {"sample_date": state["control_sample"], "observation_clock": state["control_clock"]}, "checks": checks, "iteration": info}


def pack_groups(groups: Sequence[Mapping[str, Any]], cap: int | None = None, max_groups: int | None = None) -> tuple[list[list[Mapping[str, Any]]], list[Mapping[str, Any]]]:
    """Gom nhom lien tiep sao cho TONG n <= cap (va so nhom <= max_groups); nhom co n > cap la `oversized` (khong cat dong de PASS)."""
    cap = BATCH if cap is None else cap
    max_groups = MAX_PACKED_GROUPS if max_groups is None else max_groups
    batches: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    total = 0
    oversized: list[Mapping[str, Any]] = []
    for group in groups:
        n = int(group["n"])
        if n > cap:
            oversized.append(group)
            continue
        if current and (total + n > cap or len(current) >= max_groups):
            batches.append(current)
            current, total = [], 0
        current.append(group)
        total += n
    if current:
        batches.append(current)
    return batches, oversized


def packed_sql(count: int) -> str:
    return SQL_Q3_ROWS_PACKED.format(predicates=" OR ".join([Q3_PREDICATE_SQL] * count))


def _row_signature(row: Mapping[str, Any]) -> tuple:
    return (int(row["record_id"]), int(bool(row["is_daily_snapshot_selected"])), str(row["prediction_time"]), row["po_id"], row["ownership_status"], row["item_source_code"],
            row["source_item_id"], row["run_source_code"], row["source_run_id"])


def _verdict_signature(verdict: Mapping[str, Any]) -> tuple:
    return (verdict["n"], verdict["n_selected"], verdict["exactly_one_selected"], verdict["selected_is_min_key"], verdict["strict_order"], verdict["reverse_order_would_pick_other"],
            tuple(verdict["lineage_problems"]))


def _fetch_unit(ctx: Ctx, dataset: str, unit: Sequence[Mapping[str, Any]], packed: bool) -> dict[Any, list]:
    if packed:
        flat: list[Any] = []
        for group in unit:
            flat += [group["assignment"], group["vn_date"]]
        rows = guarded(ctx, dataset, f"q3_rows_packed:{len(unit)}", packed_sql(len(unit)), [BATCH_ID, BATCH_ID, dataset, *flat], driving="s", cap=BATCH + 1)
    else:
        rows = guarded(ctx, dataset, "q3_rows", SQL_Q3_ROWS, [BATCH_ID, BATCH_ID, dataset, unit[0]["assignment"], unit[0]["vn_date"]], driving="s", cap=BATCH + 1)
    by_group: dict[Any, list] = {}
    for row in rows:
        by_group.setdefault((row["assignment"], row["vn_date"]), []).append(row)
    return by_group


def family_q3(ctx: Ctx, name: str, frame) -> dict[str, Any]:
    mode = ctx.q3_mode
    pilot = ctx.pilot_groups is not None
    groups: list[dict[str, Any]] = []
    info = iter_keyset(ctx, name, "q3_groups", SQL_Q3_GROUPS, [name], [0, 0, "1970-01-01"], groups.extend,
                       lambda rows: [rows[-1]["assignment"], rows[-1]["assignment"], rows[-1]["vn_date"]], driving="s", stop_after_rows=ctx.pilot_groups if pilot else None)
    if info.get("explain_only"):
        if mode in ("point", "compare"):
            guarded(ctx, name, "q3_rows", SQL_Q3_ROWS, [BATCH_ID, BATCH_ID, name, 0, "1970-01-01"], driving="s", cap=BATCH + 1, representative=False)
        if mode in ("packed", "compare"):
            guarded(ctx, name, "q3_rows_packed:2", packed_sql(2), [BATCH_ID, BATCH_ID, name, 0, "1970-01-01", 0, "1970-01-02"], driving="s", cap=BATCH + 1, representative=False)
        return {"status": "PLAN_OK"}
    parquet_ids = set(int(x) for x in frame["warehouse_record_id"])
    groups_seen = len(groups)
    pilot_limited = bool(pilot and (info.get("stopped") == "pilot_rows" or groups_seen > ctx.pilot_groups))
    if pilot:
        groups = groups[:ctx.pilot_groups]
    state: dict[str, Any] = {"mode": mode, "groups_total": None if pilot_limited else groups_seen, "groups_seen": groups_seen, "pilot_limited": pilot_limited, "selected_missing_in_parquet": 0,
                             "superseded_present_in_parquet": 0, "groups_expected": len(groups), "groups_evaluated": 0, "size_mismatch": 0, "unexpected_group_rows": 0, "oversized_groups": 0,
                             "exactly_one_violations": 0, "not_min_key": 0, "lineage_groups": 0, "lineage_problems": {}, "strict_groups": 0, "control_reverse_picks_other": 0, "candidate_rows": 0,
                             "selected_rows": 0, "superseded_rows": 0, "iteration_groups": info}
    if not info["complete"] and info.get("stopped") != "pilot_rows":
        return {"status": "UNVERIFIED", "reason": "danh sach nhom chua day du", "state": state}
    packed_units, oversized = pack_groups(groups)
    use_packed = mode in ("packed", "compare")
    state["oversized_groups"] = len(oversized) if use_packed else 0
    if ctx.pilot:
        pin_manifest(ctx, "q3_groups", name, {"order_rule": "(ml_reference_assignment_id, vn_observation_date) ASC theo query keyset", "mode": mode, "groups_seen": groups_seen, "pilot_groups": ctx.pilot_groups,
                                              "groups": [{"assignment": g["assignment"], "vn_date": str(g["vn_date"]), "n": int(g["n"])} for g in groups], "sum_n": sum(int(g["n"]) for g in groups),
                                              "packed_batches": [[[g["assignment"], str(g["vn_date"])] for g in unit] for unit in packed_units], "oversized": [[g["assignment"], str(g["vn_date"]), int(g["n"])] for g in oversized]})
    primary_packed = mode == "packed"
    units = packed_units if primary_packed else [[g] for g in groups]
    digest: list[Any] = []
    baseline: dict[Any, tuple[dict[int, tuple], tuple]] = {}
    for unit in units:
        by_group = _fetch_unit(ctx, name, unit, primary_packed)
        wanted = {(g["assignment"], g["vn_date"]) for g in unit}
        state["unexpected_group_rows"] += sum(len(v) for key, v in by_group.items() if key not in wanted)
        for group in unit:
            key = (group["assignment"], group["vn_date"])
            group_rows = by_group.get(key, [])
            state["candidate_rows"] += int(group["n"])
            state["selected_rows"] += int(group["n_selected"] or 0)
            if len(group_rows) != int(group["n"]):
                state["size_mismatch"] += 1
            for row in group_rows:
                in_parquet = int(row["record_id"]) in parquet_ids
                if row["is_daily_snapshot_selected"] and not in_parquet:
                    state["selected_missing_in_parquet"] += 1
                if not row["is_daily_snapshot_selected"] and in_parquet:
                    state["superseded_present_in_parquet"] += 1
            verdict = q3_group_verdict(group_rows)
            baseline[key] = ({sig[0]: sig for sig in map(_row_signature, group_rows)}, _verdict_signature(verdict))
            state["groups_evaluated"] += 1
            if verdict["lineage_problems"]:
                state["lineage_groups"] += 1
                _count(state["lineage_problems"], verdict["lineage_problems"])
            if not verdict["exactly_one_selected"]:
                state["exactly_one_violations"] += 1
            if verdict["selected_is_min_key"] is False:
                state["not_min_key"] += 1
            if verdict["strict_order"]:
                state["strict_groups"] += 1
            state["control_reverse_picks_other"] += int(verdict["reverse_order_would_pick_other"])
            digest.append([int(group["assignment"]), str(group["vn_date"]), int(group["n"]), verdict["n_selected"], verdict["exactly_one_selected"], verdict["selected_is_min_key"], verdict["lineage_problems"]])
        _sleep(PAUSE_S / 2)
    equivalence: dict[str, Any] | None = None
    if mode == "compare":
        equivalence = {"groups_compared": 0, "packed_oversized_groups": len(oversized), "missing_rows": 0, "extra_rows": 0, "value_mismatch": 0, "verdict_mismatch": 0, "unexpected_rows": 0,
                       "groups_with_mismatch": 0, "point_statements": ctx.queries_run, "baseline": "point"}
        for unit in packed_units:
            by_group = _fetch_unit(ctx, name, unit, True)
            wanted = {(g["assignment"], g["vn_date"]) for g in unit}
            equivalence["unexpected_rows"] += sum(len(v) for key, v in by_group.items() if key not in wanted)
            for group in unit:
                key = (group["assignment"], group["vn_date"])
                packed_rows = by_group.get(key, [])
                point_sigs, point_verdict = baseline[key]
                packed_sigs = {sig[0]: sig for sig in map(_row_signature, packed_rows)}
                missing, extra = set(point_sigs) - set(packed_sigs), set(packed_sigs) - set(point_sigs)
                value_bad = sum(1 for rid in set(point_sigs) & set(packed_sigs) if point_sigs[rid] != packed_sigs[rid])
                verdict_bad = int(_verdict_signature(q3_group_verdict(packed_rows)) != point_verdict)
                equivalence["groups_compared"] += 1
                equivalence["missing_rows"] += len(missing)
                equivalence["extra_rows"] += len(extra)
                equivalence["value_mismatch"] += value_bad
                equivalence["verdict_mismatch"] += verdict_bad
                equivalence["groups_with_mismatch"] += int(bool(missing or extra or value_bad or verdict_bad))
            _sleep(PAUSE_S / 2)
        equivalence["packed_statements_total"] = ctx.queries_run
        state["equivalence"] = equivalence
    state["superseded_rows"] = state["candidate_rows"] - state["selected_rows"]
    state["verdict_digest"] = hashlib.sha256(json.dumps(sorted(digest), sort_keys=True).encode("utf-8")).hexdigest()
    complete = not pilot_limited and not (use_packed and oversized)
    checks = {"groups_evaluated_equals_groups": state["groups_evaluated"] + (len(oversized) if primary_packed else 0) == state["groups_expected"], "group_sizes_match": state["size_mismatch"] == 0,
              "no_rows_outside_requested_groups": state["unexpected_group_rows"] == 0, "exactly_one_selected": state["exactly_one_violations"] == 0, "selected_is_min_key": state["not_min_key"] == 0,
              "lineage_complete": state["lineage_groups"] == 0, "selected_rows_are_in_parquet": state["selected_missing_in_parquet"] == 0,
              "superseded_rows_not_in_parquet": state["superseded_present_in_parquet"] == 0, "control_reverse_order_sensitive": state["control_reverse_picks_other"] == state["strict_groups"]}
    if equivalence is not None:
        checks["point_equals_packed"] = sum(equivalence[k] for k in ("missing_rows", "extra_rows", "value_mismatch", "verdict_mismatch", "unexpected_rows")) == 0
    if complete:
        checks["superseded_rows_equal_expected"] = state["superseded_rows"] == EXPECTED_SUPERSEDED
    fail = [key for key, value in checks.items() if not value]
    result = {"status": _status(0, complete, fail), "state": state, "expected_superseded_rows": EXPECTED_SUPERSEDED, "checks": checks}
    if not complete:
        result["reason"] = "pilot gioi han so nhom" if pilot_limited else "co nhom oversized (n > tran dong): khong cat de PASS"
    return result


def _area_parser() -> Callable[[Any], Any]:
    """Oracle parse dien tich = ham cua builder; import o day de modul thuan (import controller/test) KHONG keo `dataset_builder.env` / .env; test thay bang ham thuan."""
    sys.path.insert(0, str(REPO / "ml"))
    from dataset_builder.features import parse_room_area_m2  # noqa: PLC0415
    return parse_room_area_m2


def family_q4(ctx: Ctx, name: str, frame) -> dict[str, Any]:
    if ctx.explain_only:
        guarded(ctx, name, "q4", SQL_Q4, [name, 0], driving="s")
        return {"status": "PLAN_OK"}
    parse_room_area_m2 = _area_parser()                                  # oracle = ham parse cua builder: ghi ro trong bao cao
    position = {int(r): i for i, r in enumerate(frame["warehouse_record_id"])}
    columns = {c: frame[c].tolist() for c in ("city", "max_occupancy", "room_area_m2", "breakfast_included", "free_cancellation")}
    diagnosis = Q4Diagnosis()
    counts: dict[str, int] = {}
    drift: dict[str, int] = {}
    seen: set[int] = set()
    state = {"rows_seen": 0}

    def on_rows(rows):
        state["rows_seen"] += len(rows)
        for row in rows:
            record = int(row["record_id"])
            seen.add(record)
            i = position.get(record)
            if i is None:
                _count(counts, ["source_missing_in_parquet"])
                diagnosis.skip(record, "source_missing_in_parquet")
                continue
            parquet_row = {c: columns[c][i] for c in columns}
            _count(counts, q4_fidelity_violations(row, parquet_row, parse_room_area_m2) or ["ok"])
            _count(drift, q4_drift(row) or ["no_drift"])
            if row.get("a_id") is not None and row.get("h_id") is not None:
                diagnosis.add(record, row, parquet_row)
            else:
                diagnosis.skip(record, "orphan_assignment_or_hotel")
    info = iter_keyset(ctx, name, "q4", SQL_Q4, [name], [0], on_rows, lambda rows: [rows[-1]["record_id"]], driving="s")
    if info.get("explain_only"):
        return {"status": "PLAN_OK"}
    ordered = sorted(position)
    coverage = scoped_coverage(seen, ordered, state["rows_seen"], bool(info["complete"]))
    fail = [] if coverage["ok"] else ["key_set_mismatch"]
    prefix_ids = ordered if info["complete"] else ordered[:len(seen)]
    prefix_sha = hashlib.sha256(json.dumps(prefix_ids).encode("utf-8")).hexdigest()
    hypotheses = evaluate_null_hypotheses(diagnosis, prefix_sha, set(prefix_ids))
    path, sha = write_unique_json(f"q4_diagnosis_{name}", {"dataset": name, "run_id": ctx.run_id, "rows_diagnosed": diagnosis.rows, "prefix_ids_sha256": prefix_sha, "hypotheses": hypotheses,
                                                           "columns": diagnosis.cols, "domain_errors": diagnosis.domain_errors, "skipped": diagnosis.skipped}, ctx.run_id)
    return {"status": _status(_violation_total(counts), info["complete"], fail), "fidelity_violation_counts": counts, "assignment_vs_observation_drift_counts": drift, "coverage": coverage,
            "diagnosis": {"path": str(path), "sha256": sha, "hypotheses": hypotheses}, "oracle_for_room_area": "dataset_builder.features.parse_room_area_m2",
            "city_note": "city hien tai cua bang hotels KHONG chung minh vintage as-of", "iteration": info}


def run_families(ctx: Ctx, requested: Sequence[str], artifacts: Mapping[str, Any]) -> dict[str, Any]:
    """Moi (dataset, ho) tra status rieng. PlanRefused/Incomplete => UNVERIFIED cua ho do va chay tiep ho khac; COUNT (mau so) that bai/chua xac minh => dung toan bo run."""
    out: dict[str, Any] = {}
    plan_only = ctx.explain_only
    for name in DATASETS:
        frame = artifacts["frames"].get(name) if artifacts.get("frames") else None
        entry: dict[str, Any] = out.setdefault(name, {})
        if ctx.pilot and not plan_only and frame is not None:
            ordered = sorted(int(x) for x in frame["warehouse_record_id"])
            prefix = ordered[:ctx.max_batches * BATCH]
            pin_manifest(ctx, "prefix", name, {"build_config_sha256": DATASETS[name]["build_config_sha256"], "samples_sha256": artifacts["samples_sha"][name], "order_rule": "record_id ASC trong tap selected",
                                               "batches": ctx.max_batches, "batch_rows": BATCH, "expected_prefix_ids": prefix, "expected_prefix_ids_sha256": hashlib.sha256(json.dumps(prefix).encode("utf-8")).hexdigest(),
                                               "q3_mode": ctx.q3_mode, "pilot_groups": ctx.pilot_groups})
        for family in [f for f in FAMILIES if f in requested]:
            runs: list[tuple[str, Callable[[], dict[str, Any]]]]
            if family == "q2":
                runs = [(f"q2_h{k}", (lambda kk=k: family_q2(ctx, name, frame, kk))) for k in (1, 3, 7, 14)]
            else:
                fn = {"count": family_count, "q1": family_q1, "q1_control": family_q1_control, "q3": family_q3, "q4": family_q4}[family]
                runs = [(family, (lambda f=fn: f(ctx, name, frame)))]
            for label, fn_call in runs:
                try:
                    entry[label] = fn_call()
                except PlanRefused as exc:
                    entry[label] = {"status": "PLAN_REFUSED", "label": exc.label, "problems": exc.problems}
                except Incomplete as exc:
                    entry[label] = {"status": "UNVERIFIED", "reason": str(exc)}
                if family == "count" and entry[label]["status"] in ("FAIL", "UNVERIFIED", "PLAN_REFUSED") and not plan_only:
                    return out
    return out


KNOWN_STATUS = ("PASS", "FAIL", "UNVERIFIED", "PLAN_REFUSED", "PLAN_OK")


def labels_for(requested: Sequence[str]) -> list[str]:
    out: list[str] = []
    for family in FAMILIES:
        if family in requested:
            out += [f"q2_h{k}" for k in (1, 3, 7, 14)] if family == "q2" else [family]
    return out


def summarize(report: Mapping[str, Any]) -> dict[str, Any]:
    """Verdict may doc duoc + ma thoat. Chi PASS_FULL khi: da yeu cau DU ca 6 ho, dung tap dataset x 9 nhan (khong thieu/thua/la), va MOI status == "PASS". Status la/thieu/thua => UNVERIFIED."""
    refusal = report.get("refusals") or []
    if refusal:
        return {"overall": "REFUSED", "execution": "refused", "reasons": list(refusal), "exit_code": EXIT_CODES["REFUSED"]}
    step0 = report.get("step0")
    if not step0 or not step0.get("all_ok"):
        return {"overall": "REFUSED", "execution": "refused", "reasons": ["Buoc 0: manifest DB khong khop artifact"], "exit_code": EXIT_CODES["REFUSED"]}
    mode = report.get("mode")
    if mode == "step0":
        return {"overall": "STEP0_OK", "execution": "complete", "reasons": [], "exit_code": EXIT_CODES["STEP0_OK"]}
    families = report.get("families") or {}
    requested = list(report.get("requested") or [])
    expected = set(labels_for(requested))
    structure: list[str] = []
    if set(families) != set(DATASETS):
        structure.append(f"tap dataset sai: {sorted(families)}")
    statuses: list[tuple[str, str, Any]] = []
    for name in DATASETS:
        per = families.get(name) or {}
        missing, extra = sorted(expected - set(per)), sorted(set(per) - expected)
        if missing:
            structure.append(f"{name}: thieu nhan {missing}")
        if extra:
            structure.append(f"{name}: nhan la {extra}")
        for label, res in per.items():
            status = res.get("status") if isinstance(res, dict) else None
            if status not in KNOWN_STATUS:
                structure.append(f"{name}/{label}: status la {status!r}")
            statuses.append((name, label, status))
    if mode == "explain":
        bad = [f"{n}/{l}: {s}" for n, l, s in statuses if s != "PLAN_OK"]
        if bad or structure:
            return {"overall": "EXPLAIN_REFUSED", "execution": "explain_only", "reasons": bad + structure, "exit_code": EXIT_CODES["EXPLAIN_REFUSED"]}
        return {"overall": "EXPLAIN_OK", "execution": "explain_only", "reasons": [], "exit_code": EXIT_CODES["EXPLAIN_OK"]}
    failing = [f"{n}/{l}" for n, l, s in statuses if s == "FAIL"]
    unverified = [f"{n}/{l}: {s}" for n, l, s in statuses if s in ("UNVERIFIED", "PLAN_REFUSED", "PLAN_OK")]
    if failing:
        return {"overall": "FAIL", "execution": "complete" if not (unverified or structure) else "incomplete", "reasons": [f"FAIL: {x}" for x in failing] + unverified, "exit_code": EXIT_CODES["FAIL"]}
    if unverified or structure:                     # status hop le con lai chi la PASS (FAIL da tra o tren; UNVERIFIED/PLAN_* o day)
        return {"overall": "UNVERIFIED", "execution": "incomplete", "reasons": unverified + structure, "exit_code": EXIT_CODES["UNVERIFIED"]}
    if report.get("pilot"):
        return {"overall": "PILOT_PARTIAL", "execution": "incomplete", "reasons": ["pilot (prefix/gioi han) khong bao gio la audit day du"], "exit_code": EXIT_CODES["PILOT_PARTIAL"]}
    if set(requested) != set(FAMILIES):
        return {"overall": "PARTIAL_PASS", "execution": "incomplete", "reasons": ["chua yeu cau du cac ho truy van (PARTIAL)"], "exit_code": EXIT_CODES["PARTIAL_PASS"]}
    return {"overall": "PASS_FULL", "execution": "complete", "reasons": [], "exit_code": EXIT_CODES["PASS_FULL"]}


# --------------------------------------------------------------------------- main
def parse_families(text: str) -> list[str] | None:
    if text == "all":
        return list(FAMILIES)
    wanted = [w for w in text.split(",") if w]
    if not wanted or not set(wanted) <= set(FAMILIES):
        return None
    return wanted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="warehouse_dsdev_20261004_3src")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--step0", action="store_true")
    mode.add_argument("--explain", default="")
    mode.add_argument("--run", default="")
    parser.add_argument("--gpt-approval", required=True, help="file `<so>-gpt-*.md` trong thread, gan voi sha256 controller + bo SQL + che do")
    parser.add_argument("--crawl-evidence", required=True, help="bang chung crawler-idle tu crawl_idle_evidence.py (con han, zero run/item active)")
    parser.add_argument("--budget-seconds", type=int, default=None, help=f"tong ngan sach thoi gian (giay); step0/explain mac dinh {DEFAULT_PREFLIGHT_BUDGET_S}; --run BAT BUOC tuong minh")
    parser.add_argument("--max-queries", type=int, default=None, help=f"tran so EXPLAIN + query; step0/explain mac dinh {DEFAULT_PREFLIGHT_MAX_QUERIES}; --run BAT BUOC tuong minh")
    parser.add_argument("--max-batches", type=int, default=DEFAULT_MAX_BATCHES)
    parser.add_argument("--q3-mode", choices=("point", "packed", "compare"), default="point", help="point = mot truy van/nhom (baseline); packed = gom nhom, tong n <= tran dong; compare = point lam chuan roi packed tren CUNG manifest, 0 mismatch")
    parser.add_argument("--pilot-groups", type=int, default=None, help="chi danh gia N nhom Q3 dau theo thu tu keyset (pilot, ket qua luon UNVERIFIED/PARTIAL)")
    return parser


def write_report(report: dict[str, Any], mode: str, run_id: str) -> Path:
    path, _ = write_unique_json(mode, report, run_id)
    return path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = "step0" if args.step0 else ("explain" if args.explain else "run")
    requested = [] if args.step0 else parse_families(args.explain or args.run)
    if requested is None:
        print(f"ho truy van khong hop le; chon trong {','.join(FAMILIES)} hoac all")
        return 2
    if mode == "run" and (args.budget_seconds is None or args.max_queries is None):
        print("--run bat buoc co --budget-seconds va --max-queries tuong minh (khong co mac dinh)")
        return 2
    budget = args.budget_seconds if args.budget_seconds is not None else DEFAULT_PREFLIGHT_BUDGET_S
    max_queries = args.max_queries if args.max_queries is not None else DEFAULT_PREFLIGHT_MAX_QUERIES
    if mode == "run" and "count" not in requested:
        requested = ["count", *requested]
    ops = {"step0"} if mode == "step0" else ({"explain"} if mode == "explain" else {f"run:{f}" for f in requested})
    now = _now_utc()
    run_id = uuid.uuid4().hex[:12]
    pilot = args.pilot_groups is not None or args.max_batches < DEFAULT_MAX_BATCHES
    report: dict[str, Any] = {"run_id": run_id, "pilot": pilot, "db": args.db, "mode": mode, "requested": requested, "created_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "controller_sha256": controller_sha256(),
                              "sql_set_sha256": sql_set_sha256(), "max_execution_ms": MAX_EXECUTION_MS, "batch": BATCH, "budget_seconds": budget, "max_queries": max_queries, "max_batches": args.max_batches,
                              "q3_mode": args.q3_mode, "pilot_groups": args.pilot_groups, "refusals": []}
    refusals: list[str] = report["refusals"]
    if not database_allowed(args.db):
        refusals.append(f"ten DB khong hop le ({args.db!r}): chi `warehouse_dsdev_*`, khong bao gio DB van hanh")
    ok, why, approval = check_approval(args.gpt_approval, ops, approvals_dir=APPROVALS_DIR, controller_sha=report["controller_sha256"], sql_sha=report["sql_set_sha256"])
    report["approval"] = approval
    if not ok:
        refusals.append(f"duyet cua GPT: {why}")
    gate = EvidenceGate(args.crawl_evidence)
    try:
        gate.check(force=True)
    except GateLost as exc:
        refusals.append(str(exc))
    artifacts: dict[str, Any] = {}
    ctx: Ctx | None = None
    if not refusals:
        try:
            artifacts = load_artifacts(need_frames=(mode == "run"))
            report["artifacts"] = {"samples_sha256": artifacts["samples_sha"], "samples_rows": artifacts["samples_rows"]}
        except Exception as exc:  # noqa: BLE001 - artifact hong/thieu cot: khong chay
            refusals.append(f"artifact: {type(exc).__name__}: {exc}")
    if not refusals:
        started = _monotonic()                                      # dong ho + ngan sach CHUNG bat dau truoc ket noi / Buoc 0
        try:
            conn = connect(args.db)
        except Exception as exc:  # noqa: BLE001
            conn = None
            refusals.append(f"ket noi DB that bai: {type(exc).__name__}: {exc}")
        if conn is not None:
            try:
                ctx = Ctx(conn, explain_only=(mode != "run"), budget_s=budget, max_batches=args.max_batches, max_queries=max_queries, gate=gate, q3_mode=args.q3_mode, pilot_groups=args.pilot_groups,
                          pilot=pilot, run_id=run_id, metadata_statements=CONNECT_SET_STATEMENTS, started=started)
                rows = None
                try:
                    ctx.before_statement()                          # cong crawler + gio + ngan sach NGAY TRUOC Buoc 0, sau load_artifacts/connect
                except Incomplete as exc:
                    refusals.append(f"truoc Buoc 0: {exc}")
                else:
                    ctx.metadata_statements += 1
                    try:
                        rows = fetch(conn, SQL_STEP0, list(DATASETS))
                    except Exception as exc:  # noqa: BLE001
                        refusals.append(f"Buoc 0 loi: {type(exc).__name__}: {exc}")
                if rows is not None:
                    report["step0"] = step0_verdict(rows, artifacts)
                    if report["step0"]["all_ok"] and mode != "step0":
                        report["families"] = run_families(ctx, requested, artifacts)
                        report["queries_executed"], report["explains_executed"], report["stopped_reason"] = ctx.queries_run, ctx.explains_run, ctx.stopped
                        report["pilot_manifests"], report["plans"] = ctx.manifests, ctx.plans
                report["statements_total"], report["metadata_statements"] = ctx.statements(), ctx.metadata_statements
            finally:
                conn.close()
            report["elapsed_s"] = round(_monotonic() - started, 1)
            report["statement_accounting"] = "metadata = 2 SET (connect) + 1 SELECT Buoc 0; EXPLAIN va SELECT du lieu tinh chung; tran --max-queries ap dung cho TONG"
    report["crawl_evidence"] = {"path": str(args.crawl_evidence), "checks": gate.checks, "sha_chain": gate.sha_chain, "receipts": gate.receipts, "last_verdict": gate.last_verdict, "last_content": gate.last_content}
    report["verdict"] = summarize(report)
    path = write_report(report, mode, run_id)
    print(json.dumps({"overall": report["verdict"]["overall"], "execution": report["verdict"]["execution"], "exit_code": report["verdict"]["exit_code"], "reasons": report["verdict"]["reasons"][:20]},
                     ensure_ascii=False, indent=1))
    print("bao cao:", path)
    return int(report["verdict"]["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
