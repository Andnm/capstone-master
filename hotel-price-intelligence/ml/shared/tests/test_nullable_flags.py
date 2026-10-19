"""Co `free_cancellation` / `breakfast_included` cua builder: NULL phai la `<NA>` (chua biet), KHONG phai True/False (GPT file 46 muc 3a).

THUAN: khong import `dataset_builder` (vi `dataset_builder.env` tu nap `backend/.env` luc import). Ham `to_nullable_boolean` va `_to_float` cua encoder duoc trich bang AST tu ma nguon that.
Kiem tren moi dtype ma `pd.DataFrame(rows)` co the suy ra tu cot TINYINT cua MySQL, doi chung voi cach cu (lambda `bool(v)`) de ghi lai loi."""
from __future__ import annotations

import ast
import decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ML = Path(__file__).resolve().parents[1]


def _extract(relative: str, names: set[str], extra_globals: dict | None = None) -> dict:
    base = ML.parent / "regression" if relative.startswith("training/") else ML
    source = (base / relative).read_text(encoding="utf-8")
    nodes = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {n.name for n in nodes} == names, f"thieu {names - {n.name for n in nodes}} trong {relative}"
    namespace: dict = {"pd": pd, "np": np, **(extra_globals or {})}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), relative, "exec"), namespace)   # noqa: S102
    return namespace


FEATURES = _extract("dataset_builder/features.py", {"to_nullable_boolean"})
to_nullable_boolean = FEATURES["to_nullable_boolean"]
ENCODER = _extract("training/encoder.py", {"_to_float"})
_to_float = ENCODER["_to_float"]


def old_encoding(series: pd.Series) -> pd.Series:
    """Cach cu cua builder (loi): `map(lambda v: None if v is None else bool(v)).astype('boolean')`."""
    return series.map(lambda v: None if v is None else bool(v)).astype("boolean")


NA = pd.NA
CASES = {                                                # ten -> (series, ket qua mong doi)
    "int64 0/1": (pd.Series([1, 0, 1]), [True, False, True]),
    "float64 co NaN (cot TINYINT co None -> DataFrame suy float64)": (pd.Series([1.0, 0.0, np.nan, 1.0]), [True, False, NA, True]),
    "object int + None": (pd.Series([1, 0, None], dtype=object), [True, False, NA]),
    "object bool + None": (pd.Series([True, False, None], dtype=object), [True, False, NA]),
    "Int64 nullable": (pd.Series([1, 0, None], dtype="Int64"), [True, False, NA]),
    "boolean nullable": (pd.Series([True, False, None], dtype="boolean"), [True, False, NA]),
    "bool": (pd.Series([True, False]), [True, False]),
    "object toan None": (pd.Series([None, None], dtype=object), [NA, NA]),
    "float toan NaN": (pd.Series([np.nan, np.nan]), [NA, NA]),
    "object lan pd.NA": (pd.Series([1, NA, 0], dtype=object), [True, NA, False]),
}


@pytest.mark.parametrize("name", list(CASES))
def test_truth_table_null_stays_na_and_known_values_map_to_booleans_on_every_inferred_dtype(name):
    series, expected = CASES[name]
    out = to_nullable_boolean(series, "free_cancellation")
    assert str(out.dtype) == "boolean" and list(out.index) == list(series.index)
    assert [("NA" if v is NA else bool(v)) for v in out.tolist()] == [("NA" if v is NA else bool(v)) for v in expected]


def scalar_reference(series: pd.Series) -> list:
    """Phuong an B (GPT file 46 muc 3a): tung phan tu — NULL (None / NaN / pd.NA) -> NA; 0/1/False/True (ke ca np.*, 0.0/1.0) -> bool; con lai -> ValueError."""
    out = []
    for value in series.tolist():
        if value is None or value is pd.NA or (isinstance(value, float) and value != value):
            out.append(pd.NA)
        elif isinstance(value, (bool, np.bool_)):
            out.append(bool(value))
        elif isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool) and value in (0, 1):
            out.append(bool(value))
        else:
            raise ValueError(f"ngoai mien: {value!r}")
    return out


COMPARISON = {                                                                       # ten -> series (hop le va khong hop le)
    "int64": pd.Series([1, 0, 1]), "uint8": pd.Series([0, 1, 1], dtype="uint8"), "float64+NaN": pd.Series([1.0, np.nan, 0.0]), "-0.0": pd.Series([-0.0, 1.0]),
    "object int+None": pd.Series([1, 0, None], dtype=object), "object numpy ints": pd.Series([1, 0, np.int64(1)], dtype=object), "object bool+None": pd.Series([True, np.bool_(False), None], dtype=object),
    "object 1.0+None": pd.Series([1.0, None], dtype=object), "object NA/nan": pd.Series([pd.NA, float("nan"), 0, 1], dtype=object), "UInt8": pd.Series([0, 1, None], dtype="UInt8"),
    "Float64": pd.Series([0.0, 1.0, None], dtype="Float64"), "boolean": pd.Series([True, None, False], dtype="boolean"), "empty": pd.Series([], dtype=object),
    "int8 with 2": pd.Series([0, 1, 2], dtype="int8"), "float 0.5": pd.Series([0.5, 1.0]), "inf": pd.Series([1.0, np.inf]), "-1": pd.Series([-1, 0]), "strings": pd.Series(["True", "False"]),
    "object bool+str": pd.Series([True, "x"], dtype=object), "Decimal": pd.Series([decimal.Decimal(1), None], dtype=object), "mixed int+bool object": pd.Series([1, None, True], dtype=object),
}
SCALAR_ONLY_ACCEPTS = {"mixed int+bool object"}                                       # duy nhat lop chenh lech: cast truc tiep tu choi (to tieng), helper theo phan tu se chap nhan


@pytest.mark.parametrize("name", list(COMPARISON))
def test_direct_nullable_cast_versus_scalar_reference_agree_on_every_inferred_dtype_and_only_differ_by_being_stricter(name):
    series = COMPARISON[name]
    try:
        scalar = scalar_reference(series)
    except ValueError:
        scalar = None
    try:
        direct = to_nullable_boolean(series, "free_cancellation").tolist()
    except ValueError:
        direct = None
    if direct is not None:
        assert scalar is not None, f"cast truc tiep chap nhan thu ma phuong an theo phan tu tu choi: {name}"          # khong bao gio long hon
        assert [("NA" if v is NA else bool(v)) for v in direct] == [("NA" if v is NA else bool(v)) for v in scalar], name
    else:
        assert scalar is None or name in SCALAR_ONLY_ACCEPTS, f"cast truc tiep tu choi thu hop le: {name}"             # chi khac o lop da khai bao, va tu choi la LOUD (ValueError), khong im lang


def test_the_only_class_where_the_scalar_reference_is_more_permissive_is_declared_and_still_fails_loudly_in_the_builder():
    assert scalar_reference(COMPARISON["mixed int+bool object"]) == [True, NA, True]
    with pytest.raises(ValueError, match="free_cancellation"):
        to_nullable_boolean(COMPARISON["mixed int+bool object"], "free_cancellation")


def test_known_false_is_never_missing_and_null_is_never_true():
    values = to_nullable_boolean(pd.Series([0.0, 1.0, np.nan]), "free_cancellation").tolist()
    assert values[0] is False and values[1] is True and values[2] is pd.NA


def test_old_encoding_reproduced_the_bug_and_the_new_one_does_not():
    series = pd.Series([1, 0, None, 1])                                                 # DataFrame suy float64 [1.0, 0.0, NaN, 1.0]
    assert series.dtype == "float64"
    assert old_encoding(series).tolist() == [True, False, True, True]                  # NULL -> True (loi cu)
    assert to_nullable_boolean(series, "free_cancellation").isna().tolist() == [False, False, True, False]   # NULL -> <NA>


@pytest.mark.parametrize("values", [[1, 0, 2], [1.0, 0.5], ["1", "0"], ["yes", None], [[1], [0]], [1.0, np.inf], [decimal.Decimal(1), decimal.Decimal(0), None], [-1, 0]])
def test_values_outside_null_zero_one_are_rejected_with_the_column_name(values):
    with pytest.raises(ValueError, match="free_cancellation"):
        to_nullable_boolean(pd.Series(values, dtype=object if any(isinstance(v, (str, list, decimal.Decimal)) for v in values) else None), "free_cancellation")


def test_downstream_float_encoding_turns_na_into_nan_not_one():
    flags = to_nullable_boolean(pd.Series([1.0, 0.0, np.nan]), "breakfast_included")
    encoded = _to_float(flags)
    assert encoded.dtype == np.float64 and encoded[0] == 1.0 and encoded[1] == 0.0 and np.isnan(encoded[2])         # RF/HGB/XGBoost nhan NaN la thieu; Ridge impute + indicator


def test_downstream_old_encoding_would_have_fed_one_for_null():
    old = _to_float(old_encoding(pd.Series([1.0, 0.0, np.nan])))
    assert old.tolist() == [1.0, 0.0, 1.0]                                              # chung minh phep thu nhay: cach cu dua 1.0 cho NULL


def test_helper_text_in_builder_calls_astype_boolean_not_bool_of_nan():
    source = (ML / "dataset_builder/features.py").read_text(encoding="utf-8")
    assert 'to_nullable_boolean(df[column], column)' in source and 'None if v is None else bool(v)' not in source.split("def to_nullable_boolean", 1)[1].split("def lead_time_bucket", 1)[1]
