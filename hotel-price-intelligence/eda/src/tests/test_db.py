"""Test cho `db.py` (EDA_CURATED_PLAN.md muc 5 quy tac 1, muc 10.1).

Test MySQL (`@pytest.mark.mysql`) CHI dung disposable database tu tao/tu xoa trong cung 1 test -
KHONG BAO GIO dung tren warehouse current, dung nhu quy tac 1 cua plan ("khong thu ghi len warehouse
current de test"). Test thuan (khong MySQL) dung mock cho phan pointer/file-system.
"""
from __future__ import annotations

import decimal
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import db


# ======================================================================== _coerce_decimal_columns (thuan, khong MySQL)
def test_coerce_decimal_columns_ep_ve_float64():
    df = pd.DataFrame({
        "price_per_night": [decimal.Decimal("100.50"), decimal.Decimal("200.00"), None],
        "hotel_id": ["h1", "h2", "h3"],
    })
    out = db._coerce_decimal_columns(df)
    assert out["price_per_night"].dtype == "float64"
    assert out["price_per_night"].tolist()[0] == pytest.approx(100.50)
    assert pd.isna(out["price_per_night"].tolist()[2])
    assert out["hotel_id"].tolist() == ["h1", "h2", "h3"]  # cot khong phai Decimal giu nguyen


def test_coerce_decimal_columns_cho_phep_tinh_quantile_khong_loi():
    """Tai hien dung loi da gap tren du lieu that: Decimal tron voi float trong np.quantile."""
    df = pd.DataFrame({"price_per_night": [decimal.Decimal(x) for x in ("100", "200", "300")]})
    out = db._coerce_decimal_columns(df)
    assert out["price_per_night"].quantile(0.5) == pytest.approx(200.0)  # khong raise TypeError


# ======================================================================== load_pointer (thuan, khong MySQL)
def test_load_pointer_khong_ton_tai_thi_fail(tmp_path):
    with pytest.raises(db.SnapshotVerificationError, match="khong tim thay"):
        db.load_pointer(tmp_path / "khong_ton_tai.json")


def test_load_pointer_doc_dung_json(tmp_path):
    path = tmp_path / "pointer.json"
    payload = {"warehouse_database": "warehouse_x", "batch_id": "b1"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert db.load_pointer(path) == payload


# ======================================================================== MySQL: disposable DB
@pytest.fixture()
def disposable_db():
    """Tao 1 database toi gian (bang `hotels(hotel_id, city)`), yield ten DB, DROP trong finally."""
    name = f"wh_eda_smoketest_{uuid.uuid4().hex[:12]}"
    with db._connect_raw(None) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(f"CREATE DATABASE `{name}`")
            cursor.execute(f"CREATE TABLE `{name}`.hotels (hotel_id VARCHAR(50) PRIMARY KEY, city VARCHAR(50))")
            cursor.execute(f"INSERT INTO `{name}`.hotels (hotel_id, city) VALUES ('h1', 'Ha Noi')")
            conn.commit()
        finally:
            cursor.close()
    try:
        yield name
    finally:
        with db._connect_raw(None) as conn:
            cursor = conn.cursor()
            cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
            conn.commit()
            cursor.close()


@pytest.mark.mysql
def test_enforce_read_only_chan_update_tren_disposable_db(disposable_db):
    """Cot loi cua M1 (file 03): UPDATE/DELETE/INSERT/DDL bi chan sau khi bat READ ONLY."""
    with db._connect_raw(disposable_db) as conn:
        db.enforce_read_only_session(conn)
        db.verify_read_only_enforced(conn)  # tu raise ReadOnlyEnforcementError neu KHONG bi chan


@pytest.mark.mysql
@pytest.mark.parametrize("sql", [
    "UPDATE hotels SET city='X' WHERE hotel_id='h1'",
    "DELETE FROM hotels WHERE hotel_id='h1'",
    "INSERT INTO hotels (hotel_id, city) VALUES ('h2', 'Y')",
    "CREATE TABLE probe (i INT)",
])
def test_moi_dang_ghi_deu_bi_chan_sau_read_only(disposable_db, sql):
    with db._connect_raw(disposable_db) as conn:
        db.enforce_read_only_session(conn)
        cursor = conn.cursor()
        try:
            with pytest.raises(Exception) as exc_info:
                cursor.execute(sql)
            assert getattr(exc_info.value, "errno", None) == db.READ_ONLY_ERRNO
        finally:
            cursor.close()
            conn.rollback()


@pytest.mark.mysql
def test_select_van_chay_duoc_sau_read_only(disposable_db):
    with db._connect_raw(disposable_db) as conn:
        db.enforce_read_only_session(conn)
        result = db.read_sql(conn, "SELECT hotel_id, city FROM hotels")
        assert result.to_dict("records") == [{"hotel_id": "h1", "city": "Ha Noi"}]


@pytest.mark.mysql
def test_verify_read_only_enforced_tu_phat_hien_neu_khong_bi_chan(disposable_db):
    """Phan ung voi loi 'trong sang' - neu 1 ngay nao do READ ONLY khong con hieu luc (vd doi driver/
    server), test nay phai FAIL ro rang thay vi am tham bao xanh."""
    with db._connect_raw(disposable_db) as conn:
        # KHONG goi enforce_read_only_session() -> gia lap tinh huong "quen bat read-only"
        with pytest.raises(db.ReadOnlyEnforcementError, match="KHONG chan duoc UPDATE"):
            db.verify_read_only_enforced(conn)
        conn.rollback()  # UPDATE that su chay (vi chua bat read-only) - phai rollback, khong de sot


@pytest.mark.mysql
def test_connect_thuc_su_bi_chan_ghi_khong_can_goi_verify_rieng(disposable_db, monkeypatch, tmp_path):
    """connect() (duong san xuat that) phai tu bat read-only, khong can nguoi goi tu lam gi them."""
    # Gia lap 1 pointer + batch PASS toi thieu tren disposable DB de connect() chay het duoc.
    with db._connect_raw(disposable_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE etl_import_batches (batch_id VARCHAR(40) PRIMARY KEY, status VARCHAR(20), "
            "source_manifest_sha256 CHAR(64), canonicalization_version VARCHAR(50), "
            "canonicalization_git_commit VARCHAR(64), finished_at DATETIME)"
        )
        cursor.execute(
            "INSERT INTO etl_import_batches VALUES ('b1','pass',%s,'v1','deadbeef',NOW())", ("0" * 64,)
        )
        cursor.execute("CREATE TABLE etl_import_sources (import_batch_id VARCHAR(40), source_code VARCHAR(20))")
        conn.commit()
        cursor.close()

    pointer_path = tmp_path / "pointer.json"
    pointer_path.write_text(json.dumps({"warehouse_database": disposable_db, "batch_id": "b1"}), encoding="utf-8")

    with patch("app.warehouse.registry.verify_source_manifest", return_value="0" * 64):
        with db.connect(pointer_path=pointer_path) as (conn, snapshot):
            assert snapshot.database == disposable_db and snapshot.batch_id == "b1"
            cursor = conn.cursor()
            with pytest.raises(Exception) as exc_info:
                cursor.execute("UPDATE hotels SET city='Z' WHERE 1=0")
            assert getattr(exc_info.value, "errno", None) == db.READ_ONLY_ERRNO
            cursor.close()
            conn.rollback()
