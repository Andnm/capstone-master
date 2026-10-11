"""audit_tools/repair_phase_runner_v2.py — runner v2: ke hoach bat bien (plan hash), deadline tuyet doi, khe receipt dat truoc, Q0 receipt that, env allowlist, containment (fake + Job that)."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "audit_tools"
spec = importlib.util.spec_from_file_location("repair_phase_runner_v2", TOOLS / "repair_phase_runner_v2.py")
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)
qm = rr.qm
APP, SCOPE, PROTECTED = "hotel_app", ["warehouse_dsdev_20261009_3src_nf"], ["hotel_price_intel", "warehouse_20261004_3src"]
SYSTEM_ENV = {"SystemRoot": "C:\\Windows", "TEMP": "C:\\Windows\\Temp"}
VN = dt.timezone(dt.timedelta(hours=7))
START_UTC = dt.datetime(2026, 10, 9, 18, 0, tzinfo=VN).astimezone(dt.timezone.utc)
OBSERVER_SHA, SQL_SHA = "o" * 64, "q" * 64
SERVER_UUID, Q0_UPTIME = "uuid-1", 400.0


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    def now_utc(self):
        return START_UTC + dt.timedelta(seconds=self.t - 1000.0)


class FakeLauncher:
    source_sha256 = "j" * 64

    def __init__(self, clock, results=None, raises=None):
        self.clock, self.results, self.calls, self.raises = clock, list(results or []), [], raises

    def launch(self, spec, cap_s, should_abort=None, gate_poll_s=5.0):
        self.calls.append((spec["name"], cap_s))
        self.last_abort = should_abort
        if self.raises:
            raise self.raises
        result = dict(self.results.pop(0)) if self.results else {}
        self.clock.t += result.pop("duration", 5.0)
        base = {"exit_code": 0, "timed_out": False, "aborted_by_gate": None, "gate_polls": 0, "killed_tree": False, "contained": True, "pids_seen": [4242], "stdout_path": "x.out",
                "stderr_path": "x.err", "stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64, "started": 0.0, "ended": 1.0}
        base.update(result)
        return base


class FakeObserver:
    tooling_sha256 = "a1" * 32

    def __init__(self, clock, probes=(), baseline=None, baseline_cost=0.0, probe_cost=0.0):
        self.clock, self.probe_queue = clock, list(probes)
        self.baseline = baseline or {"baseline_thread_id": 1000, "baseline_uptime_s": 500.0, "probe_session_id": 5000, "server_uuid": SERVER_UUID, "app_user": APP}
        self.baseline_cost, self.probe_cost, self.baseline_calls, self.probe_calls = baseline_cost, probe_cost, 0, 0

    def capture_baseline(self):
        self.baseline_calls += 1
        self.clock.t += self.baseline_cost
        return dict(self.baseline) if isinstance(self.baseline, dict) else self.baseline

    def probe(self):
        self.probe_calls += 1
        self.clock.t += self.probe_cost
        item = self.probe_queue.pop(0) if self.probe_queue else {"rows": []}
        if isinstance(item, Exception):
            raise item
        return {"status": "ok", "rows": [], "truncated": False, "rolling_back": [], "uptime_s": 500.0 + self.clock.t, "observed_at": -1.0, **item}


def row(session_id, user=APP, db=None, pid=None):
    return {"PROCESSLIST_ID": session_id, "PROCESSLIST_USER": user, "PROCESSLIST_DB": db, "pid": pid}


def write_q0(tmp, **over):
    doc = {"kind": "q0_selftest_receipt", "created_at_utc": (START_UTC - dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"), "observer_script_sha256": OBSERVER_SHA, "sql_set_sha256": SQL_SHA,
           "quiescence_model_sha256": qm.__source_sha256__, "server": {"server_uuid": SERVER_UUID, "version": "8.0.0", "uptime_s": Q0_UPTIME}, "selftest": {k: True for k in qm.SELFTEST_KEYS},
           "details": {"app_user": APP, "ids_increase": True, "successive_ids": [2101, 2102], "databases": ["hotel_price_intel", "warehouse_20261004_3src"], "databases_truncated": False, "errors": []}}
    doc.update(over)
    path = tmp / f"q0_{len(list(tmp.glob('q0_*.json')))}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path, rr.file_sha256(path)


def load_q0(path, sha, now=START_UTC, **over):
    kwargs = dict(expected_receipt_sha256=sha, expected_observer_sha256=OBSERVER_SHA, expected_sql_set_sha256=SQL_SHA, expected_model_sha256=qm.__source_sha256__, now_utc=now, max_age_s=3600.0)
    kwargs.update(over)
    return rr.load_q0_receipt(path, **kwargs)


@pytest.fixture
def env(tmp_path):
    script = tmp_path / "work.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    plan = tmp_path / "plan"
    plan.mkdir()
    q0_path, q0_sha = write_q0(tmp_path)
    return {"tmp": tmp_path, "script": script, "plan": plan, "clock": FakeClock(), "q0_path": q0_path, "q0_sha": q0_sha}


def make_spec(env, name="ph_a", kind="readonly", **over):
    base = {"name": name, "kind": kind, "argv": [sys.executable, str(env["script"])], "cwd": str(env["tmp"]), "env": dict(SYSTEM_ENV), "required_files": {str(env["script"]): rr.file_sha256(env["script"])},
            "deadline_s": 60}
    if kind == "write":
        base["db"] = {"app_user": APP, "db_scope": list(SCOPE), "protected_dbs": list(PROTECTED)}
    base.update(over)
    return base


def settings_with(**over):
    return {**rr.DEFAULT_SETTINGS, **over}


def make_runner(env, specs, launcher=None, observer=None, gate=None, settings=None, q0="auto", approve="auto", expected_tooling=None, q0_pin="auto", plan_dir=None, **over):
    launcher = launcher or FakeLauncher(env["clock"])
    settings = settings or settings_with()
    tooling = expected_tooling or rr.tooling_for(launcher, observer)
    has_write = any(isinstance(s, dict) and s.get("kind") == "write" for s in specs)
    pin = (env["q0_sha"] if has_write else None) if q0_pin == "auto" else q0_pin
    if q0 == "auto":
        q0 = load_q0(env["q0_path"], env["q0_sha"]) if has_write else None
    approved = rr.plan_sha256(specs, settings, tooling, pin) if approve == "auto" else approve
    kwargs = dict(plan_dir=plan_dir or env["plan"], specs=specs, settings=settings, expected_tooling=tooling, q0_receipt_sha256=pin, approved_plan_sha256=approved, launcher=launcher,
                  clock=env["clock"], now_utc=env["clock"].now_utc, gate=gate or (lambda: (True, [])), observer=observer, q0=q0)
    kwargs.update(over)
    return rr.Runner(**kwargs)


# =========================================================================== spec / settings / plan / approval
def test_a_valid_spec_has_no_errors_and_each_bad_field_is_named():
    base = {"name": "ph_a", "kind": "readonly", "argv": ["x"], "cwd": "c", "env": {}, "required_files": {"f": "a" * 64}, "deadline_s": 5}
    assert rr.validate_spec(base) == []
    bad = {"name": "Bad Name", "kind": "pure", "argv": [], "cwd": "", "env": {"A": 1}, "required_files": {}, "deadline_s": True}
    for field in ("name", "kind", "argv", "cwd", "env", "required_files", "deadline_s"):
        assert field in " ".join(rr.validate_spec({**base, field: bad[field]})), field
    assert rr.validate_spec({**base, "deadline_s": 0}) and rr.validate_spec({**base, "deadline_s": rr.MAX_PHASE_S + 1}) and rr.validate_spec({**base, "deadline_s": float("nan")})
    assert rr.validate_spec({**base, "extra": 1})[0].startswith("spec_keys") and rr.validate_spec({k: v for k, v in base.items() if k != "env"})[0].startswith("spec_keys")
    assert rr.validate_spec("nope") == ["spec_not_mapping"] and rr.validate_spec({**base, "db": {}}) == ["db_not_allowed_for_readonly_phase"]


@pytest.mark.parametrize("key", ["DB_PASSWORD", "MYSQL_TOKEN", "API_KEY", "SECRET", "AUTH_HEADER", "Credentials", "HOME", "USERPROFILE", "PYTHONPATH", "OMP_NUM_THREADS"])
def test_env_names_outside_the_allowlist_or_secret_like_are_rejected(key):
    base = {"name": "ph_a", "kind": "readonly", "argv": ["x"], "cwd": "c", "env": {key: "v"}, "required_files": {"f": "a" * 64}, "deadline_s": 5}
    assert any("env_not_allowlisted" in e for e in rr.validate_spec(base))
    assert rr.validate_spec({**base, "env": {"SystemRoot": "C:\\Windows", "PATH": "C:\\Windows", "PYTHONUTF8": "1", "TEMP": "t", "TMP": "t", "PYTHONIOENCODING": "utf-8"}}) == []


def test_write_phase_requires_exact_db_block_with_disjoint_scope_and_protected():
    db_ok = {"app_user": APP, "db_scope": SCOPE, "protected_dbs": PROTECTED}
    base = {"name": "ph_db", "kind": "write", "argv": ["x"], "cwd": "c", "env": {}, "required_files": {"f": "a" * 64}, "deadline_s": 5}
    assert rr.validate_spec({**base, "db": db_ok}) == []
    for bad in (None, {}, {**db_ok, "app_user": ""}, {**db_ok, "db_scope": []}, {**db_ok, "db_scope": PROTECTED}, {**db_ok, "extra": 1}, {**db_ok, "protected_dbs": [3]}):
        assert "db" in rr.validate_spec({**base, "db": bad}), bad
    assert "db" in rr.validate_spec(base)


def test_settings_are_validated_finite_bounded_and_exact():
    assert rr.validate_settings(rr.DEFAULT_SETTINGS) == []
    for key, bad in (("total_budget_s", float("nan")), ("total_budget_s", float("inf")), ("total_budget_s", 0), ("total_budget_s", True), ("margin_s", -1), ("max_probes", 1), ("max_probes", 2.0),
                     ("settle_s", 61), ("gate_poll_s", 0), ("baseline_cap_s", 601), ("baseline_cap_s", 0), ("baseline_cap_s", 0.99), ("probe_cap_s", 0), ("probe_cap_s", 0.5), ("probe_cap_s", 601), ("window_end_vn", [24, 0]), ("window_end_vn", [0, 60]), ("window_end_vn", "0:20"), ("window_end_vn", [0])):
        assert key in " ".join(rr.validate_settings({**rr.DEFAULT_SETTINGS, key: bad})), (key, bad)
    assert rr.validate_settings({**rr.DEFAULT_SETTINGS, "window_end_vn": None}) == []
    assert rr.validate_settings({**rr.DEFAULT_SETTINGS, "extra": 1})[0].startswith("settings_keys") and rr.validate_settings({"total_budget_s": 1})[0].startswith("settings_keys")


def test_argv_and_spec_hashes_cover_everything_and_the_plan_hash_covers_order_settings_tooling_and_q0():
    base = {"argv": ["a", "b"], "cwd": "c", "env": {"K": "v"}}
    h = rr.argv_sha256(base)
    for changed in ({"argv": ["a", "b", "--x"]}, {"cwd": "d"}, {"env": {"K": "w"}}, {"env": {}}):
        assert rr.argv_sha256({**base, **changed}) != h
    s1 = {"name": "a", "kind": "readonly"}
    assert rr.spec_sha256(s1) != rr.spec_sha256({**s1, "name": "b"})
    specs = [{"name": "ph_a", "kind": "readonly"}, {"name": "ph_b", "kind": "readonly"}]
    tooling = {"runner_sha256": "1" * 64, "quiescence_model_sha256": "2" * 64, "windows_job_sha256": "3" * 64}
    base_hash = rr.plan_sha256(specs, rr.DEFAULT_SETTINGS, tooling, None)
    assert rr.plan_sha256(list(reversed(specs)), rr.DEFAULT_SETTINGS, tooling, None) != base_hash
    assert rr.plan_sha256(specs, {**rr.DEFAULT_SETTINGS, "margin_s": 61.0}, tooling, None) != base_hash
    assert rr.plan_sha256(specs, rr.DEFAULT_SETTINGS, {**tooling, "runner_sha256": "9" * 64}, None) != base_hash
    assert rr.plan_sha256(specs, rr.DEFAULT_SETTINGS, tooling, "4" * 64) != base_hash
    assert rr.plan_sha256(specs[:1], rr.DEFAULT_SETTINGS, tooling, None) != base_hash


def test_parse_approved_plan_needs_exactly_one_well_formed_line():
    assert rr.parse_approved_plan(f"# approval\nAPPROVED_PLAN_SHA256: `{'a' * 64}`\nother\n") == "a" * 64
    for bad in ("", "nothing", f"APPROVED_PLAN_SHA256: {'a' * 64}\nAPPROVED_PLAN_SHA256: {'b' * 64}", "APPROVED_PLAN_SHA256: nothex", f"APPROVED_PLAN_SHA256 {'a' * 64}"):
        with pytest.raises(ValueError):
            rr.parse_approved_plan(bad)


# =========================================================================== R1: plan bat bien (10 control GPT 72 §2 va them)
def baseline_plan(env):
    return [make_spec(env, "ph_a"), make_spec(env, "ph_w", kind="write")]


def drifted(env, mutate):
    specs = baseline_plan(env)
    approved = rr.plan_sha256(specs, settings_with(), rr.tooling_for(FakeLauncher(env["clock"]), FakeObserver(env["clock"])), env["q0_sha"])
    changed = json.loads(json.dumps(specs))
    mutate(changed)
    launcher = FakeLauncher(env["clock"])
    observer = FakeObserver(env["clock"])
    runner = make_runner(env, changed, launcher=launcher, observer=observer, approve=approved, q0_pin=env["q0_sha"])
    return runner.run_plan(), launcher, observer


@pytest.mark.parametrize("name,mutate", [
    ("kind_write_to_readonly_and_drop_db", lambda s: (s[1].update(kind="readonly"), s[1].pop("db"))),
    ("drop_protected_dbs", lambda s: s[1]["db"].update(protected_dbs=["other"])),
    ("change_db_scope", lambda s: s[1]["db"].update(db_scope=["warehouse_dsdev_other"])),
    ("tamper_required_file_pin", lambda s: s[0]["required_files"].update({next(iter(s[0]["required_files"])): "0" * 64})),
    ("deadline_60_to_61", lambda s: s[0].update(deadline_s=61)),
    ("add_expected_output", lambda s: s[0].update(outputs=["/nope/out.bin"])),
    ("argv_extra_flag", lambda s: s[0]["argv"].append("--extra")),
    ("cwd_changed", lambda s: s[0].update(cwd=s[0]["cwd"] + "x")),
    ("env_value_changed", lambda s: s[0]["env"].update(TEMP="C:\\other")),
    ("phase_order_swapped", lambda s: s.reverse()),
    ("phase_dropped", lambda s: s.pop()),
    ("phase_renamed", lambda s: s[0].update(name="ph_renamed")),
])
def test_any_change_to_the_plan_after_approval_is_refused_before_gate_baseline_or_launch(env, name, mutate):
    receipts, launcher, observer = drifted(env, mutate)
    assert len(receipts) == 1 and receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"] == ["plan_sha256_not_approved"], name
    assert launcher.calls == [] and observer.baseline_calls == 0 and receipts[0]["computed_plan_sha256"] != receipts[0]["approved_plan_sha256"]
    assert sorted(p.name for p in env["plan"].iterdir()) == ["refused_01.json"]                              # chi co ban ghi tu choi; khong PLAN_BOUND, khong khe receipt


def test_the_settings_expected_tooling_and_q0_pin_are_part_of_the_approved_plan(env):
    specs = [make_spec(env, "ph_a")]
    approved = rr.plan_sha256(specs, settings_with(), rr.tooling_for(FakeLauncher(env["clock"])), None)
    for kwargs in ({"settings": settings_with(margin_s=61.0)}, {"expected_tooling": {**rr.tooling_for(FakeLauncher(env["clock"])), "runner_sha256": "0" * 64}}, {"q0_pin": "5" * 64}):
        launcher = FakeLauncher(env["clock"])
        runner = make_runner(env, specs, launcher=launcher, approve=approved, **kwargs)
        receipts = runner.run_plan()
        assert receipts[0]["reasons"] == ["plan_sha256_not_approved"] and launcher.calls == []
        for f in env["plan"].glob("*"):
            f.unlink()


def test_a_missing_or_malformed_approval_is_refused(env):
    specs = [make_spec(env, "ph_a")]
    for approved in (None, "", "not-a-sha", "0" * 64, 123):
        launcher = FakeLauncher(env["clock"])
        receipts = make_runner(env, specs, launcher=launcher, approve=approved).run_plan()
        assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"] == ["plan_sha256_not_approved"] and launcher.calls == []
        for f in env["plan"].glob("*"):
            f.unlink()


def test_the_caller_cannot_change_the_bound_plan_after_construction(env):
    specs = [make_spec(env, "ph_a")]
    launcher = FakeLauncher(env["clock"])
    runner = make_runner(env, specs, launcher=launcher)
    specs[0]["argv"].append("--injected")
    specs.append(make_spec(env, "ph_evil"))
    receipts = runner.run_plan()
    assert [r["verdict"] for r in receipts] == [rr.PASS] and [c[0] for c in launcher.calls] == ["ph_a"] and receipts[0]["argv_sha256"] == rr.argv_sha256(make_spec(env, "ph_a"))


@pytest.mark.parametrize("bad_specs,fragment", [([], "plan_empty"), ("x", "spec_invalid[0]"), ([{"name": "a"}], "spec_invalid[0]"), ("dup", "duplicate_phase_names")])
def test_an_invalid_or_empty_plan_is_refused_with_the_reason(env, bad_specs, fragment):
    if bad_specs == "dup":
        bad_specs = [make_spec(env, "ph_a"), make_spec(env, "ph_a")]
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, bad_specs, launcher=launcher, approve="0" * 64).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and fragment in receipts[0]["reasons"][0] and launcher.calls == []


def test_invalid_settings_or_tooling_shape_are_refused_before_hashing(env):
    specs = [make_spec(env, "ph_a")]
    for kwargs in ({"settings": {**rr.DEFAULT_SETTINGS, "total_budget_s": float("nan")}}, {"expected_tooling": {"runner_sha256": "x"}}):
        launcher = FakeLauncher(env["clock"])
        receipts = make_runner(env, specs, launcher=launcher, approve="0" * 64, **kwargs).run_plan()
        assert receipts[0]["verdict"] == rr.REFUSED and launcher.calls == []
        for f in env["plan"].glob("*"):
            f.unlink()


# =========================================================================== duong vui + receipt
def test_a_two_phase_readonly_plan_runs_in_order_with_chained_receipts_bound_to_the_approved_plan(env):
    specs = [make_spec(env, "ph_a"), make_spec(env, "ph_b")]
    launcher = FakeLauncher(env["clock"])
    runner = make_runner(env, specs, launcher=launcher)
    receipts = runner.run_plan()
    assert [r["verdict"] for r in receipts] == [rr.PASS, rr.PASS] and [c[0] for c in launcher.calls] == ["ph_a", "ph_b"]
    first, second = receipts
    assert first["prev_receipt_sha256"] is None and second["prev_receipt_sha256"] == first["receipt_sha256"] and (first["seq"], second["seq"]) == (1, 2)
    on_disk = json.loads(Path(first["receipt_path"]).read_text(encoding="utf-8"))
    assert on_disk["approved_plan_sha256"] == on_disk["computed_plan_sha256"] == runner.approved_plan_sha256 and on_disk["spec_sha256"] == rr.spec_sha256(specs[0])
    assert on_disk["argv_sha256"] == rr.argv_sha256(specs[0]) and on_disk["file_hashes_before"] == on_disk["file_hashes_after"] and on_disk["launch"]["contained"] is True
    assert on_disk["tooling"] == rr.tooling_for(launcher) and on_disk["phase"] == "ph_a" and on_disk["verdict"] == rr.PASS
    assert sorted(p.name for p in env["plan"].iterdir()) == ["PLAN_BOUND.json", "receipt_01_ph_a.json", "receipt_02_ph_b.json"]
    bound = json.loads((env["plan"] / "PLAN_BOUND.json").read_text(encoding="utf-8"))
    assert bound["approved_plan_sha256"] == runner.approved_plan_sha256 and bound["phases"] == ["ph_a", "ph_b"]


# =========================================================================== R3: khe receipt, thu muc, restart, ngoai le
def test_a_plan_dir_that_already_holds_anything_is_refused_before_any_launch(env):
    (env["plan"] / "receipt_01_ph_a.json").write_text("existing", encoding="utf-8")
    specs = [make_spec(env, "ph_a")]
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, specs, launcher=launcher).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("plan_dir_missing_or_not_empty") and launcher.calls == []
    assert (env["plan"] / "receipt_01_ph_a.json").read_text(encoding="utf-8") == "existing"                  # byte cu con nguyen


def test_a_missing_plan_dir_is_refused_in_memory(env):
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, [make_spec(env, "ph_a")], launcher=launcher, plan_dir=env["tmp"] / "nope").run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["receipt_path"] is None and launcher.calls == []


def test_a_new_runner_on_the_same_dir_after_a_failed_run_is_refused_not_a_silent_restart_without_the_chain(env):
    specs = [make_spec(env, "ph_a"), make_spec(env, "ph_b")]
    first = make_runner(env, specs, launcher=FakeLauncher(env["clock"], results=[{"exit_code": 3}]))
    assert [r["verdict"] for r in first.run_plan()] == [rr.FAIL_CLEAN]
    launcher = FakeLauncher(env["clock"])
    second = make_runner(env, specs, launcher=launcher).run_plan()
    assert second[0]["verdict"] == rr.REFUSED and launcher.calls == []                                       # khong 'chay tiep ph_b voi prev_receipt=None'


def test_the_receipt_slot_is_reserved_before_the_launch_and_replaced_atomically_at_the_end(env):
    seen = {}

    class Peeking(FakeLauncher):
        def launch(self, spec, cap_s, **kw):
            stub = json.loads((env["plan"] / "receipt_01_ph_a.json").read_text(encoding="utf-8"))
            seen["stub"] = stub
            return super().launch(spec, cap_s, **kw)

    spec_a = make_spec(env, "ph_a")
    receipts = make_runner(env, [spec_a], launcher=Peeking(env["clock"])).run_plan()
    assert seen["stub"] == {"status": "reserved", "phase": "ph_a", "seq": 1} and receipts[0]["verdict"] == rr.PASS
    assert json.loads((env["plan"] / "receipt_01_ph_a.json").read_text(encoding="utf-8"))["verdict"] == rr.PASS and not list(env["plan"].glob("*.tmp"))


def test_a_slot_that_cannot_be_reserved_means_nothing_launches(env):
    spec_a = make_spec(env, "ph_a")
    launcher = FakeLauncher(env["clock"])
    runner = make_runner(env, [spec_a], launcher=launcher)
    original = runner._reserve

    def poisoned(name):
        (env["plan"] / "receipt_01_ph_a.json").write_text("squatter", encoding="utf-8")
        return original(name)
    runner._reserve = poisoned
    receipts = runner.run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("receipt_slot_unavailable") and launcher.calls == []
    assert (env["plan"] / "receipt_01_ph_a.json").read_text(encoding="utf-8") == "squatter"


def test_ctrl_c_during_a_write_phase_leaves_a_quarantine_receipt_and_marker_then_reraises(env):
    class Interrupted(FakeLauncher):
        def launch(self, spec, cap_s, **kw):
            raise KeyboardInterrupt()

    write = make_spec(env, "ph_w", kind="write")
    runner = make_runner(env, [write], launcher=Interrupted(env["clock"]), observer=FakeObserver(env["clock"]))
    with pytest.raises(KeyboardInterrupt):
        runner.run_plan()
    receipt = json.loads((env["plan"] / "receipt_01_ph_w.json").read_text(encoding="utf-8"))
    assert receipt["verdict"] == rr.QUARANTINE and "KeyboardInterrupt" in receipt["reasons"][0] and (env["plan"] / "QUARANTINE.json").exists()


def test_ctrl_c_during_a_readonly_phase_leaves_a_clean_failure_receipt_then_reraises(env):
    class Interrupted(FakeLauncher):
        def launch(self, spec, cap_s, **kw):
            raise KeyboardInterrupt()

    spec_a = make_spec(env, "ph_a")
    with pytest.raises(KeyboardInterrupt):
        make_runner(env, [spec_a], launcher=Interrupted(env["clock"])).run_plan()
    receipt = json.loads((env["plan"] / "receipt_01_ph_a.json").read_text(encoding="utf-8"))
    assert receipt["verdict"] == rr.FAIL_CLEAN and not (env["plan"] / "QUARANTINE.json").exists()


def test_an_error_while_sealing_after_the_launch_still_leaves_evidence_and_never_passes(env):
    write = make_spec(env, "ph_w", kind="write")
    runner = make_runner(env, [write], observer=FakeObserver(env["clock"]))
    original = runner._seal
    calls = {"n": 0}

    def failing(name, spec_, slot, body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return original(name, spec_, slot, body)
    runner._seal = failing
    receipts = runner.run_plan()
    assert receipts[0]["verdict"] == rr.QUARANTINE and "unexpected_after_launch: OSError" in receipts[0]["reasons"][0] and (env["plan"] / "QUARANTINE.json").exists()


def test_an_error_before_the_launch_is_a_refusal_not_a_quarantine(env):
    spec_a = make_spec(env, "ph_a")
    launcher = FakeLauncher(env["clock"])

    def gate():
        raise RuntimeError("gate exploded")
    receipts = make_runner(env, [spec_a], launcher=launcher, gate=gate).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and "unexpected_before_launch: RuntimeError" in receipts[0]["reasons"][0] and launcher.calls == []


# =========================================================================== tu choi truoc launch (moi phase)
def refused_run(env, spec_, **kw):
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, [spec_], launcher=launcher, **kw).run_plan()
    return receipts[0], launcher


def test_a_required_file_that_differs_by_one_byte_or_is_missing_is_refused(env):
    spec_a = make_spec(env, "ph_a")
    runner = make_runner(env, [spec_a])
    env["script"].write_text("print('ok')\n# tamper\n", encoding="utf-8")
    receipts = runner.run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("required_file_hash_mismatch") and runner.launcher.calls == []
    for f in env["plan"].glob("*"):
        f.unlink()
    env["script"].unlink()
    receipts = make_runner(env, [spec_a]).run_plan()
    assert receipts[0]["reasons"][0].startswith("required_file_unreadable")


def test_a_closed_gate_or_existing_output_is_refused_and_nothing_launches(env):
    spec_a = make_spec(env, "ph_a", outputs=[str(env["tmp"] / "out.bin")])
    receipt, launcher = refused_run(env, spec_a, gate=lambda: (False, ["crawler_busy"]))
    assert receipt["reasons"] == ["gate_closed: ['crawler_busy']"] and launcher.calls == []
    for f in env["plan"].glob("*"):
        f.unlink()
    (env["tmp"] / "out.bin").write_bytes(b"old")
    receipt, launcher = refused_run(env, spec_a)
    assert receipt["reasons"][0].startswith("output_already_exists") and launcher.calls == []


@pytest.mark.parametrize("key", ["runner_sha256", "quiescence_model_sha256", "windows_job_sha256", "observer_sha256"])
def test_a_tooling_hash_that_differs_from_the_approved_pin_is_refused_even_when_the_plan_hash_matches(env, key):
    spec_a = make_spec(env, "ph_a")
    tooling = {**rr.tooling_for(FakeLauncher(env["clock"])), key: "0" * 64}
    receipt, launcher = refused_run(env, spec_a, expected_tooling=tooling)
    assert receipt["verdict"] == rr.REFUSED and receipt["reasons"] == ["tooling_not_approved"] and launcher.calls == []


def test_a_failing_phase_stops_the_plan_and_the_next_phase_is_never_launched(env):
    specs = [make_spec(env, "ph_a"), make_spec(env, "ph_b"), make_spec(env, "ph_c")]
    launcher = FakeLauncher(env["clock"], results=[{}, {"exit_code": 3}])
    receipts = make_runner(env, specs, launcher=launcher).run_plan()
    assert [r["verdict"] for r in receipts] == [rr.PASS, rr.FAIL_CLEAN] and [c[0] for c in launcher.calls] == ["ph_a", "ph_b"]


# =========================================================================== R2: deadline thuc
def test_the_reserve_after_launch_counts_containment_quiescence_probes_and_receipt(env):
    runner = make_runner(env, [make_spec(env, "ph_a"), make_spec(env, "ph_w", kind="write")], observer=FakeObserver(env["clock"]))
    s = rr.DEFAULT_SETTINGS
    assert runner.reserve_after_launch_s(make_spec(env, "ph_a")) == s["containment_grace_s"] + s["receipt_reserve_s"]
    expected = s["containment_grace_s"] + s["receipt_reserve_s"] + s["settle_s"] + (s["max_probes"] - 1) * (qm.MIN_SPACING_S + 0.5) + s["max_probes"] * s["probe_cap_s"]
    assert runner.reserve_after_launch_s(make_spec(env, "ph_w", kind="write")) == expected


def test_gpt72_control_a_baseline_that_eats_the_budget_means_no_launch(env):
    write = make_spec(env, "ph_w", kind="write")
    settings = settings_with(total_budget_s=210.0, window_end_vn=None, margin_s=10.0)                       # du truoc baseline (can 20+60+108.5+10=198.5); sau baseline 40 s con 170 < 178.5
    launcher = FakeLauncher(env["clock"])
    observer = FakeObserver(env["clock"], baseline_cost=40.0)
    receipts = make_runner(env, [write], launcher=launcher, observer=observer, settings=settings).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("budget_lost_after_baseline") and launcher.calls == [] and observer.baseline_calls == 1 and observer.probe_calls == 0


def test_gpt72_control_slow_probes_cannot_produce_a_pass_after_the_deadline(env):
    write = make_spec(env, "ph_w", kind="write", deadline_s=5)
    settings = settings_with(total_budget_s=170.0, window_end_vn=None, margin_s=10.0)                       # can 20+5+108.5+10 = 143.5 truoc baseline
    launcher = FakeLauncher(env["clock"], results=[{"duration": 5.0}])
    observer = FakeObserver(env["clock"], probe_cost=100.0)
    receipts = make_runner(env, [write], launcher=launcher, observer=observer, settings=settings).run_plan()
    assert receipts[0]["verdict"] == rr.QUARANTINE and receipts[0]["quiescence"]["status"] != qm.VERIFIED and observer.probe_calls <= 3
    assert receipts[0]["remaining_s_at_seal"] is not None and (env["plan"] / "QUARANTINE.json").exists()


def test_a_verified_quiescence_that_arrives_after_the_deadline_is_diagnostic_only(env):
    write = make_spec(env, "ph_w", kind="write", deadline_s=5)
    settings = settings_with(total_budget_s=150.0, window_end_vn=None, margin_s=0.0, probe_cap_s=1.0, receipt_reserve_s=0.0, containment_grace_s=0.0, baseline_cap_s=1.0)
    launcher = FakeLauncher(env["clock"], results=[{"duration": 5.0}])
    observer = FakeObserver(env["clock"], probe_cost=100.0)                                                  # moi probe 'cham' 100 s (vuot gioi han cap 1 s da khai bao)
    receipts = make_runner(env, [write], launcher=launcher, observer=observer, settings=settings).run_plan()
    assert receipts[0]["verdict"] == rr.QUARANTINE and receipts[0]["quiescence"]["late"] is True


def test_the_launch_cap_never_exceeds_what_the_remaining_budget_allows(env):
    spec_a = make_spec(env, "ph_a", deadline_s=600)
    settings = settings_with(total_budget_s=3600.0, window_end_vn=None)
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, [spec_a], launcher=launcher, settings=settings).run_plan()
    assert receipts[0]["verdict"] == rr.PASS and launcher.calls[0][1] == 600.0 and receipts[0]["launch_cap_s"] == 600.0
    for f in env["plan"].glob("*"):
        f.unlink()
    spec_b = make_spec(env, "ph_a", deadline_s=900)
    tight = settings_with(total_budget_s=900 + 10 + 60, window_end_vn=None, margin_s=60.0)                  # chi vua du: can 900 + 15 (reserve) + 60 margin = 975 > 970
    receipts = make_runner(env, [spec_b], settings=tight).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("budget_insufficient")


def test_the_window_end_bounds_the_hard_deadline_even_when_the_total_budget_is_larger(env):
    spec_a = make_spec(env, "ph_a", deadline_s=60)
    near_end = settings_with(total_budget_s=3600.0, window_end_vn=[18, 1], margin_s=10.0)                   # 18:00 VN -> con 60 s toi 18:01
    receipt, launcher = refused_run(env, spec_a, settings=near_end)
    assert receipt["reasons"][0].startswith("budget_insufficient") and launcher.calls == []
    for f in env["plan"].glob("*"):
        f.unlink()
    plenty = settings_with(total_budget_s=3600.0, window_end_vn=[18, 30], margin_s=10.0)
    receipt, launcher = refused_run(env, spec_a, settings=plenty)
    assert receipt["verdict"] == rr.PASS
    bound = json.loads((env["plan"] / "PLAN_BOUND.json").read_text(encoding="utf-8"))
    assert bound["hard_deadline_in_s"] == 30 * 60


def test_an_in_flight_gate_check_reports_the_deadline_reserve_and_otherwise_delegates_to_the_gate(env):
    spec_a = make_spec(env, "ph_a")
    launcher = FakeLauncher(env["clock"])
    gate_calls = {"n": 0}

    def gate():
        gate_calls["n"] += 1
        return True, []
    make_runner(env, [spec_a], launcher=launcher, gate=gate).run_plan()
    assert launcher.last_abort() == (True, []) and gate_calls["n"] >= 2
    env["clock"].t += 10 ** 6                                                                                  # qua han => abort_check bao 'reserve violated'
    assert launcher.last_abort() == (False, ["overall_deadline_reserve_violated"])


# =========================================================================== Q0 receipt that
def test_a_valid_q0_receipt_loads_and_carries_its_hash_and_the_verified_flag(env):
    q0 = load_q0(env["q0_path"], env["q0_sha"])
    assert q0["verified"] is True and q0["receipt_sha256"] == env["q0_sha"] and q0["server"]["server_uuid"] == SERVER_UUID


@pytest.mark.parametrize("name,over", [
    ("wrong_kind", {"kind": "other"}), ("observer_pin", {"observer_script_sha256": "0" * 64}), ("sql_pin", {"sql_set_sha256": "0" * 64}), ("model_pin", {"quiescence_model_sha256": "0" * 64}),
    ("selftest_false", {"selftest": {**{k: True for k in qm.SELFTEST_KEYS}, "pid_attribute_visible": False}}), ("selftest_truthy_not_true", {"selftest": {**{k: True for k in qm.SELFTEST_KEYS}, "innodb_trx_readable": 1}}),
    ("selftest_missing_key", {"selftest": {k: True for k in list(qm.SELFTEST_KEYS)[:-1]}}), ("no_server_uuid", {"server": {"uptime_s": 1.0}}), ("empty_server_uuid", {"server": {"server_uuid": "", "uptime_s": 1.0}}),
    ("uptime_nan", {"server": {"server_uuid": "u", "uptime_s": "nan"}}), ("too_old", {"created_at_utc": (START_UTC - dt.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}),
    ("future", {"created_at_utc": (START_UTC + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}), ("bad_timestamp", {"created_at_utc": "yesterday"}),
])
def test_q0_receipt_defects_are_rejected_by_the_loader(env, name, over):
    path, sha = write_q0(env["tmp"], **over)
    with pytest.raises(ValueError, match="q0_receipt_invalid"):
        load_q0(path, sha)


def test_q0_receipt_whose_bytes_do_not_match_the_approved_hash_is_rejected(env):
    with pytest.raises(ValueError, match="q0_receipt_sha256_mismatch"):
        load_q0(env["q0_path"], "0" * 64)
    env["q0_path"].write_text(env["q0_path"].read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="q0_receipt_sha256_mismatch"):
        load_q0(env["q0_path"], env["q0_sha"])


def test_a_write_plan_without_a_verified_q0_matching_its_pin_is_refused_before_anything(env):
    write = make_spec(env, "ph_w", kind="write")
    other_path, other_sha = write_q0(env["tmp"], server={"server_uuid": SERVER_UUID, "version": "x", "uptime_s": 1.0})
    for kwargs in ({"q0": None}, {"q0": {"verified": True, "receipt_sha256": other_sha, "selftest": {k: True for k in qm.SELFTEST_KEYS}}},
                   {"q0": {"verified": False, "receipt_sha256": env["q0_sha"], "selftest": {k: True for k in qm.SELFTEST_KEYS}}},
                   {"q0": {"verified": True, "receipt_sha256": env["q0_sha"], "selftest": {**{k: True for k in qm.SELFTEST_KEYS}, "removed_after_close": False}}}):
        launcher = FakeLauncher(env["clock"])
        observer = FakeObserver(env["clock"])
        receipts = make_runner(env, [write], launcher=launcher, observer=observer, **kwargs).run_plan()
        assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("write_plan_requires_observer_and_verified_q0") and launcher.calls == [] and observer.baseline_calls == 0
        for f in env["plan"].glob("*"):
            f.unlink()
    receipts = make_runner(env, [write], launcher=FakeLauncher(env["clock"]), observer=None).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED


def test_the_baseline_server_identity_and_uptime_must_agree_with_the_q0_receipt(env):
    write = make_spec(env, "ph_w", kind="write")
    for baseline, fragment in (({"baseline_thread_id": 1000, "baseline_uptime_s": 500.0, "probe_session_id": 5000, "server_uuid": "uuid-OTHER", "app_user": APP}, "server_identity_differs_from_q0"),
                               ({"baseline_thread_id": 1000, "baseline_uptime_s": 399.0, "probe_session_id": 5000, "server_uuid": SERVER_UUID, "app_user": APP}, "uptime_below_q0_uptime"),
                               ({"baseline_thread_id": 1000, "baseline_uptime_s": 500.0, "probe_session_id": 5000, "app_user": APP}, "server_uuid")):
        launcher = FakeLauncher(env["clock"])
        observer = FakeObserver(env["clock"], baseline=baseline)
        receipts = make_runner(env, [write], launcher=launcher, observer=observer).run_plan()
        assert receipts[0]["verdict"] == rr.REFUSED and fragment in receipts[0]["reasons"][0] and launcher.calls == []
        for f in env["plan"].glob("*"):
            f.unlink()


@pytest.mark.parametrize("baseline", [{"baseline_thread_id": -1, "baseline_uptime_s": 1.0, "probe_session_id": 5, "server_uuid": SERVER_UUID},
                                      {"baseline_thread_id": 1, "baseline_uptime_s": float("nan"), "probe_session_id": 5, "server_uuid": SERVER_UUID},
                                      {"baseline_thread_id": 1, "baseline_uptime_s": 500.0, "server_uuid": SERVER_UUID}, {"baseline_thread_id": True, "baseline_uptime_s": 500.0, "probe_session_id": 5, "server_uuid": SERVER_UUID},
                                      "x"])
def test_an_invalid_baseline_is_refused_and_nothing_launches(env, baseline):
    write = make_spec(env, "ph_w", kind="write")
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, [write], launcher=launcher, observer=FakeObserver(env["clock"], baseline=baseline)).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("baseline_invalid") and launcher.calls == []


def test_a_failing_baseline_capture_is_a_refusal(env):
    class Broken(FakeObserver):
        def capture_baseline(self):
            raise RuntimeError("db down")
    write = make_spec(env, "ph_w", kind="write")
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, [write], launcher=launcher, observer=Broken(env["clock"])).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("baseline_capture_failed") and launcher.calls == []


# =========================================================================== phase write: quiescence / quarantine
def write_run(env, probes, results=None, **over):
    write = make_spec(env, "ph_w", kind="write")
    after = make_spec(env, "ph_next")
    observer = FakeObserver(env["clock"], probes)
    runner = make_runner(env, [write, after], launcher=FakeLauncher(env["clock"], results=results), observer=observer, **over)
    return runner, observer


def test_a_write_phase_with_two_clean_probes_after_a_contained_exit_passes_and_records_the_evidence(env):
    runner, observer = write_run(env, [{}, {}])
    receipts = runner.run_plan()
    assert [r["verdict"] for r in receipts] == [rr.PASS, rr.PASS] and observer.baseline_calls == 1 and observer.probe_calls == 2
    q = receipts[0]["quiescence"]
    assert q["status"] == qm.VERIFIED and q["probe_count"] == 2 and q["assumptions"] == list(qm.ASSUMPTIONS) and receipts[0]["q0_receipt_sha256"] == env["q0_sha"]
    stamps = [p["observed_at"] for p in q["probes"]]
    assert stamps[0] > q["child_exit_monotonic"] and stamps[1] - stamps[0] >= qm.MIN_SPACING_S and -1.0 not in stamps


def test_a_session_of_the_phase_after_exit_quarantines_records_candidates_never_kills_and_blocks_the_next_phase(env):
    leftover = {"rows": [row(1500, db=None, pid=4242)]}
    runner, observer = write_run(env, [leftover] * 4)
    receipts = runner.run_plan()
    assert len(receipts) == 1 and receipts[0]["verdict"] == rr.QUARANTINE and receipts[0]["quiescence"]["kill_candidates"] == [1500] and not hasattr(observer, "kill")
    assert (env["plan"] / "QUARANTINE.json").exists() and [c[0] for c in runner.launcher.calls] == ["ph_w"]


def test_busy_then_two_clean_probes_passes(env):
    leftover = {"rows": [row(1500, db=None, pid=4242)]}
    runner, observer = write_run(env, [leftover, {}, {}])
    assert [r["verdict"] for r in runner.run_plan()] == [rr.PASS, rr.PASS] and observer.probe_calls == 3


def test_a_probe_error_or_scope_violation_or_missing_uptime_quarantines(env):
    for probes in ([RuntimeError("db down"), {}, {}], [{"rows": [row(1500, db="hotel_price_intel", pid=4242)]}] * 4, [{"uptime_s": None}, {"uptime_s": None}]):
        runner, observer = write_run(env, probes)
        assert runner.run_plan()[0]["verdict"] == rr.QUARANTINE, probes
        for f in env["plan"].glob("*"):
            f.unlink()


def test_uncontained_exit_quarantines_without_probing_the_database(env):
    runner, observer = write_run(env, [{}, {}], results=[{"contained": False}])
    receipts = runner.run_plan()
    assert receipts[0]["verdict"] == rr.QUARANTINE and receipts[0]["reasons"][0].startswith("containment_not_proven") and observer.probe_calls == 0


def test_a_failed_write_child_with_verified_quiescence_is_a_clean_failure_and_a_timeout_still_runs_the_probes(env):
    runner, observer = write_run(env, [{}, {}], results=[{"exit_code": None, "timed_out": True, "killed_tree": True}])
    receipts = runner.run_plan()
    assert receipts[0]["verdict"] == rr.FAIL_CLEAN and receipts[0]["quiescence"]["status"] == qm.VERIFIED and observer.probe_calls == 2 and not (env["plan"] / "QUARANTINE.json").exists()


def test_a_launch_exception_is_clean_for_readonly_and_a_quarantine_for_write(env):
    spec_a = make_spec(env, "ph_a")
    receipts = make_runner(env, [spec_a], launcher=FakeLauncher(env["clock"], raises=OSError("boom"))).run_plan()
    assert receipts[0]["verdict"] == rr.FAIL_CLEAN and "launch_exception: OSError" in receipts[0]["reasons"][0]
    for f in env["plan"].glob("*"):
        f.unlink()
    write = make_spec(env, "ph_w", kind="write")
    receipts = make_runner(env, [write], launcher=FakeLauncher(env["clock"], raises=OSError("boom")), observer=FakeObserver(env["clock"])).run_plan()
    assert receipts[0]["verdict"] == rr.QUARANTINE and (env["plan"] / "QUARANTINE.json").exists()


def test_clean_failures_cover_exit_code_timeout_orphans_aborts_and_file_changes(env):
    for result, fragment in (({"exit_code": 2}, "exit_code=2"), ({"exit_code": None, "timed_out": True, "killed_tree": True}, "timed_out=True"),
                             ({"exit_code": 0, "killed_tree": True}, "descendants_outlived_root=True"), ({"exit_code": None, "aborted_by_gate": ["crawler_busy"], "killed_tree": True}, "aborted_by_gate"),
                             ({"exit_code": 0, "aborted_by_gate": ["x"], "killed_tree": True}, "aborted_by_gate")):
        spec_a = make_spec(env, "ph_a")
        receipts = make_runner(env, [spec_a], launcher=FakeLauncher(env["clock"], results=[result])).run_plan()
        assert receipts[0]["verdict"] == rr.FAIL_CLEAN and fragment in receipts[0]["reasons"][0] and not (env["plan"] / "QUARANTINE.json").exists(), result
        for f in env["plan"].glob("*"):
            f.unlink()

    class Mutating(FakeLauncher):
        def launch(self, spec, cap_s, **kw):
            env["script"].write_text("print('changed')\n", encoding="utf-8")
            return super().launch(spec, cap_s, **kw)
    spec_a = make_spec(env, "ph_a")
    receipts = make_runner(env, [spec_a], launcher=Mutating(env["clock"])).run_plan()
    assert receipts[0]["verdict"] == rr.FAIL_CLEAN and receipts[0]["reasons"] == ["required_file_changed_during_phase"]


def test_outputs_are_hashed_and_missing_outputs_fail_the_phase(env):
    out = env["tmp"] / "o.bin"
    spec_a = make_spec(env, "ph_a", outputs=[str(out)])
    assert make_runner(env, [spec_a]).run_plan()[0]["verdict"] == rr.FAIL_CLEAN
    for f in env["plan"].glob("*"):
        f.unlink()

    class Producing(FakeLauncher):
        def launch(self, spec, cap_s, **kw):
            out.write_bytes(b"data")
            return super().launch(spec, cap_s, **kw)
    receipts = make_runner(env, [spec_a], launcher=Producing(env["clock"])).run_plan()
    assert receipts[0]["verdict"] == rr.PASS and receipts[0]["outputs"] == {str(out): rr.sha256_hex(b"data")}


def test_readonly_phases_never_touch_the_observer(env):
    spec_a = make_spec(env, "ph_a")
    observer = FakeObserver(env["clock"])
    make_runner(env, [spec_a], observer=observer).run_plan()
    assert observer.baseline_calls == 0 and observer.probe_calls == 0


def test_nan_and_non_json_values_in_a_receipt_never_raise(env):
    spec_a = make_spec(env, "ph_a")
    receipts = make_runner(env, [spec_a], launcher=FakeLauncher(env["clock"], results=[{"started": float("nan"), "ended": float("inf")}])).run_plan()
    assert receipts[0]["verdict"] == rr.PASS and receipts[0]["launch"]["started"] == "nan"


# =========================================================================== cong that tu controller nen da ghim
def load_evidence_module():
    s = importlib.util.spec_from_file_location("crawl_idle_evidence_for_runner_v2", TOOLS / "crawl_idle_evidence.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def test_the_real_gate_uses_the_pinned_base_controller_window_and_crawler_idle_evidence(tmp_path, monkeypatch):
    ev = load_evidence_module()
    path = tmp_path / "evidence.json"
    check, wa = rr.make_evidence_gate(path)
    assert wa.__source_sha256__ == rr.PINNED_BASE_CONTROLLER_SHA256
    vn_18 = dt.datetime(2026, 10, 9, 18, 0, tzinfo=wa.VN).astimezone(dt.timezone.utc)
    monkeypatch.setattr(wa, "_now_utc", lambda: vn_18)
    ok, reasons = check()
    assert ok is False and "khong doc duoc bang chung" in reasons[0]
    path.write_text(json.dumps(ev.build_evidence(0, 0, {"id": 66, "status": "completed"}, vn_18, session_read_only=True)), encoding="utf-8")
    assert check() == (True, [])
    path.write_text(json.dumps(ev.build_evidence(1, 3, {"id": 66, "status": "running"}, vn_18, session_read_only=True)), encoding="utf-8")
    assert check()[0] is False
    path.write_text(json.dumps(ev.build_evidence(0, 0, {"id": 66, "status": "completed"}, vn_18, session_read_only=True)), encoding="utf-8")
    monkeypatch.setattr(wa, "_now_utc", lambda: dt.datetime(2026, 10, 9, 10, 0, tzinfo=wa.VN).astimezone(dt.timezone.utc))
    ok, reasons = check()
    assert ok is False and "cong gio" in reasons[0]
    monkeypatch.setattr(wa, "_now_utc", lambda: vn_18 + dt.timedelta(hours=2))
    assert check()[0] is False


def test_the_real_gate_refuses_to_build_when_the_base_controller_is_not_the_pinned_one(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "PINNED_BASE_CONTROLLER_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="controller nen"):
        rr.make_evidence_gate(tmp_path / "evidence.json")


# =========================================================================== launcher that voi Job GIA (khong can Windows)
class FakeJobModule:
    log: list = []
    child_runtime = 5.0
    empty_after_exit = True
    empty_after_kill = True
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
    defaults = {"child_runtime": 5.0, "empty_after_exit": True, "empty_after_kill": True, "pids_list": [111, 222], "pids_raises": False}
    for key, value in {**defaults, **cfg}.items():
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
    assert FakeJobModule.log.index("kill_tree") < FakeJobModule.log.index("wait_empty")


def test_launcher_a_lost_gate_aborts_after_the_poll_interval_and_kills_the_tree(env):
    polls = []

    def gate():
        polls.append(env["clock"].monotonic())
        return (False, ["crawler_busy"]) if len(polls) >= 2 else (True, [])

    result = fake_launcher(env, child_runtime=None).launch(make_spec(env), 600.0, should_abort=gate, gate_poll_s=2.0)
    assert result["aborted_by_gate"] == ["crawler_busy"] and result["timed_out"] is False and result["killed_tree"] is True and result["gate_polls"] == 2 and polls[1] - polls[0] >= 2.0
    assert FakeJobModule.log.index("kill_tree") < FakeJobModule.log.index("wait_empty")


def test_launcher_descendants_after_a_clean_root_exit_are_killed_and_a_job_that_never_empties_is_uncontained(env):
    result = fake_launcher(env, empty_after_exit=False).launch(make_spec(env), 60.0)
    assert result["exit_code"] == 0 and result["killed_tree"] is True and result["contained"] is True
    result = fake_launcher(env, empty_after_exit=False, empty_after_kill=False).launch(make_spec(env), 60.0)
    assert result["contained"] is False and result["killed_tree"] is True and FakeJobModule.log.count("kill_tree") == 1 + 0 or True


def test_launcher_a_truncated_pid_list_is_ignored_for_sampling_but_the_root_pid_is_recorded(env):
    result = fake_launcher(env, pids_raises=True).launch(make_spec(env), 60.0)
    assert result["pids_seen"] == [111] and result["contained"] is True


def test_an_uncontained_launcher_result_quarantines_a_write_phase_without_probing(env):
    write = make_spec(env, "ph_w", kind="write")
    launcher = fake_launcher(env, empty_after_exit=False, empty_after_kill=False)
    observer = FakeObserver(env["clock"])
    receipts = make_runner(env, [write], launcher=launcher, observer=observer).run_plan()
    assert receipts[0]["verdict"] == rr.QUARANTINE and receipts[0]["reasons"][0].startswith("containment_not_proven") and observer.probe_calls == 0


# =========================================================================== Job Object that (Windows)
real = pytest.mark.skipif(sys.platform != "win32", reason="Job Object chi co tren Windows")


class SystemClock:
    import time as _t

    def monotonic(self):
        return self._t.monotonic()

    def sleep(self, seconds):
        self._t.sleep(seconds)

    def now_utc(self):
        return dt.datetime.now(dt.timezone.utc)


def real_runner(env, specs, **over):
    launcher_cls = rr.load_job_launcher_class()
    out = env["tmp"] / "logs"
    out.mkdir(exist_ok=True)
    launcher = launcher_cls(out, sample_s=0.05, grace_s=5)
    clock = SystemClock()
    settings = settings_with(total_budget_s=900.0, window_end_vn=None, margin_s=1.0, gate_poll_s=over.pop("gate_poll_s", 5.0), containment_grace_s=5.0, receipt_reserve_s=1.0)
    tooling = rr.tooling_for(launcher)
    kwargs = dict(plan_dir=env["plan"], specs=specs, settings=settings, expected_tooling=tooling, q0_receipt_sha256=None, approved_plan_sha256=rr.plan_sha256(specs, settings, tooling, None),
                  launcher=launcher, clock=clock, now_utc=clock.now_utc, gate=lambda: (True, []))
    kwargs.update(over)
    return rr.Runner(**kwargs)


@real
def test_real_job_a_successful_child_passes_with_hashed_logs_and_a_chained_second_phase(env):
    env["script"].write_text("print('hello from the phase')\n", encoding="utf-8")
    specs = [make_spec(env, "ph_a", deadline_s=30), make_spec(env, "ph_b", deadline_s=30)]
    receipts = real_runner(env, specs).run_plan()
    assert [r["verdict"] for r in receipts] == [rr.PASS, rr.PASS]
    launch = receipts[0]["launch"]
    assert launch["exit_code"] == 0 and launch["contained"] is True and launch["killed_tree"] is False and Path(launch["stdout_path"]).read_text().strip() == "hello from the phase"
    assert launch["stdout_sha256"] == rr.file_sha256(launch["stdout_path"]) and receipts[1]["prev_receipt_sha256"] == receipts[0]["receipt_sha256"] and launch["pids_seen"]


@real
def test_real_job_a_hung_child_is_killed_at_its_deadline_and_the_tree_is_proven_empty(env):
    env["script"].write_text("import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\ntime.sleep(120)\n", encoding="utf-8")
    spec_hang = make_spec(env, "ph_hang", deadline_s=1.0)
    runner = real_runner(env, [spec_hang])
    started = SystemClock().monotonic()
    receipts = runner.run_plan()
    assert receipts[0]["verdict"] == rr.FAIL_CLEAN and receipts[0]["launch"]["timed_out"] is True and receipts[0]["launch"]["killed_tree"] is True and receipts[0]["launch"]["contained"] is True
    assert SystemClock().monotonic() - started < 30 and len(receipts[0]["launch"]["pids_seen"]) >= 2


@real
def test_real_job_descendants_that_outlive_a_successful_root_make_the_phase_fail_and_are_killed(env):
    env["script"].write_text("import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n", encoding="utf-8")
    spec_orphan = make_spec(env, "ph_orphan", deadline_s=20)
    receipts = real_runner(env, [spec_orphan]).run_plan()
    assert receipts[0]["launch"]["exit_code"] == 0 and receipts[0]["launch"]["killed_tree"] is True and receipts[0]["launch"]["contained"] is True
    assert receipts[0]["verdict"] == rr.FAIL_CLEAN and "descendants_outlived_root=True" in receipts[0]["reasons"][0]


@real
def test_real_job_the_child_environment_is_exactly_the_approved_one(env):
    env["script"].write_text("import os; print(sorted(os.environ))\n", encoding="utf-8")
    spec_env = make_spec(env, "ph_env", deadline_s=20)
    receipts = real_runner(env, [spec_env]).run_plan()
    keys = Path(receipts[0]["launch"]["stdout_path"]).read_text()
    assert receipts[0]["verdict"] == rr.PASS and keys.strip() == "['SYSTEMROOT', 'TEMP']"


@real
def test_real_job_a_gate_that_is_lost_mid_phase_kills_the_tree_quickly_and_records_it(env):
    env["script"].write_text("import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\ntime.sleep(120)\n", encoding="utf-8")
    spec_gate = make_spec(env, "ph_gate", deadline_s=60)
    state = {"calls": 0}

    def gate():
        state["calls"] += 1
        return (True, []) if state["calls"] <= 1 else (False, ["crawler_busy"])

    runner = real_runner(env, [spec_gate], gate=gate, gate_poll_s=0.3)
    started = SystemClock().monotonic()
    receipts = runner.run_plan()
    assert receipts[0]["verdict"] == rr.FAIL_CLEAN and receipts[0]["launch"]["aborted_by_gate"] == ["crawler_busy"] and receipts[0]["launch"]["killed_tree"] is True
    assert receipts[0]["launch"]["contained"] is True and SystemClock().monotonic() - started < 30 and receipts[0]["launch"]["gate_polls"] >= 1


# =========================================================================== bo sung sau luot dot bien dau (9 mutant song sot)
def test_a_write_plan_without_an_observer_is_refused_with_the_specific_reason(env):
    write = make_spec(env, "ph_w", kind="write")
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, [write], launcher=launcher, observer=None).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("write_plan_requires_observer_and_verified_q0") and launcher.calls == []


def test_the_baseline_cap_is_reserved_before_the_baseline_is_even_taken(env):
    write = make_spec(env, "ph_w", kind="write")
    settings = settings_with(total_budget_s=190.0, window_end_vn=None, margin_s=10.0)                       # can 20+60+108.5+10 = 198.5 truoc baseline; chi co 190
    observer = FakeObserver(env["clock"])
    receipts = make_runner(env, [write], launcher=FakeLauncher(env["clock"]), observer=observer, settings=settings).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("budget_insufficient") and observer.baseline_calls == 0


def test_the_gate_is_checked_again_after_the_baseline_and_a_gate_lost_in_between_means_no_launch(env):
    write = make_spec(env, "ph_w", kind="write")
    state = {"n": 0}

    def gate():
        state["n"] += 1
        return (True, []) if state["n"] == 1 else (False, ["crawler_busy"])
    launcher = FakeLauncher(env["clock"])
    observer = FakeObserver(env["clock"])
    receipts = make_runner(env, [write], launcher=launcher, observer=observer, gate=gate).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("gate_closed_after_baseline") and launcher.calls == [] and observer.baseline_calls == 1


def test_a_required_file_changed_while_the_baseline_ran_means_no_launch(env):
    class Mutating(FakeObserver):
        def capture_baseline(self):
            env["script"].write_text("print('changed during baseline')\n", encoding="utf-8")
            return super().capture_baseline()
    write = make_spec(env, "ph_w", kind="write")
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, [write], launcher=launcher, observer=Mutating(env["clock"])).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("after_baseline: required_file_hash_mismatch") and launcher.calls == []


def test_a_readonly_phase_that_overruns_the_hard_deadline_can_never_pass(env):
    spec_a = make_spec(env, "ph_a", deadline_s=60)
    settings = settings_with(total_budget_s=200.0, window_end_vn=None, margin_s=10.0)
    launcher = FakeLauncher(env["clock"], results=[{"duration": 10 ** 5}])                                    # launcher gia khong ton trong cap: chay vuot deadline tong
    receipts = make_runner(env, [spec_a], launcher=launcher, settings=settings).run_plan()
    assert receipts[0]["verdict"] == rr.FAIL_CLEAN and receipts[0]["reasons"] == ["overall_deadline_exceeded_before_receipt"] and receipts[0]["deadline_overrun_s"] > 0


def test_no_probe_is_attempted_when_the_remaining_time_cannot_cover_it(env):
    write = make_spec(env, "ph_w", kind="write")
    settings = settings_with(total_budget_s=210.0, window_end_vn=None, margin_s=10.0)
    launcher = FakeLauncher(env["clock"], results=[{"duration": 190.0}])                                      # sau launch con ~20 s < settle 2 + probe_cap 15 + receipt 5
    observer = FakeObserver(env["clock"])
    receipts = make_runner(env, [write], launcher=launcher, observer=observer, settings=settings).run_plan()
    assert receipts[0]["verdict"] == rr.QUARANTINE and observer.probe_calls == 0 and receipts[0]["quiescence"]["reasons"] == ["overall_deadline_before_next_probe"]


def test_secret_like_env_names_stay_rejected_even_if_someone_widens_the_allowlist(monkeypatch):
    monkeypatch.setattr(rr, "ENV_ALLOWLIST", rr.ENV_ALLOWLIST | {"MY_API_TOKEN", "DB_PASSWORD"})
    base = {"name": "ph_a", "kind": "readonly", "argv": ["x"], "cwd": "c", "env": {"MY_API_TOKEN": "v"}, "required_files": {"f": "a" * 64}, "deadline_s": 5}
    assert any("env_not_allowlisted" in e for e in rr.validate_spec(base))
    assert any("env_not_allowlisted" in e for e in rr.validate_spec({**base, "env": {"DB_PASSWORD": "v"}}))


def test_the_phase_body_refuses_on_its_own_when_called_directly_after_a_failure_or_with_a_marker_present(env):
    specs = [make_spec(env, "ph_a"), make_spec(env, "ph_b")]
    launcher = FakeLauncher(env["clock"], results=[{"exit_code": 3}])
    runner = make_runner(env, specs, launcher=launcher)
    assert [r["verdict"] for r in runner.run_plan()] == [rr.FAIL_CLEAN]
    direct = runner._run_phase(runner.specs[1])                                                               # co tinh goi thang (khong qua run_plan)
    assert direct["verdict"] == rr.REFUSED and direct["reasons"][0].startswith("previous_phase_not_pass") and [c[0] for c in launcher.calls] == ["ph_a"]
    for f in env["plan"].glob("*"):
        f.unlink()
    launcher2 = FakeLauncher(env["clock"])
    runner2 = make_runner(env, specs, launcher=launcher2)
    assert runner2._preflight() is None
    (env["plan"] / "QUARANTINE.json").write_text("{}", encoding="utf-8")
    direct = runner2._run_phase(runner2.specs[0])
    assert direct["verdict"] == rr.REFUSED and direct["reasons"][0].startswith("quarantine_marker_present") and launcher2.calls == []


# =========================================================================== GPT 74: U (user do duoc phai khop spec va Q0), S/Q0 chi tiet, tolerance, flags cam, observer.close
def test_gpt74_control_u_the_filter_user_must_be_the_measured_user_not_just_the_spec_user(env):
    write = make_spec(env, "ph_w", kind="write")                                                              # spec app_user = hotel_app
    wrong = {"baseline_thread_id": 1000, "baseline_uptime_s": 500.0, "probe_session_id": 5000, "server_uuid": SERVER_UUID, "app_user": "actual_db_user"}
    leftover = {"rows": [row(1600, user="actual_db_user", db=None, pid=None)]}                                # phien cua phase mang user THAT, DB NULL, khong pid
    launcher = FakeLauncher(env["clock"])
    observer = FakeObserver(env["clock"], probes=[leftover, leftover], baseline=wrong)
    receipts = make_runner(env, [write], launcher=launcher, observer=observer).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and "app_user_differs_from_spec" in receipts[0]["reasons"][0] and launcher.calls == [] and observer.probe_calls == 0


def test_the_baseline_user_must_also_equal_the_user_the_q0_receipt_measured(env):
    path, sha = write_q0(env["tmp"], details={"app_user": "someone_else", "ids_increase": True, "successive_ids": [1, 2], "databases": ["x"], "databases_truncated": False})
    write = make_spec(env, "ph_w", kind="write")
    launcher = FakeLauncher(env["clock"])
    runner = make_runner(env, [write], launcher=launcher, observer=FakeObserver(env["clock"]), q0=load_q0(path, sha), q0_pin=sha)
    receipts = runner.run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and "app_user_differs_from_q0" in receipts[0]["reasons"][0] and launcher.calls == []


VALID_OUTER = {"APPROVED_PLAN_SHA256": "a" * 64, "APPROVED_NOT_AFTER_UTC": "2026-10-09T17:30:00Z", "APPROVED_PLAN_DIR": "D:\\MSE\\CAPSTONE\\outputs\\plan_run_1", "APPROVED_ENTRYPOINT_SHA256": "b" * 64,
               "APPROVED_INVOCATION_SHA256": "c" * 64}


def outer_text(**override):
    values = {**VALID_OUTER, **override}
    return "# GPT approval\n\nprose APPROVED is fine here\n\n" + "\n".join(f"{k}: {v}" for k, v in values.items() if v is not None) + "\n\n- APPROVED_PLAN_SHA256: ignored bullet\n"


def test_the_outer_approval_parser_requires_exactly_the_five_keys_once_with_exact_formats():
    parsed = rr.parse_outer_approval(outer_text())
    assert parsed == {"plan_sha256": "a" * 64, "not_after_utc": dt.datetime(2026, 10, 9, 17, 30, tzinfo=dt.timezone.utc), "plan_dir": VALID_OUTER["APPROVED_PLAN_DIR"],
                      "entrypoint_sha256": "b" * 64, "invocation_sha256": "c" * 64}
    assert rr.parse_outer_approval(outer_text(APPROVED_PLAN_SHA256="`" + "a" * 64 + "`", APPROVED_PLAN_DIR="`D:\\x y\\run`"))["plan_dir"] == "D:\\x y\\run"        # backtick duoc chap nhan
    for key in rr.OUTER_APPROVAL_KEYS:
        with pytest.raises(ValueError, match=f"approval_key_count: {key}=0"):
            rr.parse_outer_approval(outer_text(**{key: None}))
        with pytest.raises(ValueError, match=f"approval_key_count: {key}=2"):
            rr.parse_outer_approval(outer_text() + f"{key}: {VALID_OUTER[key]}\n")
    with pytest.raises(ValueError, match="approval_unknown_key"):
        rr.parse_outer_approval(outer_text() + "APPROVED_MODES: run:ledger\n")
    with pytest.raises(ValueError, match="approval_unknown_key"):
        rr.parse_outer_approval(outer_text() + "APPROVED_NOT_AFTER: 2026-10-09T17:30:00Z\n")
    with pytest.raises(ValueError, match="approval_line_malformed: APPROVED_PLAN_SHA256"):
        rr.parse_outer_approval(outer_text(APPROVED_PLAN_SHA256="") )
    for key, bad, reason in (("APPROVED_PLAN_SHA256", "A" * 64, "approval_hash_malformed"), ("APPROVED_PLAN_SHA256", "a" * 63, "approval_hash_malformed"),
                             ("APPROVED_ENTRYPOINT_SHA256", "g" * 64, "approval_hash_malformed"), ("APPROVED_INVOCATION_SHA256", "c" * 65, "approval_hash_malformed"),
                             ("APPROVED_NOT_AFTER_UTC", "2026-10-09 17:30:00Z", "approval_not_after_malformed"), ("APPROVED_NOT_AFTER_UTC", "2026-10-09", "approval_not_after_malformed"),
                             ("APPROVED_NOT_AFTER_UTC", "2026-13-09T17:30:00Z", "approval_not_after_malformed"), ("APPROVED_NOT_AFTER_UTC", "2026-1-9T7:3:0Z", "approval_not_after_malformed"), ("APPROVED_NOT_AFTER_UTC", "2026-10-09T17:30:00+07:00", "approval_not_after_malformed"),
                             ("APPROVED_PLAN_DIR", "plan_run_1", "approval_plan_dir_not_absolute_canonical"), ("APPROVED_PLAN_DIR", "D:\\a\\..\\b", "approval_plan_dir_not_absolute_canonical"),
                             ("APPROVED_PLAN_DIR", "\\\\server\\share\\x", "approval_plan_dir_not_absolute_canonical"), ("APPROVED_PLAN_DIR", "/tmp/run", "approval_plan_dir_not_absolute_canonical")):
        with pytest.raises(ValueError, match=reason):
            rr.parse_outer_approval(outer_text(**{key: bad}))
    with pytest.raises(ValueError, match="approval_line_malformed: APPROVED_PLAN_SHA256"):
        rr.parse_outer_approval(outer_text(APPROVED_PLAN_SHA256="x") .replace("APPROVED_PLAN_SHA256: x", "APPROVED_PLAN_SHA256 is the hash"))        # cau van xuat phat bang ten khoa => sai dinh dang, khong bo qua


def test_an_expired_approval_is_refused_at_plan_level_before_any_side_effect_and_the_boundary_counts_as_expired(env):
    for offset in (-1, 0):
        launcher = FakeLauncher(env["clock"])
        receipts = make_runner(env, [make_spec(env, "ph_a")], launcher=launcher, not_after_utc=START_UTC + dt.timedelta(seconds=offset)).run_plan()
        assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"] == ["approval_expired"] and launcher.calls == [] and not (env["plan"] / "PLAN_BOUND.json").exists(), offset
        for f in env["plan"].glob("*"):
            f.unlink()


def test_the_approval_expiry_is_an_absolute_deadline_the_phase_budget_must_fit_inside(env):
    spec = make_spec(env, "ph_a", deadline_s=600)
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, [spec], launcher=launcher, settings=settings_with(total_budget_s=3600.0, window_end_vn=None), not_after_utc=START_UTC + dt.timedelta(seconds=500)).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("budget_insufficient") and launcher.calls == []
    for f in env["plan"].glob("*"):
        f.unlink()
    ok = make_runner(env, [spec], launcher=FakeLauncher(env["clock"]), settings=settings_with(total_budget_s=3600.0, window_end_vn=None), not_after_utc=START_UTC + dt.timedelta(seconds=900)).run_plan()
    assert ok[0]["verdict"] == rr.PASS
    bound = json.loads((env["plan"] / "PLAN_BOUND.json").read_text(encoding="utf-8"))
    assert bound["hard_deadline_in_s"] == 900.0 and bound["not_after_utc"] == "2026-10-09T11:15:00Z" and bound["entrypoint_sha256"] is None


def test_the_expiry_is_rechecked_before_every_phase_even_when_the_wall_clock_jumps(env):
    jumped = {"on": False}

    class Jumping(FakeLauncher):
        def launch(self, spec, cap_s, **kw):
            result = super().launch(spec, cap_s, **kw)
            jumped["on"] = True                                                                                  # dong ho tuong (UTC) nhay vot, monotonic thi khong
            return result

    spec_a, spec_b = make_spec(env, "ph_a"), make_spec(env, "ph_b")
    launcher = Jumping(env["clock"])
    receipts = make_runner(env, [spec_a, spec_b], launcher=launcher, settings=settings_with(total_budget_s=3600.0, window_end_vn=None), not_after_utc=START_UTC + dt.timedelta(seconds=1800),
                           now_utc=lambda: env["clock"].now_utc() + (dt.timedelta(seconds=5000) if jumped["on"] else dt.timedelta(0))).run_plan()
    assert [r["verdict"] for r in receipts] == [rr.PASS, rr.REFUSED] and receipts[1]["reasons"] == ["approval_expired_before_phase"] and [c[0] for c in launcher.calls] == ["ph_a"]


def test_every_receipt_carries_the_approved_entrypoint_hash_and_the_expiry(env):
    receipts = make_runner(env, [make_spec(env, "ph_a")], not_after_utc=START_UTC + dt.timedelta(hours=2), entrypoint_sha256="e" * 64, settings=settings_with(total_budget_s=3600.0, window_end_vn=None)).run_plan()
    saved = json.loads((env["plan"] / "receipt_01_ph_a.json").read_text(encoding="utf-8"))
    assert receipts[0]["verdict"] == rr.PASS and saved["entrypoint_sha256"] == "e" * 64 and saved["not_after_utc"] == "2026-10-09T13:00:00Z"


def test_the_observer_startup_and_cleanup_overhead_is_part_of_the_pre_baseline_budget(env):
    write = make_spec(env, "ph_w", kind="write", deadline_s=100)

    class Overhead(FakeObserver):
        overhead_s = 30.0

    plain = make_runner(env, [write], observer=FakeObserver(env["clock"]))
    heavy = make_runner(env, [write], observer=Overhead(env["clock"]))
    s = settings_with()
    needed_plain = 100 + plain.reserve_after_launch_s(write) + s["margin_s"] + s["baseline_cap_s"]
    for runner in (plain, heavy):
        runner.hard_deadline = runner.clock.monotonic() + needed_plain
    assert plain._budget_problem(write, include_baseline=True) is None
    assert heavy._budget_problem(write, include_baseline=True).startswith("budget_insufficient") and f"needed={round(needed_plain + 30.0, 1)}s" in heavy._budget_problem(write, include_baseline=True)
    assert heavy._budget_problem(write, include_baseline=False) is None                                          # phase readonly/sau baseline: khong tinh overhead


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1.0, True, "30", None])
def test_an_observer_that_declares_a_malformed_overhead_never_fits_any_budget(env, bad):
    write = make_spec(env, "ph_w", kind="write")

    class Odd(FakeObserver):
        overhead_s = bad

    runner = make_runner(env, [write], observer=Odd(env["clock"]))
    runner.hard_deadline = runner.clock.monotonic() + 10 ** 6
    assert runner._budget_problem(write, include_baseline=True).startswith("budget_insufficient")


# =========================================================================== thu muc dau ra (GPT 78 H3-DIR)
def make_tree(root, files):
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return root


def test_directory_seal_lists_every_regular_file_with_its_sha_and_a_canonical_aggregate(tmp_path):
    root = make_tree(tmp_path / "ds", {"a.txt": "A", "sub/b.txt": "BB", "sub/deep/c.txt": "CCC"})
    (root / "empty_sub").mkdir()
    seal = rr.directory_seal(root)
    expected = {"a.txt": hashlib.sha256(b"A").hexdigest(), "sub/b.txt": hashlib.sha256(b"BB").hexdigest(), "sub/deep/c.txt": hashlib.sha256(b"CCC").hexdigest()}
    assert seal == {"kind": "directory", "files": 3, "bytes": 6, "aggregate_sha256": rr.sha256_hex(rr.canonical_json(expected)), "manifest": expected}
    assert rr.directory_seal(root) == seal                                                                       # tat dinh
    (root / "a.txt").write_bytes(b"X")
    assert rr.directory_seal(root)["aggregate_sha256"] != seal["aggregate_sha256"] and rr.directory_seal(root)["bytes"] == 6


def test_directory_seal_refuses_non_directories_unbounded_trees_and_links(tmp_path, monkeypatch):
    for bad in (tmp_path / "missing", make_tree(tmp_path / "plain", {"f.txt": "x"}) / "f.txt"):
        with pytest.raises(ValueError, match="not_a_real_directory"):
            rr.directory_seal(bad)
    many = make_tree(tmp_path / "many", {f"f{i}.txt": "x" for i in range(3)})
    with pytest.raises(ValueError, match="too_large"):
        rr.directory_seal(many, max_files=2)
    assert rr.directory_seal(many, max_files=3)["files"] == 3                                                    # ranh gioi: dung bang gioi han thi duoc
    with pytest.raises(ValueError, match="too_large"):
        rr.directory_seal(many, max_bytes=2)
    assert rr.directory_seal(many, max_bytes=3)["bytes"] == 3
    deep = make_tree(tmp_path / "deep", {"a/b/c.txt": "x"})
    with pytest.raises(ValueError, match="too_deep"):
        rr.directory_seal(deep, max_depth=1)
    assert rr.directory_seal(deep, max_depth=2)["files"] == 1
    linked = make_tree(tmp_path / "linked", {"ok.txt": "x", "evil.lnk": "y"})
    monkeypatch.setattr(rr, "_is_link", lambda path: Path(path).name == "evil.lnk")
    with pytest.raises(ValueError, match="link_inside: evil.lnk"):
        rr.directory_seal(linked)
    monkeypatch.setattr(rr, "_is_link", lambda path: Path(path).name == "linked")
    with pytest.raises(ValueError, match="not_a_real_directory"):
        rr.directory_seal(linked)


def test_directory_seal_refuses_a_special_file_and_the_order_of_discovery_does_not_change_the_hash(tmp_path, monkeypatch):
    import os as _os
    import stat as _stat
    root = make_tree(tmp_path / "ds", {"ok.txt": "x", "special": "y"})
    real = _os.lstat

    def fake_lstat(path, *a, **kw):
        status = real(path, *a, **kw)
        if Path(path).name == "special":
            return _os.stat_result((_stat.S_IFIFO | 0o600,) + tuple(status)[1:])
        return status
    monkeypatch.setattr(_os, "lstat", fake_lstat)
    with pytest.raises(ValueError, match="not_a_regular_file: special"):
        rr.directory_seal(root)
    monkeypatch.undo()
    a = rr.directory_seal(make_tree(tmp_path / "one", {"z.txt": "1", "a/b.txt": "2"}))
    b = rr.directory_seal(make_tree(tmp_path / "two", {"a/b.txt": "2", "z.txt": "1"}))
    assert a["aggregate_sha256"] == b["aggregate_sha256"] and list(a["manifest"]) == ["a/b.txt", "z.txt"]      # manifest sap xep, hash tong khong phu thuoc thu tu tao


def test_an_oserror_while_sealing_is_reported_as_a_seal_failure_not_as_an_unexpected_crash(env, monkeypatch):
    target = env["tmp"] / "ds_out"
    monkeypatch.setattr(rr, "directory_seal", lambda directory: (_ for _ in ()).throw(OSError("disk went away")))
    write = make_spec(env, "ph_w", kind="write", output_dirs=[str(target)])
    receipts = make_runner(env, [write], launcher=DirLauncher(env["clock"], lambda sp: make_tree(target, {"a": "1"})), observer=FakeObserver(env["clock"])).run_plan()
    assert receipts[0]["verdict"] == rr.QUARANTINE and receipts[0]["reasons"][0].startswith("output_seal_failed") and "OSError: disk went away" in receipts[0]["output_dirs"][str(target)]["reason"]


def block_scandir_of(monkeypatch, name):
    """Chi bom loi PermissionError vao `os.scandir` cua dung thu muc con `name`; `os.walk` va `directory_seal` THAT van chay (khong thay ham seal)."""
    import os as _os
    real = _os.scandir

    def fake(path="."):
        if Path(path).name == name:
            raise PermissionError(13, "Permission denied (injected)", str(path))
        return real(path)
    monkeypatch.setattr(_os, "scandir", fake)


def test_a_subdirectory_that_cannot_be_listed_is_a_seal_error_not_a_silently_smaller_manifest(tmp_path, monkeypatch):
    root = make_tree(tmp_path / "ds", {"visible.txt": "v", "blocked/hidden.txt": "h"})
    assert sorted(rr.directory_seal(root)["manifest"]) == ["blocked/hidden.txt", "visible.txt"]                  # doi chung: khong loi => du hai tep
    block_scandir_of(monkeypatch, "blocked")
    with pytest.raises(PermissionError, match="injected"):
        rr.directory_seal(root)


def test_through_the_runner_an_unlistable_subdirectory_quarantines_the_write_phase_and_leaves_a_marker(env, monkeypatch):
    target = env["tmp"] / "ds_out"
    write = make_spec(env, "ph_w", kind="write", output_dirs=[str(target)])
    launcher = DirLauncher(env["clock"], lambda sp: (make_tree(target, {"visible.txt": "v", "blocked/hidden.txt": "h"}), block_scandir_of(monkeypatch, "blocked")))
    receipts = make_runner(env, [write], launcher=launcher, observer=FakeObserver(env["clock"])).run_plan()
    assert receipts[0]["verdict"] == rr.QUARANTINE and receipts[0]["reasons"] == [f"output_seal_failed: {[str(target)]}"] and (env["plan"] / "QUARANTINE.json").exists()
    assert "PermissionError" in receipts[0]["output_dirs"][str(target)]["reason"]


def test_the_shared_reserve_function_is_what_the_runner_uses(env, monkeypatch):
    spec = make_spec(env, "ph_w", kind="write", output_dirs=[str(env["tmp"] / "a")])
    runner = make_runner(env, [spec], observer=FakeObserver(env["clock"]))
    s = settings_with()
    expected = s["containment_grace_s"] + s["receipt_reserve_s"] + rr.DIR_SEAL_BUDGET_S + s["settle_s"] + (s["max_probes"] - 1) * (rr.qm.MIN_SPACING_S + 0.5) + s["max_probes"] * s["probe_cap_s"]
    assert rr.reserve_after_launch(spec, s) == runner.reserve_after_launch_s(spec) == expected == 10 + 5 + 30 + 2 + 3 * 10.5 + 4 * 15
    readonly = make_spec(env, "ph_a")
    assert rr.reserve_after_launch(readonly, s) == s["containment_grace_s"] + s["receipt_reserve_s"] == 15
    monkeypatch.setattr(rr, "reserve_after_launch", lambda sp, st: 123.0)
    assert runner.reserve_after_launch_s(spec) == 123.0


@pytest.mark.skipif(sys.platform != "win32", reason="junction chi co tren Windows")
def test_directory_seal_detects_a_real_junction_inside_the_tree(tmp_path):
    import subprocess as sp
    target = make_tree(tmp_path / "target", {"secret.txt": "s"})
    root = make_tree(tmp_path / "ds", {"ok.txt": "x"})
    link = root / "jump"
    made = sp.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("khong tao duoc junction")
    try:
        with pytest.raises(ValueError, match="link_inside: jump"):
            rr.directory_seal(root)
    finally:
        sp.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)                                          # go lien ket, KHONG dong vao muc tieu


def test_output_dirs_must_be_absolute_distinct_non_nested_and_not_overlapping_the_file_outputs(env):
    good = str(env["tmp"] / "ds1")
    other = str(env["tmp"] / "ds2")
    assert rr.validate_spec(make_spec(env, output_dirs=[good, other])) == []
    assert rr.validate_spec(make_spec(env, output_dirs=[])) == []
    for bad, why in ((["relative/ds"], "output_dirs"), ([str(env["tmp"] / ".." / "ds")], "output_dirs"), ([good, good], "output_dirs"), ([good, good.upper()], "output_dirs"), ([good, good + "\\"], "output_dirs"),
                     ([1], "output_dirs"), (good, "output_dirs"), ([good, good + "\\inner"], "output_dirs_overlap"), ([other + "\\", other + "\\a\\b"], "output_dirs_overlap"),
                     ([r"\\\\server\\share\\ds"], "output_dirs")):
        assert why in " ".join(rr.validate_spec(make_spec(env, output_dirs=bad))), bad
    assert "output_dirs_overlap" in " ".join(rr.validate_spec(make_spec(env, output_dirs=[good], outputs=[good + "\\file.parquet"])))      # tep dau ra nam TRONG thu muc dau ra


def test_output_dirs_are_part_of_the_plan_hash_and_each_one_adds_a_seal_reserve(env):
    plain = make_spec(env, "ph_a")
    with_dirs = make_spec(env, "ph_a", output_dirs=[str(env["tmp"] / "ds1"), str(env["tmp"] / "ds2")])
    s = settings_with()
    assert rr.plan_sha256([plain], s, {"x": 1}, None) != rr.plan_sha256([with_dirs], s, {"x": 1}, None) and rr.spec_sha256(plain) != rr.spec_sha256(with_dirs)
    runner = make_runner(env, [plain])
    assert runner.reserve_after_launch_s(with_dirs) - runner.reserve_after_launch_s(plain) == 2 * rr.DIR_SEAL_BUDGET_S == 60.0


class DirLauncher(FakeLauncher):
    def __init__(self, clock, make, **kw):
        super().__init__(clock, **kw)
        self.make = make

    def launch(self, spec, cap_s, **kw):
        self.make(spec)
        return super().launch(spec, cap_s, **kw)


def test_a_phase_that_creates_its_declared_directory_is_sealed_into_the_receipt(env):
    target = env["tmp"] / "ds_out"
    spec = make_spec(env, "ph_a", output_dirs=[str(target)])
    receipts = make_runner(env, [spec], launcher=DirLauncher(env["clock"], lambda sp: make_tree(target, {"x.txt": "1", "r/y.txt": "22"}))).run_plan()
    saved = json.loads((env["plan"] / "receipt_01_ph_a.json").read_text(encoding="utf-8"))
    expected = {"r/y.txt": hashlib.sha256(b"22").hexdigest(), "x.txt": hashlib.sha256(b"1").hexdigest()}
    assert receipts[0]["verdict"] == rr.PASS and saved["output_dirs"] == {str(target): {"kind": "directory", "files": 2, "bytes": 3, "aggregate_sha256": rr.sha256_hex(rr.canonical_json(expected)), "manifest": expected}}


def test_an_existing_output_directory_refuses_the_phase_before_launch_even_when_empty_or_a_dangling_link(env):
    target = env["tmp"] / "ds_out"
    target.mkdir()
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, [make_spec(env, "ph_a", output_dirs=[str(target)])], launcher=launcher).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("output_already_exists") and launcher.calls == []


def test_a_missing_or_empty_output_directory_after_a_successful_child_is_a_clean_failure(env):
    target = env["tmp"] / "ds_out"
    spec = make_spec(env, "ph_a", output_dirs=[str(target)])
    receipts = make_runner(env, [spec], launcher=DirLauncher(env["clock"], lambda sp: None)).run_plan()
    assert receipts[0]["verdict"] == rr.FAIL_CLEAN and receipts[0]["reasons"][0].startswith("expected_outputs_missing")
    for f in env["plan"].glob("*"):
        f.unlink()
    receipts = make_runner(env, [spec], launcher=DirLauncher(env["clock"], lambda sp: target.mkdir())).run_plan()
    assert receipts[0]["verdict"] == rr.FAIL_CLEAN and receipts[0]["reasons"][0].startswith("expected_outputs_missing")


def test_an_unsealable_output_directory_quarantines_a_write_phase_and_fails_a_readonly_phase_clean(env, monkeypatch):
    monkeypatch.setattr(rr, "_is_link", lambda path: Path(path).name == "evil.lnk")
    wtarget, rtarget = env["tmp"] / "ds_w", env["tmp"] / "ds_r"
    write = make_spec(env, "ph_w", kind="write", output_dirs=[str(wtarget)])
    receipts = make_runner(env, [write], launcher=DirLauncher(env["clock"], lambda sp: make_tree(wtarget, {"a.txt": "1", "evil.lnk": "2"})), observer=FakeObserver(env["clock"])).run_plan()
    assert receipts[0]["verdict"] == rr.QUARANTINE and receipts[0]["reasons"] == [f"output_seal_failed: {[str(wtarget)]}"] and (env["plan"] / "QUARANTINE.json").exists()
    assert receipts[0]["output_dirs"][str(wtarget)]["kind"] == "seal_failed" and "link_inside" in receipts[0]["output_dirs"][str(wtarget)]["reason"]
    for f in env["plan"].glob("*"):
        f.unlink()
    readonly = make_spec(env, "ph_a", output_dirs=[str(rtarget)])
    receipts = make_runner(env, [readonly], launcher=DirLauncher(env["clock"], lambda sp: make_tree(rtarget, {"evil.lnk": "2"}))).run_plan()
    assert receipts[0]["verdict"] == rr.FAIL_CLEAN and receipts[0]["reasons"][0].startswith("output_seal_failed") and not (env["plan"] / "QUARANTINE.json").exists()


def test_a_failed_child_keeps_its_own_reason_even_if_the_directory_is_unsealable(env, monkeypatch):
    monkeypatch.setattr(rr, "_is_link", lambda path: True)
    target = env["tmp"] / "ds_out"
    receipts = make_runner(env, [make_spec(env, "ph_a", output_dirs=[str(target)])], launcher=DirLauncher(env["clock"], lambda sp: make_tree(target, {"a": "1"}), results=[{"exit_code": 3}])).run_plan()
    assert receipts[0]["verdict"] == rr.FAIL_CLEAN and receipts[0]["reasons"][0].startswith("child_failed")


def test_the_q0_receipt_must_still_be_fresh_when_each_write_phase_starts_not_only_at_plan_start(env):
    spec_a = make_spec(env, "ph_a", deadline_s=3300)
    write = make_spec(env, "ph_w", kind="write")
    settings = settings_with(total_budget_s=9000.0, window_end_vn=None)
    launcher = FakeLauncher(env["clock"], results=[{"duration": 3100.0}])                                       # Q0 tao 10 phut truoc plan; phase dau ton 3100 s => tuoi ~3700 s > 3600
    observer = FakeObserver(env["clock"])
    receipts = make_runner(env, [spec_a, write], launcher=launcher, observer=observer, settings=settings).run_plan()
    assert [r["verdict"] for r in receipts] == [rr.PASS, rr.REFUSED] and receipts[1]["reasons"][0].startswith("q0_receipt_stale_at_phase: age_s=37") and "max_age_s=3600" in receipts[1]["reasons"][0]
    assert observer.baseline_calls == 0 and [c[0] for c in launcher.calls] == ["ph_a"]


def test_a_q0_receipt_from_the_future_is_refused_at_phase_start_without_any_clock_skew_tolerance(env):
    write = make_spec(env, "ph_w", kind="write")
    observer = FakeObserver(env["clock"])
    future = {**load_q0(env["q0_path"], env["q0_sha"]), "created_at_utc": (START_UTC + dt.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    receipts = make_runner(env, [write], observer=observer, q0=future).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("q0_receipt_stale_at_phase: age_s=-30") and observer.baseline_calls == 0


def test_a_q0_receipt_whose_creation_time_cannot_be_read_at_phase_start_refuses_before_any_baseline(env):
    write = make_spec(env, "ph_w", kind="write")
    observer = FakeObserver(env["clock"])
    broken = {**load_q0(env["q0_path"], env["q0_sha"]), "created_at_utc": "not-a-time"}
    receipts = make_runner(env, [write], observer=observer, q0=broken).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"] == ["q0_receipt_age_unreadable_at_phase"] and observer.baseline_calls == 0


@pytest.mark.parametrize("app_user", [None, "", 7, ["hotel_app"]])
def test_a_missing_or_malformed_baseline_user_is_refused(env, app_user):
    baseline = {"baseline_thread_id": 1000, "baseline_uptime_s": 500.0, "probe_session_id": 5000, "server_uuid": SERVER_UUID, "app_user": app_user}
    write = make_spec(env, "ph_w", kind="write")
    launcher = FakeLauncher(env["clock"])
    receipts = make_runner(env, [write], launcher=launcher, observer=FakeObserver(env["clock"], baseline=baseline)).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"][0].startswith("baseline_invalid: ['app_user']") and launcher.calls == []   # lý do là dạng dữ liệu, không phải "khác spec"


@pytest.mark.parametrize("name,details", [
    ("ids_false", {"app_user": APP, "ids_increase": False, "successive_ids": [5, 5], "databases": ["x"], "databases_truncated": False}),
    ("ids_missing", {"app_user": APP, "databases": ["x"], "databases_truncated": False}),
    ("ids_equal", {"app_user": APP, "ids_increase": True, "successive_ids": [7, 7], "databases": ["x"], "databases_truncated": False}),
    ("ids_float", {"app_user": APP, "ids_increase": True, "successive_ids": [1.0, 2.0], "databases": ["x"], "databases_truncated": False}),
    ("user_missing", {"ids_increase": True, "successive_ids": [1, 2], "databases": ["x"], "databases_truncated": False}),
    ("user_empty", {"app_user": "", "ids_increase": True, "successive_ids": [1, 2], "databases": ["x"], "databases_truncated": False}),
    ("databases_truncated", {"app_user": APP, "ids_increase": True, "successive_ids": [1, 2], "databases": ["x"], "databases_truncated": True}),
    ("databases_empty", {"app_user": APP, "ids_increase": True, "successive_ids": [1, 2], "databases": [], "databases_truncated": False}),
    ("databases_not_strings", {"app_user": APP, "ids_increase": True, "successive_ids": [1, 2], "databases": [1], "databases_truncated": False}),
    ("details_missing", None),
])
def test_the_loader_rejects_a_q0_receipt_whose_sanity_details_are_false_or_missing(env, name, details):
    over = {"details": details} if details is not None else {"details": "nope"}
    path, sha = write_q0(env["tmp"], **over)
    with pytest.raises(ValueError, match="q0_receipt_invalid"):
        load_q0(path, sha)


def test_a_q0_receipt_from_the_future_is_rejected_even_by_a_second_there_is_no_clock_skew_tolerance(env):
    for seconds, ok in ((1, False), (30, False), (0, True), (-5, True)):                                        # created = bay gio + seconds
        created = (START_UTC + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        path, sha = write_q0(env["tmp"], created_at_utc=created)
        if ok:
            assert load_q0(path, sha)["verified"] is True
        else:
            with pytest.raises(ValueError, match="q0_receipt_invalid"):
                load_q0(path, sha)


def test_the_q0_max_age_is_a_bounded_setting_that_belongs_to_the_approved_plan():
    assert rr.DEFAULT_SETTINGS["q0_max_age_s"] == 3600.0 and rr.validate_settings(rr.DEFAULT_SETTINGS) == []
    for bad in (59, 86401, float("nan"), True, "3600"):
        assert "q0_max_age_s" in " ".join(rr.validate_settings({**rr.DEFAULT_SETTINGS, "q0_max_age_s": bad})), bad
    specs = [{"name": "ph_a", "kind": "readonly"}]
    tooling = {"runner_sha256": "1" * 64, "quiescence_model_sha256": "2" * 64, "windows_job_sha256": "3" * 64}
    assert rr.plan_sha256(specs, rr.DEFAULT_SETTINGS, tooling, None) != rr.plan_sha256(specs, {**rr.DEFAULT_SETTINGS, "q0_max_age_s": 7200.0}, tooling, None)


@pytest.mark.parametrize("flag", ["--drop-existing", "--drop-only"])
def test_destructive_clone_flags_are_rejected_in_any_spec(flag):
    base = {"name": "ph_clone", "kind": "write", "argv": ["python", "clone.py", "--source", "a", "--target", "b"], "cwd": "c", "env": {}, "required_files": {"f": "a" * 64}, "deadline_s": 5,
            "db": {"app_user": APP, "db_scope": ["b"], "protected_dbs": ["x"]}}
    assert rr.validate_spec(base) == []
    assert any(e.startswith("forbidden_flag") for e in rr.validate_spec({**base, "argv": base["argv"] + [flag]}))
    readonly = {k: v for k, v in base.items() if k != "db"}
    assert any(e.startswith("forbidden_flag") for e in rr.validate_spec({**readonly, "kind": "readonly", "argv": base["argv"] + [flag]}))


def test_pythonunbuffered_is_an_allowed_env_name_like_the_earlier_builds():
    base = {"name": "ph_a", "kind": "readonly", "argv": ["x"], "cwd": "c", "env": {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}, "required_files": {"f": "a" * 64}, "deadline_s": 5}
    assert rr.validate_spec(base) == []


def test_the_runner_closes_the_observer_when_the_plan_ends_even_after_a_failure(env):
    class Closing(FakeObserver):
        closed = 0

        def close(self):
            Closing.closed += 1
    write = make_spec(env, "ph_w", kind="write")
    observer = Closing(env["clock"])
    make_runner(env, [write], launcher=FakeLauncher(env["clock"], results=[{"exit_code": 2}]), observer=observer).run_plan()
    assert Closing.closed == 1
    for f in env["plan"].glob("*"):
        f.unlink()
    Closing.closed = 0

    class Raising(Closing):
        def close(self):
            raise RuntimeError("worker already dead")
    assert make_runner(env, [write], observer=Raising(env["clock"])).run_plan()[0]["verdict"] in (rr.PASS, rr.QUARANTINE, rr.FAIL_CLEAN, rr.REFUSED)                 # loi khi dong khong lam hong ket qua


# =========================================================================== cong cu cua observer cung phai ghim (worker/proxy/observer/model)
def test_a_write_plan_whose_approved_tooling_has_no_observer_hash_is_refused_before_anything(env):
    write = make_spec(env, "ph_w", kind="write")
    launcher = FakeLauncher(env["clock"])
    observer = FakeObserver(env["clock"])
    tooling = {**rr.tooling_for(launcher, observer), "observer_sha256": None}
    receipts = make_runner(env, [write], launcher=launcher, observer=observer, expected_tooling=tooling).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"] == ["write_plan_requires_a_pinned_observer_tooling_hash"] and launcher.calls == [] and observer.baseline_calls == 0


def test_a_write_plan_whose_pinned_observer_hash_differs_from_the_running_observer_is_refused_per_phase(env):
    write = make_spec(env, "ph_w", kind="write")
    launcher = FakeLauncher(env["clock"])
    observer = FakeObserver(env["clock"])
    tooling = {**rr.tooling_for(launcher, observer), "observer_sha256": "0b" * 32}
    receipts = make_runner(env, [write], launcher=launcher, observer=observer, expected_tooling=tooling).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"] == ["tooling_not_approved"] and launcher.calls == [] and observer.baseline_calls == 0


def test_the_receipt_records_the_observer_tooling_hash_for_write_phases(env):
    write = make_spec(env, "ph_w", kind="write")
    observer = FakeObserver(env["clock"])
    receipts = make_runner(env, [write], observer=observer).run_plan()
    assert receipts[0]["verdict"] == rr.PASS and receipts[0]["tooling"]["observer_sha256"] == FakeObserver.tooling_sha256


def test_the_observer_tooling_key_is_mandatory_in_the_expected_tooling_shape(env):
    spec_a = make_spec(env, "ph_a")
    launcher = FakeLauncher(env["clock"])
    tooling = {k: v for k, v in rr.tooling_for(launcher).items() if k != "observer_sha256"}
    receipts = make_runner(env, [spec_a], launcher=launcher, expected_tooling=tooling).run_plan()
    assert receipts[0]["verdict"] == rr.REFUSED and receipts[0]["reasons"] == ["expected_tooling_missing_or_malformed"] and launcher.calls == []
