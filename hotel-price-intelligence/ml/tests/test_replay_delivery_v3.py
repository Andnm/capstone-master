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
    # O 5 cua notebook phai CHAP NHAN bao cao THAT cua cong cu (khong chi bao cao tong hop): doi duy nhat la co smoke_run (chay test la smoke; notebook doi smoke_run=False cho ket qua thanh cong)
    report_path = tmp_path / "replay_out" / "e2e" / "replay_report.json"
    ns_smoke = _helpers_namespace()
    ns_smoke.update(DRIVE_DIR=str(folder), WORK=str(work), OUT_ROOT=str(tmp_path / "replay_out"), REPLAY_ID="e2e", RETURNCODE=0, DATASET_VERSION="ds_rpd", RUN_ID="smoke_rpd",
                    STARTED_AT="2000-01-01T00:00:00Z", colab=json.loads((folder / "COLAB_MANIFEST.json").read_text(encoding="utf-8")), horizon=1)
    with pytest.raises(RuntimeError, match="smoke_run"):
        exec(compile(_cell("f1"), "summary_real_smoke", "exec"), ns_smoke)                           # bao cao that cua run smoke KHONG duoc coi la ket qua thanh cong
    real = json.loads(report_path.read_text(encoding="utf-8"))
    real["inputs"]["smoke_run"] = False
    report_path.write_text(json.dumps(real), encoding="utf-8")
    Path(str(report_path) + ".sha256").write_text(hashlib.sha256(report_path.read_bytes()).hexdigest() + "\n", encoding="utf-8")
    ns = dict(ns_smoke)
    exec(compile(_cell("f1"), "summary_real", "exec"), ns)                                           # moi truong khac cua bao cao that khop cac tep da kiem => thanh cong
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


CONFIG_TEXT = (TOOLS / "replay_config.json").read_text(encoding="utf-8")
_DEL = object()


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _full_world(tmp_path, horizon=1) -> dict:
    """Khong gian lam viec nhu Colab (tools/datasets/runs + Drive) voi cac tep CO THAT; bao cao duong 'day du' tinh hash tu chinh cac tep nay."""
    drive, work = tmp_path / "drive", tmp_path / "work"
    tools = work / "tools" / "replay_tools"
    tools.mkdir(parents=True)
    (tools / "replay_bundles_v3.py").write_bytes(b"# helper\n")
    (tools / "replay_config.json").write_text(CONFIG_TEXT, encoding="utf-8")
    (tools / "REPLAY_MANIFEST.json").write_text(json.dumps({"files": {n: _sha_bytes((tools / n).read_bytes()) for n in ("replay_bundles_v3.py", "replay_config.json")}}), encoding="utf-8")
    dataset = work / "datasets" / "ds_x"
    dataset.mkdir(parents=True)
    (dataset / "samples.parquet").write_bytes(b"samples")
    run = work / "runs" / "run_x"
    run.mkdir(parents=True)
    for name, data in (("run_manifest.json", b"{}"), (f"h{horizon}_report.json", b"r"), (f"h{horizon}_predictions_v3.parquet", b"p"), (f"h{horizon}_bundle_m.joblib", b"b")):
        (run / name).write_bytes(data)
    drive.mkdir()
    colab = {"code_sha256": "c" * 64}
    (drive / "COLAB_MANIFEST.json").write_text(json.dumps(colab), encoding="utf-8")
    return {"drive": drive, "work": work, "tools": tools, "run": run, "colab": colab, "horizon": horizon}


def _set(report: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = report
    for part in parts[:-1]:
        node = node[part]
    if value is _DEL:
        node.pop(parts[-1], None)
    else:
        node[parts[-1]] = value


def _full_report(w: dict, replay_id: str, overrides: dict) -> dict:
    h = w["horizon"]
    sha = lambda path: _sha_bytes(Path(path).read_bytes())    # noqa: E731
    model = {"n": 10, "failed_rows": 0, "invalid_rows": 0, "exact_match_rows": 10, "max_abs_delta_vnd": 0.0, "aggregates_pass": True}
    report = {
        "replay_id": replay_id, "created_at_utc": "2999-01-01T00:00:00Z", "problems": [],
        "tool": {"script_sha256": sha(w["tools"] / "replay_bundles_v3.py"), "config_sha256": sha(w["tools"] / "replay_config.json"), "config": json.loads(CONFIG_TEXT),
                 "manifest": {"sha256": sha(w["tools"] / "REPLAY_MANIFEST.json"), "files": 2}},
        "inputs": {"dataset_name": "ds_x", "run_dir": str(w["run"]), "colab_manifest_sha256": sha(w["drive"] / "COLAB_MANIFEST.json"), "code_sha256": w["colab"]["code_sha256"],
                   "samples_file_sha256": sha(w["work"] / "datasets" / "ds_x" / "samples.parquet"), "run_manifest_sha256": sha(w["run"] / "run_manifest.json"),
                   "bundle_sha256": {p.name: sha(p) for p in sorted(w["run"].glob("h*_bundle_*.joblib"))}, "report_sha256": {f"h{h}": sha(w["run"] / f"h{h}_report.json")},
                   "predictions_sha256": {f"h{h}": sha(w["run"] / f"h{h}_predictions_v3.parquet")}, "packaged_execution": {"packaged": True}, "smoke_run": False},
        "completeness": {"horizons": [h], "splits": ["validation", "test"], "bundles": [f"h{h}:m"]},
        "horizons": {str(h): {"splits": {"validation": {"rows": 10, "models": {"m": dict(model)}}, "test": {"rows": 10, "models": {"m": dict(model)}}}, "saved_columns_not_replayed": ["pred_other"]}},
        "verdict": {"overall": "PASS_EXACT_RUNTIME", "bounded_numeric_replay": "PASS", "bitwise_exact": True, "train_in_sample_recompute": "PASS", "runtime_exact": True, "device_verified": True},
    }
    for dotted, value in overrides.items():
        _set(report, dotted, value)
    return report


def _write_full_report(w: dict, replay_id: str, *, sidecar_ok=True, **overrides) -> None:
    report = overrides.pop("_replace", None) or _full_report(w, replay_id, {("replay_id" if k == "report_replay_id" else k.replace("__", ".")): v for k, v in overrides.items()})
    target = w["drive"] / "replay_out" / replay_id
    target.mkdir(parents=True)
    (target / "replay_report.json").write_text(json.dumps(report), encoding="utf-8")
    (target / "replay_report.json.sha256").write_text((_sha_bytes((target / "replay_report.json").read_bytes()) if sidecar_ok else "0" * 64) + "\n", encoding="utf-8")


def _run_cell5(tmp_path, *, returncode=0, replay_id="replay_now", prior=None, write_current=True, **overrides):
    w = _full_world(tmp_path)
    if prior:
        _write_full_report(w, prior)
    if write_current:
        _write_full_report(w, replay_id, **overrides)
    namespace = _helpers_namespace()
    namespace.update(DRIVE_DIR=str(w["drive"]), WORK=str(w["work"]), OUT_ROOT=str(w["drive"] / "replay_out"), REPLAY_ID=replay_id, RETURNCODE=returncode, DATASET_VERSION="ds_x", RUN_ID="run_x",
                     STARTED_AT="2026-10-08T00:00:00Z", colab=w["colab"], horizon=w["horizon"])
    exec(compile(_cell("f1"), "summary", "exec"), namespace)


def test_notebook_never_shows_a_prior_pass_when_the_current_invocation_made_no_report(tmp_path, capsys):
    with pytest.raises(RuntimeError, match="KHÔNG tạo báo cáo"):
        _run_cell5(tmp_path, returncode=1, prior="replay_prior_pass", write_current=False)
    assert "PASS_EXACT_RUNTIME" not in capsys.readouterr().out


def test_notebook_accepts_a_complete_current_report_and_never_upgrades_compat_or_unverified_device(tmp_path, capsys):
    _run_cell5(tmp_path / "a")                                                                      # bao cao day du, exact-runtime
    assert "Lần chạy này thành công: PASS_EXACT_RUNTIME" in capsys.readouterr().out
    _run_cell5(tmp_path / "b", verdict__overall="PASS_COMPAT_PROBE_ONLY", verdict__runtime_exact=False, verdict__bitwise_exact=False)
    out = capsys.readouterr().out
    assert "Lần chạy này thành công: PASS_COMPAT_PROBE_ONLY" in out and "KHÔNG phải exact-runtime replay" in out and "bitwise_exact (RIÊNG): False" in out
    _run_cell5(tmp_path / "c", verdict__overall="PASS_BOUNDED_DEVICE_UNVERIFIED", verdict__device_verified=False)
    assert "KHÔNG phải exact-runtime replay" in capsys.readouterr().out


def test_failure_branch_runs_first_and_shows_early_failures_without_other_metadata(tmp_path, capsys):
    early = {"replay_id": "replay_now", "created_at_utc": "2999-01-01T00:00:00Z", "problems": ["IntegrityError: thieu REPLAY_MANIFEST.json"], "verdict": {"overall": "FAIL", "bounded_numeric_replay": "FAIL"}}
    with pytest.raises(RuntimeError, match="THẤT BẠI"):
        _run_cell5(tmp_path / "a", returncode=3, _replace=early)                                    # loi toan ven som: khong co tool/inputs/completeness van phai hien ro nguyen nhan
    out = capsys.readouterr().out
    assert "VẤN ĐỀ: IntegrityError: thieu REPLAY_MANIFEST.json" in out and "THIẾU/LỆCH" not in out
    with pytest.raises(RuntimeError, match="train_in_sample=FAIL"):
        _run_cell5(tmp_path / "b", returncode=1, verdict__train_in_sample_recompute="FAIL")        # CHI train FAIL
    assert "TRAIN in-sample (verdict RIÊNG): FAIL" in capsys.readouterr().out
    with pytest.raises(RuntimeError, match="THẤT BẠI"):
        _run_cell5(tmp_path / "c", returncode=2)                                                    # exit != 0 du bao cao tren dia co ve PASS
    with pytest.raises(RuntimeError, match="THẤT BẠI"):
        _run_cell5(tmp_path / "d", returncode=1, verdict__overall="FAIL")


@pytest.mark.parametrize("name,overrides,message", [
    ("inputs_missing", {"inputs": _DEL}, "THIẾU/LỆCH.*inputs"),
    ("inputs_empty", {"inputs": {}}, "THIẾU/LỆCH.*inputs"),
    ("helper_sha_missing", {"tool__script_sha256": _DEL}, "THIẾU/LỆCH.*script_sha256"),
    ("config_sha_missing", {"tool__config_sha256": _DEL}, "THIẾU/LỆCH.*config_sha256"),
    ("inputs_and_helper_missing", {"inputs": _DEL, "tool__script_sha256": _DEL}, "THIẾU/LỆCH"),
    ("tool_missing", {"tool": _DEL}, "THIẾU/LỆCH.*tool"),
    ("config_protocol_widened", {"tool__config": {"numeric": {"rtol": 1e-3}}}, "THIẾU/LỆCH.*config"),
    ("manifest_files_wrong", {"tool__manifest__files": 1}, "THIẾU/LỆCH.*manifest"),
    ("verdict_empty", {"verdict": {}}, "THẤT BẠI hoặc không có verdict hợp lệ"),
    ("verdict_unknown", {"verdict__overall": "PASS_SOMETHING_ELSE"}, "THẤT BẠI hoặc không có verdict hợp lệ"),
    ("bounded_not_pass", {"verdict__bounded_numeric_replay": "NOT_RUN"}, "THẤT BẠI hoặc không có verdict hợp lệ"),
    ("train_not_run", {"verdict__train_in_sample_recompute": "NOT_RUN"}, "THIẾU/LỆCH.*train_in_sample"),
    ("problems_not_empty", {"problems": ["x"]}, "THẤT BẠI"),
    ("completeness_empty", {"completeness": {}}, "THIẾU/LỆCH.*completeness"),
    ("completeness_no_bundles", {"completeness__bundles": []}, "THIẾU/LỆCH.*completeness"),
    ("horizon_wrong", {"completeness__horizons": [3]}, "THIẾU/LỆCH.*completeness"),
    ("splits_empty", {"horizons__1__splits": {}}, "THIẾU/LỆCH.*splits"),
    ("one_split", {"horizons__1__splits__test": _DEL}, "THIẾU/LỆCH.*splits"),
    ("rows_zero", {"horizons__1__splits__test__rows": 0}, "THIẾU/LỆCH.*rows"),
    ("no_models", {"horizons__1__splits__validation__models": {}}, "THIẾU/LỆCH.*rows"),
    ("failed_rows", {"horizons__1__splits__test__models__m__failed_rows": 1}, "THIẾU/LỆCH.*failed=0"),
    ("aggregates_fail", {"horizons__1__splits__test__models__m__aggregates_pass": False}, "THIẾU/LỆCH.*aggregates_pass"),
    ("n_not_rows", {"horizons__1__splits__validation__models__m__n": 9}, "THIẾU/LỆCH.*n==rows"),
    ("dataset_name", {"inputs__dataset_name": "ds_other"}, "THIẾU/LỆCH.*dataset_name"),
    ("run_dir", {"inputs__run_dir": "/content/runs/run_other"}, "THIẾU/LỆCH.*run_dir"),
    ("colab_sha", {"inputs__colab_manifest_sha256": "1" * 64}, "THIẾU/LỆCH.*colab_manifest"),
    ("code_sha", {"inputs__code_sha256": "d" * 64}, "THIẾU/LỆCH.*code_sha256"),
    ("samples_sha", {"inputs__samples_file_sha256": "2" * 64}, "THIẾU/LỆCH.*samples_file_sha256"),
    ("run_manifest_sha", {"inputs__run_manifest_sha256": "3" * 64}, "THIẾU/LỆCH.*run_manifest_sha256"),
    ("bundle_sha_empty", {"inputs__bundle_sha256": {}}, "THIẾU/LỆCH.*bundle_sha256"),
    ("bundle_sha_wrong", {"inputs__bundle_sha256": {"h1_bundle_m.joblib": "4" * 64}}, "THIẾU/LỆCH.*bundle_sha256"),
    ("report_sha", {"inputs__report_sha256": {"h1": "5" * 64}}, "THIẾU/LỆCH.*report_sha256"),
    ("predictions_sha", {"inputs__predictions_sha256": {"h1": "6" * 64}}, "THIẾU/LỆCH.*predictions_sha256"),
    ("not_packaged", {"inputs__packaged_execution": {"packaged": False}}, "THIẾU/LỆCH.*packaged"),
    ("smoke_run", {"inputs__smoke_run": True}, "THIẾU/LỆCH.*smoke_run"),
    ("exact_but_runtime_not_exact", {"verdict__runtime_exact": False}, "THIẾU/LỆCH.*runtime_exact"),
    ("exact_but_device_unverified", {"verdict__device_verified": False}, "THIẾU/LỆCH.*device_verified"),
    ("compat_but_runtime_exact", {"verdict__overall": "PASS_COMPAT_PROBE_ONLY"}, "THIẾU/LỆCH.*runtime_exact=False"),
    ("unverified_but_device_verified", {"verdict__overall": "PASS_BOUNDED_DEVICE_UNVERIFIED"}, "THIẾU/LỆCH.*device_verified=False"),
    ("replay_id", {"report_replay_id": "replay_other"}, "THIẾU/LỆCH.*replay_id"),
    ("created_old", {"created_at_utc": "2020-01-01T00:00:00Z"}, "THIẾU/LỆCH.*created_at_utc"),
])
def test_positive_result_needs_every_required_piece_of_evidence_and_matches_the_files_checked_in_the_notebook(tmp_path, name, overrides, message):
    with pytest.raises(RuntimeError, match=message):
        _run_cell5(tmp_path, returncode=0, **{k: v for k, v in overrides.items()})


def test_notebook_rejects_a_report_whose_sidecar_does_not_match(tmp_path):
    w = _full_world(tmp_path)
    _write_full_report(w, "replay_now", sidecar_ok=False)
    namespace = _helpers_namespace()
    namespace.update(DRIVE_DIR=str(w["drive"]), WORK=str(w["work"]), OUT_ROOT=str(w["drive"] / "replay_out"), REPLAY_ID="replay_now", RETURNCODE=0, DATASET_VERSION="ds_x", RUN_ID="run_x",
                     STARTED_AT="2026-10-08T00:00:00Z", colab=w["colab"], horizon=1)
    with pytest.raises(RuntimeError, match="không khớp tệp .sha256"):
        exec(compile(_cell("f1"), "summary", "exec"), namespace)


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
