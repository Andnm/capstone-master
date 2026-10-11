"""audit_tools/repair_phase_runner_v1.py — runner THUAN: argv-binding, byte-check, ngan sach, containment, quiescence, receipt chuoi, quarantine. State machine dung fake; vai ca dung Job Object + process local that."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "audit_tools"
spec = importlib.util.spec_from_file_location("repair_phase_runner_v1", TOOLS / "repair_phase_runner_v1.py")
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)
qm = rr.qm
APP, SCOPE, PROTECTED = "hotel_app", ["warehouse_dsdev_20261009_3src_nf"], ["hotel_price_intel", "warehouse_20261004_3src"]
GOOD_SELFTEST = {k: True for k in qm.SELFTEST_KEYS}
SYSTEM_ENV = {"SystemRoot": "C:\\Windows", "TEMP": "C:\\Windows\\Temp"}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class FakeLauncher:
    def __init__(self, clock, results=None, raises=None):
        self.clock, self.results, self.calls, self.raises = clock, list(results or []), [], raises

    def launch(self, spec, cap_s, should_abort=None, gate_poll_s=5.0):
        self.calls.append((spec["name"], cap_s))
        self.last_abort = should_abort
        if self.raises:
            raise self.raises
        result = dict(self.results.pop(0)) if self.results else {}
        self.clock.t += result.pop("duration", 5.0)
        base = {"exit_code": 0, "timed_out": False, "killed_tree": False, "contained": True, "pids_seen": [4242], "stdout_path": "x.out", "stderr_path": "x.err", "stdout_sha256": "a" * 64,
                "stderr_sha256": "b" * 64, "started": 0.0, "ended": 1.0}
        base.update(result)
        return base


class FakeObserver:
    def __init__(self, clock, probes, baseline=None):
        self.clock, self.probe_queue, self.baseline = clock, list(probes), baseline or {"baseline_thread_id": 1000, "baseline_uptime_s": 500.0, "probe_session_id": 5000}
        self.baseline_calls = self.probe_calls = 0

    def capture_baseline(self):
        self.baseline_calls += 1
        return dict(self.baseline) if isinstance(self.baseline, dict) else self.baseline

    def probe(self):
        self.probe_calls += 1
        item = self.probe_queue.pop(0) if self.probe_queue else {"rows": []}
        if isinstance(item, Exception):
            raise item
        return {"status": "ok", "rows": [], "truncated": False, "rolling_back": [], "uptime_s": 500.0 + self.clock.t, "observed_at": -1.0, **item}      # observed_at gia: runner phai ghi de


def row(session_id, user=APP, db=None, pid=None):
    return {"PROCESSLIST_ID": session_id, "PROCESSLIST_USER": user, "PROCESSLIST_DB": db, "pid": pid}


@pytest.fixture
def env(tmp_path):
    script = tmp_path / "work.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    plan = tmp_path / "plan"
    plan.mkdir()
    return {"tmp": tmp_path, "script": script, "plan": plan, "clock": FakeClock()}


def make_spec(env, name="ph_a", kind="pure", **over):
    base = {"name": name, "kind": kind, "argv": [sys.executable, str(env["script"])], "cwd": str(env["tmp"]), "env": dict(SYSTEM_ENV), "required_files": {str(env["script"]): rr.file_sha256(env["script"])},
            "deadline_s": 60}
    if kind == "db":
        base["db"] = {"app_user": APP, "db_scope": list(SCOPE), "protected_dbs": list(PROTECTED)}
    base.update(over)
    return base


def make_runner(env, specs, launcher=None, observer=None, gate=None, selftest=GOOD_SELFTEST, **over):
    approvals = {s["name"]: rr.argv_sha256(s) for s in specs}
    kwargs = dict(plan_dir=env["plan"], approvals=approvals, launcher=launcher or FakeLauncher(env["clock"]), clock=env["clock"], gate=gate or (lambda: (True, [])), observer=observer,
                  selftest=selftest, selftest_sha256="s" * 64 if selftest else None, total_budget_s=3600, margin_s=10)
    kwargs.update(over)
    runner = rr.Runner(**kwargs)
    if "expected_tooling" not in over:
        runner.expected_tooling = runner.tooling()                                                          # pin = chinh cong cu dang chay (test); that: approval cung cap
    return runner


# =========================================================================== spec / approval / hash
def test_a_valid_spec_has_no_errors_and_each_bad_field_is_named():
    base = {"name": "ph_a", "kind": "pure", "argv": ["x"], "cwd": "c", "env": {}, "required_files": {"f": "a" * 64}, "deadline_s": 5}
    assert rr.validate_spec(base) == []
    bad = {"name": "Bad Name", "kind": "other", "argv": [], "cwd": "", "env": {"A": 1}, "required_files": {}, "deadline_s": True}
    for field in ("name", "kind", "argv", "cwd", "env", "required_files", "deadline_s"):
        assert field in " ".join(rr.validate_spec({**base, field: bad[field]})), field
    assert rr.validate_spec({**base, "deadline_s": 0}) and rr.validate_spec({**base, "deadline_s": rr.MAX_PHASE_S + 1}) and rr.validate_spec({**base, "argv": ["x", ""]})
    assert rr.validate_spec({**base, "extra": 1})[0].startswith("spec_keys") and rr.validate_spec({k: v for k, v in base.items() if k != "env"})[0].startswith("spec_keys")
    assert rr.validate_spec("nope") == ["spec_not_mapping"] and rr.validate_spec({**base, "db": {}}) == ["db_not_allowed_for_pure_phase"]


def test_db_phase_requires_exact_db_block_with_disjoint_scope_and_protected():
    db_ok = {"app_user": APP, "db_scope": SCOPE, "protected_dbs": PROTECTED}
    base = {"name": "ph_db", "kind": "db", "argv": ["x"], "cwd": "c", "env": {}, "required_files": {"f": "a" * 64}, "deadline_s": 5}
    assert rr.validate_spec({**base, "db": db_ok}) == []
    for bad in (None, {}, {**db_ok, "app_user": ""}, {**db_ok, "db_scope": []}, {**db_ok, "db_scope": PROTECTED}, {**db_ok, "extra": 1}, {**db_ok, "protected_dbs": [3]}):
        assert "db" in rr.validate_spec({**base, "db": bad}), bad
    assert "db" in rr.validate_spec(base)


def test_argv_hash_covers_argv_cwd_and_every_env_pair():
    base = {"argv": ["a", "b"], "cwd": "c", "env": {"K": "v"}}
    h = rr.argv_sha256(base)
    assert len(h) == 64 and h == rr.argv_sha256({"env": {"K": "v"}, "cwd": "c", "argv": ["a", "b"]})
    for changed in ({"argv": ["a", "b", "--x"]}, {"argv": ["a", "c"]}, {"cwd": "d"}, {"env": {"K": "w"}}, {"env": {"K": "v", "Z": "1"}}, {"env": {}}):
        assert rr.argv_sha256({**base, **changed}) != h


def test_parse_approved_argv_reads_lines_and_rejects_duplicates_and_malformed_ones():
    text = f"# approval\nAPPROVED_ARGV_SHA256[ph_a]: `{'a' * 64}`\nother line\nAPPROVED_ARGV_SHA256[ph_b]: {'b' * 64}\n"
    assert rr.parse_approved_argv(text) == {"ph_a": "a" * 64, "ph_b": "b" * 64}
    for bad in (f"APPROVED_ARGV_SHA256[ph_a]: {'a' * 64}\nAPPROVED_ARGV_SHA256[ph_a]: {'b' * 64}", "APPROVED_ARGV_SHA256[ph_a]: nothex", f"APPROVED_ARGV_SHA256[Ph A]: {'a' * 64}",
                f"APPROVED_ARGV_SHA256 ph_a {'a' * 64}"):
        with pytest.raises(ValueError):
            rr.parse_approved_argv(bad)


# =========================================================================== duong vui + receipt
def test_a_pure_phase_runs_once_and_writes_a_chained_never_overwritten_receipt(env):
    specs = [make_spec(env, "ph_a"), make_spec(env, "ph_b")]
    launcher = FakeLauncher(env["clock"])
    runner = make_runner(env, specs, launcher=launcher)
    receipts = runner.run_plan(specs)
    assert [r["verdict"] for r in receipts] == [rr.PASS, rr.PASS] and [c[0] for c in launcher.calls] == ["ph_a", "ph_b"]
    first, second = receipts
    assert first["prev_receipt_sha256"] is None and second["prev_receipt_sha256"] == first["receipt_sha256"] and first["seq"] == 1 and second["seq"] == 2
    on_disk = json.loads(Path(first["receipt_path"]).read_text(encoding="utf-8"))
    assert on_disk["argv_sha256"] == rr.argv_sha256(specs[0]) == on_disk["approved_argv_sha256"] and on_disk["file_hashes_before"] == on_disk["file_hashes_after"]
    assert on_disk["launch"]["contained"] is True and on_disk["kind"] == "pure" and on_disk["baseline"] is None
    assert rr.sha256_hex(rr.canonical_json({k: v for k, v in first.items() if k not in ("receipt_path", "receipt_sha256")})) == first["receipt_sha256"]


def test_receipts_are_exclusive_create_an_existing_file_is_an_error_not_an_overwrite(env):
    (env["plan"] / "receipt_01_ph_a.json").write_text("existing", encoding="utf-8")
    spec = make_spec(env)
    runner = make_runner(env, [spec])
    with pytest.raises(FileExistsError):
        runner.run_phase(spec)


# =========================================================================== tu choi truoc launch
def refused(env, spec, runner_kwargs=None, specs=None, **mods):
    launcher = FakeLauncher(env["clock"])
    runner = make_runner(env, specs or [spec], launcher=launcher, **(runner_kwargs or {}))
    receipt = runner.run_phase(spec)
    return receipt, launcher, runner


@pytest.mark.parametrize("mutate,fragment", [
    (lambda s: s.update(argv=s["argv"] + ["--extra"]), "argv_sha256_not_approved"),
    (lambda s: s.update(cwd=s["cwd"] + "x"), "argv_sha256_not_approved"),
    (lambda s: s["env"].update(NEW="1"), "argv_sha256_not_approved"),
])
def test_any_change_to_argv_cwd_or_env_after_approval_is_refused_and_nothing_launches(env, mutate, fragment):
    spec = make_spec(env)
    runner_specs = [json.loads(json.dumps(spec))]
    changed = json.loads(json.dumps(spec))
    mutate(changed)
    receipt, launcher, _ = refused(env, changed, specs=runner_specs)
    assert receipt["verdict"] == rr.REFUSED and fragment in receipt["reasons"][0] and launcher.calls == []


def test_a_missing_or_malformed_approval_is_refused(env):
    spec = make_spec(env)
    for approvals in ({}, {"ph_a": "not-a-sha"}, {"ph_a": "0" * 64}, {"other": rr.argv_sha256(spec)}):
        launcher = FakeLauncher(env["clock"])
        runner = rr.Runner(plan_dir=env["plan"], approvals=approvals, launcher=launcher, clock=env["clock"], gate=lambda: (True, []), total_budget_s=3600, margin_s=10)
        receipt = runner.run_phase(spec)
        assert receipt["verdict"] == rr.REFUSED and receipt["reasons"] == ["argv_sha256_not_approved"] and launcher.calls == []
        (env["plan"] / "receipt_01_ph_a.json").unlink()


def test_a_required_file_that_differs_by_one_byte_or_is_missing_is_refused(env):
    spec = make_spec(env)
    env["script"].write_text("print('ok')\n# tamper\n", encoding="utf-8")
    receipt, launcher, _ = refused(env, spec)
    assert receipt["verdict"] == rr.REFUSED and receipt["reasons"][0].startswith("required_file_hash_mismatch") and launcher.calls == []
    (env["plan"] / "receipt_01_ph_a.json").unlink()
    env["script"].unlink()
    receipt, launcher, _ = refused(env, spec)
    assert receipt["reasons"][0].startswith("required_file_unreadable") and launcher.calls == []


def test_a_closed_gate_or_insufficient_budget_or_existing_output_is_refused(env):
    spec = make_spec(env, outputs=[str(env["tmp"] / "out.bin")])
    receipt, launcher, _ = refused(env, spec, runner_kwargs={"gate": lambda: (False, ["crawler_busy"])})
    assert receipt["reasons"] == ["gate_closed: ['crawler_busy']"] and launcher.calls == []
    (env["plan"] / "receipt_01_ph_a.json").unlink()
    receipt, launcher, _ = refused(env, spec, runner_kwargs={"total_budget_s": 69})                          # can 60 + margin 10
    assert receipt["reasons"][0].startswith("budget_insufficient") and launcher.calls == []
    (env["plan"] / "receipt_01_ph_a.json").unlink()
    (env["tmp"] / "out.bin").write_bytes(b"old")
    receipt, launcher, _ = refused(env, spec)
    assert receipt["reasons"][0].startswith("output_already_exists") and launcher.calls == []


def test_the_total_budget_shrinks_as_phases_run_and_a_later_phase_can_be_refused_for_it(env):
    specs = [make_spec(env, "ph_a", deadline_s=60), make_spec(env, "ph_b", deadline_s=60)]
    launcher = FakeLauncher(env["clock"], results=[{"duration": 100.0}])
    runner = make_runner(env, specs, launcher=launcher, total_budget_s=150, margin_s=10)
    receipts = runner.run_plan(specs)
    assert receipts[0]["verdict"] == rr.PASS and receipts[1]["verdict"] == rr.REFUSED and receipts[1]["reasons"][0].startswith("budget_insufficient") and [c[0] for c in launcher.calls] == ["ph_a"]


def test_an_invalid_spec_is_refused_with_the_error_list(env):
    receipt, launcher, _ = refused(env, {"name": "ph_a", "kind": "x"}, specs=[make_spec(env)])
    assert receipt["verdict"] == rr.REFUSED and receipt["reasons"][0].startswith("spec_invalid") and launcher.calls == []


def test_the_plan_stops_at_the_first_non_pass_and_later_phases_are_never_launched(env):
    specs = [make_spec(env, "ph_a"), make_spec(env, "ph_b"), make_spec(env, "ph_c")]
    launcher = FakeLauncher(env["clock"], results=[{}, {"exit_code": 3}])
    runner = make_runner(env, specs, launcher=launcher)
    receipts = runner.run_plan(specs)
    assert [r["verdict"] for r in receipts] == [rr.PASS, rr.FAIL_CLEAN] and [c[0] for c in launcher.calls] == ["ph_a", "ph_b"]
    again = runner.run_phase(specs[2])                                                                       # goi truc tiep cung bi chan: phase truoc khong PASS
    assert again["verdict"] == rr.REFUSED and again["reasons"][0].startswith("previous_phase_not_pass")


# =========================================================================== ket qua launch
def test_a_failing_child_a_timeout_and_orphaned_descendants_are_clean_failures_with_distinct_reasons(env):
    for result, fragment in (({"exit_code": 2}, "exit_code=2"), ({"exit_code": None, "timed_out": True, "killed_tree": True}, "timed_out=True"),
                             ({"exit_code": 0, "killed_tree": True}, "descendants_outlived_root=True")):
        spec = make_spec(env, outputs=[str(env["tmp"] / "o.bin")])
        runner = make_runner(env, [spec], launcher=FakeLauncher(env["clock"], results=[result]))
        receipt = runner.run_phase(spec)
        assert receipt["verdict"] == rr.FAIL_CLEAN and fragment in receipt["reasons"][0] and not (env["plan"] / "QUARANTINE.json").exists()
        for f in env["plan"].glob("receipt_*"):
            f.unlink()


def test_outputs_are_hashed_into_the_receipt_and_missing_outputs_fail_the_phase(env):
    out = env["tmp"] / "o.bin"
    spec = make_spec(env, outputs=[str(out)])
    runner = make_runner(env, [spec])
    assert runner.run_phase(spec)["verdict"] == rr.FAIL_CLEAN                                               # launcher gia khong tao tep => thieu dau ra
    for f in env["plan"].glob("receipt_*"):
        f.unlink()

    class Producing(FakeLauncher):
        def launch(self, spec, cap_s, **kwargs):
            out.write_bytes(b"data")
            return super().launch(spec, cap_s, **kwargs)

    runner = make_runner(env, [spec], launcher=Producing(env["clock"]))
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.PASS and receipt["outputs"] == {str(out): rr.sha256_hex(b"data")}


def test_a_required_file_that_changes_while_the_phase_runs_fails_it(env):
    class Mutating(FakeLauncher):
        def launch(self, spec, cap_s, **kwargs):
            env["script"].write_text("print('changed')\n", encoding="utf-8")
            return super().launch(spec, cap_s, **kwargs)

    spec = make_spec(env)
    receipt = make_runner(env, [spec], launcher=Mutating(env["clock"])).run_phase(spec)
    assert receipt["verdict"] == rr.FAIL_CLEAN and receipt["reasons"] == ["required_file_changed_during_phase"] and receipt["file_hashes_before"] != receipt["file_hashes_after"]


def test_a_launch_exception_is_a_clean_failure_for_a_pure_phase_and_a_quarantine_for_a_db_phase(env):
    pure = make_spec(env, "ph_a")
    receipt = make_runner(env, [pure], launcher=FakeLauncher(env["clock"], raises=OSError("boom"))).run_phase(pure)
    assert receipt["verdict"] == rr.FAIL_CLEAN and "launch_exception: OSError" in receipt["reasons"][0]
    for f in env["plan"].glob("receipt_*"):
        f.unlink()
    db = make_spec(env, "ph_db", kind="db")
    receipt = make_runner(env, [db], launcher=FakeLauncher(env["clock"], raises=OSError("boom")), observer=FakeObserver(env["clock"], [])).run_phase(db)
    assert receipt["verdict"] == rr.QUARANTINE and (env["plan"] / "QUARANTINE.json").exists()


# =========================================================================== phase DB: baseline / quiescence / quarantine
def db_runner(env, probes, results=None, **over):
    spec = make_spec(env, "ph_db", kind="db")
    observer = FakeObserver(env["clock"], probes)
    runner = make_runner(env, [spec, make_spec(env, "ph_next")], launcher=FakeLauncher(env["clock"], results=results), observer=observer, **over)
    return spec, runner, observer


@pytest.mark.parametrize("selftest", [None, {}, {**GOOD_SELFTEST, "pid_attribute_visible": False}, {k: v for k, v in GOOD_SELFTEST.items() if k != "innodb_trx_readable"}])
def test_a_db_phase_without_a_complete_selftest_receipt_is_refused_before_any_db_activity(env, selftest):
    spec = make_spec(env, "ph_db", kind="db")
    observer = FakeObserver(env["clock"], [])
    runner = make_runner(env, [spec], observer=observer, selftest=selftest)
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.REFUSED and observer.baseline_calls == 0 and runner.launcher.calls == []
    runner2 = make_runner(env, [spec], observer=None)
    (env["plan"] / "receipt_01_ph_db.json").unlink()
    assert runner2.run_phase(spec)["verdict"] == rr.REFUSED


def test_an_invalid_or_failing_baseline_is_refused_and_nothing_launches(env):
    spec = make_spec(env, "ph_db", kind="db")
    for baseline in ({"baseline_thread_id": -1, "baseline_uptime_s": 1.0, "probe_session_id": 5}, {"baseline_thread_id": 1, "baseline_uptime_s": float("nan"), "probe_session_id": 5},
                     {"baseline_thread_id": 1, "baseline_uptime_s": 1.0}, {"baseline_thread_id": True, "baseline_uptime_s": 1.0, "probe_session_id": 5}, "x"):
        observer = FakeObserver(env["clock"], [])
        observer.baseline = baseline
        runner = make_runner(env, [spec], observer=observer)
        receipt = runner.run_phase(spec)
        assert receipt["verdict"] == rr.REFUSED and receipt["reasons"][0].startswith("baseline_invalid") and runner.launcher.calls == []
        (env["plan"] / "receipt_01_ph_db.json").unlink()

    class Broken(FakeObserver):
        def capture_baseline(self):
            raise RuntimeError("db down")

    runner = make_runner(env, [spec], observer=Broken(env["clock"], []))
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.REFUSED and receipt["reasons"][0].startswith("baseline_capture_failed") and runner.launcher.calls == []


def test_a_db_phase_with_two_clean_probes_after_a_contained_exit_passes_and_records_the_evidence(env):
    spec, runner, observer = db_runner(env, [{}, {}])
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.PASS and observer.baseline_calls == 1 and observer.probe_calls == 2
    q = receipt["quiescence"]
    assert q["status"] == qm.VERIFIED and q["probe_count"] == 2 and q["assumptions"] == list(qm.ASSUMPTIONS) and receipt["selftest_sha256"] == "s" * 64
    stamps = [p["observed_at"] for p in q["probes"]]
    assert stamps[0] > q["child_exit_monotonic"] and stamps[1] - stamps[0] >= qm.MIN_SPACING_S and -1.0 not in stamps          # dong ho cua runner, khong phai cua observer
    assert receipt["baseline"] == {"baseline_thread_id": 1000, "baseline_uptime_s": 500.0, "probe_session_id": 5000}


def test_a_session_of_the_phase_after_exit_quarantines_records_candidates_never_kills_and_blocks_the_next_phase(env):
    leftover = {"rows": [row(1500, db=None, pid=4242)]}
    spec, runner, observer = db_runner(env, [leftover, leftover, leftover, leftover])
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.QUARANTINE and receipt["quiescence"]["status"] == qm.NOT_QUIESCENT and receipt["quiescence"]["kill_candidates"] == [1500]
    assert observer.probe_calls == rr.MAX_PROBES and not hasattr(observer, "kill")                       # ung vien chi la DU LIEU; runner khong co duong KILL
    assert (env["plan"] / "QUARANTINE.json").exists()
    nxt = runner.run_phase(make_spec(env, "ph_next"))
    assert nxt["verdict"] == rr.REFUSED and nxt["reasons"][0].startswith("quarantine_marker_present") and len(runner.launcher.calls) == 1


def test_busy_then_two_clean_probes_passes_but_busy_then_clear_alone_would_not(env):
    leftover = {"rows": [row(1500, db=None, pid=4242)]}
    spec, runner, observer = db_runner(env, [leftover, {}, {}])
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.PASS and observer.probe_calls == 3 and receipt["quiescence"]["probe_count"] == 3
    for f in env["plan"].glob("receipt_*"):
        f.unlink()
    spec, runner, observer = db_runner(env, [leftover, {}], max_probes=2)
    assert runner.run_phase(spec)["verdict"] == rr.QUARANTINE


def test_a_probe_error_or_scope_violation_or_missing_uptime_quarantines(env):
    for probes in ([RuntimeError("db down"), {}, {}], [{"rows": [row(1500, db="hotel_price_intel", pid=4242)]}] * 4, [{"uptime_s": None}, {"uptime_s": None}]):
        spec, runner, observer = db_runner(env, probes)
        receipt = runner.run_phase(spec)
        assert receipt["verdict"] == rr.QUARANTINE, probes
        for f in env["plan"].glob("*"):
            f.unlink()


def test_uncontained_exit_quarantines_without_probing_the_database(env):
    spec, runner, observer = db_runner(env, [{}, {}], results=[{"contained": False}])
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.QUARANTINE and receipt["reasons"][0].startswith("containment_not_proven") and observer.probe_calls == 0


def test_a_failed_db_child_with_verified_quiescence_is_a_clean_failure_but_a_timeout_still_runs_the_probes(env):
    spec, runner, observer = db_runner(env, [{}, {}], results=[{"exit_code": None, "timed_out": True, "killed_tree": True}])
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.FAIL_CLEAN and receipt["quiescence"]["status"] == qm.VERIFIED and observer.probe_calls == 2 and not (env["plan"] / "QUARANTINE.json").exists()


def test_pure_phases_never_touch_the_observer(env):
    spec = make_spec(env, "ph_a")
    observer = FakeObserver(env["clock"], [])
    make_runner(env, [spec], observer=observer).run_phase(spec)
    assert observer.baseline_calls == 0 and observer.probe_calls == 0


# =========================================================================== Job Object that (Windows)
real = pytest.mark.skipif(sys.platform != "win32", reason="Job Object chi co tren Windows")


class SystemClock:
    import time as _t

    def monotonic(self):
        return self._t.monotonic()

    def sleep(self, seconds):
        self._t.sleep(seconds)


def real_runner(env, specs, **over):
    launcher_cls = rr.load_job_launcher_class()
    out = env["tmp"] / "logs"
    out.mkdir(exist_ok=True)
    approvals = {s["name"]: rr.argv_sha256(s) for s in specs}
    kwargs = dict(plan_dir=env["plan"], approvals=approvals, launcher=launcher_cls(out, sample_s=0.05, grace_s=5), clock=SystemClock(), gate=lambda: (True, []), total_budget_s=600, margin_s=1)
    kwargs.update(over)
    runner = rr.Runner(**kwargs)
    if "expected_tooling" not in over:
        runner.expected_tooling = runner.tooling()
    return runner


@real
def test_real_job_a_successful_child_passes_with_hashed_logs_and_a_chained_second_phase(env):
    env["script"].write_text("print('hello from the phase')\n", encoding="utf-8")
    specs = [make_spec(env, "ph_a", deadline_s=30), make_spec(env, "ph_b", deadline_s=30)]
    receipts = real_runner(env, specs).run_plan(specs)
    assert [r["verdict"] for r in receipts] == [rr.PASS, rr.PASS]
    launch = receipts[0]["launch"]
    assert launch["exit_code"] == 0 and launch["contained"] is True and launch["killed_tree"] is False and Path(launch["stdout_path"]).read_text().strip() == "hello from the phase"
    assert launch["stdout_sha256"] == rr.file_sha256(launch["stdout_path"]) and receipts[1]["prev_receipt_sha256"] == receipts[0]["receipt_sha256"] and launch["pids_seen"]


@real
def test_real_job_a_hung_child_is_killed_at_its_deadline_and_the_tree_is_proven_empty(env):
    env["script"].write_text("import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\ntime.sleep(120)\n", encoding="utf-8")
    spec = make_spec(env, "ph_hang", deadline_s=1.0)
    runner = real_runner(env, [spec])
    started = SystemClock().monotonic()
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.FAIL_CLEAN and receipt["launch"]["timed_out"] is True and receipt["launch"]["killed_tree"] is True and receipt["launch"]["contained"] is True
    assert SystemClock().monotonic() - started < 30 and len(receipt["launch"]["pids_seen"]) >= 2          # root + hau due da bi thay trong job va bi giet cung


@real
def test_real_job_descendants_that_outlive_a_successful_root_make_the_phase_fail_and_are_killed(env):
    env["script"].write_text("import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n", encoding="utf-8")        # root thoat 0, con hau due song
    spec = make_spec(env, "ph_orphan", deadline_s=20)
    receipt = real_runner(env, [spec]).run_phase(spec)
    assert receipt["launch"]["exit_code"] == 0 and receipt["launch"]["killed_tree"] is True and receipt["launch"]["contained"] is True
    assert receipt["verdict"] == rr.FAIL_CLEAN and "descendants_outlived_root=True" in receipt["reasons"][0]


@real
def test_real_job_the_child_environment_is_exactly_the_approved_one(env):
    env["script"].write_text("import os; print(sorted(os.environ))\n", encoding="utf-8")
    spec = make_spec(env, "ph_env", deadline_s=20)
    receipt = real_runner(env, [spec]).run_phase(spec)
    keys = Path(receipt["launch"]["stdout_path"]).read_text()
    assert receipt["verdict"] == rr.PASS and keys.strip() == "['SYSTEMROOT', 'TEMP']"                       # Windows chuan hoa ten bien thanh chu hoa; khong co PATH/USERPROFILE/...


# =========================================================================== cong trong luc chay (abort) + cong that tu controller nen da ghim
def test_the_runner_hands_its_gate_to_the_launcher_so_the_phase_can_be_aborted_in_flight(env):
    spec = make_spec(env)
    launcher = FakeLauncher(env["clock"])
    make_runner(env, [spec], launcher=launcher).run_phase(spec)
    assert launcher.last_abort() == (True, [])


def test_a_phase_aborted_by_the_gate_is_a_clean_failure_with_the_reason_and_a_db_phase_still_proves_quiescence(env):
    spec = make_spec(env)
    runner = make_runner(env, [spec], launcher=FakeLauncher(env["clock"], results=[{"exit_code": None, "aborted_by_gate": ["crawler_busy"], "killed_tree": True}]))
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.FAIL_CLEAN and receipt["reasons"] == ["aborted_by_gate: ['crawler_busy']"] and receipt["launch"]["aborted_by_gate"] == ["crawler_busy"]
    for f in env["plan"].glob("receipt_*"):
        f.unlink()
    spec, runner, observer = db_runner(env, [{}, {}], results=[{"exit_code": None, "aborted_by_gate": ["window_closing"], "killed_tree": True}])
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.FAIL_CLEAN and receipt["quiescence"]["status"] == qm.VERIFIED and observer.probe_calls == 2


@real
def test_real_job_a_gate_that_is_lost_mid_phase_kills_the_tree_quickly_and_records_it(env):
    env["script"].write_text("import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\ntime.sleep(120)\n", encoding="utf-8")
    spec = make_spec(env, "ph_gate", deadline_s=60)
    state = {"calls": 0}

    def gate():
        state["calls"] += 1
        return (True, []) if state["calls"] <= 1 else (False, ["crawler_busy"])                              # lan 1 (truoc launch) ok; lan sau mat cong

    runner = real_runner(env, [spec], gate=gate, gate_poll_s=0.3)
    started = SystemClock().monotonic()
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.FAIL_CLEAN and receipt["launch"]["aborted_by_gate"] == ["crawler_busy"] and receipt["launch"]["killed_tree"] is True and receipt["launch"]["contained"] is True
    assert SystemClock().monotonic() - started < 30 and receipt["launch"]["timed_out"] is False and receipt["launch"]["gate_polls"] >= 1


def load_evidence_module():
    s = importlib.util.spec_from_file_location("crawl_idle_evidence_for_runner", TOOLS / "crawl_idle_evidence.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def test_the_real_gate_uses_the_pinned_base_controller_window_and_crawler_idle_evidence(tmp_path, monkeypatch):
    import datetime as dt
    ev = load_evidence_module()
    path = tmp_path / "evidence.json"
    check, wa = rr.make_evidence_gate(path)
    assert wa.__source_sha256__ == rr.PINNED_BASE_CONTROLLER_SHA256
    vn_18 = dt.datetime(2026, 10, 9, 18, 0, tzinfo=wa.VN).astimezone(dt.timezone.utc)
    monkeypatch.setattr(wa, "_now_utc", lambda: vn_18)
    ok, reasons = check()                                                                                  # chua co tep bang chung
    assert ok is False and "khong doc duoc bang chung" in reasons[0]
    path.write_text(json.dumps(ev.build_evidence(0, 0, {"id": 66, "status": "completed"}, vn_18, session_read_only=True)), encoding="utf-8")
    assert check() == (True, [])
    path.write_text(json.dumps(ev.build_evidence(1, 3, {"id": 66, "status": "running"}, vn_18, session_read_only=True)), encoding="utf-8")
    ok, reasons = check()
    assert ok is False and "crawler" in reasons[0]
    path.write_text(json.dumps(ev.build_evidence(0, 0, {"id": 66, "status": "completed"}, vn_18, session_read_only=True)), encoding="utf-8")
    vn_10 = dt.datetime(2026, 10, 9, 10, 0, tzinfo=wa.VN).astimezone(dt.timezone.utc)
    monkeypatch.setattr(wa, "_now_utc", lambda: vn_10)
    ok, reasons = check()
    assert ok is False and "cong gio" in reasons[0]                                                        # trong khung crawl 00:30-17:00 VN
    monkeypatch.setattr(wa, "_now_utc", lambda: vn_18 + dt.timedelta(hours=2))
    assert check()[0] is False                                                                             # bang chung het han / qua cu


def test_the_real_gate_refuses_to_build_when_the_base_controller_is_not_the_pinned_one(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "PINNED_BASE_CONTROLLER_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="controller nen"):
        rr.make_evidence_gate(tmp_path / "evidence.json")


def test_an_aborted_result_never_passes_even_if_the_child_reported_exit_zero(env):
    spec = make_spec(env)
    receipt = make_runner(env, [spec], launcher=FakeLauncher(env["clock"], results=[{"exit_code": 0, "aborted_by_gate": ["x"], "killed_tree": True}])).run_phase(spec)
    assert receipt["verdict"] == rr.FAIL_CLEAN and receipt["reasons"] == ["aborted_by_gate: ['x']"]


# =========================================================================== RealJobLauncher voi Job GIA (khong can Windows): thu tu goi, timeout, abort, mo coi, khong chua duoc
class FakeJobModule:
    """Job gia: moi lan goi duoc ghi vao `log`; hanh vi cau hinh bang thuoc tinh lop."""
    log: list = []
    child_runtime = 5.0            # root thoat sau bay nhieu giay (dong ho gia); None = khong bao gio thoat
    empty_after_exit = True        # job rong ngay khi root thoat
    empty_after_kill = True        # job rong sau kill_tree
    pids_list = [111, 222]
    pids_raises = False
    clock = None
    __source_sha256__ = "e" * 64

    class Job:
        def __init__(self):
            self.killed = False
            self.exited = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            FakeJobModule.log.append("close")

        def spawn(self, argv, *, cwd, env, stdout_path, stderr_path):
            FakeJobModule.log.append("spawn")
            Path(stdout_path).write_bytes(b"out")
            Path(stderr_path).write_bytes(b"err")
            started = FakeJobModule.clock.monotonic()
            job = self

            class Child:
                pid = 111

                def wait(self, timeout_s):
                    FakeJobModule.log.append(f"child_wait:{timeout_s}")
                    runtime = FakeJobModule.child_runtime
                    if job.killed:
                        job.exited = True
                        return 1
                    elapsed = FakeJobModule.clock.monotonic() - started
                    if runtime is not None and elapsed + (timeout_s or 0) >= runtime:
                        FakeJobModule.clock.t = max(FakeJobModule.clock.t, started + runtime)
                        job.exited = True
                        return 0
                    FakeJobModule.clock.t += timeout_s or 0
                    return None
            return Child()

        def pids(self):
            if FakeJobModule.pids_raises:
                raise OSError("truncated")
            return list(FakeJobModule.pids_list)

        def kill_tree(self):
            FakeJobModule.log.append("kill_tree")
            self.killed = True

        def wait_empty(self, timeout_s):
            FakeJobModule.log.append("wait_empty")
            return FakeJobModule.empty_after_kill if self.killed else (FakeJobModule.empty_after_exit if self.exited else False)


def fake_launcher(env, **cfg):
    for key in ("child_runtime", "empty_after_exit", "empty_after_kill", "pids_list", "pids_raises"):
        setattr(FakeJobModule, key, {"child_runtime": 5.0, "empty_after_exit": True, "empty_after_kill": True, "pids_list": [111, 222], "pids_raises": False}[key])
    for key, value in cfg.items():
        setattr(FakeJobModule, key, value)
    FakeJobModule.log = []
    FakeJobModule.clock = env["clock"]
    out = env["tmp"] / "logs"
    out.mkdir(exist_ok=True)
    return rr.load_job_launcher_class(FakeJobModule)(out, sample_s=0.5, grace_s=3.0, clock=env["clock"])


def test_launcher_a_normal_exit_is_contained_without_any_kill(env):
    result = fake_launcher(env).launch(make_spec(env), 60.0)
    assert result["exit_code"] == 0 and result["timed_out"] is False and result["aborted_by_gate"] is None and result["killed_tree"] is False and result["contained"] is True
    assert "kill_tree" not in FakeJobModule.log and result["pids_seen"] == [111, 222] and result["stdout_sha256"] == rr.sha256_hex(b"out")


def test_launcher_a_timeout_kills_the_tree_before_waiting_for_the_job_to_empty(env):
    result = fake_launcher(env, child_runtime=None).launch(make_spec(env), 3.0)
    assert result["timed_out"] is True and result["killed_tree"] is True and result["contained"] is True and result["exit_code"] == 1
    log = FakeJobModule.log
    assert log.index("kill_tree") < log.index("wait_empty")                                                  # giet NGAY khi het gio, khong cho grace truoc


def test_launcher_a_lost_gate_aborts_after_the_poll_interval_and_kills_the_tree(env):
    polls = []

    def gate():
        polls.append(env["clock"].monotonic())
        return (False, ["crawler_busy"]) if len(polls) >= 2 else (True, [])

    result = fake_launcher(env, child_runtime=None).launch(make_spec(env), 600.0, should_abort=gate, gate_poll_s=2.0)
    assert result["aborted_by_gate"] == ["crawler_busy"] and result["timed_out"] is False and result["killed_tree"] is True and result["gate_polls"] == 2 and polls[1] - polls[0] >= 2.0
    assert FakeJobModule.log.index("kill_tree") < FakeJobModule.log.index("wait_empty")


def test_launcher_descendants_left_after_a_clean_root_exit_are_killed_and_reported(env):
    result = fake_launcher(env, empty_after_exit=False).launch(make_spec(env), 60.0)
    assert result["exit_code"] == 0 and result["killed_tree"] is True and result["contained"] is True and result["timed_out"] is False


def test_launcher_a_job_that_never_empties_even_after_the_kill_is_reported_uncontained(env):
    result = fake_launcher(env, empty_after_exit=False, empty_after_kill=False).launch(make_spec(env), 60.0)
    assert result["contained"] is False and result["killed_tree"] is True
    assert FakeJobModule.log.count("kill_tree") == 1


def test_launcher_a_truncated_pid_list_is_ignored_for_sampling_but_the_root_pid_is_still_recorded(env):
    result = fake_launcher(env, pids_raises=True).launch(make_spec(env), 60.0)
    assert result["pids_seen"] == [111] and result["contained"] is True


def test_an_uncontained_launcher_result_makes_a_db_phase_quarantine_through_the_real_launcher_class(env):
    spec = make_spec(env, "ph_db", kind="db")
    launcher = fake_launcher(env, empty_after_exit=False, empty_after_kill=False)
    observer = FakeObserver(env["clock"], [{}, {}])
    runner = make_runner(env, [spec], launcher=launcher, observer=observer)
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.QUARANTINE and receipt["reasons"][0].startswith("containment_not_proven") and observer.probe_calls == 0


# =========================================================================== cong cu duoc ghim + cua so lam viec
def test_tooling_hashes_are_recorded_in_every_receipt_and_must_match_the_approved_pin(env):
    spec = make_spec(env)
    runner = make_runner(env, [spec])
    receipt = runner.run_phase(spec)
    assert receipt["tooling"]["runner_sha256"] == rr.file_sha256(TOOLS / "repair_phase_runner_v1.py") and receipt["tooling"]["quiescence_model_sha256"] == qm.__source_sha256__
    assert set(receipt["tooling"]) == {"runner_sha256", "quiescence_model_sha256", "windows_job_sha256"}


@pytest.mark.parametrize("key", ["runner_sha256", "quiescence_model_sha256", "windows_job_sha256"])
def test_any_tooling_hash_that_differs_from_the_approved_pin_is_refused_before_anything_runs(env, key):
    spec = make_spec(env)
    base = make_runner(env, [spec]).tooling()
    for f in env["plan"].glob("receipt_*"):
        f.unlink()
    launcher = FakeLauncher(env["clock"])
    runner = make_runner(env, [spec], launcher=launcher, expected_tooling={**base, key: "0" * 64})
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.REFUSED and receipt["reasons"] == ["tooling_not_approved"] and launcher.calls == []


def test_a_missing_tooling_pin_is_refused_never_treated_as_no_pin(env):
    spec = make_spec(env)
    launcher = FakeLauncher(env["clock"])
    runner = make_runner(env, [spec], launcher=launcher, expected_tooling=None)
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.REFUSED and receipt["reasons"] == ["tooling_not_approved"] and launcher.calls == []


def test_seconds_until_the_end_of_the_working_window_are_computed_on_vietnam_time():
    import datetime as dt
    vn = dt.timezone(dt.timedelta(hours=7))
    cases = [((18, 0), 6 * 3600 + 20 * 60), ((23, 59), 21 * 60), ((0, 10), 10 * 60), ((0, 20), 24 * 3600), ((0, 25), 24 * 3600 - 5 * 60), ((17, 0), 7 * 3600 + 20 * 60)]
    for (hour, minute), expected in cases:
        now = dt.datetime(2026, 10, 9, hour, minute, tzinfo=vn).astimezone(dt.timezone.utc)
        assert rr.seconds_until_vn(now) == expected, (hour, minute)
    assert rr.seconds_until_vn(dt.datetime(2026, 10, 9, 12, 0, tzinfo=dt.timezone.utc), hour=22, minute=0) == 3 * 3600                  # 12:00 UTC = 19:00 VN -> 22:00 VN


def test_the_working_window_end_bounds_the_budget_even_when_the_total_budget_is_larger(env):
    spec = make_spec(env, deadline_s=60)
    launcher = FakeLauncher(env["clock"])
    end = env["clock"].monotonic() + 69                                                                      # cua so con 69 s < 60 + margin 10
    runner = make_runner(env, [spec], launcher=launcher, window_end_monotonic=end, total_budget_s=3600)
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.REFUSED and receipt["reasons"][0].startswith("budget_insufficient") and launcher.calls == []
    for f in env["plan"].glob("receipt_*"):
        f.unlink()
    runner = make_runner(env, [spec], launcher=launcher, window_end_monotonic=env["clock"].monotonic() + 71, total_budget_s=3600)
    assert runner.run_phase(spec)["verdict"] == rr.PASS


def test_a_small_total_budget_still_binds_when_the_window_is_far_away(env):
    spec = make_spec(env, deadline_s=60)
    launcher = FakeLauncher(env["clock"])
    runner = make_runner(env, [spec], launcher=launcher, window_end_monotonic=env["clock"].monotonic() + 100000, total_budget_s=69)
    receipt = runner.run_phase(spec)
    assert receipt["verdict"] == rr.REFUSED and receipt["reasons"][0].startswith("budget_insufficient") and launcher.calls == []
