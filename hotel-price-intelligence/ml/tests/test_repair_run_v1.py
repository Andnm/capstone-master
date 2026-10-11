"""audit_tools/repair_run_v1.py — ENTRY POINT cuoi: moi kiem tra di qua `main()` (approval -> plan.json -> Q0 -> thanh phan -> Runner). Fake chi tiem qua `overrides`."""
from __future__ import annotations

import datetime as dt
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


run = _load(TOOLS / "repair_run_v1.py", "repair_run_v1")
rp = _load(TOOLS / "repair_plan_v1.py", "repair_plan_v1_for_run")
obs = _load(HERE / "test_repair_db_observer_v1.py", "observer_tests_for_run")
rr = run.rr
qm = rr.qm
NOW = obs.NOW
APP = obs.APP
SETTINGS = {**rr.DEFAULT_SETTINGS, "window_end_vn": None, "total_budget_s": 1800.0}


class FakeLauncher:
    source_sha256 = "j" * 64

    def __init__(self, clock, results=None):
        self.clock, self.results, self.calls = clock, list(results or []), []

    def launch(self, spec, cap_s, should_abort=None, gate_poll_s=5.0):
        self.calls.append(spec["name"])
        result = dict(self.results.pop(0)) if self.results else {}
        self.clock.t += 5
        base = {"exit_code": 0, "timed_out": False, "aborted_by_gate": None, "gate_polls": 0, "killed_tree": False, "contained": True, "pids_seen": [4242], "stdout_path": "x", "stderr_path": "y",
                "stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64, "started": 0.0, "ended": 1.0}
        base.update(result)
        return base


def write_outer_approval(path, *, plan_sha, not_after, plan_dir, entrypoint, invocation, omit=(), extra=""):
    values = {"APPROVED_PLAN_SHA256": plan_sha, "APPROVED_NOT_AFTER_UTC": not_after.strftime("%Y-%m-%dT%H:%M:%SZ"), "APPROVED_PLAN_DIR": str(plan_dir), "APPROVED_ENTRYPOINT_SHA256": entrypoint,
              "APPROVED_INVOCATION_SHA256": invocation}
    body = "# GPT outer approval (test)\n\n" + "\n".join(f"{k}: {v}" for k, v in values.items() if k not in omit) + "\n" + extra
    Path(path).write_text(body, encoding="utf-8")


class World:
    def __init__(self, tmp_path, *, write=False, settings=None):
        tmp_path.mkdir(exist_ok=True)
        self.tmp = tmp_path
        self.clock = obs.Clock()
        self.script = tmp_path / "work.py"
        self.script.write_text("print(1)\n", encoding="utf-8")
        self.plan_dir = tmp_path / "plan"
        self.plan_dir.mkdir()
        self.logs = tmp_path / "logs"
        self.evidence = tmp_path / "evidence.json"
        self.server = obs.FakeServer()
        self.q0_path = self.q0_sha = None
        specs = [self.spec("ph_a")]
        pin = None
        if write:
            self.q0_path, self.q0_sha, _ = obs.q0_for(tmp_path, self.server)
            self.server.uptime += 100
            specs.append(self.spec("ph_w", kind="write"))
            pin = self.q0_sha
        self.write = write
        self.observer_pin = obs.observer(self.server).tooling_sha256 if write else None                       # hash cong cu cua DbObserver trong tien trinh (test tiem truc tiep)
        self.plan = rp.build_plan(specs, settings or SETTINGS, q0_receipt_sha256=pin, launcher_source_sha256=FakeLauncher.source_sha256, observer_tooling_sha256=self.observer_pin)
        written = rp.emit(tmp_path / "proposal", "plan", self.plan, "test plan")
        self.plan_file = tmp_path / "proposal" / "plan.plan.json"
        self.draft = tmp_path / "proposal" / "plan.approval_DRAFT.md"
        assert str(self.plan_file) in written and self.draft.is_file()
        self.discuss = tmp_path / "discuss"
        self.discuss.mkdir()
        self.approval = self.discuss / "100-gpt-plan-approval.md"
        self.signed_plan_sha = self.plan["plan_sha256"]
        self.not_after = self.clock.now_utc() + dt.timedelta(hours=6)
        self.autosign = True
        self.sign()

    def invocation(self, *, q0=True, q0_path=None, plan_file=None, plan_dir=None, logs=None, evidence=None):
        q0_receipt = q0_path if q0_path is not None else (self.q0_path if q0 else None)
        return run.invocation_sha256(types.SimpleNamespace(plan=plan_file or self.plan_file, plan_dir=plan_dir or self.plan_dir, launcher_logs=logs or self.logs, crawl_evidence=evidence or self.evidence,
                                                           q0_receipt=q0_receipt))

    def sign(self, *, q0=True, q0_path=None, plan_sha=None, not_after=None, plan_dir=None, entrypoint=None, invocation=None, omit=(), extra=""):
        write_outer_approval(self.approval, plan_sha=plan_sha or self.signed_plan_sha, not_after=not_after or self.not_after, plan_dir=plan_dir or self.plan_dir, entrypoint=entrypoint or run.entrypoint_sha256(),
                             invocation=invocation or self.invocation(q0=q0, q0_path=q0_path), omit=omit, extra=extra)

    def spec(self, name, kind="readonly"):
        base = {"name": name, "kind": kind, "argv": [sys.executable, str(self.script)], "cwd": str(self.tmp), "env": rp.child_env(), "required_files": {str(self.script): rr.file_sha256(self.script)}, "deadline_s": 60}
        if kind == "write":
            base["db"] = {"app_user": APP, "db_scope": ["warehouse_dsdev_nf"], "protected_dbs": ["hotel_price_intel"]}
        return base

    def argv(self, *extra, q0=True):
        argv = ["--approval", str(self.approval), "--plan", str(self.plan_file), "--plan-dir", str(self.plan_dir), "--launcher-logs", str(self.logs), "--crawl-evidence", str(self.evidence), *extra]
        if q0 and self.q0_path is not None and "--q0-receipt" not in extra:
            argv += ["--q0-receipt", str(self.q0_path)]
        if self.autosign:                                                                                       # ky lai DUNG lenh goi nay (approval khop lenh goi); test nao can lech thi tat autosign / goi sign()
            custom = Path(extra[extra.index("--q0-receipt") + 1]) if "--q0-receipt" in extra else None
            self.sign(q0=q0, q0_path=custom)
        return argv

    def overrides(self, launcher=None, gate=None, observer="auto", **kw):
        launcher = launcher or FakeLauncher(self.clock)
        if observer == "auto":
            observer = obs.observer(self.server) if self.write else None
        return {"launcher": launcher, "gate": gate or (lambda: (True, [])), "clock": self.clock, "now_utc": self.clock.now_utc, "observer": observer, "discuss_dir": self.discuss, **kw}


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture
def wworld(tmp_path):
    return World(tmp_path / "w", write=True)


def summary_of(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def rewrite_plan(world, mutate, keep_consistent):
    data = json.loads(world.plan_file.read_text(encoding="utf-8"))
    mutate(data["document"])
    if keep_consistent:
        doc = data["document"]
        data["plan_sha256"] = rr.plan_sha256(doc["specs"], doc["settings"], doc["expected_tooling"], doc["q0_receipt_sha256"])
    world.plan_file.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


# =========================================================================== duong vui
def test_a_readonly_plan_runs_through_the_entry_point_and_exits_zero_with_a_secret_free_summary(world, capsys):
    launcher = FakeLauncher(world.clock)
    rc = run.main(world.argv(), overrides=world.overrides(launcher=launcher))
    summary = summary_of(capsys)
    assert rc == 0 and summary["ok"] is True and [p["verdict"] for p in summary["phases"]] == ["PASS"] and launcher.calls == ["ph_a"]
    assert summary["approved_plan_sha256"] == summary["computed_plan_sha256"] == world.plan["plan_sha256"]
    assert sorted(p.name for p in world.plan_dir.iterdir()) == ["PLAN_BOUND.json", "receipt_01_ph_a.json"] and "password" not in json.dumps(summary).lower()


def test_a_write_plan_runs_with_a_verified_q0_receipt_a_real_observer_and_exits_zero(wworld, capsys):
    launcher = obs.PhaseLauncher(wworld.clock, wworld.server, linger=0)
    rc = run.main(wworld.argv(), overrides=wworld.overrides(launcher=launcher))
    summary = summary_of(capsys)
    assert rc == 0 and [p["verdict"] for p in summary["phases"]] == ["PASS", "PASS"]
    receipt = json.loads((wworld.plan_dir / "receipt_02_ph_w.json").read_text(encoding="utf-8"))
    assert receipt["quiescence"]["status"] == qm.VERIFIED and receipt["q0_receipt_sha256"] == wworld.q0_sha and receipt["baseline"]["server_uuid"] == "uuid-fake-1"


ENTRY_WORKER = '''
import json, os, sys, time
def out(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
out({"ready": True, "pid": os.getpid()})
if len(sys.argv) > 2:
    open(sys.argv[2], "w", encoding="utf-8").write(json.dumps({k: os.environ.get(k) for k in ("PYTHONUTF8", "PYTHONIOENCODING", "TEMP")}))
for line in sys.stdin:
    req = json.loads(line); op, rid = req["op"], req["id"]
    if op == "close":
        out({"id": rid, "ok": True, "result": "closed"}); break
    if op == "baseline":
        out({"id": rid, "ok": True, "result": {"baseline_thread_id": 1000, "baseline_uptime_s": 5200.0, "probe_session_id": 5000, "server_uuid": "uuid-fake-1", "app_user": "hotel_app"}})
    elif sys.argv[1] == "hang_probe" and op == "probe":
        time.sleep(120)
    else:
        out({"id": rid, "ok": True, "result": {"status": "ok", "rows": [], "truncated": False, "rolling_back": [], "uptime_s": 5300.0 + time.monotonic()}})
'''


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object / worker chi tren Windows")
def test_the_entry_point_builds_a_bounded_observer_worker_for_a_write_plan_and_passes_with_a_healthy_worker(tmp_path, capsys):
    world = World(tmp_path / "w", write=True)
    proxy = run._load("proxy_for_test", "repair_observer_proxy_v1.py")
    pin = proxy.BoundedObserver([sys.executable, "x"], cwd=str(tmp_path), env=rp.child_env(), stderr_path=tmp_path / "unused.err").tooling_sha256
    data = json.loads(world.plan_file.read_text(encoding="utf-8"))
    data["document"]["expected_tooling"]["observer_sha256"] = pin                                               # plan thuc te pin hash cua BoundedObserver
    data["plan_sha256"] = rr.plan_sha256(data["document"]["specs"], data["document"]["settings"], data["document"]["expected_tooling"], data["document"]["q0_receipt_sha256"])
    world.plan_file.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    world.signed_plan_sha = data["plan_sha256"]
    worker = tmp_path / "entry_worker.py"
    worker.write_text(ENTRY_WORKER, encoding="utf-8")
    launcher = obs.PhaseLauncher(world.clock, world.server, linger=0)
    overrides = world.overrides(launcher=launcher, observer_worker_argv=[sys.executable, str(worker), "ok", str(tmp_path / "worker_env.json")], observer_cwd=str(tmp_path))
    del overrides["observer"]
    rc = run.main(world.argv(), overrides=overrides)
    summary = summary_of(capsys)
    receipt = json.loads((world.plan_dir / "receipt_02_ph_w.json").read_text(encoding="utf-8"))
    assert rc == 0 and [p["verdict"] for p in summary["phases"]] == ["PASS", "PASS"] and receipt["tooling"]["observer_sha256"] == pin
    assert (world.logs / "observer_worker.stderr").is_file()
    spec_env = data["document"]["specs"][1]["env"]
    assert json.loads((tmp_path / "worker_env.json").read_text(encoding="utf-8")) == {"PYTHONUTF8": spec_env["PYTHONUTF8"], "PYTHONIOENCODING": spec_env["PYTHONIOENCODING"], "TEMP": spec_env["TEMP"]}
    assert spec_env["PYTHONUTF8"] == "1"                                                                       # worker chay voi DUNG env da ghim trong spec phase write (khong thua ke, khong rong)


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object / worker chi tren Windows")
def test_the_entry_point_observer_uses_the_plan_caps_so_a_hung_probe_quarantines_without_hanging(tmp_path, capsys):
    import time
    world = World(tmp_path / "w", write=True)
    proxy = run._load("proxy_for_test2", "repair_observer_proxy_v1.py")
    pin = proxy.BoundedObserver([sys.executable, "x"], cwd=str(tmp_path), env=rp.child_env(), stderr_path=tmp_path / "unused2.err").tooling_sha256
    data = json.loads(world.plan_file.read_text(encoding="utf-8"))
    data["document"]["expected_tooling"]["observer_sha256"] = pin
    data["document"]["settings"].update(probe_cap_s=1.0, baseline_cap_s=5.0, settle_s=0.0)
    data["plan_sha256"] = rr.plan_sha256(data["document"]["specs"], data["document"]["settings"], data["document"]["expected_tooling"], data["document"]["q0_receipt_sha256"])
    world.plan_file.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    world.signed_plan_sha = data["plan_sha256"]
    worker = tmp_path / "entry_worker.py"
    worker.write_text(ENTRY_WORKER, encoding="utf-8")
    overrides = world.overrides(launcher=obs.PhaseLauncher(world.clock, world.server, linger=0), observer_worker_argv=[sys.executable, str(worker), "hang_probe"], observer_cwd=str(tmp_path))
    del overrides["observer"]
    started = time.monotonic()
    rc = run.main(world.argv(), overrides=overrides)
    summary = summary_of(capsys)
    assert rc == 4 and summary["phases"][-1]["verdict"] == "QUARANTINE" and time.monotonic() - started < 30
    assert (world.plan_dir / "QUARANTINE.json").exists()


# =========================================================================== approval / plan: moi drift ra khoi entry point
def test_a_missing_or_malformed_approval_exits_two_and_nothing_is_created_or_launched(world, capsys):
    launcher = FakeLauncher(world.clock)
    world.autosign = False
    for text in ("", "no hash here", f"APPROVED_PLAN_SHA256: {'a' * 64}\nAPPROVED_PLAN_SHA256: {'b' * 64}", "APPROVED_PLAN_SHA256: nothex", f"APPROVED_PLAN_SHA256: {'a' * 64}"):      # dong hash don le KHONG con du
        world.approval.write_text(text, encoding="utf-8")
        assert run.main(world.argv(), overrides=world.overrides(launcher=launcher)) == 2
        assert summary_of(capsys)["ok"] is False
    assert launcher.calls == [] and list(world.plan_dir.iterdir()) == [] and not world.logs.exists()


def test_an_approval_for_a_different_plan_is_refused_by_the_runner_before_gate_or_launch(world, capsys):
    world.signed_plan_sha = "a" * 64
    launcher = FakeLauncher(world.clock)
    gate_calls = []
    rc = run.main(world.argv(), overrides=world.overrides(launcher=launcher, gate=lambda: gate_calls.append(1) or (True, [])))
    summary = summary_of(capsys)
    assert rc == 3 and summary["phases"][0]["verdict"] == "REFUSED" and summary["phases"][0]["reasons"] == ["plan_sha256_not_approved"] and launcher.calls == [] and gate_calls == []


def test_an_edited_plan_file_with_a_stale_hash_field_is_rejected_as_inconsistent(world, capsys):
    rewrite_plan(world, lambda d: d["specs"][0]["argv"].append("--injected"), keep_consistent=False)
    launcher = FakeLauncher(world.clock)
    assert run.main(world.argv(), overrides=world.overrides(launcher=launcher)) == 2
    assert "plan_file_hash_inconsistent" in summary_of(capsys)["error"] and launcher.calls == []


@pytest.mark.parametrize("name,mutate", [
    ("argv", lambda d: d["specs"][0]["argv"].append("--injected")), ("deadline", lambda d: d["specs"][0].update(deadline_s=61)), ("settings", lambda d: d["settings"].update(margin_s=1.0)),
    ("tooling", lambda d: d["expected_tooling"].update(runner_sha256="0" * 64)), ("extra_phase", lambda d: d["specs"].append(dict(d["specs"][0], name="ph_extra"))),
    ("cwd", lambda d: d["specs"][0].update(cwd=d["specs"][0]["cwd"] + "x")), ("env", lambda d: d["specs"][0]["env"].update(TEMP="D:/other_temp")),
])
def test_a_plan_file_edited_AND_re_hashed_consistently_is_still_refused_because_the_approval_does_not_match(world, capsys, name, mutate):
    rewrite_plan(world, mutate, keep_consistent=True)
    launcher = FakeLauncher(world.clock)
    rc = run.main(world.argv(), overrides=world.overrides(launcher=launcher))
    summary = summary_of(capsys)
    assert rc == 3 and summary["phases"][0]["reasons"] == ["plan_sha256_not_approved"] and launcher.calls == [], name


@pytest.mark.parametrize("name,mutate", [("probe_cap_zero", lambda d: d["settings"].update(probe_cap_s=0)), ("negative_age", lambda d: d["settings"].update(q0_max_age_s=-1)),
                                         ("huge_budget", lambda d: d["settings"].update(total_budget_s=1e9)), ("extra_key", lambda d: d["settings"].update(surprise=1)),
                                         ("bool_probes", lambda d: d["settings"].update(max_probes=True))])
def test_invalid_plan_settings_exit_two_before_any_component_is_built_even_when_the_hash_is_consistent(world, capsys, name, mutate):
    rewrite_plan(world, mutate, keep_consistent=True)
    launcher = FakeLauncher(world.clock)
    rc = run.main(world.argv(), overrides=world.overrides(launcher=launcher))
    out = capsys.readouterr().out
    assert rc == 2 and "plan_settings_invalid" in out and launcher.calls == [] and list(world.plan_dir.iterdir()) == [] and not world.logs.exists(), name


def test_build_components_gives_the_bounded_observer_the_plan_caps_and_a_statement_budget_derived_from_the_plan(wworld, tmp_path):
    doc = json.loads(wworld.plan_file.read_text(encoding="utf-8"))["document"]
    doc["settings"].update(baseline_cap_s=7.0, probe_cap_s=9.0, max_probes=3)
    args = types.SimpleNamespace(launcher_logs=tmp_path / "lg", crawl_evidence=wworld.evidence)
    observer = run.build_components(args, doc, True, {"launcher": object(), "gate": lambda: (True, [])})["observer"]
    assert observer.caps["baseline"] == 7.0 and observer.caps["probe"] == 9.0 and observer.caps["start"] == 20.0
    assert observer.argv[1].endswith("repair_observer_worker_v1.py") and observer.argv[-2:] == ["--max-statements", str(1 * (4 + 3 * 3))]            # 1 phase write: 4 SELECT baseline + 3 probe x 3 SELECT
    assert observer.env == doc["specs"][1]["env"] and observer.cwd.endswith("backend")
    doc["specs"].append(dict(doc["specs"][1], name="ph_w2"))
    assert run.observer_statement_budget(doc) == 2 * (4 + 3 * 3)                                                  # ngan sach tinh theo SO PHASE write cua ke hoach
    doc["specs"] = [doc["specs"][0]]
    assert run.observer_statement_budget(doc) == 0                                                              # khong phase write => khong ngan sach (khong observer)


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object chi co tren Windows")
def test_the_real_launcher_log_directory_is_created_only_when_asked(world, tmp_path):
    doc = json.loads(world.plan_file.read_text(encoding="utf-8"))["document"]
    gate = {"gate": lambda: (True, [])}
    made = run.build_components(types.SimpleNamespace(launcher_logs=tmp_path / "lg_made", crawl_evidence=world.evidence), doc, False, gate)
    assert (tmp_path / "lg_made").is_dir() and type(made["launcher"]).__name__ == "RealJobLauncher" and made["observer"] is None
    run.build_components(types.SimpleNamespace(launcher_logs=tmp_path / "lg_none", crawl_evidence=world.evidence), doc, False, gate, create_dirs=False)
    assert not (tmp_path / "lg_none").exists()


def test_a_plan_whose_observer_statement_budget_exceeds_the_worker_limit_exits_two_before_any_component_is_built(wworld, capsys):
    def many_write_phases(doc):
        doc["settings"]["max_probes"] = 8
        doc["specs"] += [dict(doc["specs"][1], name=f"ph_w{i}") for i in range(2, 9)]                           # 8 phase write x (4 + 3 x 8) = 224 > 200
    rewrite_plan(wworld, many_write_phases, keep_consistent=True)
    launcher = FakeLauncher(wworld.clock)
    rc = run.main(wworld.argv(), overrides=wworld.overrides(launcher=launcher))
    out = capsys.readouterr().out
    assert rc == 2 and "observer_statement_budget_exceeds_worker_limit: 224 > 200" in out and launcher.calls == [] and list(wworld.plan_dir.iterdir()) == []


def test_a_malformed_plan_file_exits_two(world, capsys):
    good = json.loads(world.plan_file.read_text(encoding="utf-8"))
    cases = {"not_json": "{not json", "wrong_version_only": json.dumps({**good, "document": {**good["document"], "plan_version": 1}}),
             "missing_key": json.dumps({**good, "document": {k: v for k, v in good["document"].items() if k != "settings"}}),
             "extra_key": json.dumps({**good, "document": {**good["document"], "surprise": 1}}), "document_not_dict": json.dumps({"document": [], "plan_sha256": "x"}),
             "minimal": json.dumps({"document": {"plan_version": 1}})}
    for name, text in cases.items():
        world.plan_file.write_text(text, encoding="utf-8")
        assert run.main(world.argv(), overrides=world.overrides()) == 2, name
        out = capsys.readouterr().out
        assert ("plan_file_malformed" in out) or name == "not_json", name


def test_a_non_empty_plan_dir_exits_three_and_leaves_existing_files_alone(world, capsys):
    (world.plan_dir / "old_receipt.json").write_text("old", encoding="utf-8")
    launcher = FakeLauncher(world.clock)
    assert run.main(world.argv(), overrides=world.overrides(launcher=launcher)) == 3
    assert (world.plan_dir / "old_receipt.json").read_text(encoding="utf-8") == "old" and launcher.calls == []


# =========================================================================== outer authority: code ep, khong chi prose (GPT 76 section 4)
def rejected(capsys):
    out = capsys.readouterr().out
    return json.loads(out.strip().splitlines()[-1])


def assert_nothing_happened(world, launcher):
    assert launcher.calls == [] and list(world.plan_dir.iterdir()) == [] and not world.logs.exists()


def test_a_valid_outer_approval_binds_the_entrypoint_hash_the_expiry_and_the_invocation_into_the_run_records(world, capsys):
    launcher = FakeLauncher(world.clock)
    rc = run.main(world.argv(), overrides=world.overrides(launcher=launcher))
    summary = summary_of(capsys)
    bound = json.loads((world.plan_dir / "PLAN_BOUND.json").read_text(encoding="utf-8"))
    receipt = json.loads((world.plan_dir / "receipt_01_ph_a.json").read_text(encoding="utf-8"))
    assert rc == 0 and summary["entrypoint_sha256"] == run.entrypoint_sha256() == bound["entrypoint_sha256"] == receipt["entrypoint_sha256"]
    assert summary["invocation_sha256"] == world.invocation() and bound["not_after_utc"] == receipt["not_after_utc"] == world.not_after.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.parametrize("delta_s", [-3600, -1, 0])
def test_an_expired_approval_is_rejected_with_exit_two_before_any_component_directory_or_worker(world, capsys, delta_s):
    world.not_after = world.clock.now_utc() + dt.timedelta(seconds=delta_s)                                      # 0 = ranh gioi: het han
    launcher = FakeLauncher(world.clock)
    assert run.main(world.argv(), overrides=world.overrides(launcher=launcher)) == 2
    out = rejected(capsys)
    assert out["error"] == "approval_rejected" and out["problems"] == ["approval_expired"]
    assert_nothing_happened(world, launcher)


def test_an_approval_that_expires_inside_the_run_bounds_the_budget_and_is_refused_by_the_runner(world, capsys):
    world.not_after = world.clock.now_utc() + dt.timedelta(seconds=30)                                           # phase can deadline 60 s + du tru
    launcher = FakeLauncher(world.clock)
    rc = run.main(world.argv(), overrides=world.overrides(launcher=launcher))
    summary = summary_of(capsys)
    assert rc == 3 and summary["phases"][0]["verdict"] == "REFUSED" and summary["phases"][0]["reasons"][0].startswith("budget_insufficient") and launcher.calls == []


@pytest.mark.parametrize("override,problem", [({"plan_dir": "PLACEHOLDER"}, "approval_plan_dir_mismatch"), ({"entrypoint": "0" * 64}, "approval_entrypoint_sha256_mismatch"),
                                              ({"invocation": "1" * 64}, "approval_invocation_sha256_mismatch")])
def test_a_plan_dir_entrypoint_or_invocation_that_differs_from_the_approval_is_rejected_before_any_side_effect(world, capsys, tmp_path, override, problem):
    if "plan_dir" in override:
        other = tmp_path / "other_plan_dir"
        other.mkdir()
        override = {"plan_dir": other}
    launcher = FakeLauncher(world.clock)
    argv = world.argv()
    world.sign(**override)
    assert run.main(argv, overrides=world.overrides(launcher=launcher)) == 2
    assert rejected(capsys)["problems"] == [problem]
    assert_nothing_happened(world, launcher)


@pytest.mark.parametrize("what", ["plan", "launcher_logs", "crawl_evidence", "q0_receipt"])
def test_changing_any_execution_path_after_the_approval_changes_the_invocation_and_is_rejected(wworld, capsys, tmp_path, what):
    signed_argv = wworld.argv()                                                                                  # approval ky cho lenh goi nay
    other = tmp_path / f"other_{what}"
    if what == "plan":
        other.write_bytes(wworld.plan_file.read_bytes())
        argv = signed_argv.copy()
        argv[argv.index("--plan") + 1] = str(other)
    elif what == "q0_receipt":
        other.write_bytes(wworld.q0_path.read_bytes())
        argv = signed_argv.copy()
        argv[argv.index("--q0-receipt") + 1] = str(other)
    else:
        flag = "--launcher-logs" if what == "launcher_logs" else "--crawl-evidence"
        argv = signed_argv.copy()
        argv[argv.index(flag) + 1] = str(other)
    launcher = FakeLauncher(wworld.clock)
    wworld.autosign = False
    assert run.main(argv, overrides=wworld.overrides(launcher=launcher)) == 2
    assert rejected(capsys)["problems"] == ["approval_invocation_sha256_mismatch"] and launcher.calls == [] and list(wworld.plan_dir.iterdir()) == []


@pytest.mark.parametrize("name,problem", [("100-gpt-plan.approval_DRAFT.md", "approval_name_not_a_numbered_gpt_md"), ("100-gpt-STANDIN-approval.md", "approval_name_not_a_numbered_gpt_md"),
                                          ("100-gpt-proposal-approval.md", "approval_name_not_a_numbered_gpt_md"), ("100-claude-approval.md", "approval_name_not_a_numbered_gpt_md"),
                                          ("approval.md", "approval_name_not_a_numbered_gpt_md"), ("100-gpt-approval.txt", "approval_name_not_a_numbered_gpt_md")])
def test_an_approval_that_is_not_a_numbered_gpt_md_in_the_discussion_directory_is_rejected(world, capsys, name, problem):
    fake = world.discuss / name
    world.approval.rename(fake)
    world.approval = fake
    launcher = FakeLauncher(world.clock)
    argv = world.argv()
    assert run.main(argv, overrides=world.overrides(launcher=launcher)) == 2
    assert rejected(capsys)["problems"] == [problem]
    assert_nothing_happened(world, launcher)


def test_an_approval_outside_or_below_the_discussion_directory_or_missing_is_rejected(world, capsys, tmp_path):
    launcher = FakeLauncher(world.clock)
    inside = world.argv()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    for place, expected in ((outside, "approval_not_in_discussion_directory"), (world.discuss / "sub", "approval_not_in_discussion_directory")):
        place.mkdir(exist_ok=True)
        moved = place / "100-gpt-plan-approval.md"
        moved.write_bytes(world.approval.read_bytes())
        argv = inside.copy()
        argv[argv.index("--approval") + 1] = str(moved)
        assert run.main(argv, overrides=world.overrides(launcher=launcher)) == 2
        assert rejected(capsys)["problems"] == [expected]
    argv = inside.copy()
    argv[argv.index("--approval") + 1] = str(world.discuss / "102-gpt-missing.md")
    assert run.main(argv, overrides=world.overrides(launcher=launcher)) == 2 and "approval_unreadable" in rejected(capsys)["error"]
    assert_nothing_happened(world, launcher)


def test_the_producer_approval_inside_a_spec_must_also_be_a_real_gpt_md(world, capsys):
    standin = world.tmp / "ph1_producer_approval_STANDIN.md"
    standin.write_text("APPROVED_CONTROLLER_SHA256: x\n", encoding="utf-8")

    def add_inner(doc, path):
        doc["specs"][0]["argv"] += ["--gpt-approval", str(path)]
    rewrite_plan(world, lambda d: add_inner(d, standin), keep_consistent=True)
    world.signed_plan_sha = json.loads(world.plan_file.read_text(encoding="utf-8"))["plan_sha256"]
    launcher = FakeLauncher(world.clock)
    assert run.main(world.argv(), overrides=world.overrides(launcher=launcher)) == 2
    out = rejected(capsys)
    assert out["error"] == "inner_approval_rejected" and out["problems"] == ["ph_a: inner_approval_not_in_discussion_directory"]
    assert_nothing_happened(world, launcher)
    inner = world.discuss / "98-gpt-inner-producer-approval.md"
    inner.write_text("APPROVED_CONTROLLER_SHA256: x\n", encoding="utf-8")
    rewrite_plan(world, lambda d: d["specs"][0]["argv"].__setitem__(slice(-2, None), ["--gpt-approval", str(inner)]), keep_consistent=True)
    world.signed_plan_sha = json.loads(world.plan_file.read_text(encoding="utf-8"))["plan_sha256"]
    assert run.main(world.argv(), overrides=world.overrides(launcher=launcher)) == 0                             # approval producer that => di qua


def test_the_invocation_hash_is_the_canonical_hash_of_the_five_resolved_paths_recomputed_independently(world):
    import hashlib as _hashlib
    import os as _os
    paths = {"plan": world.plan_file, "plan_dir": world.plan_dir, "launcher_logs": world.logs, "crawl_evidence": world.evidence, "q0_receipt": None}
    canonical = {key: (_os.path.normcase(str(Path(value).resolve())) if value is not None else None) for key, value in paths.items()}
    expected = _hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert world.invocation(q0=False) == expected
    assert world.invocation(q0=False, plan_dir=world.tmp / "elsewhere") != expected and world.invocation(q0=False, plan_file=world.tmp / "p2.json") != expected      # moi duong dan deu nam trong hash
    assert world.invocation(q0=False, logs=world.tmp / "l2") != expected and world.invocation(q0=False, evidence=world.tmp / "e2.json") != expected
    assert world.invocation(q0=False, q0_path=world.tmp / "q.json") != expected


def test_check_only_also_lists_the_inner_producer_approval_problems(world, capsys):
    standin = world.tmp / "ph1_producer_approval_STANDIN.md"
    standin.write_text("x", encoding="utf-8")
    rewrite_plan(world, lambda d: d["specs"][0]["argv"].extend(["--gpt-approval", str(standin)]), keep_consistent=True)
    world.signed_plan_sha = json.loads(world.plan_file.read_text(encoding="utf-8"))["plan_sha256"]
    rc = run.main(world.argv("--check-only"), overrides=world.overrides(launcher=FakeLauncher(world.clock)))
    out = summary_of(capsys)
    assert rc == 3 and "ph_a: inner_approval_not_in_discussion_directory" in out["problems"] and out["problems"].count("ph_a: inner_approval_not_in_discussion_directory") == 1


def test_check_only_on_a_draft_reports_the_authority_problems_and_prints_the_hashes_an_approver_needs(world, capsys):
    argv = world.argv("--check-only")
    argv[argv.index("--approval") + 1] = str(world.draft)
    launcher = FakeLauncher(world.clock)
    rc = run.main(argv, overrides=world.overrides(launcher=launcher))
    out = summary_of(capsys)
    text = " ".join(out["problems"])
    assert rc == 3 and out["ok"] is False and "approval_not_in_discussion_directory" in text and ("approval_line_malformed" in text or "approval_key_count" in text or "approval_hash_malformed" in text)
    assert out["entrypoint_sha256"] == run.entrypoint_sha256() and out["computed_invocation_sha256"] == world.invocation() and out["computed_plan_sha256"] == world.plan["plan_sha256"]
    assert_nothing_happened(world, launcher)


def test_check_only_flags_an_expired_or_mismatched_real_approval_without_writing(world, capsys):
    world.not_after = world.clock.now_utc() - dt.timedelta(seconds=5)
    world.autosign = False
    argv = world.argv("--check-only")
    world.sign(entrypoint="2" * 64)
    launcher = FakeLauncher(world.clock)
    rc = run.main(argv, overrides=world.overrides(launcher=launcher))
    text = " ".join(summary_of(capsys)["problems"])
    assert rc == 3 and "approval_expired" in text and "approval_entrypoint_sha256_mismatch" in text
    assert_nothing_happened(world, launcher)


def test_real_approval_problem_accepts_only_numbered_gpt_files_directly_inside_the_directory(tmp_path):
    root = tmp_path / "discuss"
    root.mkdir()
    for name in ("76-gpt-75-x.md", "7a-gpt-q0.md", "1000-gpt-final.approval.md"):
        (root / name).write_text("x", encoding="utf-8")
        assert run.real_approval_problem(root / name, root) is None, name
    for name in ("gpt-1.md", "76-gpt-.md", "76-gpt-x.MD"):
        (root / name).write_text("x", encoding="utf-8")
        assert run.real_approval_problem(root / name, root) == "approval_name_not_a_numbered_gpt_md", name
    assert run.real_approval_problem(root / "nope.md", root) == "approval_file_missing"


# =========================================================================== Q0 qua entry point
def test_a_write_plan_without_a_q0_receipt_argument_exits_two_before_any_component_is_built(wworld, capsys):
    launcher = FakeLauncher(wworld.clock)
    assert run.main(wworld.argv(q0=False), overrides=wworld.overrides(launcher=launcher)) == 2
    assert "write_plan_requires_q0_receipt" in summary_of(capsys)["error"] and launcher.calls == [] and list(wworld.plan_dir.iterdir()) == []


def test_a_tampered_stale_or_foreign_q0_receipt_exits_two_and_nothing_launches(wworld, capsys, tmp_path):
    launcher = FakeLauncher(wworld.clock)
    original = wworld.q0_path.read_bytes()
    wworld.q0_path.write_bytes(original + b" ")                                                              # 1 byte lech
    assert run.main(wworld.argv(), overrides=wworld.overrides(launcher=launcher)) == 2 and "sha256_mismatch" in summary_of(capsys)["error"]
    wworld.q0_path.write_bytes(original)                                                                     # khoi phuc DUNG byte (write_text tren Windows doi LF thanh CRLF)
    stale = World(tmp_path / "stale", write=True, settings={**SETTINGS, "q0_max_age_s": 60.0})             # max-age nam TRONG plan (settings), khong con la tham so CLI
    stale_launcher = FakeLauncher(stale.clock)
    assert run.main(stale.argv(), overrides=stale.overrides(launcher=stale_launcher)) == 2                   # Q0 da 5 phut tuoi > 60 s
    error = summary_of(capsys)["error"]
    assert "q0_receipt_invalid" in error and "fresh" in error and stale_launcher.calls == []               # bi tu choi vi QUA CU, khong phai vi hash
    other_server = obs.FakeServer()
    other_server.uuid = "uuid-other-server"                                                                   # Q0 cua may chu KHAC => bytes khac => hash khac pin
    foreign_path, _, _ = obs.q0_for(tmp_path, other_server)
    assert run.main(wworld.argv("--q0-receipt", str(foreign_path), q0=False), overrides=wworld.overrides(launcher=launcher)) == 2    # Q0 khac hash voi pin trong plan
    assert launcher.calls == [] and list(wworld.plan_dir.iterdir()) == []


def test_a_server_that_is_not_the_one_the_q0_receipt_describes_is_refused_at_the_baseline(wworld, capsys):
    wworld.server.uuid = "uuid-OTHER"
    launcher = obs.PhaseLauncher(wworld.clock, wworld.server, linger=0)
    rc = run.main(wworld.argv(), overrides=wworld.overrides(launcher=launcher))
    summary = summary_of(capsys)
    assert rc == 3 and [p["verdict"] for p in summary["phases"]] == ["PASS", "REFUSED"] and "server_identity_differs_from_q0" in summary["phases"][-1]["reasons"][0]
    assert launcher.calls == ["ph_a"]                                                                          # phase readonly truoc do da chay; phase write bi tu choi truoc launch


# =========================================================================== ma thoat
def test_exit_codes_distinguish_clean_failure_refusal_and_quarantine(world, wworld, capsys):
    assert run.main(world.argv(), overrides=world.overrides(launcher=FakeLauncher(world.clock, results=[{"exit_code": 3}]))) == 1
    capsys.readouterr()
    assert run.main(wworld.argv(), overrides=wworld.overrides(launcher=FakeLauncher(wworld.clock, results=[{}, {"contained": False}]))) == 4
    capsys.readouterr()


def test_a_closed_gate_is_a_refusal_exit_three_with_zero_launches(world, capsys):
    launcher = FakeLauncher(world.clock)
    assert run.main(world.argv(), overrides=world.overrides(launcher=launcher, gate=lambda: (False, ["crawler_busy"]))) == 3 and launcher.calls == []


# =========================================================================== --check-only: khong ghi gi
def test_check_only_reports_ok_and_creates_nothing(world, capsys):
    launcher = FakeLauncher(world.clock)
    rc = run.main(world.argv("--check-only"), overrides=world.overrides(launcher=launcher))
    out = summary_of(capsys)
    assert rc == 0 and out["ok"] is True and out["problems"] == [] and launcher.calls == [] and list(world.plan_dir.iterdir()) == [] and not world.logs.exists()


def test_check_only_lists_every_problem_without_writing(world, capsys):
    world.script.write_text("print(2)\n", encoding="utf-8")                                                 # byte file lech so voi pin
    (world.plan_dir / "x").write_text("x", encoding="utf-8")
    launcher = FakeLauncher(world.clock)
    launcher.source_sha256 = "z" * 64                                                                       # cong cu khac pin
    world.signed_plan_sha = "a" * 64
    rc = run.main(world.argv("--check-only"), overrides=world.overrides(launcher=launcher, gate=lambda: (False, ["crawler_busy"])))
    text = " ".join(summary_of(capsys)["problems"])
    assert rc == 3 and all(token in text for token in ("plan_sha256_not_approved", "tooling_not_approved", "plan_dir_missing_or_not_empty", "required_file_hash_mismatch", "gate_closed"))
    assert sorted(p.name for p in world.plan_dir.iterdir()) == ["x"] and launcher.calls == []


# =========================================================================== thanh phan THAT khi khong co override
def test_without_overrides_the_entry_point_builds_the_real_gate_and_the_real_job_launcher(tmp_path, capsys):
    if sys.platform != "win32":
        pytest.skip("Job Object chi co tren Windows")
    wj_sha = rr.file_sha256(TOOLS / "windows_job_v1.py")
    script = tmp_path / "work.py"
    script.write_text("print(1)\n", encoding="utf-8")
    spec = {"name": "ph_a", "kind": "readonly", "argv": [sys.executable, str(script)], "cwd": str(tmp_path), "env": rp.child_env(), "required_files": {str(script): rr.file_sha256(script)}, "deadline_s": 60}
    plan = rp.build_plan([spec], SETTINGS, q0_receipt_sha256=None, launcher_source_sha256=wj_sha, observer_tooling_sha256=None)
    rp.emit(tmp_path / "proposal", "plan", plan, "real components")
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    clock = obs.Clock()
    discuss = tmp_path / "discuss"
    discuss.mkdir()
    approval = discuss / "100-gpt-plan-approval.md"
    namespace = types.SimpleNamespace(plan=tmp_path / "proposal" / "plan.plan.json", plan_dir=plan_dir, launcher_logs=tmp_path / "logs", crawl_evidence=tmp_path / "missing_evidence.json", q0_receipt=None)
    write_outer_approval(approval, plan_sha=plan["plan_sha256"], not_after=clock.now_utc() + dt.timedelta(hours=1), plan_dir=plan_dir, entrypoint=run.entrypoint_sha256(), invocation=run.invocation_sha256(namespace))
    argv = ["--approval", str(approval), "--plan", str(tmp_path / "proposal" / "plan.plan.json"), "--plan-dir", str(plan_dir),
            "--launcher-logs", str(tmp_path / "logs"), "--crawl-evidence", str(tmp_path / "missing_evidence.json"), "--check-only"]
    rc = run.main(argv, overrides={"clock": clock, "now_utc": clock.now_utc, "discuss_dir": discuss})
    out = summary_of(capsys)
    assert rc == 3 and len(out["problems"]) == 1 and out["problems"][0].startswith("gate_closed")                                                    # chi con cong dong: tooling Job that khop plan
    assert any(token in out["problems"][0] for token in ("khong doc duoc bang chung", "cong gio"))                                                  # ly do phu thuoc gio that cua may; ca hai deu la cong that
    assert not (tmp_path / "logs").exists() and list(plan_dir.iterdir()) == []                                                                     # --check-only that su KHONG tao thu muc nao
