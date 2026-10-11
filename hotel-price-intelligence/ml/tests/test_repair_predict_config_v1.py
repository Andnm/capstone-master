"""audit_tools/repair_predict_config_v1.py — bien lai du doan config tu snapshot P4 DA TIN CAY + manifest ma moi (thuan, khong DB)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1] / "audit_tools"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pc = _load(TOOLS / "repair_predict_config_v1.py", "repair_predict_config_v1")
rp = pc.rp
CHANGED = list(rp.EXPECTED_CHANGED_FILES)
OLD_FILES = {"hotel-price-intelligence/ml/dataset_builder/a.py": "1" * 64, "hotel-price-intelligence/ml/dataset_builder/b.py": "2" * 64, **{name: "3" * 64 for name in CHANGED}}
EXCLUDED = ["app.core"]


def manifest(files):
    return {"files": dict(sorted(files.items())), "excluded_backend_prefixes": EXCLUDED, "code_sha256": rp.code_sha256_of(files, EXCLUDED)}


OLD_CONFIG = {"purpose": "dev", "evaluation_horizons": [1], "builder_code": manifest(OLD_FILES)}
OLD_CONFIG_SHA = rp.config_sha256(OLD_CONFIG)
NEW_FILES = {**OLD_FILES, **{name: "4" * 64 for name in CHANGED}}
NEW_MANIFEST = manifest(NEW_FILES)
VERSION = "ds_20261006_dev1b"


def snapshot(**override):
    doc = {"run_id": "r", "trusted": True, "evaluation": {"ok": True, "violations": [], "versions": {VERSION: {"config": OLD_CONFIG}}}}
    doc.update(override)
    return doc


def write_snapshot(tmp_path, doc=None):
    path = tmp_path / "manifest_snapshot_r.json"
    path.write_text(json.dumps(doc or snapshot()), encoding="utf-8")
    return path


def run(tmp_path, *, doc=None, pin=OLD_CONFIG_SHA, files=None, out=None):
    out = out or tmp_path / "prediction.json"
    rc = pc.main(["--snapshot", str(write_snapshot(tmp_path, doc)), "--old-version", VERSION, "--expected-old-config-sha256", pin, "--root", str(tmp_path), "--out", str(out)],
                 manifest_fn=lambda root: manifest(files or NEW_FILES))
    return rc, out


def test_a_trusted_snapshot_and_a_matching_pin_produce_the_receipt_the_parity_gate_expects(tmp_path, capsys):
    rc, out = run(tmp_path)
    receipt = json.loads(out.read_text(encoding="utf-8"))
    expected = rp.predict_new_config(OLD_CONFIG, NEW_MANIFEST, old_config_sha256=OLD_CONFIG_SHA)
    assert rc == 0 and receipt == rp.prediction_receipt(expected) and receipt["kind"] == "config_prediction_receipt" and receipt["ok"] is True
    assert receipt["old_config_sha256"] == OLD_CONFIG_SHA and receipt["new_code_sha256"] == NEW_MANIFEST["code_sha256"] and receipt["predicted_config_sha256"] != OLD_CONFIG_SHA
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is True and summary["receipt_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()


def test_the_predicted_config_equals_the_old_config_with_only_builder_code_replaced():
    outcome = pc.predict(snapshot(), VERSION, expected_old_config_sha256=OLD_CONFIG_SHA, new_manifest=NEW_MANIFEST)
    assert outcome["predicted_config"] == {**OLD_CONFIG, "builder_code": NEW_MANIFEST} and all(outcome["other_keys_identical"].values())


@pytest.mark.parametrize("doc", [snapshot(trusted=False), snapshot(trusted=None), {k: v for k, v in snapshot().items() if k != "trusted"},
                                 snapshot(evaluation={"ok": False, "violations": [], "versions": {VERSION: {"config": OLD_CONFIG}}}),
                                 snapshot(evaluation={"ok": True, "violations": ["x"], "versions": {VERSION: {"config": OLD_CONFIG}}}),
                                 snapshot(evaluation={"ok": True, "violations": [], "versions": {}}), snapshot(evaluation={"ok": True, "violations": [], "versions": {VERSION: {"config": "x"}}})])
def test_an_untrusted_or_incomplete_snapshot_is_refused_and_no_receipt_is_written(tmp_path, capsys, doc):
    rc, out = run(tmp_path, doc=doc)
    assert rc == 1 and not out.exists() and json.loads(capsys.readouterr().out)["ok"] is False


def test_a_wrong_old_config_pin_is_refused_with_the_violation_and_no_receipt(tmp_path, capsys):
    rc, out = run(tmp_path, pin="9" * 64)
    assert rc == 1 and not out.exists() and "old_config_sha_mismatch" in json.loads(capsys.readouterr().out)["violations"]


def test_a_change_set_other_than_the_allowed_files_is_refused(tmp_path, capsys):
    rc, out = run(tmp_path, files={**NEW_FILES, "hotel-price-intelligence/ml/dataset_builder/a.py": "5" * 64})
    assert rc == 1 and not out.exists() and any(v.startswith("changed_files_mismatch") for v in json.loads(capsys.readouterr().out)["violations"])
    rc, out = run(tmp_path, files={**NEW_FILES, "hotel-price-intelligence/ml/dataset_builder/c.py": "6" * 64}, out=tmp_path / "other.json")
    assert rc == 1 and not (tmp_path / "other.json").exists()                                                    # them file moi cung bi tu choi


def test_an_existing_output_or_a_bad_pin_exits_two_without_reading_anything(tmp_path, capsys):
    (tmp_path / "exists.json").write_text("x", encoding="utf-8")
    rc, _ = run(tmp_path, out=tmp_path / "exists.json")
    assert rc == 2 and (tmp_path / "exists.json").read_text(encoding="utf-8") == "x"
    rc, _ = run(tmp_path, pin="short", out=tmp_path / "o2.json")
    assert rc == 2 and not (tmp_path / "o2.json").exists()
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["error"] == "bad_pin_or_out_exists"


def test_a_missing_snapshot_file_is_reported_without_a_traceback(tmp_path, capsys):
    rc = pc.main(["--snapshot", str(tmp_path / "nope.json"), "--old-version", VERSION, "--expected-old-config-sha256", OLD_CONFIG_SHA, "--root", str(tmp_path), "--out", str(tmp_path / "o.json")],
                 manifest_fn=lambda root: NEW_MANIFEST)
    assert rc == 1 and "FileNotFoundError" in json.loads(capsys.readouterr().out)["error"] and not (tmp_path / "o.json").exists()


def test_the_real_manifest_tool_is_the_default_source_of_the_new_code_identity(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(pc.rm, "compute_manifest", lambda root: seen.setdefault("root", root) and NEW_MANIFEST)
    out = tmp_path / "default.json"
    rc = pc.main(["--snapshot", str(write_snapshot(tmp_path)), "--old-version", VERSION, "--expected-old-config-sha256", OLD_CONFIG_SHA, "--root", str(tmp_path / "repo"), "--out", str(out)])
    assert rc == 0 and seen["root"] == tmp_path / "repo" and json.loads(out.read_text(encoding="utf-8"))["new_code_sha256"] == NEW_MANIFEST["code_sha256"]


def test_the_receipt_is_created_exclusively_even_if_the_file_appears_after_the_precheck(tmp_path):
    out = tmp_path / "race.json"

    def manifest_fn(root):
        out.write_text("someone else", encoding="utf-8")                                                         # xuat hien SAU kiem tra ton tai ban dau
        return NEW_MANIFEST

    with pytest.raises(FileExistsError):
        pc.main(["--snapshot", str(write_snapshot(tmp_path)), "--old-version", VERSION, "--expected-old-config-sha256", OLD_CONFIG_SHA, "--root", str(tmp_path), "--out", str(out)], manifest_fn=manifest_fn)
    assert out.read_text(encoding="utf-8") == "someone else"
