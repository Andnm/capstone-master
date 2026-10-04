"""Adapter staging hep cho schema nguon cu (local_aux thieu `crawl_run_items.dead_link_confirmation`).

Phan thuan (find_adapter) luon chay; phan E2E tren MySQL that opt-in `WAREHOUSE_SMOKE=1` va dung dump `mysqldump` that tu DB tam.
Khong co flag nhan thieu cot tong quat: hash la / cot da ton tai / con drift khac deu phai FAIL.
"""
from __future__ import annotations

import dataclasses
import os
import subprocess
import uuid

import pytest

from app.core.config import settings
from app.warehouse import schema_adapters
from app.warehouse.batch import build_warehouse
from app.warehouse.connection import warehouse_connection
from app.warehouse.errors import SchemaMismatchError
from app.warehouse.schema_adapters import (
    AUX_MISSING_DEAD_LINK_CONFIRMATION_V1, SchemaAdapterError, apply_staging_adapter, find_adapter,
)
from app.warehouse.source_manifest import compute_schema_sha256_from_dump
from app.warehouse.staging import _client_defaults_file

from test_warehouse_build_e2e import _init, _inputs, _make_dump, _notes, _root

REAL_HASH = "5155ba4e5b4f7375d5bd4f9bd4ceb23b258990c445a6365c8635d01ac29d8037"
smoke = pytest.mark.skipif(os.environ.get("WAREHOUSE_SMOKE") != "1", reason="can MySQL that - dat WAREHOUSE_SMOKE=1")


def test_registry_matches_exact_source_and_hash_only():
    assert find_adapter("local_aux", REAL_HASH) is AUX_MISSING_DEAD_LINK_CONFIRMATION_V1
    assert find_adapter("vps", REAL_HASH) is None                              # sai source_code
    assert find_adapter("local_aux", REAL_HASH[:-1] + "0") is None             # sai hash 1 ky tu
    assert find_adapter("local_aux", REAL_HASH.upper()) is None                # hash chi nhan dang chu thuong da canonical
    assert find_adapter("local_primary", "6fc6355164d69c4d3ca5bbee08963f76dc4fd37ddcc6d148bae69d0101c4189b") is None
    assert find_adapter("local_aux", REAL_HASH, registry=()) is None           # registry rong -> khong adapter


def test_apply_returns_none_when_no_adapter_registered():
    assert apply_staging_adapter("wh_staging_whatever_vps", source_code="vps", raw_schema_sha256="0" * 64) is None


def _redump_without(database, path, drop):
    for table, column in drop:
        _root(f"ALTER TABLE `{database}`.`{table}` DROP COLUMN `{column}`")
    with _client_defaults_file(settings.DB_USER, settings.DB_PASSWORD) as cnf, open(path, "wb") as out:
        subprocess.run(["mysqldump", f"--defaults-extra-file={cnf}", "--single-transaction", "--no-tablespaces", "--set-charset",
                        "--complete-insert", database, "hotels", "crawl_runs", "crawl_run_items", "price_observations"],
                       stdout=out, check=True)


def _staging_leftovers(batch_id):
    with warehouse_connection(None, verify=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SHOW DATABASES LIKE %s", (f"wh_staging_{batch_id}%",))
        rows = cursor.fetchall()
        cursor.close()
    return rows


def _scenario(tmp_path, tag, *, drop, register: bool, hash_from_full_schema: bool = False):
    created: list[str] = []
    db_p, path_p = _make_dump(tmp_path, "local_primary", tag)
    db_v, path_v = _make_dump(tmp_path, "vps", tag)
    created += [db_p, db_v]
    if drop:
        _redump_without(db_v, path_v, drop)
    schema_hash = compute_schema_sha256_from_dump(path_v)[0]
    if register:
        adapter = dataclasses.replace(AUX_MISSING_DEAD_LINK_CONFIRMATION_V1, source_code="vps", raw_schema_sha256=schema_hash)
        registry = (adapter,)
    else:
        registry = ()
    warehouse = f"warehouse_fxad{tag}"
    _init(warehouse)
    created.append(warehouse)
    batch = f"fxad{tag}"
    inputs = dataclasses.replace(_inputs(tmp_path, [("local_primary", path_p), ("vps", path_v)], warehouse), batch_id=batch)
    return created, warehouse, batch, inputs, schema_hash, registry


@smoke
def test_adapter_end_to_end_adds_nullable_column_and_reports(tmp_path, monkeypatch):
    tag = uuid.uuid4().hex[:6]
    created, warehouse, batch, inputs, schema_hash, registry = _scenario(
        tmp_path, tag, drop=[("crawl_run_items", "dead_link_confirmation")], register=True)
    try:
        monkeypatch.setattr(schema_adapters, "REGISTERED_ADAPTERS", registry)
        report = build_warehouse(inputs)
        assert report["status"] == "pass", report.get("failed_checks")
        info = report["steps"]["10_staging"]["vps"]["schema_adapter"]
        assert info["adapter_id"] == "aux-missing-dead-link-confirmation-v1" and info["raw_schema_sha256"] == schema_hash
        assert info["rows"] == 2 and info["null_rows"] == 2 and info["non_null_rows"] == 0
        assert info["columns_after"] == info["columns_before"] + 1 and info["after_column"] == "reference_match_status"
        assert report["steps"]["10_staging"]["local_primary"]["schema_adapter"] is None
        assert _notes(warehouse, batch)["schema_adapters"]["vps"]["raw_schema_sha256"] == schema_hash
        with warehouse_connection(warehouse) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), SUM(dead_link_confirmation IS NOT NULL) FROM crawl_run_items")
            total, non_null = cursor.fetchone()
            cursor.execute("SELECT m.source_code, COUNT(*) FROM etl_item_map m GROUP BY 1 ORDER BY 1")
            per_source = dict(cursor.fetchall())
            cursor.close()
        assert int(total) == sum(per_source.values()) and int(non_null or 0) == 0     # NULL toan bo = structural missingness
        assert _staging_leftovers(batch) == []
    finally:
        for database in created:
            _root(f"DROP DATABASE IF EXISTS `{database}`")


@smoke
def test_unregistered_missing_column_still_fails_closed(tmp_path, monkeypatch):
    tag = uuid.uuid4().hex[:6]
    created, warehouse, batch, inputs, _hash, registry = _scenario(
        tmp_path, tag, drop=[("crawl_run_items", "dead_link_confirmation")], register=False)
    try:
        monkeypatch.setattr(schema_adapters, "REGISTERED_ADAPTERS", registry)
        with pytest.raises(SchemaMismatchError, match="THIEU cot"):
            build_warehouse(inputs)
        assert _staging_leftovers(batch) == []
    finally:
        for database in created:
            _root(f"DROP DATABASE IF EXISTS `{database}`")


@smoke
def test_adapter_refuses_when_column_already_present(tmp_path, monkeypatch):
    tag = uuid.uuid4().hex[:6]
    created, warehouse, batch, inputs, _hash, registry = _scenario(tmp_path, tag, drop=[], register=True)
    try:
        monkeypatch.setattr(schema_adapters, "REGISTERED_ADAPTERS", registry)
        with pytest.raises(SchemaAdapterError, match="DA TON TAI"):
            build_warehouse(inputs)
        assert _staging_leftovers(batch) == []
    finally:
        for database in created:
            _root(f"DROP DATABASE IF EXISTS `{database}`")


@smoke
def test_other_schema_drift_still_fatal_after_adapter(tmp_path, monkeypatch):
    tag = uuid.uuid4().hex[:6]
    created, warehouse, batch, inputs, _hash, registry = _scenario(
        tmp_path, tag, drop=[("crawl_run_items", "dead_link_confirmation"), ("price_observations", "rooms_left")], register=True)
    try:
        monkeypatch.setattr(schema_adapters, "REGISTERED_ADAPTERS", registry)
        with pytest.raises(SchemaMismatchError, match="THIEU cot"):
            build_warehouse(inputs)
        assert _staging_leftovers(batch) == []
    finally:
        for database in created:
            _root(f"DROP DATABASE IF EXISTS `{database}`")
