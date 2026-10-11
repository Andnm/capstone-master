"""repair_q0var_freeze_v1: dung profile + freeze manifest cho workflow Q0 THAM SO (Q0-A / Q0-B) tu plan + approval da co (thuan, khong DB). Plan sinh bang CHINH builder `repair_q0_plan_param_v1`."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

AUDIT = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools")
TOOL = Path(os.environ.get("Q0VAR_FREEZE_TOOL_UNDER_TEST", str(AUDIT / "repair_q0var_freeze_v1.py")))                    # ghi de CHI de chay mutation harness
PLAN_TOOL = AUDIT / "repair_q0_plan_param_v1.py"
SV = AUDIT / "renewal_supervisor_v1.py"
OPERATOR = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009\operator_q0var.py")
PYW = r"D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe"
WINDOWS = {"not_before_utc": "2026-10-10T12:30:00Z", "start_deadline_utc": "2026-10-10T13:30:00Z", "hard_not_after_utc": "2026-10-10T16:00:00Z"}
REAL_OUTPUTS = Path(r"D:\MSE\CAPSTONE\outputs")


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return load(TOOL, "q0var_freeze_tool_under_test")


@pytest.fixture(scope="module")
def plan_tool():
    return load(PLAN_TOOL, "q0_plan_tool_for_freeze_test")


def make_plan(plan_tool, base: Path, run_root: Path) -> Path:
    plan = plan_tool.build(python=PYW, run_root=run_root)
    out = base / "plan_draft"
    plan_tool.rp.emit(out, "q0", plan, "test")
    return out / "q0.plan.json"


def approval(tool, path: Path, plan_file: Path, run_root: Path, *, plan_sha=None, plan_dir=None, entry=None, not_after="2026-10-10T17:00:00Z", invocation="e" * 64):
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    path.write_text("# test\n\n" + "\n".join([
        f"APPROVED_PLAN_SHA256: `{plan_sha or plan['plan_sha256']}`", f"APPROVED_NOT_AFTER_UTC: {not_after}", f"APPROVED_PLAN_DIR: {plan_dir or run_root / 'q0' / 'plan'}",
        f"APPROVED_ENTRYPOINT_SHA256: `{entry or tool.ci.sha256_file(tool.FIXED['entry'])}`", f"APPROVED_INVOCATION_SHA256: `{invocation}`"]) + "\n", encoding="utf-8")
    return path


@pytest.fixture(params=["repair-run-q0a-r120", "repair-run-q0b-r121"])
def real_name_root(request):
    return REAL_OUTPUTS / request.param                                                                         # chi dung TEN (build() thuan, khong tao thu muc)


def test_profile_and_freeze_for_a_valid_run_root_have_n_1200_and_the_fixed_pins(tool, plan_tool, tmp_path, real_name_root):
    plan_file = make_plan(plan_tool, tmp_path, real_name_root)
    profile, freeze = tool.build(plan_file=plan_file, approval_file=approval(tool, tmp_path / "a.md", plan_file, real_name_root), windows=WINDOWS, run_root=real_name_root)
    assert profile["renew_seconds"] == 1200 and profile["plan_total_budget_s"] == 600.0 and "1 x check_only_cap 120" in profile["formula"] and profile["plan"] == "q0_selftest"
    assert profile["argv"][-1] == str(real_name_root / "evidence_archive" / "q0v") and profile["outputs"]["live"] == str(real_name_root / "live_crawl_idle_evidence.json")
    assert profile["stdout_stderr"] == str(real_name_root / "q0v_publisher.log")
    p = freeze["plans"]["q0"]
    assert freeze["kind"] == "q0_freeze" and freeze["run_root"] == str(real_name_root) and p["plan_dir"] == str(real_name_root / "q0" / "plan") and p["launcher_logs"] == str(real_name_root / "q0" / "logs")
    assert p["output"] == str(real_name_root / "q0" / "q0_receipt.json") and p["total_budget_s"] == 600.0
    assert set(freeze["pins"]) == set(tool.FIXED) and {"observer_module", "quiescence_model", "q0_script"} <= set(freeze["pins"]) and all(len(v["sha256"]) == 64 for v in freeze["pins"].values())


def test_the_freeze_pin_set_is_exactly_the_operator_pin_set(tool):
    operator = load(OPERATOR, "operator_q0var_for_freeze_test")
    assert {name: str(path) for name, path in tool.FIXED.items()} == {name: str(path) for name, path in operator.FIXED_PIN_PATHS.items()}
    assert tool.RUN_NAME_RE.pattern == operator.RUN_NAME_RE.pattern


def test_the_generated_profile_passes_the_reviewed_supervisor_profile_validator(tool, plan_tool, tmp_path, real_name_root):
    sv = load(SV, "supervisor_for_q0var_freeze_test")
    plan_file = make_plan(plan_tool, tmp_path, real_name_root)
    profile, freeze = tool.build(plan_file=plan_file, approval_file=approval(tool, tmp_path / "a.md", plan_file, real_name_root), windows=WINDOWS, run_root=real_name_root)
    assert sv.validate_profile(profile, expected_source_sha256=freeze["pins"]["source"]["sha256"], expected_interpreter_sha256=freeze["pins"]["interpreter"]["sha256"]) == []


@pytest.mark.parametrize("override,why", [
    ({"plan_sha": "0" * 64}, "approval_plan_sha256"), ({"plan_dir": r"D:\elsewhere\plan"}, "approval_plan_dir"), ({"entry": "1" * 64}, "approval_entrypoint"),
    ({"not_after": "2026-10-10T15:00:00Z"}, "approval_expires_before_hard_not_after")])
def test_every_approval_binding_violation_is_refused(tool, plan_tool, tmp_path, override, why):
    run_root = REAL_OUTPUTS / "repair-run-q0a-r120"
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    with pytest.raises(ValueError, match=why):
        tool.build(plan_file=plan_file, approval_file=approval(tool, tmp_path / "a.md", plan_file, run_root, **override), windows=WINDOWS, run_root=run_root)


@pytest.mark.parametrize("name,ok", [
    ("repair-run-q0a-r120", True), ("repair-run-q0b-r001", True), ("repair-run-20261009", False), ("repair-run-q0c-r001", False), ("repair-run-q0a-r12", False), ("repair-run-q0a-r1234", False),
    ("repair-run-q0a-r120-x", False), ("x-repair-run-q0a-r120", False), ("repair-run-q0A-r120", False), ("repair-run-probe-r105", False)])
def test_only_the_q0a_q0b_run_root_names_under_outputs_are_accepted(tool, name, ok):
    if ok:
        tool.check_run_root(REAL_OUTPUTS / name)
    else:
        with pytest.raises(ValueError, match="run_root_must_be_outputs_repair_run_q0a_or_q0b_rNNN"):
            tool.check_run_root(REAL_OUTPUTS / name)


def test_a_run_root_under_another_parent_is_refused(tool):
    with pytest.raises(ValueError, match="run_root_must_be_outputs"):
        tool.check_run_root(Path(r"D:\elsewhere") / "repair-run-q0a-r120")
    with pytest.raises(ValueError, match="run_root_must_be_outputs"):
        tool.check_run_root(REAL_OUTPUTS / "nested" / "repair-run-q0a-r120")


def test_windows_must_be_ordered_and_the_plan_must_belong_to_the_run_root(tool, plan_tool, tmp_path):
    run_root = REAL_OUTPUTS / "repair-run-q0a-r120"
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    ap = approval(tool, tmp_path / "a.md", plan_file, run_root)
    with pytest.raises(ValueError, match="windows_order"):
        tool.build(plan_file=plan_file, approval_file=ap, windows={**WINDOWS, "start_deadline_utc": "2026-10-10T12:00:00Z"}, run_root=run_root)
    other_root = REAL_OUTPUTS / "repair-run-q0b-r121"                                                            # plan dung cho run-root khac (Q0-B) khong duoc dung cho Q0-A
    other_plan = make_plan(plan_tool, tmp_path / "o", other_root)
    with pytest.raises(ValueError, match="plan_paths_differ_from_run_root"):
        tool.build(plan_file=other_plan, approval_file=approval(tool, tmp_path / "b.md", other_plan, run_root), windows=WINDOWS, run_root=run_root)


def test_a_plan_with_a_q0_pin_or_the_wrong_shape_is_refused(tool, plan_tool, tmp_path):
    run_root = REAL_OUTPUTS / "repair-run-q0a-r120"
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    doc = json.loads(plan_file.read_text(encoding="utf-8"))
    doc["document"]["q0_receipt_sha256"] = "a" * 64
    pinned = tmp_path / "pinned.plan.json"
    pinned.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="must_not_pin_a_q0_receipt"):
        tool.build(plan_file=pinned, approval_file=approval(tool, tmp_path / "a.md", pinned, run_root), windows=WINDOWS, run_root=run_root)
    def other_out(doc):                                                                                          # `outputs` dung nhung argv --out tro receipt khac
        argv = doc["document"]["specs"][0]["argv"]
        argv[argv.index("--out") + 1] = str(REAL_OUTPUTS / "repair-run-q0b-r121" / "q0" / "q0_receipt.json")
    for index, (mutate, why) in enumerate([(other_out, "plan_paths_differ_from_run_root"), (lambda d: d["document"]["specs"][0].__setitem__("outputs", []), "plan_paths_differ_from_run_root"),
                                           (lambda d: d["document"]["specs"][0].__setitem__("deadline_s", 300.0), "outer_deadline_180"), (lambda d: d["document"]["specs"][0].__setitem__("kind", "write"), "one_readonly_q0_selftest"),
                                           (lambda d: d["document"]["specs"][0].__setitem__("name", "innodb_probe"), "one_readonly_q0_selftest"), (lambda d: d["document"]["specs"].append(dict(d["document"]["specs"][0])), "one_readonly_q0_selftest")]):
        bad = json.loads(plan_file.read_text(encoding="utf-8"))
        mutate(bad)
        path = tmp_path / f"bad{index}.plan.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError, match=why):
            tool.build(plan_file=path, approval_file=approval(tool, tmp_path / f"ab{index}.md", path, run_root), windows=WINDOWS, run_root=run_root)


def test_main_writes_profile_and_freeze_exclusively_and_creates_the_empty_plan_dir(tool, plan_tool, tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "outputs").mkdir(parents=True)
    monkeypatch.setattr(tool, "ROOT", root)
    run_root = root / "outputs" / "repair-run-q0a-r120"
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    ap = approval(tool, tmp_path / "a.md", plan_file, run_root)
    args = ["--plan", str(plan_file), "--approval", str(ap), "--not-before-utc", WINDOWS["not_before_utc"], "--start-deadline-utc", WINDOWS["start_deadline_utc"], "--hard-not-after-utc", WINDOWS["hard_not_after_utc"],
            "--run-root", str(run_root)]
    assert tool.main(args) == 0
    freeze = json.loads((run_root / "q0v_freeze.json").read_text(encoding="utf-8"))
    assert freeze["profile"]["sha256"] == tool.ci.sha256_file(run_root / "renewal_profile_q0v.json") and freeze["profile"]["renew_seconds"] == 1200
    assert (run_root / "q0" / "plan").is_dir() and not any((run_root / "q0" / "plan").iterdir()) and not (run_root / "q0" / "q0_receipt.json").exists() and not (run_root / "q0" / "logs").exists()
    assert tool.main(args) == 1


def test_main_refuses_a_run_root_that_already_has_a_q0_dir_and_writes_nothing(tool, plan_tool, tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "outputs").mkdir(parents=True)
    monkeypatch.setattr(tool, "ROOT", root)
    run_root = root / "outputs" / "repair-run-q0b-r121"
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    ap = approval(tool, tmp_path / "a.md", plan_file, run_root)
    (run_root / "q0").mkdir(parents=True)                                                                        # dir cha ton tai san => khong con "moi"
    args = ["--plan", str(plan_file), "--approval", str(ap), "--not-before-utc", WINDOWS["not_before_utc"], "--start-deadline-utc", WINDOWS["start_deadline_utc"], "--hard-not-after-utc", WINDOWS["hard_not_after_utc"],
            "--run-root", str(run_root)]
    assert tool.main(args) == 1 and not (run_root / "q0v_freeze.json").exists() and not (run_root / "renewal_profile_q0v.json").exists()


def test_main_refuses_a_bad_run_root_name_before_touching_the_disk(tool, plan_tool, tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "outputs").mkdir(parents=True)
    monkeypatch.setattr(tool, "ROOT", root)
    run_root = root / "outputs" / "repair-run-q0c-r120"
    plan_file = make_plan(plan_tool, tmp_path, root / "outputs" / "repair-run-q0a-r120")
    ap = approval(tool, tmp_path / "a.md", plan_file, run_root)
    args = ["--plan", str(plan_file), "--approval", str(ap), "--not-before-utc", WINDOWS["not_before_utc"], "--start-deadline-utc", WINDOWS["start_deadline_utc"], "--hard-not-after-utc", WINDOWS["hard_not_after_utc"],
            "--run-root", str(run_root)]
    assert tool.main(args) == 1 and not run_root.exists()
