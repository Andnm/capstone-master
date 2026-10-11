"""Chay cong tong `repair_parity_v1.validate_repair` (THUAN, doc-chi; khong DB) cho hai cap old->new: dev1b->nf1 (h1) va dev3b->nf3 (h3). Duong dan Windows tuong minh (tranh loi shell). Ghi `xb` vao run-root.
Claude, uy quyen nguoi dung; GPT chua duyet. Anchor SHA lay tu hang so da ghim (plan_v1/producer/da duyet), KHONG lay tu artifact moi."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

AT = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\audit_tools")
spec = importlib.util.spec_from_file_location("rpar", AT / "repair_parity_v1.py")
rp = importlib.util.module_from_spec(spec)
sys.modules["rpar"] = rp
spec.loader.exec_module(rp)

OUT = Path(r"D:\MSE\CAPSTONE\outputs")
WA = OUT / "model-diagnosis-20261008" / "warehouse_audit"
RUN = OUT / "repair-run-ledger-r124"
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


def main(keys: list[str]) -> int:
    worst = 0
    for key in keys:
        c = CASES[key]
        result = rp.validate_repair(
            old_dir=OUT / "datasets" / c["old"], new_dir=OUT / "datasets" / c["new"],
            old_ledger_path=WA / f"ledger_{c['old']}_{c['old_run']}.parquet", new_ledger_path=WA / f"ledger_{c['new']}_{c['new_run']}.parquet",
            old_version=c["old"], new_version=c["new"], expected_code_sha256=NEW_CODE, expected_config_sha256=c["new_cfg"], expected_old_config_sha256=c["old_cfg"],
            expected_old_code_sha256=OLD_CODE, expected_old_samples_sha256=c["old_samples"], expected_producer_script_sha256=PRODUCER, expected_producer_sql_set_sha256=SQLSET,
            expected_base_controller_sha256=CONTROLLER,
            provenance_paths={"old_ledger_report": WA / c["old_report"], "new_ledger_report": WA / c["new_report"], "config_prediction": PRED / c["pred"], "old_config_snapshot": WA / c["old_snapshot"]})
        out = RUN / f"parity_{key}.json"
        with open(out, "xb") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
        print(json.dumps({"case": key, "verdict": result["verdict"], "violations": result["violations"][:12], "receipt_sha256": result["receipt_sha256"], "file": str(out)}, ensure_ascii=False))
        comp = result.get("components", {}).get("samples", {})
        print(json.dumps({"samples": {k: comp.get(k) for k in ("verdict", "changed_rows_total", "flags", "ledger_old", "ledger_new")}}, ensure_ascii=False, default=str)[:1500])
        if result["verdict"] != "PASS_REPAIR_PARITY":
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["h1", "h3"]))
