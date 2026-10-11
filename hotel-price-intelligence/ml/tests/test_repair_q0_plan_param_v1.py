"""repair_q0_plan_param_v1: Q0 plan voi run-root tham so. Kiem chung then chot: voi run-root cua lan Q0 da review/chay (09/10) phai TAI TAO dung hash `6c5a18fe…`."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\repair_q0_plan_param_v1.py")
HERE = TOOL.parent
PYW = r"D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe"
REVIEWED_RUN_ROOT = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009")
REVIEWED_PLAN_SHA = "6c5a18fe50913d62fd0e8b30822b09d0b6fda23d39a14b3277ce8ab96ed4261c"
REVIEWED_PLAN_FILE = Path(r"D:\MSE\CAPSTONE\outputs\repair-proposal-20261009-r81\q0.plan.json")


def load():
    spec = importlib.util.spec_from_file_location("q0_param_tool_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return load()


def test_the_builder_reproduces_the_reviewed_and_executed_q0_plan_hash_for_the_reviewed_run_root(tool):
    plan = tool.build(python=PYW, run_root=REVIEWED_RUN_ROOT)
    assert plan["plan_sha256"] == REVIEWED_PLAN_SHA
    reviewed = json.loads(REVIEWED_PLAN_FILE.read_text(encoding="utf-8"))
    assert plan["document"] == reviewed["document"]                                                              # toan bo document (specs/settings/tooling) trung tung truong


def test_another_run_root_changes_only_the_paths_and_therefore_the_hash(tool):
    other = tool.build(python=PYW, run_root=Path(r"D:\MSE\CAPSTONE\outputs\repair-run-q0a-r115"))
    spec = other["document"]["specs"][0]
    assert other["plan_sha256"] != REVIEWED_PLAN_SHA and spec["name"] == "q0_selftest" and spec["kind"] == "readonly" and spec["deadline_s"] == 180.0
    assert spec["outputs"] == [r"D:\MSE\CAPSTONE\outputs\repair-run-q0a-r115\q0\q0_receipt.json"] and spec["argv"][spec["argv"].index("--out") + 1] == spec["outputs"][0]
    assert other["document"]["q0_receipt_sha256"] is None and other["document"]["settings"]["total_budget_s"] == 600.0


@pytest.mark.parametrize("kwargs", [dict(python="python.exe"), dict(python="D:/MSE/CAPSTONE/hotel-price-intelligence/eda/.venv/Scripts/python.exe"), dict(run_root=Path("relative"))])
def test_relative_or_forward_slash_paths_are_refused(tool, kwargs):
    args = dict(python=PYW, run_root=REVIEWED_RUN_ROOT)
    args.update(kwargs)
    with pytest.raises(ValueError, match="must_be_absolute_backslash_path"):
        tool.build(**args)


def test_required_files_are_pinned_with_real_hashes_and_the_plan_is_valid(tool):
    plan = tool.build(python=PYW, run_root=REVIEWED_RUN_ROOT)
    spec = plan["document"]["specs"][0]
    assert spec["argv"][0] in spec["required_files"]
    for path, digest in spec["required_files"].items():
        assert digest == hashlib.sha256(Path(path).read_bytes()).hexdigest(), path
    assert tool.rr.validate_settings(plan["document"]["settings"]) == [] and tool.rr.validate_spec(spec) == []


def test_main_writes_the_plan_and_a_non_authoritative_draft_exclusively(tool, tmp_path):
    args = ["--out-dir", str(tmp_path / "d"), "--python", PYW, "--run-root", r"D:\MSE\CAPSTONE\outputs\repair-run-q0b-r115"]
    assert tool.main(args) == 0
    assert "BAN NHAP approval (chua co hieu luc)" in (tmp_path / "d" / "q0.approval_DRAFT.md").read_text(encoding="utf-8")
    before = (tmp_path / "d" / "q0.plan.json").read_bytes()
    assert tool.main(args) == 1 and (tmp_path / "d" / "q0.plan.json").read_bytes() == before
