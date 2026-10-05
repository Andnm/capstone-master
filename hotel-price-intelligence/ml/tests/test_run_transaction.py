"""GPT review vong 2 (R2-M2, R2-M3, R2-m1, R2-m2) cho Phase 4: aggregate CODE_MANIFEST, giao dich run, kiem tham so CLI. Thuan (khong MySQL)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training import TRAINING_VERSION  # noqa: E402
from training.provenance import ProvenanceError, aggregate_sha256, code_provenance, require_lineage, validate_code_manifest  # noqa: E402
from training.run_transaction import (  # noqa: E402
    ArgumentError, RunExistsError, RunStateError, RunTransaction, parse_selection, validate_run_id, verify_run_dir,
)

from test_training import make_dataset  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------- R2-m1
def test_training_version_was_bumped_for_the_changed_logic():
    major, minor, _ = TRAINING_VERSION.rsplit("-", 1)[1].split(".")
    assert (int(major), int(minor)) >= (1, 1), TRAINING_VERSION


# ----------------------------------------------------------------- R2-M2: aggregate cua CODE_MANIFEST khong duoc tin o khai bao
def _manifest_tree(tmp_path: Path) -> tuple[Path, dict]:
    ml = tmp_path / "ml"
    (ml / "training").mkdir(parents=True)
    (ml / "training" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (ml / "training" / "b.py").write_text("y = 2\n", encoding="utf-8")
    files = {f"ml/training/{n}": hashlib.sha256((ml / "training" / n).read_bytes()).hexdigest() for n in ("a.py", "b.py")}
    return ml, {"created_at": "t", "files": files, "code_sha256": aggregate_sha256(files), "git": {}}


def _write_manifest(ml: Path, manifest: dict) -> None:
    (ml / "CODE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_aggregate_formula_is_the_same_as_the_packager(tmp_path):
    pkg = _load("package_for_colab")
    ml, manifest = _manifest_tree(tmp_path)
    built = pkg.build_code_manifest([(ml / "training" / "a.py", "ml/training/a.py"), (ml / "training" / "b.py", "ml/training/b.py")], "t")
    assert built["code_sha256"] == manifest["code_sha256"] == aggregate_sha256(manifest["files"])


def test_valid_manifest_is_accepted(tmp_path):
    ml, manifest = _manifest_tree(tmp_path)
    _write_manifest(ml, manifest)
    prov = code_provenance(ml)
    assert prov["source"] == "code_manifest" and prov["code_sha256"] == manifest["code_sha256"] and prov["verified_files"] == 2
    assert Path(prov["manifest_path"]).name == "CODE_MANIFEST.json"


def test_manifest_with_every_file_correct_but_forged_aggregate_is_rejected(tmp_path):
    """Tai hien dung bang chung cua GPT: moi file dung, `code_sha256` = 000...0 => truoc day van `source=code_manifest`."""
    ml, manifest = _manifest_tree(tmp_path)
    manifest["code_sha256"] = "0" * 64
    _write_manifest(ml, manifest)
    with pytest.raises(ProvenanceError, match="aggregate"):
        code_provenance(ml)


@pytest.mark.parametrize("mutate, message", [
    (lambda m: m.update(files={}), "rong"),
    (lambda m: m.pop("files"), "files"),
    (lambda m: m.update(code_sha256="xyz"), "64 ky tu hex"),
    (lambda m: m.update(code_sha256=None), "64 ky tu hex"),
    (lambda m: m["files"].update({"ml/training/a.py": "not-a-hash"}), "khong hop le"),
    (lambda m: m["files"].update({"../escape.py": "a" * 64}), "tuong doi"),
    (lambda m: m["files"].update({"/abs/path.py": "a" * 64}), "tuong doi"),
    (lambda m: m["files"].update({"ml\\training\\c.py": "a" * 64}), "tuong doi"),
])
def test_malformed_manifest_is_rejected_before_any_file_is_trusted(tmp_path, mutate, message):
    ml, manifest = _manifest_tree(tmp_path)
    mutate(manifest)
    _write_manifest(ml, manifest)
    with pytest.raises(ProvenanceError, match=message):
        code_provenance(ml)


def test_validate_code_manifest_rejects_non_object_and_recomputes_after_a_file_hash_edit(tmp_path):
    ml, manifest = _manifest_tree(tmp_path)
    with pytest.raises(ProvenanceError):
        validate_code_manifest([1, 2], root=tmp_path)
    manifest["files"]["ml/training/a.py"] = "b" * 64                      # sua hash 1 file nhung giu aggregate cu
    with pytest.raises(ProvenanceError, match="aggregate"):
        validate_code_manifest(manifest, root=tmp_path)


def test_official_on_a_colab_package_needs_the_colab_manifest_in_lineage():
    prov = {"source": "code_manifest", "code_sha256": "a" * 64}
    with pytest.raises(ProvenanceError, match="colab-manifest"):
        require_lineage(prov, None, official=True)
    require_lineage(prov, {"sha256": "b" * 64}, official=True)
    require_lineage(prov, None, official=False)
    require_lineage({"source": "git", "head": "abc", "ml_dirty": False}, None, official=True)


# ----------------------------------------------------------------- R2-m2: kiem tham so
def test_parse_selection_defaults_and_valid_lists():
    assert parse_selection("", name="horizons", allowed=(1, 3, 7, 14), default=[1, 3], cast=int) == [1, 3]
    assert parse_selection("7, 14", name="horizons", allowed=(1, 3, 7, 14), default=None, cast=int) == [7, 14]
    assert parse_selection("ridge,xgb", name="models", allowed=("ridge", "rf", "xgb"), default=None) == ["ridge", "xgb"]


@pytest.mark.parametrize("raw, allowed, default, cast, message", [
    (",,", (1, 3), [1], int, "khong chua gia tri"),
    ("5", (1, 3, 7, 14), [1], int, "ngoai tap"),
    ("7,7", (1, 7), [1], int, "trung lap"),
    ("seven", (1, 7), [1], int, "khong hop le"),
    ("ridg", ("ridge", "rf", "xgb"), None, str, "ngoai tap"),
    ("", ("ridge", "rf", "xgb"), None, str, "khong duoc de trong"),
])
def test_parse_selection_rejects_typos_empty_and_duplicates(raw, allowed, default, cast, message):
    with pytest.raises(ArgumentError, match=message):
        parse_selection(raw, name="x", allowed=allowed, default=default, cast=cast)


@pytest.mark.parametrize("run_id", ["", ".hidden", "a/b", "a b", "x" * 65, "r.tmp-1", "r.failed-1", "..", "a\\b"])
def test_run_id_must_be_a_safe_directory_name(run_id):
    with pytest.raises(ArgumentError):
        validate_run_id(run_id)


def test_run_id_accepts_the_default_pattern():
    assert validate_run_id("run_20261006_120000") == "run_20261006_120000"


# ----------------------------------------------------------------- R2-M3: giao dich
def _tx(tmp_path: Path, horizons=(7, 14), run_id="r1") -> RunTransaction:
    return RunTransaction(tmp_path / "models" / "ds_x", run_id, {"official": False, "models": ["ridge"]}, list(horizons))


def _finish_horizon(tx: RunTransaction, h: int) -> None:
    (tx.tmp_dir / f"h{h}_report.json").write_text(json.dumps({"horizon": h}), encoding="utf-8")
    tx.record_horizon({"horizon": h, "status": "ok"})


def test_transaction_pass_publishes_atomically_with_checksums(tmp_path):
    tx = _tx(tmp_path)
    tmp = tx.start()
    assert tmp.name.startswith(".r1.tmp-") and not tx.final_dir.exists()          # trong luc chay chua co ten cuoi
    running = json.loads((tmp / "run_manifest.json").read_text(encoding="utf-8"))
    assert running["state"] == "running" and running["horizons"] == []
    _finish_horizon(tx, 7)
    mid = json.loads((tmp / "run_manifest.json").read_text(encoding="utf-8"))
    assert [e["horizon"] for e in mid["horizons"]] == [7] and mid["state"] == "running"   # cap nhat sau tung horizon
    _finish_horizon(tx, 14)
    final = tx.commit()
    assert final == tx.final_dir and final.is_dir() and not tmp.exists()
    manifest = json.loads((final / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "pass" and set(manifest["outputs"]) == {"h7_report.json", "h14_report.json"}
    assert verify_run_dir(final) == []


def test_crash_on_second_horizon_never_creates_the_final_name_and_keeps_evidence(tmp_path):
    tx = _tx(tmp_path)
    tx.start()
    _finish_horizon(tx, 7)
    failed = tx.fail("RuntimeError: boom o horizon 14")
    assert not tx.final_dir.exists() and failed.name.startswith("r1.failed-")
    manifest = json.loads((failed / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "fail" and "boom" in manifest["error"] and [e["horizon"] for e in manifest["horizons"]] == [7]
    assert verify_run_dir(failed)                                                 # run that bai khong bao gio la run hop le
    again = _tx(tmp_path)                                                         # cung run-id duoc chay lai tu dau (khong resume)
    again.start()
    _finish_horizon(again, 7)
    _finish_horizon(again, 14)
    assert verify_run_dir(again.commit()) == []
    assert failed.exists()                                                        # bang chung that bai van con


def test_killed_process_leaves_only_a_running_temp_dir_that_is_not_a_valid_run(tmp_path):
    tx = _tx(tmp_path)
    tmp = tx.start()
    _finish_horizon(tx, 7)                                                        # process bi giet o day: khong commit/fail
    assert not tx.final_dir.exists()
    assert any("state='running'" in p for p in verify_run_dir(tmp))


def test_commit_requires_every_declared_horizon_and_report(tmp_path):
    tx = _tx(tmp_path)
    tx.start()
    _finish_horizon(tx, 7)
    with pytest.raises(RunStateError, match="chua xong het horizon"):
        tx.commit()
    tx.record_horizon({"horizon": 14, "status": "ok"})                            # khai bao xong nhung thieu file report
    with pytest.raises(RunStateError, match="thieu h"):
        tx.commit()
    assert not tx.final_dir.exists()


def test_existing_final_dir_is_refused_at_start_and_at_commit(tmp_path):
    first = _tx(tmp_path, horizons=(7,))
    first.start()
    _finish_horizon(first, 7)
    first.commit()
    with pytest.raises(RunExistsError):
        _tx(tmp_path, horizons=(7,)).start()
    racer = _tx(tmp_path, horizons=(7,), run_id="r2")
    racer.start()
    _finish_horizon(racer, 7)
    (racer.dataset_root / "r2").mkdir()                                            # ten cuoi xuat hien giua luc chay
    with pytest.raises(RunExistsError):
        racer.commit()


def test_transaction_state_machine_guards(tmp_path):
    tx = _tx(tmp_path, horizons=(7,))
    with pytest.raises(RunStateError):
        tx.record_horizon({"horizon": 7})
    with pytest.raises(RunStateError):
        tx.fail("x")
    tx.start()
    _finish_horizon(tx, 7)
    tx.commit()
    with pytest.raises(RunStateError):
        tx.commit()


def test_verify_run_dir_detects_tamper_missing_and_extra_files(tmp_path):
    tx = _tx(tmp_path)
    tx.start()
    _finish_horizon(tx, 7)
    _finish_horizon(tx, 14)
    final = tx.commit()
    assert verify_run_dir(final) == []
    (final / "h7_report.json").write_text('{"horizon": 7, "tampered": true}', encoding="utf-8")
    assert any("lech checksum" in p for p in verify_run_dir(final))
    (final / "h14_report.json").unlink()
    assert any("thieu output h14_report.json" in p for p in verify_run_dir(final))
    (final / "stray.txt").write_text("x", encoding="utf-8")
    assert any("khong co trong checksum" in p for p in verify_run_dir(final))
    manifest = json.loads((final / "run_manifest.json").read_text(encoding="utf-8"))
    manifest["state"] = "running"
    (final / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert any("state=" in p for p in verify_run_dir(final))
    assert verify_run_dir(tmp_path / "nowhere") == ["thieu run_manifest.json"]


# ----------------------------------------------------------------- CLI end-to-end (in-process de gia loi o horizon thu hai)
def _cli(module, monkeypatch, ds: Path, tmp_path: Path, *extra: str) -> int:
    argv = ["train_models.py", "--dataset-dir", str(ds), "--models", "ridge", "--output-root", str(tmp_path / "models"), *extra]
    monkeypatch.setattr(sys, "argv", argv)
    return module.main()


def test_cli_crash_on_second_horizon_marks_fail_and_a_rerun_with_same_id_passes(tmp_path, monkeypatch, capsys):
    module = _load("train_models")
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_tx")
    real = module.run_horizon

    def flaky(dataset_dir, h, *args, **kwargs):
        if h == 14:
            raise RuntimeError("gia loi o horizon 14")
        return real(dataset_dir, h, *args, **kwargs)

    monkeypatch.setattr(module, "run_horizon", flaky)
    assert _cli(module, monkeypatch, ds, tmp_path, "--horizons", "7,14", "--run-id", "r1") == 1
    root = tmp_path / "models" / "ds_tx"
    assert not (root / "r1").exists()
    failed = [p for p in root.iterdir() if p.name.startswith("r1.failed-")]
    assert len(failed) == 1 and not [p for p in root.iterdir() if ".tmp-" in p.name]
    manifest = json.loads((failed[0] / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "fail" and "gia loi" in manifest["error"] and [e["horizon"] for e in manifest["horizons"]] == [7]
    assert "giu bang chung" in capsys.readouterr().err

    def second_ok(dataset_dir, h, cfg, out_dir, models, **kwargs):      # dataset gia chi co h7: horizon 14 chay "thanh cong" bang bao cao gia
        if h != 14:
            return real(dataset_dir, h, cfg, out_dir, models, **kwargs)
        (Path(out_dir) / "h14_report.json").write_text('{"horizon": 14}', encoding="utf-8")
        return {"status": "skipped", "rows": {}, "reason": "stub", "evaluation_status": "unknown", "selected_model": None}

    monkeypatch.setattr(module, "run_horizon", second_ok)
    assert _cli(module, monkeypatch, ds, tmp_path, "--horizons", "7,14", "--run-id", "r1") == 0
    assert verify_run_dir(root / "r1") == []
    assert failed[0].exists()


def test_cli_rejects_bad_arguments_before_creating_anything(tmp_path, monkeypatch, capsys):
    module = _load("train_models")
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_args")
    for extra, text in ((("--horizons", "5"), "ngoai tap"), (("--horizons", ",,"), "khong chua"), (("--models", "ridg"), "ngoai tap"),
                        (("--models", ""), "khong duoc de trong"), (("--run-id", "a/b"), "khong hop le")):
        argv_models = [] if extra[0] == "--models" else ["--models", "ridge"]
        monkeypatch.setattr(sys, "argv", ["train_models.py", "--dataset-dir", str(ds), "--output-root", str(tmp_path / "models"), *argv_models, *extra])
        assert module.main() == 2, extra
        assert text in capsys.readouterr().err
    assert not (tmp_path / "models").exists()


def test_cli_verification_failure_leaves_no_directory_at_all(tmp_path, monkeypatch, capsys):
    module = _load("train_models")
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_nover")
    with open(ds / "samples.parquet", "ab") as handle:
        handle.write(b"x")
    assert _cli(module, monkeypatch, ds, tmp_path, "--horizons", "7", "--run-id", "r1") == 3
    assert "KHONG qua xac minh" in capsys.readouterr().err
    assert not (tmp_path / "models").exists()                                      # truoc day de lai thu muc rong


def test_cli_run_carries_environment_and_passes_verification(tmp_path, monkeypatch):
    module = _load("train_models")
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_env")
    assert _cli(module, monkeypatch, ds, tmp_path, "--horizons", "7", "--run-id", "r1") == 0
    run = tmp_path / "models" / "ds_env" / "r1"
    assert (run / "environment_resolved.txt").is_file() and verify_run_dir(run) == []
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert "environment_resolved.txt" in manifest["outputs"] and manifest["state"] == "pass"


def test_official_colab_run_copies_code_and_colab_manifests_into_the_run(tmp_path):
    """R2-M2: artifact tu chua dinh danh ma - CODE_MANIFEST.json + COLAB_MANIFEST.json duoc copy nguyen byte vao run va nam trong checksum; thieu COLAB => exit 3."""
    import subprocess

    pkg = _load("package_for_colab")
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_off")
    manifest = pkg.build_package(tmp_path / "out", ds, stamp="t3")
    work = tmp_path / "colab"
    zipfile.ZipFile(tmp_path / "out" / "ml_train_pkg_t3.zip").extractall(work)
    zipfile.ZipFile(tmp_path / "out" / "dataset_ds_off.zip").extractall(work)
    colab_manifest = tmp_path / "out" / "COLAB_MANIFEST.json"
    assert manifest["archives"] and colab_manifest.is_file()
    base = [sys.executable, str(work / "ml" / "scripts" / "train_models.py"), "--dataset-dir", str(work / "ds_off"), "--horizons", "7",
            "--models", "ridge", "--output-root", str(work / "models"), "--official"]
    without = subprocess.run([*base, "--run-id", "r0"], capture_output=True, text=True, cwd=str(work), timeout=300)
    assert without.returncode == 3 and "colab-manifest" in without.stderr
    assert not (work / "models").exists()
    done = subprocess.run([*base, "--run-id", "r1", "--colab-manifest", str(colab_manifest)], capture_output=True, text=True, cwd=str(work), timeout=300)
    assert done.returncode == 0, done.stderr[-1500:]
    run = work / "models" / "ds_off" / "r1"
    assert (run / "CODE_MANIFEST.json").read_bytes() == (work / "ml" / "CODE_MANIFEST.json").read_bytes()
    assert (run / "COLAB_MANIFEST.json").read_bytes() == colab_manifest.read_bytes()
    run_manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert {"CODE_MANIFEST.json", "COLAB_MANIFEST.json"} <= set(run_manifest["outputs"]) and run_manifest["official"] is True
    assert verify_run_dir(run) == []
