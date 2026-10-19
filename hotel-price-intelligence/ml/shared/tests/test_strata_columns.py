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


# ----------------------------------------------------------------- validation (khong DB: fake fetch_all theo SQL)
class _Conn:
    def commit(self):
        return None


class _World:
    """Mot DB gia + Parquet nhat quan: sample i co target (moi horizon) la sample i+1 (mau cuoi khong co nhan). Moi sample co mot dong match o trang thai `pred[i]`."""

    def __init__(self, pred):
        n = len(pred)
        self.ids = list(range(100, 100 + n))
        self.status = dict(zip(self.ids, pred))                                       # record_id -> match_status (DB)
        self.targets = {i: (self.ids[j + 1] if j + 1 < n else None) for j, i in enumerate(self.ids)}
        frame = pd.DataFrame({"warehouse_record_id": self.ids, "prediction_match_status": list(pred)})
        for k in HORIZONS:
            frame[f"has_label_h{k}"] = [self.targets[i] is not None for i in self.ids]
            frame[f"label_match_status_h{k}"] = [None if self.targets[i] is None else self.status[self.targets[i]] for i in self.ids]
        self.frame = frame

    def fetch_all(self, conn, sql, params=()):
        if "GROUP BY m.match_status" in sql:
            counts: dict[str, int] = {}
            for rid in self.ids:
                if rid in self.status:                                                    # JOIN: sample khong co match thi khong vao tong ket
                    counts[self.status[rid]] = counts.get(self.status[rid], 0) + 1
            return [{"st": s, "n": n} for s, n in counts.items()]
        if "FROM ml_samples s WHERE" in sql:
            return [{"record_id": rid, **{f"label_source_record_id_h{k}": self.targets[rid] for k in HORIZONS}} for rid in self.ids]
        if "FROM ml_item_reference_matches m" in sql:
            return [{"selected_record_id": rid, "match_status": st} for rid, st in self.status.items()]
        raise AssertionError(sql)


def _run(monkeypatch, tmp_path, world, *, frame=None, config=None):
    monkeypatch.setattr(validation, "fetch_all", world.fetch_all)
    return validation._strata_check(_Conn(), world.frame if frame is None else frame, config or cfg.build_config(**BASE), "ds_x", tmp_path)


def test_strata_check_accepts_a_consistent_world(monkeypatch, tmp_path):
    result = _run(monkeypatch, tmp_path, _World(["exact", "exact", "alias", "exact"]))
    assert result["ok"] is True, result


@pytest.mark.parametrize("mutate, message", [
    (lambda f: f.assign(prediction_match_status=["exact", "ambiguous", "alias", "exact"]), "ngoai"),
    (lambda f: f.assign(prediction_match_status=["exact", None, "alias", "exact"]), "ngoai"),
    (lambda f: f.assign(label_match_status_h7=["exact", "exact", "exact", "exact"]), "h7: label_match_status co gia tri khi khong co nhan"),
    (lambda f: f.assign(label_match_status_h3=["exact", "unavailable", "alias", None]), "h3: nhan co label_match_status ngoai"),
    (lambda f: f.assign(label_match_status_h14=[None, "exact", "alias", None]), "h14: nhan co label_match_status ngoai"),             # co nhan nhung thieu status
])
def test_strata_check_rejects_enum_and_nullness_violations(monkeypatch, tmp_path, mutate, message):
    world = _World(["exact", "exact", "alias", "exact"])
    result = _run(monkeypatch, tmp_path, world, frame=mutate(world.frame))
    assert result["ok"] is False and any(message in problem for problem in result["detail"]), result["detail"]


def test_strata_check_catches_a_swap_that_keeps_the_aggregate_counts(monkeypatch, tmp_path):
    """GPT file 54 C53-M2: hoan vi exact/alias giua hai dong Parquet giu nguyen phan bo (1 alias, 3 exact) nhung sai record."""
    world = _World(["exact", "exact", "alias", "exact"])
    swapped = world.frame.copy()
    swapped["prediction_match_status"] = ["alias", "exact", "exact", "exact"]                 # alias nhay tu sample 2 sang sample 0
    result = _run(monkeypatch, tmp_path, world, frame=swapped)
    assert result["ok"] is False and not any("phan bo" in p for p in result["detail"])         # tong ket van khop; chi per-record bat duoc
    assert any("prediction_match_status khac match DB o 2 mau" in p for p in result["detail"])


def test_strata_check_catches_a_wrong_label_status_with_a_valid_enum(monkeypatch, tmp_path):
    world = _World(["exact", "exact", "alias", "exact"])
    flipped = world.frame.copy()
    flipped.loc[0, "label_match_status_h7"] = "alias"                                           # dung enum, sai target (target cua sample 0 la sample 1 = exact)
    result = _run(monkeypatch, tmp_path, world, frame=flipped)
    assert result["ok"] is False and any("label_match_status_h7 khac match cua target DB o 1 mau" in p for p in result["detail"])
    same_other_target = world.frame.copy()
    same_other_target.loc[0, "label_match_status_h1"] = world.status[world.ids[2]]               # status that cua MOT target khac (sample 2 = alias)
    assert _run(monkeypatch, tmp_path, world, frame=same_other_target)["ok"] is False


def test_strata_check_catches_db_rows_swapped_with_the_same_counts(monkeypatch, tmp_path):
    world = _World(["exact", "exact", "alias", "exact"])
    frame = world.frame.copy()                                                                    # Parquet giu nguyen
    world.status[world.ids[0]], world.status[world.ids[2]] = world.status[world.ids[2]], world.status[world.ids[0]]    # DB doi cho: cung phan bo
    result = _run(monkeypatch, tmp_path, world, frame=frame)
    assert result["ok"] is False and not any("phan bo" in p for p in result["detail"])


def test_strata_check_catches_missing_target_match_and_row_set_drift(monkeypatch, tmp_path):
    world = _World(["exact", "exact", "alias", "exact"])
    target = world.targets[world.ids[1]]
    del world.status[target]                                                                       # target cua sample 1 mat match
    assert any("khong co match" in p for p in _run(monkeypatch, tmp_path, world)["detail"])
    world2 = _World(["exact", "exact", "alias"])
    shrunk = world2.frame.iloc[:2].copy()
    assert any("tap warehouse_record_id" in p for p in _run(monkeypatch, tmp_path, world2, frame=shrunk)["detail"])
    duplicated = world2.frame.copy()
    duplicated.loc[1, "warehouse_record_id"] = duplicated.loc[0, "warehouse_record_id"]
    assert any("trung" in p for p in _run(monkeypatch, tmp_path, world2, frame=duplicated)["detail"])


def test_strata_check_rejects_a_config_that_lets_audit_columns_into_a_feature_group(monkeypatch, tmp_path):
    config = cfg.build_config(**BASE)
    leaky = {**config, "feature_config": {**config["feature_config"], "groups": {**config["feature_config"]["groups"],
                                                                              "static": config["feature_config"]["groups"]["static"] + ["prediction_match_status"]}}}
    result = _run(monkeypatch, tmp_path, _World(["exact", "exact", "alias"]), config=leaky)
    assert result["ok"] is False and any("nhom feature" in p for p in result["detail"])
    wrong_dictionary = tmp_path / "data_dictionary.csv"
    pd.DataFrame({"column": list(AUDIT_MATCH_COLUMNS), "group": ["static"] * len(AUDIT_MATCH_COLUMNS)}).to_csv(wrong_dictionary, index=False)
    result2 = _run(monkeypatch, tmp_path, _World(["exact", "exact", "alias"]))
    assert result2["ok"] is False and any("group=identifier" in p for p in result2["detail"])


# ----------------------------------------------------------------- features: join theo selected_record_id, kiem cardinality
def test_match_status_by_record_rejects_two_rows_for_one_selected_record(monkeypatch):
    monkeypatch.setattr(features, "fetch_all", lambda conn, sql, params=(): [{"selected_record_id": 5, "match_status": "exact"}, {"selected_record_id": 5, "match_status": "alias"}])
    with pytest.raises(ValueError, match="nhieu dong"):
        features._match_status_by_record(_Conn(), "ds_x")
    monkeypatch.setattr(features, "fetch_all", lambda conn, sql, params=(): [{"selected_record_id": 5, "match_status": "exact"}, {"selected_record_id": 6, "match_status": "alias"}])
    assert features._match_status_by_record(_Conn(), "ds_x") == {5: "exact", 6: "alias"}
