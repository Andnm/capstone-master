"""Dataset THAT (chi doc): so lieu da kiem doc lap boi GPT + negative test chuoi pin/toan ven tren ban SAO cua dataset."""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest

from cls_testenv import needs_real_data
from cls_training.data import PinError, describe_folds, load_dataset, make_folds
from cls_training.labels import LabelContractError
from cls_training.upstream.provenance import DatasetVerificationError, file_sha256

pytestmark = needs_real_data


def test_h1_counts_match_independent_gpt_verification(loaded_h1, cfg):
    assert loaded_h1.entries_verified == 14 and len(loaded_h1.features) == 36
    assert loaded_h1.label_report["n_usable"] == 116256
    assert loaded_h1.frames_info["rows_primary"] == {"train": 74777, "validation": 20414, "test": 20993}
    assert loaded_h1.frames_info["rows_all_hotels"] == {"train": 74777, "validation": 20414, "test": 21065}
    assert loaded_h1.frames_info["excluded_unseen_hotel_rows"] == {"validation": 0, "test": 72} and loaded_h1.frames_info["excluded_unseen_hotels"] == {"validation": 0, "test": 1}
    assert {sp: int(f["vn_observation_date"].nunique()) for sp, f in loaded_h1.frames.items()} == {"train": 26, "validation": 7, "test": 7}
    folds = describe_folds(loaded_h1.frames["train"], make_folds(loaded_h1.frames["train"], 1, cfg["cv"]), 1)
    assert [(f["n_train"], f["n_val"]) for f in folds] == [(37040, 12554), (49212, 14718), (63390, 7639)]


def test_h3_counts_match_independent_gpt_verification(loaded_h3, cfg):
    assert loaded_h3.label_report["n_usable"] == 78668
    assert loaded_h3.frames_info["rows_primary"] == {"train": 57015, "validation": 10528, "test": 11054}
    assert loaded_h3.frames_info["rows_all_hotels"] == {"train": 57015, "validation": 10540, "test": 11113}
    assert loaded_h3.frames_info["excluded_unseen_hotel_rows"] == {"validation": 12, "test": 59} and loaded_h3.frames_info["excluded_unseen_hotels"] == {"validation": 1, "test": 2}
    assert {sp: int(f["vn_observation_date"].nunique()) for sp, f in loaded_h3.frames.items()} == {"train": 22, "validation": 4, "test": 4}
    folds = describe_folds(loaded_h3.frames["train"], make_folds(loaded_h3.frames["train"], 3, cfg["cv"]), 3)
    assert [(f["n_train"], f["n_val"]) for f in folds] == [(34391, 9352), (42200, 5463)]


def test_label_boundary_rows_are_exactly_what_the_report_says(loaded_h1, loaded_h3):
    assert loaded_h1.label_report["boundary_mass"]["rows_changed_by_naive_ratio_arithmetic"] == 56
    assert loaded_h3.label_report["boundary_mass"]["rows_changed_by_naive_ratio_arithmetic"] == 41


def test_feature_list_has_no_identifier_label_or_audit_columns(loaded_h1):
    banned_prefix = ("y_", "has_label", "label_usable", "label_match", "hotel_seen")
    assert not [c for c in loaded_h1.features if c.startswith(banned_prefix)]
    assert not {"hotel_id", "canonical_series_id", "warehouse_record_id", "prediction_match_status", "split"} & set(loaded_h1.features)
    assert {"city", "inference_mode"} <= set(loaded_h1.features)


def test_wrong_pin_fields_are_rejected(cfg, dataset_copy):
    d, pin = dataset_copy
    for field in ("output_checksums_sha256", "dataset_contract_sha256", "samples_file_sha256", "feature_list_sha256"):
        bad = copy.deepcopy(pin)
        bad[field] = "0" * 64
        with pytest.raises(PinError, match="pin"):
            load_dataset(d, bad, 1)
    bad = copy.deepcopy(pin)
    bad["evaluation_horizons"] = [1, 3]
    with pytest.raises(PinError):
        load_dataset(d, bad, 1)
    with pytest.raises(PinError):
        load_dataset(d, pin, 3)                                          # horizon ngoai evaluation_horizons cua dataset
    bad = copy.deepcopy(pin)
    bad["n_entries"] = 15
    with pytest.raises(DatasetVerificationError):
        load_dataset(d, bad, 1)


def test_tampered_listed_file_is_rejected(dataset_copy):
    d, pin = dataset_copy
    target = d / "inputs" / "n1_policy" / "n1_scan_identity.json"           # nam trong output_checksums nhung KHONG thuoc 7 file co ban
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(DatasetVerificationError):
        load_dataset(d, pin, 1)


def test_missing_listed_file_is_rejected(dataset_copy):
    d, pin = dataset_copy
    (d / "inputs" / "n1_policy" / "parity_on_artifacts.out").unlink()
    with pytest.raises(DatasetVerificationError):
        load_dataset(d, pin, 1)


def test_corrupt_samples_parquet_is_rejected(dataset_copy):
    d, pin = dataset_copy
    raw = bytearray((d / "samples.parquet").read_bytes())
    raw[len(raw) // 3] ^= 0xFF
    (d / "samples.parquet").write_bytes(bytes(raw))
    with pytest.raises(DatasetVerificationError):
        load_dataset(d, pin, 1)


def _flip_one_stored_label_and_refresh_checksums(d):
    s = pd.read_parquet(d / "samples.parquet")
    idx = s.index[(s["label_usable_h1"].fillna(False).astype(bool)) & (s["y_direction_h1"] == "stable")][0]
    s.loc[idx, "y_direction_h1"] = "up"
    s.to_parquet(d / "samples.parquet", index=False)
    checks = json.loads((d / "output_checksums.json").read_text(encoding="utf-8"))
    checks["samples.parquet"]["file_sha256"] = file_sha256(d / "samples.parquet")
    (d / "output_checksums.json").write_text(json.dumps(checks, indent=2, sort_keys=True), encoding="utf-8")


def test_pin_catches_a_consistently_replaced_dataset(dataset_copy):
    """Thay ca file lan output_checksums.json cho nhat quan van bi chan boi pin (chuoi tin cay nam trong config, khong trong chinh dataset)."""
    d, pin = dataset_copy
    _flip_one_stored_label_and_refresh_checksums(d)
    with pytest.raises(PinError, match="output_checksums.json|samples.parquet"):
        load_dataset(d, pin, 1)


def test_label_validator_catches_it_even_if_the_attacker_also_updates_the_pin(dataset_copy):
    d, pin = dataset_copy
    _flip_one_stored_label_and_refresh_checksums(d)
    new_pin = copy.deepcopy(pin)
    new_pin["output_checksums_sha256"] = file_sha256(d / "output_checksums.json")
    new_pin["samples_file_sha256"] = file_sha256(d / "samples.parquet")
    with pytest.raises(LabelContractError, match="khac phep tinh"):
        load_dataset(d, new_pin, 1)
