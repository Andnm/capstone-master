"""repair_probe_freeze_v1: dung profile + freeze manifest cho workflow probe InnoDB tu plan + approval da co (thuan, khong DB). Plan sinh bang CHINH builder `repair_innodb_probe_plan_v1` (run-root tam)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

AUDIT = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools")
TOOL = AUDIT / "repair_probe_freeze_v1.py"
PLAN_TOOL = AUDIT / "repair_innodb_probe_plan_v1.py"
SV = AUDIT / "renewal_supervisor_v1.py"
PYW = r"D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe"
WINDOWS = {"not_before_utc": "2026-10-10T12:30:00Z", "start_deadline_utc": "2026-10-10T13:30:00Z", "hard_not_after_utc": "2026-10-10T16:00:00Z"}


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return load(TOOL, "probe_freeze_tool_under_test")


@pytest.fixture(scope="module")
def plan_tool():
    return load(PLAN_TOOL, "probe_plan_tool_for_freeze_test")


def make_plan(plan_tool, base: Path, run_root: Path) -> Path:
    plan = plan_tool.build(python=PYW, run_root=run_root, crawl_evidence=run_root / "live_crawl_idle_evidence.json")
    out = base / "plan_draft"
    plan_tool.rp.emit(out, "innodb_probe", plan, "test")
    return out / "innodb_probe.plan.json"


def approval(tool, path: Path, plan_file: Path, run_root: Path, *, plan_sha=None, plan_dir=None, entry=None, not_after="2026-10-10T17:00:00Z", invocation="e" * 64):
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    path.write_text("# test\n\n" + "\n".join([
        f"APPROVED_PLAN_SHA256: `{plan_sha or plan['plan_sha256']}`", f"APPROVED_NOT_AFTER_UTC: {not_after}", f"APPROVED_PLAN_DIR: {plan_dir or run_root / 'probe' / 'plan'}",
        f"APPROVED_ENTRYPOINT_SHA256: `{entry or tool.ci.sha256_file(tool.FIXED['entry'])}`", f"APPROVED_INVOCATION_SHA256: `{invocation}`"]) + "\n", encoding="utf-8")
    return path


def test_profile_and_freeze_for_the_real_run_root_have_n_900_and_the_fixed_pins(tool, plan_tool, tmp_path):
    run_root = tool.RUN
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    profile, freeze = tool.build(plan_file=plan_file, approval_file=approval(tool, tmp_path / "a.md", plan_file, run_root), windows=WINDOWS, run_root=run_root)
    assert profile["renew_seconds"] == 900 and profile["plan_total_budget_s"] == 240.0 and "1 x check_only_cap 120" in profile["formula"]
    assert profile["argv"][-1] == str(run_root / "evidence_archive" / "probe") and profile["outputs"]["live"] == str(run_root / "live_crawl_idle_evidence.json")
    p = freeze["plans"]["probe"]
    assert freeze["kind"] == "probe_freeze" and p["plan_dir"] == str(run_root / "probe" / "plan") and p["launcher_logs"] == str(run_root / "probe" / "logs") and p["output"] == str(run_root / "probe" / "innodb_probe_report.json")
    assert set(freeze["pins"]) == set(tool.FIXED) and {"probe_script", "gate_module"} <= set(freeze["pins"]) and all(len(v["sha256"]) == 64 for v in freeze["pins"].values())


def test_the_generated_profile_passes_the_reviewed_supervisor_profile_validator(tool, plan_tool, tmp_path):
    sv = load(SV, "supervisor_for_probe_freeze_test")
    run_root = tool.RUN
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    profile, freeze = tool.build(plan_file=plan_file, approval_file=approval(tool, tmp_path / "a.md", plan_file, run_root), windows=WINDOWS, run_root=run_root)
    assert sv.validate_profile(profile, expected_source_sha256=freeze["pins"]["source"]["sha256"], expected_interpreter_sha256=freeze["pins"]["interpreter"]["sha256"]) == []


@pytest.mark.parametrize("override,why", [
    ({"plan_sha": "0" * 64}, "approval_plan_sha256"), ({"plan_dir": r"D:\elsewhere\plan"}, "approval_plan_dir"), ({"entry": "1" * 64}, "approval_entrypoint"),
    ({"not_after": "2026-10-10T15:00:00Z"}, "approval_expires_before_hard_not_after")])
def test_every_approval_binding_violation_is_refused(tool, plan_tool, tmp_path, override, why):
    run_root = tool.RUN
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    with pytest.raises(ValueError, match=why):
        tool.build(plan_file=plan_file, approval_file=approval(tool, tmp_path / "a.md", plan_file, run_root, **override), windows=WINDOWS, run_root=run_root)


def test_windows_must_be_ordered_and_the_plan_must_belong_to_the_run_root(tool, plan_tool, tmp_path):
    run_root = tool.RUN
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    ap = approval(tool, tmp_path / "a.md", plan_file, run_root)
    with pytest.raises(ValueError, match="windows_order"):
        tool.build(plan_file=plan_file, approval_file=ap, windows={**WINDOWS, "start_deadline_utc": "2026-10-10T12:00:00Z"}, run_root=run_root)
    other_root = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-probe-other")
    other_plan = make_plan(plan_tool, tmp_path / "o", other_root)
    with pytest.raises(ValueError, match="plan_paths_differ_from_run_root"):
        tool.build(plan_file=other_plan, approval_file=approval(tool, tmp_path / "b.md", other_plan, run_root), windows=WINDOWS, run_root=run_root)


def test_a_plan_with_a_q0_pin_or_the_wrong_shape_is_refused(tool, plan_tool, tmp_path):
    run_root = tool.RUN
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    doc = json.loads(plan_file.read_text(encoding="utf-8"))
    doc["document"]["q0_receipt_sha256"] = "a" * 64
    pinned = tmp_path / "pinned.plan.json"
    pinned.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="must_not_pin_a_q0_receipt"):
        tool.build(plan_file=pinned, approval_file=approval(tool, tmp_path / "a.md", pinned, run_root), windows=WINDOWS, run_root=run_root)
    doc = json.loads(plan_file.read_text(encoding="utf-8"))
    doc["document"]["specs"][0]["deadline_s"] = 300.0
    slow = tmp_path / "slow.plan.json"
    slow.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="outer_deadline_30"):
        tool.build(plan_file=slow, approval_file=approval(tool, tmp_path / "b.md", slow, run_root), windows=WINDOWS, run_root=run_root)


def test_main_writes_profile_and_freeze_exclusively_and_creates_the_empty_plan_dir(tool, plan_tool, tmp_path):
    run_root = tmp_path / "run"
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    ap = approval(tool, tmp_path / "a.md", plan_file, run_root)
    args = ["--plan", str(plan_file), "--approval", str(ap), "--not-before-utc", WINDOWS["not_before_utc"], "--start-deadline-utc", WINDOWS["start_deadline_utc"], "--hard-not-after-utc", WINDOWS["hard_not_after_utc"],
            "--run-root", str(run_root)]
    assert tool.main(args) == 0
    freeze = json.loads((run_root / "probe_freeze.json").read_text(encoding="utf-8"))
    assert freeze["profile"]["sha256"] == tool.ci.sha256_file(run_root / "renewal_profile_probe.json") and freeze["profile"]["renew_seconds"] == 900
    assert (run_root / "probe" / "plan").is_dir() and not any((run_root / "probe" / "plan").iterdir()) and not (run_root / "probe" / "innodb_probe_report.json").exists()
    assert tool.main(args) == 1


def test_main_refuses_a_run_root_that_already_has_a_probe_dir_and_writes_nothing(tool, plan_tool, tmp_path):
    run_root = tmp_path / "run"
    plan_file = make_plan(plan_tool, tmp_path, run_root)
    ap = approval(tool, tmp_path / "a.md", plan_file, run_root)
    (run_root / "probe").mkdir(parents=True)                                                                     # dir cha ton tai san => khong con "moi"
    args = ["--plan", str(plan_file), "--approval", str(ap), "--not-before-utc", WINDOWS["not_before_utc"], "--start-deadline-utc", WINDOWS["start_deadline_utc"], "--hard-not-after-utc", WINDOWS["hard_not_after_utc"],
            "--run-root", str(run_root)]
    assert tool.main(args) == 1 and not (run_root / "probe_freeze.json").exists() and not (run_root / "renewal_profile_probe.json").exists()
