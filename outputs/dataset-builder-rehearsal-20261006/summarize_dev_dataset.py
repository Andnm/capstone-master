"""Tom tat mot dataset dev theo horizon K (doc-chi artifact): contract, split, sufficiency, so mau/nhan usable theo split, so hotel primary. In JSON 1 dong + bang."""
import json
import sys
from pathlib import Path

import pandas as pd

root = Path(r"D:\MSE\CAPSTONE\outputs\datasets")
for version in sys.argv[1:]:
    d = root / version
    contract = json.loads((d / "dataset_contract.json").read_text(encoding="utf-8"))
    suff = json.loads((d / "sufficiency_report.json").read_text(encoding="utf-8"))
    validation = json.loads((d / "reports" / "validation.json").read_text(encoding="utf-8"))
    k = contract["evaluation_horizons"][0]
    frame = pd.read_parquet(d / "samples.parquet", columns=["split", f"label_usable_h{k}", f"hotel_seen_in_train_h{k}", "hotel_id"])
    frame["s"] = frame["split"].fillna("purge")
    usable = frame[frame[f"label_usable_h{k}"]]
    primary = usable[(usable["s"] == "train") | usable[f"hotel_seen_in_train_h{k}"]]
    summary = {"dataset": version, "purpose": contract["purpose"], "horizon": k, "purge": contract["purge_gap_days"], "validation_ok": validation["ok"],
               "split_plan": {key: contract["split_plan"][key] for key in ("train_start", "train_end", "validation_start", "validation_end", "test_start", "test_end", "policy_path")},
               "sufficiency": {name: (e["status"], e.get("failed_gates")) for name, e in suff["horizons"].items() if e["status"] != "not_evaluated"},
               "rows": int(len(frame)), "usable_by_split": {s: int(n) for s, n in usable["s"].value_counts().items()},
               "primary_usable_by_split": {s: int(n) for s, n in primary["s"].value_counts().items()}, "hotels_in_primary_test": int(primary.loc[primary["s"] == "test", "hotel_id"].nunique()),
               "n1_policy": (contract.get("n1_policy") or {}).get("policy_version")}
    print(json.dumps(summary, ensure_ascii=False))
