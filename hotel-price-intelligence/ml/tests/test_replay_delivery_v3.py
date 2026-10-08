"""Thu muc ban giao replay (replay_tools/build_replay_delivery.py): dung tu du, hash tai lap, giai nen an toan (chong traversal), notebook fail-closed, va CHAY THAT mot vong
kieu Colab end-to-end: goi code r3 giai nen -> train smoke bang goi -> dong goi ban giao -> giai nen bang safe_extract cua notebook -> replay (runtime exact, lineage co CODE_MANIFEST)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from v3_fixtures import make_v3_dataset

ML = Path(__file__).resolve().parents[1]
REPO = ML.parent
TOOLS = REPO / "replay_tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_{abs(hash(str(TOOLS)))}", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load("build_replay_delivery")


def _pkg_module():
    spec = importlib.util.spec_from_file_location("package_for_colab", ML / "scripts" / "package_for_colab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("replay_delivery")
    ds = make_v3_dataset(root / "src", version="ds_rpd")
    colab = root / "colab"
    (colab / "ds_rpd_v3_r3").parent.mkdir(parents=True)
    _pkg_module().build_package(colab / "ds_rpd_v3_r3", ds, stamp="rpd1")
    ext = root / "extract"
    ext.mkdir()
    for archive in sorted((colab / "ds_rpd_v3_r3").glob("*.zip")):
        zipfile.ZipFile(archive).extractall(ext if archive.name.startswith("ml_train_pkg") else ext)
    env = {**os.environ, "OMP_NUM_THREADS": "1", "LOKY_MAX_CPU_COUNT": "1", "PYTHONIOENCODING": "utf-8", "GIT_CEILING_DIRECTORIES": str(root.parent)}
    done = subprocess.run([sys.executable, "-I", str(ext / "ml" / "scripts" / "train_models_v3.py"), "--dataset-dir", str(ext / "ds_rpd"), "--smoke", "--smoke-families", "hgb_l1",
                           "--smoke-n-iter", "3", "--smoke-null-n", "2", "--device", "cpu", "--run-id", "smoke_rpd", "--allow-env-drift", "--output-root", str(root / "models"),
                           "--colab-manifest", str(colab / "ds_rpd_v3_r3" / "COLAB_MANIFEST.json")], capture_output=True, text=True, cwd=str(ext), env=env, timeout=900)
    assert done.returncode == 0, done.stderr[-2000:]
    case = {"label": "case_h1", "pkg_dir": "ds_rpd_v3_r3", "run": "ds_rpd/smoke_rpd", "dataset": "ds_rpd", "horizon": 1}
    out = root / "delivery"
    result = builder.build(colab, root / "models", out, cases=[case], check_approved=False)
    return {"root": root, "out": out, "colab": colab, "models": root / "models", "case": case, "result": result, "env": env}


def _notebook_cells():
    return json.loads((builder.build_notebook() and json.dumps(builder.build_notebook())))["cells"]


def test_delivery_is_self_contained_with_manifests_and_reproducible_zip_hashes(world, tmp_path):
    out = world["out"]
    names = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert {"replay_v3_r3.ipynb", "HANDOFF_replay.md", "DELIVERY_MANIFEST.json"} <= set(names)
    folder = out / "case_h1"
    assert sorted(p.name for p in folder.iterdir()) == sorted(["COLAB_MANIFEST.json", "DELIVERY_MANIFEST.json", "dataset_ds_rpd.zip", "replay_tools.zip", "run_ds_rpd_smoke_rpd.zip"] + [p.name for p in folder.glob("ml_train_pkg_*.zip")])
    manifest = json.loads((folder / "DELIVERY_MANIFEST.json").read_text(encoding="utf-8"))
    for name, info in manifest["files"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == info["sha256"]
    top = json.loads((out / "DELIVERY_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    assert all(hashlib.sha256((out / rel).read_bytes()).hexdigest() == sha for rel, sha in top.items()) and "case_h1/replay_tools.zip" in top
    again = builder.build(world["colab"], world["models"], tmp_path / "again", cases=[world["case"]], check_approved=False)["files"]
    assert {k: v for k, v in again.items() if k.endswith(".zip")} == {k: v for k, v in world["result"]["files"].items() if k.endswith(".zip")}          # zip tai lap duoc


def test_builder_refuses_existing_output_unapproved_sources_and_invalid_runs(world, tmp_path):
    with pytest.raises(SystemExit, match="da ton tai"):
        builder.build(world["colab"], world["models"], world["out"], cases=[world["case"]], check_approved=False)
    with pytest.raises(SystemExit, match="khong khop hash"):
        builder.build(world["colab"], world["models"], tmp_path / "x", cases=[world["case"]], check_approved=True)      # nguon tong hop khong khop APPROVED => tu choi
    import shutil

    bad_models = tmp_path / "bad_models"
    shutil.copytree(world["models"], bad_models)
    (bad_models / "ds_rpd" / "smoke_rpd" / "h1_report.json").write_bytes(b"{}")
    with pytest.raises(SystemExit, match="khong hop le"):
        builder.build(world["colab"], bad_models, tmp_path / "y", cases=[world["case"]], check_approved=False)


def test_real_inputs_listed_in_approved_table_cover_all_eight_artifacts_from_file_20():
    assert len(builder.APPROVED) == 8 and len({v for v in builder.APPROVED.values()}) == 8
    assert builder.APPROVED["ds_20261006_dev1b_v3_r3/ml_train_pkg_20261008_063424.zip"].startswith("15138d4b")
    assert builder.APPROVED["ds_20261006_dev3b_v3_r3/COLAB_MANIFEST.json"].startswith("64debfec")


def test_notebook_is_output_free_compiles_and_defaults_are_fail_closed():
    nb = builder.build_notebook()
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert len(code_cells) == 5 and all(c["execution_count"] is None and c["outputs"] == [] for c in code_cells)
    for index, cell in enumerate(code_cells):
        text = "".join(cell["source"])
        stripped = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("!") and "google.colab" not in line and not line.startswith("drive.mount"))
        compile(stripped, f"cell{index}", "exec")
    params = "".join(code_cells[0]["source"])
    assert "COMPAT_PROBE = False" in params and "tolerance" not in params.lower() and "rtol" not in params
    run_cell = "".join(code_cells[3]["source"])
    assert "replay_bundles_v3.py" in run_cell and "--colab-manifest" in run_cell and "--allow-smoke-run" not in run_cell
    env_cell = "".join(code_cells[1]["source"])
    assert "Restart session" in env_cell and "COMPAT_PROBE" in env_cell and env_cell.index("assert sha256_file") < env_cell.index("pip")


def _safe_extract_namespace():
    namespace: dict = {}
    exec(compile(builder.NOTEBOOK_SAFE_EXTRACT, "safe_extract", "exec"), namespace)
    return namespace


def test_notebook_safe_extract_blocks_traversal_absolute_paths_and_existing_destination(tmp_path):
    ns = _safe_extract_namespace()
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("ml/a.txt", "x")
    dest = ns["safe_extract"](good, tmp_path / "dest")
    assert (dest / "ml" / "a.txt").read_text() == "x"
    with pytest.raises(RuntimeError, match="đã tồn tại"):
        ns["safe_extract"](good, tmp_path / "dest")
    for index, name in enumerate(("../escape.txt", "ml/../../escape.txt", "/abs.txt", "C:/win.txt", "a/../../b.txt")):
        evil = tmp_path / f"evil{index}.zip"
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr(name, "x")
        with pytest.raises(RuntimeError, match="không an toàn|ra ngoài"):
            ns["safe_extract"](evil, tmp_path / f"d{index}")
        assert not (tmp_path / f"d{index}").exists() and not (tmp_path / "escape.txt").exists()


def test_end_to_end_colab_style_replay_from_the_delivery_folder(world, tmp_path):
    ns = _safe_extract_namespace()
    folder = world["out"] / "case_h1"
    manifest = json.loads((folder / "DELIVERY_MANIFEST.json").read_text(encoding="utf-8"))
    work = tmp_path / "work"
    code_zip = next(n for n in manifest["files"] if n.startswith("ml_train_pkg_"))
    ns["safe_extract"](folder / code_zip, work / "code")
    ns["safe_extract"](folder / "dataset_ds_rpd.zip", work / "datasets")
    ns["safe_extract"](folder / manifest["run_archive"], work / "runs")
    ns["safe_extract"](folder / "replay_tools.zip", work / "tools")
    env = {**world["env"], "GIT_DIR": str(tmp_path / "no-git")}
    cmd = [sys.executable, str(work / "tools" / "replay_tools" / "replay_bundles_v3.py"), "--code-root", str(work / "code"), "--run-dir", str(work / "runs" / "smoke_rpd"),
           "--dataset-dir", str(work / "datasets" / "ds_rpd"), "--colab-manifest", str(folder / "COLAB_MANIFEST.json"), "--output-root", str(tmp_path / "replay_out"),
           "--replay-id", "e2e", "--allow-smoke-run"]
    done = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-2500:]
    rep = json.loads((tmp_path / "replay_out" / "e2e" / "replay_report.json").read_text(encoding="utf-8"))
    assert rep["verdict"]["overall"] == "PASS_EXACT_RUNTIME" and rep["verdict"]["bitwise_exact"] is True and rep["tool"]["manifest"]["files"] == 2
    assert rep["inputs"]["packaged_execution"]["packaged"] is True and rep["inputs"]["packaged_execution"]["manifest_files"] >= 30
    # cung quy trinh nhung CODE_MANIFEST bi go => fail-closed (khong ha cap sang git), thoat 3, khong co bao cao PASS
    (work / "code" / "ml" / "CODE_MANIFEST.json").unlink()
    cmd[cmd.index("e2e")] = "e2e_no_manifest"
    bad = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)
    assert bad.returncode == 3 and "PASS" not in (bad.stdout.split("verdict:")[1].split("|")[0] if "verdict:" in bad.stdout else "")


# --------------------------------------------------------------------------- R-M2 (GPT file 28): notebook chi hien thi ket qua cua DUNG lan chay hien tai
def _cell(cell_id: str) -> str:
    return "".join(next(c for c in builder.build_notebook()["cells"] if c["id"] == cell_id)["source"])


def _helpers_namespace() -> dict:
    namespace: dict = {}
    exec(compile(builder.NOTEBOOK_SAFE_EXTRACT, "helpers", "exec"), namespace)
    return namespace


def _drive(tmp_path):
    drive, work = tmp_path / "drive", tmp_path / "work"
    (work / "tools" / "replay_tools").mkdir(parents=True)
    drive.mkdir()
    (work / "tools" / "replay_tools" / "replay_bundles_v3.py").write_bytes(b"# helper\n")
    (drive / "COLAB_MANIFEST.json").write_bytes(b"{}")
    return drive, work


def _write_report(out_root: Path, replay_id: str, *, overall="PASS_EXACT_RUNTIME", train="PASS", created="2999-01-01T00:00:00Z", dataset="ds_x", run_id="run_x", sidecar_ok=True,
                  script_sha=None, colab_sha=None, field_id=None, inputs=True, work=None, drive=None):
    sha = lambda b: hashlib.sha256(b).hexdigest()    # noqa: E731
    report = {"replay_id": field_id or replay_id, "created_at_utc": created, "problems": [] if overall != "FAIL" else ["ReplayError: x"],
              "tool": {"script_sha256": script_sha or sha((work / "tools" / "replay_tools" / "replay_bundles_v3.py").read_bytes())},
              "verdict": {"overall": overall, "bounded_numeric_replay": "PASS" if overall != "FAIL" else "FAIL", "train_in_sample_recompute": train}, "horizons": {}}
    if inputs:
        report["inputs"] = {"dataset_name": dataset, "run_dir": f"/content/runs/{run_id}", "colab_manifest_sha256": colab_sha or sha((drive / "COLAB_MANIFEST.json").read_bytes())}
    target = out_root / replay_id
    target.mkdir(parents=True)
    (target / "replay_report.json").write_text(json.dumps(report), encoding="utf-8")
    raw = (target / "replay_report.json").read_bytes()
    (target / "replay_report.json.sha256").write_text((sha(raw) if sidecar_ok else "0" * 64) + "\n", encoding="utf-8")


def _run_summary(tmp_path, *, returncode, replay_id="replay_now", prior=None, **kwargs):
    drive, work = _drive(tmp_path)
    out_root = drive / "replay_out"
    if prior:
        _write_report(out_root, prior, work=work, drive=drive, dataset="ds_x", run_id="run_x")
    if kwargs.pop("write_current", True):
        _write_report(out_root, replay_id, work=work, drive=drive, **kwargs)
    namespace = _helpers_namespace()
    namespace.update(DRIVE_DIR=str(drive), WORK=str(work), OUT_ROOT=str(out_root), REPLAY_ID=replay_id, RETURNCODE=returncode, DATASET_VERSION="ds_x", RUN_ID="run_x", STARTED_AT="2026-10-08T00:00:00Z")
    exec(compile(_cell("f1"), "summary", "exec"), namespace)


def test_notebook_never_shows_a_prior_pass_when_the_current_invocation_made_no_report(tmp_path, capsys):
    with pytest.raises(RuntimeError, match="KHÔNG tạo báo cáo"):
        _run_summary(tmp_path, returncode=1, prior="replay_prior_pass", write_current=False)
    assert "PASS_EXACT_RUNTIME" not in capsys.readouterr().out


def test_notebook_reports_a_current_pass_and_fails_loudly_on_current_fail_or_train_only_fail_or_nonzero_exit(tmp_path, capsys):
    _run_summary(tmp_path / "a", returncode=0)                                                      # lan chay hien tai PASS => in ket qua, khong loi
    assert "Lần chạy này thành công: PASS_EXACT_RUNTIME" in capsys.readouterr().out
    with pytest.raises(RuntimeError, match="THẤT BẠI"):
        _run_summary(tmp_path / "b", returncode=1, overall="FAIL", train="NOT_RUN", inputs=False)  # FAIL hien tai (loi toan ven som, khong co inputs)
    assert "VẤN ĐỀ: ReplayError" in capsys.readouterr().out
    with pytest.raises(RuntimeError, match="train_in_sample=FAIL"):
        _run_summary(tmp_path / "c", returncode=1, overall="PASS_EXACT_RUNTIME", train="FAIL")      # CHI train FAIL: khong duoc chi hien 'overall PASS'
    out = capsys.readouterr().out
    assert "TRAIN in-sample (verdict RIÊNG): FAIL" in out and "returncode của lần chạy: 1" in out
    with pytest.raises(RuntimeError, match="THẤT BẠI"):
        _run_summary(tmp_path / "d", returncode=2)                                                  # exit khac 0 du report tren dia trong co ve PASS


def test_notebook_rejects_tampered_or_foreign_reports(tmp_path):
    cases = [dict(sidecar_ok=False, match="không khớp tệp .sha256"), dict(field_id="replay_other", match="replay_id"), dict(created="2020-01-01T00:00:00Z", match="cũ hơn"),
             dict(dataset="ds_other", match="dataset_name"), dict(run_id="run_other", match="run_id"), dict(colab_sha="1" * 64, match="colab_manifest_sha256"), dict(script_sha="2" * 64, match="script_sha256")]
    for index, case in enumerate(cases):
        match = case.pop("match")
        with pytest.raises(RuntimeError, match=match):
            _run_summary(tmp_path / f"t{index}", returncode=0, **case)


def test_notebook_pins_a_fresh_replay_id_before_running_and_passes_a_list_of_arguments(tmp_path, monkeypatch):
    seen = []

    class Fake:
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: seen.append(cmd) or Fake())
    import time as _time

    monkeypatch.setattr(_time, "strftime", lambda fmt, *a, **k: "2026-10-08T00:00:00Z" if "T" in fmt else "20261008_000000")      # co dinh thoi gian: ID chi khac nhau nho uuid
    ids = []
    for compat in (False, True):
        namespace = _helpers_namespace()
        namespace.update(DRIVE_DIR="/content/drive/My Drive/replay v3/dev1b_h1", WORK="/content/replay work", RUN_ID="run_x", DATASET_VERSION="ds_x", COMPAT_PROBE=compat)
        exec(compile(_cell("e1"), "run", "exec"), namespace)
        ids.append(namespace["REPLAY_ID"])
        cmd = seen[-1]
        assert isinstance(cmd, list) and cmd[cmd.index("--replay-id") + 1] == namespace["REPLAY_ID"] and ("--compat-probe" in cmd) is compat
        assert "/content/drive/My Drive/replay v3/dev1b_h1/COLAB_MANIFEST.json" in cmd and "--config" not in cmd and namespace["STARTED_AT"].endswith("Z")
    assert ids[0] != ids[1] and all(i.startswith("replay_") for i in ids)
