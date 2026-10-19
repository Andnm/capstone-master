"""Tom tat run (summary.py) + dong goi Colab (scripts/package_for_colab.py). Dong goi dung dataset/snapshot THAT (chi doc) nen tu skip neu thieu outputs/datasets."""
from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from cls_training import candidates as C
from cls_training import pipeline as P
from cls_training import summary as SM
from cls_training import transaction as T

from cls_testenv import DATASETS_ROOT, SNAPSHOT_DIR, V1_DIR, needs_real_data
from synth import make_loaded as make_runner_loaded, runner_config
from test_candidates_selection import fake_xgb_factory
from test_pipeline_drive_cli import synthetic_snapshot


@pytest.fixture(autouse=True)
def _fake_xgb(monkeypatch):
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", fake_xgb_factory)


def _packager():
    spec = importlib.util.spec_from_file_location("_cls_package_for_colab", V1_DIR / "scripts" / "package_for_colab.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------------- summary

def test_summary_reads_report_and_analysis_without_recomputing(tmp_path):
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    res = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r1")
    s = SM.summarize_run(res["run_dir"])
    assert s["run_id"] == "r1" and s["horizon"] == 1 and s["contract_complete"] is True and s["manifest_contract_complete"] is True and s["champion"]
    assert len(s["finalists"]) == 3 and sum(f["is_champion"] for f in s["finalists"]) == 1 and all(f["validation"]["macro_f1"] is not None and f["test"]["macro_f1"] is not None for f in s["finalists"])
    assert {f["weight"] for f in s["finalists"]} <= {"none", "balanced"} and s["probability_status"] == "uncalibrated"
    assert s["claim"]["validation_comparator_id"] and s["claim_level"] in ("none", "dev_exploratory_validation_gain") and any(p["id"].startswith("C0") for p in s["panel"])
    assert set(s["ablations"]) == {"a1", "a2"} and all(a["status"] == "ok" for a in s["ablations"].values())
    assert any("dev/exploratory" in w for w in s["warnings"]) and s["data_sufficiency"]["report_present"] is False
    assert s["data_sufficiency"]["short_window"] is (s["data_sufficiency"]["eligible_prediction_dates"]["validation"] < 7 or s["data_sufficiency"]["eligible_prediction_dates"]["test"] < 7)
    run = Path(res["run_dir"])
    (run / "h1_report.json").write_bytes((run / "h1_report.json").read_bytes() + b" ")
    with pytest.raises(T.TransactionError):
        SM.summarize_run(run)                                                                # run hong khong duoc tom tat


def test_summary_flags_incomplete_runs_and_missing_analysis(tmp_path, monkeypatch):
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("xgb hong")))
    res = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r1")
    s = SM.summarize_run(res["run_dir"])
    assert s["contract_complete"] is False and s["status"] == "incomplete" and any("contract_complete=false" in w for w in s["warnings"]) and s["incomplete_reasons"]


# ----------------------------------------------------------------- dong goi

@needs_real_data
def test_package_is_verified_deterministic_and_self_contained(tmp_path):
    import nbformat
    pk = _packager()
    a = pk.build("t_a", tmp_path / "c", DATASETS_ROOT, SNAPSHOT_DIR)
    b = pk.build("t_b", tmp_path / "c", DATASETS_ROOT, SNAPSHOT_DIR)
    manifest = json.loads((a / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    for name in pk.PACKAGE_FILES:
        assert (a / name).read_bytes() == (b / name).read_bytes(), f"{name} khong xac dinh giua hai lan dong goi"       # cung noi dung => cung bytes
    assert set(manifest["files"]) == set(pk.PACKAGE_FILES) and manifest["schema"] == "cls-package-1"
    ident = manifest["identity"]
    assert ident["environment_pins"] == {"scikit-learn": "1.7.2", "numpy": "2.1.3", "pandas": "2.3.3", "xgboost": "3.4.1"} and set(ident["datasets"]) == {"h1", "h3"} == set(ident["snapshots"])
    with zipfile.ZipFile(a / "upload" / "cls_v1_code.zip") as zf:
        names = zf.namelist()
    assert "scripts/train_cls.py" in names and "cls_training/summary.py" in names and "configs/train.yaml" in names and "requirements-colab.txt" in names
    assert not any(n.startswith("tests/") or "__pycache__" in n or n.endswith(".pyc") for n in names)
    with zipfile.ZipFile(a / "upload" / "datasets.zip") as zf:
        assert {n.split("/")[0] for n in zf.namelist()} == {"ds_20261010_nf1", "ds_20261010_nf3"}
    nb = nbformat.read(str(a / "cls_v1_colab.ipynb"), as_version=4)
    nbformat.validate(nb)
    code = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
    assert "ALLOW_ENV_DRIFT" not in code and "--selftest-only" in code and "cls_training.summary" in code and "Restart session" in code and "DEVICE = \"cuda\"" in code
    handoff = (a / "HANDOFF.md").read_text(encoding="utf-8")
    assert "fake" in handoff and "dev/exploratory" in handoff.lower() and all(info["sha256"] in handoff for info in manifest["files"].values()) and manifest["identity"]["runtime_code_sha256"] in handoff
    assert pk.verify_delivery(a)["verified"] is True


@needs_real_data
def test_package_refuses_overwrite_and_detects_tampering(tmp_path):
    pk = _packager()
    a = pk.build("t_a", tmp_path / "c", DATASETS_ROOT, SNAPSHOT_DIR)
    with pytest.raises(pk.PackageError, match="đã tồn tại"):
        pk.build("t_a", tmp_path / "c", DATASETS_ROOT, SNAPSHOT_DIR)
    z = a / "upload" / "baseline_snapshot.zip"
    z.write_bytes(z.read_bytes()[:-5] + b"xxxxx")
    with pytest.raises(pk.PackageError, match="hash"):
        pk.verify_delivery(a)


@needs_real_data
def test_package_preflight_rejects_a_dataset_that_differs_from_the_pin(tmp_path):
    import shutil
    pk = _packager()
    bad_root = tmp_path / "datasets"
    shutil.copytree(DATASETS_ROOT / "ds_20261010_nf1", bad_root / "ds_20261010_nf1")
    shutil.copytree(DATASETS_ROOT / "ds_20261010_nf3", bad_root / "ds_20261010_nf3")
    (bad_root / "ds_20261010_nf1" / "data_dictionary.csv").write_bytes((bad_root / "ds_20261010_nf1" / "data_dictionary.csv").read_bytes() + b"\n")
    with pytest.raises(pk.PackageError, match="entry dataset"):
        pk.build("t_x", tmp_path / "c", bad_root, SNAPSHOT_DIR)
    assert not (tmp_path / "c" / "t_x").exists()                                              # loi preflight => khong tao thu muc giao hang


def test_summary_marks_incomplete_run_claim_as_none_with_block_reason(tmp_path, monkeypatch):
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("xgb hong")))
    res = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r1")
    s = SM.summarize_run(res["run_dir"])
    assert s["claim_level"] == "none" and s["claim"]["validation_gate_passed"] is False and s["claim"]["claim_block_reasons"] == ["training_contract_incomplete"]
    assert "diagnostic_validation_gate_passed" in s["claim"]


# ----------------------------------------------------------------- GPT 16: MINOR-1 (delivery an toan), MINOR-3 (ID o xac dinh), SHA256SUMS, kiem am tinh

@pytest.fixture()
def fast_pack(monkeypatch):
    """Dong goi that nhung preflight (14 entry hash) bi thay bang stub de test am tinh chay nhanh; verify_delivery van kiem lai tu dau."""
    pk = _packager()
    monkeypatch.setattr(pk, "preflight", lambda cfg, d, s: {"datasets": {}, "snapshots": {}})
    return pk


@pytest.mark.parametrize("bad", ["../escaped", "a/b", "a\\b", "C:\\x", "/abs", "..", "", ".hidden", "x" * 65, "a..b", "\\\\srv\\share"])
def test_unsafe_delivery_names_fail_before_preflight_and_create_nothing(tmp_path, monkeypatch, bad):
    pk = _packager()
    monkeypatch.setattr(pk, "preflight", lambda *a, **k: (_ for _ in ()).throw(AssertionError("preflight khong duoc chay voi ten khong an toan")))
    root = tmp_path / "colab"
    with pytest.raises(pk.PackageError, match="không an toàn"):
        pk.build(bad, root, DATASETS_ROOT, SNAPSHOT_DIR)
    assert not root.exists() and not (tmp_path / "escaped").exists() and not (tmp_path.parent / "escaped").exists()


def test_junction_out_root_and_preexisting_building_dir_are_rejected(tmp_path, monkeypatch, fast_pack):
    import subprocess
    import sys
    pk = fast_pack
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / ".building_d1").mkdir()
    (tmp_path / "root" / ".building_d1" / "keep.txt").write_text("cua tien trinh khac", encoding="utf-8")
    with pytest.raises(pk.PackageError, match="đã tồn tại"):
        pk.build("d1", tmp_path / "root", DATASETS_ROOT, SNAPSHOT_DIR)
    assert (tmp_path / "root" / ".building_d1" / "keep.txt").is_file()                              # khong tu xoa thu muc cua nguoi khac
    if sys.platform == "win32":
        target = tmp_path / "real"
        target.mkdir()
        link = tmp_path / "link"
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if r.returncode == 0:
            with pytest.raises(Exception, match="reparse"):
                pk.build("d2", link, DATASETS_ROOT, SNAPSHOT_DIR)
            assert list(target.iterdir()) == []


@needs_real_data
def test_package_has_external_digests_deterministic_cell_ids_and_no_nbformat_warning(tmp_path):
    import hashlib
    import warnings
    import nbformat
    pk = _packager()
    a = pk.build("t_a", tmp_path / "c1", DATASETS_ROOT, SNAPSHOT_DIR)
    b = pk.build("t_a", tmp_path / "c2", DATASETS_ROOT, SNAPSHOT_DIR)
    for name in ("cls_v1_colab.ipynb", "HANDOFF.md"):
        assert (a / name).read_bytes() == (b / name).read_bytes()                                    # xac dinh giua hai lan dong goi
    with warnings.catch_warnings():
        warnings.simplefilter("error")                                                               # MissingIDFieldWarning (va moi canh bao) => loi
        nb = nbformat.read(str(a / "cls_v1_colab.ipynb"), as_version=4)
        nbformat.validate(nb)
    ids = [c["id"] for c in nb.cells]
    assert len(ids) == len(set(ids)) == 8 and all(len(i) == 12 for i in ids)
    manifest = json.loads((a / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    sums = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in (a / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()}
    assert set(sums) == {"PACKAGE_MANIFEST.json", "cls_v1_colab.ipynb", "HANDOFF.md", *pk.PACKAGE_FILES} and "SHA256SUMS.txt" not in sums           # khong tu tham chieu
    for rel, digest in sums.items():
        assert hashlib.sha256((a / rel).read_bytes()).hexdigest() == digest
    assert manifest["companions"]["cls_v1_colab.ipynb"]["sha256"] == sums["cls_v1_colab.ipynb"] and manifest["companions"]["HANDOFF.md"]["sha256"] == sums["HANDOFF.md"]
    handoff = (a / "HANDOFF.md").read_text(encoding="utf-8")
    assert "CI được tính sau lựa chọn mô hình, chỉ mang tính thăm dò" in handoff and "chua duoc chon loc sau" not in handoff and "hotel_cls_v1" in handoff
    assert "KHÔNG resume được giữa fit" in handoff
    assert "logs" in handoff and "lần lượt" in handoff


@needs_real_data
def test_delivery_tampering_and_incomplete_packages_are_detected(tmp_path):
    pk = _packager()
    a = pk.build("t_a", tmp_path / "c", DATASETS_ROOT, SNAPSHOT_DIR)
    assert pk.verify_delivery(a)["verified"] is True
    for name, mutate, match in (("cls_v1_colab.ipynb", lambda p: p.write_bytes(p.read_bytes() + b" "), "hash"), ("HANDOFF.md", lambda p: p.write_bytes(p.read_bytes() + b" "), "hash"),
                                ("SHA256SUMS.txt", lambda p: p.write_text("x\n", encoding="utf-8"), "SHA256SUMS")):
        path = a / name
        original = path.read_bytes()
        mutate(path)
        with pytest.raises(pk.PackageError, match=match):
            pk.verify_delivery(a)
        path.write_bytes(original)
    (a / "stray.txt").write_text("x", encoding="utf-8")
    with pytest.raises(pk.PackageError, match="ngoài danh sách giao"):
        pk.verify_delivery(a)
    (a / "stray.txt").unlink()
    (a / "SHA256SUMS.txt").unlink()
    with pytest.raises(pk.PackageError, match="SHA256SUMS"):
        pk.verify_delivery(a)


@needs_real_data
@pytest.mark.parametrize("which,mutate,match", [
    ("code_entries", lambda e: {**e, "tests/test_x.py": b"x"}, "không được giao"),
    ("code_entries", lambda e: {**e, ".env": b"SECRET=1"}, "không được giao"),
    ("code_entries", lambda e: {**e, "cls_training/__pycache__/a.pyc": b"x"}, "không được giao"),
    ("dataset_entries", lambda e: {k: v for k, v in e.items() if not k.endswith("coverage_report.json")}, "entry"),
    ("dataset_entries", lambda e: {k: v for k, v in e.items() if not k.startswith("ds_20261010_nf3/")}, "datasets.zip có"),
    ("snapshot_entries", lambda e: {k: v for k, v in e.items() if "h3_manifest" not in k}, "baseline_snapshot.zip có"),
])
def test_incomplete_or_polluted_packages_are_never_delivered(tmp_path, monkeypatch, fast_pack, which, mutate, match):
    pk = fast_pack
    real = getattr(pk, which)
    monkeypatch.setattr(pk, which, lambda *a, **k: mutate(real(*a, **k)))
    root = tmp_path / "c"
    with pytest.raises(pk.PackageError, match=match):
        pk.build("t_x", root, DATASETS_ROOT, SNAPSHOT_DIR)
    assert not (root / "t_x").exists() and not (root / ".building_t_x").exists()                        # khong de lai goi hong, khong de lai thu muc tam cua ta


def _code_cells(delivery):
    import nbformat
    nb = nbformat.read(str(delivery / "cls_v1_colab.ipynb"), as_version=4)
    return [c.source for c in nb.cells if c.cell_type == "code"]


def _localize(src, delivery, content):
    """Chay logic o notebook tren may nay: /content -> thu muc tam, DRIVE_DIR -> thu muc giao hang; bo cac dong chi co Colab (`!`, mount)."""
    src = src.replace("/content", content.as_posix())
    lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("!") and "google.colab" not in ln and "drive.mount" not in ln]
    return "\n".join(lines).replace('DRIVE_DIR = "' + content.as_posix() + '/drive/MyDrive/hotel_cls_v1"', f'DRIVE_DIR = r"{delivery}"')


@needs_real_data
def test_notebook_cells_compile_and_cell_2_extracts_and_verifies_the_delivery_like_colab_would(tmp_path):
    pk = _packager()
    delivery = pk.build("t_nb", tmp_path / "c", DATASETS_ROOT, SNAPSHOT_DIR)
    cells = _code_cells(delivery)
    assert len(cells) == 6
    content = tmp_path / "content"
    content.mkdir()
    for i, src in enumerate(cells):
        compile(_localize(src, delivery, content), f"cell{i + 1}", "exec")                         # cu phap hop le (bo qua dong chi co Colab)
    ns: dict = {}
    import sys
    saved_path = list(sys.path)
    try:
        exec(compile(_localize(cells[0], delivery, content), "cell1", "exec"), ns)                 # o 1: tham so (mount bi bo)
        exec(compile(_localize(cells[1], delivery, content), "cell2", "exec"), ns)                 # o 2: kiem hash + giai nen sach + runtime hash
    finally:
        sys.path[:] = saved_path
        for m in [m for m in sys.modules if m == "cls_training" or m.startswith("cls_training.")]:
            if getattr(sys.modules[m], "__file__", "").startswith(str(content)):
                del sys.modules[m]
    assert ns["runtime"] == json.loads((delivery / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))["identity"]["runtime_code_sha256"]
    assert (content / "cls_v1" / "scripts" / "train_cls.py").is_file() and (content / "datasets" / "ds_20261010_nf1" / "samples.parquet").is_file()
    assert sorted(p.name for p in (content / "snapshot").iterdir()) == sorted(["baseline_snapshot_h1.parquet", "baseline_snapshot_h1_manifest.json", "baseline_snapshot_h3.parquet", "baseline_snapshot_h3_manifest.json"])
    # o 2 phai tu choi goi bi sua
    z = delivery / "upload" / "cls_v1_code.zip"
    z.write_bytes(z.read_bytes()[:-3] + b"xxx")
    with pytest.raises(AssertionError, match="SHA-256"):
        exec(compile(_localize(cells[1], delivery, content), "cell2", "exec"), {"DRIVE_DIR": str(delivery)})
