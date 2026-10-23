"""Wrapper inference (C6–C8): routing, mode lạ, category lạ, missingness theo hợp đồng train, đầu ra xấu, bất biến thứ tự/batch. Bundle GIẢ (không fit, không joblib)."""
import numpy as np
import pandas as pd
import pytest

from app.ml_serving import inference as I
from app.ml_serving.errors import InputContractError
from app.ml_serving.registry import activate_code_paths, load_registry

REG = load_registry()
activate_code_paths(REG)
from dataset_builder.feature_spec import CATEGORY_DOMAINS  # noqa: E402
from training.encoder import TreeEncoder  # noqa: E402

FEATS = ["inference_mode", "city", "lead_time_bucket", "current_price", "price_lag_1", "day_of_week"]
POLICY = {"cold_start": {"use_model": False, "reason": "ci_lower_not_positive"}, "history_enriched": {"use_model": True, "reason": "ok"}}


class FakeModel:
    """Không fit: z là hàm xác định của cột current_price (cột 3) để kiểm bất biến thứ tự/batch."""

    def __init__(self, z=None):
        self.z = z
        self.calls = []

    def predict(self, X):
        X = np.asarray(X, float)
        self.calls.append(X.copy())
        if self.z is None:
            return np.log1p(X[:, 3] / 1e6) * 0.05
        return np.full(len(X), self.z, float)


def make_bundle(model, features=FEATS, policy=POLICY):
    return {"name": "fake", "horizon": 1, "matrix": "tree", "features": list(features), "effective_features": list(features), "n_features_in": len(features),
            "encoder_categories": TreeEncoder(list(features)).categories, "model": model, "routing_policy": policy, "target_transform": "log_ratio"}


def make_frame(n=4):
    cities, buckets = CATEGORY_DOMAINS["city"], CATEGORY_DOMAINS["lead_time_bucket"]
    return pd.DataFrame({
        "inference_mode": ["cold_start", "history_enriched"] * (n // 2), "city": [cities[i % len(cities)] for i in range(n)], "lead_time_bucket": [buckets[i % len(buckets)] for i in range(n)],
        "current_price": [1_000_000.0 + 10_000 * i for i in range(n)], "price_lag_1": [900_000.0 + 5_000 * i for i in range(n)], "day_of_week": list(range(n)),
        "y_price_h1": [1.0] * n, "label_usable_h1": [True] * n, "split": ["test"] * n})                       # cột ngoài allowlist: KHÔNG được đi vào model


def test_c6_routing_cold_start_is_persistence_and_history_uses_model():
    model = FakeModel(z=0.1)
    out = I.predict_prices(make_bundle(model), make_frame(4), REG)
    cold, hist = out[out["method"] == "persistence"], out[out["method"] == "model"]
    assert len(cold) == 2 and len(hist) == 2
    frame = make_frame(4)
    assert (out.loc[cold.index, "predicted_price"].to_numpy() == frame.loc[cold.index, "current_price"].to_numpy()).all()      # bằng tuyệt đối
    assert (cold["expected_pct_change"] == 0.0).all() and (cold["direction"] == "stable").all() and (cold["routing_reason"] == "ci_lower_not_positive").all()
    assert hist["expected_pct_change"].between(10.5, 10.6).all() and (hist["direction"] == "up").all() and (hist["routing_reason"] == "ok").all()


def test_model_only_sees_bundle_features_not_labels_or_split():
    model = FakeModel(z=0.0)
    I.predict_prices(make_bundle(model), make_frame(4), REG)
    assert model.calls[0].shape == (4, len(FEATS))


def test_unknown_inference_mode_is_rejected_before_any_prediction():
    model = FakeModel(z=0.1)
    frame = make_frame(4)
    frame.loc[1, "inference_mode"] = "warm_start"
    with pytest.raises(InputContractError, match="inference_mode"):
        I.predict_prices(make_bundle(model), frame, REG)
    assert model.calls == []                                                          # không predict, không rơi về persistence ngầm


def test_unknown_or_missing_category_is_rejected_not_turned_into_nan():
    for column, bad in (("city", "NOT_A_VALID_CITY"), ("lead_time_bucket", "999d"), ("city", None)):
        model = FakeModel(z=0.1)
        frame = make_frame(4)
        frame.loc[2, column] = bad
        with pytest.raises(InputContractError, match=column):
            I.predict_prices(make_bundle(model), frame, REG)
        assert model.calls == []


def test_c7a_nullable_feature_nan_is_kept_not_imputed():
    model = FakeModel(z=0.0)
    frame = make_frame(4)
    frame.loc[1, "price_lag_1"] = np.nan                                              # price_lag_1 thuộc nullableFeatures
    out = I.predict_prices(make_bundle(model), frame, REG)
    assert len(out) == 4 and np.isnan(model.calls[0][1, 4]) and not np.isnan(model.calls[0][0, 4])
    assert I.nan_report(frame, make_bundle(model))["price_lag_1"] == 1


def test_c7b_nan_in_required_feature_is_rejected():
    for column in ("day_of_week", "current_price"):
        model = FakeModel(z=0.0)
        frame = make_frame(4)
        frame.loc[0, column] = np.nan
        with pytest.raises(InputContractError):
            I.predict_prices(make_bundle(model), frame, REG)
        assert model.calls == []


@pytest.mark.parametrize("bad", [0.0, -5.0, np.inf, np.nan])
def test_current_price_must_be_finite_positive(bad):
    frame = make_frame(4)
    frame.loc[3, "current_price"] = bad
    with pytest.raises(InputContractError):
        I.predict_prices(make_bundle(FakeModel(z=0.0)), frame, REG)


def test_missing_column_and_empty_frame():
    with pytest.raises(InputContractError, match="thiếu cột"):
        I.predict_prices(make_bundle(FakeModel(z=0.0)), make_frame(4).drop(columns=["price_lag_1"]), REG)
    with pytest.raises(InputContractError):
        I.predict_prices(make_bundle(FakeModel(z=0.0)), make_frame(4).iloc[0:0], REG)


def test_model_output_nan_or_overflow_is_an_error_not_a_price():
    for z in (np.nan, np.inf, 1000.0):
        with pytest.raises(Exception):
            I.predict_prices(make_bundle(FakeModel(z=z)), make_frame(4), REG)


def test_unsupported_matrix_is_rejected():
    bundle = make_bundle(FakeModel(z=0.0))
    bundle["matrix"] = "raw"
    with pytest.raises(InputContractError, match="matrix"):
        I.predict_prices(bundle, make_frame(4), REG)


def test_c8_order_and_batch_invariance():
    frame = make_frame(40)
    frame["current_price"] = np.linspace(500_000, 3_000_000, 40)
    bundle = make_bundle(FakeModel())
    whole = I.predict_prices(bundle, frame, REG)
    perm = np.random.default_rng(7).permutation(40)
    shuffled = I.predict_prices(bundle, frame.iloc[perm].reset_index(drop=True), REG)
    back = shuffled.iloc[np.argsort(perm)].reset_index(drop=True)
    chunked = pd.concat([I.predict_prices(bundle, frame.iloc[i:i + 7].reset_index(drop=True), REG) for i in range(0, 40, 7)], ignore_index=True)
    for other in (back, chunked):
        assert np.allclose(whole["predicted_price"].to_numpy(), other["predicted_price"].to_numpy(), rtol=0, atol=0.01)
        assert (whole["method"].to_numpy() == other["method"].to_numpy()).all() and (whole["direction"].to_numpy() == other["direction"].to_numpy()).all()
        assert (whole["routing_reason"].to_numpy() == other["routing_reason"].to_numpy()).all()


@pytest.mark.parametrize("column, bad", [("day_of_week", "BAD_NUMBER"), ("price_lag_1", "BAD_NUMBER"), ("price_lag_1", np.inf), ("price_lag_1", -np.inf), ("day_of_week", "5"),
                                         ("current_price", "1000000"), ("day_of_week", float("inf")), ("price_lag_1", b"12"), ("price_lag_1", object())])
def test_m1_non_numeric_strings_and_infinity_never_become_silent_nan(column, bad):
    model = FakeModel(z=0.0)
    frame = make_frame(4)
    frame[column] = frame[column].astype(object)
    frame.loc[1, column] = bad
    with pytest.raises(InputContractError):
        I.predict_prices(make_bundle(model), frame, REG)
    assert model.calls == []                                                          # model.predict CHƯA được gọi


def test_m1_genuine_missing_and_typed_values_still_pass_without_imputation():
    model = FakeModel(z=0.0)
    frame = make_frame(4)
    frame["price_lag_1"] = pd.array([900_000, None, 910_000, None], dtype="Float64")      # thiếu thật ở feature nullable, dtype nullable của pandas
    frame["day_of_week"] = frame["day_of_week"].astype(object)                             # object nhưng chỉ chứa int Python: hợp lệ
    out = I.predict_prices(make_bundle(model), frame, REG)
    assert len(out) == 4 and np.isnan(model.calls[0][1, 4]) and np.isnan(model.calls[0][3, 4]) and model.calls[0][0, 4] == 900_000
