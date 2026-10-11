"""Observer DB (CHI DOC) cho runner repair: baseline truoc phase, probe sau phase, self-test Q0. Cap SQL CO DINH (toan SELECT), moi cau co MAX_EXECUTION_TIME, dem so lenh, khong luu text SQL cua phien khac.

TRANG THAI: THIET KE + MA + TEST tren ket noi GIA. KHONG co CLI, KHONG ket noi that, KHONG co quyen thuc thi. Ket noi that (`connect_fn`) do runner/approval cung cap sau; moi cau SQL, so luong, timeout, quyen
visibility trong file nay la thu GPT phai duyet TRUOC bat ky phep do live nao. Observer KHONG BAO GIO KILL, KHONG ghi, KHONG DDL.

Giao dien cho `repair_phase_runner_v1.Runner`: `capture_baseline()`, `probe()`; va `selftest()` (Q0) tra dict day du cac khoa `quiescence_model_v1.SELFTEST_KEYS` + `details`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import types
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


qm = _load("quiescence_model_for_observer", "quiescence_model_v1.py")
MAX_EXEC_MS = 5000
HINT = f"/*+ MAX_EXECUTION_TIME({MAX_EXEC_MS}) */"
PROBE_LIMIT = qm.PROBE_ROW_LIMIT                                                                          # 50: cham tran => model coi la bi cat (UNKNOWN)
DATABASES_LIMIT = 200                                                                                     # liet ke DB de dung danh sach protected; 201 hang tro len => bi cat => Q0 tu choi

# Cac cau SQL: DUNG MOT cau SELECT moi hang, khong ';', khong INFO/text cua phien khac. Tham so (neu co) di rieng qua %s.
SQL_CONNECTION_INFO = f"SELECT {HINT} CONNECTION_ID() AS probe_session_id, SUBSTRING_INDEX(USER(), '@', 1) AS app_user"
SQL_SERVER_IDENTITY = f"SELECT {HINT} @@server_uuid AS server_uuid, @@version AS version"
SQL_UPTIME = f"SELECT {HINT} CAST(VARIABLE_VALUE AS UNSIGNED) AS uptime_s FROM performance_schema.global_status WHERE VARIABLE_NAME = 'Uptime'"
SQL_MAX_THREAD_ID = f"SELECT {HINT} MAX(PROCESSLIST_ID) AS max_id FROM performance_schema.threads WHERE TYPE = 'FOREGROUND'"
SQL_SESSIONS = (f"SELECT {HINT} t.PROCESSLIST_ID AS PROCESSLIST_ID, t.PROCESSLIST_USER AS PROCESSLIST_USER, t.PROCESSLIST_DB AS PROCESSLIST_DB, a.ATTR_VALUE AS pid "
                f"FROM performance_schema.threads t LEFT JOIN performance_schema.session_connect_attrs a ON a.PROCESSLIST_ID = t.PROCESSLIST_ID AND a.ATTR_NAME = '_pid' "
                f"WHERE t.TYPE = 'FOREGROUND' AND t.PROCESSLIST_ID > %s ORDER BY t.PROCESSLIST_ID LIMIT {PROBE_LIMIT}")
SQL_DATABASES = (f"SELECT {HINT} SCHEMA_NAME AS schema_name FROM information_schema.SCHEMATA WHERE SCHEMA_NAME NOT IN ('information_schema','mysql','performance_schema','sys') "
                 f"ORDER BY SCHEMA_NAME LIMIT {DATABASES_LIMIT + 1}")
SQL_SESSION_SANITY = f"SELECT {HINT} @@session.transaction_read_only AS ro, @@session.time_zone AS tz"
AUX_SET_STATEMENTS = ("SET SESSION TRANSACTION READ ONLY",)                                                # DUY NHAT lenh non-SELECT cua adapter ket noi (phien chi-doc); cong them ba lenh SET cua connector (xem docstring)
SQL_ROLLING_BACK = f"SELECT {HINT} trx_mysql_thread_id AS trx_mysql_thread_id, trx_state AS trx_state FROM information_schema.INNODB_TRX WHERE trx_state = 'ROLLING BACK' LIMIT {PROBE_LIMIT}"


def sql_statements() -> dict[str, str]:
    return {"SQL_CONNECTION_INFO": SQL_CONNECTION_INFO, "SQL_SERVER_IDENTITY": SQL_SERVER_IDENTITY, "SQL_UPTIME": SQL_UPTIME, "SQL_MAX_THREAD_ID": SQL_MAX_THREAD_ID, "SQL_SESSIONS": SQL_SESSIONS,
            "SQL_ROLLING_BACK": SQL_ROLLING_BACK, "SQL_DATABASES": SQL_DATABASES, "SQL_SESSION_SANITY": SQL_SESSION_SANITY}


def sql_set_sha256() -> str:
    """Hash CA cac SELECT lan lenh SET phu cua adapter (de approval gan ca hai)."""
    body = "\n".join(f"{k}={v}" for k, v in sorted(sql_statements().items())) + "\nAUX=" + "|".join(AUX_SET_STATEMENTS)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def script_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def fetch(conn: Any, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    """Mot SELECT -> list dict (cursor dictionary); luon dong cursor."""
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, tuple(params))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()


def _int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or (isinstance(value, str) and not re.fullmatch(r"[0-9]+", value)):
        raise ValueError(f"{what}: not an integer: {value!r}")
    return int(value)


def _one(rows: Sequence[Mapping[str, Any]], what: str) -> Mapping[str, Any]:
    if len(rows) != 1:
        raise ValueError(f"{what}: expected exactly 1 row, got {len(rows)}")
    return rows[0]


def normalize_session_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """PROCESSLIST_ID int; user/db str|None; `pid` (chuoi thap phan tu session_connect_attrs) -> int, NULL/khong phai so -> None (rule (a) van bat theo user/DB)."""
    pid_raw = row.get("pid")
    pid = int(pid_raw) if isinstance(pid_raw, str) and re.fullmatch(r"[0-9]{1,9}", pid_raw) else (pid_raw if isinstance(pid_raw, int) and not isinstance(pid_raw, bool) else None)
    return {"PROCESSLIST_ID": _int(row.get("PROCESSLIST_ID"), "PROCESSLIST_ID"), "PROCESSLIST_USER": row.get("PROCESSLIST_USER"), "PROCESSLIST_DB": row.get("PROCESSLIST_DB"), "pid": pid}


def readonly_connect_factory(connector_connect: Callable[..., Any], kwargs_fn: Callable[[], Mapping[str, Any]]) -> Callable[[], Any]:
    """Adapter ket noi RIENG (khong sua backend): mo ket noi bang kwargs backend (`_connect_kwargs(None)`: user/host/port, time_zone '+00:00', autocommit False, utf8mb4), roi dat phien CHI-DOC va XAC MINH.
    Lenh phat sinh MOI ket noi (connector 8.3.0 thuan Python, doc tu nguon): `SET NAMES '<cs>' COLLATE '<co>'`, `SET @@session.autocommit = OFF`, `SET @@session.time_zone = '+00:00'` (ba SET cua connector, khong
    kiem soat duoc) + `SET SESSION TRANSACTION READ ONLY` + `SELECT @@session.transaction_read_only, @@session.time_zone` (SQL_SESSION_SANITY) cua adapter. Sai (khong read-only / sai time_zone) => dong + ngoai le.
    `use_pure=True` DUOC GHIM trong ma (che do driver khong phu thuoc moi truong): kiem ke ba lenh SET o tren doc tu nguon driver thuan Python."""
    def make() -> Any:
        conn = connector_connect(**{**dict(kwargs_fn()), "use_pure": True})
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(AUX_SET_STATEMENTS[0])
            finally:
                cursor.close()
            row = _one(fetch(conn, SQL_SESSION_SANITY), "session_sanity")
            if str(row.get("ro")) not in ("1", "True") or row.get("tz") != "+00:00":
                raise RuntimeError(f"session_not_read_only_or_wrong_timezone: ro={row.get('ro')!r} tz={row.get('tz')!r}")
        except BaseException:
            try:
                conn.close()
            finally:
                pass
            raise
        return conn
    return make


class DbObserver:
    """Mot observer = MOT phien probe (ket noi do `connect_fn()` tao) + so lenh toi da. Moi phuong thuc chi SELECT."""

    @property
    def tooling_sha256(self) -> str:
        """Hash cong cu cua observer trong-tien-trinh: script nay + model Q (de ghim vao ke hoach)."""
        return hashlib.sha256(json.dumps({"observer": script_sha256(), "quiescence_model": qm.__source_sha256__}, sort_keys=True).encode("utf-8")).hexdigest()

    def __init__(self, connect_fn: Callable[[], Any], *, max_statements: int = 40, closer: Callable[[Any], None] | None = None) -> None:
        self._connect, self._closer = connect_fn, closer or (lambda conn: conn.close())
        self.max_statements, self.statements = max_statements, 0
        self.conn: Any = None
        self.baseline: dict[str, Any] | None = None

    # ---- tien ich
    def _q(self, conn: Any, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        if self.statements >= self.max_statements:
            raise RuntimeError("observer_statement_budget_exhausted")
        self.statements += 1
        return fetch(conn, sql, params)

    def _ensure(self) -> Any:
        if self.conn is None:
            self.conn = self._connect()
        return self.conn

    def close(self) -> None:
        if self.conn is not None:
            try:
                self._closer(self.conn)
            finally:
                self.conn = None

    # ---- baseline (TRUOC launch)
    def capture_baseline(self) -> dict[str, Any]:
        conn = self._ensure()
        info = _one(self._q(conn, SQL_CONNECTION_INFO), "connection_info")
        uptime = _int(_one(self._q(conn, SQL_UPTIME), "uptime").get("uptime_s"), "uptime_s")
        max_id = _int(_one(self._q(conn, SQL_MAX_THREAD_ID), "max_thread_id").get("max_id"), "max_id")
        identity = _one(self._q(conn, SQL_SERVER_IDENTITY), "server_identity")
        app_user, server_uuid = info.get("app_user"), identity.get("server_uuid")
        if not isinstance(app_user, str) or not app_user:
            raise ValueError("app_user: empty")
        if not isinstance(server_uuid, str) or not server_uuid:
            raise ValueError("server_uuid: empty")
        self.baseline = {"baseline_thread_id": max_id, "baseline_uptime_s": float(uptime), "probe_session_id": _int(info.get("probe_session_id"), "probe_session_id"), "app_user": app_user,
                         "server_uuid": server_uuid}
        return dict(self.baseline)

    # ---- probe (SAU khi job rong). Thoi gian do RUNNER ghi; observer chi tra du lieu server.
    def probe(self) -> dict[str, Any]:
        if self.baseline is None:
            raise RuntimeError("probe_before_baseline")
        conn = self._ensure()
        uptime = _int(_one(self._q(conn, SQL_UPTIME), "uptime").get("uptime_s"), "uptime_s")
        rows = [normalize_session_row(r) for r in self._q(conn, SQL_SESSIONS, [self.baseline["baseline_thread_id"]])]
        rolling = [{"trx_mysql_thread_id": r.get("trx_mysql_thread_id"), "trx_state": r.get("trx_state")} for r in self._q(conn, SQL_ROLLING_BACK)]
        return {"status": "ok", "rows": rows, "truncated": len(rows) >= PROBE_LIMIT or len(rolling) >= PROBE_LIMIT, "rolling_back": rolling, "uptime_s": float(uptime)}

    # ---- Q0 self-test (chi doc; mo them 2 ket noi phu roi dong)
    def selftest(self, *, wait_gone_s: float = 10.0, poll_s: float = 0.5, sleep: Callable[[float], None] | None = None, monotonic: Callable[[], float] | None = None) -> dict[str, Any]:
        import time
        sleep, monotonic = sleep or time.sleep, monotonic or time.monotonic
        keys = {k: False for k in qm.SELFTEST_KEYS}
        details: dict[str, Any] = {"errors": []}
        conn_a = self._ensure()
        info_a = _one(self._q(conn_a, SQL_CONNECTION_INFO), "connection_info")
        details["probe_session_id"], details["app_user"] = _int(info_a.get("probe_session_id"), "probe_session_id"), info_a.get("app_user")
        identity = _one(self._q(conn_a, SQL_SERVER_IDENTITY), "server_identity")
        server = {"server_uuid": identity.get("server_uuid"), "version": identity.get("version"), "uptime_s": float(_int(_one(self._q(conn_a, SQL_UPTIME), "uptime").get("uptime_s"), "uptime_s"))}
        conn_b = self._connect()
        conn_c = None
        try:
            id_b = _int(_one(self._q(conn_b, SQL_CONNECTION_INFO), "connection_info_b").get("probe_session_id"), "id_b")
            rows = {r["PROCESSLIST_ID"]: r for r in (normalize_session_row(x) for x in self._q(conn_a, SQL_SESSIONS, [id_b - 1]))}
            seen = rows.get(id_b)
            keys["visible_other_session"] = bool(seen is not None and seen["PROCESSLIST_USER"] == details["app_user"])
            keys["visible_db_null_session"] = bool(seen is not None and seen["PROCESSLIST_DB"] is None)
            keys["pid_attribute_visible"] = bool(seen is not None and seen["pid"] == os.getpid())
            try:
                self._q(conn_a, SQL_ROLLING_BACK)
                keys["innodb_trx_readable"] = True
            except Exception as exc:                                                                       # noqa: BLE001
                details["errors"].append(f"innodb_trx: {type(exc).__name__}")
            conn_c = self._connect()
            id_c = _int(_one(self._q(conn_c, SQL_CONNECTION_INFO), "connection_info_c").get("probe_session_id"), "id_c")
            details["successive_ids"] = [id_b, id_c]
            details["ids_increase"] = id_c > id_b                                                          # kiem tra tinh tao (KHONG phai chung minh don dieu) — NHUNG sai thi KHONG ghi Q0 receipt
            schemas = [r.get("schema_name") for r in self._q(conn_a, SQL_DATABASES)]
            details["databases_truncated"] = len(schemas) > DATABASES_LIMIT
            details["databases"] = sorted(x for x in schemas[:DATABASES_LIMIT] if isinstance(x, str))
        finally:
            for conn in (conn_b, conn_c):
                if conn is not None:
                    try:
                        self._closer(conn)
                    except Exception as exc:                                                               # noqa: BLE001
                        details["errors"].append(f"close: {type(exc).__name__}")
        deadline = monotonic() + wait_gone_s
        while True:
            remaining_rows = {normalize_session_row(r)["PROCESSLIST_ID"] for r in self._q(conn_a, SQL_SESSIONS, [id_b - 1])}
            if id_b not in remaining_rows:
                keys["removed_after_close"] = True
                break
            if monotonic() >= deadline or self.statements >= self.max_statements:
                break
            sleep(poll_s)
        return {**keys, "details": details, "server": server}


def q0_receipt_document(result: Mapping[str, Any], *, now_utc) -> dict[str, Any]:
    """Tai lieu Q0 receipt (dung dinh dang `repair_phase_runner_v2.load_q0_receipt`): pin observer/SQL-set/model + server identity + ket qua self-test. Khong chua text SQL cua phien khac."""
    return {"kind": "q0_selftest_receipt", "created_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "observer_script_sha256": script_sha256(), "sql_set_sha256": sql_set_sha256(),
            "quiescence_model_sha256": qm.__source_sha256__, "server": dict(result["server"]), "selftest": {k: result.get(k) for k in qm.SELFTEST_KEYS}, "details": result.get("details", {})}


def write_q0_receipt(result: Mapping[str, Any], path, *, now_utc) -> str:
    """Ghi Q0 receipt `xb` (khong ghi de) CHI KHI moi khoa self-test la True; tra SHA-256 cua byte da ghi (approval ghim gia tri nay)."""
    import json
    if any(result.get(k) is not True for k in qm.SELFTEST_KEYS):
        raise ValueError("q0_selftest_not_all_true: khong ghi receipt")
    details = result.get("details") if isinstance(result.get("details"), Mapping) else {}
    ids = details.get("successive_ids")
    if details.get("ids_increase") is not True or not (isinstance(ids, list) and len(ids) == 2 and all(type(i) is int and i > 0 for i in ids) and ids[0] < ids[1]):
        raise ValueError("q0_connection_id_sanity_false: khong ghi receipt")
    if details.get("databases_truncated") is not False or not isinstance(details.get("databases"), list) or not details["databases"]:
        raise ValueError("q0_database_list_missing_or_truncated: khong ghi receipt")
    if not isinstance(details.get("app_user"), str) or not details["app_user"]:
        raise ValueError("q0_app_user_missing: khong ghi receipt")
    data = json.dumps(q0_receipt_document(result, now_utc=now_utc), ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    with open(path, "xb") as handle:
        handle.write(data)
    return hashlib.sha256(data).hexdigest()
