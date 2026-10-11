"""GPT 130: regression NGU NGHIA cua lenh init — `actual argv cua spec -> CHINH init_dataset_build.main() (CLI parsing + build_config + policy_descriptor that) -> canonical config sha` phai bang config DU DOAN
(config cu da tin cay, chi thay `builder_code`). Chay trong SUBPROCESS `python -I` voi harness chan `.env`/Settings/DB (xem harness_init_semantics.py); SQL le => loi. Khong chi assert argv bang argv tu suy ra.

Su co 10/10: argv thieu `--n1-policy` => config `c2717818…` (khac du doan `39d9eca0…`) => verify FAIL. Test nay tai tao dung gia tri do va khoa lai de khong lap."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ML = TESTS.parent
AUDIT = ML.parent / "audit_tools"
HARNESS = TESTS / "harness_init_semantics.py"
SNAPSHOT = Path(r"D:\MSE\CAPSTONE\outputs\model-diagnosis-20261008\warehouse_audit\manifest_snapshot_20261009_181037_468216267760.json")
PRED_DIR = Path(r"D:\MSE\CAPSTONE\outputs\repair-config-predictions-20261009-ph1")
OLD_CONFIG_SHA = {"h1": "6494c1d4ece098e12855644f26780eecfe6706be32baee8a8f486b8b10069928"}
FAILED_STORED_H1 = "c2717818dd03174d54b831d75b2f3d6c46c91402bd331c5a28c513d9a7bcf436"                       # config luu that cua ds_20261009_nf1 (thieu n1)

pytestmark = pytest.mark.skipif(not SNAPSHOT.exists() or not PRED_DIR.exists(), reason="trusted P4 snapshot / prediction receipts not present")


def load(path: Path, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hp = load(Path(os.environ.get("HEAVY_PLAN_UNDER_TEST", str(AUDIT / "repair_heavy_plan_v1.py"))), "hp_for_init_semantics")                    # ghi de CHI de chay mutation harness
predict = load(AUDIT / "repair_predict_config_v1.py", "predict_for_init_semantics")


def clean_env() -> dict[str, str]:
    return {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1"}                      # khong DB_*/secret nao


def run_init(argv: list[str], *, min_runs=3, min_coverage=0.8, batch_id="b20261004_3src") -> dict:
    payload = json.dumps({"argv": argv, "min_runs": min_runs, "min_coverage": min_coverage, "batch_id": batch_id})
    done = subprocess.run([sys.executable, "-I", str(HARNESS), str(ML), payload], capture_output=True, text=True, env=clean_env(), timeout=300, cwd=str(TESTS))
    line = next((x for x in done.stdout.splitlines() if x.startswith("HARNESS_RESULT=")), None)
    assert line is not None, (done.returncode, done.stdout[-1500:], done.stderr[-1500:])
    return json.loads(line[len("HARNESS_RESULT="):])


def spec_argv(key: str, version: str | None = None) -> list[str]:
    name, horizon = hp.VERSIONS[key]
    db = {"app_user": "x", "db_scope": [hp.TARGET_DB], "protected_dbs": []}
    spec = hp.init_spec(python=sys.executable, name=f"ph4_init_{key}", target=hp.TARGET_DB, version=version or name, horizon=horizon, db=db)
    return list(spec["argv"][2:])                                                                       # bo python + script: phan con lai la argv CLI that


def replace_value(argv: list[str], flag: str, value: str) -> list[str]:
    out = list(argv)
    out[out.index(flag) + 1] = value
    return out


def without_flag(argv: list[str], flag: str) -> list[str]:
    out = list(argv)
    index = out.index(flag)
    del out[index:index + 2]
    return out


@pytest.fixture(scope="module")
def expected():
    snapshot = json.loads(SNAPSHOT.read_bytes().decode("utf-8"))
    manifest = predict.rm.compute_manifest(ML.parent.parent)
    out = {}
    for key, old_version in (("h1", "ds_20261006_dev1b"), ("h3", "ds_20261006_dev3b")):
        receipt = json.loads((PRED_DIR / f"prediction_receipt_dev{key[1]}b.json").read_bytes().decode("utf-8"))
        old = predict.old_config_from_snapshot(snapshot, old_version)
        result = predict.rp.predict_new_config(old, manifest, old_config_sha256=receipt["old_config_sha256"])
        out[key] = {"receipt": receipt, "config": result["predicted_config"] if "predicted_config" in result else result["config"], "sha": receipt["predicted_config_sha256"]}
    return out


def init_call(result: dict) -> dict:
    inits = [c for c in result["calls"] if c["event"] == "init"]
    assert result["rc"] == 0 and len(inits) == 1, (result["rc"], result["stdout"][-800:], result["stderr"][-800:])
    return inits[0]


# =========================================================================== duong dung
@pytest.mark.parametrize("key", ["h1", "h3"])
def test_the_actual_init_argv_of_the_spec_produces_exactly_the_predicted_config_object_and_sha(expected, key):
    result = run_init(spec_argv(key), min_runs=3, min_coverage=0.8)
    call = init_call(result)
    assert call["config_sha256"] == expected[key]["sha"], (key, call["config_sha256"], expected[key]["sha"])
    assert call["config"] == expected[key]["config"] and call["dataset_version"] == hp.VERSIONS[key][0]
    assert f"build_config_sha256 = {expected[key]['sha']}" in result["stdout"]
    assert set(call["config"]["n1_policy"]) >= {"policy_sha256", "excluded_hotels", "evidence"} and len(call["config"]["eligibility_overrides"]["exclude_hotels"]) == 5


def test_the_dataset_version_name_is_not_part_of_the_canonical_config(expected):
    a = init_call(run_init(spec_argv("h1")))
    b = init_call(run_init(spec_argv("h1", version="ds_20261011_whatever")))
    assert a["config_sha256"] == b["config_sha256"] == expected["h1"]["sha"] and "dataset_version" not in a["config"]


# =========================================================================== su co 10/10 tai tao + moi thay doi phai khien cong that bai
def test_the_failed_09_10_argv_without_n1_policy_reproduces_the_stored_hash_and_never_equals_the_prediction(expected):
    broken = without_flag(spec_argv("h1"), "--n1-policy")
    call = init_call(run_init(broken))
    assert call["config_sha256"] == FAILED_STORED_H1 != expected["h1"]["sha"]
    assert call["config"]["n1_policy"] is None and call["config"]["eligibility_overrides"]["exclude_hotels"] == []


def changed(argv):
    return {
        "exclude_hotel": [*argv, "--exclude-hotel", "some-hotel"],
        "horizon_3": replace_value(argv, "--evaluation-horizons", "3"),
        "horizon_1_3": replace_value(argv, "--evaluation-horizons", "1,3"),
        "cutoff": replace_value(argv, "--anomaly-cutoff", "2026-10-06T00:00:00Z"),
        "purge": [*argv, "--purge-gap-days", "5"],
        "seed": [*argv, "--random-seed", "1"],
        "required_label": [*argv, "--required-label", "h3:train"],
    }


@pytest.mark.parametrize("label", ["exclude_hotel", "horizon_3", "horizon_1_3", "cutoff", "purge", "seed", "required_label"])
def test_any_change_of_a_config_flag_makes_the_config_differ_from_the_prediction(expected, label):
    argv = spec_argv("h1")
    result = run_init(changed(argv)[label])
    if result["rc"] == 0:
        assert init_call(result)["config_sha256"] != expected["h1"]["sha"], label
    else:                                                                                                # tu choi (vd horizon/purge khong hop le) phai xay ra TRUOC khi ghi
        assert result["rc"] == 2 and not any(c["event"] == "init" for c in result["calls"]), (label, result["stderr"][-300:])


def test_a_batch_id_that_equals_the_default_changes_nothing_but_a_different_batch_changes_the_config(expected):
    same = init_call(run_init([*spec_argv("h1"), "--batch-id", "b20261004_3src"]))
    other = init_call(run_init([*spec_argv("h1"), "--batch-id", "b20261004_other"]))
    assert same["config_sha256"] == expected["h1"]["sha"] and other["config_sha256"] != expected["h1"]["sha"]


def test_different_thresholds_from_the_pinned_etl_config_change_the_config(expected):
    assert init_call(run_init(spec_argv("h1"), min_runs=4))["config_sha256"] != expected["h1"]["sha"]
    assert init_call(run_init(spec_argv("h1"), min_coverage=0.7))["config_sha256"] != expected["h1"]["sha"]


def policy_copy(tmp_path: Path) -> Path:
    target = tmp_path / "n1"
    shutil.copytree(ML / "policies" / "n1", target)
    return target


def test_a_policy_directory_copy_with_identical_bytes_is_accepted_and_changes_nothing_except_the_path(expected, tmp_path):
    copy = policy_copy(tmp_path)
    argv = replace_value(spec_argv("h1"), "--n1-policy", str(copy / "n1_policy_v1.json"))
    call = init_call(run_init(argv))
    assert call["config_sha256"] == expected["h1"]["sha"]                                               # duong dan khong nam trong config; noi dung moi la identity


@pytest.mark.parametrize("which", ["n1_hotels_from_scan.json", "n1_affected_items.json", "n1_matcher_replay.json", "n1_scan_identity.json"])
def test_changing_one_byte_of_an_evidence_file_is_refused_before_any_db_access(tmp_path, which):
    copy = policy_copy(tmp_path)
    path = copy / which
    path.write_bytes(path.read_bytes() + b" ")
    result = run_init(replace_value(spec_argv("h1"), "--n1-policy", str(copy / "n1_policy_v1.json")))
    assert result["rc"] == 2 and not any(c["event"] in ("connect", "init") for c in result["calls"]) and "FAIL" in result["stderr"]


def test_changing_the_policy_file_itself_either_fails_or_changes_the_config(expected, tmp_path):
    copy = policy_copy(tmp_path)
    policy = copy / "n1_policy_v1.json"
    doc = json.loads(policy.read_bytes().decode("utf-8"))
    doc["reason"] = str(doc.get("reason", "")) + " (edited)"
    policy.write_bytes(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    result = run_init(replace_value(spec_argv("h1"), "--n1-policy", str(policy)))
    if result["rc"] == 0:
        assert init_call(result)["config_sha256"] != expected["h1"]["sha"]
    else:
        assert result["rc"] == 2 and not any(c["event"] in ("connect", "init") for c in result["calls"])


def test_a_missing_policy_file_is_refused_before_any_db_access(tmp_path):
    result = run_init(replace_value(spec_argv("h1"), "--n1-policy", str(tmp_path / "nope.json")))
    assert result["rc"] == 2 and not any(c["event"] in ("connect", "init") for c in result["calls"])


def test_dry_run_would_write_nothing_so_the_spec_must_never_contain_it(expected):
    argv = [*spec_argv("h1"), "--dry-run"]
    result = run_init(argv)
    assert result["rc"] == 0 and not any(c["event"] == "init" for c in result["calls"]) and "[dry-run]" in result["stdout"]
    assert "--dry-run" not in spec_argv("h1")


def test_the_harness_blocks_unexpected_db_sql_and_settings(tmp_path):
    """Harness an toan: SQL la hoac Settings => that bai ro rang, khong ket noi that."""
    done = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path[:0]=[sys.argv[1]]; sys.modules['dotenv']=None\ntry:\n    import dotenv\nexcept ImportError:\n    print('blocked')", str(ML)],
                          capture_output=True, text=True, env=clean_env(), timeout=60)
    assert done.stdout.strip() == "blocked"
    text = HARNESS.read_text(encoding="utf-8")
    assert 'sys.modules[blocked] = None' in text and "unexpected SQL in harness" in text and "DB access blocked by harness" in text
