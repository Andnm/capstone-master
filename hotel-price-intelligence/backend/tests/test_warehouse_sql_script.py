"""Lexer SQL - pure.

Moi test o day tuong ung 1 cach ma "split bang `;`" hoac "regex don gian" se sai:
chuoi/identifier chua `;`, comment chua `;`, va executable comment chua CODE that.

Nhom `test_adversarial_*` la cac ca GPT dua ra o review 06 va da chung minh ban pre-filter cu BO SOT
(2 trong so do bo sot o CA full scan). Chung phai o lai vinh vien - do la bang chung duy nhat rang
scan an toan truoc restore thuc su kin.
"""
import pytest

from app.warehouse.errors import SqlScriptError
from app.warehouse.sql_script import (
    classify_forbidden,
    iter_statements,
    sanitize_setup_sql,
    scan_file_for_forbidden,
    scan_text_for_forbidden,
)


def texts(sql):
    return [statement.text for statement in iter_statements(sql)]


def test_dau_cham_phay_trong_chuoi_khong_ket_thuc_statement():
    sql = "INSERT INTO t VALUES ('a;b'); SELECT 1;"
    assert texts(sql) == ["INSERT INTO t VALUES ('a;b')", "SELECT 1"]


def test_dau_cham_phay_trong_identifier_backtick():
    sql = "SELECT `weird;name` FROM t; SELECT 2;"
    assert texts(sql) == ["SELECT `weird;name` FROM t", "SELECT 2"]


def test_backslash_escape_trong_chuoi():
    sql = r"INSERT INTO t VALUES ('it\'s; here'); SELECT 3;"
    assert texts(sql) == [r"INSERT INTO t VALUES ('it\'s; here')", "SELECT 3"]


def test_doubling_quote_trong_chuoi():
    sql = "INSERT INTO t VALUES ('it''s; here'); SELECT 4;"
    assert texts(sql) == ["INSERT INTO t VALUES ('it''s; here')", "SELECT 4"]


def test_comment_gach_ngang_va_thang_va_khoi():
    sql = "-- day la; comment\nSELECT 1; # them; comment\n/* khoi; comment */ SELECT 2;"
    assert texts(sql) == ["-- day la; comment\nSELECT 1", "# them; comment\n/* khoi; comment */ SELECT 2"]


def test_statement_cuoi_khong_co_dau_cham_phay():
    assert texts("SELECT 1;\nSELECT 2") == ["SELECT 1", "SELECT 2"]


def test_statement_rong_bi_bo_qua():
    assert texts("SELECT 1;;   ;\n-- chi comment\n") == ["SELECT 1"]


def test_use_trong_chuoi_khong_bi_coi_la_bi_cam():
    sql = "INSERT INTO hotels VALUES ('Free use of gym', 'USE this room');"
    assert scan_text_for_forbidden(sql) == []


def test_use_that_bi_bat():
    found = scan_text_for_forbidden("SELECT 1;\nUSE other_db;")
    assert [item[0] for item in found] == ["USE"]


def test_create_database_va_schema_deu_bi_bat():
    assert classify_forbidden("CREATE DATABASE x") == "CREATE DATABASE"
    assert classify_forbidden("CREATE   SCHEMA  x") == "CREATE DATABASE"
    assert classify_forbidden("DROP DATABASE x") == "DROP DATABASE"
    assert classify_forbidden("drop schema x") == "DROP DATABASE"


def test_statement_bi_cam_giau_trong_executable_comment():
    """`/*!40000 USE x */` duoc MySQL THUC THI - khong duoc bo qua nhu comment thuong."""
    assert classify_forbidden("/*!40000 USE other_db */") == "USE"
    assert classify_forbidden("/*! CREATE DATABASE evil */") == "CREATE DATABASE"


def test_comment_thuong_thi_khong_phai_statement_bi_cam():
    assert classify_forbidden("/* USE other_db */ SELECT 1") is None
    assert classify_forbidden("-- USE other_db\nSELECT 1") is None


def test_comment_dung_truoc_statement_bi_cam_van_bi_bat():
    assert classify_forbidden("/* ghi chu */ USE other_db") == "USE"


def test_sanitize_bo_create_database_va_use():
    sql = "CREATE DATABASE IF NOT EXISTS hotel_price_intel;\nUSE hotel_price_intel;\nCREATE TABLE t (id INT);"
    assert sanitize_setup_sql(sql) == ["CREATE TABLE t (id INT)"]


def test_sanitize_tu_choi_drop_database():
    with pytest.raises(SqlScriptError, match="DROP DATABASE"):
        sanitize_setup_sql("DROP DATABASE hotel_price_intel;\nCREATE TABLE t (id INT);")


def test_sanitize_fail_neu_khong_con_statement():
    with pytest.raises(SqlScriptError, match="rong"):
        sanitize_setup_sql("USE x;")


def test_comment_khong_dong_thi_fail_chu_khong_nuot_im_lang():
    with pytest.raises(SqlScriptError, match="khong bao gio duoc dong"):
        list(iter_statements("SELECT 1; /* chua dong"))


def test_trich_dan_khong_dong_thi_fail():
    with pytest.raises(SqlScriptError, match="khong bao gio duoc dong"):
        list(iter_statements("SELECT 'chua dong"))


def test_delimiter_bi_tu_choi_thay_vi_parse_sai():
    with pytest.raises(SqlScriptError, match="DELIMITER"):
        scan_text_for_forbidden("DELIMITER //\nCREATE PROCEDURE p() BEGIN END //\nDELIMITER ;")


# ======================================================================================
# Ca doi khang GPT dua ra o review 06 - ban pre-filter cu BO SOT ca 5, trong do 2 ca
# (`CREATE/**/DATABASE`, `DROP/* ; */SCHEMA`) bo sot o CA full scan vi `\s+` khong khop comment.
# MySQL 8.0.45 that CHAP NHAN `CREATE/**/DATABASE x` va tao database - khong phai ca ly thuyet.
# ======================================================================================
ADVERSARIAL = {
    "newline_giua_2_keyword": ("CREATE\nDATABASE evil;", "CREATE DATABASE"),
    "crlf_giua_2_keyword": ("DROP\r\nSCHEMA evil;", "DROP DATABASE"),
    "comment_giua_2_keyword": ("CREATE/**/DATABASE evil;", "CREATE DATABASE"),
    "comment_co_cham_phay_giua": ("DROP/* ghi chu ; */SCHEMA evil;", "DROP DATABASE"),
    "exec_comment_co_newline": ("/*!40101 CREATE\nDATABASE evil */;", "CREATE DATABASE"),
    "exec_comment_co_comment": ("/*!40101 CREATE/**/DATABASE evil */;", "CREATE DATABASE"),
    "comment_dung_truoc_use": ("/* x */USE evil;", "USE"),
    "nhieu_dong_truoc_keyword": ("-- ghi chu\n\n  /* nua */\n  USE evil;", "USE"),
    "tab_va_formfeed": ("CREATE\t\r\n \tDATABASE evil;", "CREATE DATABASE"),
}

HARMLESS = {
    "keyword_trong_chuoi": "INSERT INTO t VALUES ('free use of gym','CREATE DATABASE x');",
    "keyword_trong_line_comment": "-- CREATE DATABASE x\nSELECT 1;",
    "keyword_trong_block_comment": "/* USE y */\nSELECT 1;",
    "keyword_la_identifier_backtick": "SELECT `use`, `database` FROM t;",
    "create_table_binh_thuong": "CREATE TABLE hotels (id INT);",
    "chuoi_nhieu_dong": "INSERT INTO t VALUES ('dong1\nUSE evil;\ndong2');",
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_adversarial_bi_bat_o_ca_3_duong(name, tmp_path):
    sql, expected = ADVERSARIAL[name]
    path = tmp_path / f"{name}.sql"
    path.write_text(sql, encoding="utf-8")
    assert classify_forbidden(sql) == expected
    assert [item[0] for item in scan_text_for_forbidden(sql)] == [expected]
    assert [item[0] for item in scan_file_for_forbidden(path)] == [expected]


@pytest.mark.parametrize("name", sorted(HARMLESS))
def test_harmless_khong_bao_gia(name, tmp_path):
    sql = HARMLESS[name]
    path = tmp_path / f"{name}.sql"
    path.write_text(sql, encoding="utf-8")
    assert classify_forbidden(sql) is None
    assert scan_text_for_forbidden(sql) == []
    assert scan_file_for_forbidden(path) == []


def test_scan_file_va_scan_text_luon_cho_cung_ket_qua(tmp_path):
    """Doc theo dong (file) va doc theo splitlines (text) phai khong bao gio lech nhau."""
    for name, (sql, _expected) in {**ADVERSARIAL, **{k: (v, None) for k, v in HARMLESS.items()}}.items():
        path = tmp_path / f"cmp_{name}.sql"
        path.write_text(sql, encoding="utf-8")
        assert [item[0] for item in scan_file_for_forbidden(path)] == [
            item[0] for item in scan_text_for_forbidden(sql)
        ], name


def test_utf8_bom_dau_file_khong_lam_lot_statement_bi_cam(tmp_path):
    path = tmp_path / "bom.sql"
    path.write_bytes("﻿USE evil;\n".encode("utf-8"))
    assert [item[0] for item in scan_file_for_forbidden(path)] == ["USE"]


def test_bom_khong_lam_hong_statement_binh_thuong(tmp_path):
    path = tmp_path / "bom_ok.sql"
    path.write_bytes("﻿CREATE TABLE t (id INT);\n".encode("utf-8"))
    assert scan_file_for_forbidden(path) == []


def test_chuoi_co_backslash_escape_newline_van_dong_dung():
    """'\\' cuoi dong escape ky tu dau dong sau - `USE` ben trong chuoi khong duoc tinh."""
    sql = "INSERT INTO t VALUES ('abc\\" + "\n" + "USE evil');\nSELECT 2;"
    assert scan_text_for_forbidden(sql) == []
    assert [statement.text for statement in iter_statements(sql)][-1] == "SELECT 2"


def test_dong_rat_dai_van_xu_ly_duoc(tmp_path):
    """mysqldump extended insert cho ra dong rat dai - khong duoc gia dinh dong ngan."""
    payload = ",".join(f"('hotel {index}; use x')" for index in range(20000))
    path = tmp_path / "long.sql"
    path.write_text(f"INSERT INTO t VALUES {payload};\nUSE evil;\n", encoding="utf-8")
    assert [item[0] for item in scan_file_for_forbidden(path)] == ["USE"]


def test_chuoi_ben_trong_exec_comment_khong_lam_lac_state():
    """mysqldump THAT SU phat `/*!40103 SET TIME_ZONE='+00:00' */;`.

    Bug da tung ton tai: dong chuoi ben trong exec comment tra state ve _NORMAL thay vi ve exec,
    khien `*/` sau do thanh token la va statement bi cat sai cho.
    """
    sql = (
        "/*!40103 SET TIME_ZONE='+00:00' */;\n"
        "/*!40101 SET NAMES utf8mb4 */;\n"
        "USE evil;\n"
    )
    assert [item[0] for item in scan_text_for_forbidden(sql)] == ["USE"]
    assert [statement.text for statement in iter_statements(sql)] == [
        "/*!40103 SET TIME_ZONE='+00:00' */",
        "/*!40101 SET NAMES utf8mb4 */",
        "USE evil",
    ]


def test_comment_thuong_long_trong_exec_comment_khong_thoat_som():
    sql = "/*!40101 SET x=1 /* ghi chu */ , y=2 */;\nSELECT 1;"
    assert [statement.text for statement in iter_statements(sql)] == [
        "/*!40101 SET x=1 /* ghi chu */ , y=2 */",
        "SELECT 1",
    ]


def test_cham_phay_ben_trong_exec_comment_khong_cat_statement():
    sql = "/*!40101 SET a=1; SET b=2 */;\nSELECT 9;"
    texts = [statement.text for statement in iter_statements(sql)]
    assert texts == ["/*!40101 SET a=1; SET b=2 */", "SELECT 9"]


def test_exec_comment_khong_dong_thi_fail(tmp_path):
    path = tmp_path / "unclosed_exec.sql"
    path.write_text("/*!40101 SET x=1\nSELECT 1;\n", encoding="utf-8")
    with pytest.raises(SqlScriptError, match="khong bao gio duoc dong"):
        scan_file_for_forbidden(path)


def test_statement_bi_cam_o_cuoi_file_khong_co_dau_cham_phay(tmp_path):
    path = tmp_path / "tail.sql"
    path.write_text("SELECT 1;\nUSE evil", encoding="utf-8")
    assert [item[0] for item in scan_file_for_forbidden(path)] == ["USE"]
