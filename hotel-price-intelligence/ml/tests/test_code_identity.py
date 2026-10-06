"""GPT review vong 2 R2-M1: danh tinh MA cua dataset build (builder + dependency backend) - thuan, khong MySQL.

Cac test MySQL tuong ung (doi mot byte backend sau init => `--apply` fail truoc step; step cuoi doi ma => khong PASS) nam o `test_b5_mysql.py`
va chi chay duoc khi operational DB khong con run queued/running.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset_builder import code_identity as ci  # noqa: E402
from dataset_builder import config as cfg  # noqa: E402
from dataset_builder import runner  # noqa: E402


def _tree(root: Path) -> dict[str, Path]:
    """Cay gia: repo/hotel-price-intelligence/{ml/dataset_builder, ml/scripts, backend/app/...}."""
    pi = root / "hotel-price-intelligence"
    files = {
        "builder": pi / "ml" / "dataset_builder" / "steps.py",
        "init": pi / "ml" / "scripts" / "init_dataset_build.py",
        "build": pi / "ml" / "scripts" / "build_dataset.py",
        "app_init": pi / "backend" / "app" / "__init__.py",
        "core_init": pi / "backend" / "app" / "core" / "__init__.py",
        "core_cfg": pi / "backend" / "app" / "core" / "config.py",
        "wh_init": pi / "backend" / "app" / "warehouse" / "__init__.py",
        "canon": pi / "backend" / "app" / "warehouse" / "canonicalize.py",
        "hashing": pi / "backend" / "app" / "warehouse" / "hashing.py",
        "helper": pi / "backend" / "app" / "warehouse" / "helper.py",
        "ref": pi / "backend" / "app" / "scraper" / "reference.py",
        "sc_init": pi / "backend" / "app" / "scraper" / "__init__.py",
        "ddl": pi / "backend" / "app" / "warehouse" / "etl_ddl.py",
        "sql": pi / "backend" / "app" / "database" / "setup.sql",
        "unused": pi / "backend" / "app" / "warehouse" / "unused.py",
    }
    bodies = {
        "builder": "from app.warehouse.canonicalize import canon\n\ndef f():\n    from app.scraper.reference import pick  # import trong ham\n    return pick\n",
        "init": "from app.warehouse.hashing import sha\n", "build": "x = 1\n", "app_init": "", "core_init": "", "core_cfg": "SECRET = 1\n",
        "wh_init": "", "canon": "from . import helper\nfrom app.core.config import SECRET\n", "hashing": "def sha():\n    return 1\n",
        "helper": "H = 1\n", "ref": "def pick():\n    return 1\n", "sc_init": "", "ddl": "DDL = 'x'\n", "sql": "CREATE TABLE t (a INT);\n",
        "unused": "U = 1\n",
    }
    for name, path in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(bodies[name], encoding="utf-8")
    return files


def _manifest(root: Path) -> dict:
    pi = root / "hotel-price-intelligence"
    return ci.builder_code_manifest(ml_dir=pi / "ml", repo_root=root, backend_dir=pi / "backend", extra_files=(
        "hotel-price-intelligence/ml/scripts/init_dataset_build.py", "hotel-price-intelligence/ml/scripts/build_dataset.py",
        "hotel-price-intelligence/backend/app/database/setup.sql", "hotel-price-intelligence/backend/app/warehouse/etl_ddl.py"))


def test_closure_follows_absolute_relative_function_local_imports_and_parent_packages(tmp_path):
    _tree(tmp_path)
    files = set(_manifest(tmp_path)["files"])
    prefix = "hotel-price-intelligence/backend/app/"
    for expected in ("warehouse/canonicalize.py", "warehouse/hashing.py", "warehouse/helper.py",      # relative `from . import helper`
                     "scraper/reference.py",                                                           # import trong ham
                     "warehouse/__init__.py", "scraper/__init__.py", "__init__.py",                    # goi cha cung chay
                     "warehouse/etl_ddl.py", "database/setup.sql"):
        assert prefix + expected in files, expected
    assert prefix + "warehouse/unused.py" not in files                                                # khong duoc import => khong thuoc danh tinh
    assert not [f for f in files if "/app/core/" in f]                                                # app.core bi loai co chu dich


def test_manifest_is_deterministic_and_pins_the_exclusion_list(tmp_path):
    _tree(tmp_path)
    first, second = _manifest(tmp_path), _manifest(tmp_path)
    assert first == second and len(first["code_sha256"]) == 64 and first["excluded_backend_prefixes"] == ["app.core"]


@pytest.mark.parametrize("target", ["canon", "hashing", "helper", "ref", "wh_init", "sql", "ddl", "builder", "init"])
def test_changing_one_byte_of_any_dependency_changes_the_identity(tmp_path, target):
    files = _tree(tmp_path)
    before = _manifest(tmp_path)
    files[target].write_bytes(files[target].read_bytes() + b"\n# x")
    after = _manifest(tmp_path)
    assert after["code_sha256"] != before["code_sha256"]
    changed = ci.diff_manifests(before["files"], after["files"])["changed"]
    assert len(changed) == 1 and changed[0].endswith(files[target].name)


def test_a_new_import_of_a_backend_module_is_detected_as_an_added_file(tmp_path):
    files = _tree(tmp_path)
    before = _manifest(tmp_path)
    files["builder"].write_text(files["builder"].read_text(encoding="utf-8") + "from app.warehouse.unused import U\n", encoding="utf-8")
    after = _manifest(tmp_path)
    diff = ci.diff_manifests(before["files"], after["files"])
    assert any(name.endswith("warehouse/unused.py") for name in diff["added"]) and any(name.endswith("steps.py") for name in diff["changed"])


def test_missing_declared_file_is_an_error(tmp_path):
    files = _tree(tmp_path)
    files["sql"].unlink()
    with pytest.raises(ci.CodeIdentityError, match="thieu file"):
        _manifest(tmp_path)


def test_verify_code_identity_accepts_match_and_names_the_changed_file_on_drift(tmp_path):
    files = _tree(tmp_path)
    pinned = _manifest(tmp_path)
    config = {"builder_code": pinned}
    assert ci.verify_code_identity(config, current=lambda: _manifest(tmp_path))["code_sha256"] == pinned["code_sha256"]
    files["canon"].write_bytes(files["canon"].read_bytes() + b"# drift\n")
    with pytest.raises(ci.CodeIdentityError, match=r"canonicalize\.py") as raised:
        ci.verify_code_identity(config, current=lambda: _manifest(tmp_path))
    assert "dataset_version MOI" in str(raised.value)


@pytest.mark.parametrize("config", [{}, {"builder_code": None}, {"builder_code": {"files": {}}}])
def test_config_without_a_pinned_manifest_cannot_be_verified(config):
    with pytest.raises(ci.CodeIdentityError, match="khong ghim"):
        ci.verify_code_identity(config, current=lambda: {"code_sha256": "a" * 64, "files": {}})


def test_official_requires_head_and_clean_dependency_files_but_rehearsal_does_not():
    pinned = {"builder_code": {"files": {"a.py": "1" * 64, "b.py": "2" * 64}, "code_sha256": "c" * 64}}
    ci.assert_official_clean({**pinned, "purpose": "rehearsal"}, git_dirty=lambda files: ["a.py"], head=lambda: None)
    ci.assert_official_clean({**pinned, "purpose": "dev"}, git_dirty=lambda files: ["a.py"], head=lambda: None)
    with pytest.raises(ci.CodeIdentityError, match="HEAD"):
        ci.assert_official_clean({**pinned, "purpose": "official"}, git_dirty=lambda files: [], head=lambda: None)
    seen: list = []
    with pytest.raises(ci.CodeIdentityError, match="chua commit"):
        ci.assert_official_clean({**pinned, "purpose": "official"}, git_dirty=lambda files: seen.append(files) or ["b.py"], head=lambda: "abc")
    assert seen == [["a.py", "b.py"]]                                                                 # kiem DUNG tap file da ghim (ke ca backend)
    ci.assert_official_clean({**pinned, "purpose": "official"}, git_dirty=lambda files: [], head=lambda: "abc")


def test_build_config_pins_the_manifest_and_any_code_change_changes_the_config_hash():
    base = dict(import_batch_id="b1", purpose="rehearsal", min_runs=3, min_coverage=0.8, anomaly_mode="retrospective_full",
                anomaly_cutoff_at=None, anomaly_registry_file_sha256="0" * 64, calendar_input={"name": "vn_holidays.csv", "sha256": "1" * 64, "bytes": 1})
    one = cfg.build_config(**base, builder_code={"files": {"a.py": "1" * 64}, "code_sha256": "a" * 64})
    two = cfg.build_config(**base, builder_code={"files": {"a.py": "1" * 64}, "code_sha256": "b" * 64})
    assert one["builder_code"]["code_sha256"] == "a" * 64 and cfg.config_sha256(one) != cfg.config_sha256(two)
    default = cfg.build_config(**base)                                                                # mac dinh: tinh tu cay that
    assert len(default["builder_code"]["code_sha256"]) == 64 and default["builder_code"]["files"]


def test_real_manifest_covers_the_files_gpt_named():
    files = set(ci.builder_code_manifest()["files"])
    for name in ("backend/app/warehouse/canonicalize.py", "backend/app/warehouse/hashing.py", "backend/app/warehouse/etl_config.py",
                 "backend/app/scraper/reference.py", "backend/app/scraper/anomaly_registry_lib.py", "backend/app/warehouse/etl_ddl.py",
                 "backend/app/database/setup.sql", "ml/dataset_builder/runner.py", "ml/dataset_builder/code_identity.py",
                 "ml/scripts/init_dataset_build.py", "ml/scripts/build_dataset.py"):
        assert f"hotel-price-intelligence/{name}" in files, name


# ----------------------------------------------------------------- runner: verify truoc mark_pass / complete_step (khong can MySQL)
class _Conn:
    def rollback(self) -> None:
        pass


def _ctx(tmp_path: Path):
    return runner.StepContext(database="warehouse_x", dataset_version="ds_20261006_t", conn=_Conn(), config={"builder_code": {"code_sha256": "a" * 64}},
                              output_root=tmp_path)


@pytest.fixture()
def calls(monkeypatch):
    log: list[tuple] = []
    monkeypatch.setattr(runner, "fail_step", lambda conn, dv, reason: log.append(("fail", reason)))
    monkeypatch.setattr(runner, "mark_pass", lambda conn, dv: log.append(("pass",)))
    monkeypatch.setattr(runner, "complete_step", lambda conn, dv, step: log.append(("complete", step)))
    monkeypatch.setattr(runner, "_publish_reports_bundle", lambda ctx: log.append(("bundle",)))
    return log


def test_step_changing_code_during_its_run_cannot_complete_or_pass(tmp_path, monkeypatch, calls):
    state = {"drift": False}

    def verify(config):
        if state["drift"]:
            raise ci.CodeIdentityError("ma doi giua step")

    monkeypatch.setattr(runner, "_verify_identity", verify)

    def last_step(ctx):
        state["drift"] = True                              # "step cuoi doi ma" truoc khi ket thuc
        return {"ok": True, "failed": []}

    with pytest.raises(ci.CodeIdentityError, match="ma doi giua step"):
        runner._execute_step(_ctx(tmp_path), "validation", {"validation": last_step}, heartbeat_seconds=3600)
    assert ("pass",) not in calls and any(c[0] == "fail" and "ma doi giua step" in c[1] for c in calls)
    state["drift"] = False
    calls.clear()
    with pytest.raises(ci.CodeIdentityError):
        state["drift"] = True
        runner._execute_step(_ctx(tmp_path), "split", {"split": lambda ctx: {}}, heartbeat_seconds=3600)
    assert ("complete", "split") not in calls and any(c[0] == "fail" for c in calls)


def test_validation_publishes_the_bundle_then_verifies_identity_then_marks_pass(tmp_path, monkeypatch, calls):
    monkeypatch.setattr(runner, "_verify_identity", lambda config: calls.append(("verify",)))
    runner._execute_step(_ctx(tmp_path), "validation", {"validation": lambda ctx: {"ok": True, "failed": []}}, heartbeat_seconds=3600)
    assert calls == [("bundle",), ("verify",), ("pass",)]
    calls.clear()
    runner._execute_step(_ctx(tmp_path), "split", {"split": lambda ctx: {}}, heartbeat_seconds=3600)
    assert calls == [("verify",), ("complete", "split")]


def test_failed_validation_does_not_publish_bundle_or_pass(tmp_path, monkeypatch, calls):
    monkeypatch.setattr(runner, "_verify_identity", lambda config: calls.append(("verify",)))
    with pytest.raises(runner.BuildFailedError):
        runner._execute_step(_ctx(tmp_path), "validation", {"validation": lambda ctx: {"ok": False, "failed": ["x"]}}, heartbeat_seconds=3600)
    assert calls == [("fail", "validation: ['x']")]


def test_bundle_failure_marks_fail_and_never_passes(tmp_path, monkeypatch, calls):
    monkeypatch.setattr(runner, "_verify_identity", lambda config: calls.append(("verify",)))

    def boom(ctx):
        raise RuntimeError("ghi bundle loi")

    monkeypatch.setattr(runner, "_publish_reports_bundle", boom)
    with pytest.raises(RuntimeError, match="ghi bundle loi"):
        runner._execute_step(_ctx(tmp_path), "validation", {"validation": lambda ctx: {"ok": True, "failed": []}}, heartbeat_seconds=3600)
    assert ("pass",) not in calls and calls[0][0] == "fail"


def test_step_reports_carry_the_builder_code_sha(tmp_path, monkeypatch, calls):
    monkeypatch.setattr(runner, "_verify_identity", lambda config: None)
    ctx = _ctx(tmp_path)
    report = runner._execute_step(ctx, "split", {"split": lambda c: {"n": 1}}, heartbeat_seconds=3600)
    assert report["builder_code_sha256"] == "a" * 64


# ----------------------------------------------------------------- runner._session: ma doi => khong step/cleanup/ghi nao chay (khong can MySQL)
@pytest.fixture()
def session_spies(monkeypatch):
    """Gia lap ha tang DB cua runner (lock/connect/manifest) va ghi lai MOI hanh dong co the ghi/xoa."""
    from contextlib import contextmanager

    log: list[str] = []

    class Conn:
        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

    @contextmanager
    def lock(database, version):
        yield

    @contextmanager
    def connect(database):
        yield Conn()

    state = {"row": {"status": "running", "active_step": None, "last_completed_step": "initialized", "active_step_attempt": 0,
                     "max_step_attempts": 3}}
    monkeypatch.setattr(runner, "advisory_lock", lock)
    monkeypatch.setattr(runner, "connect", connect)
    monkeypatch.setattr(runner, "load_manifest", lambda conn, version, for_update=False: dict(state["row"]))
    monkeypatch.setattr(runner, "verify_manifest", lambda conn, row: {"builder_code": {"code_sha256": "a" * 64}, "purpose": "rehearsal"})
    for name in ("cleanup_from", "start_step", "write_recovery_marker", "begin_retry_attempt", "append_retry_override", "complete_step",
                 "mark_pass", "fail_step"):
        monkeypatch.setattr(runner, name, lambda *args, _n=name, **kwargs: log.append(_n))

    def drift(config):
        raise ci.CodeIdentityError("ma doi sau init")

    monkeypatch.setattr(runner, "verify_code_identity", drift)
    return log, state


def test_drifted_code_stops_apply_rebuild_and_retry_before_any_write(tmp_path, session_spies):
    log, state = session_spies
    called: list[str] = []
    registry = {name: (lambda ctx, n=name: called.append(n) or {}) for name in runner.STEPS}
    with pytest.raises(ci.CodeIdentityError, match="ma doi sau init"):
        runner.apply("warehouse_x", "ds_20261006_t", steps=registry, output_root=tmp_path)
    with pytest.raises(ci.CodeIdentityError):
        runner.rebuild_from("warehouse_x", "ds_20261006_t", "causal_references", steps=registry, output_root=tmp_path)
    state["row"]["active_step"] = "split"
    with pytest.raises(ci.CodeIdentityError):
        runner.retry_failed_step("warehouse_x", "ds_20261006_t", reason="r", actor="a", steps=registry, output_root=tmp_path)
    assert called == [] and log == []                                  # khong step, khong cleanup, khong marker, khong ghi manifest


def test_apply_on_a_passed_dataset_only_verifies_outputs_even_if_code_moved_on(tmp_path, session_spies, monkeypatch):
    log, state = session_spies
    state["row"]["status"] = "pass"
    import dataset_builder.validation as validation
    monkeypatch.setattr(validation, "verify_pass_outputs", lambda *args, **kwargs: [])
    assert runner.apply("warehouse_x", "ds_20261006_t", steps={}, output_root=tmp_path)["status"] == "pass"
    assert log == []
    monkeypatch.setattr(validation, "verify_pass_outputs", lambda *args, **kwargs: ["hash_file_samples.parquet: lech"])
    with pytest.raises(runner.ManifestError, match="output khong con khop"):
        runner.apply("warehouse_x", "ds_20261006_t", steps={}, output_root=tmp_path)
    with pytest.raises(ci.CodeIdentityError):                          # nhung rebuild tren dataset PASS van bi chan vi doi ma
        runner.rebuild_from("warehouse_x", "ds_20261006_t", "split", steps={}, output_root=tmp_path)
    assert log == []
