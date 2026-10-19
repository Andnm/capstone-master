"""Ma tran feature fold-local, tap feature A1/A2, boolean nullable, domain co dinh."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cls_training import features as F
from cls_training.upstream.ratio import RATIO_BLOCK_COLUMNS

from synth import SYNTH_COLUMNS, make_frame, synth_dictionary


def test_feature_sets_a2_drops_history_and_mode_a1_adds_ratio_block():
    sets = F.feature_sets(SYNTH_COLUMNS, synth_dictionary())
    assert sets["base36"] == SYNTH_COLUMNS
    assert sets["a1_ratio"] == SYNTH_COLUMNS + list(RATIO_BLOCK_COLUMNS)
    assert "inference_mode" not in sets["a2_no_history"] and "price_velocity" not in sets["a2_no_history"] and "history_observation_count" not in sets["a2_no_history"]
    assert set(sets["a2_no_history"]) == {"city", "free_cancellation", "current_price", "lead_time", "lead_time_bucket", "is_weekend"}


def test_feature_sets_reject_unknown_column_and_dictionary_without_history():
    with pytest.raises(F.FeatureError, match="khong co trong data_dictionary"):
        F.feature_sets(SYNTH_COLUMNS + ["ghost"], synth_dictionary())
    d = synth_dictionary()
    d.loc[d["group"].isin(["history", "mode"]), "group"] = "calendar"
    with pytest.raises(F.FeatureError, match="A2 phai bo"):
        F.feature_sets(SYNTH_COLUMNS, d)


def test_real_dataset_feature_sets_have_36_36plus6_21(loaded_h1):
    from cls_training.upstream.schema import read_dictionary
    sets = F.feature_sets(loaded_h1.features, read_dictionary(loaded_h1.dataset_dir))
    assert (len(sets["base36"]), len(sets["a1_ratio"]), len(sets["a2_no_history"])) == (36, 42, 21)
    assert not {"inference_mode", "price_velocity", "history_observation_count", "price_lag_1"} & set(sets["a2_no_history"])


def test_prepare_frame_adds_ratio_block_only_for_a1():
    frame, _ = make_frame(50)
    assert not set(RATIO_BLOCK_COLUMNS) & set(F.prepare_frame(frame, "base36").columns)
    a1 = F.prepare_frame(frame, "a1_ratio")
    assert set(RATIO_BLOCK_COLUMNS) <= set(a1.columns) and np.isfinite(a1["log_current"]).all()
    with pytest.raises(F.FeatureError):
        F.prepare_frame(frame, "bogus")


def test_tree_matrix_preserves_nan_and_never_turns_missing_boolean_into_true():
    frame, _ = make_frame(300, seed=3)
    m = F.TreeMatrix(SYNTH_COLUMNS).fit(frame)
    x = m.transform(frame)
    j = SYNTH_COLUMNS.index("free_cancellation")
    missing = frame["free_cancellation"].isna().to_numpy()
    assert missing.any() and np.isnan(x[missing, j]).all() and set(np.unique(x[~missing, j])) <= {0.0, 1.0}
    assert np.isnan(x[:, SYNTH_COLUMNS.index("price_velocity")]).any()
    assert x.shape == (300, len(SYNTH_COLUMNS)) and m.names == SYNTH_COLUMNS


def test_categorical_codes_follow_fixed_domain_not_data_order():
    frame, _ = make_frame(40)
    only = frame[frame["city"] == "Phú Quốc"].head(5)
    code = F.TreeMatrix(["city"]).fit(only).transform(only)
    assert set(code[:, 0]) == {4.0}                                  # Phu Quoc la phan tu cuoi cua domain co dinh, du la gia tri duy nhat trong du lieu


def test_unknown_category_and_infinity_fail_closed():
    frame, _ = make_frame(30)
    bad = frame.copy()
    bad.loc[bad.index[0], "city"] = "Atlantis"
    for cls in (F.TreeMatrix, F.LinearMatrix):
        with pytest.raises(F.FeatureError, match="ngoai domain"):
            cls(SYNTH_COLUMNS).fit(bad)
    inf = frame.copy()
    inf.loc[inf.index[0], "current_price"] = np.inf
    with pytest.raises(F.FeatureError, match="vo cuc"):
        F.TreeMatrix(SYNTH_COLUMNS).fit(inf).transform(inf)
    with pytest.raises(F.FeatureError, match="vo cuc"):
        F.LinearMatrix(SYNTH_COLUMNS).fit(inf)


def test_linear_matrix_statistics_are_fit_on_the_given_fold_only():
    frame, _ = make_frame(400, seed=5)
    train, other = frame.iloc[:200], frame.iloc[200:]
    m = F.LinearMatrix(SYNTH_COLUMNS).fit(train)
    expected_median = np.nanmedian(F.TreeMatrix(SYNTH_COLUMNS).transform(train)[:, SYNTH_COLUMNS.index("current_price")])
    j = m.num_cols.index("current_price")
    assert m.median_[j] == expected_median                             # thong ke tu CHINH tap fit
    full = F.LinearMatrix(SYNTH_COLUMNS).fit(frame)
    assert not np.isclose(m.mean_[j], full.mean_[j])                    # khong bi anh huong boi phan con lai
    xt = m.transform(train)
    assert np.allclose(xt[:, : len(m.num_cols)].mean(axis=0), 0, atol=1e-8)       # tren chinh tap fit: trung binh sau chuan hoa == 0
    assert m.transform(other).shape[1] == xt.shape[1] == len(m.names)


def test_linear_matrix_missing_flags_only_for_columns_with_nan_in_fit_and_onehot_domain():
    frame, _ = make_frame(300, seed=6)
    m = F.LinearMatrix(SYNTH_COLUMNS).fit(frame)
    flagged = [m.num_cols[i] for i in m.flag_idx_]
    assert "price_velocity" in flagged and "free_cancellation" in flagged and "current_price" not in flagged
    names = m.names
    assert "price_velocity__missing" in names and "city=Phú Quốc" in names and "inference_mode=cold_start" in names and len(names) == len(set(names))
    x = m.transform(frame)
    onehot = x[:, [names.index(f"city={c}") for c in ("Hồ Chí Minh", "Hà Nội", "Vũng Tàu", "Đà Lạt", "Phú Quốc")]]
    assert (onehot.sum(axis=1) == 1).all() and np.isfinite(x).all()
    # fit tren tap KHONG co NaN o price_velocity => khong co co bao; transform tap co NaN => impute theo trung vi, khong NaN
    clean = frame[frame["price_velocity"].notna()]
    m2 = F.LinearMatrix(SYNTH_COLUMNS).fit(clean)
    assert "price_velocity__missing" not in m2.names and np.isfinite(m2.transform(frame)).all()


def test_linear_matrix_requires_fit_and_make_matrix_kinds():
    with pytest.raises(F.FeatureError, match="chua fit"):
        F.LinearMatrix(SYNTH_COLUMNS).transform(make_frame(10)[0])
    assert isinstance(F.make_matrix("tree", SYNTH_COLUMNS), F.TreeMatrix) and isinstance(F.make_matrix("linear", SYNTH_COLUMNS), F.LinearMatrix)
    with pytest.raises(F.FeatureError):
        F.make_matrix("forest", SYNTH_COLUMNS)


def test_dry_run_reports_nan_columns_and_ratio_invalid_counts_without_labels():
    frame, _ = make_frame(300, seed=8)
    frame = frame.drop(columns=["y_direction_h1"])                                   # dry-run khong duoc can nhan
    frames = {"train": frame.iloc[:150], "validation": frame.iloc[150:220], "test": frame.iloc[220:]}
    sets = F.feature_sets(SYNTH_COLUMNS, synth_dictionary())
    out = F.dry_run(frames, sets)
    assert set(out) == {"base36", "a1_ratio", "a2_no_history"} and out["a1_ratio"]["n_columns"] == len(SYNTH_COLUMNS) + 6
    assert out["base36"]["splits"]["train"]["tree_nan_columns"]["price_velocity"] > 0 and out["base36"]["splits"]["test"]["rows"] == 80
    assert set(out["a1_ratio"]["ratio_block_invalid"]["train"]) == set(F.RATIO_BLOCK_COLUMNS) and out["base36"]["linear_n_columns"] > len(SYNTH_COLUMNS)


def test_dry_run_fails_closed_on_unknown_category_in_any_split():
    frame, _ = make_frame(200, seed=9)
    frames = {"train": frame.iloc[:100].copy(), "validation": frame.iloc[100:150].copy(), "test": frame.iloc[150:].copy()}
    frames["test"].loc[frames["test"].index[0], "city"] = "Atlantis"                # loi se lo ra o dry-run, khong phai sau khi da khoa tren Colab
    with pytest.raises(F.FeatureError, match="ngoai domain"):
        F.dry_run(frames, F.feature_sets(SYNTH_COLUMNS, synth_dictionary()))
