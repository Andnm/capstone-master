"""Policy N1 (GPT file 54/56): ghim policy + bang chung bat bien, thieu/lech => fail, hotel cua policy bi loai qua moi regime, official bat buoc co policy. Thuan, khong MySQL."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset_builder import config as cfg  # noqa: E402
from dataset_builder import n1_policy as n1  # noqa: E402
from dataset_builder import validation  # noqa: E402
from test_horizon_contract import BASE, SCRIPTS  # noqa: E402

HOTELS_V1 = ["chez-mimosa-ho-chi-minh", "chez-van", "ks-huy-hoang-airport", "mila-homestay", "starview-villa"]


@pytest.fixture()
def policy_dir(tmp_path):
    """Ban sao policy + bang chung that vao thu muc tam de test doi/xoa byte ma khong dung toi repo."""
    target = tmp_path / "n1"
    shutil.copytree(n1.POLICY_DIR, target)
    return target


def _rewrite(path: Path, **changes):
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy.update(changes)
    path.write_text(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


# ------------------------------------------------------------------ descriptor tu policy that cua repo
def test_repo_policy_v1_lists_the_five_known_hotels_and_pins_every_evidence_file():
    descriptor = n1.policy_descriptor()
    assert descriptor["excluded_hotels"] == HOTELS_V1 and descriptor["policy_version"] == "n1-policy-1.2.0"
    assert {e["name"] for e in descriptor["evidence"]} == {"n1_hotels_from_scan.json", "n1_affected_items.json", "n1_matcher_replay.json", "n1_scan_identity.json", "parity_on_artifacts.out"}
    assert all(len(e["sha256"]) == 64 and e["bytes"] > 0 and e["scope"] for e in descriptor["evidence"])
    assert descriptor["scan_identity"]["scan_database"] == "hotel_price_intel_fullscan_20260924" and "MOT ngay check-in" in descriptor["scan_identity"]["scope"]
    assert [r["run_id"] for r in descriptor["scan_identity"]["runs"]] == [1, 3] and all(r["scraper_version"] and r["git_commit"] for r in descriptor["scan_identity"]["runs"])
    assert n1.policy_descriptor() == descriptor                                             # xac dinh: doc lai cho cung ket qua


# ------------------------------------------------------------------ mutation: bang chung / policy bi doi hoac thieu
def test_a_tampered_or_missing_evidence_file_is_rejected(policy_dir):
    path = policy_dir / n1.POLICY_FILE
    (policy_dir / "n1_affected_items.json").write_bytes((policy_dir / "n1_affected_items.json").read_bytes() + b" ")
    with pytest.raises(n1.N1PolicyError, match="n1_affected_items.json lech hash"):
        n1.policy_descriptor(path)
    (policy_dir / "n1_affected_items.json").unlink()
    with pytest.raises(n1.N1PolicyError, match="thieu file bang chung n1_affected_items.json"):
        n1.policy_descriptor(path)


@pytest.mark.parametrize("changes, message", [
    ({"excluded_hotels": []}, "excluded_hotels"), ({"excluded_hotels": ["b", "a"]}, "sap xep"), ({"excluded_hotels": ["a", "a"]}, "sap xep"),
    ({"excluded_hotels": [1, 2]}, "excluded_hotels"), ({"policy_version": ""}, "policy_version"), ({"reason": None}, "reason"), ({"schema_version": 2}, "schema_version"),
    ({"scan_identity": {"scan_database": "x"}}, "scan_identity thieu khoa"), ({"scan_identity": None}, "scan_identity"),
    ({"evidence": []}, "evidence"), ({"evidence": [{"name": "../x", "sha256": "0" * 64, "scope": "s"}]}, "ten file don gian"),
    ({"evidence": [{"name": "n1_affected_items.json", "sha256": "xyz", "scope": "s"}]}, "sha256 hop le"),
])
def test_invalid_policy_documents_are_rejected(policy_dir, changes, message):
    _rewrite(policy_dir / n1.POLICY_FILE, **changes)
    with pytest.raises(n1.N1PolicyError, match=message):
        n1.policy_descriptor(policy_dir / n1.POLICY_FILE)


def test_missing_or_non_json_policy_is_rejected(tmp_path):
    with pytest.raises(n1.N1PolicyError, match="khong tim thay"):
        n1.policy_descriptor(tmp_path / "none.json")
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    with pytest.raises(n1.N1PolicyError, match="khong doc duoc JSON"):
        n1.policy_descriptor(tmp_path / "bad.json")


def test_changing_the_declared_evidence_hash_without_the_bytes_is_rejected(policy_dir):
    path = policy_dir / n1.POLICY_FILE
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["evidence"][0]["sha256"] = "0" * 64
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(n1.N1PolicyError, match="lech hash khai bao"):
        n1.policy_descriptor(path)


# ------------------------------------------------------------------ ghim vao config + verify moi phien
def test_config_pins_the_descriptor_merges_hotels_and_changes_the_identity():
    plain = cfg.build_config(**BASE)
    pinned = cfg.build_config(**BASE, n1_policy=n1.policy_descriptor(), exclude_hotels=("operator-hotel",))
    assert plain["n1_policy"] is None and plain["eligibility_overrides"]["exclude_hotels"] == []
    assert pinned["eligibility_overrides"]["exclude_hotels"] == sorted([*HOTELS_V1, "operator-hotel"])
    assert pinned["eligibility_overrides"]["sources"] == {"operator": ["operator-hotel"], "n1_policy": HOTELS_V1}
    assert cfg.config_sha256(pinned) != cfg.config_sha256(plain)
    other = cfg.build_config(**BASE, n1_policy={**n1.policy_descriptor(), "policy_version": "n1-policy-9"})
    assert cfg.config_sha256(other) != cfg.config_sha256(pinned)


def test_verify_policy_passes_unchanged_and_fails_after_any_change(policy_dir):
    config = cfg.build_config(**BASE, n1_policy=n1.policy_descriptor(policy_dir / n1.POLICY_FILE))
    n1.verify_policy(config, policy_dir=policy_dir)                                          # nguyen ven
    n1.verify_policy(cfg.build_config(**BASE))                                               # khong dung policy: hop le
    (policy_dir / "n1_hotels_from_scan.json").write_bytes((policy_dir / "n1_hotels_from_scan.json").read_bytes() + b"\n")
    with pytest.raises(n1.N1PolicyError, match="lech hash"):
        n1.verify_policy(config, policy_dir=policy_dir)
    (policy_dir / "n1_hotels_from_scan.json").write_bytes(json.dumps([]).encode())          # va hash khai bao khong con khop
    with pytest.raises(n1.N1PolicyError):
        n1.verify_policy(config, policy_dir=policy_dir)


def test_policy_edit_that_stays_internally_consistent_still_fails_against_the_pinned_descriptor(policy_dir):
    path = policy_dir / n1.POLICY_FILE
    config = cfg.build_config(**BASE, n1_policy=n1.policy_descriptor(path))
    _rewrite(path, excluded_hotels=sorted([*HOTELS_V1, "them-hotel"]))                       # tu nhat quan nhung khac policy_sha256/danh sach da ghim
    n1.policy_descriptor(path)                                                               # van hop le tu than
    with pytest.raises(n1.N1PolicyError, match="DA DOI so voi luc init"):
        n1.verify_policy(config, policy_dir=policy_dir)


def test_snapshot_copies_exact_bytes_and_a_manifest(tmp_path, policy_dir):
    config = cfg.build_config(**BASE, n1_policy=n1.policy_descriptor(policy_dir / n1.POLICY_FILE))
    hashes = n1.snapshot_files(config, tmp_path / "out", policy_dir=policy_dir)
    base = tmp_path / "out" / n1.SNAPSHOT_SUBDIR
    for name in [n1.POLICY_FILE, *(e["name"] for e in config["n1_policy"]["evidence"])]:
        assert (base / name).read_bytes() == (policy_dir / name).read_bytes() and f"{n1.SNAPSHOT_SUBDIR}/{name}" in hashes
    manifest = json.loads((base / n1.INPUT_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["pinned_in_config"] == config["n1_policy"] and f"{n1.SNAPSHOT_SUBDIR}/{n1.INPUT_MANIFEST}" in hashes
    assert n1.snapshot_files(cfg.build_config(**BASE), tmp_path / "none") == {}              # khong policy => khong ghi gi
    assert not (tmp_path / "none").exists()


def test_snapshot_refuses_when_the_policy_changed_after_init(tmp_path, policy_dir):
    config = cfg.build_config(**BASE, n1_policy=n1.policy_descriptor(policy_dir / n1.POLICY_FILE))
    (policy_dir / "parity_on_artifacts.out").write_bytes(b"changed")
    with pytest.raises(n1.N1PolicyError):
        n1.snapshot_files(config, tmp_path / "out", policy_dir=policy_dir)
    assert not (tmp_path / "out").exists()


# ------------------------------------------------------------------ validation: artifact + official
def _artifact(tmp_path, policy_dir, *, leak=False):
    descriptor = n1.policy_descriptor(policy_dir / n1.POLICY_FILE)
    config = cfg.build_config(**BASE, n1_policy=descriptor)
    out = tmp_path / "ds"
    stored = n1.snapshot_files(config, out, policy_dir=policy_dir)
    frame = pd.DataFrame({"hotel_id": ["ok-hotel", HOTELS_V1[0]] if leak else ["ok-hotel", "other-hotel"]})
    return out, config, stored, frame


def test_n1_check_accepts_a_consistent_artifact(tmp_path, policy_dir):
    out, config, stored, frame = _artifact(tmp_path, policy_dir)
    result = validation._n1_policy_check(out, config, stored, frame)
    assert result["ok"] is True, result


def test_n1_check_rejects_every_drift(tmp_path, policy_dir):
    out, config, stored, frame = _artifact(tmp_path, policy_dir)
    base = out / n1.SNAPSHOT_SUBDIR
    assert validation._n1_policy_check(out, config, {k: v for k, v in stored.items() if "n1_affected_items" not in k}, frame)["ok"] is False   # thieu trong checksum DB
    (base / "n1_matcher_replay.json").write_bytes(b"tampered")
    assert any("sha256 trong artifact khac" in p for p in validation._n1_policy_check(out, config, stored, frame)["detail"])
    (base / "n1_matcher_replay.json").unlink()
    assert any("thieu inputs/n1_policy/n1_matcher_replay.json" in p for p in validation._n1_policy_check(out, config, stored, frame)["detail"])
    out2, config2, stored2, frame2 = _artifact(tmp_path / "leak", policy_dir, leak=True)
    assert any("van co mat trong Parquet" in p for p in validation._n1_policy_check(out2, config2, stored2, frame2)["detail"])
    out3, config3, stored3, frame3 = _artifact(tmp_path / "manifest", policy_dir)
    manifest_path = out3 / n1.SNAPSHOT_SUBDIR / n1.INPUT_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["policy_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert any("khong khop descriptor" in p for p in validation._n1_policy_check(out3, config3, stored3, frame3)["detail"])
    dropped = {**config, "eligibility_overrides": {"exclude_hotels": [], "sources": {}}}
    assert any("khong nam het" in p for p in validation._n1_policy_check(out, dropped, stored, frame)["detail"])


def test_official_without_a_policy_fails_validation_but_rehearsal_does_not(tmp_path):
    frame = pd.DataFrame({"hotel_id": ["a"]})
    official = cfg.build_config(**{**BASE, "purpose": "official"})
    assert validation._n1_policy_check(tmp_path, official, {}, frame)["ok"] is False
    assert validation._n1_policy_check(tmp_path, cfg.build_config(**BASE), {}, frame)["ok"] is True


def test_contract_check_includes_the_n1_summary(tmp_path):
    from dataset_builder.export import dataset_contract
    from test_horizon_contract import _contract_inputs

    out, config, contract, stored = _contract_inputs(tmp_path)
    assert contract["n1_policy"] is None and validation._contract_check(out, config, stored, dataset_version="ds_20261006_t_h7")["ok"] is True
    pinned = cfg.build_config(**BASE, evaluation_horizons=[7], n1_policy=n1.policy_descriptor())
    new_contract = dataset_contract(dataset_version="ds_20261006_t_h7", config=pinned, split_report={"plan": contract["split_plan"]},
                                    sufficiency=json.loads((out / "sufficiency_report.json").read_text(encoding="utf-8")), calendar_sha=pinned["calendar_input"]["sha256"])
    assert new_contract["n1_policy"]["excluded_hotels"] == HOTELS_V1 and len(new_contract["n1_policy"]["policy_sha256"]) == 64
    (out / "dataset_contract.json").write_text(json.dumps({**new_contract, "n1_policy": None}), encoding="utf-8")
    assert validation._contract_check(out, pinned, stored, dataset_version="ds_20261006_t_h7")["ok"] is False        # contract bo n1_policy trong khi config co


# ------------------------------------------------------------------ training: official can contract co n1_policy
def test_training_rejects_an_official_contract_without_a_valid_n1_policy(tmp_path):
    from test_training import make_dataset, write_checksums
    from training.provenance import DatasetVerificationError, verify_dataset

    ds = make_dataset(tmp_path, n_days=30, n_series=4, version="ds_n1a", evaluation_horizons=(7,), purpose="official", status="primary_eligible")
    assert verify_dataset(ds)["contract"]["purpose"] == "official"
    contract = json.loads((ds / "dataset_contract.json").read_text(encoding="utf-8"))
    rows = len(pd.read_parquet(ds / "samples.parquet"))
    for bad, message in ((None, "n1_policy thieu"), ({"policy_version": "v", "policy_sha256": "xyz", "excluded_hotels": ["a"]}, "n1_policy khong hop le"),
                         ({"policy_version": "v", "policy_sha256": "9" * 64, "excluded_hotels": []}, "n1_policy khong hop le")):
        (ds / "dataset_contract.json").write_text(json.dumps({**contract, "n1_policy": bad}), encoding="utf-8")
        write_checksums(ds, rows=rows)
        with pytest.raises(DatasetVerificationError, match=message):
            verify_dataset(ds)


# ------------------------------------------------------------------ init CLI: official bat buoc policy, loi truoc moi ket noi DB
def _load_init():
    spec = importlib.util.spec_from_file_location("init_dataset_build", SCRIPTS / "init_dataset_build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_init_cli_requires_a_policy_for_official_and_rejects_a_bad_policy_before_any_connection(monkeypatch, capsys, policy_dir):
    module = _load_init()
    monkeypatch.setattr(module, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("khong duoc ket noi DB")))
    base = ["init_dataset_build.py", "--database", "x", "--dataset-version", "ds_20261006_x"]
    monkeypatch.setattr(sys, "argv", [*base, "--purpose", "official"])
    assert module.main() == 2 and "bat buoc --n1-policy" in capsys.readouterr().err
    (policy_dir / "n1_affected_items.json").write_bytes(b"x")
    monkeypatch.setattr(sys, "argv", [*base, "--purpose", "rehearsal", "--n1-policy", str(policy_dir / n1.POLICY_FILE)])
    assert module.main() == 2 and "lech hash" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", [*base, "--purpose", "rehearsal", "--n1-policy", str(policy_dir / "missing.json")])
    assert module.main() == 2 and "khong tim thay" in capsys.readouterr().err


# ------------------------------------------------------------------ UTC provenance (GPT file 60 C59-M2)
def test_policy_pins_the_real_utc_times_of_the_scan_runs():
    """Gia tri DB-verified (session UTC): run 1 va 3; truoc day policy ghim gio VN duoi nhan _utc (sai 7 gio)."""
    scan = n1.policy_descriptor()["scan_identity"]
    runs = {r["run_id"]: r for r in scan["runs"]}
    assert scan["session_time_zone"] == "+00:00"
    assert (runs[1]["started_at_utc"], runs[1]["finished_at_utc"]) == ("2026-09-24 13:23:57", "2026-09-24 14:49:02")
    assert (runs[3]["started_at_utc"], runs[3]["finished_at_utc"]) == ("2026-09-25 14:11:25", "2026-09-25 15:35:16")
    assert runs[1]["started_at_vn"] == "2026-09-24 20:23:57" and runs[3]["finished_at_vn"] == "2026-09-25 22:35:16"
    assert any(e["name"] == "n1_scan_identity.json" for e in n1.policy_descriptor()["evidence"])


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s.update(session_time_zone="+07:00"), "session_time_zone"),
    (lambda s: s["runs"][0].update(started_at_utc="2026-09-24T13:23:57Z"), "sai dinh dang"),
    (lambda s: s["runs"][0].update(finished_at_utc="2026-09-24 12:00:00"), "finished_at_utc < started_at_utc"),
    (lambda s: s["runs"][0].update(started_at_vn="2026-09-24 13:23:57"), "started_at_vn khong bang started_at_utc"),
    (lambda s: s["runs"][1].update(finished_at_vn="not a time"), "finished_at_vn sai dinh dang"),
])
def test_scan_times_must_be_real_utc_with_consistent_vn_offset(policy_dir, mutate, message):
    path = policy_dir / n1.POLICY_FILE
    policy = json.loads(path.read_text(encoding="utf-8"))
    mutate(policy["scan_identity"])
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(n1.N1PolicyError, match=message):
        n1.policy_descriptor(path)


def test_changing_a_scan_timestamp_changes_the_descriptor_and_the_config_identity(policy_dir):
    path = policy_dir / n1.POLICY_FILE
    before = n1.policy_descriptor(path)
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["scan_identity"]["runs"][0]["started_at_utc"] = "2026-09-24 13:23:58"
    policy["scan_identity"]["runs"][0]["started_at_vn"] = "2026-09-24 20:23:58"
    path.write_text(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    after = n1.policy_descriptor(path)
    assert after["policy_sha256"] != before["policy_sha256"]
    assert cfg.config_sha256(cfg.build_config(**BASE, n1_policy=after)) != cfg.config_sha256(cfg.build_config(**BASE, n1_policy=before))


def test_utc_session_check_fails_closed_on_any_other_time_zone():
    from analysis.utc_connection import SessionTimezoneError, verify_utc_session

    class _Cur:
        def __init__(self, value):
            self.value = value

        def execute(self, sql):
            assert sql == "SELECT @@session.time_zone"

        def fetchone(self):
            return (self.value,)

        def close(self):
            pass

    class _Conn:
        def __init__(self, value):
            self.value = value

        def cursor(self):
            return _Cur(self.value)

    verify_utc_session(_Conn("+00:00"))
    for bad in ("+07:00", "SYSTEM", "UTC", None):
        with pytest.raises(SessionTimezoneError):
            verify_utc_session(_Conn(bad))


def test_make_policy_refuses_a_scan_identity_that_is_not_utc(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_n1_policy", SCRIPTS / "make_n1_policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "n1_scan_identity.json").write_text(json.dumps({"scan_database": "x", "session_time_zone": "+07:00", "runs": []}), encoding="utf-8")
    with pytest.raises(SystemExit, match="khong ghim thoi gian"):
        module.scan_identity(evidence)
