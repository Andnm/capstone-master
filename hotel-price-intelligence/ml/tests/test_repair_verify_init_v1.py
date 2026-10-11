"""audit_tools/repair_verify_init_v1.py — verify-config sau init, truoc build: 1 SELECT, gia tri du kien tu bien lai du doan doc lap (khong tu hang DB), fail-closed."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1] / "audit_tools"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vi = _load(TOOLS / "repair_verify_init_v1.py", "repair_verify_init_v1")
VERSION = "ds_20261009_nf1"
CONFIG = {"builder_code": {"code_sha256": "c" * 64, "files": {"a.py": "d" * 64}}, "purpose": "dev", "evaluation_horizons": [1], "note": "tiếng Việt"}
CONFIG_SHA = hashlib.sha256(json.dumps(CONFIG, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()          # tinh DOC LAP (khong qua ham cua script)
RECEIPT = {"kind": "config_prediction_receipt", "ok": True, "violations": [], "old_config_sha256": "a" * 64, "predicted_config_sha256": CONFIG_SHA, "old_code_sha256": "b" * 64, "new_code_sha256": "c" * 64,
           "changed_files": ["x"]}


def receipt_file(tmp_path, **override):
    path = tmp_path / "prediction.json"
    path.write_text(json.dumps({**RECEIPT, **override}), encoding="utf-8")
    return path


def manifest_row(**override):
    row = {"dataset_version": VERSION, "status": "running", "last_completed_step": "initialized", "build_config_sha256": CONFIG_SHA, "build_config_json": json.dumps(CONFIG, ensure_ascii=False)}
    row.update(override)
    return row


class FakeCursor:
    def __init__(self, conn, dictionary):
        self.conn, self.dictionary, self.rows = conn, dictionary, []

    def execute(self, sql, params=()):
        self.conn.log.append((sql, tuple(params)))
        if sql == vi.SQL_SANITY:
            self.rows = [{"ro": self.conn.ro, "tz": self.conn.tz}]
        elif sql == vi.SQL_MANIFEST:
            self.rows = list(self.conn.rows)
        else:
            self.rows = []

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows, ro=1, tz="+00:00"):
        self.rows, self.ro, self.tz, self.log, self.rolled_back, self.closed = rows, ro, tz, [], 0, False

    def cursor(self, dictionary=False):
        return FakeCursor(self, dictionary)

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


def receipt_obj(tmp_path, **override):
    return vi.load_prediction_receipt(receipt_file(tmp_path, **override))


# =========================================================================== canonical + ket noi mac dinh
def test_canonical_json_sorts_keys_uses_compact_separators_and_keeps_non_ascii():
    assert vi.canonical_json({"b": 1, "a": "ế", "c": [1, {"z": 0, "y": None}]}) == '{"a":"ế","b":1,"c":[1,{"y":null,"z":0}]}'
    assert CONFIG_SHA == vi.sha256_hex(vi.canonical_json(CONFIG)) and not (set(CONFIG) == set(sorted(CONFIG)) and list(CONFIG) == sorted(CONFIG))         # CONFIG co thu tu khoa KHONG sap xep => kiem duoc sort_keys


def test_the_default_connection_uses_the_backend_kwargs_for_the_named_database_in_pure_python_mode(monkeypatch):
    import sys as _sys
    import types as _types
    calls = {}
    fake_mysql, fake_connector = _types.ModuleType("mysql"), _types.ModuleType("mysql.connector")
    fake_connector.connect = lambda **kw: calls.setdefault("kwargs", kw) or "conn"
    fake_mysql.connector = fake_connector
    fake_app, fake_wh, fake_conn = _types.ModuleType("app"), _types.ModuleType("app.warehouse"), _types.ModuleType("app.warehouse.connection")
    fake_conn._connect_kwargs = lambda database: {"host": "h", "database": database, "use_pure": False}
    for name, module in (("mysql", fake_mysql), ("mysql.connector", fake_connector), ("app", fake_app), ("app.warehouse", fake_wh), ("app.warehouse.connection", fake_conn)):
        monkeypatch.setitem(_sys.modules, name, module)
    vi.default_connect("warehouse_dsdev_x")
    assert calls["kwargs"] == {"host": "h", "database": "warehouse_dsdev_x", "use_pure": True}                   # che do thuan duoc ghim trong ma, backend khong tat duoc


# =========================================================================== bien lai du doan
def test_a_valid_prediction_receipt_is_loaded_with_its_byte_hash(tmp_path):
    path = receipt_file(tmp_path)
    loaded = vi.load_prediction_receipt(path)
    assert loaded["file_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest() and loaded["predicted_config_sha256"] == CONFIG_SHA


@pytest.mark.parametrize("override", [{"kind": "other"}, {"ok": False}, {"violations": ["changed_files_mismatch"]}, {"predicted_config_sha256": "A" * 64}, {"predicted_config_sha256": None},
                                      {"new_code_sha256": "short"}, {"old_config_sha256": 7}])
def test_a_receipt_that_is_not_ok_or_malformed_is_rejected(tmp_path, override):
    with pytest.raises(ValueError, match="prediction_receipt_invalid"):
        vi.load_prediction_receipt(receipt_file(tmp_path, **override))


# =========================================================================== verify (1 SELECT, kiem tung dieu kien)
def test_a_consistent_initialized_row_passes_every_check(tmp_path):
    conn = FakeConn([manifest_row()])
    report = vi.verify(conn, dataset_version=VERSION, receipt=receipt_obj(tmp_path))
    assert report["verdict"] == "PASS" and all(report["checks"].values()) and report["rows"] == 1
    assert report["stored_config_sha256"] == report["recomputed_config_sha256"] == report["predicted_config_sha256"] == CONFIG_SHA
    assert conn.log == [(vi.SQL_MANIFEST, (VERSION,))]                                                          # dung MOT cau lenh, tham so tach rieng


def test_a_passing_report_has_exactly_the_required_checks_and_echoes_the_database(tmp_path):
    report = vi.verify(FakeConn([manifest_row()]), dataset_version=VERSION, receipt=receipt_obj(tmp_path), database="warehouse_dsdev_x")
    assert set(report["checks"]) == set(vi.REQUIRED_CHECKS) and len(vi.REQUIRED_CHECKS) == 8 and len(set(vi.REQUIRED_CHECKS)) == 8 and report["database"] == "warehouse_dsdev_x"
    assert vi.verify(FakeConn([manifest_row()]), dataset_version=VERSION, receipt=receipt_obj(tmp_path))["database"] is None                  # khong khai bao DB => None (consumer se tu choi)


@pytest.mark.parametrize("override,failed", [
    ({"status": "pass"}, "status_running"), ({"last_completed_step": "validation"}, "step_initialized"), ({"build_config_sha256": "f" * 64}, "stored_sha_equals_predicted"),
    ({"build_config_json": json.dumps({**CONFIG, "purpose": "official"})}, "recomputed_sha_equals_predicted"),
    ({"build_config_json": json.dumps({**CONFIG, "builder_code": {"code_sha256": "e" * 64}})}, "code_sha_equals_receipt"),
    ({"build_config_json": "not json"}, "json_parsed"), ({"build_config_json": None}, "json_parsed"),
])
def test_each_single_deviation_fails_with_the_specific_check_and_the_verdict_is_fail(tmp_path, override, failed):
    report = vi.verify(FakeConn([manifest_row(**override)]), dataset_version=VERSION, receipt=receipt_obj(tmp_path))
    assert report["verdict"] == "FAIL" and report["checks"][failed] is False, failed


def test_stored_hash_disagreeing_with_the_recomputed_hash_fails_even_if_one_of_them_equals_the_prediction(tmp_path):
    tampered = {**CONFIG, "evaluation_horizons": [3]}
    row = manifest_row(build_config_json=json.dumps(tampered))                                                  # cot sha van la gia tri du kien nhung JSON da doi
    report = vi.verify(FakeConn([row]), dataset_version=VERSION, receipt=receipt_obj(tmp_path))
    assert report["checks"]["stored_sha_equals_predicted"] is True and report["checks"]["recomputed_sha_equals_predicted"] is False and report["checks"]["stored_sha_equals_recomputed"] is False
    assert report["verdict"] == "FAIL"


@pytest.mark.parametrize("rows", [[], [manifest_row(), manifest_row()]])
def test_zero_or_several_rows_fail_without_reading_any_field(tmp_path, rows):
    report = vi.verify(FakeConn(rows), dataset_version=VERSION, receipt=receipt_obj(tmp_path))
    assert report["verdict"] == "FAIL" and report["checks"] == {"exactly_one_row": False}


def test_the_json_column_may_arrive_as_bytes(tmp_path):
    row = manifest_row(build_config_json=json.dumps(CONFIG, ensure_ascii=False).encode("utf-8"))
    assert vi.verify(FakeConn([row]), dataset_version=VERSION, receipt=receipt_obj(tmp_path))["verdict"] == "PASS"


def test_the_expected_value_comes_from_the_receipt_not_from_the_database_row(tmp_path):
    other = receipt_obj(tmp_path, predicted_config_sha256="9" * 64)
    report = vi.verify(FakeConn([manifest_row()]), dataset_version=VERSION, receipt=other)
    assert report["verdict"] == "FAIL" and report["checks"]["stored_sha_equals_predicted"] is False and report["checks"]["recomputed_sha_equals_predicted"] is False


# =========================================================================== phien chi-doc + CLI
def test_the_session_must_be_read_only_and_utc_or_the_run_fails_closed():
    good = FakeConn([])
    vi.readonly_session(good)
    assert [sql for sql, _ in good.log] == [vi.SQL_SET_READ_ONLY, vi.SQL_SANITY]
    for ro, tz in ((0, "+00:00"), (1, "SYSTEM")):
        with pytest.raises(RuntimeError, match="session_not_read_only_or_wrong_timezone"):
            vi.readonly_session(FakeConn([], ro=ro, tz=tz))


def test_main_writes_the_report_once_exits_zero_and_closes_the_connection(tmp_path, capsys):
    conn = FakeConn([manifest_row()])
    out = tmp_path / "verify.json"
    rc = vi.main(["--db", "warehouse_dsdev_x", "--dataset-version", VERSION, "--prediction-receipt", str(receipt_file(tmp_path)), "--out", str(out)], connect=lambda db: conn)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0 and report["verdict"] == "PASS" and report["database"] == "warehouse_dsdev_x" and conn.closed and conn.rolled_back == 1
    assert [sql for sql, _ in conn.log] == [vi.SQL_SET_READ_ONLY, vi.SQL_SANITY, vi.SQL_MANIFEST]
    assert "password" not in json.dumps(report).lower() and json.loads(capsys.readouterr().out)["verdict"] == "PASS"
    assert vi.main(["--db", "warehouse_dsdev_x", "--dataset-version", VERSION, "--prediction-receipt", str(receipt_file(tmp_path)), "--out", str(out)], connect=lambda db: pytest.fail("no DB")) == 2   # khong ghi de, khong cham DB


def test_main_exits_one_on_a_failed_verdict_but_still_writes_the_report(tmp_path):
    out = tmp_path / "fail.json"
    rc = vi.main(["--db", "warehouse_dsdev_x", "--dataset-version", VERSION, "--prediction-receipt", str(receipt_file(tmp_path)), "--out", str(out)], connect=lambda db: FakeConn([manifest_row(status="fail")]))
    assert rc == 1 and json.loads(out.read_text(encoding="utf-8"))["verdict"] == "FAIL"


@pytest.mark.parametrize("db,version", [("bad name", VERSION), ("x;DROP", VERSION), ("warehouse_dsdev_x", "ds_1"), ("warehouse_dsdev_x", "ds_20261009_NF1"), ("a" * 65, VERSION)])
def test_bad_identifiers_exit_two_before_any_database_activity(tmp_path, capsys, db, version):
    rc = vi.main(["--db", db, "--dataset-version", version, "--prediction-receipt", str(receipt_file(tmp_path)), "--out", str(tmp_path / "o.json")], connect=lambda d: pytest.fail("no DB"))
    assert rc == 2 and json.loads(capsys.readouterr().out)["error"] == "bad_identifier" and not (tmp_path / "o.json").exists()


def test_a_bad_receipt_exits_two_before_any_database_activity(tmp_path, capsys):
    rc = vi.main(["--db", "warehouse_dsdev_x", "--dataset-version", VERSION, "--prediction-receipt", str(receipt_file(tmp_path, ok=False)), "--out", str(tmp_path / "o.json")], connect=lambda d: pytest.fail("no DB"))
    assert rc == 2 and "prediction_receipt_rejected" in json.loads(capsys.readouterr().out)["error"]


def test_a_session_that_is_not_read_only_closes_the_connection_and_raises(tmp_path):
    conn = FakeConn([manifest_row()], ro=0)
    with pytest.raises(RuntimeError, match="session_not_read_only"):
        vi.main(["--db", "warehouse_dsdev_x", "--dataset-version", VERSION, "--prediction-receipt", str(receipt_file(tmp_path)), "--out", str(tmp_path / "o.json")], connect=lambda d: conn)
    assert conn.closed and not (tmp_path / "o.json").exists() and all(sql != vi.SQL_MANIFEST for sql, _ in conn.log)


def test_the_sql_set_hash_is_recomputed_from_the_three_statements():
    expected = hashlib.sha256("\n".join([vi.SQL_SET_READ_ONLY, vi.SQL_SANITY, vi.SQL_MANIFEST]).encode("utf-8")).hexdigest()
    assert vi.sql_set_sha256() == expected
    for sql in (vi.SQL_SANITY, vi.SQL_MANIFEST):
        assert sql.startswith("SELECT /*+ MAX_EXECUTION_TIME(5000) */ ") and ";" not in sql
    assert vi.SQL_MANIFEST.count("%s") == 1


def test_the_report_is_created_exclusively_even_if_the_file_appears_after_the_precheck(tmp_path):
    out = tmp_path / "race.json"

    def connect(db):
        out.write_text("someone else", encoding="utf-8")                                                         # file xuat hien SAU kiem tra ton tai ban dau
        return FakeConn([manifest_row()])

    with pytest.raises(FileExistsError):
        vi.main(["--db", "warehouse_dsdev_x", "--dataset-version", VERSION, "--prediction-receipt", str(receipt_file(tmp_path)), "--out", str(out)], connect=connect)
    assert out.read_text(encoding="utf-8") == "someone else"
