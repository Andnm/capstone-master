"""repair_innodb_probe_plan_v1: ke hoach readonly MOT phase cho probe InnoDB (GPT 104): outer deadline 30.0 s, output tuyet doi moi, pin nguon + phu thuoc, cwd backend, env 6 bien, khong Q0. Thuan, khong DB."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\repair_innodb_probe_plan_v1.py")
HERE = TOOL.parent
PYW = r"D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe"
RUN_ROOT = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-probe-r105")
LIVE = RUN_ROOT / "live_crawl_idle_evidence.json"


def load():
    spec = importlib.util.spec_from_file_location("probe_plan_tool_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def tool():
    return load()


@pytest.fixture(scope="module")
def plan(tool):
    return tool.build(python=PYW, run_root=RUN_ROOT, crawl_evidence=LIVE)


def spec_of(plan):
    (spec,) = plan["document"]["specs"]
    return spec


def test_the_plan_is_one_readonly_phase_with_a_mandatory_30_second_outer_deadline(tool, plan):
    spec = spec_of(plan)
    assert spec["name"] == "innodb_probe" and spec["kind"] == "readonly" and spec["deadline_s"] == 30.0 == tool.OUTER_DEADLINE_S
    assert len(plan["document"]["specs"]) == 1 and plan["document"]["q0_receipt_sha256"] is None


def test_argv_cwd_env_and_output_are_exact_absolute_and_new(tool, plan):
    spec = spec_of(plan)
    out = str(RUN_ROOT / "probe" / "innodb_probe_report.json")
    assert spec["argv"] == [PYW, str(HERE / "repair_innodb_probe_v1.py"), "--crawl-evidence", str(LIVE), "--out", out] and "--q0-receipt" not in spec["argv"]
    assert spec["cwd"] == str(HERE.parent / "backend") and spec["outputs"] == [out]
    assert spec["env"] == {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Windows\Temp", "TMP": r"C:\Windows\Temp", "PATH": r"C:\Program Files\Git\cmd;C:\Windows\System32;C:\Windows", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    assert not Path(out).exists()                                                                                # output phai MOI tai thoi diem freeze


def test_required_files_pin_the_source_the_gate_module_the_settings_closure_and_the_interpreter(tool, plan):
    spec = spec_of(plan)
    expected = [PYW, HERE / "repair_innodb_probe_v1.py", HERE / "warehouse_audit_v3.py", *[HERE.parent / "backend" / rel for rel in ("app/core/config.py", "app/warehouse/connection.py", "app/warehouse/naming.py", "app/warehouse/errors.py")]]
    assert set(spec["required_files"]) == {str(Path(p)) for p in expected}
    for path, digest in spec["required_files"].items():
        assert digest == sha(path), path
    assert spec["argv"][0] in spec["required_files"]


def test_the_budget_covers_deadline_plus_reserve_and_the_plan_is_deterministic(tool, plan):
    assert plan["document"]["settings"]["total_budget_s"] == 240.0                                               # 60 margin + 120 slack + 30 deadline + 15 reserve = 225 -> 240
    again = tool.build(python=PYW, run_root=RUN_ROOT, crawl_evidence=LIVE)
    assert again["plan_sha256"] == plan["plan_sha256"] and again["document"] == plan["document"]


def test_any_changed_parameter_changes_the_plan_hash(tool, plan):
    other_root = tool.build(python=PYW, run_root=Path(r"D:\MSE\CAPSTONE\outputs\repair-run-probe-other"), crawl_evidence=Path(r"D:\MSE\CAPSTONE\outputs\repair-run-probe-other\live_crawl_idle_evidence.json"))
    assert other_root["plan_sha256"] != plan["plan_sha256"]


@pytest.mark.parametrize("kwargs", [dict(python="python.exe"), dict(python="D:/MSE/CAPSTONE/hotel-price-intelligence/eda/.venv/Scripts/python.exe"), dict(run_root=Path("relative")), dict(crawl_evidence=Path("live.json"))])
def test_relative_or_forward_slash_paths_are_refused(tool, kwargs):
    args = dict(python=PYW, run_root=RUN_ROOT, crawl_evidence=LIVE)
    args.update(kwargs)
    with pytest.raises(ValueError, match="must_be_absolute_backslash_path"):
        tool.build(**args)


def test_the_plan_passes_the_runner_validators_and_pins_the_current_runner_and_launcher(tool, plan):
    doc = plan["document"]
    assert tool.rr.validate_settings(doc["settings"]) == [] and tool.rr.validate_spec(spec_of(plan)) == []
    assert doc["expected_tooling"]["runner_sha256"] == sha(HERE / "repair_phase_runner_v2.py") and doc["expected_tooling"]["windows_job_sha256"] == sha(HERE / "windows_job_v1.py")
    assert plan["plan_sha256"] == tool.rr.plan_sha256(doc["specs"], doc["settings"], doc["expected_tooling"], None)


def test_main_writes_the_plan_and_a_non_authoritative_draft_exclusively(tool, tmp_path):
    out_dir = tmp_path / "drafts"
    args = ["--out-dir", str(out_dir), "--python", PYW, "--run-root", str(RUN_ROOT)]
    assert tool.main(args) == 0
    plan_file, draft = out_dir / "innodb_probe.plan.json", out_dir / "innodb_probe.approval_DRAFT.md"
    assert plan_file.is_file() and draft.is_file() and "BAN NHAP approval (chua co hieu luc)" in draft.read_text(encoding="utf-8")
    assert json.loads(plan_file.read_text(encoding="utf-8"))["plan_sha256"] == tool.build(python=PYW, run_root=RUN_ROOT, crawl_evidence=LIVE)["plan_sha256"]
    before = plan_file.read_bytes()
    assert tool.main(args) == 1 and plan_file.read_bytes() == before                                             # lan hai: khong ghi de


def test_the_budget_formula_includes_the_post_launch_reserve_not_just_the_deadline(tool):
    settings = dict(tool.rr.DEFAULT_SETTINGS)
    reserve = tool.rr.reserve_after_launch({"kind": "readonly", "deadline_s": 46.0}, settings)
    assert reserve == 15.0 and tool.suggest_total_budget({"kind": "readonly", "deadline_s": 46.0}, settings) == 300.0     # 60 + 120 + 46 + 15 = 241 -> 300 (neu thieu reserve: 226 -> 240)
