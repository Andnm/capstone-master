"""repair_heavy_init_freeze_v1: dung profile + freeze manifest cho workflow HEAVY INIT-ONLY tu plan + approval + bang chung (Q0 receipt, prediction receipts, clone PASS, quarantine). Plan duoc dung lai tu cac dau vao
bang `hp.init_plan` (cung hash, cung document). Thuan, khong DB. Q0 receipt/prediction receipt dung fixture cua test heavy plan (khong phai receipt live)."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from test_repair_heavy_plan_v1 import prediction_files, r2t

AUDIT = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools")
TOOL = Path(os.environ.get("HEAVY_INIT_FREEZE_TOOL_UNDER_TEST", str(AUDIT / "repair_heavy_init_freeze_v1.py")))                    # ghi de CHI de chay mutation harness
SV = AUDIT / "renewal_supervisor_v1.py"
OPERATOR = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009\operator_heavy_init.py")
PY = sys.executable
WINDOWS = {"not_before_utc": "2026-10-10T13:06:00Z", "start_deadline_utc": "2026-10-10T13:19:00Z", "hard_not_after_utc": "2026-10-10T14:30:00Z"}
BAD = "ds_20261009_nf1"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return load(TOOL, "heavy_init_freeze_tool_under_test")


class World:
    def __init__(self, tool, tmp_path, monkeypatch):
        self.tool, self.tmp = tool, tmp_path
        self.outputs = tmp_path / "outputs"
        self.q0 = self.outputs / "repair-run-q0b-r120" / "q0" / "q0_receipt.json"
        self.q0.parent.mkdir(parents=True)
        source_q0, _ = r2t.write_q0(tmp_path)
        shutil.copyfile(source_q0, self.q0)
        (tmp_path / "pred").mkdir()
        self.receipts = prediction_files(tmp_path / "pred")
        self.run_root = self.outputs / "repair-run-heavy-init-r121"
        monkeypatch.setattr(tool, "OUTPUTS", self.outputs)
        self.deps = {}
        for name, content in (("clone_receipt", {"phase": "ph3_clone", "verdict": "PASS"}), ("clone_plan_bound", {"kind": "plan_bound"}), ("quarantine_verify_report", {"verdict": "FAIL", "dataset_version": BAD}),
                              ("quarantine_init_receipt", {"phase": "ph4_init_h1"}), ("quarantine_verify_receipt", {"phase": "ph4_verify_h1"})):
            path = tmp_path / "dep" / f"{name}.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(content), encoding="utf-8")
            self.deps[name] = path
        self.plan_file = self.make_plan()
        self.approval = self.make_approval(self.plan_file)

    def plan(self, **override):
        kwargs = dict(python=PY, q0_receipt=self.q0, prediction_receipts=self.receipts, report_dir=self.run_root / "h2" / "reports")
        kwargs.update(override)
        return self.tool.hp.init_plan(**kwargs)

    def make_plan(self, name="plan_draft", **override):
        out = self.tmp / name
        self.tool.hp.rp.emit(out, "heavy_init", self.plan(**override), "test")
        return out / "heavy_init.plan.json"

    def make_approval(self, plan_file, *, plan_sha=None, plan_dir=None, entry=None, not_after="2026-10-10T15:00:00Z", invocation="e" * 64, name="approval.md"):
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        path = self.tmp / name
        path.write_text("# test\n\n" + "\n".join([
            f"APPROVED_PLAN_SHA256: `{plan_sha or plan['plan_sha256']}`", f"APPROVED_NOT_AFTER_UTC: {not_after}", f"APPROVED_PLAN_DIR: {plan_dir or self.run_root / 'init' / 'plan'}",
            f"APPROVED_ENTRYPOINT_SHA256: `{entry or self.tool.ci.sha256_file(self.tool.FIXED['entry'])}`", f"APPROVED_INVOCATION_SHA256: `{invocation}`"]) + "\n", encoding="utf-8")
        return path

    def build(self, **override):
        kwargs = dict(q0_receipt=self.q0, plan_file=self.plan_file, approval_file=self.approval, windows=WINDOWS, run_root=self.run_root, prediction_receipts=self.receipts, dependencies=dict(self.deps),
                      quarantined_version=BAD, python=PY)
        kwargs.update(override)
        return self.tool.build(**kwargs)

    def args(self, **override):
        values = {"--run-root": str(self.run_root), "--q0-receipt": str(self.q0), "--plan": str(self.plan_file), "--approval": str(self.approval), "--quarantined-version": BAD, "--python": PY,
                  "--not-before-utc": WINDOWS["not_before_utc"], "--start-deadline-utc": WINDOWS["start_deadline_utc"], "--hard-not-after-utc": WINDOWS["hard_not_after_utc"],
                  **{f"--{name.replace('_', '-')}": str(path) for name, path in self.deps.items()}}
        values.update(override)
        out = [x for k, v in values.items() for x in (k, v)]
        for key in ("h1", "h3"):
            out += ["--prediction-receipt", f"{key}={self.receipts[key]}"]
        return out


@pytest.fixture
def world(tool, tmp_path, monkeypatch):
    return World(tool, tmp_path, monkeypatch)


def test_profile_and_freeze_bind_the_plan_q0_dependencies_h2_reports_and_pins(world):
    tool = world.tool
    profile, freeze = world.build()
    plan = json.loads(world.plan_file.read_text(encoding="utf-8"))
    budget = plan["document"]["settings"]["total_budget_s"]
    assert budget == 1380.0 and profile["renew_seconds"] == tool.ci.renew_seconds(budget, 1) == 1800 and profile["plan"] == "heavy_init" and "1 x check_only_cap 120" in profile["formula"]
    assert profile["argv"][-1] == str(world.run_root / "evidence_archive" / "hi") and profile["stdout_stderr"] == str(world.run_root / "hi_publisher.log") and "clone" in profile["forbidden"] and "build" in profile["forbidden"]
    p = freeze["plans"]["init"]
    versions = {k: tool.hp.VERSIONS[k][0] for k in ("h1", "h3")}
    assert freeze["kind"] == "heavy_init_freeze" and p["plan_dir"] == str(world.run_root / "init" / "plan") and p["launcher_logs"] == str(world.run_root / "init" / "logs")
    assert p["versions"] == versions and p["target_database"] == tool.hp.TARGET_DB and p["total_budget_s"] == budget and p["plan_sha256"] == plan["plan_sha256"]
    assert p["h2_reports"] == [str(world.run_root / "h2" / "reports" / f"verify_{versions[k]}.json") for k in ("h1", "h3")]
    assert p["q0_max_age_s"] == 3600.0 and 0 < p["q0_preceding_worst_case_s"] < p["q0_max_age_s"]
    assert freeze["q0_receipt"]["path"] == str(world.q0) and freeze["q0_receipt"]["sha256"] == tool.ci.sha256_file(world.q0) == plan["document"]["q0_receipt_sha256"]
    assert set(freeze["dependencies"]) == set(tool.DEPENDENCY_ARGS) and all(freeze["dependencies"][k]["sha256"] == tool.ci.sha256_file(world.deps[k]) and freeze["dependencies"][k]["path"] == str(world.deps[k]) for k in world.deps)
    assert freeze["quarantined_version"] == BAD and BAD not in versions.values()
    assert set(freeze["pins"]) == set(tool.FIXED) and {"observer_module", "quiescence_model", "runner"} <= set(freeze["pins"]) and all(len(v["sha256"]) == 64 for v in freeze["pins"].values())


def test_the_preceding_worst_case_covers_exactly_init_h1_and_verify_h1(world):
    tool = world.tool
    _, freeze = world.build()
    plan = json.loads(world.plan_file.read_text(encoding="utf-8"))
    specs, settings = plan["document"]["specs"], plan["document"]["settings"]
    _, overhead = tool.hp._observer_facts()
    assert freeze["plans"]["init"]["q0_preceding_worst_case_s"] == tool.hp.suggest_total_budget(specs[:2], settings, observer_overhead_s=overhead)
    assert [s["name"] for s in specs[:2]] == ["ph4_init_h1", "ph4_verify_h1"] and specs[2]["kind"] == "write"


def test_the_freeze_pin_set_is_exactly_the_operator_pin_set(tool):
    operator = load(OPERATOR, "operator_heavy_init_for_freeze_test")
    assert {name: str(path) for name, path in tool.FIXED.items()} == {name: str(path) for name, path in operator.FIXED_PIN_PATHS.items()}
    assert tool.RUN_NAME_RE.pattern == operator.RUN_NAME_RE.pattern and tool.Q0_RUN_NAME_RE.pattern == operator.Q0_RUN_NAME_RE.pattern and tool.Q0_AGE_MARGIN_S == operator.Q0_AGE_MARGIN_S
    assert tool.PHASES == operator.PLAN_PHASES["init"] and dict(zip(tool.PHASES, tool.KINDS)) == operator.PHASE_KINDS and tool.DEPENDENCY_ARGS == operator.DEPENDENCY_KEYS


def test_the_generated_profile_passes_the_reviewed_supervisor_profile_validator(world):
    sv = load(SV, "supervisor_for_heavy_init_freeze_test")
    profile, freeze = world.build()
    assert sv.validate_profile(profile, expected_source_sha256=freeze["pins"]["source"]["sha256"], expected_interpreter_sha256=freeze["pins"]["interpreter"]["sha256"]) == []


@pytest.mark.parametrize("override,why", [
    ({"plan_sha": "0" * 64}, "approval_plan_sha256"), ({"plan_dir": r"D:\elsewhere\plan"}, "approval_plan_dir"), ({"entry": "1" * 64}, "approval_entrypoint"),
    ({"not_after": "2026-10-10T14:00:00Z"}, "approval_expires_before_hard_not_after")])
def test_every_approval_binding_violation_is_refused(world, override, why):
    with pytest.raises(ValueError, match=why):
        world.build(approval_file=world.make_approval(world.plan_file, **override))


@pytest.mark.parametrize("name,ok", [("repair-run-heavy-init-r121", True), ("repair-run-heavy-init-r001", True), ("repair-run-heavy-ci-r101", False), ("repair-run-build-r122", False),
                                     ("repair-run-heavy-init-r12", False), ("repair-run-heavy-init-r1210", False), ("repair-run-heavy-init-r121-x", False), ("x-repair-run-heavy-init-r121", False)])
def test_only_heavy_init_run_roots_under_outputs_are_accepted(world, name, ok):
    if ok:
        world.tool.check_run_root(world.outputs / name)
    else:
        with pytest.raises(ValueError, match="run_root_must_be_outputs_repair_run_heavy_init_rNNN"):
            world.tool.check_run_root(world.outputs / name)
    with pytest.raises(ValueError, match="run_root_must_be_outputs"):
        world.tool.check_run_root(Path(r"D:\elsewhere") / "repair-run-heavy-init-r121")


@pytest.mark.parametrize("relative,ok", [
    (Path("repair-run-q0a-r120") / "q0" / "q0_receipt.json", True), (Path("repair-run-q0b-r121") / "q0" / "q0_receipt.json", True), (Path("repair-run-20261009") / "q0" / "q0_receipt.json", False),
    (Path("repair-run-q0c-r120") / "q0" / "q0_receipt.json", False), (Path("repair-run-q0a-r120") / "q0" / "other.json", False), (Path("repair-run-q0a-r120") / "q1" / "q0_receipt.json", False),
    (Path(r"D:\elsewhere") / "repair-run-q0a-r120" / "q0" / "q0_receipt.json", False)])
def test_only_a_q0a_q0b_receipt_path_is_accepted(world, relative, ok):
    if ok:
        world.tool.check_q0_path(world.outputs / relative)
    else:
        with pytest.raises(ValueError, match="q0_receipt_must_be_outputs_repair_run_q0a_or_q0b"):
            world.tool.check_q0_path(world.outputs / relative)


def test_windows_must_be_ordered(world):
    with pytest.raises(ValueError, match="windows_order"):
        world.build(windows={**WINDOWS, "start_deadline_utc": "2026-10-10T13:00:00Z"})


def test_a_plan_that_is_not_the_one_rebuilt_from_these_inputs_is_refused(world, tmp_path):
    doc = json.loads(world.plan_file.read_text(encoding="utf-8"))
    doc["document"]["settings"]["q0_max_age_s"] = 7200.0
    tampered = tmp_path / "tampered.plan.json"
    tampered.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="plan_differs_from_the_plan_rebuilt"):
        world.build(plan_file=tampered, approval_file=world.make_approval(tampered, name="a2.md"))
    doc = json.loads(world.plan_file.read_text(encoding="utf-8"))
    doc["plan_sha256"] = "0" * 64
    wrong = tmp_path / "wrong_hash.plan.json"
    wrong.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="plan_differs_from_the_plan_rebuilt"):
        world.build(plan_file=wrong, approval_file=world.make_approval(wrong, name="a3.md"))


def test_the_old_argv_without_the_n1_policy_flag_can_never_be_frozen(world, tmp_path):
    """Su co 10/10: mot plan init thieu `--n1-policy` khong bang plan dung lai tu builder => freeze tu choi."""
    doc = json.loads(world.plan_file.read_text(encoding="utf-8"))
    for spec in doc["document"]["specs"]:
        if spec["name"].startswith("ph4_init_"):
            index = spec["argv"].index("--n1-policy")
            del spec["argv"][index:index + 2]
    old_style = tmp_path / "old_style.plan.json"
    old_style.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="plan_differs_from_the_plan_rebuilt"):
        world.build(plan_file=old_style, approval_file=world.make_approval(old_style, name="a4.md"))


def test_a_plan_built_for_another_q0_receipt_is_refused(world):
    other = world.outputs / "repair-run-q0a-r119" / "q0" / "q0_receipt.json"
    other.parent.mkdir(parents=True)
    doc = json.loads(world.q0.read_text(encoding="utf-8"))
    doc["created_at_utc"] = "2026-10-09T10:00:01Z"
    other.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="plan_q0_receipt_mismatch"):
        world.build(q0_receipt=other)


def test_a_q0_file_that_is_not_a_q0_receipt_is_refused(world):
    doc = json.loads(world.q0.read_text(encoding="utf-8"))
    doc["kind"] = "something_else"
    world.q0.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="q0_receipt_malformed"):
        world.build()


@pytest.mark.parametrize("mutate", [lambda d: d["document"]["specs"].pop(), lambda d: d["document"]["specs"].reverse(), lambda d: d["document"]["specs"][1].__setitem__("kind", "write"),
                                    lambda d: d["document"]["specs"][0].__setitem__("kind", "readonly"), lambda d: d["document"]["specs"][0].__setitem__("name", "ph4_init_x")],
                         ids=["missing", "reversed", "verify_write", "init_readonly", "renamed_same_kind"])
def test_wrong_phases_or_kinds_are_refused(world, tmp_path, mutate):
    doc = json.loads(world.plan_file.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / "bad.plan.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="plan_must_be_the_four_phases"):
        world.build(plan_file=path, approval_file=world.make_approval(path, name="ab.md"))


def test_dependency_problems_are_refused(world, tmp_path):
    with pytest.raises(ValueError, match="dependencies_or_quarantined_version_malformed"):
        world.build(dependencies={k: v for k, v in world.deps.items() if k != "clone_receipt"})
    with pytest.raises(ValueError, match="dependencies_or_quarantined_version_malformed"):
        world.build(quarantined_version="BAD")
    with pytest.raises(ValueError, match="plan_versions_must_not_include_the_quarantined_version"):
        world.build(quarantined_version=world.tool.hp.VERSIONS["h1"][0])
    with pytest.raises(ValueError, match="dependency_missing: clone_receipt"):
        world.build(dependencies={**world.deps, "clone_receipt": tmp_path / "nope.json"})
    world.deps["clone_receipt"].write_text(json.dumps({"phase": "ph3_clone", "verdict": "FAIL_CLEAN"}), encoding="utf-8")
    with pytest.raises(ValueError, match="clone_receipt_is_not_a_pass"):
        world.build()
    world.deps["clone_receipt"].write_text(json.dumps({"phase": "other", "verdict": "PASS"}), encoding="utf-8")
    with pytest.raises(ValueError, match="clone_receipt_is_not_a_pass"):
        world.build()


def test_an_impossible_q0_age_budget_is_refused(world, monkeypatch):
    real = world.tool.hp.suggest_total_budget
    monkeypatch.setattr(world.tool.hp, "suggest_total_budget", lambda specs, settings, **k: 3600.0 if len(specs) == 2 else real(specs, settings, **k))
    with pytest.raises(ValueError, match="q0_age_budget_impossible"):
        world.build()


def test_main_writes_profile_and_freeze_exclusively_creates_the_empty_plan_dir_but_not_h2(world, capsys):
    assert world.tool.main(world.args()) == 0
    out = json.loads(capsys.readouterr().out)
    freeze = json.loads((world.run_root / "hi_freeze.json").read_text(encoding="utf-8"))
    assert out["ok"] and freeze["profile"]["sha256"] == world.tool.ci.sha256_file(world.run_root / "renewal_profile_hi.json") and freeze["profile"]["renew_seconds"] == out["renew_seconds"] == 1800
    assert (world.run_root / "init" / "plan").is_dir() and not any((world.run_root / "init" / "plan").iterdir()) and not (world.run_root / "h2").exists() and not (world.run_root / "init" / "logs").exists()
    assert world.tool.main(world.args()) == 1


@pytest.mark.parametrize("existing", ["init", "h2"])
def test_main_refuses_a_run_root_that_already_has_an_init_or_h2_dir_and_writes_nothing(world, existing):
    (world.run_root / existing).mkdir(parents=True)
    assert world.tool.main(world.args()) == 1 and not (world.run_root / "hi_freeze.json").exists() and not (world.run_root / "renewal_profile_hi.json").exists()


def test_main_refuses_a_bad_run_root_name_before_touching_the_disk(world):
    bad = world.outputs / "repair-run-heavy-ci-r101"
    assert world.tool.main(world.args(**{"--run-root": str(bad)})) == 1 and not bad.exists()


def test_the_freeze_made_by_the_tool_loads_in_the_operator(world, monkeypatch, capsys):
    operator = load(OPERATOR, "operator_heavy_init_for_roundtrip")
    monkeypatch.setattr(operator, "ROOT", world.tmp)
    assert world.tool.main(world.args()) == 0
    result = json.loads(capsys.readouterr().out)
    assert operator.load_freeze(world.run_root / "hi_freeze.json", result["freeze_sha256"]) == []
    cfg = operator.CFG["plans"]["init"]
    assert cfg["versions"] == {"h1": world.tool.hp.VERSIONS["h1"][0], "h3": world.tool.hp.VERSIONS["h3"][0]} and operator.RUN == world.run_root and operator.CFG["quarantined_version"] == BAD
    assert operator.approved_argv("init")[-2:] == ["--q0-receipt", str(world.q0)]
