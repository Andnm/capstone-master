"""Phản ví dụ M1 (GPT 10) trên BUNDLE THẬT h1 + 2 hàng history_enriched thật ở lát as-of: chỉ sửa bản copy trong RAM, không sửa Parquet, không fit."""
import numpy as np
import pandas as pd
import pytest

from app.ml_serving import inference as I
from app.ml_serving.errors import InputContractError
from app.ml_serving.registry import WORKSPACE_ROOT, load_bundle, load_registry

REG = load_registry()
DATASET = WORKSPACE_ROOT / "outputs" / "datasets" / REG.bundle_entry("reg-h1-hgb_l1-08")["datasetVersion"] / "samples.parquet"
pytestmark = pytest.mark.skipif(not DATASET.exists(), reason="thiếu dataset cục bộ")


class NoPredict:
    def __init__(self):
        self.calls = 0

    def predict(self, X):
        self.calls += 1
        raise AssertionError("model.predict không được gọi khi đầu vào sai hợp đồng")


@pytest.fixture(scope="module")
def real():
    bundle, _ = load_bundle(REG, "reg-h1-hgb_l1-08")
    samples = pd.read_parquet(DATASET)
    asof = samples[samples["vn_observation_date"].astype(str) == samples["vn_observation_date"].astype(str).max()]
    rows = asof[(asof["lead_time"] >= 1) & (asof["inference_mode"] == "history_enriched")].head(2).reset_index(drop=True)
    return bundle, rows


def test_unmutated_real_rows_pass_and_are_finite(real):
    bundle, rows = real
    out = I.predict_prices(bundle, rows, REG)
    assert len(out) == 2 and np.isfinite(out["predicted_price"]).all()


@pytest.mark.parametrize("column, value", [("day_of_week", "BAD_NUMBER"), ("price_lag_1", "BAD_NUMBER"), ("price_lag_1", np.inf), ("price_rolling_mean_14", -np.inf), ("month", "10")])
def test_m1_mutations_on_real_bundle_are_rejected_before_predict(real, column, value):
    bundle, rows = real
    spy = NoPredict()
    mutated = {**bundle, "model": spy}
    frame = rows.copy()
    frame[column] = frame[column].astype(object)
    frame.loc[0, column] = value
    with pytest.raises(InputContractError):
        I.predict_prices(mutated, frame, REG)
    assert spy.calls == 0
