"""repair_heavy_ci_freeze_v1: dung profile + freeze manifest tu tep da co (thuan, khong DB). Phat sinh hai plan draft bang CHINH tool heavy-plan (report-dir nam trong run-root tam) + approval gia (tmp)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\repair_heavy_ci_freeze_v1.py")
SV = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\renewal_supervisor_v1.py")
HEAVY = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\repair_heavy_plan_v1.py")
PREDICT = Path(r"D:\MSE\CAPSTONE\outputs\repair-config-predictions-20261009-ph1")
PYW = r"D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe"
Q0 = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009\q0\q0_receipt.json")
WINDOWS = {"not_before_utc": "2026-10-09T12:30:00Z", "start_deadline_utc": "2026-10-09T13:30:00Z", "hard_not_after_utc": "2026-10-09T16:00:00Z"}


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tool():
    return load(TOOL, "freeze_tool_under_test")


def emit_drafts(base: Path) -> tuple[Path, Path]:
    """Phat sinh (thuan) hai plan draft bang chinh tool heavy-plan, report-dir nam trong <base>/run/h2/reports; tra (draft_dir, run_root)."""
    heavy = load(HEAVY, f"heavy_for_freeze_test_{abs(hash(str(base)))}")
    run_root = base / "run"
    assert heavy.main(["--stage", "clone", "--out-dir", str(base / "draft" / "clone"), "--python", PYW, "--q0-receipt", str(Q0)]) == 0
    assert heavy.main(["--stage", "init", "--out-dir", str(base / "draft" / "init"), "--python", PYW, "--q0-receipt", str(Q0), "--prediction-receipt", f"h1={PREDICT / 'prediction_receipt_dev1b.json'}",
                       "--prediction-receipt", f"h3={PREDICT / 'prediction_receipt_dev3b.json'}", "--report-dir", str(run_root / "h2" / "reports")]) == 0
    return base / "draft", run_root


def approval(tool, tmp_path, key, run_root, draft, *, plan_sha=None, plan_dir=None, entry=None, not_after="2026-10-09T17:00:00Z", invocation="e" * 64, name=None):
    plan = json.loads((draft / key / f"heavy_{key}.plan.json").read_text(encoding="utf-8"))
    path = tmp_path / (name or f"approval_{key}.md")
    path.write_text("# test approval\n\n" + "\n".join([
        f"APPROVED_PLAN_SHA256: `{plan_sha or plan['plan_sha256']}`", f"APPROVED_NOT_AFTER_UTC: {not_after}", f"APPROVED_PLAN_DIR: {plan_dir or run_root / key / 'plan'}",
        f"APPROVED_ENTRYPOINT_SHA256: `{entry or tool.sha256_file(tool.FIXED['entry'])}`", f"APPROVED_INVOCATION_SHA256: `{invocation}`"]) + "\n", encoding="utf-8")
    return draft / key / f"heavy_{key}.plan.json", path


@pytest.fixture(scope="module")
def shared_drafts(tmp_path_factory):
    return emit_drafts(tmp_path_factory.mktemp("freeze_drafts"))


def build(tool, tmp_path, shared, **overrides):
    draft, run_root = shared
    plans = {k: approval(tool, tmp_path, k, run_root, draft, **overrides.get(k, {})) for k in ("clone", "init")}
    return tool.build(q0_receipt=overrides.get("q0", Q0), plans=plans, windows=overrides.get("windows", WINDOWS), run_root=run_root), run_root, plans


def test_profile_and_freeze_are_built_from_the_real_drafts_with_n_3300(tool, tmp_path, shared_drafts):
    (profile, freeze), run_root, plans = build(tool, tmp_path, shared_drafts)
    assert profile["renew_seconds"] == 3300 and profile["plan_total_budget_s"] == 2640.0 and profile["argv"][profile["argv"].index("--renew-seconds") + 1] == "3300" and "2 x check_only_cap 120" in profile["formula"]
    assert profile["argv"][-1] == str(run_root / "evidence_archive" / "ci") and profile["outputs"]["live"] == str(run_root / "live_crawl_idle_evidence.json")
    assert freeze["kind"] == "heavy_ci_freeze" and set(freeze["plans"]) == {"clone", "init"} and freeze["q0_receipt"]["sha256"] == tool.sha256_file(Q0)
    assert freeze["plans"]["clone"]["plan_dir"] == str(run_root / "clone" / "plan") and freeze["plans"]["init"]["launcher_logs"] == str(run_root / "init" / "logs")
    assert freeze["plans"]["clone"]["total_budget_s"] == 1260.0 and freeze["plans"]["init"]["total_budget_s"] == 1380.0
    assert [Path(r).name for r in freeze["h2_reports"]] == ["verify_ds_20261010_nf1.json", "verify_ds_20261010_nf3.json"]
    assert set(freeze["pins"]) == set(tool.FIXED) and all(len(v["sha256"]) == 64 for v in freeze["pins"].values())


def test_the_generated_profile_passes_the_reviewed_supervisor_profile_validator(tool, tmp_path, shared_drafts):
    sv = load(SV, "supervisor_for_freeze_test")
    (profile, freeze), _, _ = build(tool, tmp_path, shared_drafts)
    assert sv.validate_profile(profile, expected_source_sha256=freeze["pins"]["source"]["sha256"], expected_interpreter_sha256=freeze["pins"]["interpreter"]["sha256"]) == []


@pytest.mark.parametrize("n_budget,n_plans,expected", [(2640.0, 2, 3300), (2640.0, 1, 3300), (2190.0, 2, 2700), (2191.0, 2, 3000), (4200.0, 1, 4800), (1260.0, 1, 1800)])
def test_renewal_n_follows_the_formula_including_the_check_only_cap_per_plan(tool, n_budget, n_plans, expected):
    assert tool.renew_seconds(n_budget, n_plans) == expected


@pytest.mark.parametrize("override,why", [
    ({"clone": {"plan_sha": "0" * 64}}, "approval_clone_plan_sha256"),
    ({"init": {"plan_dir": r"D:\elsewhere\plan"}}, "approval_init_plan_dir"),
    ({"clone": {"entry": "1" * 64}}, "approval_clone_entrypoint"),
    ({"init": {"not_after": "2026-10-09T15:00:00Z"}}, "approval_init_expires_before_hard_not_after"),
    ({"windows": {**WINDOWS, "start_deadline_utc": "2026-10-09T12:00:00Z"}}, "windows_order"),
])
def test_every_binding_violation_is_refused(tool, tmp_path, shared_drafts, override, why):
    with pytest.raises(ValueError, match=why):
        build(tool, tmp_path, shared_drafts, **override)


def test_a_plan_bound_to_another_q0_receipt_is_refused(tool, tmp_path, shared_drafts):
    other = tmp_path / "other_q0.json"
    receipt = json.loads(Q0.read_text(encoding="utf-8"))
    receipt["created_at_utc"] = "2026-10-09T20:00:00Z"
    other.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="q0_receipt_mismatch"):
        build(tool, tmp_path, shared_drafts, q0=other)


def test_parse_approval_needs_exactly_the_five_keys_once(tool, tmp_path):
    good = tmp_path / "a.md"
    good.write_text("\n".join(f"{k}: `{'x' * 5}`" for k in tool.APPROVAL_KEYS), encoding="utf-8")
    assert set(tool.parse_approval(good)) == set(tool.APPROVAL_KEYS)
    dup = tmp_path / "d.md"
    dup.write_text("APPROVED_PLAN_SHA256: a\nAPPROVED_PLAN_SHA256: b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        tool.parse_approval(dup)
    missing = tmp_path / "m.md"
    missing.write_text("APPROVED_PLAN_SHA256: a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="approval_keys"):
        tool.parse_approval(missing)


def test_main_writes_profile_and_freeze_exclusively_and_creates_empty_plan_dirs(tool, tmp_path):
    draft, run_root = emit_drafts(tmp_path)
    clone_plan, clone_ap = approval(tool, tmp_path, "clone", run_root, draft)
    init_plan, init_ap = approval(tool, tmp_path, "init", run_root, draft)
    args = ["--q0-receipt", str(Q0), "--clone-plan", str(clone_plan), "--clone-approval", str(clone_ap), "--init-plan", str(init_plan), "--init-approval", str(init_ap),
            "--not-before-utc", WINDOWS["not_before_utc"], "--start-deadline-utc", WINDOWS["start_deadline_utc"], "--hard-not-after-utc", WINDOWS["hard_not_after_utc"], "--run-root", str(run_root)]
    assert tool.main(args) == 0
    freeze = json.loads((run_root / "ci_freeze.json").read_text(encoding="utf-8"))
    assert freeze["profile"]["sha256"] == tool.sha256_file(run_root / "renewal_profile_ci.json") and freeze["profile"]["renew_seconds"] == 3300
    assert (run_root / "clone" / "plan").is_dir() and not any((run_root / "clone" / "plan").iterdir()) and (run_root / "init" / "plan").is_dir()
    assert tool.main(args) == 1                                                                                  # lan hai: run-root khong con moi => tu choi, khong ghi de
