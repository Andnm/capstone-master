"""audit_tools/repair_plan_v1.py — dung ke hoach bat bien (Q0 + Ph1): spec hop le theo runner v2, hash tat dinh, doi bat ky gia tri nao => doi hash, ban nhap approval khong chua secret, emit khong ghi de."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "audit_tools"
spec = importlib.util.spec_from_file_location("repair_plan_v1", TOOLS / "repair_plan_v1.py")
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)
rr = rp.rr
PROJECT = TOOLS.parent
BACKEND = PROJECT / "backend"


@pytest.fixture
def paths(tmp_path):
    approval = tmp_path / "70-gpt-ph1-approval.md"
    approval.write_text("APPROVED_CONTROLLER_SHA256: x\n", encoding="utf-8")
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    return {"approval": approval, "evidence": evidence, "out": tmp_path / "q0.json", "datasets": tmp_path / "datasets", "python": sys.executable}


def ph1(paths, **over):
    kwargs = dict(python=paths["python"], backend=BACKEND, datasets_dir=paths["datasets"], producer_approval=paths["approval"], crawl_evidence=paths["evidence"])
    kwargs.update(over)
    return rp.ph1_specs(**kwargs)


def q0(paths, **over):
    kwargs = dict(python=paths["python"], backend=BACKEND, out_receipt=paths["out"])
    kwargs.update(over)
    return rp.q0_spec(**kwargs)


def test_the_child_environment_is_an_exact_allowlisted_dict_without_secrets():
    env = rp.child_env()
    assert set(env) == {"SystemRoot", "TEMP", "TMP", "PATH", "PYTHONUTF8", "PYTHONIOENCODING"} and set(env) <= rr.ENV_ALLOWLIST
    assert "Git" in env["PATH"] and env["PYTHONUTF8"] == "1" and not any(rr.SECRET_LIKE.search(k) for k in env)


def test_every_generated_spec_is_valid_for_the_runner_and_readonly(paths):
    specs = [q0(paths), *ph1(paths)]
    assert rp.validate_all(specs, rr.DEFAULT_SETTINGS) == [] and [s["kind"] for s in specs] == ["readonly"] * 3
    assert [s["name"] for s in specs] == ["q0_selftest", "ph1_old_ledger_h1", "ph1_old_ledger_h3"]


def test_the_q0_spec_runs_the_q0_script_in_the_backend_dir_with_a_byte_closure_and_a_pinned_output(paths):
    s = q0(paths)
    assert s["argv"][1] == str(TOOLS / "repair_q0_v1.py") and s["cwd"] == str(BACKEND) and s["outputs"] == [str(paths["out"])] and s["argv"][s["argv"].index("--out") + 1] == str(paths["out"])
    closure = set(s["required_files"])
    assert {str(TOOLS / "repair_q0_v1.py"), str(TOOLS / "repair_db_observer_v1.py"), str(TOOLS / "quiescence_model_v1.py"), str(BACKEND / "app" / "warehouse" / "connection.py")} <= closure
    assert not any(p.endswith(".env") for p in closure) and all(rr.file_sha256(p) == sha for p, sha in s["required_files"].items())


def test_the_ph1_specs_are_two_sequential_producer_runs_with_the_pinned_old_values_and_p4_over_both_versions(paths):
    h1, h3 = ph1(paths)
    for s, dataset in ((h1, "ds_20261006_dev1b"), (h3, "ds_20261006_dev3b")):
        argv = s["argv"]
        assert argv[argv.index("--dataset") + 1] == dataset and argv[argv.index("--db") + 1] == rp.EXISTING_DB and "--run" in argv and "--explain" not in argv
        assert argv[argv.index("--expected-samples-sha256") + 1] == rp.OLD_DATASETS[dataset]["samples_sha256"] and argv[argv.index("--expected-rows") + 1] == "146431"
        assert argv[argv.index("--snapshot-versions") + 1] == "ds_20261006_dev1b,ds_20261006_dev3b" and argv[argv.index("--expected-old-code-sha256") + 1] == rp.OLD_CODE_SHA256
        pins = [argv[i + 1] for i, a in enumerate(argv) if a == "--expected-config-sha256"]
        assert pins == [f"{v}={info['config_sha256']}" for v, info in rp.OLD_DATASETS.items()]
        assert {str(TOOLS / "warehouse_flag_ledger_v1.py"), str(TOOLS / "repair_parity_v1.py"), str(TOOLS / "warehouse_audit_v3.py"), str(paths["approval"])} <= set(s["required_files"])
        assert s["cwd"] == str(BACKEND) and "outputs" not in s
    assert h1["argv"][h1["argv"].index("--dataset-dir") + 1] != h3["argv"][h3["argv"].index("--dataset-dir") + 1]


def test_the_plan_hash_is_deterministic_and_matches_the_runner_function(paths):
    specs = ph1(paths)
    one = rp.build_plan(specs, rr.DEFAULT_SETTINGS, q0_receipt_sha256=None, launcher_source_sha256="j" * 64)
    two = rp.build_plan(copy.deepcopy(specs), dict(rr.DEFAULT_SETTINGS), q0_receipt_sha256=None, launcher_source_sha256="j" * 64)
    assert one["plan_sha256"] == two["plan_sha256"] == rr.plan_sha256(specs, rr.DEFAULT_SETTINGS, one["document"]["expected_tooling"], None)
    assert one["document"]["expected_tooling"]["windows_job_sha256"] == "j" * 64 and one["document"]["expected_tooling"]["runner_sha256"] == rr.file_sha256(TOOLS / "repair_phase_runner_v2.py")


@pytest.mark.parametrize("change", ["settings", "launcher", "q0_pin", "approval_bytes", "evidence_path", "deadline", "order", "dataset_dir", "python", "producer_flag"])
def test_changing_any_input_changes_the_plan_hash(paths, change):
    specs = ph1(paths)
    base = rp.build_plan(specs, rr.DEFAULT_SETTINGS, q0_receipt_sha256=None, launcher_source_sha256="j" * 64)["plan_sha256"]
    settings, launcher, pin = rr.DEFAULT_SETTINGS, "j" * 64, None
    if change == "settings":
        settings = {**rr.DEFAULT_SETTINGS, "margin_s": 61.0}
    elif change == "launcher":
        launcher = "k" * 64
    elif change == "q0_pin":
        pin = "5" * 64
    elif change == "approval_bytes":
        paths["approval"].write_text("APPROVED_CONTROLLER_SHA256: y\n", encoding="utf-8")
        specs = ph1(paths)
    elif change == "evidence_path":
        specs = ph1(paths, crawl_evidence=paths["evidence"].with_name("other_evidence.json"))
        paths["evidence"].with_name("other_evidence.json").write_text("{}", encoding="utf-8")
    elif change == "deadline":
        specs = ph1(paths, deadline_s=901.0)
    elif change == "order":
        specs = list(reversed(specs))
    elif change == "dataset_dir":
        specs = ph1(paths, datasets_dir=paths["datasets"].with_name("datasets2"))
    elif change == "python":
        specs = ph1(paths, python=str(Path(sys.executable).with_name("pythonw.exe")))
    elif change == "producer_flag":
        specs = ph1(paths, max_batches=161)
    assert rp.build_plan(specs, settings, q0_receipt_sha256=pin, launcher_source_sha256=launcher)["plan_sha256"] != base, change


def test_building_a_plan_with_a_missing_file_or_invalid_spec_or_settings_fails_loudly(paths):
    with pytest.raises(FileNotFoundError):
        ph1(paths, producer_approval=paths["approval"].with_name("does-not-exist.md"))
    specs = ph1(paths)
    with pytest.raises(ValueError, match="plan_invalid"):
        rp.build_plan(specs, {**rr.DEFAULT_SETTINGS, "total_budget_s": float("nan")}, q0_receipt_sha256=None, launcher_source_sha256="j" * 64)
    broken = copy.deepcopy(specs)
    broken[0]["env"]["DB_PASSWORD"] = "x"
    with pytest.raises(ValueError, match="plan_invalid"):
        rp.build_plan(broken, rr.DEFAULT_SETTINGS, q0_receipt_sha256=None, launcher_source_sha256="j" * 64)


def test_the_approval_draft_carries_the_plan_hash_every_phase_hash_and_no_secret_looking_content(paths):
    plan = rp.build_plan(ph1(paths), rr.DEFAULT_SETTINGS, q0_receipt_sha256=None, launcher_source_sha256="j" * 64)
    text = rp.render_approval_draft(plan, title="Ph1")
    assert rr.parse_approved_plan(text) == plan["plan_sha256"] and "BAN NHAP" in text
    keys = [line.split(":", 1)[0] for line in text.splitlines() if line.startswith("APPROVED_")]
    assert keys == list(rr.OUTER_APPROVAL_KEYS)                                                                  # ban nhap liet ke DUNG nam khoa cua approval ngoai
    assert f"APPROVED_ENTRYPOINT_SHA256: `{rr.file_sha256(TOOLS / 'repair_run_v1.py')}`" in text               # hash entry point da dien san
    with pytest.raises(ValueError):
        rr.parse_outer_approval(text)                                                                            # ban nhap KHONG the dung lam approval that (con placeholder)
    for spec_ in plan["document"]["specs"]:
        assert rr.spec_sha256(spec_) in text and rr.argv_sha256(spec_) in text and spec_["name"] in text
    assert "DB_PASSWORD" not in text and "password" not in text.lower()


def test_emit_writes_plan_json_and_draft_exclusively_and_never_overwrites(paths, tmp_path):
    plan = rp.build_plan([q0(paths)], rr.DEFAULT_SETTINGS, q0_receipt_sha256=None, launcher_source_sha256="j" * 64)
    out = tmp_path / "proposal"
    written = rp.emit(out, "q0", plan, "Q0")
    assert sorted(Path(p).name for p in written) == ["q0.approval_DRAFT.md", "q0.plan.json"] and json.loads((out / "q0.plan.json").read_text(encoding="utf-8"))["plan_sha256"] == plan["plan_sha256"]
    with pytest.raises(FileExistsError):
        rp.emit(out, "q0", plan, "Q0")


def test_the_generated_plan_runs_through_the_runner_preflight_with_fakes_and_stops_at_the_gate(paths, tmp_path):
    """Ke hoach that tu builder di qua preflight cua runner v2 (hash khop, thu muc trong) roi bi gate dong chan => KHONG co launch."""
    import datetime as dt
    specs = ph1(paths)
    plan = rp.build_plan(specs, rr.DEFAULT_SETTINGS, q0_receipt_sha256=None, launcher_source_sha256="j" * 64)

    class Launcher:
        source_sha256 = "j" * 64
        calls = []

        def launch(self, *a, **k):
            self.calls.append(a)
            raise AssertionError("must not launch")

    class Clock:
        t = 1000.0

        def monotonic(self):
            return self.t

        def sleep(self, s):
            self.t += s

    launcher, clock = Launcher(), Clock()
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    now = dt.datetime(2026, 10, 9, 11, 0, tzinfo=dt.timezone.utc)
    runner = rr.Runner(plan_dir=plan_dir, specs=specs, settings=rr.DEFAULT_SETTINGS, expected_tooling=plan["document"]["expected_tooling"], q0_receipt_sha256=None, approved_plan_sha256=plan["plan_sha256"],
                       launcher=launcher, clock=clock, now_utc=lambda: now, gate=lambda: (False, ["crawler_busy"]))
    receipts = runner.run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"] == ["gate_closed: ['crawler_busy']"] and launcher.calls == [] and receipts[0]["computed_plan_sha256"] == plan["plan_sha256"]
