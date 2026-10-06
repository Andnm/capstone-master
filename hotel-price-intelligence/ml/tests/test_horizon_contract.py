"""Lat 1 (GPT file 50 muc 4): allowed evaluation horizons + purge >= max + dataset_contract.json + whitelist training. Thuan, khong MySQL."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset_builder import config as cfg  # noqa: E402
from dataset_builder.export import CONTRACT_NAME, CONTRACT_VERSION, dataset_contract  # noqa: E402
from dataset_builder.feature_spec import HORIZONS  # noqa: E402
from dataset_builder.splitter import plan_split  # noqa: E402
from dataset_builder.sufficiency import candidate_frame, evaluate_horizon, gate_for, sufficiency_report  # noqa: E402
from dataset_builder.validation import _contract_check, _official_gate_check  # noqa: E402
from test_training import make_dataset, write_checksums, write_contract  # noqa: E402
from training.provenance import DatasetVerificationError, verify_dataset  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
BASE = dict(import_batch_id="b1", purpose="rehearsal", min_runs=3, min_coverage=0.8, anomaly_mode="retrospective_full", anomaly_cutoff_at=None,
            anomaly_registry_file_sha256="0" * 64, builder_code={"files": {"a.py": "1" * 64}, "code_sha256": "a" * 64},
            calendar_input={"name": "vn_holidays.csv", "sha256": "1" * 64, "bytes": 1})


# ----------------------------------------------------------------- invariant 1-2: whitelist + purge
@pytest.mark.parametrize("values, message", [
    ([], "khong duoc rong"), ([7, 7], "trung"), ([5], "ngoai tap"), ([1, 30], "ngoai tap"), (None, "danh sach"), ("7", "danh sach"), (["x"], "phai la so nguyen"),
    ([True], "phai la so nguyen"), ([7.9], "phai la so nguyen"), ([7.0], "phai la so nguyen"), (["7"], "phai la so nguyen"), ([None], "phai la so nguyen"),
])
def test_evaluation_horizons_must_be_nonempty_unique_and_known(values, message):
    with pytest.raises(ValueError, match=message):
        cfg.normalize_evaluation_horizons(values)


@pytest.mark.parametrize("purge", [7.9, 7.0, True, "7", None])
def test_purge_gap_days_must_be_a_real_integer_and_is_never_truncated(purge):
    with pytest.raises(ValueError, match="purge_gap_days phai la so nguyen"):
        cfg.validate_horizon_contract([7], purge)
    if purge is not None:                                                                   # None = mac dinh max(evaluation_horizons) cua build_config, hop le
        with pytest.raises(ValueError, match="purge_gap_days phai la so nguyen"):
            cfg.build_config(**BASE, evaluation_horizons=[7], purge_gap_days=purge)


def test_numpy_integers_are_accepted_but_bool_is_not():
    import numpy as np

    assert cfg.normalize_evaluation_horizons([np.int64(7), np.int32(1)]) == [1, 7]
    assert cfg.validate_horizon_contract([7], np.int64(7)) == [7]
    assert cfg.build_config(**BASE, evaluation_horizons=[7], purge_gap_days=np.int64(9))["purge_gap_days"] == 9


@pytest.mark.parametrize("argument", ["7.5", "x", "1,,3x", "True"])
def test_init_cli_rejects_non_integer_horizons_before_any_database_connection(argument, capsys, monkeypatch):
    spec = importlib.util.spec_from_file_location("init_dataset_build", SCRIPTS / "init_dataset_build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("khong duoc ket noi DB")))
    monkeypatch.setattr(sys, "argv", ["init_dataset_build.py", "--database", "x", "--dataset-version", "ds_20261006_x", "--purpose", "rehearsal", "--evaluation-horizons", argument])
    assert module.main() == 2
    assert "FAIL" in capsys.readouterr().err


def test_normalize_sorts_and_accepts_every_subset():
    assert cfg.normalize_evaluation_horizons([14, 1, 7]) == [1, 7, 14]
    assert cfg.normalize_evaluation_horizons(list(HORIZONS)) == [1, 3, 7, 14]


@pytest.mark.parametrize("horizons, purge, ok", [([7], 7, True), ([7], 14, True), ([7], 6, False), ([1, 3, 7, 14], 14, True), ([1, 3, 7, 14], 13, False), ([1], 1, True), ([14], 7, False)])
def test_purge_must_cover_the_largest_evaluated_horizon(horizons, purge, ok):
    if ok:
        assert cfg.validate_horizon_contract(horizons, purge) == sorted(horizons)
    else:
        with pytest.raises(ValueError, match="purge_gap_days"):
            cfg.validate_horizon_contract(horizons, purge)


def test_candidates_must_equal_the_allowed_horizons():
    assert cfg.split_selection_policy([7])["horizon_candidates_desc"] == [7]
    assert cfg.split_selection_policy()["horizon_candidates_desc"] == [14, 7, 3, 1]
    with pytest.raises(ValueError, match="horizon_candidates_desc"):
        cfg.validate_horizon_contract([7], 7, policy=cfg.split_selection_policy([14, 7, 3, 1]))


def test_build_config_defaults_and_single_horizon_mode():
    shared = cfg.build_config(**BASE)
    assert shared["evaluation_horizons"] == [1, 3, 7, 14] and shared["purge_gap_days"] == 14 and shared["computed_label_horizons"] == [1, 3, 7, 14]
    assert shared["split_selection_policy"]["horizon_candidates_desc"] == [14, 7, 3, 1]
    assert shared["pass_requirements"]["required_label_splits"] == {"h1": ["train", "validation", "test"]}
    single = cfg.build_config(**BASE, evaluation_horizons=[7])
    assert single["evaluation_horizons"] == [7] and single["purge_gap_days"] == 7 and single["computed_label_horizons"] == [1, 3, 7, 14]
    assert single["split_selection_policy"]["horizon_candidates_desc"] == [7]
    assert single["pass_requirements"]["required_label_splits"] == {"h7": ["train", "validation", "test"]}
    assert cfg.config_sha256(shared) != cfg.config_sha256(single)                       # whitelist nam TRONG identity
    assert cfg.build_config(**BASE, evaluation_horizons=[7], purge_gap_days=14)["purge_gap_days"] == 14   # purge lon hon van hop le (sensitivity)


@pytest.mark.parametrize("kwargs", [dict(evaluation_horizons=[7], purge_gap_days=3), dict(evaluation_horizons=[]), dict(evaluation_horizons=[2]),
                                    dict(purge_gap_days=5)])
def test_invalid_horizon_config_fails_before_anything_is_built(kwargs):
    with pytest.raises(ValueError):
        cfg.build_config(**BASE, **kwargs)


def _row(config):
    return {"build_config_sha256": cfg.config_sha256(config), "import_batch_id": config["import_batch_id"], "reference_algorithm_version": config["reference_algorithm_version"],
            "label_config_sha256": config["label_config_sha256"], "feature_config_sha256": config["feature_config_sha256"], "purge_gap_days": config["purge_gap_days"],
            "random_seed": config["random_seed"]}


def test_verify_config_projection_rejects_tampered_horizon_contract():
    config = cfg.build_config(**BASE, evaluation_horizons=[7])
    assert cfg.verify_config_projection(config, _row(config)) == []
    for mutate, text in ((lambda c: c.update(evaluation_horizons=[]), "hop dong horizon"),
                         (lambda c: c.update(evaluation_horizons=[7, 14]), "hop dong horizon"),                       # candidates khong khop whitelist
                         (lambda c: c.update(purge_gap_days=3), "hop dong horizon"),
                         (lambda c: c.update(computed_label_horizons=[7]), "computed_label_horizons")):
        tampered = json.loads(json.dumps(config))
        mutate(tampered)
        problems = cfg.verify_config_projection(tampered, {**_row(tampered), "build_config_sha256": cfg.config_sha256(tampered)})
        assert any(text in p for p in problems), (text, problems)


# ----------------------------------------------------------------- splitter: chi xet horizon duoc phep, khong "ready" cho K bang H khac
def _policy(horizons):
    return cfg.split_selection_policy(horizons)


def test_split_candidates_only_include_the_allowed_horizon_and_infeasible_means_fallback():
    import datetime as dt

    audit_calls: list[int] = []

    def evaluate(plan, horizon):
        audit_calls.append(horizon)
        return {"status": "primary_eligible", "failed_gates": [], "splits": {}, "shortfall": {}}

    plan = plan_split(dt.date(2026, 8, 21), dt.date(2026, 10, 4), policy=_policy([7]), purge_gap_days=7, evaluate=evaluate)       # 45 ngay < 77
    assert plan.feasible_horizon is None and plan.policy_path == "fallback_ratio"
    assert [c["horizon"] for c in plan.candidates] == [7] and plan.candidates[0]["needed_days"] == 77 and audit_calls == []   # khong danh gia horizon khac
    wide = plan_split(dt.date(2026, 8, 21), dt.date(2026, 12, 5), policy=_policy([7]), purge_gap_days=7, evaluate=evaluate)
    assert wide.feasible_horizon == 7 and wide.policy_path == "gate_driven:H=7" and audit_calls == [7]


@pytest.mark.parametrize("horizon, needed", [(1, 47), (3, 57), (7, 77), (14, 98)])
def test_needed_days_with_purge_equal_to_k_match_the_design(horizon, needed):
    import datetime as dt

    plan = plan_split(dt.date(2026, 8, 21), dt.date(2026, 8, 21) + dt.timedelta(days=needed - 1), policy=_policy([horizon]), purge_gap_days=horizon,
                      evaluate=lambda p, h: {"status": "primary_eligible", "failed_gates": [], "splits": {}, "shortfall": {}})
    assert plan.feasible_horizon == horizon and plan.candidates[0]["needed_days"] == needed
    short = plan_split(dt.date(2026, 8, 21), dt.date(2026, 8, 21) + dt.timedelta(days=needed - 2), policy=_policy([horizon]), purge_gap_days=horizon,
                       evaluate=lambda p, h: {"status": "primary_eligible", "failed_gates": [], "splits": {}, "shortfall": {}})
    assert short.feasible_horizon is None and short.candidates[0]["calendar_days_short"] == 1


@pytest.mark.parametrize("horizon", [1, 3, 7, 14])
def test_purge_is_effective_no_train_label_crosses_the_boundary_and_gaps_are_at_least_k(horizon):
    """Muc tieu leakage: khoang cach giua cac split >= K ngay va khong mau train nao co d+K >= validation_start duoc coi la label_usable."""
    import datetime as dt

    days = 120
    first = dt.date(2026, 8, 21)
    plan = plan_split(first, first + dt.timedelta(days=days - 1), policy=_policy([horizon]), purge_gap_days=horizon,
                      evaluate=lambda p, h: {"status": "primary_eligible", "failed_gates": [], "splits": {}, "shortfall": {}})
    assert plan.feasible_horizon == horizon
    assert (plan.validation_start - plan.train_end).days - 1 >= horizon and (plan.test_start - plan.validation_end).days - 1 >= horizon
    dates = pd.date_range(first, periods=days)
    samples = pd.DataFrame({"hotel_id": "h", "city": "Hà Nội", "canonical_series_id": "s", "vn_observation_date": dates, f"has_label_h{horizon}": True})
    frame = candidate_frame(samples, plan, horizon)
    usable_train = frame[(frame["split"] == "train") & frame[f"label_usable_h{horizon}"]]
    assert (usable_train["vn_observation_date"] + pd.Timedelta(days=horizon) < pd.Timestamp(plan.validation_start)).all()
    usable_val = frame[(frame["split"] == "validation") & frame[f"label_usable_h{horizon}"]]
    assert (usable_val["vn_observation_date"] + pd.Timedelta(days=horizon) < pd.Timestamp(plan.test_start)).all()
    assert not frame[(frame["split"] == "train") & (frame["vn_observation_date"] + pd.Timedelta(days=horizon) >= pd.Timestamp(plan.validation_start))][f"label_usable_h{horizon}"].any()


# ----------------------------------------------------------------- sufficiency: horizon ngoai whitelist => not_evaluated
def test_sufficiency_marks_non_evaluated_horizons_and_evaluates_only_the_allowed():
    frame = pd.DataFrame({"hotel_id": ["h1"], "city": ["Hà Nội"], "canonical_series_id": ["s"], "vn_observation_date": pd.to_datetime(["2026-09-01"]),
                          "split": ["train"]})
    for k in HORIZONS:
        frame[f"label_usable_h{k}"], frame[f"has_label_h{k}"] = True, True
    report = sufficiency_report(frame, cfg.REGISTERED_SUFFICIENCY_GATES, [7])
    assert report["evaluation_horizons"] == [7]
    assert report["horizons"]["h7"]["status"] == "exploratory" and "splits" in report["horizons"]["h7"]
    for other in ("h1", "h3", "h14"):
        assert report["horizons"][other]["status"] == "not_evaluated" and "splits" not in report["horizons"][other]
    assert sufficiency_report(frame, cfg.REGISTERED_SUFFICIENCY_GATES)["horizons"]["h1"]["status"] == "exploratory"           # mac dinh: ca bon


# ----------------------------------------------------------------- dataset_contract.json + validation
def _contract_inputs(tmp_path, *, purpose="rehearsal", horizons=(7,)):
    config = cfg.build_config(**{**BASE, "purpose": purpose}, evaluation_horizons=list(horizons))
    sufficiency = {"horizons": {f"h{k}": {"status": "primary_eligible" if (k in horizons and purpose == "official") else ("exploratory" if k in horizons else "not_evaluated")}
                                for k in HORIZONS}}
    split_report = {"plan": {"train_start": "2026-08-21", "train_end": "2026-09-30", "validation_start": "2026-10-08", "validation_end": "2026-10-15",
                             "test_start": "2026-10-23", "test_end": "2026-10-30", "purge_gap_days": 7, "policy_path": "gate_driven:H=7", "feasible_horizon": 7}}
    contract = dataset_contract(dataset_version="ds_20261006_t_h7", config=config, split_report=split_report, sufficiency=sufficiency, calendar_sha=config["calendar_input"]["sha256"])
    out = tmp_path / "ds"
    out.mkdir(parents=True)
    (out / CONTRACT_NAME).write_text(json.dumps(contract), encoding="utf-8")
    (out / "sufficiency_report.json").write_text(json.dumps(sufficiency), encoding="utf-8")
    return out, config, contract, {CONTRACT_NAME: {"file_sha256": "a" * 64}}


def test_dataset_contract_is_deterministic_and_carries_the_identity(tmp_path):
    out, config, contract, _ = _contract_inputs(tmp_path)
    again = dataset_contract(dataset_version="ds_20261006_t_h7", config=config, split_report={"plan": contract["split_plan"]},
                             sufficiency=json.loads((out / "sufficiency_report.json").read_text(encoding="utf-8")), calendar_sha=config["calendar_input"]["sha256"])
    assert again == contract and contract["contract_version"] == CONTRACT_VERSION
    assert contract["evaluation_horizons"] == [7] and contract["computed_label_horizons"] == [1, 3, 7, 14] and contract["purge_gap_days"] == 7
    assert contract["build_config_sha256"] == cfg.config_sha256(config) and contract["builder_code_sha256"] == "a" * 64
    assert contract["sufficiency_status"]["h7"] == "exploratory" and contract["sufficiency_status"]["h1"] == "not_evaluated"
    assert "created_at" not in json.dumps(contract)                                    # khong timestamp: tai lap duoc


def test_contract_check_accepts_a_consistent_artifact_and_rejects_every_drift(tmp_path):
    out, config, contract, stored = _contract_inputs(tmp_path)
    assert _contract_check(out, config, stored, dataset_version="ds_20261006_t_h7")["ok"] is True
    assert _contract_check(out, config, {}, dataset_version="ds_20261006_t_h7")["ok"] is False                       # khong co trong checksum DB
    assert _contract_check(out, config, stored, dataset_version="ds_other")["ok"] is False                            # dataset_version khac
    bad_plan = {**contract["split_plan"], "validation_start": "2026-10-01"}                                              # khoang cach train->validation = 0 < purge 7
    swapped = {**contract["split_plan"], "train_end": "2026-11-30"}                                                      # sai thu tu
    for key, value in (("evaluation_horizons", [1]), ("purge_gap_days", 14), ("builder_code_sha256", "b" * 64), ("purpose", "official"),
                       ("sufficiency_status", {"h1": "exploratory"}), ("builder_version", "dataset-builder-0.0.0"), ("build_config_sha256", "0" * 64),
                       ("calendar_sha256", "3" * 64), ("split_plan", bad_plan), ("split_plan", swapped), ("split_plan", {}),
                       ("split_plan", {**contract["split_plan"], "test_start": "not-a-date"}), ("purpose", "bogus")):
        (out / CONTRACT_NAME).write_text(json.dumps({**contract, key: value}), encoding="utf-8")
        assert _contract_check(out, config, stored, dataset_version="ds_20261006_t_h7")["ok"] is False, key
    (out / CONTRACT_NAME).write_text("not json", encoding="utf-8")
    assert _contract_check(out, config, stored, dataset_version="ds_20261006_t_h7")["ok"] is False
    (out / CONTRACT_NAME).unlink()
    assert _contract_check(out, config, stored, dataset_version="ds_20261006_t_h7")["ok"] is False


def test_official_requires_every_evaluation_horizon_primary_eligible_but_rehearsal_does_not(tmp_path):
    out, config, _, _ = _contract_inputs(tmp_path, purpose="rehearsal", horizons=(7,))
    assert _official_gate_check(out, config)["ok"] is True                              # rehearsal: exploratory van PASS
    out2, config2, _, _ = _contract_inputs(tmp_path / "o", purpose="official", horizons=(7,))
    assert _official_gate_check(out2, config2)["ok"] is True                            # official + primary_eligible
    (out2 / "sufficiency_report.json").write_text(json.dumps({"horizons": {"h7": {"status": "exploratory"}}}), encoding="utf-8")
    failing = _official_gate_check(out2, config2)
    assert failing["ok"] is False and failing["detail"]["evaluation_horizon_status"] == {"h7": "exploratory"}
    (out2 / "sufficiency_report.json").unlink()
    assert _official_gate_check(out2, config2)["ok"] is False                           # thieu bao cao => khong PASS official
    multi, mconfig, _, _ = _contract_inputs(tmp_path / "m", purpose="official", horizons=(1, 7))
    (multi / "sufficiency_report.json").write_text(json.dumps({"horizons": {"h1": {"status": "primary_eligible"}, "h7": {"status": "exploratory"}}}), encoding="utf-8")
    assert _official_gate_check(multi, mconfig)["ok"] is False                          # MOI evaluation horizon phai dat, khong chi mot


# ----------------------------------------------------------------- training: verify_dataset doc + kiem contract
def test_verify_dataset_reads_the_contract_and_exposes_whitelist(tmp_path):
    ds = make_dataset(tmp_path, n_days=30, n_series=4, version="ds_c1", evaluation_horizons=(7,))
    meta = verify_dataset(ds)
    assert meta["contract"]["evaluation_horizons"] == [7] and meta["contract"]["purge_gap_days"] == 7 and meta["contract"]["purpose"] == "rehearsal"
    assert len(meta["contract"]["sha256"]) == 64 and "dataset_contract.json" in meta["verified_file_sha256"]


@pytest.mark.parametrize("override, message", [
    ({"evaluation_horizons": []}, "evaluation_horizons"), ({"evaluation_horizons": [7, 7]}, "evaluation_horizons"), ({"evaluation_horizons": [5]}, "evaluation_horizons"),
    ({"evaluation_horizons": [True]}, "evaluation_horizons"), ({"evaluation_horizons": "7"}, "evaluation_horizons"),
    ({"purge_gap_days": 3, "evaluation_horizons": [7]}, "purge_gap_days"), ({"contract_version": 2}, "contract_version"),
    ({"dataset_version": "ds_other"}, "khac ten thu muc"), ({"purpose": None}, "purpose"),
    # noi dung hop dong (GPT file 52 muc 3): computed horizons, purpose enum, hash hop le, hash lich, split, sufficiency khop bao cao
    ({"computed_label_horizons": [7]}, "computed_label_horizons"), ({"purpose": "final"}, "purpose='final'"),
    ({"builder_code_sha256": "xyz"}, "builder_code_sha256 khong phai 64 hex"), ({"build_config_sha256": None}, "build_config_sha256 khong phai 64 hex"),
    ({"builder_version": ""}, "builder_version thieu"), ({"calendar_sha256": "9" * 64}, "calendar_sha256"),
    ({"split_plan": None}, "split_plan thieu"), ({"split_plan": {"policy_path": "fallback_ratio"}}, "split_plan thieu/sai ngay ISO"),
    ({"sufficiency_status": {"h1": "not_evaluated"}}, "sufficiency_status phai co dung khoa"),
    ({"sufficiency_status": {"h1": "not_evaluated", "h3": "not_evaluated", "h7": "primary_eligible", "h14": "not_evaluated"}}, "sufficiency_report.json"),
    ({"sufficiency_status": {"h1": "exploratory", "h3": "not_evaluated", "h7": "exploratory", "h14": "not_evaluated"}}, "khong khop evaluation_horizons"),
])
def test_verify_dataset_rejects_an_invalid_contract(tmp_path, override, message):
    ds = make_dataset(tmp_path, n_days=30, n_series=4, version="ds_c2", evaluation_horizons=(7,))
    contract = json.loads((ds / CONTRACT_NAME).read_text(encoding="utf-8"))
    (ds / CONTRACT_NAME).write_text(json.dumps({**contract, **override}), encoding="utf-8")
    write_checksums(ds, rows=len(pd.read_parquet(ds / "samples.parquet")))             # ke tan cong ghi lai ca checksum => chi kiem noi dung hop dong bat duoc
    with pytest.raises(DatasetVerificationError, match=message):
        verify_dataset(ds)


def test_verify_dataset_rejects_split_dates_that_are_out_of_order_or_closer_than_the_purge(tmp_path):
    ds = make_dataset(tmp_path, n_days=30, n_series=4, version="ds_c8", evaluation_horizons=(7,))
    contract = json.loads((ds / CONTRACT_NAME).read_text(encoding="utf-8"))
    rows = len(pd.read_parquet(ds / "samples.parquet"))
    cases = [({"validation_start": contract["split_plan"]["train_end"]}, "sai thu tu"),                                  # validation bat dau ngay train ket thuc
             ({"validation_start": "2026-10-20"}, "khoang cach giua cac split"),                                          # train_end 10-16 => chi cach 3 ngay < purge 7
             ({"purge_gap_days": 14}, "split_plan.purge_gap_days")]
    for override, message in cases:
        (ds / CONTRACT_NAME).write_text(json.dumps({**contract, "split_plan": {**contract["split_plan"], **override}}), encoding="utf-8")
        write_checksums(ds, rows=rows)
        with pytest.raises(DatasetVerificationError, match=message):
            verify_dataset(ds)
    (ds / CONTRACT_NAME).write_text(json.dumps(contract), encoding="utf-8")
    write_checksums(ds, rows=rows)
    assert verify_dataset(ds)["contract"]["evaluation_horizons"] == [7]                                                  # hop dong goc van hop le


def test_verify_dataset_rejects_a_policy_path_with_a_feasible_horizon_outside_the_whitelist(tmp_path):
    ds = make_dataset(tmp_path, n_days=30, n_series=4, version="ds_c9", evaluation_horizons=(7,))
    contract = json.loads((ds / CONTRACT_NAME).read_text(encoding="utf-8"))
    (ds / CONTRACT_NAME).write_text(json.dumps({**contract, "split_plan": {**contract["split_plan"], "policy_path": "gate_driven:H=14", "feasible_horizon": 14}}), encoding="utf-8")
    write_checksums(ds, rows=len(pd.read_parquet(ds / "samples.parquet")))
    with pytest.raises(DatasetVerificationError, match="feasible_horizon"):
        verify_dataset(ds)


def test_verify_dataset_rejects_missing_tampered_and_non_json_contract(tmp_path):
    ds = make_dataset(tmp_path, n_days=30, n_series=4, version="ds_c3", evaluation_horizons=(7,))
    original = (ds / CONTRACT_NAME).read_bytes()
    (ds / CONTRACT_NAME).write_bytes(original + b" ")
    with pytest.raises(DatasetVerificationError, match="dataset_contract.json: file_sha256 that"):
        verify_dataset(ds)
    (ds / CONTRACT_NAME).write_bytes(b"not json")
    write_checksums(ds, rows=len(pd.read_parquet(ds / "samples.parquet")))
    with pytest.raises(DatasetVerificationError, match="khong doc duoc JSON"):
        verify_dataset(ds)
    (ds / CONTRACT_NAME).unlink()
    with pytest.raises(DatasetVerificationError, match="dataset_contract.json: file khong ton tai"):
        verify_dataset(ds)


# ----------------------------------------------------------------- training CLI: whitelist truoc khi tao run
def _load_cli():
    spec = importlib.util.spec_from_file_location("train_models", SCRIPTS / "train_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli(module, monkeypatch, ds, tmp_path, *extra):
    import training.runner as runner_module

    monkeypatch.setattr(runner_module, "require_known_provenance", lambda *a, **k: None)   # cay git dang dirty khi phat trien; chi kiem logic whitelist/pham vi
    monkeypatch.setattr(runner_module, "require_lineage", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["train_models.py", "--dataset-dir", str(ds), "--models", "ridge", "--output-root", str(tmp_path / "models"), *extra])
    return module.main()


def test_official_cli_refuses_a_horizon_outside_the_whitelist_before_creating_anything(tmp_path, monkeypatch, capsys):
    module = _load_cli()
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_c4", evaluation_horizons=(1,))
    assert _run_cli(module, monkeypatch, ds, tmp_path, "--horizons", "7", "--run-id", "r1", "--official") == 2
    assert "ngoai evaluation_horizons [1]" in capsys.readouterr().err
    assert not (tmp_path / "models").exists()


def test_non_official_cli_may_run_outside_the_whitelist_but_flags_the_report(tmp_path, monkeypatch):
    module = _load_cli()
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_c5", evaluation_horizons=(1,))
    assert _run_cli(module, monkeypatch, ds, tmp_path, "--horizons", "7", "--run-id", "r1") == 0
    run = tmp_path / "models" / "ds_c5" / "r1"
    report = json.loads((run / "h7_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert report["outside_evaluation_whitelist"] is True and report["dataset"]["evaluation_horizons"] == [1] and report["dataset"]["purge_gap_days"] == 1
    assert manifest["evaluation_whitelist"] == [1] and manifest["horizons_outside_whitelist"] == [7] and manifest["official"] is False


def test_inside_the_whitelist_the_report_is_not_flagged_and_default_horizons_follow_the_whitelist(tmp_path, monkeypatch):
    module = _load_cli()
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_c6", evaluation_horizons=(7,), purpose="official", status="primary_eligible")
    assert _run_cli(module, monkeypatch, ds, tmp_path, "--horizons", "7", "--run-id", "r1", "--official") == 0
    report = json.loads((tmp_path / "models" / "ds_c6" / "r1" / "h7_report.json").read_text(encoding="utf-8"))
    assert report["outside_evaluation_whitelist"] is False
    # khong chi dinh --horizons: chi horizon cua whitelist (config co 1,3,7,14; dataset gia chi co cot h7 nen chi chay 7 duoc)
    assert _run_cli(module, monkeypatch, ds, tmp_path, "--run-id", "r2") == 0
    manifest = json.loads((tmp_path / "models" / "ds_c6" / "r2" / "run_manifest.json").read_text(encoding="utf-8"))
    assert [h["horizon"] for h in manifest["horizons"]] == [7] and manifest["expected_horizons"] == [7]


def test_official_cli_needs_an_official_contract_with_every_requested_horizon_primary_eligible(tmp_path, monkeypatch, capsys):
    module = _load_cli()
    rehearsal = make_dataset(tmp_path / "a", n_days=110, n_series=24, version="ds_o1", evaluation_horizons=(7,))                      # purpose=rehearsal
    assert _run_cli(module, monkeypatch, rehearsal, tmp_path, "--horizons", "7", "--run-id", "r1", "--official") == 2
    assert "purpose=official" in capsys.readouterr().err and not (tmp_path / "models").exists()                                    # khong tao thu muc nao
    exploratory = make_dataset(tmp_path / "b", n_days=110, n_series=24, version="ds_o2", evaluation_horizons=(7,), purpose="official", status="exploratory")
    assert _run_cli(module, monkeypatch, exploratory, tmp_path, "--horizons", "7", "--run-id", "r1", "--official") == 2
    assert "chua primary_eligible" in capsys.readouterr().err and not (tmp_path / "models").exists()
    dev = make_dataset(tmp_path / "c", n_days=110, n_series=24, version="ds_o3", evaluation_horizons=(7,), purpose="dev", status="primary_eligible")
    assert _run_cli(module, monkeypatch, dev, tmp_path, "--horizons", "7", "--run-id", "r1", "--official") == 2                      # primary_eligible nhung khong phai purpose official
    assert not (tmp_path / "models").exists()
    good = make_dataset(tmp_path / "d", n_days=110, n_series=24, version="ds_o4", evaluation_horizons=(7,), purpose="official", status="primary_eligible")
    assert _run_cli(module, monkeypatch, good, tmp_path, "--horizons", "7", "--run-id", "r1", "--official") == 0
    report = json.loads((tmp_path / "models" / "ds_o4" / "r1" / "h7_report.json").read_text(encoding="utf-8"))
    assert report["target_assessment"] == "official" and report["meets_project_target_accuracy_at_20pct"] in (True, False)


def test_non_official_report_has_no_target_conclusion_even_inside_the_whitelist(tmp_path, monkeypatch):
    module = _load_cli()
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_o5", evaluation_horizons=(7,))                           # rehearsal + exploratory, trong whitelist
    assert _run_cli(module, monkeypatch, ds, tmp_path, "--horizons", "7", "--run-id", "r1") == 0
    run = tmp_path / "models" / "ds_o5" / "r1"
    report = json.loads((run / "h7_report.json").read_text(encoding="utf-8"))
    assert report["outside_evaluation_whitelist"] is False and report["evaluation_status"] == "exploratory"
    assert report["meets_project_target_accuracy_at_20pct"] is None and report["target_assessment"] == "exploratory_not_official"
    outside = make_dataset(tmp_path / "src2", n_days=110, n_series=24, version="ds_o6", evaluation_horizons=(1,), purpose="official", status="primary_eligible")
    assert _run_cli(module, monkeypatch, outside, tmp_path, "--horizons", "7", "--run-id", "r1") == 0                                   # ngoai whitelist: tham do, du la hop dong official
    report2 = json.loads((tmp_path / "models" / "ds_o6" / "r1" / "h7_report.json").read_text(encoding="utf-8"))
    assert report2["outside_evaluation_whitelist"] is True and report2["meets_project_target_accuracy_at_20pct"] is None
    assert report2["target_assessment"] == "exploratory_not_official"


def test_bundle_stores_the_validation_selected_fallback_and_the_fixed_encoding(tmp_path, monkeypatch):
    import joblib

    module = _load_cli()
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_o7", evaluation_horizons=(7,))
    assert _run_cli(module, monkeypatch, ds, tmp_path, "--horizons", "7", "--run-id", "r1") == 0
    run = tmp_path / "models" / "ds_o7" / "r1"
    report = json.loads((run / "h7_report.json").read_text(encoding="utf-8"))
    bundle = joblib.load(next(run.glob("h7_model_*.joblib")))
    assert bundle["selection"] == report["selection"] and bundle["deployment_fallback"] == report["deployment_fallback"]
    assert bundle["deployment_fallback"]["decided_on"] == "validation" and bundle["target_assessment"] == "exploratory_not_official"
    assert bundle["encoder_categories"] == report["encoding"]["domains"] and report["encoding"]["method"] == "fixed_domain"
    assert set(report["encoding"]["unknown_counts"]) == {"train", "validation", "test"}


def test_default_horizons_with_no_overlap_fail_cleanly(tmp_path, monkeypatch, capsys):
    module = _load_cli()
    ds = make_dataset(tmp_path / "src", n_days=30, n_series=4, version="ds_c7", evaluation_horizons=(7,))
    contract = json.loads((ds / CONTRACT_NAME).read_text(encoding="utf-8"))
    monkeypatch.setattr(module, "load_config", lambda path=None: {**__import__("training.config", fromlist=["load_config"]).load_config(), "horizons": [1, 3]})
    assert _run_cli(module, monkeypatch, ds, tmp_path, "--run-id", "r1") == 2
    assert "khong horizon nao" in capsys.readouterr().err and contract["evaluation_horizons"] == [7]
    assert not (tmp_path / "models").exists()
