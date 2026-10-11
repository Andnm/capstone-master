"""operator_build + run_build_workflow (BUILD: MOT plan, HAI phase GHI `ph5_build_h1`/`ph5_build_h3`, Q0 pin, thu muc dataset niem phong): freeze voi run-root tham so (chi outputs\\repair-run-build-rNNN), Q0 receipt kiem bang
`load_q0_receipt`, cong tuoi Q0 tinh den phase ghi CUOI, bang chung H2 + `required_files`, chuoi receipt hai phase ghi + niem phong thu muc dataset tinh lai, cleanup finally. Toan bo fake: khong SQL, khong process that.
Hash runner/observer lay tu CHINH tep da ghim (exec-hash)."""
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
OPERATOR = Path(os.environ.get("BUILD_OPERATOR_UNDER_TEST", str(RUN_REAL / "operator_build.py")))                   # ghi de CHI de chay mutation harness
DRIVER = RUN_REAL / "run_build_workflow.py"
OBSERVER = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools\repair_db_observer_v1.py")
NOW = dt.datetime(2026, 10, 9, 14, 0, tzinfo=dt.timezone.utc)
SELFTEST_KEYS = ["innodb_trx_readable", "pid_attribute_visible", "removed_after_close", "visible_db_null_session", "visible_other_session"]
V1, V3 = "ds_20261009_nf1", "ds_20261009_nf3"
H2_SHA = "d" * 64
PRECEDING = 1900.0                                                                                              # q0_preceding_worst_case_s trong freeze; allowed age = 3600 - 1900 - 120 = 1580
ALLOWED_AGE = 3600.0 - PRECEDING - 120.0


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def text(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class Build:
    def __init__(self, tmp_path, monkeypatch, name="repair-run-build-r122", q0_age_s=300.0):
        self.mod = load(OPERATOR, "operator_build_under_test")
        mod = self.mod
        self.root = tmp_path / "root"
        self.outputs = self.root / "outputs"
        self.outputs.mkdir(parents=True)
        monkeypatch.setattr(mod, "ROOT", self.root)
        monkeypatch.setattr(mod, "DATASET_ROOT", self.outputs / "datasets")
        self.run = self.outputs / name
        self.run.mkdir()
        self.dirs = [self.outputs / "datasets" / V1, self.outputs / "datasets" / V3]
        self.world = FakeWorld(mod, self.run, monkeypatch)
        monkeypatch.setattr(mod, "now", lambda: NOW + dt.timedelta(seconds=self.world.t - self.world.t0))
        self.tmp, self.monkeypatch = tmp_path, monkeypatch
        self.windows = {"not_before_utc": text(NOW - dt.timedelta(minutes=5)), "start_deadline_utc": text(NOW + dt.timedelta(minutes=55)), "hard_not_after_utc": text(NOW + dt.timedelta(hours=6))}
        self.q0_path = self.outputs / "repair-run-q0b-r121" / "q0" / "q0_receipt.json"
        self.q0_path.parent.mkdir(parents=True)
        self.q0_doc = self.make_q0(q0_age_s)
        self.q0_path.write_bytes(json.dumps(self.q0_doc, indent=2, sort_keys=True).encode("utf-8"))
        self.freeze = self.make_freeze()
        self.freeze_path, self.freeze_sha = self.write_freeze(self.freeze)

    # ---- fixtures du lieu
    def make_q0(self, age_s, **mutate):
        observer = load(OBSERVER, "observer_for_build_tests")
        result = {**{k: True for k in SELFTEST_KEYS}, "server": {"server_uuid": "92d7a052-0723-11f1-8a6b-a82bdd5416bb", "uptime_s": 1000.0, "version": "8.0.45"},
                  "details": {"app_user": "root", "databases": ["hotel_price_intel", "warehouse_20261004_3src"], "databases_truncated": False, "errors": [], "ids_increase": True, "probe_session_id": 743,
                              "successive_ids": [744, 745]}}
        doc = observer.q0_receipt_document(result, now_utc=NOW - dt.timedelta(seconds=age_s))
        doc.update(mutate)
        return doc

    def file(self, name, content=b"x"):
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def make_h2(self):
        h2 = self.tmp / "h2" / "plan"
        h2.mkdir(parents=True)
        (h2 / "PLAN_BOUND.json").write_text(json.dumps({"kind": "plan_bound", "computed_plan_sha256": H2_SHA, "approved_plan_sha256": H2_SHA}), encoding="utf-8")
        for name in ("receipt_01_ph4_init_h1.json", "receipt_02_ph4_verify_h1.json", "receipt_03_ph4_init_h3.json", "receipt_04_ph4_verify_h3.json"):
            (h2 / name).write_text(json.dumps({"n": name}), encoding="utf-8")
        reports = []
        for version in (V1, V3):
            reports.append(self.file(f"h2/reports/verify_{version}.json", b"report"))
        return h2, reports

    def make_freeze(self):
        mod, run = self.mod, self.run
        (run / "build" / "plan").mkdir(parents=True)
        self.h2_dir, self.reports = self.make_h2()
        req_a = self.file("req_a.txt", b"input a")
        req_b = self.file("req_b.txt", b"input b only for h3")                                                   # chi phase h3 ghim tep nay => kiem drift phai duyet MOI spec, khong chi spec dau
        pinned = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (req_a, self.h2_dir / "PLAN_BOUND.json", self.h2_dir / "receipt_02_ph4_verify_h1.json", *self.reports)}
        q0_sha = hashlib.sha256(self.q0_path.read_bytes()).hexdigest()
        extra = {"ph5_build_h1": {}, "ph5_build_h3": {str(req_b): hashlib.sha256(req_b.read_bytes()).hexdigest()}}
        specs = [{"name": name, "kind": "write", "argv": ["py", "build_dataset.py", "--database", "warehouse_dsdev_x", "--dataset-version", version, "--apply"], "cwd": str(self.tmp), "env": {"PATH": "p"},
                  "required_files": {**pinned, **extra[name]}, "deadline_s": 1800.0, "db": {"db_scope": ["warehouse_dsdev_x"]}, "output_dirs": [str(directory)]}
                 for name, version, directory in (("ph5_build_h1", V1, self.dirs[0]), ("ph5_build_h3", V3, self.dirs[1]))]
        plan_sha = "c" * 64
        document = {"specs": specs, "q0_receipt_sha256": q0_sha, "settings": {"q0_max_age_s": 3600.0, "total_budget_s": 4200.0}}
        plan_file = self.file("build.plan.json", json.dumps({"document": document, "plan_sha256": plan_sha}).encode())
        approval = self.file("build_approval.md", b"approval build")
        plans = {"build": {"plan_file": str(plan_file), "plan_file_sha256": hashlib.sha256(plan_file.read_bytes()).hexdigest(), "plan_sha256": plan_sha, "approval": str(approval),
                           "approval_sha256": hashlib.sha256(approval.read_bytes()).hexdigest(), "invocation_sha256": "a" * 64, "plan_dir": str(run / "build" / "plan"), "launcher_logs": str(run / "build" / "logs"),
                           "total_budget_s": 4200.0, "dataset_dirs": [str(d) for d in self.dirs], "q0_max_age_s": 3600.0, "q0_preceding_worst_case_s": PRECEDING}}
        profile = self.file("profile.json", b"profile")
        pins = {name: {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()} for name, path in mod.FIXED_PIN_PATHS.items()}
        self.req_a, self.req_b = req_a, req_b
        return {"kind": "build_freeze", "run_root": str(run), "windows": dict(self.windows),
                "q0_receipt": {"path": str(self.q0_path), "sha256": q0_sha, "created_at_utc": self.q0_doc["created_at_utc"]}, "plans": plans,
                "h2": {"plan_dir": str(self.h2_dir), "plan_sha256": H2_SHA, "reports": [str(r) for r in self.reports]},
                "profile": {"path": str(profile), "sha256": hashlib.sha256(profile.read_bytes()).hexdigest(), "renew_seconds": 4800}, "pins": pins}

    def write_freeze(self, doc, name="build_freeze.json"):
        path = self.tmp / name
        data = json.dumps(doc, sort_keys=True).encode("utf-8")
        path.write_bytes(data)
        return path, hashlib.sha256(data).hexdigest()

    def load(self):
        return self.mod.load_freeze(self.freeze_path, self.freeze_sha)

    def started(self, wall_seconds=6000):
        self.world.append({"event": "started", "pid": 1, "at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=wall_seconds))})

    def evidence(self, **kw):
        state = {"present": True, "age_s": 10, "active_runs": 0, "active_items": 0, "read_only_verified": True, "latest_run_id": 66, "latest_run_status": "completed", "sha256": "x" * 64,
                 "generator_sha256": self.mod.CFG["pins"]["source"]["sha256"]}
        state.update(kw)
        self.monkeypatch.setattr(self.mod, "evidence_state", lambda: dict(state))

    def entry(self, *, check_ok=True, check_rc=None, rc=0, receipt_tamper=None, bound_tamper=None, summary_tamper=None, extra_files=(), check_elapsed_s=0.0, invoke_exc=None, stdout_prefix=b"",
              hash_override=None, quiescence=None, seal_tamper=None, no_dirs=False, after_receipts=None, keys_tamper=None, plan_hash_override=None):
        """`receipt_tamper`/`seal_tamper`/`quiescence`: {phase_index: {...}}; `after_receipts(dirs)`: bien doi thu muc dataset SAU khi ghi receipt (de gia lap 'doi sau khi niem phong')."""
        calls = []
        rr = self.mod.runner()

        def fake_run(argv, **kwargs):
            calls.append((list(argv), kwargs))
            p = self.mod.CFG["plans"]["build"]
            if argv[-1] == "--check-only":
                self.world.t += check_elapsed_s
                payload = {"ok": check_ok, "computed_plan_sha256": plan_hash_override or p["plan_sha256"], "computed_invocation_sha256": hash_override or p["invocation_sha256"]}
                return subprocess.CompletedProcess(argv, check_rc if check_rc is not None else (0 if check_ok else 1), stdout=(json.dumps(payload) + "\n").encode(), stderr=b"")
            if invoke_exc:
                raise invoke_exc
            directory = Path(p["plan_dir"])
            plan_doc = json.loads(Path(p["plan_file"]).read_text(encoding="utf-8"))["document"]
            entry_sha = self.mod.CFG["pins"]["entry"]["sha256"]
            q0_sha = self.mod.CFG["q0"]["sha256"]
            bound = {"kind": "plan_bound", "approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "phases": ["ph5_build_h1", "ph5_build_h3"], **(bound_tamper or {})}
            (directory / "PLAN_BOUND.json").write_text(json.dumps(bound), encoding="utf-8")
            previous, rows = None, []
            for index, (spec, out_dir) in enumerate(zip(plan_doc["specs"], self.dirs)):
                if not no_dirs:
                    out_dir.mkdir(parents=True)
                    (out_dir / "samples.parquet").write_bytes(f"data {index}".encode())
                    (out_dir / "manifest.json").write_text("{}", encoding="utf-8")
                launch = {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": True}
                seal = rr.directory_seal(out_dir) if not no_dirs else None
                seal = {**seal, **(seal_tamper or {}).get(index, {})} if seal else seal
                seals = {str(out_dir): seal} if not (keys_tamper and index in keys_tamper) else {str(out_dir) + "x": seal}
                full = {"phase": spec["name"], "seq": index + 1, "verdict": "PASS", "reasons": [], "kind": "write", "prev_receipt_sha256": previous, "approved_plan_sha256": p["plan_sha256"],
                        "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "spec_sha256": rr.spec_sha256(spec), "argv_sha256": rr.argv_sha256(spec), "q0_receipt_sha256": q0_sha,
                        "launch": launch, "outputs": {}, "output_dirs": seals, "quiescence": {"status": rr.qm.VERIFIED}, **((receipt_tamper or {}).get(index, {}))}
                if quiescence and index in quiescence:
                    full["quiescence"] = quiescence[index]
                (directory / f"receipt_{index + 1:02d}_{spec['name']}.json").write_text(json.dumps(full, indent=2, sort_keys=True), encoding="utf-8")
                previous = rr.sha256_hex(rr.canonical_json(full))
                rows.append({"phase": spec["name"], "reasons": [], "receipt_path": "x", "receipt_sha256": previous, "verdict": "PASS"})
            for extra in extra_files:
                (directory / extra).write_text("x", encoding="utf-8")
            if after_receipts:
                after_receipts(self.dirs)
            summary = {"approved_plan_sha256": p["plan_sha256"], "computed_plan_sha256": p["plan_sha256"], "entrypoint_sha256": entry_sha, "invocation_sha256": p["invocation_sha256"], "ok": True, "phases": rows,
                       **(summary_tamper or {})}
            return subprocess.CompletedProcess(argv, rc, stdout=stdout_prefix + json.dumps(summary).encode() + b"\r\n", stderr=b"")
        self.monkeypatch.setattr(self.mod.subprocess, "run", fake_run)
        return calls


@pytest.fixture
def pr(tmp_path, monkeypatch):
    env = Build(tmp_path, monkeypatch)
    assert env.load() == []
    return env


def ready(pr, **kw):
    pr.started()
    pr.evidence()
    return pr.entry(stdout_prefix=b"line1\r\n", **kw)


# =========================================================================== load_freeze + run-root tham so
def test_a_valid_freeze_loads_and_the_argv_pins_the_q0_receipt_of_the_freeze(pr):
    mod = pr.mod
    p = mod.CFG["plans"]["build"]
    assert mod.approved_argv("build") == [mod.PY_VENV, str(mod.ENTRY), "--approval", p["approval"], "--plan", p["plan_file"], "--plan-dir", p["plan_dir"], "--launcher-logs", p["launcher_logs"],
                                          "--crawl-evidence", str(mod.LIVE), "--q0-receipt", str(pr.q0_path)]
    assert set(mod.CFG["plans"]) == {"build"} and mod.PLAN_ORDER == ("build",) and mod.PLAN_PHASES == {"build": ("ph5_build_h1", "ph5_build_h3")} and mod.RUN == pr.run


def test_every_path_follows_the_run_root_from_the_freeze(pr):
    mod, run = pr.mod, pr.run
    assert (mod.LIVE, mod.SUP_LOG, mod.PUB_LOG, mod.STOP, mod.SUP_STDOUT, mod.OP_LOG, mod.PREFLIGHT, mod.ARCHIVE_DIR) == (
        run / "live_crawl_idle_evidence.json", run / "bld_supervisor.jsonl", run / "bld_publisher.log", run / "bld_STOP", run / "bld_supervisor_stdout.log", run / "bld_operator_log.jsonl",
        run / "bld_dossier_preflight.md", run / "evidence_archive" / "build")


def mutated(pr, fn):
    doc = json.loads(json.dumps(pr.freeze))
    fn(doc)
    path, digest = pr.write_freeze(doc, "mutated.json")
    return pr.mod.load_freeze(path, digest)


def plan_field(key, value):
    return lambda d: d["plans"]["build"].__setitem__(key, value)


@pytest.mark.parametrize("name,fn,expected", [
    ("kind", lambda d: d.__setitem__("kind", "q0_freeze"), "freeze_kind_or_run_root"),
    ("kind_heavy", lambda d: d.__setitem__("kind", "heavy_ci_freeze"), "freeze_kind_or_run_root"),
    ("windows_order", lambda d: d["windows"].__setitem__("start_deadline_utc", d["windows"]["not_before_utc"]), "freeze_windows_order"),
    ("q0_elsewhere", lambda d: d["q0_receipt"].__setitem__("path", r"D:\elsewhere\q0_receipt.json"), "freeze_q0_receipt"),
    ("q0_in_a_build_run_root", lambda d: d["q0_receipt"].__setitem__("path", str(Path(d["run_root"]) / "q0" / "q0_receipt.json")), "freeze_q0_receipt"),
    ("q0_in_a_q0c_run_root", lambda d: d["q0_receipt"].__setitem__("path", str(Path(d["run_root"]).parent / "repair-run-q0c-r121" / "q0" / "q0_receipt.json")), "freeze_q0_receipt"),
    ("q0_under_another_parent", lambda d: d["q0_receipt"].__setitem__("path", str(Path(d["run_root"]).parent.parent / "elsewhere" / "repair-run-q0b-r121" / "q0" / "q0_receipt.json")), "freeze_q0_receipt"),
    ("q0_wrong_file_name", lambda d: d["q0_receipt"].__setitem__("path", str(Path(d["run_root"]).parent / "repair-run-q0b-r121" / "q0" / "other.json")), "freeze_q0_receipt"),
    ("q0_wrong_dir_name", lambda d: d["q0_receipt"].__setitem__("path", str(Path(d["run_root"]).parent / "repair-run-q0b-r121" / "q1" / "q0_receipt.json")), "freeze_q0_receipt"),
    ("q0_bad_sha", lambda d: d["q0_receipt"].__setitem__("sha256", "short"), "freeze_q0_receipt"),
    ("q0_bad_created", lambda d: d["q0_receipt"].__setitem__("created_at_utc", "yesterday"), "freeze_q0_receipt"),
    ("plan_key_missing", lambda d: d["plans"]["build"].pop("dataset_dirs"), "freeze_plan_build"),
    ("plan_relative", lambda d: d["plans"]["build"].__setitem__("approval", "approval.md"), "freeze_plan_build"),
    ("plan_budget_text", plan_field("total_budget_s", "4200"), "freeze_plan_build"),
    ("plan_max_age_bool", plan_field("q0_max_age_s", True), "freeze_plan_build"),
    ("plan_dir_elsewhere", plan_field("plan_dir", r"D:\elsewhere\plan"), "freeze_plan_build_dirs_not_in_run_root"),
    ("launcher_logs_elsewhere", plan_field("launcher_logs", r"D:\elsewhere\logs"), "freeze_plan_build_dirs_not_in_run_root"),
    ("dataset_dirs_one", lambda d: d["plans"]["build"].__setitem__("dataset_dirs", d["plans"]["build"]["dataset_dirs"][:1]), "freeze_plan_build_dataset_dirs"),
    ("dataset_dirs_duplicate", lambda d: d["plans"]["build"].__setitem__("dataset_dirs", [d["plans"]["build"]["dataset_dirs"][0]] * 2), "freeze_plan_build_dataset_dirs"),
    ("dataset_dirs_elsewhere", lambda d: d["plans"]["build"].__setitem__("dataset_dirs", [r"D:\elsewhere\ds_20261009_nf1", r"D:\elsewhere\ds_20261009_nf3"]), "freeze_plan_build_dataset_dirs"),
    ("dataset_dirs_bad_name", lambda d: d["plans"]["build"].__setitem__("dataset_dirs", [str(Path(d["plans"]["build"]["dataset_dirs"][0]).parent / "BadName"), d["plans"]["build"]["dataset_dirs"][1]]), "freeze_plan_build_dataset_dirs"),
    ("dataset_dir_nested", lambda d: d["plans"]["build"].__setitem__("dataset_dirs", [str(Path(d["plans"]["build"]["dataset_dirs"][0]) / "ds_20261009_nf2"), d["plans"]["build"]["dataset_dirs"][1]]), "freeze_plan_build_dataset_dirs"),
    ("age_budget_impossible", plan_field("q0_preceding_worst_case_s", 3500.0), "freeze_plan_build_q0_age_budget_impossible"),
    ("age_budget_negative", plan_field("q0_preceding_worst_case_s", -1.0), "freeze_plan_build_q0_age_budget_impossible"),
    ("max_age_zero", plan_field("q0_max_age_s", 0.0), "freeze_plan_build_q0_age_budget_impossible"),
    ("h2_missing", lambda d: d.pop("h2"), "freeze_h2"),
    ("h2_one_report", lambda d: d["h2"].__setitem__("reports", d["h2"]["reports"][:1]), "freeze_h2"),
    ("h2_bad_sha", lambda d: d["h2"].__setitem__("plan_sha256", "x"), "freeze_h2"),
    ("pin_runner_redirected", lambda d: d["pins"]["runner"].__setitem__("path", r"D:\evil\runner.py"), "freeze_pin_runner"),
    ("pin_observer_redirected", lambda d: d["pins"]["observer_module"].__setitem__("path", r"D:\evil\observer.py"), "freeze_pin_observer_module"),
    ("pin_model_redirected", lambda d: d["pins"]["quiescence_model"].__setitem__("path", r"D:\evil\model.py"), "freeze_pin_quiescence_model"),
    ("pin_observer_missing", lambda d: d["pins"].pop("observer_module"), "freeze_pin_observer_module"),
    ("pin_entry_short_sha", lambda d: d["pins"]["entry"].__setitem__("sha256", "abc"), "freeze_pin_entry"),
    ("pins_not_a_dict", lambda d: d.__setitem__("pins", ["x"]), "freeze_pins"),
    ("profile_bad", lambda d: d["profile"].__setitem__("sha256", "short"), "freeze_profile"),
    ("profile_renew_text", lambda d: d["profile"].__setitem__("renew_seconds", "4800"), "freeze_profile"),
])
def test_every_malformed_or_redirected_freeze_is_refused_and_leaves_cfg_empty(pr, name, fn, expected):
    assert expected in mutated(pr, fn) and pr.mod.CFG == {}


def test_a_windows_field_that_is_missing_or_unparsable_is_refused(pr):
    assert mutated(pr, lambda d: d["windows"].pop("hard_not_after_utc")) == ["freeze_windows"] and pr.mod.CFG == {}
    assert mutated(pr, lambda d: d["windows"].__setitem__("not_before_utc", "yesterday")) == ["freeze_windows"] and pr.mod.CFG == {}


@pytest.mark.parametrize("name", ["repair-run-20261009", "repair-run-q0a-r120", "repair-run-build-r12", "repair-run-build-r1220", "repair-run-build-r122-x", "x-repair-run-build-r122", "repair-run-heavy-ci-r101"])
def test_only_build_run_roots_are_accepted_and_a_refusal_never_reconfigures_the_module(pr, name):
    before = (pr.mod.RUN, pr.mod.STOP)
    pr.mod.CFG.clear()
    problems = mutated(pr, lambda d: d.__setitem__("run_root", str(pr.outputs / name)))
    assert problems == ["freeze_kind_or_run_root"] and pr.mod.CFG == {} and (pr.mod.RUN, pr.mod.STOP) == before


def test_a_build_run_root_under_another_parent_is_refused(pr):
    before = (pr.mod.RUN, pr.mod.STOP)
    pr.mod.CFG.clear()
    assert mutated(pr, lambda d: d.__setitem__("run_root", str(pr.tmp / "elsewhere" / "repair-run-build-r122"))) == ["freeze_kind_or_run_root"] and pr.mod.CFG == {} and (pr.mod.RUN, pr.mod.STOP) == before


def test_a_wrong_sha_or_unreadable_or_non_json_freeze_is_refused(pr):
    assert pr.mod.load_freeze(pr.freeze_path, "0" * 64) == ["freeze_sha256_mismatch"] and pr.mod.CFG == {}
    assert pr.mod.load_freeze(pr.tmp / "nope.json", pr.freeze_sha) == ["freeze_unreadable"]
    junk = pr.tmp / "junk.json"
    junk.write_bytes(b"{nope")
    assert pr.mod.load_freeze(junk, hashlib.sha256(b"{nope").hexdigest()) == ["freeze_not_json"]


def test_log_before_a_freeze_only_prints_and_never_creates_a_directory(capsys):
    mod = load(OPERATOR, "operator_build_unset")
    assert not mod.RUN.exists()
    mod.log("early", x=1)
    assert not mod.RUN.exists() and '"event": "early"' in capsys.readouterr().out


# =========================================================================== crawler probe + preconditions
@pytest.mark.parametrize("result,success,pids", [
    (subprocess.CompletedProcess([], 0, stdout="", stderr=""), True, []),
    (subprocess.CompletedProcess([], 1, stdout="", stderr=""), False, []),
    (subprocess.CompletedProcess([], 0, stdout="", stderr="denied"), False, []),
    (subprocess.CompletedProcess([], 0, stdout="junk 5", stderr=""), False, [5]),
])
def test_crawler_probe_is_fail_closed(pr, monkeypatch, result, success, pids):
    monkeypatch.setattr(pr.mod.subprocess, "run", lambda *a, **k: result)
    probe = pr.mod.crawler_probe()
    assert probe["success"] is success and probe["pids"] == pids


def clean_probe(pr, monkeypatch, **override):
    probe = {"success": True, "pids": [], "returncode": 0, "timed_out": False}
    probe.update(override)
    monkeypatch.setattr(pr.mod, "crawler_probe", lambda: dict(probe))


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
    Path(pr.mod.CFG["plans"]["build"]["approval"]).write_bytes(b"tampered")
    (pr.run / "bld_STOP").write_text("x", encoding="utf-8")
    (pr.run / "build" / "plan" / "leftover.json").write_text("x", encoding="utf-8")
    (pr.run / "build" / "logs").mkdir()
    (pr.run / "bld_build_entry_stdout.json").write_text("x", encoding="utf-8")
    monkeypatch.setenv("DB_HOST", "x")
    problems = pr.mod.preconditions()
    for needle in ("sha_mismatch: build_approval", "already_exists: bld_STOP", "already_exists: logs", "already_exists: bld_build_entry_stdout.json", "plan_dir_not_empty: build", "shell_inherits_DB_vars"):
        assert needle in problems


@pytest.mark.parametrize("which", [0, 1])
def test_an_existing_dataset_directory_blocks_the_start_even_if_empty(pr, monkeypatch, which):
    clean_probe(pr, monkeypatch)
    pr.dirs[which].mkdir(parents=True)
    assert f"already_exists: {pr.dirs[which].name}" in pr.mod.preconditions()


@pytest.mark.parametrize("pin", ["observer_module", "quiescence_model", "runner", "entry", "supervisor", "source", "interpreter", "parent_interpreter"])
def test_a_drifted_pinned_file_blocks_the_start(pr, monkeypatch, pin):
    clean_probe(pr, monkeypatch)
    pr.mod.CFG["pins"][pin]["sha256"] = "0" * 64
    assert f"sha_mismatch: {pin}" in pr.mod.preconditions()


def test_a_changed_q0_receipt_or_plan_file_blocks_the_start(pr, monkeypatch):
    clean_probe(pr, monkeypatch)
    pr.q0_path.write_bytes(b"changed")
    problems = pr.mod.preconditions()
    assert "sha_mismatch: q0_receipt" in problems and any(p.startswith("q0_receipt_invalid_or_too_old") for p in problems)


def edit_plan(pr, fn):
    path = Path(pr.mod.CFG["plans"]["build"]["plan_file"])
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")
    pr.mod.CFG["plans"]["build"]["plan_file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()           # giu pin tep khop de chi cau truc plan bi test


@pytest.mark.parametrize("name,fn,needle", [
    ("plan_hash", lambda d: d.__setitem__("plan_sha256", "9" * 64), "plan_file_hash_differs_from_freeze"),
    ("phase_names", lambda d: d["document"]["specs"][0].__setitem__("name", "ph5_build_x"), "plan_phases_differ_from_expected"),
    ("phase_order", lambda d: d["document"]["specs"].reverse(), "plan_phases_differ_from_expected"),
    ("one_phase_readonly", lambda d: d["document"]["specs"][1].__setitem__("kind", "readonly"), "plan_has_a_non_write_phase"),
    ("output_dirs", lambda d: d["document"]["specs"][0].__setitem__("output_dirs", [str(Path(d["document"]["specs"][0]["output_dirs"][0]).parent / "ds_20261009_zz")]), "plan_output_dirs_differ_from_freeze"),
    ("output_dirs_swapped", lambda d: d["document"]["specs"][0].__setitem__("output_dirs", d["document"]["specs"][1]["output_dirs"]), "plan_output_dirs_differ_from_freeze"),
    ("q0_pin", lambda d: d["document"].__setitem__("q0_receipt_sha256", "8" * 64), "plan_q0_pin_differs_from_freeze"),
    ("max_age_setting", lambda d: d["document"]["settings"].__setitem__("q0_max_age_s", 7200.0), "plan_settings_differ_from_freeze"),
    ("budget_setting", lambda d: d["document"]["settings"].__setitem__("total_budget_s", 100.0), "plan_settings_differ_from_freeze"),
])
def test_a_plan_file_that_does_not_match_the_freeze_blocks_the_start(pr, monkeypatch, name, fn, needle):
    clean_probe(pr, monkeypatch)
    edit_plan(pr, fn)
    assert needle in pr.mod.preconditions()


def test_a_required_file_that_drifted_since_the_plan_was_built_blocks_the_start(pr, monkeypatch):
    clean_probe(pr, monkeypatch)
    pr.req_a.write_bytes(b"input a but changed")
    assert any(p.startswith("required_files_drifted") and "req_a.txt" in p for p in pr.mod.preconditions())
    pr.req_a.write_bytes(b"input a")
    pr.req_b.write_bytes(b"input b changed")                                                                      # tep chi duoc ghim boi spec THU HAI (h3)
    assert any(p.startswith("required_files_drifted") and "req_b.txt" in p for p in pr.mod.preconditions())
    pr.req_b.write_bytes(b"input b only for h3")
    (pr.h2_dir / "receipt_02_ph4_verify_h1.json").write_text("{}", encoding="utf-8")                              # bang chung H2 duoc ghim trong required_files
    assert any(p.startswith("required_files_drifted") for p in pr.mod.preconditions())
    pr.req_a.unlink()
    assert any(p.startswith("required_files_drifted") for p in pr.mod.preconditions())


def test_h2_evidence_problems_block_the_start(pr, monkeypatch):
    clean_probe(pr, monkeypatch)
    (pr.h2_dir / "QUARANTINE.json").write_text("x", encoding="utf-8")
    assert any(p.startswith("h2_plan_dir_unexpected_content") for p in pr.mod.preconditions())
    (pr.h2_dir / "QUARANTINE.json").unlink()
    (pr.h2_dir / "PLAN_BOUND.json").write_text(json.dumps({"computed_plan_sha256": "0" * 64, "approved_plan_sha256": H2_SHA}), encoding="utf-8")
    assert "h2_plan_bound_hash_differs_from_freeze" in pr.mod.preconditions()
    (pr.h2_dir / "PLAN_BOUND.json").write_text(json.dumps({"computed_plan_sha256": H2_SHA, "approved_plan_sha256": "0" * 64}), encoding="utf-8")
    assert "h2_plan_bound_hash_differs_from_freeze" in pr.mod.preconditions()
    (pr.h2_dir / "PLAN_BOUND.json").write_text("{nope", encoding="utf-8")
    assert "h2_plan_bound_unreadable" in pr.mod.preconditions()
    pr.reports[0].unlink()
    assert any(p.startswith("h2_report_missing") for p in pr.mod.preconditions())


def test_preconditions_refuse_when_no_freeze_is_loaded(pr):
    pr.mod.CFG.clear()
    assert pr.mod.preconditions() == ["freeze_not_loaded"]


# =========================================================================== tuoi Q0 tinh den phase ghi CUOI
def test_the_allowed_q0_age_at_start_is_max_age_minus_preceding_worst_case_minus_margin(pr):
    assert pr.mod.q0_allowed_age_s(pr.mod.CFG["plans"]["build"]) == ALLOWED_AGE == 1580.0


@pytest.mark.parametrize("age_s,ok", [(0, True), (300, True), (1500, True), (1570, True), (1700, False), (3000, False), (3590, False)], ids=["fresh", "five_min", "just_inside", "edge_inside", "too_old", "old", "almost_expired"])
def test_a_q0_receipt_must_leave_room_for_the_last_write_phase_at_the_start(tmp_path, monkeypatch, age_s, ok):
    env = Build(tmp_path, monkeypatch, q0_age_s=float(age_s))
    assert env.load() == []
    clean_probe(env, monkeypatch)
    problems = env.mod.preconditions()
    assert (not any(p.startswith("q0_receipt_invalid_or_too_old") for p in problems)) is ok


def test_a_q0_receipt_from_the_future_is_refused(tmp_path, monkeypatch):
    env = Build(tmp_path, monkeypatch, q0_age_s=-300.0)
    assert env.load() == []
    clean_probe(env, monkeypatch)
    assert any(p.startswith("q0_receipt_invalid_or_too_old") for p in env.mod.preconditions())


@pytest.mark.parametrize("name,mutate", [
    ("kind", dict(kind="other")), ("observer_pin", dict(observer_script_sha256="0" * 64)), ("sql_set_pin", dict(sql_set_sha256="0" * 64)), ("model_pin", dict(quiescence_model_sha256="0" * 64)),
    ("selftest_false", dict(selftest={**{k: True for k in SELFTEST_KEYS}, "removed_after_close": False})), ("no_server", dict(server={})),
    ("details_missing", dict(details={})), ("created_malformed", dict(created_at_utc="yesterday"))])
def test_an_invalid_q0_receipt_is_refused_by_load_q0_receipt_even_with_the_right_hash(tmp_path, monkeypatch, name, mutate):
    env = Build(tmp_path, monkeypatch)
    doc = env.make_q0(300.0, **mutate)
    env.q0_path.write_bytes(json.dumps(doc, indent=2, sort_keys=True).encode("utf-8"))
    frozen = json.loads(env.freeze_path.read_text(encoding="utf-8"))
    frozen["q0_receipt"]["sha256"] = hashlib.sha256(env.q0_path.read_bytes()).hexdigest()                        # freeze ghim DUNG hash tep moi: loi phai den tu noi dung receipt, khong tu hash
    path, digest = env.write_freeze(frozen, "requalified.json")
    assert env.mod.load_freeze(path, digest) == []
    clean_probe(env, monkeypatch)
    problems = env.mod.preconditions()
    assert any(p.startswith("q0_receipt_invalid_or_too_old") for p in problems)


# =========================================================================== plan thanh cong + cong
def test_the_plan_runs_one_check_only_then_one_invocation_with_exact_argv_timeouts_and_byte_exact_outputs(pr):
    calls = ready(pr)
    assert pr.mod.cmd_plans() == 0
    assert [a[-1] == "--check-only" for a, _ in calls] == [True, False]
    for argv, kw in calls:
        assert argv == pr.mod.approved_argv("build") + (["--check-only"] if argv[-1] == "--check-only" else []) and kw["env"] == pr.mod.ENV6 and kw["cwd"] == str(pr.mod.ROOT)
        assert kw["capture_output"] is True and kw["text"] is False
    assert calls[0][1]["timeout"] == 120 and calls[1][1]["timeout"] == 4200 + 120                               # wrapper = budget + margin
    written = (pr.run / "bld_build_entry_stdout.json").read_bytes()
    assert written.startswith(b"line1\r\n") and hashlib.sha256(written).hexdigest() in pr.mod.OP_LOG.read_text(encoding="utf-8")
    assert pr.world.stopped() and pr.mod.STOP.exists() and all(d.is_dir() for d in pr.dirs)


def test_the_lead_wait_happens_before_the_check_only(pr):
    ready(pr)
    pr.mod.cmd_plans()
    assert pr.world.t - pr.world.t0 >= 90 and "waiting_lead" in pr.world.op_events()


def test_without_a_started_publisher_nothing_runs(pr):
    calls = pr.entry()
    assert pr.mod.cmd_plans() == 2 and calls == [] and "stop_not_needed" in pr.world.op_events() and not any(d.exists() for d in pr.dirs)


@pytest.mark.parametrize("what", ["check_not_ok", "hash_differs", "plan_hash_differs"])
def test_a_failing_check_only_means_no_invocation(pr, what):
    pr.started()
    pr.evidence()
    calls = pr.entry(check_ok=(what != "check_not_ok"), hash_override=("f" * 64 if what == "hash_differs" else None), plan_hash_override=("e" * 64 if what == "plan_hash_differs" else None))
    assert pr.mod.cmd_plans() == 2 and len(calls) == 1 and pr.world.stopped() and not any(d.exists() for d in pr.dirs)


def test_a_check_only_that_exits_nonzero_means_no_invocation_even_if_its_json_says_ok(pr):
    pr.started()
    pr.evidence()
    calls = pr.entry(check_ok=True, check_rc=1)
    assert pr.mod.cmd_plans() == 2 and len(calls) == 1 and pr.world.stopped()


def test_gates_are_rechecked_right_before_the_invocation_for_publisher_time(pr):
    pr.started(wall_seconds=90 + 4320 + 100)
    pr.evidence()
    calls = pr.entry(check_elapsed_s=115.0)
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "before_invocation" in pr.mod.OP_LOG.read_text(encoding="utf-8")
    assert "insufficient_publisher_time" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_the_q0_age_gate_is_rechecked_after_the_check_only(tmp_path, monkeypatch):
    """Q0 con du tuoi o cong 1 (1500 s + lead 90 s = 1590 > 1580 => phai tru lead; dung q0 tuoi 1400): sau check-only 115 s + lead tuoi 1400+90+115 = 1605 > 1580 => cong 2 tu choi."""
    env = Build(tmp_path, monkeypatch, q0_age_s=1400.0)
    assert env.load() == []
    env.started()
    env.evidence()
    calls = env.entry(check_elapsed_s=115.0)
    assert env.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"]
    log = env.mod.OP_LOG.read_text(encoding="utf-8")
    assert "before_invocation" in log and "q0_receipt_unusable_for_the_last_write_phase" in log and not any(d.exists() for d in env.dirs)


def test_a_q0_receipt_too_old_at_the_first_gate_refuses_before_the_check_only(tmp_path, monkeypatch):
    env = Build(tmp_path, monkeypatch, q0_age_s=1450.0)                                                          # 1450 + lead 90 = 1540 < 1580 o preconditions; sau lead = 1540 OK => dung 1500
    assert env.load() == []
    env.started()
    env.evidence()
    calls = env.entry()
    env.monkeypatch.setattr(env.mod, "q0_allowed_age_s", lambda p: 1400.0)                                       # ep cong 1 chat hon: tuoi sau lead (1540) vuot 1400
    assert env.mod.cmd_plans() == 2 and calls == [] and "before_check_only" in env.mod.OP_LOG.read_text(encoding="utf-8")


def test_a_q0_receipt_file_replaced_by_another_valid_receipt_after_the_first_gate_refuses_the_invocation(pr):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    real = pr.mod.subprocess.run

    def swap_q0_during_check(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] == "--check-only":
            pr.q0_path.write_bytes(json.dumps(pr.make_q0(200.0), indent=2, sort_keys=True).encode("utf-8"))      # receipt khac nhung HOP LE (hash khac freeze)
        return result
    pr.monkeypatch.setattr(pr.mod.subprocess, "run", swap_q0_during_check)
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "q0_receipt_sha256_mismatch" in pr.mod.OP_LOG.read_text(encoding="utf-8")


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
    assert pr.mod.run_plan("build", {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=6000))}, first=True) == 2 and calls == []


def test_the_hard_not_after_limit_refuses_with_its_own_reason(pr):
    pr.evidence()
    calls = pr.entry()
    pr.mod.CFG["windows"]["hard_not_after_utc"] = NOW - dt.timedelta(seconds=1)
    assert pr.mod.run_plan("build", {"at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=6000))}, first=True) == 2 and calls == []
    assert "hard_not_after_passed" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("which", [0, 1])
def test_a_dataset_directory_that_appears_after_the_start_refuses_the_invocation(pr, which):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    real = pr.mod.subprocess.run

    def create_during_check(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] == "--check-only":
            pr.dirs[which].mkdir(parents=True)
        return result
    pr.monkeypatch.setattr(pr.mod.subprocess, "run", create_during_check)
    assert pr.mod.cmd_plans() == 2 and [a[-1] for a, _ in calls] == ["--check-only"] and "dataset_dir_already_exists" in pr.mod.OP_LOG.read_text(encoding="utf-8")


# =========================================================================== sau invocation: chuoi receipt hai phase ghi + niem phong thu muc
def per_phase(tamper):
    return [(f"phase{index}", {index: tamper}) for index in (0, 1)]


RECEIPT_TAMPER = [
    ("foreign_plan", {"approved_plan_sha256": "f" * 64}), ("wrong_prev", {"prev_receipt_sha256": "0" * 64}), ("wrong_argv", {"argv_sha256": "e" * 64}), ("wrong_spec", {"spec_sha256": "e" * 64}),
    ("wrong_seq", {"seq": 9}), ("readonly_kind", {"kind": "readonly"}), ("q0_pin_missing", {"q0_receipt_sha256": None}), ("q0_pin_other", {"q0_receipt_sha256": "9" * 64}), ("wrong_entry", {"entrypoint_sha256": "7" * 64}),
    ("timed_out", {"launch": {"exit_code": 0, "timed_out": True, "killed_tree": False, "aborted_by_gate": None, "contained": True}}),
    ("killed_tree", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": True, "aborted_by_gate": None, "contained": True}}),
    ("not_contained", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": False}}),
    ("aborted_by_gate", {"launch": {"exit_code": 0, "timed_out": False, "killed_tree": False, "aborted_by_gate": "crawler", "contained": True}}),
    ("nonzero_exit", {"launch": {"exit_code": 3, "timed_out": False, "killed_tree": False, "aborted_by_gate": None, "contained": True}}),
    ("verdict", {"verdict": "QUARANTINE"}), ("reasons", {"reasons": ["x"]}), ("wrong_phase", {"phase": "other"}),
]


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("name,tamper", RECEIPT_TAMPER, ids=[n for n, _ in RECEIPT_TAMPER])
def test_a_receipt_that_is_not_the_genuine_runner_shape_is_a_failure_in_either_write_phase(pr, name, tamper, index):
    pr.started()
    pr.evidence()
    pr.entry(receipt_tamper={index: tamper})
    assert pr.mod.cmd_plans() == 5 and "plan_not_pass" in pr.world.op_events() and pr.world.stopped()


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("quiescence", [{"status": "unknown"}, {"status": "busy"}, {}, None], ids=["unknown", "busy", "empty", "none"])
def test_a_write_phase_without_a_verified_quiescence_is_a_failure(pr, quiescence, index):
    pr.started()
    pr.evidence()
    pr.entry(receipt_tamper={index: {"quiescence": quiescence}})
    assert pr.mod.cmd_plans() == 5 and "quiescence_not_verified" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("name,seal", [("zero_files", {"files": 0}), ("kind", {"kind": "seal_failed"}), ("float_files", {"files": 2.0}), ("bool_files", {"files": True})], ids=["zero_files", "kind", "float_files", "bool_files"])
def test_a_receipt_whose_directory_seal_is_empty_or_not_a_directory_seal_is_a_failure(pr, name, seal, index):
    pr.started()
    pr.evidence()
    pr.entry(seal_tamper={index: seal})
    assert pr.mod.cmd_plans() == 5 and "output_dir_not_sealed" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("index", [0, 1])
def test_a_receipt_with_the_wrong_or_missing_output_dir_keys_is_a_failure(pr, index):
    pr.started()
    pr.evidence()
    pr.entry(keys_tamper={index})
    assert pr.mod.cmd_plans() == 5 and "output_dirs_keys" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("io")], ids=["permission", "oserror"])
def test_a_dataset_directory_that_cannot_be_read_back_for_the_reseal_is_a_failure(pr, error):
    pr.started()
    pr.evidence()
    pr.entry()
    real = pr.mod.subprocess.run

    def break_reseal_after_invocation(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] != "--check-only":
            def failing(path, **kwargs):
                raise error
            pr.mod.runner().directory_seal = failing
        return result
    pr.monkeypatch.setattr(pr.mod.subprocess, "run", break_reseal_after_invocation)
    assert pr.mod.cmd_plans() == 5 and "output_dir_cannot_be_resealed" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_a_receipt_for_a_phase_without_declared_output_dirs_is_not_a_pass(pr):
    ready(pr)
    assert pr.mod.cmd_plans() == 0
    directory = Path(pr.mod.CFG["plans"]["build"]["plan_dir"])
    plan_file = Path(pr.mod.CFG["plans"]["build"]["plan_file"])
    doc = json.loads(plan_file.read_text(encoding="utf-8"))
    for spec in doc["document"]["specs"]:
        spec["output_dirs"] = []
    plan_file.write_text(json.dumps(doc), encoding="utf-8")
    for receipt in directory.glob("receipt_*.json"):
        data = json.loads(receipt.read_text(encoding="utf-8"))
        data["output_dirs"] = {}
        receipt.write_text(json.dumps(data), encoding="utf-8")
    assert any("output_dirs_keys" in problem for problem in pr.mod.plan_receipts_pass("build"))                   # spec hash cung lech, nhung PHAI co ly do output_dirs_keys rieng


@pytest.mark.parametrize("index", [0, 1])
def test_a_dataset_directory_that_changed_after_the_seal_is_a_failure(pr, index):
    pr.started()
    pr.evidence()
    pr.entry(after_receipts=lambda dirs: (dirs[index] / "samples.parquet").write_bytes(b"tampered after seal"))
    assert pr.mod.cmd_plans() == 5 and "output_dir_changed_after_seal" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("index", [0, 1])
def test_an_extra_file_in_a_dataset_directory_after_the_seal_is_a_failure(pr, index):
    pr.started()
    pr.evidence()
    pr.entry(after_receipts=lambda dirs: (dirs[index] / "extra.txt").write_text("late", encoding="utf-8"))
    assert pr.mod.cmd_plans() == 5 and "output_dir_changed_after_seal" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("index", [0, 1])
def test_a_dataset_directory_that_vanished_after_the_seal_is_a_failure(pr, index):
    import shutil
    pr.started()
    pr.evidence()
    pr.entry(after_receipts=lambda dirs: shutil.rmtree(dirs[index]))
    assert pr.mod.cmd_plans() == 5 and "output_dir_cannot_be_resealed" in pr.mod.OP_LOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("kwargs", [
    dict(bound_tamper={"entrypoint_sha256": "7" * 64}), dict(bound_tamper={"phases": ["ph5_build_h1"]}), dict(bound_tamper={"phases": ["ph5_build_h3", "ph5_build_h1"]}),
    dict(summary_tamper={"invocation_sha256": "0" * 64}), dict(summary_tamper={"ok": False}), dict(summary_tamper={"phases": []}), dict(extra_files=["QUARANTINE.json"]), dict(rc=3),
    dict(summary_tamper={"phases": [{"phase": "ph5_build_h1", "reasons": [], "receipt_path": "x", "receipt_sha256": "0" * 64, "verdict": "PASS"}]}),
    dict(bound_tamper={"approved_plan_sha256": "f" * 64}), dict(bound_tamper={"computed_plan_sha256": "f" * 64}), dict(bound_tamper={"kind": "other"}), dict(no_dirs=True)])
def test_plan_bound_summary_stray_files_missing_directories_and_nonzero_exit_are_failures(pr, kwargs):
    pr.started()
    pr.evidence()
    pr.entry(**kwargs)
    assert pr.mod.cmd_plans() != 0 and pr.world.stopped()


def test_a_summary_row_with_a_different_phase_or_a_swapped_order_is_a_failure(pr):
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
    plan_file = Path(pr.mod.CFG["plans"]["build"]["plan_file"])
    original = plan_file.read_text(encoding="utf-8")
    doc = json.loads(original)
    doc["document"]["specs"].append(dict(doc["document"]["specs"][0], name="extra_phase"))
    plan_file.write_text(json.dumps(doc), encoding="utf-8")
    assert pr.mod.plan_receipts_pass("build") == ["plan_file_phase_order_differs_from_expected"]
    doc = json.loads(original)
    doc["document"]["specs"].reverse()
    plan_file.write_text(json.dumps(doc), encoding="utf-8")
    assert pr.mod.plan_receipts_pass("build") == ["plan_file_phase_order_differs_from_expected"]


def test_a_runner_that_differs_from_the_freeze_pin_stops_the_plan_at_the_first_gate_because_the_q0_check_needs_it(pr):
    pr.started()
    pr.evidence()
    calls = pr.entry()
    pr.mod.CFG["pins"]["runner"]["sha256"] = "0" * 64
    pr.mod._RUNNER = None
    assert pr.mod.cmd_plans() == 2 and calls == [] and "runner_sha256_differs_from_freeze_pin" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_a_runner_that_drifts_after_the_invocation_cannot_validate_the_receipt_chain(pr):
    pr.started()
    pr.evidence()
    pr.entry()
    real = pr.mod.subprocess.run

    def drift_after_invocation(argv, **kw):
        result = real(argv, **kw)
        if argv[-1] != "--check-only":
            pr.mod.CFG["pins"]["runner"]["sha256"] = "0" * 64
            pr.mod._RUNNER = None
        return result
    pr.monkeypatch.setattr(pr.mod.subprocess, "run", drift_after_invocation)
    assert pr.mod.cmd_plans() == 5 and "receipt_chain_unreadable" in pr.mod.OP_LOG.read_text(encoding="utf-8")


def test_the_good_fixture_is_accepted_end_to_end_with_both_sealed_directories(pr):
    ready(pr)
    assert pr.mod.cmd_plans() == 0 and pr.mod.plan_receipts_pass("build") == [] and all((d / "samples.parquet").exists() for d in pr.dirs)


@pytest.mark.parametrize("exc", [KeyboardInterrupt, RuntimeError, subprocess.TimeoutExpired])
def test_exception_or_cancel_in_the_invocation_stops_the_publisher_and_propagates(pr, exc):
    pr.started()
    pr.evidence()
    pr.entry(invoke_exc=exc("x") if exc is not subprocess.TimeoutExpired else subprocess.TimeoutExpired("entry", 4320))
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

    def fake_popen(command, **kw):
        launched.append((command, kw))
        return FakeSup()
    monkeypatch.setattr(pr.mod.subprocess, "Popen", fake_popen)
    pr.world.timeline.append((1, {"event": "started", "pid": 1, "at_utc": NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "wall_deadline_utc": text(NOW + dt.timedelta(seconds=6000))}))
    assert pr.mod.cmd_start() == 0 and len(launched) == 1
    command, kw = launched[0]
    cfg, mod = pr.mod.CFG, pr.mod
    expected = [mod.PY_SUP, str(mod.SUPERVISOR), "--profile", cfg["profile"]["path"], "--expected-profile-sha256", cfg["profile"]["sha256"], "--expected-source-sha256", cfg["pins"]["source"]["sha256"],
                "--expected-interpreter-sha256", cfg["pins"]["interpreter"]["sha256"], "--not-before-utc", pr.windows["not_before_utc"], "--start-deadline-utc", pr.windows["start_deadline_utc"],
                "--hard-not-after-utc", pr.windows["hard_not_after_utc"], "--max-pool-size", "5", "--supervisor-log", str(mod.SUP_LOG), "--publisher-log", str(mod.PUB_LOG), "--stop-file", str(mod.STOP)]
    assert command == expected and kw["env"] == mod.ENV6 and kw["cwd"] == str(mod.ROOT) and kw["stdin"] == subprocess.DEVNULL and kw["stderr"] == subprocess.STDOUT and kw["creationflags"] == 0x08000000
    body = mod.PREFLIGHT.read_text(encoding="utf-8")
    assert "shell" not in kw and pr.freeze_sha in body and str(pr.run) in body and pr.mod.CFG["q0"]["sha256"] in body and str(ALLOWED_AGE) in body


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


def test_collect_lists_the_files_of_the_run_root_only_and_never_touches_the_dataset_directories(pr):
    ready(pr)
    pr.mod.cmd_plans()
    assert pr.mod.cmd_collect() == 0
    logged = [json.loads(x) for x in pr.mod.OP_LOG.read_text(encoding="utf-8").splitlines() if '"event": "collected"' in x]
    assert logged and "bld_build_entry_stdout.json" in logged[-1]["files"] and not any("samples.parquet" in name for name in logged[-1]["files"])


# =========================================================================== driver
@pytest.fixture
def driver():
    return load(DRIVER, "run_build_workflow_under_test")


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
    def stamp(self): return "2026-10-09T14:00:00Z"


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
    assert code != 0                                                                                             # khong ghi duoc ket qua => van nonzero


def test_driver_refuses_on_operator_pin_drift_or_a_previous_run(driver, tmp_path):
    fake = FakeOp(tmp_path)
    kwargs = dict(freeze_path=tmp_path / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: p.write_text("a"), result_path=tmp_path / "r.json", ack_path=tmp_path / "a.md")
    driver.OPERATOR_BUILD_SHA256 = "0" * 64
    assert driver.run_workflow(fake, **kwargs) == 2 and fake.calls == []
    driver.OPERATOR_BUILD_SHA256 = hashlib.sha256(OPERATOR.read_bytes()).hexdigest()
    (tmp_path / "a.md").write_text("already")
    assert driver.run_workflow(fake, **kwargs) == 2 and fake.calls == ["load_freeze"]                              # pin dung => nap freeze (dat run-root) roi moi phat hien da chay


def test_driver_derives_ack_and_result_paths_from_the_freeze_run_root_after_loading_it(driver, tmp_path):
    fake = FakeOp(tmp_path)
    acks = []
    code = driver.run_workflow(fake, freeze_path=tmp_path / "f.json", freeze_sha256="0" * 64, ack=lambda m, p: (acks.append(p), p.write_text("ack")), pre_checks=False)
    assert code == 0 and acks == [tmp_path / "bld_dossier_outer_cleanup_ack.md"] and (tmp_path / "bld_workflow_result.json").exists()
    assert not (tmp_path / "q0v_workflow_result.json").exists() and not (tmp_path / "probe_workflow_result.json").exists()


@pytest.mark.skipif("BUILD_OPERATOR_UNDER_TEST" in os.environ, reason="mutation run uses a different operator file")
def test_driver_pin_constant_equals_the_sha256_of_the_real_operator(driver):
    assert driver.OPERATOR_BUILD_SHA256 == hashlib.sha256((RUN_REAL / "operator_build.py").read_bytes()).hexdigest()


def test_driver_ack_records_pins_and_the_cleanup_contract(driver, pr, tmp_path):
    out = tmp_path / "ack.md"
    driver.write_ack(pr.mod, out, driver_path=str(DRIVER))
    body = out.read_text(encoding="utf-8")
    assert pr.freeze_sha in body and hashlib.sha256(DRIVER.read_bytes()).hexdigest() in body and "ensure_stopped" in body and "collect" in body and str(pr.run) in body
    assert hashlib.sha256(OPERATOR.read_bytes()).hexdigest() in body
