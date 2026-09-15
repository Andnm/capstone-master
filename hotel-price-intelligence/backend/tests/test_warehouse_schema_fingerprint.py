"""So sanh schema theo cau truc - pure (fingerprint dung san, khong can MySQL).

Ca quan trong nhat: khac biet THAT giua 2 nguon hien tai chi la TEN constraint
(`fk_runs_retry_of` tren local vs `crawl_runs_ibfk_1` tren VPS). Khac biet do PHAI la NOTE, khong
duoc chan import - nhung khac `COLUMN_TYPE`, khac FK rule, khac CHECK thi PHAI FAIL.
"""
from app.warehouse.schema_fingerprint import (
    ColumnFingerprint,
    ForeignKeyFingerprint,
    KeyFingerprint,
    TableFingerprint,
    compare_table,
)


def column(name, column_type="int", ordinal=1, nullable="NO", default=None, extra=""):
    return ColumnFingerprint(
        ordinal_position=ordinal, column_name=name, column_type=column_type, is_nullable=nullable,
        column_default=default, extra=extra, generation_expression=None,
        character_set_name=None, collation_name=None,
    )


def fingerprint(*, columns=None, keys=None, fks=None, checks=(), names=None, engine="InnoDB"):
    return TableFingerprint(
        table_name="crawl_runs",
        engine=engine,
        table_collation="utf8mb4_unicode_ci",
        columns=tuple(columns if columns is not None else (column("id"),)),
        keys=frozenset(keys if keys is not None else {
            KeyFingerprint(is_unique=True, is_primary=True, index_type="BTREE", columns=(("id", None),))
        }),
        foreign_keys=frozenset(fks if fks is not None else set()),
        checks=frozenset(checks),
        cosmetic_names=names or {"indexes": ("PRIMARY",), "foreign_keys": (), "checks": ()},
    )


def test_giong_het_thi_pass():
    result = compare_table(fingerprint(), fingerprint(), label="vps")
    assert result.ok and not result.notes


def test_khac_ten_constraint_chi_la_note():
    """Dung ca that: migration 20260808 doi ten FK tren local, setup.sql baseline thi chua."""
    fk = ForeignKeyFingerprint(
        columns=("retry_of_run_id",), referenced_table="crawl_runs", referenced_columns=("id",),
        update_rule="NO ACTION", delete_rule="SET NULL",
    )
    expected = fingerprint(fks={fk}, names={"indexes": ("PRIMARY",), "foreign_keys": ("crawl_runs_ibfk_1",), "checks": ()})
    actual = fingerprint(fks={fk}, names={"indexes": ("PRIMARY",), "foreign_keys": ("fk_runs_retry_of",), "checks": ()})
    result = compare_table(expected, actual, label="local_primary")
    assert result.ok, result.fatal
    assert any("cosmetic" in note for note in result.notes)


def test_varchar_khac_do_dai_thi_fail():
    """`DATA_TYPE='varchar'` giong nhau van co the la VARCHAR(20) vs VARCHAR(500) - phai bat duoc."""
    expected = fingerprint(columns=(column("name", "varchar(500)"),))
    actual = fingerprint(columns=(column("name", "varchar(20)"),))
    result = compare_table(expected, actual, label="vps")
    assert not result.ok
    assert any("column_type" in item for item in result.fatal)


def test_decimal_khac_precision_thi_fail():
    expected = fingerprint(columns=(column("price", "decimal(15,2)"),))
    actual = fingerprint(columns=(column("price", "decimal(10,2)"),))
    assert not compare_table(expected, actual, label="vps").ok


def test_enum_thieu_gia_tri_thi_fail():
    expected = fingerprint(columns=(column("status", "enum('a','b','c')"),))
    actual = fingerprint(columns=(column("status", "enum('a','b')"),))
    assert not compare_table(expected, actual, label="vps").ok


def test_cot_thieu_va_cot_thua_deu_fail():
    expected = fingerprint(columns=(column("id"), column("extra_col", ordinal=2)))
    actual = fingerprint(columns=(column("id"),))
    missing = compare_table(expected, actual, label="vps")
    assert any("THIEU cot" in item for item in missing.fatal)

    added = compare_table(actual, expected, label="vps")
    assert any("cot THUA" in item for item in added.fatal)


def test_doi_thu_tu_cot_thi_fail():
    expected = fingerprint(columns=(column("a", ordinal=1), column("b", ordinal=2)))
    actual = fingerprint(columns=(column("a", ordinal=2), column("b", ordinal=1)))
    result = compare_table(expected, actual, label="vps")
    assert any("ordinal_position" in item for item in result.fatal)


def test_nullable_khac_thi_fail():
    expected = fingerprint(columns=(column("x", nullable="NO"),))
    actual = fingerprint(columns=(column("x", nullable="YES"),))
    assert any("is_nullable" in item for item in compare_table(expected, actual, label="vps").fatal)


def test_delete_rule_khac_thi_fail():
    base = dict(columns=("a",), referenced_table="t", referenced_columns=("id",), update_rule="NO ACTION")
    expected = fingerprint(fks={ForeignKeyFingerprint(delete_rule="CASCADE", **base)})
    actual = fingerprint(fks={ForeignKeyFingerprint(delete_rule="SET NULL", **base)})
    result = compare_table(expected, actual, label="vps")
    assert not result.ok
    assert any("foreign key" in item for item in result.fatal)


def test_thieu_unique_key_thi_fail():
    unique = KeyFingerprint(is_unique=True, is_primary=False, index_type="BTREE", columns=(("a", None),))
    expected = fingerprint(keys={unique})
    actual = fingerprint(keys=set())
    assert any("key/index" in item for item in compare_table(expected, actual, label="vps").fatal)


def test_index_prefix_length_khac_thi_fail():
    expected = fingerprint(keys={KeyFingerprint(False, False, "BTREE", (("link", 255),))})
    actual = fingerprint(keys={KeyFingerprint(False, False, "BTREE", (("link", 100),))})
    assert not compare_table(expected, actual, label="vps").ok


def test_check_khac_thi_fail_nhung_khoang_trang_thi_khong():
    expected = fingerprint(checks={"(`x` > 0)"})
    same_but_spaced = fingerprint(checks={"( x > 0 )"})
    different = fingerprint(checks={"(x > 1)"})
    # _normalize_expression bo backtick + gom khoang trang, nhung '( x > 0 )' van khac '(x > 0)'
    # o dau ngoac - day la ca that duy nhat con lai, nen so sau khi normalize cua ca 2 phia.
    assert not compare_table(expected, different, label="vps").ok
    assert compare_table(expected, expected, label="vps").ok
    assert not compare_table(expected, same_but_spaced, label="vps").ok


def test_engine_va_collation_khac_thi_fail():
    assert not compare_table(fingerprint(), fingerprint(engine="MyISAM"), label="vps").ok
