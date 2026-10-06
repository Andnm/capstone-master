"""Wave B - test thuan tren "the gioi" gia nhat quan (wave_b_world.py): tinh toan, mau so tach bat, kiem tra toan ven phat hien duoc lech, bucket nua mo, strict JSON,
coverage matrix, preflight fail-closed (fake conn), output + hinh, notebook khong chua SQL. Khong can MySQL."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import artifacts
import wave_b
import wave_b_inputs
import wave_b_output
import wave_b_queries as wbq
from wave_b_world import FIRST_SAMPLE_DAY, HORIZONS, START, make_world

NOTEBOOK = Path(__file__).resolve().parents[2] / "notebooks" / "02_curated_ml_eda.ipynb"


@pytest.fixture(scope="module")
def world_tables():
    world = make_world()
    return world, wave_b.compute_tables(world)


def violations(world):
    return wave_b.hard_violations(wave_b.compute_tables(world))


# ------------------------------------------------------------------ bucket nua mo + helper
def test_lead_buckets_are_half_open_and_labelled_like_the_builder():
    leads = pd.Series([-1, 0, 2, 3, 6, 7, 13, 14, 29, 30, 59, 60, 400])
    assert wave_b.lead_bucket(leads).tolist() == ["negative", "lt3", "lt3", "3-7", "3-7", "7-14", "7-14", "14-30", "14-30", "30-60", "30-60", "gt60", "gt60"]


def test_rate_never_turns_a_zero_denominator_into_zero_or_one():
    assert math.isnan(wave_b.rate(0, 0)) and math.isnan(wave_b.rate(5, 0)) and math.isnan(wave_b.rate(1, float("nan")))
    assert wave_b.rate(1, 4) == 0.25
    frame = wave_b.with_rate(pd.DataFrame({"n": [0, 3], "d": [0, 6]}), "r", "n", "d")
    assert frame["r_status"].tolist() == ["undefined_zero_denominator", "defined"] and math.isnan(frame.loc[0, "r"]) and frame.loc[1, "r"] == 0.5


def test_sanitize_makes_strict_json_with_null_for_undefined_and_nat():
    payload = {"a": float("nan"), "b": [np.float64("inf"), np.int64(3), pd.NaT, pd.Timestamp("2026-10-01")], "c": {"d": np.bool_(True), "e": pd.NA}}
    clean = wave_b.sanitize(payload)
    assert clean == {"a": None, "b": [None, 3, None, "2026-10-01T00:00:00"], "c": {"d": True, "e": None}}
    json.dumps(clean, allow_nan=False)


# ------------------------------------------------------------------ the gioi nhat quan: 0 vi pham + coverage matrix
def test_consistent_world_has_no_hard_violations(world_tables):
    _, tables = world_tables
    assert wave_b.hard_violations(tables) == {k: 0 for k in wave_b.hard_violations(tables)}


def test_every_plan_bullet_is_covered_by_a_nonempty_table(world_tables):
    _, tables = world_tables
    matrix = wave_b.coverage_matrix(tables, {"fig": (1, 2)})
    assert set(matrix["bullet"]) == set(wave_b.COVERAGE_BULLETS) and matrix["covered"].all() and (matrix["n_rows_total"] > 0).all()
    assert matrix.loc[matrix["bullet"] == 1, "figures"].iloc[0] == "fig" and matrix.loc[matrix["bullet"] == 3, "figures"].iloc[0] == ""


# ------------------------------------------------------------------ bang so (doi chieu doc lap bang vong lap python)
def test_label_rate_denominators_match_an_independent_recount(world_tables):
    world, tables = world_tables
    rates = tables["b05_label_rates"].frame
    samples = world.samples
    last = samples["vn_observation_date"].max()
    for k in HORIZONS:
        for split in ("train", "validation", "test", "purge", "ALL"):
            row = rates[(rates["horizon"] == k) & (rates["split"] == split)].iloc[0]
            subset = samples if split == "ALL" else samples[samples["split"].isna()] if split == "purge" else samples[samples["split"] == split]
            possible = [(d + pd.Timedelta(days=k)) <= last and (d + pd.Timedelta(days=k)) <= c for d, c in zip(subset["vn_observation_date"], subset["checkin_date"])]
            assert row["n_selected"] == len(subset) and row["n_calendar_possible"] == sum(possible)
            assert row["n_has_label"] == int(subset[f"has_label_h{k}"].sum()) and row["n_usable"] == int(subset[f"label_usable_h{k}"].sum())
            eligible = sum(p and s for p, s in zip(possible, subset["split"].notna()))
            assert row["n_source_eligible"] == eligible
            if eligible:
                assert row["usable_rate_of_source_eligible"] == pytest.approx(row["n_usable"] / eligible) and row["usable_rate_of_source_eligible_status"] == "defined"
            else:
                assert math.isnan(row["usable_rate_of_source_eligible"]) and row["usable_rate_of_source_eligible_status"] == "undefined_zero_denominator"


def test_a_world_too_short_for_h14_reports_undefined_not_zero_percent():
    world = make_world(n_days=12, purge=1, train_end=6, validation_end=9)
    rates = wave_b.compute_tables(world)["b05_label_rates"].frame
    h14 = rates[(rates["horizon"] == 14) & (rates["split"] == "ALL")].iloc[0]
    assert h14["n_calendar_possible"] == 0 and h14["n_usable"] == 0
    assert math.isnan(h14["has_label_rate_of_calendar_possible"]) and h14["has_label_rate_of_calendar_possible_status"] == "undefined_zero_denominator"


def test_match_phases_and_status_tables_use_separate_denominators(world_tables):
    world, tables = world_tables
    by_phase = tables["b02_match_by_phase"].frame
    assert set(by_phase["phase"]) == {"pre_approval", "post_approval"}                                  # khong co item cua chinh run approving trong the gioi nay
    for phase, group in by_phase.groupby("phase"):
        assert group["n_items"].sum() == group["n_group"].iloc[0] and group["share"].sum() == pytest.approx(1.0)
    matches = wave_b.prepare_matches(world.matches)
    assert by_phase.set_index(["phase", "match_status"])["n_items"].to_dict() == matches.groupby(["phase", "match_status"]).size().to_dict()
    lead = tables["b02_match_by_lead_bucket"].frame
    assert set(lead["lead_bucket"]) <= set(wave_b.LEAD_LABELS) and (lead["n_items"] <= lead["n_group"]).all()


def test_phase_boundary_is_decided_by_run_finish_versus_approved_at():
    matches = pd.DataFrame({"crawl_run_item_id": [1, 2, 3], "assignment_id": [1] * 3, "match_status": ["exact"] * 3, "match_score": [1.0] * 3, "selected_record_id": [1, 2, 3],
                            "run_id": [1, 2, 3], "checkin_date": [pd.Timestamp("2026-10-20")] * 3, "city": ["x"] * 3, "source_code": ["s"] * 3,
                            "run_started_at": pd.to_datetime(["2026-10-01 00:00"] * 3), "run_finished_at": pd.to_datetime(["2026-10-01 09:00", "2026-10-01 10:00", "2026-10-01 11:00"]),
                            "approved_at": pd.to_datetime(["2026-10-01 10:00"] * 3)})
    assert wave_b.prepare_matches(matches)["phase"].tolist() == ["pre_approval", "approving_run", "post_approval"]


def test_time_to_approval_is_run_completion_semantics_in_days(world_tables):
    _, tables = world_tables
    summary = tables["b01_time_to_approval"].frame.set_index("city")
    assert summary.loc["ALL", "n_with_first_evidence"] == 3 and summary.loc["ALL", "median_days"] == pytest.approx(FIRST_SAMPLE_DAY)
    assert summary.loc["ALL", "n_negative_days"] == 0
    distribution = tables["b01_time_to_approval_distribution"].frame
    assert distribution["n_assignments"].sum() == 3 and distribution.set_index("bin").loc["[3,5)", "n_assignments"] == 3


def test_snapshot_tables_separate_selected_from_excluded_duplicates(world_tables):
    world, tables = world_tables
    reasons = tables["b03_snapshot_reasons"].frame
    assert reasons.loc[reasons["is_daily_snapshot_selected"], "n_samples"].sum() == int(world.ml_samples["is_daily_snapshot_selected"].sum())
    assert reasons.loc[~reasons["is_daily_snapshot_selected"], "daily_snapshot_reason"].tolist() == ["later_in_day"]
    per_day = tables["b03_snapshot_candidates_per_series_day"].frame
    assert per_day["is_exactly_one_selected"].all() and set(per_day["candidates_bucket"]) == {"1", "2"}


def test_split_and_mode_counts_partition_the_selected_samples(world_tables):
    world, tables = world_tables
    assert tables["b04_sample_counts_by_split"].frame["n_samples"].sum() == len(world.samples)
    assert tables["b04_sample_counts_by_split_mode"].frame["n_samples"].sum() == len(world.samples)
    assert tables["b04_sample_counts_by_city"].frame.set_index("city")["n_samples"].to_dict() == world.samples.groupby("city").size().to_dict()
    modes = tables["b10_readiness_by_mode"].frame
    assert set(modes["inference_mode"]) == {"cold_start", "history_enriched"}
    enrich = tables["b10_history_enrichment"].frame.iloc[0]
    assert enrich["n_series"] == 3 and enrich["share_reaching_history_enriched"] == 1.0 and enrich["median_days_to_history_enriched"] == 3.0


def test_review_tier_is_post_hoc_and_partitions_samples(world_tables):
    world, tables = world_tables
    tiers = tables["b11_review_tier"].frame.set_index("review_tier")
    assert tiers["n_samples"].sum() == len(world.samples) and set(tiers.index) == {">=9.0", "<8.0"}
    assert tiers["caveat"].str.startswith("POST-HOC").all()


def test_strata_table_counts_exact_only_labels_from_the_match_table(world_tables):
    _, tables = world_tables
    strata = tables["b12_strata_exact_alias"].frame
    all_rows = strata[strata["split"] == "ALL"].set_index("horizon")
    assert (all_rows["n_status_missing"] == 0).all() and (all_rows["n_both_exact"] + all_rows["n_any_alias"] == all_rows["n_usable"]).all()
    assert all_rows.loc[1, "n_any_alias"] > 0                                                            # alias xuat hien 1/7 ngay


# ------------------------------------------------------------------ kiem tra toan ven phat hien lech
def test_label_integrity_detects_a_cross_assignment_or_wrong_date_target():
    world = make_world()
    ml = world.ml_samples
    first = ml[ml["is_daily_snapshot_selected"]].index[0]
    original = ml.loc[first, "label_source_record_id_h1"]
    ml.loc[first, "label_source_record_id_h1"] = 2000 + 20                                              # target thuoc series khac
    result = violations(world)
    assert result["label_integrity_violations"] >= 1
    ml.loc[first, "label_source_record_id_h1"] = original + 1                                            # cung series nhung sai ngay (d+2)
    assert violations(world)["label_integrity_violations"] >= 1


def test_purge_zone_check_detects_a_sample_assigned_inside_a_purge_zone():
    world = make_world()
    purge_day = START + pd.Timedelta(days=15)                                                            # train_end=14, purge=2 => 15,16 la purge
    mask = world.samples["vn_observation_date"] == purge_day
    assert mask.any() and world.samples.loc[mask, "split"].isna().all()
    world.samples.loc[mask, "split"] = "train"
    assert violations(world)["purge_zone_mismatch"] >= 1


def test_lag_causality_detects_a_lag_that_uses_the_wrong_day_or_value():
    world = make_world()
    target = world.samples.index[world.samples["price_lag_3"].notna()][0]
    world.samples.loc[target, "price_lag_3"] += 1.0                                                      # gia tri khong phai gia ngay d-3
    assert violations(world)["lag_causality_violations"] >= 1
    world2 = make_world()
    world2.samples.loc[world2.samples["price_lag_1"].notna().idxmax(), "price_lag_1"] = np.nan          # thieu lag du ngay d-1 co mau
    assert violations(world2)["lag_causality_violations"] >= 1


def test_attrition_detects_has_label_that_disagrees_with_the_series_calendar():
    world = make_world()
    world.samples.loc[world.samples["has_label_h7"].idxmax(), "has_label_h7"] = False
    assert violations(world)["attrition_has_label_disagrees_with_target_exists"] >= 1


def test_snapshot_check_detects_a_series_day_with_two_selected_samples():
    world = make_world()
    dup = world.ml_samples[~world.ml_samples["is_daily_snapshot_selected"]].index[0]
    world.ml_samples.loc[dup, "is_daily_snapshot_selected"] = True
    assert violations(world)["series_days_without_exactly_one_selected"] >= 1


def test_strata_detects_a_selected_record_without_exact_or_alias_match():
    world = make_world()
    record = int(world.samples["warehouse_record_id"].iloc[5])
    world.matches = world.matches[world.matches["selected_record_id"].fillna(-1) != record]
    assert violations(world)["strata_status_missing"] >= 1


def test_strata_parquet_columns_are_cross_checked_when_present():
    world = make_world()
    by_record = world.matches.dropna(subset=["selected_record_id"]).set_index(world.matches.dropna(subset=["selected_record_id"])["selected_record_id"].astype("int64"))["match_status"]
    world.samples["prediction_match_status"] = world.samples["warehouse_record_id"].map(by_record)
    for k in HORIZONS:
        world.samples[f"label_match_status_h{k}"] = None
    assert violations(world)["strata_parquet_disagree"] >= 1                                              # cot label rong trong khi ml_samples co nhan => lech
    ml = world.ml_samples.set_index("record_id")
    for k in HORIZONS:
        target = world.samples["warehouse_record_id"].map(ml[f"label_source_record_id_h{k}"])
        world.samples[f"label_match_status_h{k}"] = target.map(lambda r: by_record.get(int(r)) if pd.notna(r) else None)
    assert violations(world)["strata_parquet_disagree"] == 0
    world.samples.loc[world.samples.index[0], "prediction_match_status"] = "alias" if world.samples.loc[world.samples.index[0], "prediction_match_status"] == "exact" else "exact"
    assert violations(world)["strata_parquet_disagree"] == 1


def test_check_violations_raises_after_evidence_is_written_not_before():
    with pytest.raises(wave_b_output.WaveBIntegrityError, match="purge_zone_mismatch"):
        wave_b_output.check_violations({"purge_zone_mismatch": 2, "label_integrity_violations": 0})
    wave_b_output.check_violations({"purge_zone_mismatch": 0})


def test_feature_columns_require_a_dictionary_and_never_guess():
    world = make_world()
    assert "current_price" in wave_b.feature_columns(world.samples, world.dictionary) and "hotel_id" not in wave_b.feature_columns(world.samples, world.dictionary)
    with pytest.raises(ValueError, match="data_dictionary"):
        wave_b.feature_columns(world.samples, None)


# ------------------------------------------------------------------ output + hinh + manifest
def test_write_outputs_produces_versioned_artifacts_with_strict_json(tmp_path, world_tables):
    world, tables = world_tables
    analysis = artifacts.new_analysis_dir("eda_b_test", outputs_dir=tmp_path)
    figures = wave_b_output.make_figures(world, tables, analysis / "figures")
    assert len(figures) == 7 and all((analysis / "figures" / f"{name}.png").stat().st_size > 1000 for name in figures)
    summary = wave_b_output.write_outputs(analysis, world, tables, figures, wave_b.hard_violations(tables))
    for name in ("coverage_matrix_b.csv", "query_catalog_b.json", "input_manifest_b.json", "eda_summary_b.json", "EDA_REPORT_B.md", "DATA_DICTIONARY_B.md"):
        assert (analysis / name).exists(), name
    for name in ("query_catalog_b.json", "input_manifest_b.json", "eda_summary_b.json"):
        json.loads((analysis / name).read_text(encoding="utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"NaN/inf trong {name}: {c}")))
    assert summary["hard_violations_total"] == 0 and summary["dataset_version"] == "ds_world" and set(summary["usable_labels_by_horizon"]) == {"h1", "h3", "h7", "h14"}
    assert len(list((analysis / "tables").glob("*.csv"))) == len(tables)
    manifest_path = artifacts.write_artifact_manifest(analysis)
    names = {entry["path"] for entry in json.loads(manifest_path.read_text(encoding="utf-8"))["files"]}
    assert "tables/b05_label_rates.csv" in names and "figures/fig01_samples_by_date_split.png" in names
    assert "REHEARSAL/EXPLORATORY" in (analysis / "EDA_REPORT_B.md").read_text(encoding="utf-8")


def test_query_catalog_pins_sql_hashes_and_enforces_schema():
    manifest = wbq.catalog_manifest()
    assert manifest["catalog_version"] == wbq.CATALOG_VERSION and {q["query_id"] for q in manifest["queries"]} == set(wbq.QUERIES)
    assert all(len(q["sql_sha256"]) == 64 and q["grain"] and q["denominator"] for q in manifest["queries"])
    assert all(q.sql.lower().lstrip().startswith("select") for q in wbq.QUERIES.values())               # chi doc

    class _Frame:
        columns = ["a"]

    with pytest.raises(KeyError):
        wbq.run_query(None, "nope")
    with pytest.raises(ValueError, match="tham so"):
        wbq.run_query(None, "assignments")


def test_notebook_has_no_sql_and_only_orchestrates():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    source = "\n".join("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code")
    for banned in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "read_sql", "mysql"):
        assert banned not in source, banned
    assert "run_all" in source and "preflight" in source and "EDA_WB_DATASET_VERSION" in source


# ------------------------------------------------------------------ preflight fail-closed (fake conn)
class _Cursor:
    def __init__(self, conn):
        self.conn, self.description, self._rows = conn, None, []

    def execute(self, sql, params=()):
        rows = self.conn.respond(sql, params)
        self._rows = rows
        self.description = [(c,) for c in (rows[0].keys() if rows else ["n"])]

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, manifest, counts, selected):
        self.manifest, self.counts, self.selected = manifest, counts, selected

    def cursor(self, dictionary=False):
        return _Cursor(self)

    def respond(self, sql, params):
        if "FROM dataset_build_manifests" in sql:
            return [self.manifest] if self.manifest is not None else []
        if "is_daily_snapshot_selected" in sql:
            return [{"n": self.selected}]
        for table, value in self.counts.items():
            if f"FROM {table}" in sql:
                return [{"n": value}]
        raise AssertionError(sql)


def _preflight_setup(tmp_path, *, status="pass", batch="b_test", counts=None, selected=None, parquet_version="ds_t", rows=4, tamper=False, drop_samples=False):
    frame = pd.DataFrame({"dataset_version": [parquet_version] * rows, "x": range(rows)})
    path = tmp_path / "samples.parquet"
    frame.to_parquet(path, index=False)
    sha = wave_b_inputs.file_sha256(path)
    manifest = {"dataset_version": "ds_t", "status": status, "import_batch_id": batch, "last_completed_step": "validation", "purge_gap_days": 14,
                "split_train_end": "2026-08-31", "split_validation_end": "2026-09-17", "build_config_json": json.dumps({"builder_version": "b", "feature_config": {"version": "f"}, "evaluation_horizons": [1]}),
                "output_parquet_sha256_json": json.dumps({"samples.parquet": {"file_sha256": ("0" * 64) if tamper else sha}}), "build_config_sha256": "c" * 64,
                "feature_config_sha256": "d" * 64, "label_config_sha256": "e" * 64, "anomaly_registry_mode": "evaluation_asof", "anomaly_registry_cutoff_at": None}
    if drop_samples:
        path.unlink()
    conn = _FakeConn(manifest, counts or {"ml_reference_assignments": 3, "ml_item_reference_matches": 9, "ml_samples": 8}, rows if selected is None else selected)
    return conn, wave_b_inputs.WaveBInputs("db", "b_test", "ds_t", tmp_path)


def test_preflight_accepts_a_consistent_dataset_and_records_the_artifact_generation(tmp_path):
    conn, inputs = _preflight_setup(tmp_path)
    result = wave_b_inputs.preflight(conn, inputs)
    assert result["manifest_status"] == "pass" and result["selected_samples"] == 4 and result["artifact_generation"]["legacy"] is True
    assert result["artifact_generation"]["has_dataset_contract"] is False and result["artifact_generation"]["evaluation_horizons"] == [1]
    assert result["verified_file_sha256"]["samples.parquet"] and result["parquet"]["has_strata_columns"] is False


@pytest.mark.parametrize("kwargs, message", [
    ({"status": "running"}, "status='running'"), ({"batch": "other"}, "batch da chi dinh"),
    ({"counts": {"ml_reference_assignments": 0, "ml_item_reference_matches": 9, "ml_samples": 8}}, "ml_reference_assignments rong"),
    ({"counts": {"ml_reference_assignments": 3, "ml_item_reference_matches": 0, "ml_samples": 0}}, "ml_item_reference_matches rong"),
    ({"tamper": True}, "sha256 that"), ({"parquet_version": "ds_other"}, "cot dataset_version"), ({"selected": 5}, "mau duoc chon"), ({"drop_samples": True}, "khong tim thay"),
])
def test_preflight_fails_closed_and_lists_every_problem(tmp_path, kwargs, message):
    conn, inputs = _preflight_setup(tmp_path, **kwargs)
    with pytest.raises(wave_b_inputs.PreflightError, match=message):
        wave_b_inputs.preflight(conn, inputs)


def test_preflight_refuses_an_unknown_dataset_version(tmp_path):
    conn, inputs = _preflight_setup(tmp_path)
    conn.manifest = None
    with pytest.raises(wave_b_inputs.PreflightError, match="khong co trong dataset_build_manifests"):
        wave_b_inputs.preflight(conn, inputs)
