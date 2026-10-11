"""repair_build_freeze_v1: dung profile + freeze manifest cho workflow BUILD tu plan + approval + bang chung H2 THAT (chuoi receipt tao bang Runner that + verifier that, dung `h2_world` cua test heavy plan).
Plan duoc dung lai tu bang chung H2 + Q0 receipt (cung hash, cung document) nen plan GPT duyet gan dung voi chuoi H2. Thuan, khong DB."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest

from test_repair_heavy_plan_v1 import PY as TEST_PY
from test_repair_heavy_plan_v1 import V1, V3, h2_world

AUDIT = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools")
TOOL = Path(os.environ.get("BUILD_FREEZE_TOOL_UNDER_TEST", str(AUDIT / "repair_build_freeze_v1.py")))                    # ghi de CHI de chay mutation harness
SV = AUDIT / "renewal_supervisor_v1.py"
OPERATOR = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009\operator_build.py")
WINDOWS = {"not_before_utc": "2026-10-10T12:30:00Z", "start_deadline_utc": "2026-10-10T13:30:00Z", "hard_not_after_utc": "2026-10-10T18:00:00Z"}


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return load(TOOL, "build_freeze_tool_under_test")


class World:
    """Chuoi H2 that (tmp) + Q0 receipt dat dung cho Q0-B (tmp\\outputs\\repair-run-q0b-r121\\q0) + thu muc dataset tam; tool/outputs tro sang tmp."""

    def __init__(self, tool, tmp_path, monkeypatch):
        self.tool, self.tmp = tool, tmp_path
        (tmp_path / "h2").mkdir()
        self.h2 = h2_world(tmp_path / "h2")
        self.outputs = tmp_path / "outputs"
        self.q0 = self.outputs / "repair-run-q0b-r121" / "q0" / "q0_receipt.json"
        self.q0.parent.mkdir(parents=True)
        shutil.copyfile(self.h2["q0"], self.q0)
        self.run_root = self.outputs / "repair-run-build-r122"
        monkeypatch.setattr(tool, "OUTPUTS", self.outputs)
        monkeypatch.setattr(tool.hp, "DATASET_ROOT", self.outputs / "datasets")
        self.plan_file = self.make_plan()
        self.approval = self.make_approval(self.plan_file)

    def plan(self, **override):
        kwargs = dict(python=TEST_PY, q0_receipt=self.q0, verify_reports=self.h2["reports"], prediction_receipts=self.h2["receipts"], report_dir=self.h2["report_dir"], h2_plan_dir=self.h2["h2_dir"],
                      h2_plan_sha256=self.h2["h2_sha"])
        kwargs.update(override)
        return self.tool.hp.build_plan(**kwargs)

    def make_plan(self, name="plan_draft", **override):
        out = self.tmp / name
        self.tool.hp.rp.emit(out, "heavy_build", self.plan(**override), "test")
        return out / "heavy_build.plan.json"

    def make_approval(self, plan_file, *, plan_sha=None, plan_dir=None, entry=None, not_after="2026-10-10T19:00:00Z", invocation="e" * 64, name="approval.md"):
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        path = self.tmp / name
        path.write_text("# test\n\n" + "\n".join([
            f"APPROVED_PLAN_SHA256: `{plan_sha or plan['plan_sha256']}`", f"APPROVED_NOT_AFTER_UTC: {not_after}", f"APPROVED_PLAN_DIR: {plan_dir or self.run_root / 'build' / 'plan'}",
            f"APPROVED_ENTRYPOINT_SHA256: `{entry or self.tool.ci.sha256_file(self.tool.FIXED['entry'])}`", f"APPROVED_INVOCATION_SHA256: `{invocation}`"]) + "\n", encoding="utf-8")
        return path

    def build(self, **override):
        kwargs = dict(q0_receipt=self.q0, plan_file=self.plan_file, approval_file=self.approval, windows=WINDOWS, run_root=self.run_root, h2_plan_dir=self.h2["h2_dir"], h2_plan_sha256=self.h2["h2_sha"],
                      report_dir=self.h2["report_dir"], prediction_receipts=self.h2["receipts"], python=TEST_PY)
        kwargs.update(override)
        return self.tool.build(**kwargs)

    def args(self, **override):
        values = {"--run-root": str(self.run_root), "--q0-receipt": str(self.q0), "--plan": str(self.plan_file), "--approval": str(self.approval), "--h2-plan-dir": str(self.h2["h2_dir"]),
                  "--h2-plan-sha256": self.h2["h2_sha"], "--report-dir": str(self.h2["report_dir"]), "--python": TEST_PY, "--not-before-utc": WINDOWS["not_before_utc"],
                  "--start-deadline-utc": WINDOWS["start_deadline_utc"], "--hard-not-after-utc": WINDOWS["hard_not_after_utc"]}
        values.update(override)
        out = [x for k, v in values.items() for x in (k, v)]
        for key in ("h1", "h3"):
            out += ["--prediction-receipt", f"{key}={self.h2['receipts'][key]}"]
        return out


@pytest.fixture
def world(tool, tmp_path, monkeypatch):
    return World(tool, tmp_path, monkeypatch)


def test_profile_and_freeze_bind_the_plan_the_q0_receipt_the_h2_evidence_and_the_pins(world):
    tool = world.tool
    profile, freeze = world.build()
    plan = json.loads(world.plan_file.read_text(encoding="utf-8"))
    budget = plan["document"]["settings"]["total_budget_s"]
    n = tool.ci.renew_seconds(budget, 1)
    assert profile["renew_seconds"] == n and profile["plan"] == "build" and profile["plan_total_budget_s"] == budget and "1 x check_only_cap 120" in profile["formula"]
    assert profile["argv"][-1] == str(world.run_root / "evidence_archive" / "build") and profile["stdout_stderr"] == str(world.run_root / "bld_publisher.log")
    p = freeze["plans"]["build"]
    assert freeze["kind"] == "build_freeze" and freeze["run_root"] == str(world.run_root) and p["plan_dir"] == str(world.run_root / "build" / "plan") and p["launcher_logs"] == str(world.run_root / "build" / "logs")
    assert p["dataset_dirs"] == [str(world.outputs / "datasets" / V1), str(world.outputs / "datasets" / V3)] and p["plan_sha256"] == plan["plan_sha256"] and p["total_budget_s"] == budget
    assert p["q0_max_age_s"] == plan["document"]["settings"]["q0_max_age_s"] == 3600.0 and 0 < p["q0_preceding_worst_case_s"] < p["q0_max_age_s"]
    assert freeze["q0_receipt"]["path"] == str(world.q0) and freeze["q0_receipt"]["sha256"] == tool.ci.sha256_file(world.q0) and plan["document"]["q0_receipt_sha256"] == freeze["q0_receipt"]["sha256"]
    assert freeze["h2"] == {"plan_dir": str(world.h2["h2_dir"]), "plan_sha256": world.h2["h2_sha"], "reports": sorted(str(world.h2["reports"][k]) for k in ("h1", "h3"))}
    assert set(freeze["pins"]) == set(tool.FIXED) and {"observer_module", "quiescence_model", "runner"} <= set(freeze["pins"]) and all(len(v["sha256"]) == 64 for v in freeze["pins"].values())


def test_the_preceding_worst_case_is_the_first_write_phase_budget_from_the_published_formula(world):
    tool = world.tool
    _, freeze = world.build()
    plan = json.loads(world.plan_file.read_text(encoding="utf-8"))
    _, overhead = tool.hp._observer_facts()
    expected = tool.hp.suggest_total_budget(plan["document"]["specs"][:1], plan["document"]["settings"], observer_overhead_s=overhead)
    assert freeze["plans"]["build"]["q0_preceding_worst_case_s"] == expected and expected > 1800.0                  # >= deadline phase dau (1800 s): h3 chi bat dau SAU khi h1 co the dung het deadline


def test_the_freeze_pin_set_is_exactly_the_operator_pin_set(tool):
    operator = load(OPERATOR, "operator_build_for_freeze_test")
    assert {name: str(path) for name, path in tool.FIXED.items()} == {name: str(path) for name, path in operator.FIXED_PIN_PATHS.items()}
    assert tool.RUN_NAME_RE.pattern == operator.RUN_NAME_RE.pattern and tool.Q0_RUN_NAME_RE.pattern == operator.Q0_RUN_NAME_RE.pattern and tool.Q0_AGE_MARGIN_S == operator.Q0_AGE_MARGIN_S
    assert tool.PHASES == operator.PLAN_PHASES["build"]


def test_the_generated_profile_passes_the_reviewed_supervisor_profile_validator(world):
    sv = load(SV, "supervisor_for_build_freeze_test")
    profile, freeze = world.build()
    assert sv.validate_profile(profile, expected_source_sha256=freeze["pins"]["source"]["sha256"], expected_interpreter_sha256=freeze["pins"]["interpreter"]["sha256"]) == []


@pytest.mark.parametrize("override,why", [
    ({"plan_sha": "0" * 64}, "approval_plan_sha256"), ({"plan_dir": r"D:\elsewhere\plan"}, "approval_plan_dir"), ({"entry": "1" * 64}, "approval_entrypoint"),
    ({"not_after": "2026-10-10T15:00:00Z"}, "approval_expires_before_hard_not_after")])
def test_every_approval_binding_violation_is_refused(world, override, why):
    with pytest.raises(ValueError, match=why):
        world.build(approval_file=world.make_approval(world.plan_file, **override))


@pytest.mark.parametrize("name,ok", [("repair-run-build-r122", True), ("repair-run-build-r001", True), ("repair-run-20261009", False), ("repair-run-q0a-r120", False), ("repair-run-build-r12", False),
                                     ("repair-run-build-r1222", False), ("repair-run-build-r122-x", False), ("x-repair-run-build-r122", False), ("repair-run-heavy-ci-r101", False)])
def test_only_build_run_roots_under_outputs_are_accepted(world, name, ok):
    if ok:
        world.tool.check_run_root(world.outputs / name)
    else:
        with pytest.raises(ValueError, match="run_root_must_be_outputs_repair_run_build_rNNN"):
            world.tool.check_run_root(world.outputs / name)
    with pytest.raises(ValueError, match="run_root_must_be_outputs"):
        world.tool.check_run_root(Path(r"D:\elsewhere") / "repair-run-build-r122")


@pytest.mark.parametrize("relative,ok", [
    (Path("repair-run-q0a-r120") / "q0" / "q0_receipt.json", True), (Path("repair-run-q0b-r121") / "q0" / "q0_receipt.json", True), (Path("repair-run-20261009") / "q0" / "q0_receipt.json", False),
    (Path("repair-run-q0c-r120") / "q0" / "q0_receipt.json", False), (Path("repair-run-q0a-r120") / "q0" / "other.json", False), (Path("repair-run-q0a-r120") / "q1" / "q0_receipt.json", False),
    (Path("repair-run-q0a-r120") / "q0_receipt.json", False), (Path("repair-run-q0a-r120") / "x" / "q0" / "q0_receipt.json", False),
    (Path(r"D:\elsewhere") / "repair-run-q0a-r120" / "q0" / "q0_receipt.json", False)])
def test_only_a_q0a_q0b_receipt_path_is_accepted(world, relative, ok):
    if ok:
        world.tool.check_q0_path(world.outputs / relative)
    else:
        with pytest.raises(ValueError, match="q0_receipt_must_be_outputs_repair_run_q0a_or_q0b"):
            world.tool.check_q0_path(world.outputs / relative)


def test_a_q0_receipt_outside_a_q0_run_root_is_refused_by_build(world, tmp_path):
    other = tmp_path / "somewhere" / "q0_receipt.json"
    other.parent.mkdir()
    shutil.copyfile(world.q0, other)
    with pytest.raises(ValueError, match="q0_receipt_must_be_outputs_repair_run_q0a_or_q0b"):
        world.build(q0_receipt=other)


def test_windows_must_be_ordered(world):
    with pytest.raises(ValueError, match="windows_order"):
        world.build(windows={**WINDOWS, "start_deadline_utc": "2026-10-10T12:00:00Z"})


def test_a_plan_that_is_not_the_one_rebuilt_from_the_h2_evidence_is_refused(world, tmp_path):
    doc = json.loads(world.plan_file.read_text(encoding="utf-8"))
    doc["document"]["settings"]["q0_max_age_s"] = 7200.0                                                         # doi mot setting, giu nguyen plan_sha256 => document != rebuilt
    tampered = tmp_path / "tampered.plan.json"
    tampered.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="plan_differs_from_the_plan_rebuilt"):
        world.build(plan_file=tampered, approval_file=world.make_approval(tampered, name="a2.md"))
    doc = json.loads(world.plan_file.read_text(encoding="utf-8"))
    doc["plan_sha256"] = "0" * 64
    wrong_hash = tmp_path / "wrong_hash.plan.json"
    wrong_hash.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="plan_differs_from_the_plan_rebuilt"):
        world.build(plan_file=wrong_hash, approval_file=world.make_approval(wrong_hash, name="a3.md"))


def test_a_plan_built_for_another_q0_receipt_is_refused(world, tmp_path):
    other = world.outputs / "repair-run-q0a-r120" / "q0" / "q0_receipt.json"
    other.parent.mkdir(parents=True)
    doc = json.loads(world.q0.read_text(encoding="utf-8"))
    doc["created_at_utc"] = "2026-10-09T10:00:01Z"                                                               # receipt khac (hash khac) nhung hop le; plan van gan receipt cu
    other.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="plan_q0_receipt_mismatch"):
        world.build(q0_receipt=other)


def test_wrong_phases_or_kinds_are_refused(world, tmp_path):
    for index, mutate in enumerate([lambda d: d["document"]["specs"].pop(), lambda d: d["document"]["specs"][0].__setitem__("name", "ph5_build_x"),
                                    lambda d: d["document"]["specs"][1].__setitem__("kind", "readonly")]):
        doc = json.loads(world.plan_file.read_text(encoding="utf-8"))
        mutate(doc)
        path = tmp_path / f"bad{index}.plan.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(ValueError, match="plan_must_be_the_two_write_phases"):
            world.build(plan_file=path, approval_file=world.make_approval(path, name=f"ab{index}.md"))


@pytest.mark.parametrize("name,mutate", [
    ("first_has_none", lambda d: d["document"]["specs"][0].__setitem__("output_dirs", [])),
    ("first_has_two", lambda d: d["document"]["specs"][0].__setitem__("output_dirs", [*d["document"]["specs"][0]["output_dirs"], r"D:\elsewhere\ds_20261009_zz"])),
    ("elsewhere", lambda d: d["document"]["specs"][0].__setitem__("output_dirs", [r"D:\elsewhere\ds_20261009_nf1"])),
    ("nested_under_dataset_root", lambda d: d["document"]["specs"][0].__setitem__("output_dirs", [str(Path(d["document"]["specs"][0]["output_dirs"][0]) / "sub")]))])
def test_plan_output_directories_must_be_exactly_two_direct_children_of_the_dataset_root(world, tmp_path, name, mutate):
    doc = json.loads(world.plan_file.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / f"dirs_{name}.plan.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="dataset_dirs_must_be_two_new_directories"):
        world.build(plan_file=path, approval_file=world.make_approval(path, name=f"a_{name}.md"))


def test_a_q0_file_that_is_not_a_q0_receipt_is_refused(world):
    doc = json.loads(world.q0.read_text(encoding="utf-8"))
    doc["kind"] = "something_else"
    world.q0.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="q0_receipt_malformed"):
        world.build()
    doc["kind"] = "q0_selftest_receipt"
    del doc["created_at_utc"]
    world.q0.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="q0_receipt_malformed"):
        world.build()


def test_existing_dataset_directories_are_refused(world):
    (world.outputs / "datasets" / V1).mkdir(parents=True)
    with pytest.raises(ValueError, match="dataset_dirs_must_be_two_new_directories"):
        world.build()


def test_h2_evidence_that_does_not_match_the_plan_is_refused(world):
    with pytest.raises(ValueError):
        world.build(h2_plan_sha256="0" * 64)
    (world.h2["h2_dir"] / "receipt_02_ph4_verify_h1.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        world.build()


def test_an_impossible_q0_age_budget_is_refused(world, monkeypatch):
    real = world.tool.hp.suggest_total_budget
    monkeypatch.setattr(world.tool.hp, "suggest_total_budget", lambda specs, settings, **k: 3600.0 if len(specs) == 1 else real(specs, settings, **k))   # chi doi du tru "phase truoc", plan dung lai van y het
    with pytest.raises(ValueError, match="q0_age_budget_impossible"):
        world.build()


def test_main_writes_profile_and_freeze_exclusively_and_creates_the_empty_plan_dir(world, capsys):
    assert world.tool.main(world.args()) == 0
    out = json.loads(capsys.readouterr().out)
    freeze = json.loads((world.run_root / "build_freeze.json").read_text(encoding="utf-8"))
    assert out["ok"] and freeze["profile"]["sha256"] == world.tool.ci.sha256_file(world.run_root / "renewal_profile_build.json") and freeze["profile"]["renew_seconds"] == out["renew_seconds"]
    assert (world.run_root / "build" / "plan").is_dir() and not any((world.run_root / "build" / "plan").iterdir()) and not (world.run_root / "build" / "logs").exists()
    assert world.tool.main(world.args()) == 1


def test_main_refuses_a_run_root_that_already_has_a_build_dir_and_writes_nothing(world):
    (world.run_root / "build").mkdir(parents=True)
    assert world.tool.main(world.args()) == 1 and not (world.run_root / "build_freeze.json").exists() and not (world.run_root / "renewal_profile_build.json").exists()


def test_main_refuses_a_bad_run_root_name_before_touching_the_disk(world):
    bad = world.outputs / "repair-run-q0a-r999"
    assert world.tool.main(world.args(**{"--run-root": str(bad)})) == 1 and not bad.exists()


def test_the_freeze_made_by_the_tool_loads_in_the_operator(world, monkeypatch, capsys):
    operator = load(OPERATOR, "operator_build_for_roundtrip")
    monkeypatch.setattr(operator, "ROOT", world.tmp)
    monkeypatch.setattr(operator, "DATASET_ROOT", world.outputs / "datasets")
    assert world.tool.main(world.args()) == 0
    result = json.loads(capsys.readouterr().out)
    assert operator.load_freeze(world.run_root / "build_freeze.json", result["freeze_sha256"]) == []
    cfg = operator.CFG["plans"]["build"]
    assert cfg["dataset_dirs"] == [str(world.outputs / "datasets" / V1), str(world.outputs / "datasets" / V3)] and operator.RUN == world.run_root
    assert operator.approved_argv("build")[-2:] == ["--q0-receipt", str(world.q0)]
