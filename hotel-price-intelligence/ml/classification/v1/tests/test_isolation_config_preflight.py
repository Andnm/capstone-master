"""Co lap goi (khong import/doc regression/shared luc chay), config validate + hash, code identity rieng cua v1, va preflight (CLI) tren du lieu that."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from cls_testenv import DATASETS_ROOT, SNAPSHOT_DIR, V1_DIR, needs_real_data
from cls_training import codeid
from cls_training.config import ConfigError, canonical_sha256, dataset_pin, load_config, snapshot_pin, validate_config

PKG = V1_DIR / "cls_training"
CONFIG_FILE = V1_DIR / "configs" / "train.yaml"


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, 0
        elif isinstance(node, ast.ImportFrom):
            yield (node.module or ""), node.level


def test_no_runtime_import_of_regression_or_shared_packages():
    forbidden = {"training", "dataset_builder", "regression", "shared"}
    offenders = []
    for path in PKG.rglob("*.py"):
        if path.name == "upstream_sync.py":                       # cong cu dev doc NGUON de sinh copy; che do packaged khong dung nguon
            continue
        for module, level in _imports(path):
            if level == 0 and module.split(".")[0] in forbidden:
                offenders.append((path.name, module))
    assert not offenders


def test_import_in_isolated_interpreter_does_not_load_regression_or_shared():
    code = (f"import sys; sys.path.insert(0, r'{V1_DIR}'); "
            "import cls_training.data, cls_training.snapshot, cls_training.panel, cls_training.metrics, cls_training.labels, cls_training.upstream_sync; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('training', 'dataset_builder')]; "
            "assert not bad, bad; print('isolated-ok')")
    out = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "isolated-ok" in out.stdout, out.stderr[-1500:]


# ------------------------------------------------------------------ config

def _raw():
    return yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))


def _write(tmp_path, raw, name="c.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return p


def test_config_hash_is_stable_sensitive_and_ignores_yaml_comments(tmp_path):
    a, b = load_config(), load_config()
    assert a["config_sha256"] == b["config_sha256"]
    raw = _raw()
    raw["seed"] = 7
    assert load_config(_write(tmp_path, raw))["config_sha256"] != a["config_sha256"]
    raw = _raw()
    raw["config_sha256"] = "x"                                    # khong duoc tu khai hash
    assert load_config(_write(tmp_path, raw, "c2.yaml"))["config_sha256"] == a["config_sha256"]
    commented = "# them mot dong comment va doi dinh dang\n" + CONFIG_FILE.read_text(encoding="utf-8").replace("seed: 20261019", "seed:    20261019")
    p = tmp_path / "c3.yaml"
    p.write_text(commented, encoding="utf-8")
    assert load_config(p)["config_sha256"] == a["config_sha256"]
    assert canonical_sha256({"b": 1, "a": 2}) == canonical_sha256({"a": 2, "b": 1})


def test_config_has_exactly_one_pin_per_horizon_and_rejects_unknown(cfg):
    assert dataset_pin(cfg, 1)["dataset_version"] == "ds_20261010_nf1" and dataset_pin(cfg, 3)["dataset_version"] == "ds_20261010_nf3"
    assert snapshot_pin(cfg, 1)["horizon"] == 1 and snapshot_pin(cfg, 3)["horizon"] == 3
    with pytest.raises(ConfigError):
        dataset_pin(cfg, 7)
    with pytest.raises(ConfigError):
        snapshot_pin(cfg, 7)
    assert cfg["labels"]["threshold"] == 0.02 and cfg["labels"]["classes"] == ["down", "stable", "up"] and cfg["seed"] == 20261019


def _mutations():
    def drop_snapshot_pin(r): r["baseline_snapshot_pins"] = [p for p in r["baseline_snapshot_pins"] if p["horizon"] != 1]
    def empty_snapshot_pins(r): r["baseline_snapshot_pins"] = []
    def dup_snapshot_pin(r): r["baseline_snapshot_pins"].append(copy.deepcopy(r["baseline_snapshot_pins"][0]))
    def dup_dataset_pin(r): r["dataset_pins"].append(copy.deepcopy(r["dataset_pins"][0]))
    def drop_dataset_pin(r): r["dataset_pins"] = r["dataset_pins"][1:]
    def extra_pin_horizon7(r): r["baseline_snapshot_pins"].append({**r["baseline_snapshot_pins"][0], "horizon": 7})
    def threshold(r): r["labels"]["threshold"] = 0.5
    def swapped_classes(r): r["labels"]["classes"] = ["up", "stable", "down"]
    def primary_off(r): r["primary_requires_seen_hotel"] = False
    def horizons_dup(r): r["horizons"] = [1, 1]
    def horizons_unsupported(r): r["horizons"] = [1, 7]
    def horizons_empty(r): r["horizons"] = []
    def bad_hash(r): r["dataset_pins"][0]["output_checksums_sha256"] = "XYZ"
    def bad_snapshot_hash(r): r["baseline_snapshot_pins"][0]["snapshot_file_sha256"] = "0" * 63
    def missing_field(r): del r["dataset_pins"][1]["n_entries"]
    def missing_snapshot_field(r): del r["baseline_snapshot_pins"][1]["manifest_sha256"]
    def eval_horizons(r): r["dataset_pins"][0]["evaluation_horizons"] = [1, 3]
    def gain_range(r): r["panel"]["practical_gain_macro_f1"] = 1.5
    def momentum(r): r["panel"]["momentum_threshold"] = 0.05
    def tie(r): r["panel"]["tie_atol"] = 0.5
    def cv_missing(r): del r["cv"]["n_splits"]["3"]
    def seed(r): r["seed"] = "abc"
    def boot(r): r["bootstrap"]["n"] = 10
    return {k: v for k, v in locals().items() if callable(v)}


@pytest.mark.parametrize("name", sorted(_mutations()))
def test_validate_config_rejects_invalid_configuration(name, tmp_path):
    raw = _raw()
    _mutations()[name](raw)
    with pytest.raises(ConfigError, match="KHONG hop le"):
        load_config(_write(tmp_path, raw))
    with pytest.raises(ConfigError):
        validate_config(raw)


def test_validate_config_accepts_the_shipped_config_and_rejects_dropped_pins():
    validate_config(_raw())
    raw = _raw()
    raw["baseline_snapshot_pins"] = []
    with pytest.raises(ConfigError):
        validate_config(raw)


# ------------------------------------------------------------------ code identity

def test_code_manifest_is_v1_scoped_and_ignores_pycache(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\r\n", encoding="utf-8", newline="")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "b.py").write_text("y", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("z", encoding="utf-8")
    m = codeid.code_manifest(tmp_path)
    assert set(m["files"]) == {"a.py", "tests/t.py"} and m["root_name"] == tmp_path.name
    assert set(codeid.code_manifest(tmp_path, exclude_tests=True)["files"]) == {"a.py"}
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8", newline="")
    assert codeid.code_manifest(tmp_path)["code_sha256"] == m["code_sha256"]          # CRLF/LF khong doi hash
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8", newline="")
    assert codeid.code_manifest(tmp_path)["code_sha256"] != m["code_sha256"]
    real = codeid.code_manifest()
    assert real["root_name"] == "v1" and "cls_training/labels.py" in real["files"]


# ------------------------------------------------------------------ preflight (CLI)

def _run_preflight(*extra, script=None, isolated=False):
    cmd = [sys.executable] + (["-I"] if isolated else []) + ["-B", str(script or V1_DIR / "scripts" / "preflight_real_data.py"), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=900)


@needs_real_data
def test_preflight_end_to_end_numbers(tmp_path):
    out = tmp_path / "report.json"
    r = _run_preflight("--out", str(out))
    assert r.returncode == 0, r.stderr[-2000:]
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["config_stage"] == "code" and rep["upstream"]["mode"] == "packaged" and rep["upstream"]["verified"] is True
    h1, h3 = rep["horizons"]["h1"], rep["horizons"]["h3"]
    assert h1["rows_primary"] == {"train": 74777, "validation": 20414, "test": 20993} and h3["rows_primary"] == {"train": 57015, "validation": 10528, "test": 11054}
    assert h1["eligible_prediction_dates"] == {"train": 26, "validation": 7, "test": 7} and h1["short_window"] is False
    assert h3["eligible_prediction_dates"] == {"train": 22, "validation": 4, "test": 4} and h3["short_window"] is True
    assert h1["sufficiency"] == {"status": "exploratory", "failed_gates": ["train: eligible_prediction_dates 26 < 28"], "has_failed_gates": True}
    assert h3["sufficiency"]["status"] == "exploratory" and len(h3["sufficiency"]["failed_gates"]) == 3
    assert h1["snapshot"]["pin_checked"] and h3["snapshot"]["pin_checked"]
    for hz in (h1, h3):
        assert hz["feature_set_sizes"] == {"base36": 36, "a1_ratio": 42, "a2_no_history": 21}
        dry = hz["feature_dry_run"]
        assert {k: v["linear_n_columns"] for k, v in dry.items()} == {"base36": 64, "a1_ratio": 75, "a2_no_history": 35}
        assert all(not sp["all_nan_columns"] for v in dry.values() for sp in v["splits"].values())
        assert all(n == 0 for per_split in dry["a1_ratio"]["ratio_block_invalid"].values() for n in per_split.values())
    p1, p3 = h1["panel"], h3["panel"]
    assert p1["validation_comparator"] == "C2:pred_rf_l2" and p1["validation_comparator_scored_on_test"] is True and p1["is_substitute"] is False
    assert p3["validation_comparator"] == "C2:pred_xgb_ph-11" and p3["validation_comparator_scored_on_test"] is False and p3["is_substitute"] is True
    assert p3["test_available_comparator"] == "C2:pred_hgb_l2"
    assert sorted(p1["members_not_scored_on_test"]) == ["C2:pred_ridge", "C2:pred_xgb_ph-11"]
    assert sorted(p3["members_not_scored_on_test"]) == ["C2:pred_hgb_l1-00", "C2:pred_hgb_l1-04", "C2:pred_ridge", "C2:pred_xgb_abs-02", "C2:pred_xgb_ph-11"]
    rows1 = {r["id"]: r for r in p1["table"]}
    assert abs(rows1["C1:momentum"]["validation"]["macro_f1"] - 0.339512) < 5e-7 and abs(p1["validation_comparator_validation_macro_f1"] - 0.5148) < 5e-4
    rows3 = {r["id"]: r for r in p3["table"]}
    assert abs(rows3["C1:momentum"]["validation"]["macro_f1"] - 0.342220) < 5e-7
    assert p1["c3_roles"]["C3-A"] == "pred_xgb_abs-02" and p1["c3_roles"]["C3-B"] == "pred_hgb_l1-08"
    assert p3["c3_roles"]["C3-A"] == p3["c3_roles"]["C3-B"] == "pred_hgb_l1-ratio1"


def _bad_config_run(tmp_path, mutate, name):
    raw = _raw()
    mutate(raw)
    return _run_preflight("--config", str(_write(tmp_path, raw, name)))


@needs_real_data
@pytest.mark.parametrize("name,expect", [
    ("drop_snapshot_pin", "ConfigError"), ("empty_snapshot_pins", "ConfigError"), ("dup_snapshot_pin", "ConfigError"), ("dup_dataset_pin", "ConfigError"),
    ("threshold", "ConfigError"), ("swapped_classes", "ConfigError"), ("primary_off", "ConfigError"), ("missing_field", "ConfigError"),
])
def test_preflight_cli_rejects_invalid_config_before_touching_data(tmp_path, name, expect):
    r = _bad_config_run(tmp_path, _mutations()[name], f"{name}.yaml")
    assert r.returncode == 2 and "PREFLIGHT FAIL" in r.stderr and expect in r.stderr, r.stderr[-800:]


@needs_real_data
def test_preflight_cli_rejects_wrong_dataset_or_snapshot_pin(tmp_path):
    r = _bad_config_run(tmp_path, lambda raw: raw["dataset_pins"][0].update(output_checksums_sha256="0" * 64), "bad_ds.yaml")
    assert r.returncode == 2 and "PinError" in r.stderr
    r2 = _bad_config_run(tmp_path, lambda raw: raw["baseline_snapshot_pins"][0].update(snapshot_file_sha256="0" * 64), "bad_snap.yaml")
    assert r2.returncode == 2 and "SnapshotError" in r2.stderr


def _isolated_copy(tmp_path):
    root = tmp_path / "clean" / "ml" / "classification"
    shutil.copytree(V1_DIR, root / "v1", ignore=shutil.ignore_patterns("__pycache__", "tests"))
    assert not (tmp_path / "clean" / "ml" / "regression").exists() and not (tmp_path / "clean" / "ml" / "shared").exists()       # nguon KHONG ton tai
    return root / "v1"


@needs_real_data
def test_preflight_runs_in_a_clean_tree_without_regression_or_shared_sources(tmp_path):
    v1 = _isolated_copy(tmp_path)
    out = tmp_path / "isolated_report.json"
    r = _run_preflight("--datasets-root", str(DATASETS_ROOT), "--snapshot-dir", str(SNAPSHOT_DIR), "--upstream-check", "packaged", "--out", str(out),
                       script=v1 / "scripts" / "preflight_real_data.py", isolated=True)
    assert r.returncode == 0, r.stderr[-2000:]
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["upstream"]["mode"] == "packaged" and rep["horizons"]["h1"]["rows_primary"]["test"] == 20993
    r2 = _run_preflight("--datasets-root", str(DATASETS_ROOT), "--snapshot-dir", str(SNAPSHOT_DIR), "--upstream-check", "dev", script=v1 / "scripts" / "preflight_real_data.py", isolated=True)
    assert r2.returncode == 2 and "PREFLIGHT FAIL" in r2.stderr                      # che do dev can nguon: that bai ro rang, khong im lang


@needs_real_data
def test_packaged_preflight_detects_an_edited_upstream_copy(tmp_path):
    v1 = _isolated_copy(tmp_path)
    cv = v1 / "cls_training" / "upstream" / "cv.py"
    cv.write_text(cv.read_text(encoding="utf-8") + "\n# sua tay\n", encoding="utf-8")
    r = _run_preflight("--datasets-root", str(DATASETS_ROOT), "--snapshot-dir", str(SNAPSHOT_DIR), script=v1 / "scripts" / "preflight_real_data.py", isolated=True)
    assert r.returncode == 2 and "UPSTREAM LECH" in r.stderr and "cv.py" in r.stderr


@needs_real_data
def test_snapshot_manifest_records_the_current_export_code(cfg):
    """Neu snapshot.py hoac export script doi sau lan xuat, manifest/pin cu khong con dai dien: bat buoc xuat lai + cap nhat pin."""
    def lf_sha(p):
        return hashlib.sha256(p.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")).hexdigest()

    for h in (1, 3):
        manifest = json.loads((SNAPSHOT_DIR / f"baseline_snapshot_h{h}_manifest.json").read_text(encoding="utf-8"))
        rec = manifest["export_code_lf_sha256"]
        assert rec["cls_training/snapshot.py"] == lf_sha(PKG / "snapshot.py"), "snapshot.py da doi sau lan xuat: xuat lai snapshot + cap nhat baseline_snapshot_pins"
        assert rec["scripts/export_baseline_snapshot.py"] == lf_sha(V1_DIR / "scripts" / "export_baseline_snapshot.py"), "export script da doi: xuat lai"
