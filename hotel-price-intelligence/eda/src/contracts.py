"""Schema/kieu du lieu assertion tap trung (EDA_CURATED_PLAN.md muc 5 quy tac 11-12).

Ly do co file nay: MySQL luu BOOLEAN nhu TINYINT(1); `mysql-connector-python` tra ve Python `int` (0/1),
KHONG phai `bool`. Du an da tung bi loi nay lam sai 100% `rate_plan_key` khi hash truc tiep gia tri int
(xem memory `project_mysql_tinyint_bool_hash_pitfall`). Quy tac: KHONG rai `.astype(bool)` tuy notebook
cell - moi cot BOOLEAN phai di qua `coerce_boolean_columns()`/`coerce_boolean_series()` duy nhat o day,
va gia tri ngoai {0, 1, True, False} (hoac NULL neu cot nullable) phai FAIL, khong am tham ep.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

# Dang ky DUNG cot BOOLEAN cua tung bang warehouse (doi chieu truc tiep tu app/database/setup.sql va
# app/warehouse/etl_ddl.py, khong doan). `nullable=True` <=> cot cho phep NULL trong DDL.
BooleanColumn = tuple[str, bool]  # (ten cot, nullable)

BOOLEAN_SCHEMA: Mapping[str, tuple[BooleanColumn, ...]] = {
    "price_observations": (
        ("is_sold_out", False),
        ("is_anomaly", False),
        # is_reference_room CO trong schema nhung BI CAM dung trong EDA (muc 5 quy tac 12 - da reset
        # ve FALSE luc import, khong phan anh reference cua warehouse). Van dang ky o day de neu co
        # ai lo doc no, contract van validate dung gia tri THAT (0/False) thay vi crash kieu la.
        ("is_reference_room", False),
        ("breakfast_included", True),
        ("free_cancellation", True),
        ("price_includes_tax", True),
    ),
    "etl_run_map": (
        ("include_reference", False), ("include_eda_raw", False),
        ("include_eda_main", False), ("include_training", False),
    ),
    "etl_item_map": (
        ("include_reference", False), ("include_eda_raw", False),
        ("include_eda_main", False), ("include_training", False),
    ),
    "crawl_runs": (("save_artifacts", False),),
    "vn_holidays": (("is_tet", False),),
}

# CLAUDE.md muc 6 rule 3 / EDA_CURATED_PLAN.md muc 5.12: 4 cot reference-operational cua
# price_observations da bi reset luc import, KHONG phai source of truth cho reference EDA.
FORBIDDEN_REFERENCE_COLUMNS = (
    "is_reference_room", "reference_definition_id", "reference_match_status", "reference_match_score",
)

_VALID_TRUE = {1, True, np.True_, "1", "true", "True"}
_VALID_FALSE = {0, False, np.False_, "0", "false", "False"}


@dataclass(frozen=True)
class BooleanContractError(ValueError):
    table: str
    column: str
    bad_values: tuple
    row_count: int

    def __str__(self) -> str:  # noqa: D105
        return (
            f"cot BOOLEAN {self.table}.{self.column} co {self.row_count} dong gia tri ngoai hop dong "
            f"{{0,1,True,False}}: {self.bad_values[:10]}{' ...' if len(self.bad_values) > 10 else ''}. "
            f"Day la du lieu that hoac loi query, KHONG duoc am tham ep kieu."
        )


def coerce_boolean_series(series: "pd.Series", *, nullable: bool, table: str = "?", column: str = "?") -> "pd.Series":
    """1 cot -> pandas nullable boolean dtype (`boolean`), FAIL neu co gia tri ngoai hop dong.

    Dung dtype `boolean` (khong phai `bool` tho) de giu duoc NULL that su cho cot nullable, thay vi
    ep NULL thanh False mot cach ngam. Idempotent: goi lai tren cot da coerce (dtype `boolean`, mang
    `True/False/pd.NA`) tra ve nguyen ban, khong crash - dung `pd.isna()` chu khong so sanh `is None`
    tay, vi `pd.NA`/`NaT` khong phai `None` va so sanh truc tiep voi no nem `TypeError`.
    """
    is_na = series.map(pd.isna)
    if is_na.any() and not nullable:
        bad_rows = series[is_na]
        raise BooleanContractError(table, column, tuple(bad_rows.index.tolist()), int(is_na.sum()))

    non_null = series[~is_na]
    is_true = non_null.isin(_VALID_TRUE)
    is_false = non_null.isin(_VALID_FALSE)
    invalid = non_null[~(is_true | is_false)]
    if len(invalid):
        raise BooleanContractError(table, column, tuple(invalid.unique().tolist()), len(invalid))

    result = pd.Series(pd.NA, index=series.index, dtype="boolean")
    result.loc[is_true.index[is_true]] = True
    result.loc[is_false.index[is_false]] = False
    return result


def coerce_boolean_columns(df: "pd.DataFrame", table: str) -> "pd.DataFrame":
    """Coerce moi cot BOOLEAN da dang ky cua `table` co mat trong `df`. Cot khong dang ky giu nguyen.

    Tra ve DataFrame MOI (khong sua in-place) - notebook/ham goi phai gan lai bien.
    """
    if table not in BOOLEAN_SCHEMA:
        raise KeyError(f"bang {table!r} chua dang ky trong BOOLEAN_SCHEMA - them vao trUOc khi dung.")
    out = df.copy()
    for column, nullable in BOOLEAN_SCHEMA[table]:
        if column in out.columns:
            out[column] = coerce_boolean_series(out[column], nullable=nullable, table=table, column=column)
    return out


def assert_no_forbidden_reference_columns(df: "pd.DataFrame", *, context: str) -> None:
    """Chan cung: neu 1 DataFrame published (bang/figure) vo tinh mang theo cot reference bi cam."""
    present = [c for c in FORBIDDEN_REFERENCE_COLUMNS if c in df.columns]
    if present:
        raise ValueError(
            f"{context}: DataFrame chua cot reference-operational BI CAM {present} - day la cot da bi "
            f"reset luc import warehouse, khong phai source of truth (muc 5 quy tac 12)."
        )


def assert_columns_present(df: "pd.DataFrame", required: tuple[str, ...], *, context: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{context}: thieu cot bat buoc {missing}. Cot hien co: {list(df.columns)}")


def assert_no_null(df: "pd.DataFrame", columns: tuple[str, ...], *, context: str) -> None:
    for column in columns:
        n = int(df[column].isna().sum())
        if n:
            raise ValueError(f"{context}: cot {column!r} co {n} gia tri NULL nhung duoc khai la NOT NULL.")
