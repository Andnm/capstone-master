"""B3 tren MySQL that: split + feature/nhan + Parquet + reports + checksum + tai lap. Chay: `ML_SMOKE=1 pytest -m mysql`."""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from dataset_builder import env, runner
from dataset_builder.dictionary import dictionary_rows
from dataset_builder.feature_spec import FORBIDDEN_FEATURES
from dataset_builder.steps import STEP_FUNCTIONS

from helpers import rows, steps_with

pytestmark = pytest.mark.mysql
REAL = {name: STEP_FUNCTIONS[name] for name in ("causal_references", "item_matches", "samples_labels", "split", "features_labels")}


def _run_to_features(database, version, tmp_path):
    return runner.apply(database, version, steps=steps_with(REAL), output_root=tmp_path, stop_after="features_labels")


def test_split_applies_formula_and_purge(pipeline, tmp_path):
    database, version = pipeline([], purge_gap_days=1)
    runner.apply(database, version, steps=steps_with(REAL), output_root=tmp_path, stop_after="split")
    manifest = rows(database, "SELECT split_train_end, split_validation_end FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]
    assert manifest["split_train_end"] == dt.date(2026, 9, 8) and manifest["split_validation_end"] == dt.date(2026, 9, 10)
    by_day = {r["d"].day: r["s"] for r in rows(database, "SELECT vn_observation_date d, split s FROM ml_samples WHERE dataset_version=%s GROUP BY 1,2",
                                              (version,))}
    assert [by_day[d] for d in range(4, 13)] == ["train"] * 5 + [None, "validation", None, "test"]     # ngay 9 va 11 la purge
    report = json.loads((tmp_path / "_reports" / version / "split.json").read_text(encoding="utf-8"))
    assert report["plan"]["policy_path"] == "fallback_ratio" and report["sufficiency"] == "none"


def test_parquet_features_labels_and_reports(pipeline, tmp_path):
    database, version = pipeline([], purge_gap_days=1)
    _run_to_features(database, version, tmp_path)
    out = tmp_path / version
    frame = pd.read_parquet(out / "samples.parquet")
    assert len(frame) == 19 and frame["split"].value_counts().to_dict() == {"train": 11, "validation": 2, "test": 2} and frame["split"].isna().sum() == 4
    assert not set(FORBIDDEN_FEATURES) & set(frame.columns)
    # dictionary dong du cot
    dictionary = pd.read_csv(out / "data_dictionary.csv")
    assert dictionary["column"].tolist() == frame.columns.tolist()
    # nhan: has_label khong doi theo split, label_usable chi khi target cung split
    assert frame["has_label_h1"].sum() == 15 and frame["label_usable_h1"].sum() == 7 and frame["label_usable_h3"].sum() == 3
    assert frame["label_usable_h7"].sum() == 0 and frame["has_label_h14"].sum() == 0
    usable = frame[frame["label_usable_h1"]]
    assert (usable["split"] == "train").all() and (usable["y_price_h1"] - usable["current_price"] == usable["y_delta_h1"]).all()
    assert (usable["y_direction_h1"] == "up").all()                                    # gia tang 10k/ngay tren 100k+ > 2%?
    # lead time tinh lai, mau dau series chua co lich su
    h1 = frame[frame["hotel_id"] == "h1"].sort_values("vn_observation_date")
    assert h1["lead_time"].tolist() == [16, 15, 14, 13, 12, 11, 10, 9, 8]
    assert h1["history_observation_count"].tolist() == list(range(9))
    assert h1["inference_mode"].tolist() == ["cold_start"] * 3 + ["history_enriched"] * 6
    assert h1.iloc[0]["price_lag_1"] != h1.iloc[0]["price_lag_1"]                      # NaN
    assert h1.iloc[1]["price_lag_1"] == h1.iloc[0]["current_price"]
    # hotel val/test deu da co o train
    assert frame.loc[frame["split"].isin(["validation", "test"]), "hotel_seen_in_train_h1"].all()
    # lich: 14/09 la le hoi Phu Quoc, check-in cua h4/h5 la 21/09 (khong trung); 02/09 quoc khanh khong anh huong check-in 20/09
    assert frame["is_public_holiday"].sum() == 0 and frame["day_of_week"].iloc[0] in range(7)
    sufficiency = json.loads((out / "sufficiency_report.json").read_text(encoding="utf-8"))
    assert {h: v["status"] for h, v in sufficiency["horizons"].items()} == {"h1": "exploratory", "h3": "exploratory", "h7": "exploratory", "h14": "exploratory"}
    assert sufficiency["horizons"]["h1"]["splits"]["train"]["labeled_samples"] == 7
    coverage = json.loads((out / "coverage_report.json").read_text(encoding="utf-8"))
    assert coverage["rows"] == 19 and coverage["by_split"]["train"] == 11
    checks = json.loads((out / "output_checksums.json").read_text(encoding="utf-8"))
    manifest = rows(database, "SELECT output_parquet_sha256_json o, library_versions_json l FROM dataset_build_manifests WHERE dataset_version=%s", (version,))[0]
    stored = json.loads(manifest["o"]) if isinstance(manifest["o"], str) else manifest["o"]
    libs = json.loads(manifest["l"]) if isinstance(manifest["l"], str) else manifest["l"]
    assert stored == checks and stored["samples.parquet"]["rows"] == 19 and "pyarrow" in libs
    # R3-M1: snapshot bytes cua lich + calendar_input.json nam trong checksum, bytes == lich da ghim luc init
    assert {"calendar_input.json", "inputs/vn_holidays.csv"} <= set(stored)
    assert (out / "inputs" / "vn_holidays.csv").read_bytes() == env.HOLIDAYS_CSV.read_bytes()
    assert not (out / "tmp").exists()


def test_two_builds_have_same_content_checksum(pipeline, tmp_path):
    """Tai lap (spec muc 16): hai dataset_version cung input/config -> cung content_sha256 (loai technical ID va nhan version)."""
    database, v1 = pipeline([], purge_gap_days=1)
    _run_to_features(database, v1, tmp_path)
    database, v2 = pipeline([], purge_gap_days=1)
    _run_to_features(database, v2, tmp_path)
    a = json.loads((tmp_path / v1 / "output_checksums.json").read_text(encoding="utf-8"))["samples.parquet"]["content_sha256"]
    b = json.loads((tmp_path / v2 / "output_checksums.json").read_text(encoding="utf-8"))["samples.parquet"]["content_sha256"]
    assert a == b
    # assignment/sample cung noi dung (khong tinh technical ID)
    q = ("SELECT a.hotel_id, a.checkin_date, a.canonical_series_id, a.approved_at, COUNT(s.record_id) n FROM ml_reference_assignments a "
         "LEFT JOIN ml_samples s ON s.ml_reference_assignment_id=a.id WHERE a.dataset_version=%s GROUP BY 1,2,3,4 ORDER BY 1,2")
    assert rows(database, q, (v1,)) == rows(database, q, (v2,))


def test_rebuild_from_features_replaces_output_atomically(pipeline, tmp_path):
    database, version = pipeline([], purge_gap_days=1)
    _run_to_features(database, version, tmp_path)
    first = json.loads((tmp_path / version / "output_checksums.json").read_text(encoding="utf-8"))["samples.parquet"]["content_sha256"]
    runner.rebuild_from(database, version, "features_labels", steps=steps_with(REAL), output_root=tmp_path, stop_after="features_labels")
    second = json.loads((tmp_path / version / "output_checksums.json").read_text(encoding="utf-8"))["samples.parquet"]["content_sha256"]
    assert first == second and not (tmp_path / version / "tmp").exists()


def test_dictionary_rows_cover_every_output_column():
    from dataset_builder.feature_spec import feature_config
    from dataset_builder.features import output_columns
    columns = output_columns({"feature_config": feature_config()})
    assert len(columns) == len(set(columns))
    assert [r["column"] for r in dictionary_rows(columns)] == columns
