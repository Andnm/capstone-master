"""Cot audit strata (GPT file 48/52): `prediction_match_status` / `label_match_status_hK` la dinh danh, KHONG BAO GIO la feature. Thuan; phan MySQL o cuoi file."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset_builder import config as cfg  # noqa: E402
from dataset_builder import features, validation  # noqa: E402
from dataset_builder.dictionary import dictionary_rows  # noqa: E402
from dataset_builder.feature_spec import (AUDIT_MATCH_COLUMNS, FEATURE_GROUPS, FEATURE_VERSION, FORBIDDEN_FEATURES, HORIZONS, IDENTIFIER_COLUMNS,  # noqa: E402
                                          feature_config)
from test_horizon_contract import BASE  # noqa: E402

LABEL_STATUS = [f"label_match_status_h{k}" for k in HORIZONS]


def test_audit_columns_are_identifiers_outside_every_feature_group():
    assert AUDIT_MATCH_COLUMNS == ("prediction_match_status", *LABEL_STATUS)
    assert set(AUDIT_MATCH_COLUMNS) <= set(IDENTIFIER_COLUMNS)
    assert not set(AUDIT_MATCH_COLUMNS) & {c for group in FEATURE_GROUPS.values() for c in group}
    assert IDENTIFIER_COLUMNS[-1] == "warehouse_record_id"                       # cot ky thuat van o cuoi nhom dinh danh
    assert FEATURE_VERSION == "features-v1.2.0" and feature_config()["audit_only_columns"] == list(AUDIT_MATCH_COLUMNS)
    assert not set(AUDIT_MATCH_COLUMNS) & set(FORBIDDEN_FEATURES)               # FORBIDDEN_FEATURES = khong duoc co mat trong Parquet; audit co mat nhung khong phai feature


def test_feature_config_pins_the_category_domains_and_changes_the_dataset_identity():
    domains = feature_config()["category_domains"]
    assert domains["city"] == ["Hồ Chí Minh", "Hà Nội", "Vũng Tàu", "Đà Lạt", "Phú Quốc"] and domains["inference_mode"] == ["cold_start", "history_enriched"]
    config = cfg.build_config(**BASE)
    assert config["feature_config"]["category_domains"] == domains
    changed = {**config, "feature_config": {**config["feature_config"], "category_domains": {**domains, "city": domains["city"][:-1]}}}
    assert cfg.config_sha256(changed) != cfg.config_sha256(config)              # doi domain = dataset_version moi


def test_dictionary_marks_audit_columns_as_identifier_with_a_future_information_warning():
    rows = {r["column"]: r for r in dictionary_rows(list(AUDIT_MATCH_COLUMNS))}
    assert {r["group"] for r in rows.values()} == {"identifier"}
    assert "TUONG LAI" in rows["label_match_status_h7"]["leakage_note"] and "KHONG" in rows["prediction_match_status"]["leakage_note"]


def test_output_columns_put_audit_columns_in_the_identifier_prefix():
    config = cfg.build_config(**BASE)
    columns = features.output_columns(config)
    prefix = columns[:len(IDENTIFIER_COLUMNS)]
    assert prefix == list(IDENTIFIER_COLUMNS) and set(AUDIT_MATCH_COLUMNS) <= set(prefix)


@pytest.mark.parametrize("group", ["static", "history", "listing_signals", "mode", "calendar", "current", "lead_time"])
def test_dictionary_that_puts_an_audit_column_in_a_feature_group_is_rejected(group):
    from training.schema import SchemaError, select_features

    base = pd.DataFrame({"column": ["current_price", "city"], "group": ["current", "static"]})
    for column in AUDIT_MATCH_COLUMNS:
        bad = pd.concat([base, pd.DataFrame({"column": [column], "group": [group]})], ignore_index=True)
        with pytest.raises(SchemaError, match="bi cam"):
            select_features(bad)
        good = pd.concat([base, pd.DataFrame({"column": [column], "group": ["identifier"]})], ignore_index=True)
        assert select_features(good) == ["current_price", "city"]            # dung nhom identifier => khong bao gio thanh feature


# ----------------------------------------------------------------- validation (khong DB: fake fetch_all)
class _Conn:
    def commit(self):
        return None


def _frame(prediction, **labels):
    n = len(prediction)
    frame = pd.DataFrame({"prediction_match_status": prediction})
    for k in HORIZONS:
        frame[f"has_label_h{k}"] = labels.get(f"has{k}", [True] * n)
        frame[f"label_match_status_h{k}"] = labels.get(f"s{k}", ["exact"] * n)
    return frame


def _run(monkeypatch, tmp_path, frame, *, db=(("exact", 2), ("alias", 1)), config=None):
    monkeypatch.setattr(validation, "fetch_all", lambda conn, sql, params=(): [{"st": s, "n": n} for s, n in db])
    return validation._strata_check(_Conn(), frame, config or cfg.build_config(**BASE), "ds_x", tmp_path)


def test_strata_check_accepts_consistent_statuses(monkeypatch, tmp_path):
    frame = _frame(["exact", "exact", "alias"], s7=["alias", None, "exact"], has7=[True, False, True])
    frame.loc[1, "label_match_status_h7"] = None
    result = _run(monkeypatch, tmp_path, frame)
    assert result["ok"] is True, result


@pytest.mark.parametrize("mutate, message", [
    (lambda f: f.assign(prediction_match_status=["exact", "ambiguous", "alias"]), "ngoai"),
    (lambda f: f.assign(prediction_match_status=["exact", None, "alias"]), "ngoai"),
    (lambda f: f.assign(label_match_status_h7=["exact", "exact", "exact"], has_label_h7=[True, False, True]), "h7: label_match_status co gia tri khi khong co nhan"),
    (lambda f: f.assign(label_match_status_h3=["exact", "unavailable", "alias"]), "h3: nhan co label_match_status ngoai"),
    (lambda f: f.assign(label_match_status_h14=[None, "exact", "alias"]), "h14: nhan co label_match_status ngoai"),            # co nhan nhung thieu status
    (lambda f: f.assign(prediction_match_status=["exact", "exact", "exact"]), "phan bo prediction_match_status"),              # khac DB (2 exact + 1 alias)
])
def test_strata_check_rejects_every_inconsistency(monkeypatch, tmp_path, mutate, message):
    frame = mutate(_frame(["exact", "exact", "alias"]))
    result = _run(monkeypatch, tmp_path, frame)
    assert result["ok"] is False and any(message in problem for problem in result["detail"]), result["detail"]


def test_strata_check_rejects_a_config_that_lets_audit_columns_into_a_feature_group(monkeypatch, tmp_path):
    config = cfg.build_config(**BASE)
    leaky = {**config, "feature_config": {**config["feature_config"], "groups": {**config["feature_config"]["groups"],
                                                                              "static": config["feature_config"]["groups"]["static"] + ["prediction_match_status"]}}}
    result = _run(monkeypatch, tmp_path, _frame(["exact", "exact", "alias"]), config=leaky)
    assert result["ok"] is False and any("nhom feature" in p for p in result["detail"])
    wrong_dictionary = tmp_path / "data_dictionary.csv"
    pd.DataFrame({"column": list(AUDIT_MATCH_COLUMNS), "group": ["static"] * len(AUDIT_MATCH_COLUMNS)}).to_csv(wrong_dictionary, index=False)
    result2 = _run(monkeypatch, tmp_path, _frame(["exact", "exact", "alias"]))
    assert result2["ok"] is False and any("group=identifier" in p for p in result2["detail"])


# ----------------------------------------------------------------- features: join theo selected_record_id, kiem cardinality
def test_match_status_by_record_rejects_two_rows_for_one_selected_record(monkeypatch):
    monkeypatch.setattr(features, "fetch_all", lambda conn, sql, params=(): [{"selected_record_id": 5, "match_status": "exact"}, {"selected_record_id": 5, "match_status": "alias"}])
    with pytest.raises(ValueError, match="nhieu dong"):
        features._match_status_by_record(_Conn(), "ds_x")
    monkeypatch.setattr(features, "fetch_all", lambda conn, sql, params=(): [{"selected_record_id": 5, "match_status": "exact"}, {"selected_record_id": 6, "match_status": "alias"}])
    assert features._match_status_by_record(_Conn(), "ds_x") == {5: "exact", 6: "alias"}
