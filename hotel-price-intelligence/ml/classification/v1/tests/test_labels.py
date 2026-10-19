"""Hop dong nhan (LABEL_CONTRACT): bien +-2%, phep tinh (y-c)/c, ma hoa lop, validator fail-closed. Tu viet, doc lap voi builder."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cls_training.labels import (LABELS, CLASS_INDEX, LabelContractError, assert_all_classes, class_index, direction_from_pct, pct_change, usable_mask,
                                 validate_label_contract)


def test_class_order_is_fixed():
    assert LABELS == ("down", "stable", "up") and CLASS_INDEX == {"down": 0, "stable": 1, "up": 2}


def test_boundary_is_strict_and_exact_two_percent_is_stable():
    pct = np.array([0.02, -0.02, 0.0200001, -0.0200001, 0.0, 0.5, -0.5])
    assert direction_from_pct(pct).tolist() == ["stable", "stable", "up", "down", "stable", "up", "down"]


def test_builder_arithmetic_vs_naive_ratio_differs_exactly_at_boundary():
    y, c = 2067996.0, 2110200.0                                   # vi du thuc tu dataset: (y-c)/c = -0.02 chinh xac
    assert pct_change(y, c) == -0.02
    assert direction_from_pct(pct_change(y, c)).tolist() == ["stable"]
    assert direction_from_pct(y / c - 1.0).tolist() == ["down"]    # phep SAI: 56 hang h1 / 41 hang h3 bi doi lop
    y2, c2 = 594864.0, 583200.0
    assert direction_from_pct(pct_change(y2, c2)).tolist() == ["stable"] and direction_from_pct(y2 / c2 - 1.0).tolist() == ["up"]


def test_non_finite_values_raise_unless_momentum_policy():
    with pytest.raises(LabelContractError):
        direction_from_pct(np.array([0.1, np.nan]))
    assert direction_from_pct(np.array([0.1, np.nan]), nan_policy="stable").tolist() == ["up", "stable"]
    with pytest.raises(LabelContractError):
        direction_from_pct(np.array([np.inf]))


def test_class_index_and_unknown_labels():
    assert class_index(["up", "down", "stable"]).tolist() == [2, 0, 1]
    with pytest.raises(LabelContractError):
        class_index(["up", "sideways"])
    with pytest.raises(LabelContractError):
        class_index(["up", None])


def _frame(n=12, horizon=1):
    rng = np.random.default_rng(0)
    c = rng.uniform(1e5, 5e6, n).round(0)
    factor = np.array([1.0, 1.05, 0.9, 1.02, 0.98, 1.5] * (n // 6 + 1))[:n]
    y = (c * factor).round(0)
    df = pd.DataFrame({"split": ["train"] * (n // 2) + ["validation"] * (n - n // 2), "current_price": c, f"y_price_h{horizon}": y})
    df[f"label_usable_h{horizon}"] = True
    df[f"y_direction_h{horizon}"] = direction_from_pct(pct_change(y, c))
    return df


def test_validator_happy_path_reports_counts():
    df = _frame()
    report = validate_label_contract(df, 1)
    assert report["n_usable"] == 12 and set(report["class_counts_by_split"]["train"]) <= set(LABELS)


def test_validator_fails_when_usable_row_has_null_or_unknown_direction():
    df = _frame()
    df.loc[3, "y_direction_h1"] = None
    with pytest.raises(LabelContractError, match="ngoai"):
        validate_label_contract(df, 1)
    df = _frame()
    df.loc[3, "y_direction_h1"] = "sideways"
    with pytest.raises(LabelContractError):
        validate_label_contract(df, 1)


@pytest.mark.parametrize("bad", [0.0, -5.0, np.nan, np.inf])
def test_validator_fails_when_usable_row_has_invalid_price(bad):
    df = _frame()
    df.loc[2, "y_price_h1"] = bad
    with pytest.raises(LabelContractError, match="khong huu han|<= 0"):
        validate_label_contract(df, 1)
    df = _frame()
    df.loc[2, "current_price"] = bad
    with pytest.raises(LabelContractError):
        validate_label_contract(df, 1)


def test_validator_fails_when_stored_label_differs_from_recomputed_even_for_one_row():
    df = _frame()
    i = int(df.index[df["y_direction_h1"] == "stable"][0])
    df.loc[i, "y_direction_h1"] = "up"
    with pytest.raises(LabelContractError, match="khac phep tinh"):
        validate_label_contract(df, 1)


def test_unusable_rows_are_ignored_and_not_forced_to_null():
    df = _frame()
    df.loc[4, "label_usable_h1"] = False
    df.loc[4, "y_price_h1"] = np.nan                       # unusable: gia invalid khong lam fail
    df.loc[4, "y_direction_h1"] = "up"                      # va nhan khong bi ep null
    report = validate_label_contract(df, 1)
    assert report["n_usable"] == 11
    assert usable_mask(df, 1).sum() == 11


def test_missing_columns_fail():
    with pytest.raises(LabelContractError, match="thieu cot"):
        validate_label_contract(_frame().drop(columns=["current_price"]), 1)


def test_naive_arithmetic_rows_are_counted_for_report():
    df = _frame(6)
    df.loc[0, ["current_price", "y_price_h1"]] = [2110200.0, 2067996.0]
    df.loc[0, "y_direction_h1"] = "stable"
    assert validate_label_contract(df, 1)["boundary_mass"]["rows_changed_by_naive_ratio_arithmetic"] >= 1


def test_assert_all_classes():
    assert_all_classes(np.array([0, 1, 2, 1]), where="x")
    with pytest.raises(LabelContractError, match="thieu lop"):
        assert_all_classes(np.array([1, 1, 2]), where="x")


@pytest.mark.parametrize("bad", ["False", "True", "yes", 2, -1, 0.5])
def test_usable_mask_rejects_non_boolean_values(bad):
    df = pd.DataFrame({"label_usable_h1": pd.Series([True, False, bad], dtype=object)})
    with pytest.raises(LabelContractError, match="khong phai bool"):
        usable_mask(df, 1)


def test_usable_mask_accepts_bool_zero_one_and_na():
    df = pd.DataFrame({"label_usable_h1": pd.Series([True, False, 1, 0, None], dtype=object)})
    assert usable_mask(df, 1).tolist() == [True, False, True, False, False]
    df2 = pd.DataFrame({"label_usable_h1": pd.array([True, False, None], dtype="boolean")})
    assert usable_mask(df2, 1).tolist() == [True, False, False]
