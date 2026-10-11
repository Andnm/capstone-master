"""audit_tools/repair_heavy_plan_v1.py — spec/plan THUAN cho clone / init+verify / build: argv chinh xac, khong co co huy, dong dong import tinh duoc ghim, db_scope chinh xac + protected, ngan sach tong du,
bao cao verify NGHIEM NGAT + chuoi receipt H2 (tao bang verifier va Runner that, khong fixture `checks` tu che), thu muc dataset duoc niem phong qua Runner."""
from __future__ import annotations

import hashlib
import os
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1] / "audit_tools"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hp = _load(Path(os.environ.get("HEAVY_PLAN_UNDER_TEST", str(TOOLS / "repair_heavy_plan_v1.py"))), "repair_heavy_plan_v1")                    # ghi de CHI de chay mutation harness
r2t = _load(HERE / "test_repair_phase_runner_v2.py", "runner2_tests_for_heavy")
vt = _load(HERE / "test_repair_verify_init_v1.py", "verify_tests_for_heavy")
rr, rp, vi = hp.rr, hp.rp, hp.vi
PY = sys.executable
APP = "hotel_app"
DATABASES = ["hotel_price_intel", "hotel_price_intel_fullscan_20260924", "warehouse_20260916_2src", "warehouse_20261004_3src", "warehouse_dsdev_20261004_3src"]
V1, V3 = hp.VERSIONS["h1"][0], hp.VERSIONS["h3"][0]


def write_q0(tmp_path, **details_override):
    details = {"app_user": APP, "databases": list(DATABASES), "databases_truncated": False, "ids_increase": True, "successive_ids": [1, 2], "errors": []}
    details.update(details_override)
    path = tmp_path / "q0.json"
    path.write_text(json.dumps({"kind": "q0_selftest_receipt", "details": details}), encoding="utf-8")
    return path


def prediction_files(tmp_path):
    out = {}
    for key in ("h1", "h3"):
        path = tmp_path / f"prediction_{key}.json"
        path.write_text(json.dumps(vt.RECEIPT), encoding="utf-8")
        out[key] = path
    return out


# =========================================================================== dong dong import tinh
def tree(tmp_path):
    ml, backend = tmp_path / "ml", tmp_path / "backend"
    (ml / "pkg").mkdir(parents=True)
    (backend / "app" / "sub").mkdir(parents=True)
    (ml / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (ml / "pkg" / "a.py").write_text("from . import b\nfrom app.sub import deep\n", encoding="utf-8")
    (ml / "pkg" / "b.py").write_text("import json\n\ndef late():\n    from app.lazy import thing\n    return thing\n", encoding="utf-8")
    (ml / "pkg" / "unused.py").write_text("x = 1\n", encoding="utf-8")
    (backend / "app" / "__init__.py").write_text("", encoding="utf-8")
    (backend / "app" / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (backend / "app" / "sub" / "deep.py").write_text("from ..other import helper\n", encoding="utf-8")
    (backend / "app" / "other.py").write_text("import os\n", encoding="utf-8")
    (backend / "app" / "lazy.py").write_text("thing = 1\n", encoding="utf-8")
    script = ml / "run.py"
    script.write_text("from pkg import a\nimport requests\n", encoding="utf-8")
    return ml, backend, script


def test_static_closure_follows_relative_absolute_and_function_level_imports_and_parent_packages(tmp_path, monkeypatch):
    ml, backend, script = tree(tmp_path)
    monkeypatch.setattr(hp, "ML", ml)
    monkeypatch.setattr(hp, "BACKEND", backend)
    found = {p.relative_to(tmp_path).as_posix() for p in hp.static_closure(script)}
    assert found == {"ml/run.py", "ml/pkg/__init__.py", "ml/pkg/a.py", "ml/pkg/b.py", "backend/app/__init__.py", "backend/app/sub/__init__.py", "backend/app/sub/deep.py", "backend/app/other.py", "backend/app/lazy.py"}
    assert "ml/pkg/unused.py" not in found                                                                       # chi tep THUC SU duoc import; thu vien ngoai (json/requests) khong vao
    assert hp.static_closure(script) == sorted(hp.static_closure(script), key=str)


def test_the_real_heavy_scripts_have_the_expected_core_modules_in_their_closure():
    clone = {p.relative_to(hp.PROJECT).as_posix() for p in hp.static_closure(hp.ML / "scripts" / "clone_warehouse_for_dev.py")}
    build = {p.relative_to(hp.PROJECT).as_posix() for p in hp.static_closure(hp.ML / "scripts" / "build_dataset.py")}
    assert {"ml/scripts/clone_warehouse_for_dev.py", "ml/dataset_builder/db.py", "backend/app/warehouse/bootstrap.py", "backend/app/warehouse/connection.py", "backend/app/core/config.py"} <= clone
    assert {"ml/scripts/build_dataset.py", "ml/dataset_builder/runner.py", "ml/dataset_builder/code_identity.py", "backend/app/warehouse/canonicalize.py"} <= build
    assert not any("audit_tools" in p for p in clone | build)


# =========================================================================== Q0 facts, db block, ten
def test_q0_facts_read_the_measured_user_and_databases_and_the_receipt_hash(tmp_path):
    path = write_q0(tmp_path)
    assert hp.q0_facts(path) == {"app_user": APP, "databases": sorted(DATABASES), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.mark.parametrize("override", [{"app_user": ""}, {"app_user": None}, {"databases": []}, {"databases": "x"}, {"databases": [1]}, {"databases_truncated": True}, {"databases_truncated": None}])
def test_q0_facts_reject_a_receipt_without_a_complete_user_and_database_list(tmp_path, override):
    with pytest.raises(ValueError, match="q0_receipt_missing_user_or_databases"):
        hp.q0_facts(write_q0(tmp_path, **override))


def test_q0_facts_reject_a_non_receipt(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"kind": "other", "details": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="q0_receipt_malformed"):
        hp.q0_facts(path)


def test_the_explicit_deny_list_names_both_known_operational_databases_independently_of_q0():
    assert hp.OPERATIONAL_DENY == ("hotel_price_intel", "hotel_price_intel_fullscan_20260924")
    only_deny = hp.db_block(app_user=APP, scope=["a"], q0_databases=[])
    assert only_deny["protected_dbs"] == ["hotel_price_intel", "hotel_price_intel_fullscan_20260924"]            # deny toi thieu KHONG phu thuoc Q0 (GPT 78 §5.1)


def test_db_block_is_the_exact_scope_with_protected_equal_to_the_explicit_deny_plus_everything_else_the_q0_saw():
    block = hp.db_block(app_user=APP, scope=["warehouse_dsdev_x"], q0_databases=DATABASES + ["warehouse_dsdev_x"])
    assert block == {"app_user": APP, "db_scope": ["warehouse_dsdev_x"], "protected_dbs": sorted(set(DATABASES))}
    assert "warehouse_dsdev_x" not in block["protected_dbs"]
    clone = hp.db_block(app_user=APP, scope=["warehouse_20261004_3src", "warehouse_dsdev_x"], q0_databases=DATABASES)
    assert clone["db_scope"] == ["warehouse_20261004_3src", "warehouse_dsdev_x"] and "warehouse_20261004_3src" not in clone["protected_dbs"]


def test_db_block_refuses_a_scope_that_overlaps_the_explicit_deny_list_or_has_bad_names():
    for forbidden in hp.OPERATIONAL_DENY:
        with pytest.raises(ValueError, match="db_scope_overlaps_explicit_deny"):
            hp.db_block(app_user=APP, scope=[forbidden, "warehouse_dsdev_x"], q0_databases=DATABASES)             # khong ha deny bang cach dua DB van hanh vao scope
    for bad in ([], ["bad name"], ["x;DROP"], [""]):
        with pytest.raises(ValueError, match="db_scope_invalid"):
            hp.db_block(app_user=APP, scope=bad, q0_databases=DATABASES)


@pytest.mark.parametrize("source,target", [("warehouse_20261004_3src", "warehouse_20261004_3src"), ("warehouse_20261004_3src", "not_dev_target"), ("warehouse_dsdev_a", "warehouse_dsdev_b"),
                                           ("bad name", "warehouse_dsdev_b"), ("warehouse_20261004_3src", "warehouse_dsdev_b;x")])
def test_clone_names_are_validated(source, target):
    with pytest.raises(ValueError):
        hp.clone_spec(python=PY, source=source, target=target, db={"app_user": APP, "db_scope": [source, target], "protected_dbs": ["hotel_price_intel"]})


# =========================================================================== spec
def spec_validates(spec):
    assert rr.validate_spec(spec) == [], spec["name"]


def test_the_clone_spec_has_exact_argv_no_destructive_flag_cwd_ml_and_pinned_closure(tmp_path):
    facts = hp.q0_facts(write_q0(tmp_path))
    db = hp.db_block(app_user=facts["app_user"], scope=[hp.SOURCE_DB, hp.TARGET_DB], q0_databases=facts["databases"])
    spec = hp.clone_spec(python=PY, source=hp.SOURCE_DB, target=hp.TARGET_DB, db=db)
    spec_validates(spec)
    assert spec["argv"] == [PY, str(hp.ML / "scripts" / "clone_warehouse_for_dev.py"), "--source", hp.SOURCE_DB, "--target", hp.TARGET_DB]
    assert not (rr.FORBIDDEN_ARGV & set(spec["argv"])) and spec["kind"] == "write" and spec["cwd"] == str(hp.ML) and spec["deadline_s"] == 900.0
    assert spec["env"]["PYTHONUNBUFFERED"] == "1" and "DATASET_BUILDER_ANOMALY_REGISTRY" not in spec["env"] and set(spec["env"]) <= rr.ENV_ALLOWLIST
    pinned = set(spec["required_files"])
    assert PY in pinned and str(hp.SETUP_SQL) in pinned and str(hp.ML / "scripts" / "clone_warehouse_for_dev.py") in pinned and str(hp.BACKEND / "app" / "warehouse" / "bootstrap.py") in pinned
    assert all(rr.file_sha256(path) == sha for path, sha in spec["required_files"].items())


def test_the_init_specs_reproduce_the_old_invocation_with_new_version_names_the_n1_policy_flag_and_pin_the_builder_identity_files():
    """10/10: lan chay that cua `ds_20261009_nf1` thieu `--n1-policy` => config != du doan => verify FAIL. Lenh cu (config dev1b/dev3b co `n1_policy`, 3895 byte) = lenh nay + ten version moi."""
    db = {"app_user": APP, "db_scope": [hp.TARGET_DB], "protected_dbs": ["hotel_price_intel"]}
    for key, (version, horizon) in hp.VERSIONS.items():
        spec = hp.init_spec(python=PY, name=f"ph4_init_{key}", target=hp.TARGET_DB, version=version, horizon=horizon, db=db)
        spec_validates(spec)
        assert spec["argv"] == [PY, str(hp.ML / "scripts" / "init_dataset_build.py"), "--database", hp.TARGET_DB, "--dataset-version", version, "--purpose", "dev", "--anomaly-cutoff", "2026-10-05T00:00:00Z",
                                "--evaluation-horizons", str(horizon), "--n1-policy", str(hp.ML / "policies" / "n1" / "n1_policy_v1.json")]
        assert "--dry-run" not in spec["argv"] and "--random-seed" not in spec["argv"] and "--exclude-hotel" not in spec["argv"] and spec["argv"].count("--n1-policy") == 1
        pinned = set(spec["required_files"])
        manifest_files = {str(p) for p in hp.builder_manifest_files()}
        assert manifest_files <= pinned and str(hp.REGISTRY_JSON) in pinned and str(hp.HOLIDAYS_CSV) in pinned and len(manifest_files) >= 38


def test_the_n1_policy_directory_files_are_all_pinned_by_hash_in_the_init_and_build_specs(tmp_path):
    db = {"app_user": APP, "db_scope": [hp.TARGET_DB], "protected_dbs": ["hotel_price_intel"]}
    n1_files = [str(p) for p in hp.n1_policy_files()]
    assert str(hp.N1_POLICY_FILE) in n1_files and len(n1_files) >= 5 and all(Path(p).parent == hp.N1_POLICY_DIR for p in n1_files)
    init = hp.init_spec(python=PY, name="ph4_init_h1", target=hp.TARGET_DB, version=V1, horizon=1, db=db)
    report = tmp_path / "verify.json"
    report.write_text("{}", encoding="utf-8")
    build = hp.build_spec(python=PY, name="ph5_build_h1", target=hp.TARGET_DB, version=V1, verify_report=report, db=db)
    for spec in (init, build):
        for path in n1_files:
            assert spec["required_files"].get(path) == rr.file_sha256(path), (spec["name"], path)


def test_the_default_versions_are_the_unburnt_10_10_names_and_never_the_failed_09_10_name():
    assert hp.VERSIONS == {"h1": ("ds_20261010_nf1", 1), "h3": ("ds_20261010_nf3", 3)}
    assert not {v for v, _ in hp.VERSIONS.values()} & {"ds_20261009_nf1", "ds_20261009_nf3"}


def test_the_verify_spec_is_readonly_pins_the_prediction_receipt_and_declares_its_report_as_an_output(tmp_path):
    prediction = prediction_files(tmp_path)["h1"]
    report = tmp_path / "verify_h1.json"
    spec = hp.verify_spec(python=PY, name="ph4_verify_h1", target=hp.TARGET_DB, version=V1, prediction_receipt=prediction, out_report=report)
    spec_validates(spec)
    assert spec["kind"] == "readonly" and "db" not in spec and spec["outputs"] == [str(report)] and str(prediction) in spec["required_files"] and spec["deadline_s"] == 120.0
    assert spec["argv"] == [PY, str(TOOLS / "repair_verify_init_v1.py"), "--db", hp.TARGET_DB, "--dataset-version", V1, "--prediction-receipt", str(prediction), "--out", str(report)]


def test_the_build_spec_is_apply_only_declares_the_dataset_directory_as_a_sealed_output_and_pins_its_provenance(tmp_path):
    report = tmp_path / "verify.json"
    report.write_text("{}", encoding="utf-8")
    pin = tmp_path / "PLAN_BOUND.json"
    pin.write_text("{}", encoding="utf-8")
    db = {"app_user": APP, "db_scope": [hp.TARGET_DB], "protected_dbs": ["hotel_price_intel"]}
    spec = hp.build_spec(python=PY, name="ph5_build_h1", target=hp.TARGET_DB, version=V1, verify_report=report, db=db, h2_pins=[pin])
    spec_validates(spec)
    assert spec["argv"] == [PY, str(hp.ML / "scripts" / "build_dataset.py"), "--database", hp.TARGET_DB, "--dataset-version", V1, "--apply"]
    assert spec["output_dirs"] == [str(hp.DATASET_ROOT / V1)] and "outputs" not in spec and spec["deadline_s"] == 1800.0
    assert str(report) in spec["required_files"] and str(pin) in spec["required_files"]


# =========================================================================== bao cao verify nghiem ngat (GPT 78 H2-REPORT)
def genuine_report(tmp_path, version=V1, *, database=None, prediction=None):
    """Bao cao do CHINH verifier tao tren ket noi gia (khong fixture tu che)."""
    out = tmp_path / f"genuine_{version}.json"
    path = prediction or prediction_files(tmp_path)["h1"]
    rc = vi.main(["--db", database or hp.TARGET_DB, "--dataset-version", version, "--prediction-receipt", str(path), "--out", str(out)], connect=lambda db: vt.FakeConn([vt.manifest_row(dataset_version=version)]))
    assert rc == 0
    return out, path


def rewritten(report_path, tmp_path, name, mutate):
    doc = json.loads(report_path.read_bytes().decode("utf-8"))
    mutate(doc)
    out = tmp_path / name
    out.write_text(json.dumps(doc), encoding="utf-8")
    return out


def test_the_report_made_by_the_verifier_itself_is_accepted_with_eight_true_checks_one_row_and_the_expected_version_and_database(tmp_path):
    report, prediction = genuine_report(tmp_path)
    doc = hp.load_verify_report(report, version=V1, target=hp.TARGET_DB, prediction_receipt=prediction)
    assert set(doc["checks"]) == set(vi.REQUIRED_CHECKS) and len(vi.REQUIRED_CHECKS) == 8 and all(v is True for v in doc["checks"].values())
    assert doc["rows"] == 1 and doc["dataset_version"] == V1 and doc["database"] == hp.TARGET_DB and doc["verdict"] == "PASS"


@pytest.mark.parametrize("name,mutate", [
    ("no_checks", lambda d: d.pop("checks")), ("empty_checks", lambda d: d.update(checks={})), ("arbitrary_checks", lambda d: d.update(checks={"a": True, "b": True})),
    ("integer_truth", lambda d: d.update(checks={k: 1 for k in d["checks"]})), ("one_check_false", lambda d: d["checks"].update(status_running=False)),
    ("extra_check", lambda d: d["checks"].update(extra=True)), ("missing_check", lambda d: d["checks"].pop("json_parsed")),
    ("wrong_database", lambda d: d.update(database="warehouse_other")), ("no_database", lambda d: d.update(database=None)), ("wrong_version", lambda d: d.update(dataset_version="ds_20261009_nf9")),
    ("verdict_fail", lambda d: d.update(verdict="FAIL")), ("wrong_kind", lambda d: d.update(kind="other")), ("rows_two", lambda d: d.update(rows=2)), ("rows_true", lambda d: d.update(rows=True)),
    ("status_pass", lambda d: d.update(status="pass")), ("step_validation", lambda d: d.update(last_completed_step="validation")),
    ("other_sql_set", lambda d: d.update(sql_set_sha256="0" * 64)), ("other_predicted", lambda d: d.update(predicted_config_sha256="0" * 64)),
    ("other_stored", lambda d: d.update(stored_config_sha256="0" * 64)), ("other_recomputed", lambda d: d.update(recomputed_config_sha256="0" * 64)),
    ("other_code", lambda d: d.update(config_builder_code_sha256="0" * 64)), ("other_expected_code", lambda d: d.update(expected_new_code_sha256="0" * 64)),
    ("other_prediction_file", lambda d: d.update(prediction_receipt_sha256="0" * 64)),
])
def test_a_verify_report_that_is_not_exactly_the_verifier_output_for_this_version_target_and_prediction_is_rejected(tmp_path, name, mutate):
    report, prediction = genuine_report(tmp_path)
    bad = rewritten(report, tmp_path, f"bad_{name}.json", mutate)
    with pytest.raises(ValueError, match="verify_report_rejected_for_"):
        hp.load_verify_report(bad, version=V1, target=hp.TARGET_DB, prediction_receipt=prediction)


def test_a_report_for_another_prediction_receipt_or_an_unreadable_report_is_rejected(tmp_path):
    report, prediction = genuine_report(tmp_path)
    other = tmp_path / "other_prediction.json"
    other.write_text(json.dumps({**vt.RECEIPT, "predicted_config_sha256": "9" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="verify_report_rejected_for_"):
        hp.load_verify_report(report, version=V1, target=hp.TARGET_DB, prediction_receipt=other)
    with pytest.raises(ValueError, match="verify_report_unreadable_for_"):
        hp.load_verify_report(tmp_path / "missing.json", version=V1, target=hp.TARGET_DB, prediction_receipt=prediction)
    (tmp_path / "garbage.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="verify_report_unreadable_for_"):
        hp.load_verify_report(tmp_path / "garbage.json", version=V1, target=hp.TARGET_DB, prediction_receipt=prediction)


# =========================================================================== chuoi H2 (Runner THAT voi launcher gia chay verifier THAT) -> build plan
def h2_world(tmp_path):
    q0_path, q0_sha = r2t.write_q0(tmp_path)
    receipts = prediction_files(tmp_path)
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    h2 = hp.init_plan(python=PY, q0_receipt=q0_path, prediction_receipts=receipts, report_dir=report_dir)
    doc = h2["document"]
    env = {"tmp": tmp_path, "script": tmp_path / "unused.py", "plan": tmp_path / "h2_plan", "clock": r2t.FakeClock(), "q0_path": q0_path, "q0_sha": q0_sha}
    env["plan"].mkdir()

    class H2Launcher(r2t.FakeLauncher):
        def launch(self, spec, cap_s, **kw):
            if spec["name"].startswith("ph4_verify_"):
                version = spec["argv"][spec["argv"].index("--dataset-version") + 1]
                assert vi.main(spec["argv"][2:], connect=lambda db: vt.FakeConn([vt.manifest_row(dataset_version=version)])) == 0       # verifier THAT, ket noi gia
            return super().launch(spec, cap_s, **kw)

    runner = r2t.make_runner(env, doc["specs"], launcher=H2Launcher(env["clock"]), observer=r2t.FakeObserver(env["clock"]), settings=doc["settings"])
    results = runner.run_plan()
    assert [r["verdict"] for r in results] == ["PASS"] * 4, [r.get("reasons") for r in results]
    return {"q0": q0_path, "receipts": receipts, "report_dir": report_dir, "reports": {"h1": report_dir / f"verify_{V1}.json", "h3": report_dir / f"verify_{V3}.json"}, "h2_dir": env["plan"],
            "h2_sha": runner.computed_plan_sha, "tmp": tmp_path}


def build(world, **override):
    kwargs = dict(python=PY, q0_receipt=world["q0"], verify_reports=world["reports"], prediction_receipts=world["receipts"], report_dir=world["report_dir"], h2_plan_dir=world["h2_dir"],
                  h2_plan_sha256=world["h2_sha"])
    kwargs.update(override)
    return hp.build_plan(**kwargs)


def test_the_genuine_h2_chain_is_accepted_and_the_build_specs_pin_the_reports_and_the_h2_receipts(tmp_path):
    world = h2_world(tmp_path)
    plan = build(world)
    specs = plan["document"]["specs"]
    assert [s["name"] for s in specs] == ["ph5_build_h1", "ph5_build_h3"] and all(s["output_dirs"] == [str(hp.DATASET_ROOT / v)] for s, v in zip(specs, (V1, V3)))
    for spec, key in zip(specs, ("h1", "h3")):
        pinned = set(spec["required_files"])
        assert str(world["reports"][key]) in pinned and str(world["h2_dir"] / "PLAN_BOUND.json") in pinned and str(world["h2_dir"] / f"receipt_0{2 if key == 'h1' else 4}_ph4_verify_{key}.json") in pinned
        assert spec["db"]["db_scope"] == [hp.TARGET_DB] and hp.SOURCE_DB in spec["db"]["protected_dbs"] and all(d in spec["db"]["protected_dbs"] for d in hp.OPERATIONAL_DENY)


def test_every_h2_negative_is_rejected_before_any_build_spec_exists(tmp_path):
    world = h2_world(tmp_path)
    h2 = world["h2_dir"]
    # --- bao cao: byte khac (van la bao cao hop le ve noi dung nhung KHAC byte voi bien lai) => chuoi tu choi
    extra_key = rewritten(world["reports"]["h1"], tmp_path, "extra.json", lambda d: d.update(note="x"))
    original = world["reports"]["h1"].read_bytes()
    world["reports"]["h1"].write_bytes(extra_key.read_bytes())
    with pytest.raises(ValueError, match="report_bytes_differ_from_the_receipt_output_hash"):
        build(world)
    world["reports"]["h1"].write_bytes(original)
    # --- bao cao thuoc DB / bien lai khac
    other_prediction = tmp_path / "other_prediction.json"
    other_prediction.write_text(json.dumps({**vt.RECEIPT, "predicted_config_sha256": "9" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="verify_report_rejected_for_"):
        build(world, prediction_receipts={"h1": other_prediction, "h3": world["receipts"]["h3"]})
    # --- plan hash H2 sai / sai dinh dang
    with pytest.raises(ValueError, match="h2_plan_bound_hash_mismatch"):
        build(world, h2_plan_sha256="1" * 64)
    with pytest.raises(ValueError, match="h2_plan_sha256_malformed"):
        build(world, h2_plan_sha256="short")
    # --- duong dan bao cao khac duong dan dau ra cua H2
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    copy = elsewhere / f"verify_{V1}.json"
    copy.write_bytes(original)
    with pytest.raises(ValueError, match="verify_report_path_differs_from_the_h2_output_path"):
        build(world, verify_reports={"h1": copy, "h3": world["reports"]["h3"]})
    # --- thu muc H2 co them tep / thieu receipt / co marker QUARANTINE
    (h2 / "QUARANTINE.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="h2_plan_dir_unexpected_content"):
        build(world)
    (h2 / "QUARANTINE.json").unlink()
    saved = (h2 / "receipt_03_ph4_init_h3.json").read_bytes()
    (h2 / "receipt_03_ph4_init_h3.json").unlink()
    with pytest.raises(ValueError, match="h2_plan_dir_unexpected_content"):
        build(world)
    (h2 / "receipt_03_ph4_init_h3.json").write_bytes(saved)
    build(world)                                                                                              # khoi phuc dung byte => chap nhan lai


def test_a_failed_reordered_or_unlinked_or_foreign_h2_receipt_chain_is_rejected(tmp_path):
    world = h2_world(tmp_path)
    h2 = world["h2_dir"]
    path = h2 / "receipt_02_ph4_verify_h1.json"
    good = path.read_bytes()
    for name, mutate, message in (("verdict", lambda d: d.update(verdict="FAIL"), "h2_receipt_rejected: ph4_verify_h1"), ("reasons", lambda d: d.update(reasons=["x"]), "h2_receipt_rejected: ph4_verify_h1"),
                                  ("chain", lambda d: d.update(prev_receipt_sha256="0" * 64), "h2_receipt_rejected: ph4_verify_h1"), ("seq", lambda d: d.update(seq=9), "h2_receipt_rejected: ph4_verify_h1"),
                                  ("phase", lambda d: d.update(phase="ph4_init_h1"), "h2_receipt_rejected: ph4_verify_h1"), ("plan_sha", lambda d: d.update(computed_plan_sha256="2" * 64), "h2_receipt_rejected: ph4_verify_h1"),
                                  ("spec", lambda d: d.update(spec_sha256="3" * 64), "spec_or_argv_hash_differs"), ("argv", lambda d: d.update(argv_sha256="4" * 64), "spec_or_argv_hash_differs"),
                                  ("output", lambda d: d.update(outputs={}), "report_bytes_differ_from_the_receipt_output_hash")):
        doc = json.loads(good.decode("utf-8"))
        mutate(doc)
        path.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            build(world)
    path.write_bytes(good)
    # mot receipt TRUOC bi doi (noi dung) => receipt SAU khong noi duoc
    first = h2 / "receipt_01_ph4_init_h1.json"
    first_good = first.read_bytes()
    doc = json.loads(first_good.decode("utf-8"))
    doc["entrypoint_sha256"] = "5" * 64
    first.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="h2_receipt_rejected: ph4_verify_h1"):
        build(world)
    first.write_bytes(first_good)
    # H2 khac (cung noi dung nhung plan hash khac) khong the dong vai H2 da duyet
    with pytest.raises(ValueError, match="h2_plan_bound_hash_mismatch"):
        build(world, h2_plan_sha256="6" * 64)
    assert build(world)["plan_sha256"]


def test_the_build_consumer_requires_both_versions_and_the_prediction_receipts(tmp_path):
    world = h2_world(tmp_path)
    with pytest.raises(ValueError, match="verify_reports_and_prediction_receipts_must_be_h1_and_h3"):
        build(world, verify_reports={"h1": world["reports"]["h1"]})
    with pytest.raises(ValueError, match="verify_reports_and_prediction_receipts_must_be_h1_and_h3"):
        build(world, prediction_receipts={"h1": world["receipts"]["h1"]})


# =========================================================================== thu muc dataset niem phong QUA RUNNER (GPT 78 H3-DIR)
DEFAULT_LAYOUT = {"samples.parquet": "PAR1", "data_dictionary.csv": "a,b", "reports/validation.json": "{}", "reports/REPORTS_MANIFEST.json": "{}"}


class BuildLauncher(r2t.FakeLauncher):
    """Mo phong 'build thanh cong': tao thu muc dataset (hinh dang that: samples.parquet, reports/...) ngay trong launch. layout=None => khong tao gi; {} => chi tao thu muc rong."""

    def __init__(self, clock, root, layout="default", **kw):
        super().__init__(clock, **kw)
        self.root, self.layout = Path(root), (DEFAULT_LAYOUT if layout == "default" else layout)

    def launch(self, spec, cap_s, **kw):
        if self.layout is not None and spec["name"].startswith("ph5_build_"):
            version = spec["argv"][spec["argv"].index("--dataset-version") + 1]
            (self.root / version).mkdir(parents=True, exist_ok=True)
            for rel, text in self.layout.items():
                target = self.root / version / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
        return super().launch(spec, cap_s, **kw)


def run_h3(tmp_path, monkeypatch, *, layout="default", pre=None, results=None):
    tmp_path.mkdir(exist_ok=True)
    world = h2_world(tmp_path)
    dataset_root = tmp_path / "datasets"
    monkeypatch.setattr(hp, "DATASET_ROOT", dataset_root)
    doc = build(world)["document"]
    q0_sha = hashlib.sha256(world["q0"].read_bytes()).hexdigest()
    env = {"tmp": tmp_path, "script": tmp_path / "unused.py", "plan": tmp_path / "h3_plan", "clock": r2t.FakeClock(), "q0_path": world["q0"], "q0_sha": q0_sha}
    env["plan"].mkdir()
    if pre:
        pre(dataset_root)
    launcher = BuildLauncher(env["clock"], dataset_root, layout=layout, results=results)
    runner = r2t.make_runner(env, doc["specs"], launcher=launcher, observer=r2t.FakeObserver(env["clock"]), settings=doc["settings"])
    return runner.run_plan(), dataset_root, launcher


def test_a_successful_heavy_build_shaped_directory_is_sealed_by_the_runner_with_a_per_file_manifest(tmp_path, monkeypatch):
    results, root, launcher = run_h3(tmp_path, monkeypatch)
    assert [r["verdict"] for r in results] == ["PASS", "PASS"] and launcher.calls and [c[0] for c in launcher.calls] == ["ph5_build_h1", "ph5_build_h3"]
    seal = results[0]["output_dirs"][str(root / V1)]
    expected = {"data_dictionary.csv": hashlib.sha256(b"a,b").hexdigest(), "reports/REPORTS_MANIFEST.json": hashlib.sha256(b"{}").hexdigest(), "reports/validation.json": hashlib.sha256(b"{}").hexdigest(),
                "samples.parquet": hashlib.sha256(b"PAR1").hexdigest()}
    assert seal["kind"] == "directory" and seal["files"] == 4 and seal["manifest"] == expected and seal["aggregate_sha256"] == rr.sha256_hex(rr.canonical_json(expected))
    assert results[0]["outputs"] == {}                                                                           # khong con tep dau ra le; dau ra la thu muc da niem phong


def test_an_existing_dataset_root_old_or_empty_refuses_the_build_before_any_launch(tmp_path, monkeypatch):
    results, root, launcher = run_h3(tmp_path, monkeypatch, pre=lambda r: (r / V1).mkdir(parents=True))
    assert results[0]["verdict"] == "REFUSED" and results[0]["reasons"][0].startswith("output_already_exists") and launcher.calls == [] and len(results) == 1


def test_a_build_that_leaves_no_dataset_directory_or_an_empty_one_is_a_clean_failure_not_a_pass(tmp_path, monkeypatch):
    results, root, launcher = run_h3(tmp_path, monkeypatch, layout=None)
    assert results[0]["verdict"] == "FAIL_CLEAN" and results[0]["reasons"][0].startswith("expected_outputs_missing") and [c[0] for c in launcher.calls] == ["ph5_build_h1"]
    results, root, launcher = run_h3(tmp_path / "again", monkeypatch, layout={})                                # thu muc dataset RONG cung khong phai dau ra hop le
    assert results[0]["verdict"] == "FAIL_CLEAN" and "expected_outputs_missing" in results[0]["reasons"][0]


def test_a_build_that_changes_a_pinned_input_while_it_runs_does_not_pass_even_if_it_produced_a_sealed_directory(tmp_path, monkeypatch):
    world = h2_world(tmp_path)
    monkeypatch.setattr(hp, "DATASET_ROOT", tmp_path / "datasets")
    doc = build(world)["document"]
    env = {"tmp": tmp_path, "script": tmp_path / "unused.py", "plan": tmp_path / "h3_plan", "clock": r2t.FakeClock(), "q0_path": world["q0"], "q0_sha": hashlib.sha256(world["q0"].read_bytes()).hexdigest()}
    env["plan"].mkdir()

    class Tampering(BuildLauncher):
        def launch(self, spec, cap_s, **kw):
            result = super().launch(spec, cap_s, **kw)
            Path(world["reports"]["h1"]).write_bytes(Path(world["reports"]["h1"]).read_bytes() + b" ")             # doi dau vao da ghim TRONG LUC build
            return result

    runner = r2t.make_runner(env, doc["specs"], launcher=Tampering(env["clock"], tmp_path / "datasets"), observer=r2t.FakeObserver(env["clock"]), settings=doc["settings"])
    results = runner.run_plan()
    assert results[0]["verdict"] == "FAIL_CLEAN" and results[0]["reasons"] == ["required_file_changed_during_phase"] and len(results) == 1


def test_a_dataset_directory_that_cannot_be_sealed_quarantines_the_write_phase_and_stops_the_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(r2t.rr, "_is_link", lambda path: Path(path).name == "evil.lnk")                         # Runner chay la ban nap boi r2t, khong phai ban cua hp
    results, root, launcher = run_h3(tmp_path, monkeypatch, layout={"samples.parquet": "PAR1", "evil.lnk": "x"})
    assert results[0]["verdict"] == "QUARANTINE" and results[0]["reasons"][0].startswith("output_seal_failed") and len(results) == 1
    assert (results[0]["output_dirs"][str(root / V1)]["kind"], (tmp_path / "h3_plan" / "QUARANTINE.json").exists()) == ("seal_failed", True)


def test_the_seal_time_is_inside_the_total_budget_of_the_heavy_build_plan(tmp_path, monkeypatch):
    world = h2_world(tmp_path)
    monkeypatch.setattr(hp, "DATASET_ROOT", tmp_path / "datasets")
    doc = build(world)["document"]
    runner = r2t.make_runner({"tmp": tmp_path, "script": tmp_path / "unused.py", "plan": tmp_path / "x", "clock": r2t.FakeClock(), "q0_path": world["q0"],
                              "q0_sha": hashlib.sha256(world["q0"].read_bytes()).hexdigest()}, doc["specs"], observer=r2t.FakeObserver(r2t.FakeClock()), settings=doc["settings"], q0=None)
    spec = doc["specs"][0]
    without = {k: v for k, v in spec.items() if k != "output_dirs"}
    assert runner.reserve_after_launch_s(spec) - runner.reserve_after_launch_s(without) == rr.DIR_SEAL_BUDGET_S


# =========================================================================== ke hoach
def test_the_clone_plan_is_one_write_phase_with_the_q0_pin_the_observer_pin_and_exact_scope(tmp_path):
    q0 = write_q0(tmp_path)
    plan = hp.clone_plan(python=PY, q0_receipt=q0)
    doc = plan["document"]
    assert [s["name"] for s in doc["specs"]] == ["ph3_clone"] and doc["q0_receipt_sha256"] == hashlib.sha256(q0.read_bytes()).hexdigest()
    assert doc["specs"][0]["db"]["db_scope"] == sorted([hp.SOURCE_DB, hp.TARGET_DB]) and all(d in doc["specs"][0]["db"]["protected_dbs"] for d in hp.OPERATIONAL_DENY)
    assert doc["expected_tooling"]["observer_sha256"] == hp._observer_facts()[0] and plan["plan_sha256"] == rr.plan_sha256(doc["specs"], doc["settings"], doc["expected_tooling"], doc["q0_receipt_sha256"])
    assert doc["settings"]["total_budget_s"] % 60 == 0 and doc["settings"]["q0_max_age_s"] == 3600.0
    (tmp_path / "q0b").mkdir()
    no_ops = hp.clone_plan(python=PY, q0_receipt=write_q0(tmp_path / "q0b", databases=["only_this"]))
    assert no_ops["document"]["specs"][0]["db"]["protected_dbs"] == sorted(["only_this", *hp.OPERATIONAL_DENY])      # deny nam trong protected du Q0 khong liet ke


def test_the_init_plan_alternates_init_and_verify_per_version_and_protects_the_clone_source(tmp_path):
    q0 = write_q0(tmp_path)
    receipts = prediction_files(tmp_path)
    (tmp_path / "q0c").mkdir()
    plan = hp.init_plan(python=PY, q0_receipt=write_q0(tmp_path / "q0c", databases=["only_this"]), prediction_receipts=receipts, report_dir=tmp_path / "reports")
    specs = plan["document"]["specs"]
    assert [(s["name"], s["kind"]) for s in specs] == [("ph4_init_h1", "write"), ("ph4_verify_h1", "readonly"), ("ph4_init_h3", "write"), ("ph4_verify_h3", "readonly")]
    assert specs[1]["outputs"] == [str(tmp_path / "reports" / f"verify_{V1}.json")] and specs[3]["outputs"] == [str(tmp_path / "reports" / f"verify_{V3}.json")]
    for spec in (s for s in specs if s["kind"] == "write"):
        assert spec["db"]["db_scope"] == [hp.TARGET_DB] and hp.SOURCE_DB in spec["db"]["protected_dbs"] and all(d in spec["db"]["protected_dbs"] for d in hp.OPERATIONAL_DENY)   # nguon clone + DB van hanh protected
    with pytest.raises(ValueError, match="prediction_receipts_must_be_h1_and_h3"):
        hp.init_plan(python=PY, q0_receipt=q0, prediction_receipts={"h1": receipts["h1"]}, report_dir=tmp_path)


def test_versions_must_be_two_distinct_well_formed_names_with_positive_integer_horizons():
    for bad in ({"h1": ("ds_20261009_nf1", 1)}, {"h1": ("ds_20261009_nf1", 1), "h3": ("ds_20261009_nf1", 3)}, {"h1": ("bad", 1), "h3": ("ds_20261009_nf3", 3)},
                {"h1": ("ds_20261009_nf1", 0), "h3": ("ds_20261009_nf3", 3)}, {"h1": ("ds_20261009_nf1", "1"), "h3": ("ds_20261009_nf3", 3)}):
        with pytest.raises(ValueError, match="versions_invalid"):
            hp._check_versions(bad)
    hp._check_versions(hp.VERSIONS)


# =========================================================================== ngan sach tong
class Clock:
    t = 0.0

    def monotonic(self):
        return self.t


def worst_case_fits(doc, tmp_path):
    settings, overhead = doc["settings"], hp._observer_facts()[1]
    observer = types.SimpleNamespace(overhead_s=overhead)
    runner = rr.Runner(plan_dir=tmp_path / "unused", specs=doc["specs"], settings=settings, expected_tooling=doc["expected_tooling"], q0_receipt_sha256=doc["q0_receipt_sha256"], approved_plan_sha256="0" * 64,
                       launcher=None, clock=Clock(), now_utc=None, gate=None, observer=observer)
    remaining = settings["total_budget_s"]
    for spec in doc["specs"]:
        runner.hard_deadline = remaining                                                                          # Clock.t = 0 => remaining_s() == hard_deadline
        assert runner._budget_problem(spec, include_baseline=(spec["kind"] == "write")) is None, spec["name"]
        remaining -= spec["deadline_s"] + runner.reserve_after_launch_s(spec) + ((settings["baseline_cap_s"] + overhead) if spec["kind"] == "write" else 0)         # kich ban xau nhat
    assert remaining >= settings["margin_s"] + hp.TOTAL_SLACK_S                                                  # slack cong bo (120 s) con NGUYEN sau kich ban xau nhat, khong bi phan seal an mat


def test_the_suggested_total_budget_fits_every_phase_even_if_each_one_uses_its_whole_deadline_and_seals_its_directory(tmp_path, monkeypatch):
    world = h2_world(tmp_path)
    monkeypatch.setattr(hp, "DATASET_ROOT", tmp_path / "datasets")
    for plan in (hp.clone_plan(python=PY, q0_receipt=world["q0"]), hp.init_plan(python=PY, q0_receipt=world["q0"], prediction_receipts=world["receipts"], report_dir=tmp_path / "r2"), build(world)):
        worst_case_fits(plan["document"], tmp_path)


def test_the_suggested_total_budgets_equal_the_published_formula_with_the_directory_seal_reserve_included(tmp_path, monkeypatch):
    s = rr.DEFAULT_SETTINGS
    write_extra = s["settle_s"] + (s["max_probes"] - 1) * (rr.qm.MIN_SPACING_S + 0.5) + s["max_probes"] * s["probe_cap_s"] + s["baseline_cap_s"] + 25.0          # 2 + 31,5 + 60 + 20 + 25 = 138,5
    after = s["containment_grace_s"] + s["receipt_reserve_s"]                                                  # 15
    clone = [{"kind": "write", "deadline_s": 900.0}]
    build = [{"kind": "write", "deadline_s": 1800.0, "output_dirs": ["D:\\x"]}] * 2
    init = [{"kind": "write", "deadline_s": 300.0}, {"kind": "readonly", "deadline_s": 120.0}] * 2
    expected_build = 2 * (1800 + after + 30 + write_extra) + s["margin_s"] + hp.TOTAL_SLACK_S                  # 3967 + 180 = 4147 => 4200 (GPT 80 §4)
    expected_clone = 900 + after + write_extra + s["margin_s"] + hp.TOTAL_SLACK_S                              # 1053,5 + 180 = 1233,5 => 1260
    expected_init = 2 * (300 + after + write_extra) + 2 * (120 + after) + s["margin_s"] + hp.TOTAL_SLACK_S     # 907 + 270 + 180 = 1357 => 1380
    assert (expected_clone, expected_init, expected_build) == (1233.5, 1357.0, 4147.0)
    assert hp.suggest_total_budget(clone, s, observer_overhead_s=25.0) == 1260.0 and hp.suggest_total_budget(init, s, observer_overhead_s=25.0) == 1380.0
    assert hp.suggest_total_budget(build, s, observer_overhead_s=25.0) == 4200.0                                # KHONG phai 4140 (lech -60 s khi bo quen phan seal)
    monkeypatch.setattr(rr, "reserve_after_launch", lambda spec, settings: 1000.0)                              # chung minh dung CHUNG ham du tru cua runner
    assert hp.suggest_total_budget(clone, s, observer_overhead_s=25.0) == 2160.0                                # 60 + 120 + 900 + 1000 + 20 + 25 = 2125 => 2160
    assert hp.suggest_total_budget([{"kind": "readonly", "deadline_s": 100.0}], s, observer_overhead_s=25.0) == 1320.0     # 180 + 100 + 1000 = 1280 => 1320 (readonly: khong baseline/overhead)


def test_a_total_budget_above_the_runner_cap_is_refused():
    big = [{"kind": "readonly", "deadline_s": 3600.0}] * 8
    with pytest.raises(ValueError, match="total_budget_exceeds_cap"):
        hp.suggest_total_budget(big, rr.DEFAULT_SETTINGS, observer_overhead_s=25.0)


# =========================================================================== CLI + khong co co nguy hiem
def test_the_cli_writes_the_clone_plan_and_a_draft_and_prints_the_plan_hash(tmp_path, capsys):
    q0 = write_q0(tmp_path)
    rc = hp.main(["--stage", "clone", "--out-dir", str(tmp_path / "out"), "--python", PY, "--q0-receipt", str(q0)])
    out = json.loads(capsys.readouterr().out)
    saved = json.loads((tmp_path / "out" / "heavy_clone.plan.json").read_text(encoding="utf-8"))
    assert rc == 0 and out["plan_sha256"] == saved["plan_sha256"] and (tmp_path / "out" / "heavy_clone.approval_DRAFT.md").is_file()


def test_the_cli_builds_the_init_and_build_plans_from_the_h2_artifacts(tmp_path, capsys, monkeypatch):
    world = h2_world(tmp_path)
    capsys.readouterr()                                                                                          # bo dau ra cua verifier trong h2_world
    monkeypatch.setattr(hp, "DATASET_ROOT", tmp_path / "datasets")
    pr = [f"{k}={v}" for k, v in world["receipts"].items()]
    rc = hp.main(["--stage", "init", "--out-dir", str(tmp_path / "out_init"), "--python", PY, "--q0-receipt", str(world["q0"]), "--report-dir", str(tmp_path / "r3"), *sum((["--prediction-receipt", x] for x in pr), [])])
    assert rc == 0 and json.loads(capsys.readouterr().out)["stage"] == "init"
    rc = hp.main(["--stage", "build", "--out-dir", str(tmp_path / "out_build"), "--python", PY, "--q0-receipt", str(world["q0"]), "--report-dir", str(world["report_dir"]), "--h2-plan-dir", str(world["h2_dir"]),
                  "--h2-plan-sha256", world["h2_sha"], *sum((["--prediction-receipt", x] for x in pr), []), *sum((["--verify-report", f"{k}={v}"] for k, v in world["reports"].items()), [])])
    assert rc == 0 and json.loads(capsys.readouterr().out)["stage"] == "build"
    with pytest.raises(SystemExit):
        hp.main(["--stage", "build", "--out-dir", str(tmp_path / "o"), "--python", PY, "--q0-receipt", str(world["q0"])])
    with pytest.raises(SystemExit):
        hp.main(["--stage", "init", "--out-dir", str(tmp_path / "o"), "--python", PY, "--q0-receipt", str(world["q0"])])


def test_no_spec_of_any_heavy_plan_carries_a_destructive_or_unpinned_flag(tmp_path, monkeypatch):
    world = h2_world(tmp_path)
    monkeypatch.setattr(hp, "DATASET_ROOT", tmp_path / "datasets")
    for plan in (hp.clone_plan(python=PY, q0_receipt=world["q0"]), hp.init_plan(python=PY, q0_receipt=world["q0"], prediction_receipts=world["receipts"], report_dir=tmp_path / "r2"), build(world)):
        for spec in plan["document"]["specs"]:
            assert not (rr.FORBIDDEN_ARGV & set(spec["argv"])), spec["name"]
            assert not ({"--rebuild-from", "--retry-failed-step", "--stop-after", "--output-root", "--max-select-seconds", "--dry-run"} & set(spec["argv"])), spec["name"]
            n1_pairs = [i for i, token in enumerate(spec["argv"]) if token == "--n1-policy"]
            assert (n1_pairs == [len(spec["argv"]) - 2] and spec["argv"][-1] == str(hp.N1_POLICY_FILE)) if spec["name"].startswith("ph4_init_") else n1_pairs == [], spec["name"]   # chi INIT mang --n1-policy, dung tep policy da ghim
            assert "DATASET_BUILDER_ANOMALY_REGISTRY" not in spec["env"], spec["name"]
            assert rr.validate_spec(spec) == [], spec["name"]


def test_the_heavy_env_is_the_six_variable_environment_plus_unbuffered_output_and_nothing_secret():
    env = hp.heavy_env()
    assert env == {**rp.child_env(), "PYTHONUNBUFFERED": "1"} and set(env) <= rr.ENV_ALLOWLIST and not any(rr.SECRET_LIKE.search(k) for k in env)


def test_the_default_target_is_the_already_cloned_base_and_the_source_is_the_promoted_warehouse():
    """Ten DB dich mac dinh = DB da clone PASS 10/10 (init-only dung lai, khong clone lai); nguon van la warehouse promoted."""
    assert hp.TARGET_DB == "warehouse_dsdev_20261009_3src_nf" and hp.SOURCE_DB == "warehouse_20261004_3src" and hp.TARGET_DB.startswith(hp.DEV_PREFIX)
