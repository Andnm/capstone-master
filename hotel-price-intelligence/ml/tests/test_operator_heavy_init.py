"""operator_heavy_init + run_heavy_init_workflow (INIT-ONLY: MOT plan, BON phase init_h1/verify_h1/init_h3/verify_h3 tren DB dich da clone PASS; version moi; `--n1-policy` bat buoc; phu thuoc clone PASS + quarantine ghim SHA; cong tuoi Q0 2700 s):
freeze voi run-root tham so (chi outputs\\repair-run-heavy-init-rNNN), plan khop freeze (ke ca argv init co `--n1-policy` THAT), probe crawler fail-closed, hai cong gate, chuoi receipt hon hop write/readonly + bao cao verify, cleanup finally.
Toan bo fake: khong SQL, khong process that. Hash runner/observer lay tu CHINH tep da ghim (exec-hash)."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_operator_q0 import FakeWorld

RUN_REAL = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009")
OPERATOR = Path(os.environ.get("HEAVY_INIT_OPERATOR_UNDER_TEST", str(RUN_REAL / "operator_heavy_init.py")))                   # ghi de CHI de chay mutation harness
DRIVER = RUN_REAL / "run_heavy_init_workflow.py"
OBSERVER = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\repair_db_observer_v1.py")
QM = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\quiescence_model_v1.py")
NOW = dt.datetime(2026, 10, 10, 13, 0, tzinfo=dt.timezone.utc)
SELFTEST_KEYS = ["innodb_trx_readable", "pid_attribute_visible", "removed_after_close", "visible_db_null_session", "visible_other_session"]
V1, V3, BAD = "ds_20261010_nf1", "ds_20261010_nf3", "ds_20261009_nf1"
TARGET = "warehouse_dsdev_20261009_3src_nf"
PRECEDING = 780.0
ALLOWED_AGE = 3600.0 - PRECEDING - 120.0                                                                        # 2700
PHASES = ("ph4_init_h1", "ph4_verify_h1", "ph4_init_h3", "ph4_verify_h3")
KINDS = {"ph4_init_h1": "write", "ph4_verify_h1": "readonly", "ph4_init_h3": "write", "ph4_verify_h3": "readonly"}


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def text(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class Hi:
    def __init__(self, tmp_path, monkeypatch, name="repair-run-heavy-init-r121", q0_age_s=300.0):
        self.mod = load(OPERATOR, "operator_heavy_init_under_test")
        mod = self.mod
        self.root = tmp_path / "root"
        self.outputs = self.root / "outputs"
        self.outputs.mkdir(parents=True)
        monkeypatch.setattr(mod, "ROOT", self.root)
        self.run = self.outputs / name
        self.run.mkdir()
        self.world = FakeWorld(mod, self.run, monkeypatch)
        monkeypatch.setattr(mod, "now", lambda: NOW + dt.timedelta(seconds=self.world.t - self.world.t0))
        self.tmp, self.monkeypatch = tmp_path, monkeypatch
        self.windows = {"not_before_utc": text(NOW - dt.timedelta(minutes=5)), "start_deadline_utc": text(NOW + dt.timedelta(minutes=55)), "hard_not_after_utc": text(NOW + dt.timedelta(hours=6))}
        self.q0_path = self.outputs / "repair-run-q0b-r120" / "q0" / "q0_receipt.json"
        self.q0_path.parent.mkdir(parents=True)
        self.q0_doc = self.make_q0(q0_age_s)
        self.q0_path.write_bytes(json.dumps(self.q0_doc, indent=2, sort_keys=True).encode("utf-8"))
        self.report_dir = self.run / "h2" / "reports"
        self.reports = [self.report_dir / f"verify_{V1}.json", self.report_dir / f"verify_{V3}.json"]
        self.freeze = self.make_freeze()
        self.freeze_path, self.freeze_sha = self.write_freeze(self.freeze)

    def make_q0(self, age_s, **mutate):
        observer = load(OBSERVER, "observer_for_heavy_init_tests")
        result = {**{k: True for k in SELFTEST_KEYS}, "server": {"server_uuid": "92d7a052-0723-11f1-8a6b-a82bdd5416bb", "uptime_s": 1000.0, "version": "8.0.45"},
                  "details": {"app_user": "root", "databases": ["hotel_price_intel", "warehouse_dsdev_20261009_3src_nf"], "databases_truncated": False, "errors": [], "ids_increase": True, "probe_session_id": 743,
                              "successive_ids": [744, 745]}}
        doc = observer.q0_receipt_document(result, now_utc=NOW - dt.timedelta(seconds=age_s))
        doc.update(mutate)
        return doc

    def file(self, name, content=b"x"):
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode("utf-8"))
        return path

    def spec(self, name):
        key = "h1" if name.endswith("_h1") else "h3"
        version = {"h1": V1, "h3": V3}[key]
        report = self.report_dir / f"verify_{version}.json"
        if name.startswith("ph4_init_"):
            argv = ["py", "init_dataset_build.py", "--database", TARGET, "--dataset-version", version, "--purpose", "dev", "--anomaly-cutoff", "2026-10-05T00:00:00Z", "--evaluation-horizons", "1" if key == "h1" else "3",
                    "--n1-policy", str(self.mod.N1_POLICY_FILE)]
            extra = {"db": {"db_scope": [TARGET]}}
        else:
            argv = ["py", "repair_verify_init_v1.py", "--db", TARGET, "--dataset-version", version, "--prediction-receipt", str(self.req_a), "--out", str(report)]
            extra = {"outputs": [str(report)]}
        return {"name": name, "kind": KINDS[name], "argv": argv, "cwd": str(self.tmp), "env": {"PATH": "p"}, "required_files": {**self.pinned, **self.extra.get(name, {})}, "deadline_s": 300.0 if name.startswith("ph4_init_") else 120.0, **extra}

    def make_freeze(self):
        mod, run = self.mod, self.run
        (run / "init" / "plan").mkdir(parents=True)
        self.req_a = self.file("req_a.txt", b"prediction receipt stand-in")
        self.pinned = {str(self.req_a): hashlib.sha256(self.req_a.read_bytes()).hexdigest()}
        self.req_b = self.file("req_b.txt", b"input b only for the LAST write phase")                         # chi phase init_h3 ghim tep nay => kiem drift phai duyet MOI spec
        self.extra = {"ph4_init_h3": {str(self.req_b): hashlib.sha256(self.req_b.read_bytes()).hexdigest()}}
        q0_sha = hashlib.sha256(self.q0_path.read_bytes()).hexdigest()
        specs = [self.spec(name) for name in PHASES]
        plan_sha = "c" * 64
        document = {"specs": specs, "q0_receipt_sha256": q0_sha, "settings": {"q0_max_age_s": 3600.0, "total_budget_s": 1380.0}}
        plan_file = self.file("init.plan.json", json.dumps({"document": document, "plan_sha256": plan_sha}).encode())
        approval = self.file("init_approval.md", b"approval init")
        plans = {"init": {"plan_file": str(plan_file), "plan_file_sha256": hashlib.sha256(plan_file.read_bytes()).hexdigest(), "plan_sha256": plan_sha, "approval": str(approval),
                          "approval_sha256": hashlib.sha256(approval.read_bytes()).hexdigest(), "invocation_sha256": "a" * 64, "plan_dir": str(run / "init" / "plan"), "launcher_logs": str(run / "init" / "logs"),
                          "total_budget_s": 1380.0, "q0_max_age_s": 3600.0, "q0_preceding_worst_case_s": PRECEDING, "target_database": TARGET, "versions": {"h1": V1, "h3": V3},
                          "h2_reports": [str(r) for r in self.reports]}}
        qm = load(QM, "qm_for_heavy_init_tests")
        clone = self.file("dep/clone_receipt.json", {"phase": "ph3_clone", "verdict": "PASS", "quiescence": {"status": qm.VERIFIED}})
        bound = self.file("dep/clone_plan_bound.json", {"kind": "plan_bound"})
        qreport = self.file("dep/quarantine_verify_report.json", {"verdict": "FAIL", "dataset_version": BAD, "database": TARGET})
        qinit = self.file("dep/quarantine_init_receipt.json", {"phase": "ph4_init_h1", "verdict": "PASS"})
        qverify = self.file("dep/quarantine_verify_receipt.json", {"phase": "ph4_verify_h1", "verdict": "FAIL_CLEAN"})
        self.deps = {"clone_receipt": clone, "clone_plan_bound": bound, "quarantine_verify_report": qreport, "quarantine_init_receipt": qinit, "quarantine_verify_receipt": qverify}
        dependencies = {name: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for name, path in self.deps.items()}
        profile = self.file("profile.json", b"profile")
        pins = {name: {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()} for name, path in mod.FIXED_PIN_PATHS.items()}
        return {"kind": "heavy_init_freeze", "run_root": str(run), "windows": dict(self.windows), "q0_receipt": {"path": str(self.q0_path), "sha256": q0_sha, "created_at_utc": self.q0_doc["created_at_utc"]},
                "plans": plans, "dependencies": dependencies, "quarantined_version": BAD, "profile": {"path": str(profile), "sha256": hashlib.sha256(profile.read_bytes()).hexdigest(), "renew_seconds": 1800}, "pins": pins}

    def write_freeze(self, doc, name="hi_freeze.json"):
        path = self.tmp / name
        data = json.dumps(doc, sort_keys=True).encode("utf-8")
        path.write_bytes(data)
        return path, hashlib.sha256(data).hexdigest()

    def load(self):
        return self.mod.load_freeze(self.freeze_path, self.freeze_sha)

    def started(self, wall_seconds=4000):
        self.world.append({"event": "started", "pid": 1, "at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=wall_seconds))})

    def evidence(self, **kw):
        state = {"present": True, "age_s": 10, "active_runs": 0, "active_items": 0, "read_only_verified": True, "latest_run_id": 67, "latest_run_status": "completed", "sha256": "x" * 64,
                 "generator_sha256": self.mod.CFG["pins"]["source"]["sha256"]}
        state.update(kw)
        self.monkeypatch.setattr(self.mod, "evidence_state", lambda: dict(state))

    def entry(self, *, check_ok=True, check_rc=None, rc=0, receipt_tamper=None, bound_tamper=None, summary_tamper=None, extra_files=(), check_elapsed_s=0.0, invoke_exc=None, stdout_prefix=b"",
              hash_override=None, plan_hash_override=None, quiescence=None, report_override=None, outputs_override=None, record=None):
        """`receipt_tamper`/`quiescence`/`report_override`/`outputs_override`: {phase_name: ...}; `record(list)` nhan ket qua kiem tra tai thoi diem invocation (vd thu muc h2 da ton tai)."""
        calls = []
        rr = self.mod.runner()

        def fake_run(argv, **kwargs):
            calls.append((list(argv), kwargs))
            p = self.mod.CFG["plans"]["init"]
            if argv[-1] == "--check-only":
                self.world.t += check_elapsed_s
                payload = {"ok": check_ok, "computed_plan_sha256": plan_hash_override or p["plan_sha256"], "computed_invocation_sha256": hash_override or p["invocation_sha256"]}
                return subprocess.CompletedProcess(argv, check_rc if check_rc is not None else (0 if check_ok else 1), stdout=(json.dumps(payload) + "\n").encode(), stderr=b"")
            if invoke_exc:
                raise invoke_exc
            if record is not None:
                record.append({"h2_reports_dir_exists": self.mod.H2_REPORTS.is_dir(), "reports_exist": [os.path.lexists(r) for r in p["h2_reports"]]})
            directory = Path(p["plan_dir"])
            plan_doc = json.loads(Path(p["plan_file"]).read_text(encoding="utf-8"))["document"]
            entry_sha = self.mod.CFG["pins"]["entry"]["sha256"]
            q0_sha = self.mod.CFG["q0"]["sha256"]
            bound = {"kind": "plan_bound", "approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "phases": list(PHASES), **(bound_tamper or {})}
            (directory / "PLAN_BOUND.json").write_text(json.dumps(bound), encoding="utf-8")
            previous, rows = None, []
            for index, spec in enumerate(plan_doc["specs"]):
                name = spec["name"]
                version = V1 if name.endswith("_h1") else V3
                outputs = {}
                if spec["kind"] == "readonly":
                    report = Path(spec["outputs"][0])
                    content = (report_override or {}).get(name, {"verdict": "PASS", "dataset_version": version, "database": TARGET})
                    if content is not None:
                        report.write_bytes(json.dumps(content).encode("utf-8") if not isinstance(content, bytes) else content)
                        outputs = {str(report): hashlib.sha256(report.read_bytes()).hexdigest()}
                    if outputs_override and name in outputs_override:
                        outputs = outputs_override[name]
                launch = {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": True}
                full = {"phase": name, "seq": index + 1, "verdict": "PASS", "reasons": [], "kind": spec["kind"], "prev_receipt_sha256": previous, "approved_plan_sha256": p["plan_sha256"],
                        "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "spec_sha256": rr.spec_sha256(spec), "argv_sha256": rr.argv_sha256(spec),
                        "q0_receipt_sha256": q0_sha if spec["kind"] == "write" else None, "launch": launch, "outputs": outputs, **((receipt_tamper or {}).get(name, {}))}
                if spec["kind"] == "write":
                    full["quiescence"] = (quiescence or {}).get(name, {"status": rr.qm.VERIFIED})
                (directory / f"receipt_{index + 1:02d}_{name}.json").write_text(json.dumps(full, indent=2, sort_keys=True), encoding="utf-8")
                previous = rr.sha256_hex(rr.canonical_json(full))
                rows.append({"phase": name, "reasons": [], "receipt_path": "x", "receipt_sha256": previous, "verdict": "PASS"})
            for extra in extra_files:
                (directory / extra).write_text("x", encoding="utf-8")
            summary = {"approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "invocation_sha256": p["invocation_sha256"], "ok": True, "phases": rows,
                       **(summary_tamper or {})}
            return subprocess.CompletedProcess(argv, rc, stdout=stdout_prefix + json.dumps(summary).encode() + b"\r\n", stderr=b"")
        self.monkeypatch.setattr(self.mod.subprocess, "run", fake_run)
        return calls


@pytest.fixture
def pr(tmp_path, monkeypatch):
    env = Hi(tmp_path, monkeypatch)
    assert env.load() == []
    return env


def ready(pr, **kw):
    pr.started()
    pr.evidence()
    return pr.entry(stdout_prefix=b"line1\r\n", **kw)


def clean_probe(pr, monkeypatch, **override):
    probe = {"success": True, "pids": [], "returncode": 0, "timed_out": False}
    probe.update(override)
    monkeypatch.setattr(pr.mod, "crawler_probe", lambda: dict(probe))


# =========================================================================== load_freeze
def test_a_valid_freeze_loads_and_the_argv_pins_the_q0_receipt_of_the_freeze(pr):
    mod = pr.mod
    p = mod.CFG["plans"]["init"]
    assert mod.approved_argv("init") == [mod.PY_VENV, str(mod.ENTRY), "--approval", p["approval"], "--plan", p["plan_file"], "--plan-dir", p["plan_dir"], "--launcher-logs", p["launcher_logs"],
                                         "--crawl-evidence", str(mod.LIVE), "--q0-receipt", str(pr.q0_path)]
    assert mod.PLAN_ORDER == ("init",) and mod.PLAN_PHASES == {"init": PHASES} and mod.RUN == pr.run and mod.CFG["quarantined_version"] == BAD


def test_every_path_follows_the_run_root_from_the_freeze(pr):
    mod, run = pr.mod, pr.run
    assert (mod.LIVE, mod.SUP_LOG, mod.PUB_LOG, mod.STOP, mod.SUP_STDOUT, mod.OP_LOG, mod.PREFLIGHT, mod.ARCHIVE_DIR, mod.H2_DIR, mod.H2_REPORTS) == (
        run / "live_crawl_idle_evidence.json", run / "hi_supervisor.jsonl", run / "hi_publisher.log", run / "hi_STOP", run / "hi_supervisor_stdout.log", run / "hi_operator_log.jsonl",
        run / "hi_dossier_preflight.md", run / "evidence_archive" / "hi", run / "h2", run / "h2" / "reports")


def mutated(pr, fn):
    doc = json.loads(json.dumps(pr.freeze))
    fn(doc)
    path, digest = pr.write_freeze(doc, "mutated.json")
    return pr.mod.load_freeze(path, digest)


def plan_field(key, value):
    return lambda d: d["plans"]["init"].__setitem__(key, value)


@pytest.mark.parametrize("name,fn,expected", [
    ("kind", lambda d: d.__setitem__("kind", "build_freeze"), "freeze_kind_or_run_root"),
    ("windows_order", lambda d: d["windows"].__setitem__("start_deadline_utc", d["windows"]["not_before_utc"]), "freeze_windows_order"),
    ("q0_elsewhere", lambda d: d["q0_receipt"].__setitem__("path", r"D:\elsewhere\q0_receipt.json"), "freeze_q0_receipt"),
    ("q0_in_q0c", lambda d: d["q0_receipt"].__setitem__("path", str(Path(d["run_root"]).parent / "repair-run-q0c-r121" / "q0" / "q0_receipt.json")), "freeze_q0_receipt"),
    ("q0_another_parent", lambda d: d["q0_receipt"].__setitem__("path", str(Path(d["run_root"]).parent.parent / "elsewhere" / "repair-run-q0b-r120" / "q0" / "q0_receipt.json")), "freeze_q0_receipt"),
    ("q0_wrong_file_name", lambda d: d["q0_receipt"].__setitem__("path", str(Path(d["run_root"]).parent / "repair-run-q0b-r120" / "q0" / "other.json")), "freeze_q0_receipt"),
    ("q0_wrong_dir_name", lambda d: d["q0_receipt"].__setitem__("path", str(Path(d["run_root"]).parent / "repair-run-q0b-r120" / "q1" / "q0_receipt.json")), "freeze_q0_receipt"),
    ("q0_bad_sha", lambda d: d["q0_receipt"].__setitem__("sha256", "short"), "freeze_q0_receipt"),
    ("plan_key_missing", lambda d: d["plans"]["init"].pop("versions"), "freeze_plan_init"),
    ("plan_relative", lambda d: d["plans"]["init"].__setitem__("approval", "approval.md"), "freeze_plan_init"),
    ("plan_budget_text", plan_field("total_budget_s", "1380"), "freeze_plan_init"),
    ("plan_dir_elsewhere", plan_field("plan_dir", r"D:\elsewhere\plan"), "freeze_plan_init_dirs_not_in_run_root"),
    ("launcher_logs_elsewhere", plan_field("launcher_logs", r"D:\elsewhere\logs"), "freeze_plan_init_dirs_not_in_run_root"),
    ("versions_equal", plan_field("versions", {"h1": V1, "h3": V1}), "freeze_plan_init_versions"),
    ("versions_bad_name", plan_field("versions", {"h1": "BadName", "h3": V3}), "freeze_plan_init_versions"),
    ("versions_missing_key", plan_field("versions", {"h1": V1}), "freeze_plan_init_versions"),
    ("h2_reports_wrong_order", lambda d: d["plans"]["init"].__setitem__("h2_reports", list(reversed(d["plans"]["init"]["h2_reports"]))), "freeze_plan_init_h2_reports"),
    ("h2_reports_elsewhere", plan_field("h2_reports", [r"D:\elsewhere\verify_a.json", r"D:\elsewhere\verify_b.json"]), "freeze_plan_init_h2_reports"),
    ("target_database_bad", plan_field("target_database", "hotel_price_intel"), "freeze_plan_init_target_database"),
    ("target_database_promoted", plan_field("target_database", "warehouse_20261004_3src"), "freeze_plan_init_target_database"),
    ("age_budget_impossible", plan_field("q0_preceding_worst_case_s", 3500.0), "freeze_plan_init_q0_age_budget_impossible"),
    ("age_budget_negative", plan_field("q0_preceding_worst_case_s", -1.0), "freeze_plan_init_q0_age_budget_impossible"),
    ("deps_missing", lambda d: d.pop("dependencies"), "freeze_dependencies"),
    ("deps_missing_key", lambda d: d["dependencies"].pop("clone_receipt"), "freeze_dependencies"),
    ("deps_extra_key", lambda d: d["dependencies"].__setitem__("other", {"path": r"D:\x\y.json", "sha256": "a" * 64}), "freeze_dependencies"),
    ("deps_bad_sha", lambda d: d["dependencies"]["clone_receipt"].__setitem__("sha256", "short"), "freeze_dependencies"),
    ("deps_relative", lambda d: d["dependencies"]["clone_receipt"].__setitem__("path", "clone.json"), "freeze_dependencies"),
    ("quarantined_missing", lambda d: d.pop("quarantined_version"), "freeze_dependencies"),
    ("quarantined_bad_name", lambda d: d.__setitem__("quarantined_version", "BAD"), "freeze_dependencies"),
    ("quarantined_equals_plan_version", lambda d: d.__setitem__("quarantined_version", V1), "freeze_plan_version_equals_the_quarantined_version"),
    ("pin_runner_redirected", lambda d: d["pins"]["runner"].__setitem__("path", r"D:\evil\runner.py"), "freeze_pin_runner"),
    ("pin_observer_redirected", lambda d: d["pins"]["observer_module"].__setitem__("path", r"D:\evil\observer.py"), "freeze_pin_observer_module"),
    ("pin_entry_short_sha", lambda d: d["pins"]["entry"].__setitem__("sha256", "abc"), "freeze_pin_entry"),
    ("pins_not_a_dict", lambda d: d.__setitem__("pins", ["x"]), "freeze_pins"),
    ("profile_bad", lambda d: d["profile"].__setitem__("sha256", "short"), "freeze_profile"),
    ("profile_renew_text", lambda d: d["profile"].__setitem__("renew_seconds", "1800"), "freeze_profile"),
])
def test_every_malformed_or_redirected_freeze_is_refused_and_leaves_cfg_empty(pr, name, fn, expected):
    assert expected in mutated(pr, fn) and pr.mod.CFG == {}


@pytest.mark.parametrize("name", ["repair-run-20261009", "repair-run-heavy-ci-r101", "repair-run-build-r122", "repair-run-heavy-init-r12", "repair-run-heavy-init-r1210", "repair-run-heavy-init-r121-x", "x-repair-run-heavy-init-r121"])
def test_only_heavy_init_run_roots_are_accepted_and_a_refusal_never_reconfigures_the_module(pr, name):
    before = (pr.mod.RUN, pr.mod.STOP)
    pr.mod.CFG.clear()
    assert mutated(pr, lambda d: d.__setitem__("run_root", str(pr.outputs / name))) == ["freeze_kind_or_run_root"] and pr.mod.CFG == {} and (pr.mod.RUN, pr.mod.STOP) == before
    assert mutated(pr, lambda d: d.__setitem__("run_root", str(pr.tmp / "elsewhere" / "repair-run-heavy-init-r121"))) == ["freeze_kind_or_run_root"]


def test_a_wrong_sha_or_unreadable_or_non_json_freeze_is_refused(pr):
    assert pr.mod.load_freeze(pr.freeze_path, "0" * 64) == ["freeze_sha256_mismatch"] and pr.mod.CFG == {}
    assert pr.mod.load_freeze(pr.tmp / "nope.json", pr.freeze_sha) == ["freeze_unreadable"]
    junk = pr.tmp / "junk.json"
    junk.write_bytes(b"{nope")
    assert pr.mod.load_freeze(junk, hashlib.sha256(b"{nope").hexdigest()) == ["freeze_not_json"]


def test_a_windows_field_that_is_missing_or_unparsable_is_refused(pr):
    assert mutated(pr, lambda d: d["windows"].pop("hard_not_after_utc")) == ["freeze_windows"]
    assert mutated(pr, lambda d: d["windows"].__setitem__("not_before_utc", "yesterday")) == ["freeze_windows"]


def test_log_before_a_freeze_only_prints_and_never_creates_a_directory(capsys):
    mod = load(OPERATOR, "operator_heavy_init_unset")
    assert not mod.RUN.exists()
    mod.log("early", x=1)
    assert not mod.RUN.exists() and '"event": "early"' in capsys.readouterr().out


# =========================================================================== crawler probe + preconditions
@pytest.mark.parametrize("result,success,pids", [
    (subprocess.CompletedProcess([], 0, stdout="", stderr=""), True, []), (subprocess.CompletedProcess([], 1, stdout="", stderr=""), False, []),
    (subprocess.CompletedProcess([], 0, stdout="", stderr="denied"), False, []), (subprocess.CompletedProcess([], 0, stdout="junk 5", stderr=""), False, [5])])
def test_crawler_probe_is_fail_closed(pr, monkeypatch, result, success, pids):
    monkeypatch.setattr(pr.mod.subprocess, "run", lambda *a, **k: result)
    probe = pr.mod.crawler_probe()
    assert probe["success"] is success and probe["pids"] == pids


def test_preconditions_clean(pr, monkeypatch):
    clean_probe(pr, monkeypatch)
    assert pr.mod.preconditions() == []


def test_preconditions_each_basic_problem(pr, monkeypatch):
    clean_probe(pr, monkeypatch, success=False, returncode=1)
    assert any("crawler_probe_failed" in p for p in pr.mod.preconditions())
    clean_probe(pr, monkeypatch, pids=[3])
    assert any("crawler_process_alive" in p for p in pr.mod.preconditions())
    clean_probe(pr, monkeypatch)
    pr.world.t += 3600
    assert any("outside_start_window" in p for p in pr.mod.preconditions())
    pr.world.t = pr.world.t0
    Path(pr.mod.CFG["plans"]["init"]["approval"]).write_bytes(b"tampered")
    (pr.run / "hi_STOP").write_text("x", encoding="utf-8")
    (pr.run / "init" / "plan" / "leftover.json").write_text("x", encoding="utf-8")
    (pr.run / "init" / "logs").mkdir()
    (pr.run / "hi_init_entry_stdout.json").write_text("x", encoding="utf-8")
    monkeypatch.setenv("DB_HOST", "x")
    problems = pr.mod.preconditions()
    for needle in ("sha_mismatch: init_approval", "already_exists: hi_STOP", "already_exists: logs", "already_exists: hi_init_entry_stdout.json", "plan_dir_not_empty: init", "shell_inherits_DB_vars"):
        assert needle in problems


def test_an_existing_h2_directory_or_report_blocks_the_start(pr, monkeypatch):
    clean_probe(pr, monkeypatch)
    pr.report_dir.mkdir(parents=True)
    assert "already_exists: h2" in pr.mod.preconditions()
    pr.reports[0].write_text("{}", encoding="utf-8")
    assert f"already_exists: {pr.reports[0].name}" in pr.mod.preconditions()


@pytest.mark.parametrize("pin", ["observer_module", "quiescence_model", "runner", "entry", "supervisor", "source", "interpreter", "parent_interpreter"])
def test_a_drifted_pinned_file_blocks_the_start(pr, monkeypatch, pin):
    clean_probe(pr, monkeypatch)
    pr.mod.CFG["pins"][pin]["sha256"] = "0" * 64
    assert f"sha_mismatch: {pin}" in pr.mod.preconditions()


def test_preconditions_refuse_when_no_freeze_is_loaded(pr):
    pr.mod.CFG.clear()
    assert pr.mod.preconditions() == ["freeze_not_loaded"]


# =========================================================================== plan file khop freeze (ke ca chong lap loi n1 10/10)
def edit_plan(pr, fn):
    path = Path(pr.mod.CFG["plans"]["init"]["plan_file"])
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")
    pr.mod.CFG["plans"]["init"]["plan_file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def spec_of(doc, name):
    return next(s for s in doc["document"]["specs"] if s["name"] == name)


def drop_pair(argv, flag):
    index = argv.index(flag)
    del argv[index:index + 2]


@pytest.mark.parametrize("name,fn,needle", [
    ("plan_hash", lambda d: d.__setitem__("plan_sha256", "9" * 64), "plan_file_hash_differs_from_freeze"),
    ("phase_renamed", lambda d: d["document"]["specs"][0].__setitem__("name", "ph4_init_x"), "plan_phases_differ_from_expected"),
    ("phase_order", lambda d: d["document"]["specs"].reverse(), "plan_phases_differ_from_expected"),
    ("a_phase_is_missing", lambda d: d["document"]["specs"].pop(), "plan_phases_differ_from_expected"),
    ("init_kind_readonly", lambda d: spec_of(d, "ph4_init_h1").__setitem__("kind", "readonly"), "plan_phase_kinds_differ_from_expected"),
    ("verify_kind_write", lambda d: spec_of(d, "ph4_verify_h3").__setitem__("kind", "write"), "plan_phase_kinds_differ_from_expected"),
    ("init_h1_without_the_n1_policy_flag", lambda d: drop_pair(spec_of(d, "ph4_init_h1")["argv"], "--n1-policy"), "init_argv_without_the_pinned_n1_policy: ph4_init_h1"),
    ("init_h3_without_the_n1_policy_flag", lambda d: drop_pair(spec_of(d, "ph4_init_h3")["argv"], "--n1-policy"), "init_argv_without_the_pinned_n1_policy: ph4_init_h3"),
    ("init_h1_other_n1_policy_path", lambda d: spec_of(d, "ph4_init_h1")["argv"].__setitem__(spec_of(d, "ph4_init_h1")["argv"].index("--n1-policy") + 1, r"D:\elsewhere\n1_policy_v1.json"), "init_argv_without_the_pinned_n1_policy: ph4_init_h1"),
    ("init_h1_n1_policy_twice", lambda d: spec_of(d, "ph4_init_h1")["argv"].extend(["--n1-policy", "x"]), "init_argv_without_the_pinned_n1_policy: ph4_init_h1"),
    ("init_h1_dry_run", lambda d: spec_of(d, "ph4_init_h1")["argv"].append("--dry-run"), "init_spec_has_dry_run_or_outputs: ph4_init_h1"),
    ("init_h3_has_outputs", lambda d: spec_of(d, "ph4_init_h3").__setitem__("outputs", ["x"]), "init_spec_has_dry_run_or_outputs: ph4_init_h3"),
    ("init_h1_wrong_database", lambda d: spec_of(d, "ph4_init_h1")["argv"].__setitem__(spec_of(d, "ph4_init_h1")["argv"].index("--database") + 1, "warehouse_20261004_3src"), "init_argv_target_or_version_differs: ph4_init_h1"),
    ("init_h3_wrong_version", lambda d: spec_of(d, "ph4_init_h3")["argv"].__setitem__(spec_of(d, "ph4_init_h3")["argv"].index("--dataset-version") + 1, V1), "init_argv_target_or_version_differs: ph4_init_h3"),
    ("verify_h1_wrong_db", lambda d: spec_of(d, "ph4_verify_h1")["argv"].__setitem__(spec_of(d, "ph4_verify_h1")["argv"].index("--db") + 1, "other_db"), "verify_argv_or_outputs_differ: ph4_verify_h1"),
    ("verify_h3_wrong_out", lambda d: spec_of(d, "ph4_verify_h3")["argv"].__setitem__(spec_of(d, "ph4_verify_h3")["argv"].index("--out") + 1, r"D:\elsewhere\r.json"), "verify_argv_or_outputs_differ: ph4_verify_h3"),
    ("verify_h1_outputs_differ", lambda d: spec_of(d, "ph4_verify_h1").__setitem__("outputs", []), "verify_argv_or_outputs_differ: ph4_verify_h1"),
    ("verify_h3_wrong_version", lambda d: spec_of(d, "ph4_verify_h3")["argv"].__setitem__(spec_of(d, "ph4_verify_h3")["argv"].index("--dataset-version") + 1, BAD), "verify_argv_or_outputs_differ: ph4_verify_h3"),
    ("quarantined_version_in_a_spec", lambda d: spec_of(d, "ph4_init_h1").__setitem__("db", {"note": BAD}), "quarantined_version_referenced: ph4_init_h1"),
    ("q0_pin", lambda d: d["document"].__setitem__("q0_receipt_sha256", "8" * 64), "plan_q0_pin_differs_from_freeze"),
    ("max_age_setting", lambda d: d["document"]["settings"].__setitem__("q0_max_age_s", 7200.0), "plan_settings_differ_from_freeze"),
    ("budget_setting", lambda d: d["document"]["settings"].__setitem__("total_budget_s", 100.0), "plan_settings_differ_from_freeze"),
])
def test_a_plan_file_that_does_not_match_the_freeze_blocks_the_start(pr, monkeypatch, name, fn, needle):
    clean_probe(pr, monkeypatch)
    edit_plan(pr, fn)
    assert any(needle in p for p in pr.mod.preconditions()), name


def test_a_required_file_pinned_only_by_a_later_spec_that_drifted_blocks_the_start(pr, monkeypatch):
    clean_probe(pr, monkeypatch)
    pr.req_b.write_bytes(b"changed")
    assert any(p.startswith("required_files_drifted") and "req_b.txt" in p for p in pr.mod.preconditions())


def test_a_required_file_that_drifted_blocks_the_start(pr, monkeypatch):
    clean_probe(pr, monkeypatch)
    pr.req_a.write_bytes(b"changed")
    assert any(p.startswith("required_files_drifted") for p in pr.mod.preconditions())
    pr.req_a.unlink()
    assert any(p.startswith("required_files_drifted") for p in pr.mod.preconditions())


# =========================================================================== phu thuoc: clone PASS + quarantine
@pytest.mark.parametrize("name", list(Hi.__init__.__code__.co_varnames and ["clone_receipt", "clone_plan_bound", "quarantine_verify_report", "quarantine_init_receipt", "quarantine_verify_receipt"]))
def test_a_changed_dependency_file_blocks_the_start(pr, monkeypatch, name):
    clean_probe(pr, monkeypatch)
    pr.deps[name].write_bytes(pr.deps[name].read_bytes() + b" ")
    assert f"dependency_sha_mismatch: {name}" in pr.mod.preconditions()
    pr.deps[name].unlink()
    assert f"dependency_unreadable: {name}" in pr.mod.preconditions()


def rewrite_dep(pr, name, content):
    pr.deps[name].write_text(json.dumps(content), encoding="utf-8")
    pr.mod.CFG["deps"][name]["sha256"] = hashlib.sha256(pr.deps[name].read_bytes()).hexdigest()


@pytest.mark.parametrize("content", [{"phase": "ph3_clone", "verdict": "FAIL_CLEAN", "quiescence": {"status": "VERIFIED"}}, {"phase": "other", "verdict": "PASS", "quiescence": {"status": "VERIFIED"}},
                                     {"phase": "ph3_clone", "verdict": "PASS", "quiescence": {"status": "unknown"}}, {"phase": "ph3_clone", "verdict": "PASS"}], ids=["clone_failed", "wrong_phase", "not_quiescent", "no_quiescence"])
def test_a_clone_receipt_that_is_not_a_verified_pass_blocks_the_start(pr, monkeypatch, content):
    clean_probe(pr, monkeypatch)
    rewrite_dep(pr, "clone_receipt", content)
    assert "clone_receipt_is_not_a_verified_pass" in pr.mod.preconditions()


@pytest.mark.parametrize("content", [{"verdict": "PASS", "dataset_version": BAD}, {"verdict": "FAIL", "dataset_version": V1}, {"verdict": "FAIL"}], ids=["pass_not_fail", "other_version", "no_version"])
def test_a_quarantine_report_that_is_not_the_fail_of_the_quarantined_version_blocks_the_start(pr, monkeypatch, content):
    clean_probe(pr, monkeypatch)
    rewrite_dep(pr, "quarantine_verify_report", content)
    assert "quarantine_report_is_not_the_fail_of_the_quarantined_version" in pr.mod.preconditions()


def test_unparsable_dependency_content_blocks_the_start(pr, monkeypatch):
    clean_probe(pr, monkeypatch)
    pr.deps["clone_receipt"].write_text("{nope", encoding="utf-8")
    pr.mod.CFG["deps"]["clone_receipt"]["sha256"] = hashlib.sha256(pr.deps["clone_receipt"].read_bytes()).hexdigest()
    assert any(p.startswith("dependency_content_unreadable") for p in pr.mod.preconditions())


# =========================================================================== tuoi Q0 tinh den phase ghi CUOI
def test_the_allowed_q0_age_at_start_is_max_age_minus_preceding_worst_case_minus_margin(pr):
    assert pr.mod.q0_allowed_age_s(pr.mod.CFG["plans"]["init"]) == ALLOWED_AGE == 2700.0


@pytest.mark.parametrize("age_s,ok", [(0, True), (300, True), (2500, True), (2600, True), (2800, False), (3300, False), (3590, False)], ids=["fresh", "five_min", "inside", "edge_inside", "too_old", "old", "almost_expired"])
def test_a_q0_receipt_must_leave_room_for_the_last_write_phase_at_the_start(tmp_path, monkeypatch, age_s, ok):
    env = Hi(tmp_path, monkeypatch, q0_age_s=float(age_s))
    assert env.load() == []
    clean_probe(env, monkeypatch)
    assert (not any(p.startswith("q0_receipt_invalid_or_too_old") for p in env.mod.preconditions())) is ok


@pytest.mark.parametrize("name,mutate", [
    ("kind", dict(kind="other")), ("observer_pin", dict(observer_script_sha256="0" * 64)), ("sql_set_pin", dict(sql_set_sha256="0" * 64)), ("model_pin", dict(quiescence_model_sha256="0" * 64)),
    ("selftest_false", dict(selftest={**{k: True for k in SELFTEST_KEYS}, "removed_after_close": False})), ("no_server", dict(server={})), ("created_malformed", dict(created_at_utc="yesterday"))])
def test_an_invalid_q0_receipt_is_refused_by_load_q0_receipt_even_with_the_right_hash(tmp_path, monkeypatch, name, mutate):
    env = Hi(tmp_path, monkeypatch)
    env.q0_path.write_bytes(json.dumps(env.make_q0(300.0, **mutate), indent=2, sort_keys=True).encode("utf-8"))
    frozen = json.loads(env.freeze_path.read_text(encoding="utf-8"))
    frozen["q0_receipt"]["sha256"] = hashlib.sha256(env.q0_path.read_bytes()).hexdigest()
    path, digest = env.write_freeze(frozen, "requalified.json")
    assert env.mod.load_freeze(path, digest) == []
    clean_probe(env, monkeypatch)
    assert any(p.startswith("q0_receipt_invalid_or_too_old") for p in env.mod.preconditions())


def gate(pr, offset_s):
    pr.started(wall_seconds=40000)
    pr.evidence()
    started = {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=40000))}
    pr.world.t = pr.world.t0 + offset_s
    return pr.mod.plan_gate_reason("init", started, first=False, stage="unit")


@pytest.mark.parametrize("offset,ok", [(2399, True), (2400, True), (2401, False), (2500, False)])
def test_the_gate_age_boundary_is_exactly_2700_s(pr, offset, ok):                                          # Q0 tao luc NOW-300 s => tuoi = offset + 300
    reason = gate(pr, offset)
    assert (reason is None) is ok and (ok or reason.startswith("q0_receipt_unusable_for_the_last_write_phase"))


def test_a_receipt_inside_the_bound_at_the_first_gate_that_check_only_pushes_over_gets_zero_actual_invocation(tmp_path, monkeypatch):
    env = Hi(tmp_path, monkeypatch, q0_age_s=2500.0)                                                      # tuoi cong 1 = 2500 + lead 90 = 2590 <= 2700; sau check-only 115 s = 2705 > 2700
    assert env.load() == []
    env.started(wall_seconds=20000)
    env.evidence()
    calls = env.entry(check_elapsed_s=115.0)
    assert env.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "before_invocation" in env.mod.OP_LOG.read_text(encoding="utf-8")
    assert "q0_receipt_unusable_for_the_last_write_phase" in env.mod.OP_LOG.read_text(encoding="utf-8")


def test_exactly_2700_s_at_the_second_gate_still_runs(tmp_path, monkeypatch):
    env = Hi(tmp_path, monkeypatch, q0_age_s=2495.0)                                                      # cong 1: 2495+90 = 2585; cong 2: +115 = 2700 chinh xac
    assert env.load() == []
    env.started(wall_seconds=20000)
    env.evidence()
    calls = env.entry(check_elapsed_s=115.0)
    assert env.mod.cmd_plans() == 0 and [a[-1] == "--check-only" for a, _ in calls] == [True, False]


def test_a_q0_receipt_file_replaced_by_another_valid_receipt_after_the_first_gate_refuses_the_invocation(pr):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    real = pr.mod.subprocess.run

    def swap(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] == "--check-only":
            pr.q0_path.write_bytes(json.dumps(pr.make_q0(200.0), indent=2, sort_keys=True).encode("utf-8"))
        return result
    pr.monkeypatch.setattr(pr.mod.subprocess, "run", swap)
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "q0_receipt_sha256_mismatch" in pr.mod.OP_LOG.read_text(encoding="utf-8")


# =========================================================================== plan thanh cong + cong
def test_the_plan_runs_one_check_only_then_one_invocation_with_exact_argv_timeouts_byte_exact_outputs_and_a_fresh_h2_dir(pr):
    record: list = []
    calls = ready(pr, record=record)
    assert pr.mod.cmd_plans() == 0
    assert [a[-1] == "--check-only" for a, _ in calls] == [True, False] and record == [{"h2_reports_dir_exists": True, "reports_exist": [False, False]}]          # thu muc bao cao tao ngay truoc invocation, bao cao chua ton tai
    for argv, kw in calls:
        assert argv == pr.mod.approved_argv("init") + (["--check-only"] if argv[-1] == "--check-only" else []) and kw["env"] == pr.mod.ENV6 and kw["cwd"] == str(pr.mod.ROOT)
        assert kw["capture_output"] is True and kw["text"] is False
    assert calls[0][1]["timeout"] == 120 and calls[1][1]["timeout"] == 1380 + 120
    written = (pr.run / "hi_init_entry_stdout.json").read_bytes()
    assert written.startswith(b"line1\r\n") and hashlib.sha256(written).hexdigest() in pr.mod.OP_LOG.read_text(encoding="utf-8")
    assert pr.world.stopped() and pr.mod.STOP.exists() and all(r.exists() for r in pr.reports)


def test_the_lead_wait_happens_before_the_check_only(pr):
    ready(pr)
    pr.mod.cmd_plans()
    assert pr.world.t - pr.world.t0 >= 90 and "waiting_lead" in pr.world.op_events()


def test_without_a_started_publisher_nothing_runs(pr):
    calls = pr.entry()
    assert pr.mod.cmd_plans() == 2 and calls == [] and "stop_not_needed" in pr.world.op_events() and not pr.mod.H2_DIR.exists()


@pytest.mark.parametrize("kwargs", [dict(check_ok=False), dict(hash_override="f" * 64), dict(plan_hash_override="e" * 64), dict(check_ok=True, check_rc=1)], ids=["not_ok", "invocation_hash", "plan_hash", "rc_nonzero"])
def test_a_failing_check_only_means_no_invocation_and_no_h2_dir(pr, kwargs):
    pr.started()
    pr.evidence()
    calls = pr.entry(**kwargs)
    assert pr.mod.cmd_plans() == 2 and len(calls) == 1 and pr.world.stopped() and not pr.mod.H2_DIR.exists()


def test_gates_are_rechecked_right_before_the_invocation_for_publisher_time(pr):
    pr.started(wall_seconds=90 + 1500 + 100)
    pr.evidence()
    calls = pr.entry(check_elapsed_s=115.0)
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "insufficient_publisher_time" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_the_start_deadline_is_a_gate_of_the_actual_first_invocation(pr):
    pr.started(wall_seconds=9000)
    pr.evidence()
    calls = pr.entry(check_elapsed_s=115.0)
    pr.mod.CFG["windows"]["start_deadline_utc"] = NOW + dt.timedelta(seconds=100)
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "start_deadline_passed" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("kwargs", [dict(active_runs=1), dict(read_only_verified=False), dict(age_s=500), dict(generator_sha256="f" * 64)])
def test_busy_or_stale_evidence_blocks_the_plan_at_the_first_gate(pr, kwargs):
    pr.started()
    pr.evidence(**kwargs)
    calls = pr.entry()
    assert pr.mod.cmd_plans() == 2 and calls == [] and pr.world.stopped()


def test_evidence_that_turns_busy_during_check_only_blocks_the_invocation(pr, monkeypatch):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    real = pr.mod.subprocess.run

    def run_then_busy(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] == "--check-only":
            pr.evidence(active_runs=1)
        return result
    monkeypatch.setattr(pr.mod.subprocess, "run", run_then_busy)
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"]


def test_a_publisher_that_stops_or_a_hard_deadline_that_passes_blocks_the_plan(pr):
    pr.started()
    pr.world.append({"event": "stopped", "verified_dead": True, "reason": "wall_deadline"})
    pr.evidence()
    calls = pr.entry()
    started = {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=4000))}
    assert pr.mod.run_plan("init", started, first=True) == 2 and calls == []


def test_the_hard_not_after_limit_refuses_with_its_own_reason(pr):
    pr.evidence()
    calls = pr.entry()
    pr.mod.CFG["windows"]["hard_not_after_utc"] = NOW - dt.timedelta(seconds=1)
    started = {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=4000))}
    assert pr.mod.run_plan("init", started, first=True) == 2 and calls == [] and "hard_not_after_passed" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_an_h2_directory_created_after_the_start_refuses_the_invocation_and_is_never_overwritten(pr):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    real = pr.mod.subprocess.run

    def create_during_check(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] == "--check-only":
            pr.report_dir.mkdir(parents=True)
        return result
    pr.monkeypatch.setattr(pr.mod.subprocess, "run", create_during_check)
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "h2_reports_dir_unavailable" in pr.mod.OP_LOG.read_text(encoding="utf-8")


# =========================================================================== sau invocation: chuoi receipt hon hop + bao cao verify
RECEIPT_TAMPER = [
    ("foreign_plan", {"approved_plan_sha256": "f" * 64}), ("wrong_prev", {"prev_receipt_sha256": "0" * 64}), ("wrong_argv", {"argv_sha256": "e" * 64}), ("wrong_spec", {"spec_sha256": "e" * 64}),
    ("wrong_seq", {"seq": 9}), ("wrong_entry", {"entrypoint_sha256": "7" * 64}),
    ("timed_out", {"launch": {"exit_code": 0, "timed_out": True, "killed_tree": False, "aborted_by_gate": None, "contained": True}}),
    ("killed_tree", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": True, "aborted_by_gate": None, "contained": True}}),
    ("not_contained", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": False}}),
    ("aborted_by_gate", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": "crawler", "contained": True}}),
    ("nonzero_exit", {"launch": {"exit_code": 3, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": True}}),
    ("verdict", {"verdict": "QUARANTINE"}), ("reasons", {"reasons": ["x"]}), ("wrong_phase", {"phase": "other"})]


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("name,tamper", RECEIPT_TAMPER, ids=[n for n, _ in RECEIPT_TAMPER])
def test_a_receipt_that_is_not_the_genuine_runner_shape_is_a_failure_in_any_phase(pr, phase, name, tamper):
    pr.started()
    pr.evidence()
    pr.entry(receipt_tamper={phase: tamper})
    assert pr.mod.cmd_plans() == 5 and "plan_not_pass" in pr.world.op_events() and pr.world.stopped()


@pytest.mark.parametrize("phase", ["ph4_init_h1", "ph4_init_h3"])
@pytest.mark.parametrize("tamper", [{"kind": "readonly"}, {"q0_receipt_sha256": None}, {"q0_receipt_sha256": "9" * 64}], ids=["kind", "q0_missing", "q0_other"])
def test_a_write_phase_must_be_write_with_the_q0_pin(pr, phase, tamper):
    pr.started()
    pr.evidence()
    pr.entry(receipt_tamper={phase: tamper})
    assert pr.mod.cmd_plans() == 5


@pytest.mark.parametrize("phase", ["ph4_init_h1", "ph4_init_h3"])
@pytest.mark.parametrize("quiescence", [{"status": "unknown"}, {}, None], ids=["unknown", "empty", "none"])
def test_a_write_phase_without_a_verified_quiescence_is_a_failure(pr, phase, quiescence):
    pr.started()
    pr.evidence()
    pr.entry(quiescence={phase: quiescence})
    assert pr.mod.cmd_plans() == 5 and "quiescence_not_verified" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("phase", ["ph4_verify_h1", "ph4_verify_h3"])
@pytest.mark.parametrize("tamper", [{"kind": "write"}, {"q0_receipt_sha256": "9" * 64}], ids=["kind", "q0_present"])
def test_a_readonly_phase_must_be_readonly_without_a_q0_pin(pr, phase, tamper):
    pr.started()
    pr.evidence()
    pr.entry(receipt_tamper={phase: tamper})
    assert pr.mod.cmd_plans() == 5


@pytest.mark.parametrize("phase", ["ph4_verify_h1", "ph4_verify_h3"])
@pytest.mark.parametrize("why,report", [
    ("verdict_fail", {"verdict": "FAIL", "dataset_version": None, "database": TARGET}), ("wrong_version", {"verdict": "PASS", "dataset_version": BAD, "database": TARGET}),
    ("wrong_database", {"verdict": "PASS", "dataset_version": None, "database": "other_db"}), ("not_json", b"{partial")], ids=["fail", "version", "database", "garbage"])
def test_a_verify_report_that_is_not_a_pass_for_this_version_and_database_is_a_failure(pr, phase, why, report):
    if isinstance(report, dict):
        report = {**report, "dataset_version": report["dataset_version"] or (V1 if phase.endswith("_h1") else V3)}
    pr.started()
    pr.evidence()
    pr.entry(report_override={phase: report})
    assert pr.mod.cmd_plans() == 5


@pytest.mark.parametrize("phase", ["ph4_verify_h1", "ph4_verify_h3"])
@pytest.mark.parametrize("outputs", [{}, {"x": "0" * 64}], ids=["empty", "other"])
def test_a_verify_receipt_whose_outputs_do_not_bind_the_report_bytes_is_a_failure(pr, phase, outputs):
    pr.started()
    pr.evidence()
    pr.entry(outputs_override={phase: outputs})
    assert pr.mod.cmd_plans() == 5 and "report_hash_differs_from_receipt_outputs" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("phase", ["ph4_verify_h1", "ph4_verify_h3"])
def test_a_missing_verify_report_is_a_failure(pr, phase):
    pr.started()
    pr.evidence()
    pr.entry(report_override={phase: None})
    assert pr.mod.cmd_plans() == 5 and "verify_report_unreadable" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("kwargs", [
    dict(bound_tamper={"entrypoint_sha256": "7" * 64}), dict(bound_tamper={"phases": ["ph4_init_h1"]}), dict(bound_tamper={"approved_plan_sha256": "f" * 64}), dict(bound_tamper={"computed_plan_sha256": "f" * 64}),
    dict(bound_tamper={"kind": "other"}), dict(summary_tamper={"invocation_sha256": "0" * 64}), dict(summary_tamper={"ok": False}), dict(summary_tamper={"phases": []}), dict(extra_files=["QUARANTINE.json"]), dict(rc=3)])
def test_plan_bound_summary_stray_files_and_nonzero_exit_are_failures(pr, kwargs):
    pr.started()
    pr.evidence()
    pr.entry(**kwargs)
    assert pr.mod.cmd_plans() != 0 and pr.world.stopped()


def test_a_summary_with_swapped_rows_is_a_failure(pr):
    pr.started()
    pr.evidence()
    pr.entry()
    real = pr.mod.subprocess.run

    def swapped(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] != "--check-only":
            summary = json.loads(result.stdout.decode("utf-8").strip().splitlines()[-1])
            summary["phases"].reverse()
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(summary).encode() + b"\r\n", stderr=b"")
        return result
    pr.monkeypatch.setattr(pr.mod.subprocess, "run", swapped)
    assert pr.mod.cmd_plans() == 5 and "stdout_summary_phases" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_a_plan_file_with_an_extra_or_reordered_phase_is_never_a_pass(pr):
    ready(pr)
    assert pr.mod.cmd_plans() == 0
    plan_file = Path(pr.mod.CFG["plans"]["init"]["plan_file"])
    original = plan_file.read_text(encoding="utf-8")
    doc = json.loads(original)
    doc["document"]["specs"].append(dict(doc["document"]["specs"][0], name="extra_phase"))
    plan_file.write_text(json.dumps(doc), encoding="utf-8")
    assert pr.mod.plan_receipts_pass("init") == ["plan_file_phase_order_differs_from_expected"]
    doc = json.loads(original)
    doc["document"]["specs"].reverse()
    plan_file.write_text(json.dumps(doc), encoding="utf-8")
    assert pr.mod.plan_receipts_pass("init") == ["plan_file_phase_order_differs_from_expected"]


def test_a_runner_that_differs_from_the_freeze_pin_stops_the_plan_at_the_first_gate_because_the_q0_check_needs_it(pr):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    pr.mod.CFG["pins"]["runner"]["sha256"] = "0" * 64
    pr.mod._RUNNER = None
    assert pr.mod.cmd_plans() == 2 and calls == [] and "runner_sha256_differs_from_freeze_pin" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_the_good_fixture_is_accepted_end_to_end(pr):
    ready(pr)
    assert pr.mod.cmd_plans() == 0 and pr.mod.plan_receipts_pass("init") == []


@pytest.mark.parametrize("exc", [KeyboardInterrupt, RuntimeError, subprocess.TimeoutExpired])
def test_exception_or_cancel_in_the_invocation_stops_the_publisher_and_propagates(pr, exc):
    pr.started()
    pr.evidence()
    pr.entry(invoke_exc=exc("x") if exc is not subprocess.TimeoutExpired else subprocess.TimeoutExpired("entry", 1500))
    with pytest.raises(exc):
        pr.mod.cmd_plans()
    assert pr.mod.STOP.exists() and pr.world.stopped()


def test_unverified_cleanup_makes_a_passing_run_exit_nonzero(pr):
    ready(pr)
    pr.world.verified_dead = False
    with pytest.raises(SystemExit) as info:
        pr.mod.cmd_plans()
    assert info.value.code == 3


def test_a_failing_diagnostic_log_never_skips_the_stop(pr, monkeypatch):
    pr.started()
    real = pr.mod.log
    monkeypatch.setattr(pr.mod, "log", lambda kind, **f: (_ for _ in ()).throw(OSError("disk")) if kind == "stop_required_by_terminal_path" else real(kind, **f))
    assert pr.mod.ensure_stopped("test") == 0 and pr.mod.STOP.exists() and pr.world.stopped()


def test_start_handshake_timeout_requests_stop_and_confirms_refusal(pr):
    class Sup:
        def poll(self):
            return None
    pr.mod.SUP_STDOUT.write_bytes(b"")
    pr.world.refuse_on_stop_when_pending = True
    assert pr.mod.start_handshake(Sup()) == 2 and pr.mod.STOP.exists()


class FakeSup:
    pid = 77

    def poll(self):
        return None


def test_cmd_start_launches_the_supervisor_with_exactly_the_frozen_profile_pins_and_window(pr, monkeypatch):
    clean_probe(pr, monkeypatch)
    launched = []
    monkeypatch.setattr(pr.mod.subprocess, "Popen", lambda command, **kw: (launched.append((command, kw)), FakeSup())[1])
    pr.world.timeline.append((1, {"event": "started", "pid": 1, "at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=4000))}))
    assert pr.mod.cmd_start() == 0 and len(launched) == 1
    command, kw = launched[0]
    cfg, mod = pr.mod.CFG, pr.mod
    expected = [mod.PY_SUP, str(mod.SUPERVISOR), "--profile", cfg["profile"]["path"], "--expected-profile-sha256", cfg["profile"]["sha256"], "--expected-source-sha256", cfg["pins"]["source"]["sha256"],
                "--expected-interpreter-sha256", cfg["pins"]["interpreter"]["sha256"], "--not-before-utc", pr.windows["not_before_utc"], "--start-deadline-utc", pr.windows["start_deadline_utc"],
                "--hard-not-after-utc", pr.windows["hard_not_after_utc"], "--max-pool-size", "5", "--supervisor-log", str(mod.SUP_LOG), "--publisher-log", str(mod.PUB_LOG), "--stop-file", str(mod.STOP)]
    assert command == expected and kw["env"] == mod.ENV6 and kw["cwd"] == str(mod.ROOT) and kw["stdin"] == subprocess.DEVNULL and kw["stderr"] == subprocess.STDOUT and kw["creationflags"] == 0x08000000
    body = mod.PREFLIGHT.read_text(encoding="utf-8")
    assert "shell" not in kw and pr.freeze_sha in body and BAD in body and str(mod.N1_POLICY_FILE) in body and str(ALLOWED_AGE) in body and pr.mod.CFG["q0"]["sha256"] in body


def test_cmd_start_refuses_without_launching_anything_when_a_precondition_fails(pr, monkeypatch):
    clean_probe(pr, monkeypatch, pids=[9])
    monkeypatch.setattr(pr.mod.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not launch")))
    assert pr.mod.cmd_start() == 2 and not pr.mod.PREFLIGHT.exists() and not pr.mod.SUP_STDOUT.exists()


@pytest.mark.parametrize("kwargs", [dict(active_runs=1), dict(active_items=2), dict(read_only_verified=False), dict(age_s=500), dict(generator_sha256="f" * 64)])
def test_cmd_evidence_stops_the_started_publisher_when_evidence_is_busy_stale_or_foreign(pr, kwargs):
    pr.started()
    pr.evidence(**kwargs)
    assert pr.mod.cmd_evidence() == 1 and pr.mod.STOP.exists() and pr.world.stopped()


def test_cmd_evidence_with_clean_evidence_keeps_the_publisher_running(pr):
    pr.started()
    pr.evidence()
    assert pr.mod.cmd_evidence() == 0 and not pr.world.stopped() and not pr.mod.STOP.exists()


def test_cmd_evidence_without_a_running_publisher_is_not_applicable_and_cleans_up(pr):
    pr.evidence()
    assert pr.mod.cmd_evidence() == 1 and "evidence_not_applicable" in pr.world.op_events()


def test_a_stop_that_never_produces_a_verified_dead_publisher_is_nonzero(pr):
    pr.started()
    pr.world.stopped_after_stop_file = False
    assert pr.mod.ensure_stopped("x") == 3 and "publisher_stop_timeout" in pr.world.op_events()
    pr.world.stopped_after_stop_file = True
    pr.world.verified_dead = False
    assert pr.mod.ensure_stopped("y") == 3


def test_ensure_stopped_does_nothing_for_a_publisher_that_was_refused_before_spawning(pr):
    pr.world.append({"event": "refused", "reasons": ["outside_window"], "stage": "preflight"})
    assert pr.mod.publisher_state() == "refused" and pr.mod.ensure_stopped("x") == 0 and not pr.mod.STOP.exists() and "stop_not_needed" in pr.world.op_events()


def test_collect_lists_the_files_of_the_run_root_only(pr):
    ready(pr)
    pr.mod.cmd_plans()
    assert pr.mod.cmd_collect() == 0
    logged = [json.loads(x) for x in pr.mod.OP_LOG.read_text(encoding="utf-8").splitlines() if '"event": "collected"' in x]
    assert logged and "hi_init_entry_stdout.json" in logged[-1]["files"] and any(name.startswith("h2\\reports\\verify_") for name in logged[-1]["files"])


# =========================================================================== driver
@pytest.fixture
def driver():
    return load(DRIVER, "run_heavy_init_workflow_under_test")


class FakeOp:
    __file__ = str(OPERATOR)

    def __init__(self, run: Path, **rcs):
        self.RUN = run
        self.calls, self.logged = [], []
        self.rcs = {"load_freeze": [], "preconditions": [], "cmd_start": 0, "cmd_evidence": 0, "cmd_plans": 0, "ensure_stopped": 0, "cmd_collect": 0}
        self.rcs.update(rcs)

    def _do(self, name):
        self.calls.append(name)
        value = self.rcs[name]
        if isinstance(value, BaseException):
            raise value
        return value

    def load_freeze(self, path, sha): return self._do("load_freeze")
    def preconditions(self): return self._do("preconditions")
    def cmd_start(self): return self._do("cmd_start")
    def cmd_evidence(self): return self._do("cmd_evidence")
    def cmd_plans(self): return self._do("cmd_plans")
    def ensure_stopped(self, reason): return self._do("ensure_stopped")
    def cmd_collect(self): return self._do("cmd_collect")
    def log(self, kind, **fields): self.logged.append(kind)
    def stamp(self): return "2026-10-10T13:00:00Z"


def go(driver, tmp_path, **rcs):
    fake = FakeOp(tmp_path, **rcs)
    code = driver.run_workflow(fake, freeze_path=tmp_path / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: p.write_text("ack"), result_path=tmp_path / "result.json", ack_path=tmp_path / "ack.md", pre_checks=False)
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) if (tmp_path / "result.json").exists() else None
    return code, fake, result


def test_driver_order_cleanup_and_collect(driver, tmp_path):
    code, fake, result = go(driver, tmp_path)
    assert code == 0 and fake.calls == ["load_freeze", "preconditions", "cmd_start", "cmd_evidence", "cmd_plans", "ensure_stopped", "cmd_collect"] and result["exit_code"] == 0


def test_driver_refuses_a_bad_freeze_before_acknowledging(driver, tmp_path):
    code, fake, result = go(driver, tmp_path, load_freeze=["freeze_sha256_mismatch"])
    assert code == 2 and fake.calls == ["load_freeze"] and result is None and not (tmp_path / "ack.md").exists()


@pytest.mark.parametrize("failing,rc,expected_calls", [("cmd_start", 2, ["load_freeze", "preconditions", "cmd_start"]), ("cmd_evidence", 1, ["load_freeze", "preconditions", "cmd_start", "cmd_evidence"])])
def test_driver_stops_after_a_nonzero_stage_but_cleans_up(driver, tmp_path, failing, rc, expected_calls):
    code, fake, result = go(driver, tmp_path, **{failing: rc})
    assert code == rc and fake.calls == [*expected_calls, "ensure_stopped", "cmd_collect"]


def test_driver_precondition_problems_and_plan_failure(driver, tmp_path):
    code, fake, _ = go(driver, tmp_path, preconditions=["crawler_process_alive: [1]"])
    assert code == 2 and "cmd_start" not in fake.calls and fake.calls[-2:] == ["ensure_stopped", "cmd_collect"]
    other = tmp_path / "o"
    other.mkdir()
    code, _, result = go(driver, other, cmd_plans=5)
    assert code == 5 and result["plans"] == 5


@pytest.mark.parametrize("where,exc", [("cmd_start", RuntimeError("x")), ("cmd_evidence", OSError("x")), ("cmd_plans", KeyboardInterrupt()), ("cmd_plans", SystemExit(3))])
def test_driver_exceptions_cancel_system_exit_still_cleanup(driver, tmp_path, where, exc):
    code, fake, _ = go(driver, tmp_path, **{where: exc})
    assert code != 0 and fake.calls[-2:] == ["ensure_stopped", "cmd_collect"]


def test_driver_cleanup_and_collect_failures_are_nonzero_without_masking(driver, tmp_path):
    for index, (rcs, expected) in enumerate([({"ensure_stopped": 3}, 3), ({"cmd_collect": 1}, 4), ({"cmd_collect": OSError("h")}, 4), ({"cmd_plans": 5, "cmd_collect": 1}, 5), ({"ensure_stopped": 3, "cmd_collect": 1}, 3)]):
        folder = tmp_path / f"c{index}"
        folder.mkdir()
        assert go(driver, folder, **rcs)[0] == expected


def test_driver_cleanup_exception_is_recorded_and_still_collects(driver, tmp_path):
    code, fake, result = go(driver, tmp_path, ensure_stopped=OSError("x"))
    assert code == 3 and fake.calls[-2:] == ["ensure_stopped", "cmd_collect"] and result["cleanup_exception"] == "OSError"


def test_driver_result_write_failure_cannot_turn_a_failure_into_success(driver, tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    fake = FakeOp(folder)
    code = driver.run_workflow(fake, freeze_path=folder / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: p.write_text("ack"), result_path=folder / "missing_dir" / "result.json", ack_path=folder / "ack.md", pre_checks=False)
    assert code != 0


def test_driver_refuses_on_operator_pin_drift_or_a_previous_run(driver, tmp_path):
    fake = FakeOp(tmp_path)
    kwargs = dict(freeze_path=tmp_path / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: p.write_text("a"), result_path=tmp_path / "r.json", ack_path=tmp_path / "a.md")
    driver.OPERATOR_HEAVY_INIT_SHA256 = "0" * 64
    assert driver.run_workflow(fake, **kwargs) == 2 and fake.calls == []
    driver.OPERATOR_HEAVY_INIT_SHA256 = hashlib.sha256(OPERATOR.read_bytes()).hexdigest()
    (tmp_path / "a.md").write_text("already")
    assert driver.run_workflow(fake, **kwargs) == 2 and fake.calls == ["load_freeze"]


def test_driver_derives_ack_and_result_paths_from_the_freeze_run_root_after_loading_it(driver, tmp_path):
    fake = FakeOp(tmp_path)
    acks = []
    code = driver.run_workflow(fake, freeze_path=tmp_path / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: (acks.append(p), p.write_text("ack")), pre_checks=False)
    assert code == 0 and acks == [tmp_path / "hi_dossier_outer_cleanup_ack.md"] and (tmp_path / "hi_workflow_result.json").exists()
    assert not (tmp_path / "bld_workflow_result.json").exists() and not (tmp_path / "q0v_workflow_result.json").exists()


@pytest.mark.skipif("HEAVY_INIT_OPERATOR_UNDER_TEST" in os.environ, reason="mutation run uses a different operator file")
def test_driver_pin_constant_equals_the_sha256_of_the_real_operator(driver):
    assert driver.OPERATOR_HEAVY_INIT_SHA256 == hashlib.sha256((RUN_REAL / "operator_heavy_init.py").read_bytes()).hexdigest()


def test_driver_ack_records_pins_and_the_cleanup_contract(driver, pr, tmp_path):
    out = tmp_path / "ack.md"
    driver.write_ack(pr.mod, out, driver_path=str(DRIVER))
    body = out.read_text(encoding="utf-8")
    assert pr.freeze_sha in body and hashlib.sha256(DRIVER.read_bytes()).hexdigest() in body and "ensure_stopped" in body and "collect" in body and str(pr.run) in body and BAD in body
    assert hashlib.sha256(OPERATOR.read_bytes()).hexdigest() in body


# =========================================================================== survivor mutation (131a): cac phong ve sau invocation
def test_plan_receipts_pass_re_checks_the_phase_kinds_against_the_table_after_the_run(pr):
    ready(pr)
    assert pr.mod.cmd_plans() == 0 and pr.mod.plan_receipts_pass("init") == []
    pr.monkeypatch.setitem(pr.mod.PHASE_KINDS, "ph4_init_h1", "readonly")                                          # bang kind thay doi (plan va receipt van nhat quan voi nhau)
    problems = pr.mod.plan_receipts_pass("init")
    assert any("unexpected_phase_kind" in p for p in problems)


def test_an_existing_report_file_found_after_the_h2_dir_is_created_refuses_the_invocation(pr):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    pr.mod.CFG["plans"]["init"]["h2_reports"] = [str(pr.req_a), str(pr.reports[1])]                                  # req_a ton tai san: mo phong bao cao co truoc
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "h2_report_file_already_exists" in pr.mod.OP_LOG.read_text(encoding="utf-8")
