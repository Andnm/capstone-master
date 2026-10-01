"""Lexer SQL + preflight dump - pure (khong can MySQL).

`ADVERSARIAL` la phan vi du THAT GPT dua ra o review 06 va 08; o vong 08 GPT da tao duoc database
that tren MySQL 8.0.45 qua ban lexer cu bang cac ca `--\\f` va `/*!99999`. Khong xoa nhom nay.
Quy tac duoc test o day da DO THAT tren server (discuss/warehouse-build-implementation/09).
"""
import pytest

from app.warehouse.errors import SqlScriptError
from app.warehouse.etl_ddl import CORE_TABLES
from app.warehouse.sql_script import (
    SqlLexer,
    classify_forbidden,
    iter_statements,
    mysql_version_id,
    preflight_dump,
    sanitize_setup_sql,
    scan_file_for_forbidden,
    scan_text_for_forbidden,
)

V = 80045  # SELECT VERSION() tren may chinh = 8.0.45


def texts(sql, **kw):
    return [statement.text for statement in iter_statements(sql, server_version=V, **kw)]


def write(tmp_path, name, content=None, *, raw=None):
    path = tmp_path / name
    if raw is not None:
        path.write_bytes(raw)
    else:
        path.write_text(content, encoding="utf-8", newline="")  # KHONG de Windows doi \n thanh \r\n
    return path


# ======================================================================================
# Tach statement
# ======================================================================================
def test_dau_cham_phay_trong_chuoi_va_identifier():
    assert texts("INSERT INTO t VALUES ('a;b'); SELECT `w;x` FROM t;") == [
        "INSERT INTO t VALUES ('a;b')", "SELECT `w;x` FROM t"]


def test_escape_backslash_va_doubling():
    assert texts(r"INSERT INTO t VALUES ('it\'s; ok'); SELECT 'a''b;c';") == [
        r"INSERT INTO t VALUES ('it\'s; ok')", "SELECT 'a''b;c'"]


def test_comment_dung_truoc_khong_nam_trong_text():
    assert texts("-- a; b\nSELECT 1; # c; d\n/* e; f */ SELECT 2;") == ["SELECT 1", "SELECT 2"]


def test_comment_giua_statement_van_giu_trong_text():
    assert texts("SELECT 1 /* c */ , 2;") == ["SELECT 1 /* c */ , 2"]


def test_statement_cuoi_khong_cham_phay_va_statement_rong():
    assert texts("SELECT 1;;  ;\n-- chi comment\nSELECT 2") == ["SELECT 1", "SELECT 2"]


# ======================================================================================
# Phan vi du THAT (GPT 06 + 08) - phai bi bat o ca 3 duong
# ======================================================================================
ADVERSARIAL = {
    "newline_giua_2_keyword": ("CREATE\nDATABASE evil;", "CREATE DATABASE"),
    "crlf_giua_2_keyword": ("DROP\r\nSCHEMA evil;", "DROP DATABASE"),
    "comment_giua_2_keyword": ("CREATE/**/DATABASE evil;", "CREATE DATABASE"),
    "comment_co_cham_phay_giua": ("DROP/* ghi chu ; */SCHEMA evil;", "DROP DATABASE"),
    "exec_comment_co_newline": ("/*!40101 CREATE\nDATABASE evil */;", "CREATE DATABASE"),
    "comment_dung_truoc_use": ("/* x */USE evil;", "USE"),
    "nhieu_dong_truoc_keyword": ("-- ghi chu\n\n  /* nua */\n  USE evil;", "USE"),
    # GPT 08 B1a: MySQL coi `--` + ky tu dieu khien la comment
    "dashdash_formfeed": ("--\f comment\nCREATE DATABASE evil;", "CREATE DATABASE"),
    "dashdash_vtab": ("--\v comment\nCREATE DATABASE evil;", "CREATE DATABASE"),
    "dashdash_0x1f": ("--\x1f comment\nCREATE DATABASE evil;", "CREATE DATABASE"),
    "dashdash_del": ("--\x7f comment\nCREATE DATABASE evil;", "CREATE DATABASE"),
    # GPT 08 B1b: server BO QUA version comment cao hon chinh no
    "version_cao_giua_2_keyword": ("CREATE /*!99999 harmless */ DATABASE evil;", "CREATE DATABASE"),
    "version_cao_dau_statement": ("/*!99999 X*/ CREATE DATABASE evil;", "CREATE DATABASE"),
    "version_cao_roi_version_thap": ("CREATE /*!99999 X*/ /*!40101 DATABASE*/ evil;", "CREATE DATABASE"),
    "version_80046_bi_bo_qua": ("CREATE /*!80046 TABLE */ DATABASE evil;", "CREATE DATABASE"),
}

HARMLESS = {
    "keyword_trong_chuoi": "INSERT INTO t VALUES ('free use of gym','CREATE DATABASE x');",
    "keyword_trong_line_comment": "-- CREATE DATABASE x\nSELECT 1;",
    "keyword_trong_block_comment": "/* USE y */\nSELECT 1;",
    "identifier_backtick": "SELECT `use`, `database` FROM t;",
    "create_table": "CREATE TABLE hotels (id INT);",
    "chuoi_nhieu_dong": "INSERT INTO t VALUES ('dong1\nUSE evil;\ndong2');",
    "dashdash_nbsp_khong_la_comment": "SELECT 1 -- x;",
    "version_cao_an_lenh_nguy_hiem": "/*!99999 DROP DATABASE hotel_price_intel */;",
    "vtab_formfeed_trong_chuoi": "INSERT INTO t VALUES ('a\vb\fc');",
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_adversarial_bi_bat_o_ca_3_duong(name, tmp_path):
    sql, expected = ADVERSARIAL[name]
    path = write(tmp_path, f"{name}.sql", sql)
    assert classify_forbidden(sql, server_version=V) == expected
    assert [f[0] for f in scan_text_for_forbidden(sql, server_version=V)] == [expected]
    assert [f[0] for f in scan_file_for_forbidden(path, server_version=V)] == [expected]


@pytest.mark.parametrize("name", sorted(HARMLESS))
def test_harmless_khong_bao_gia(name, tmp_path):
    sql = HARMLESS[name]
    path = write(tmp_path, f"{name}.sql", sql)
    assert classify_forbidden(sql, server_version=V) is None
    assert scan_text_for_forbidden(sql, server_version=V) == []
    assert scan_file_for_forbidden(path, server_version=V) == []


def test_duong_text_va_file_khong_bao_gio_lech(tmp_path):
    """Ca 2 chi tach o `\\n` - `str.splitlines()` tach ca \\v/\\f nen tung la nguon lech tiem an."""
    cases = {**{k: v for k, (v, _) in ADVERSARIAL.items()}, **HARMLESS}
    for name, sql in cases.items():
        path = write(tmp_path, f"cmp_{name}.sql", sql)
        assert [f[0] for f in scan_file_for_forbidden(path, server_version=V)] == [
            f[0] for f in scan_text_for_forbidden(sql, server_version=V)], name


# ======================================================================================
# Fail-closed cho moi cho chua kiem chung
# ======================================================================================
def test_versioned_comment_khi_khong_biet_version_server_thi_fail():
    with pytest.raises(SqlScriptError, match="version server"):
        classify_forbidden("/*!40101 SET x=1 */", server_version=None)


@pytest.mark.parametrize("sql", ["/*!1234 SET x=1 */;", "/*!800450 SET x=1 */;"])
def test_version_4_hoac_6_chu_so_thi_fail(sql):
    with pytest.raises(SqlScriptError, match="chu so"):
        list(iter_statements(sql, server_version=V))


@pytest.mark.parametrize("sql", [
    "/*!40101 SET x=1 /* c */ */;", "/*!40101 SET x=1 -- c\n */;",
    "/*!40101 SET x=1 # c\n */;", "/*!40101 SET x=1 /*!40101 y */ */;",
])
def test_comment_long_trong_exec_comment_thi_fail(sql):
    with pytest.raises(SqlScriptError, match="executable comment"):
        list(iter_statements(sql, server_version=V))


def test_chu_so_unicode_khong_bi_coi_la_version():
    """`\\d` cua Python khop '٤٠١٠١'; MySQL thi khong -> day la noi dung code, khong phai version."""
    assert classify_forbidden("/*!٤٠١٠١ USE x */", server_version=V) is None


def test_chuoi_ben_trong_exec_comment_khong_lam_lac_state():
    """mysqldump THAT phat `/*!40103 SET TIME_ZONE='+00:00' */`."""
    sql = "/*!40103 SET TIME_ZONE='+00:00' */;\n/*!50503 SET NAMES utf8mb4 */;\nUSE evil;\n"
    assert texts(sql) == ["/*!40103 SET TIME_ZONE='+00:00' */", "/*!50503 SET NAMES utf8mb4 */", "USE evil"]
    assert [f[0] for f in scan_text_for_forbidden(sql, server_version=V)] == ["USE"]


def test_cham_phay_trong_exec_comment_khong_cat_statement():
    assert texts("/*!40101 SET a=1; SET b=2 */;\nSELECT 9;") == ["/*!40101 SET a=1; SET b=2 */", "SELECT 9"]


@pytest.mark.parametrize("content,match", [
    ("/*!40101 SET x=1\nSELECT 1;\n", "khong bao gio duoc dong"),
    ("SELECT 1; /* chua dong", "khong bao gio duoc dong"),
    ("SELECT 'chua dong", "khong bao gio duoc dong"),
    ("DELIMITER //\nCREATE PROCEDURE p() BEGIN END //\n", "DELIMITER"),
])
def test_file_hong_thi_fail(tmp_path, content, match):
    with pytest.raises(SqlScriptError, match=match):
        scan_file_for_forbidden(write(tmp_path, "bad.sql", content), server_version=V)


def test_byte_khong_phai_utf8_thi_fail(tmp_path):
    with pytest.raises(SqlScriptError, match="UTF-8"):
        scan_file_for_forbidden(write(tmp_path, "bin.sql", raw=b"SELECT '\xff';\n"), server_version=V)


def test_bom_dau_file(tmp_path):
    assert [f[0] for f in scan_file_for_forbidden(
        write(tmp_path, "b1.sql", raw="﻿USE evil;\n".encode()), server_version=V)] == ["USE"]
    assert scan_file_for_forbidden(
        write(tmp_path, "b2.sql", raw="﻿CREATE TABLE t (i INT);\n".encode()), server_version=V) == []


def test_backslash_cuoi_dong_escape_newline_trong_chuoi():
    sql = "INSERT INTO t VALUES ('abc\\" + "\n" + "USE evil');\nSELECT 2;"
    assert scan_text_for_forbidden(sql, server_version=V) == []
    assert texts(sql)[-1] == "SELECT 2"


def test_dong_rat_dai_va_statement_cuoi_khong_cham_phay(tmp_path):
    payload = ",".join(f"('hotel {i}; use x')" for i in range(20000))
    path = write(tmp_path, "long.sql", f"INSERT INTO t VALUES {payload};\nUSE evil")
    assert [f[0] for f in scan_file_for_forbidden(path, server_version=V)] == ["USE"]


def test_max_capture_that_su_gioi_han():
    """GPT 08 MINOR 1: ban cu append nguyen doan -> text 1.000.023 ky tu du max_capture=200."""
    lexer = SqlLexer(capture_text=True, max_capture=200, server_version=V)
    line = "INSERT INTO t VALUES ('" + "x" * 1_000_000 + "');\n"
    statement = (list(lexer.feed_line(line)) + list(lexer.finalize()))[0]
    assert len(statement.text) <= 200 and statement.truncated


def test_sanitize_setup_sql():
    assert sanitize_setup_sql("CREATE DATABASE IF NOT EXISTS x;\nUSE x;\nCREATE TABLE t (id INT);") == [
        "CREATE TABLE t (id INT)"]
    with pytest.raises(SqlScriptError, match="DROP DATABASE"):
        sanitize_setup_sql("DROP DATABASE x;\nCREATE TABLE t (id INT);")
    with pytest.raises(SqlScriptError, match="rong"):
        sanitize_setup_sql("USE x;")


def test_mysql_version_id():
    assert mysql_version_id("8.0.45") == 80045
    assert mysql_version_id("8.0.46-log") == 80046
    with pytest.raises(SqlScriptError):
        mysql_version_id("khong phai version")


# ======================================================================================
# Preflight: allowlist dang mysqldump (token lech -> khong khop -> FAIL)
# ======================================================================================
DUMP_OK = (
    "-- MySQL dump 10.13  Distrib 8.0.45\n"
    "/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;\n"
    "/*!40103 SET TIME_ZONE='+00:00' */;\n"
    "DROP TABLE IF EXISTS `hotels`;\n"
    "/*!40101 SET @saved_cs_client     = @@character_set_client */;\n"
    "CREATE TABLE `hotels` (\n  `hotel_id` varchar(255) NOT NULL,\n  PRIMARY KEY (`hotel_id`)\n) ENGINE=InnoDB;\n"
    "/*!40101 SET character_set_client = @saved_cs_client */;\n"
    "LOCK TABLES `hotels` WRITE;\n"
    "/*!40000 ALTER TABLE `hotels` DISABLE KEYS */;\n"
    "INSERT INTO `hotels` (`hotel_id`) VALUES ('a'),('b; USE x');\n"
    "/*!40000 ALTER TABLE `hotels` ENABLE KEYS */;\n"
    "UNLOCK TABLES;\n"
    "/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;\n"
)


def test_preflight_dump_chuan_mysqldump_qua(tmp_path):
    report = preflight_dump(write(tmp_path, "ok.sql", DUMP_OK), server_version=V)
    assert report["findings"] == [] and report["statements"] == 12


@pytest.mark.parametrize("statement,expected", [
    ("SET SQL_MODE='NO_BACKSLASH_ESCAPES';", "SET ngoai whitelist"),
    ("/*!40101 SET SQL_MODE='ANSI_QUOTES' */;", "SET ngoai whitelist"),
    ("/*!40101 SET @OLD_SQL_MODE='NO_BACKSLASH_ESCAPES' */;", "SET ngoai whitelist"),
    ("SET NAMES gbk;", "SET ngoai whitelist"),
    ("SET GLOBAL max_connections=1;", "SET ngoai whitelist"),
    ("DROP TABLE IF EXISTS `hotel_price_intel`.`hotels`;", "tien to database"),
    ("INSERT INTO `hotel_price_intel`.`hotels` VALUES (1);", "tien to database"),
    ("LOCK TABLES `hotel_price_intel`.`hotels` WRITE;", "tien to database"),
    ("DROP TABLE `hotels`;", "DROP ngoai dang"),
    ("INSERT INTO hotels VALUES (1);", "ten bang"),
    ("PREPARE s FROM @x;", "ngoai allowlist"),
    ("CALL p();", "ngoai allowlist"),
    ("RENAME TABLE `a` TO `b`;", "ngoai allowlist"),
    ("DELETE FROM `hotels`;", "ngoai allowlist"),
    ("CREATE /*!99999 TABLE */ DATABASE evil;", "CREATE DATABASE"),
    ("--\f comment\nCREATE DATABASE evil;", "CREATE DATABASE"),
])
def test_preflight_bat_statement_ngoai_dang_mysqldump(tmp_path, statement, expected):
    report = preflight_dump(write(tmp_path, "bad.sql", DUMP_OK + statement + "\n"), server_version=V)
    assert len(report["findings"]) == 1, report["findings"]
    assert expected in report["findings"][0][0]


# ======================================================================================
# Preflight: tap bang du kien (GPT review 10, N2) - dump chi duoc gom DUNG 4 bang core
# ======================================================================================
def _table_block(table: str) -> str:
    return (f"DROP TABLE IF EXISTS `{table}`;\n"
            f"CREATE TABLE `{table}` (\n  `id` int NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB;\n"
            f"LOCK TABLES `{table}` WRITE;\n"
            f"INSERT INTO `{table}` (`id`) VALUES (1);\n"
            "UNLOCK TABLES;\n")


def _core_dump(tables=CORE_TABLES, extra: str = "") -> str:
    return "".join(_table_block(table) for table in tables) + extra


def test_preflight_du_ddl_4_bang_core_thi_qua(tmp_path):
    report = preflight_dump(write(tmp_path, "core.sql", _core_dump()), server_version=V, expected_tables=CORE_TABLES)
    assert report["findings"] == []
    assert report["tables_created"] == report["expected_tables"] == sorted(CORE_TABLES)


def test_preflight_thieu_ddl_1_bang_core_thi_fail(tmp_path):
    report = preflight_dump(write(tmp_path, "m.sql", _core_dump(CORE_TABLES[:-1])), server_version=V,
                            expected_tables=CORE_TABLES)
    assert [finding[0] for finding in report["findings"]] == [f"MISSING_TABLE_DDL: {CORE_TABLES[-1]}"]


def test_preflight_insert_ma_khong_co_ddl_thi_van_fail(tmp_path):
    """Dump cu chi co INSERT: bang co bi 'cham' nhung khong co CREATE -> van thieu DDL."""
    report = preflight_dump(write(tmp_path, "i.sql", "INSERT INTO `hotels` (`id`) VALUES (1);\n"), server_version=V,
                            expected_tables=CORE_TABLES)
    assert sorted(finding[0] for finding in report["findings"]) == sorted(
        f"MISSING_TABLE_DDL: {table}" for table in CORE_TABLES)


@pytest.mark.parametrize("extra", [
    "INSERT INTO `hotel_reference_rooms` (`id`) VALUES (1);\n",
    "DROP TABLE IF EXISTS `hotel_reference_rooms`;\n",
    "LOCK TABLES `hotel_link_health` WRITE;\n",
    "CREATE TABLE `bang_la` (\n  `id` int\n) ENGINE=InnoDB;\n",
])
def test_preflight_tac_dong_bang_ngoai_core_thi_fail(tmp_path, extra):
    report = preflight_dump(write(tmp_path, "u.sql", _core_dump(extra=extra)), server_version=V,
                            expected_tables=CORE_TABLES)
    assert len(report["findings"]) == 1, report["findings"]
    assert report["findings"][0][0].startswith("UNEXPECTED_TABLE: ")


def test_preflight_khong_truyen_expected_tables_thi_khong_kiem_tap_bang(tmp_path):
    report = preflight_dump(write(tmp_path, "n.sql", DUMP_OK), server_version=V)
    assert report["findings"] == [] and report["expected_tables"] is None
