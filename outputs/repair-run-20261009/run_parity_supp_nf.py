"""Chay policy bo sung `label-timing-exception-v1` (audit_tools/repair_parity_supp_v1.py) tren hai cap that old->new (h1, h3) va cac control NHOM R (tham so neo sai, KHONG mutate artifact).
THUAN, doc-chi, khong DB. Ghi `xb` vao root MOI `outputs/repair-run-parity-supp-r125/`. Claude (implementer); contract: GPT file 146 §5a, xac nhan truoc o file 147.
    python run_parity_supp_nf.py real          # supp_h1.json, supp_h3.json
    python run_parity_supp_nf.py controls      # controls_real_h1.json (4 luot validate_repair that voi tham so sai)"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

AT = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools")
spec = importlib.util.spec_from_file_location("rsupp", AT / "repair_parity_supp_v1.py")
sp = importlib.util.module_from_spec(spec)
sys.modules["rsupp"] = sp
spec.loader.exec_module(sp)
V1 = sp.load_v1()                                                                                                   # tu choi neu SHA != 1022c5ee…

OUT = Path(r"D:\MSE\CAPSTONE\outputs")
WA = OUT / "model-diagnosis-20261008" / "warehouse_audit"
RUN = OUT / "repair-run-parity-supp-r125"
PRED = OUT / "repair-config-predictions-20261009-ph1"
NEW_CODE = "c576079a7ca0310a8f5bb0074f29a58a3e66c1765cf4197306229bfa777edbfe"
OLD_CODE = "562d22af69cb66b9d2c5bbbadbff1d325bc447cc9ffdb5a5ddf102ebf75f278d"
PRODUCER, SQLSET, CONTROLLER = ("a6968c0ff7a15bed7e91833933359bf2b2fa05f6cf9201539068add9b44ab67c", "b2a008b99778caaaadc841a801c843d8551cb7cf8a0b52866e6774c5a4a041ee",
                                "491d3f4a3e220da15ed2b57eac07762351cd98c2cf95ab2058f492676cfcbbc0")
CASES = {
    "h1": dict(old="ds_20261006_dev1b", new="ds_20261010_nf1", old_run="468216267760", old_report="flag_ledger_run_20261009_181140_468216267760.json",
               old_snapshot="manifest_snapshot_20261009_181037_468216267760.json", new_run="cec0162acc5b", new_report="flag_ledger_run_20261010_204041_cec0162acc5b.json",
               new_cfg="39d9eca0633fe84e1c422279bd4b4ba8e95da9f3776a8287e06c1c2e39597a91", old_cfg="6494c1d4ece098e12855644f26780eecfe6706be32baee8a8f486b8b10069928",
               old_samples="1a4a099ac6d11fcced901e662987f78e7f03e79c60911eb092b19213e693dce1", pred="prediction_receipt_dev1b.json"),
    "h3": dict(old="ds_20261006_dev3b", new="ds_20261010_nf3", old_run="9abddda1cd12", old_report="flag_ledger_run_20261009_181243_9abddda1cd12.json",
               old_snapshot="manifest_snapshot_20261009_181141_9abddda1cd12.json", new_run="5facc54aacf9", new_report="flag_ledger_run_20261010_204143_5facc54aacf9.json",
               new_cfg="27a922dc264aa1099e13f3facd54a20ffcc7b4c727209db856971d74a3940b27", old_cfg="45f2fc6ab177505de3b5a1590f6cab2988bb9e05ac72a1996127c2760289cbd8",
               old_samples="c0a3aef94e9038578b8dacc802c2d04fa59f44c3629d1c23456571f9cc7d1d03", pred="prediction_receipt_dev3b.json"),
}


def kwargs_for(key: str, **override) -> dict:
    c = CASES[key]
    kw = dict(old_dir=OUT / "datasets" / c["old"], new_dir=OUT / "datasets" / c["new"], old_ledger_path=WA / f"ledger_{c['old']}_{c['old_run']}.parquet",
              new_ledger_path=WA / f"ledger_{c['new']}_{c['new_run']}.parquet", old_version=c["old"], new_version=c["new"], expected_code_sha256=NEW_CODE, expected_config_sha256=c["new_cfg"],
              expected_old_config_sha256=c["old_cfg"], expected_old_code_sha256=OLD_CODE, expected_old_samples_sha256=c["old_samples"], expected_producer_script_sha256=PRODUCER,
              expected_producer_sql_set_sha256=SQLSET, expected_base_controller_sha256=CONTROLLER,
              provenance_paths={"old_ledger_report": WA / c["old_report"], "new_ledger_report": WA / c["new_report"], "config_prediction": PRED / c["pred"], "old_config_snapshot": WA / c["old_snapshot"]})
    kw.update(override)
    return kw


def flip(hexstr: str) -> str:
    return hexstr[:-1] + ("0" if hexstr[-1] != "0" else "1")


def write_new(name: str, payload) -> Path:
    RUN.mkdir(parents=True, exist_ok=True)
    path = RUN / name
    with open(path, "xb") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
    return path


def real() -> int:
    worst = 0
    for key in ("h1", "h3"):
        receipt = sp.run_supplemental(v1=V1, **kwargs_for(key))
        path = write_new(f"supp_{key}.json", receipt)
        pol = receipt["policy"]
        print(json.dumps({"case": key, "original_v1_verdict": receipt["original_v1_verdict"], "supplemental_verdict": receipt["supplemental_verdict"], "refusals": pol["refusals"],
                          "allowlisted": pol["allowlisted"], "matched": pol["matched_violations"], "others": pol["other_violations"], "original_receipt_sha256": receipt["original_receipt_sha256"],
                          "supplemental_receipt_sha256": receipt["supplemental_receipt_sha256"], "file": str(path)}, ensure_ascii=False, default=str))
        if receipt["supplemental_verdict"] != sp.PASS_WITH_EXCEPTION:
            worst = 1
    return worst


def controls() -> int:
    c = CASES["h1"]
    cases = [
        ("R1_wrong_expected_config_sha", dict(expected_config_sha256=flip(c["new_cfg"]))),
        ("R2_wrong_expected_old_samples_sha", dict(expected_old_samples_sha256=flip(c["old_samples"]))),
        ("R3_wrong_expected_producer_script_sha", dict(expected_producer_script_sha256=flip(PRODUCER))),
        ("R4_new_ledger_is_other_dataset_nf3", dict(new_ledger_path=WA / f"ledger_{CASES['h3']['new']}_{CASES['h3']['new_run']}.parquet")),
    ]
    results, bad = [], 0
    for name, override in cases:
        receipt = sp.run_supplemental(v1=V1, **kwargs_for("h1", **override))
        pol = receipt["policy"]
        ok = receipt["supplemental_verdict"] == sp.FAIL and len(pol["other_violations"]) >= 1 and len(pol["matched_violations"]) == 1
        bad += not ok
        results.append({"control": name, "expected": "FAIL because of non-timing violations while the real timing violation is still present", "supplemental_verdict": receipt["supplemental_verdict"],
                        "matched_timing_violations": len(pol["matched_violations"]), "other_violations_count": len(pol["other_violations"]), "other_violations_head": pol["other_violations"][:4],
                        "refusals": pol["refusals"], "control_ok": ok, "mutated": {k: str(v)[-60:] for k, v in override.items()}})
        print(json.dumps(results[-1], ensure_ascii=False, default=str)[:900])
    path = write_new("controls_real_h1.json", {"policy_id": sp.POLICY_ID, "group": "R (real-input; parameters mutated, artifacts untouched)", "results": results, "all_controls_ok": bad == 0})
    print(json.dumps({"controls_file": str(path), "all_controls_ok": bad == 0}))
    return 0 if bad == 0 else 1


def controls_extra() -> int:
    """BO SUNG SAU KHI THAY R4 (R4 FAIL dung nhung cong v1 bo buoc so cay artifact vi `content_sha256_not_measured`, nen khong con vi pham timing de thu 'timing + delta khac'). Khong doi policy/R1-R4."""
    c = CASES["h1"]
    cases = [
        ("R5_old_ledger_is_other_dataset_dev3b", dict(old_ledger_path=WA / f"ledger_{CASES['h3']['old']}_{CASES['h3']['old_run']}.parquet")),
        ("R6_wrong_expected_code_sha", dict(expected_code_sha256=flip(NEW_CODE))),
    ]
    results, bad = [], 0
    for name, override in cases:
        receipt = sp.run_supplemental(v1=V1, **kwargs_for("h1", **override))
        pol = receipt["policy"]
        ok = receipt["supplemental_verdict"] == sp.FAIL and len(pol["other_violations"]) >= 1
        timing_present = len(pol["matched_violations"]) == 1
        bad += not ok
        results.append({"control": name, "registered": "post-hoc supplement after seeing R4 (not in 147)", "supplemental_verdict": receipt["supplemental_verdict"], "timing_violation_still_present": timing_present,
                        "other_violations_count": len(pol["other_violations"]), "other_violations_head": pol["other_violations"][:4], "refusals": pol["refusals"], "control_ok": ok,
                        "mutated": {k: str(v)[-60:] for k, v in override.items()}})
        print(json.dumps(results[-1], ensure_ascii=False, default=str)[:900])
    path = write_new("controls_real_h1_extra.json", {"policy_id": sp.POLICY_ID, "group": "R-extra (post-hoc supplement)", "results": results, "all_controls_ok": bad == 0})
    print(json.dumps({"controls_file": str(path), "all_controls_ok": bad == 0}))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "real"
    sys.exit({"real": real, "controls": controls, "controls_extra": controls_extra}[mode]())
