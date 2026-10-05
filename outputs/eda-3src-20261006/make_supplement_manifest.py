"""Tao manifest versioned cho hai phan tich bo sung cua gate tong the Phan 1 (GPT file 14 M2): SHA-256 script + output, warehouse DB/batch,
manifest nguon, commit code, scope/denominator. CHI DOC file; khong sua artifact Wave A da dong bang.

    python make_supplement_manifest.py            # in ra va ghi GATE_PART1_SUPPLEMENT_MANIFEST.json canh script
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
WAREHOUSE_POINTER = REPO / "outputs" / "warehouse" / "warehouse_current.json"
ARTIFACT = REPO / "hotel-price-intelligence" / "eda" / "outputs" / "eda_b20261004_3src_20261005_172407_b274"
ANALYSES = {
    "status_transitions": ("gate_part1_status_transitions.py", "gate_part1_status_transitions.out"),
    "cross_source_not_bookable": ("gate_part1_cross_source_nb.py", "gate_part1_cross_source_nb.out"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    pointer = json.loads(WAREHOUSE_POINTER.read_text(encoding="utf-8"))
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO, check=True).stdout.strip()
    manifest = {
        "purpose": "Phan tich bo sung chi-doc cho gate tong the Phan 1 (discuss/canonical-key-duplicates/13); KHONG thuoc artifact Wave A.",
        "warehouse_database": pointer["warehouse_database"], "batch_id": pointer["batch_id"],
        "source_manifest_sha256": pointer["source_manifest_sha256"], "ownership_manifest_sha256": pointer["ownership_manifest_sha256"],
        "cohort_manifest_sha256": pointer["cohort_manifest_sha256"], "canonicalization_git_commit": pointer["canonicalization_git_commit"],
        "code_git_head_at_manifest": head,
        "wave_a_artifact": ARTIFACT.name,
        "wave_a_artifact_manifest_sha256": sha256(ARTIFACT / "artifact_manifest.json"),
        "scope": {
            "population": "crawl_run_items thuoc run status='completed', include_eda_main=1, hotel_id IS NOT NULL (373.720 item)",
            "excluded_vs_main_375850": "2.115 item error khong resolve duoc hotel_id + 15 item cua run failed (aux run 13); toan bo 5.494 item not_bookable van nam trong tap",
            "grain": "(source_code, hotel_id, ngay crawl VN) - ngay tu crawl_runs.started_at + 7h",
            "note_circuit_break": "mixed_nb = 0 trong cung hotel-nguon-ngay la he qua thiet ke circuit-break, KHONG phai bang chung property-state; chi so giua nguon moi co thong tin",
        },
        "analyses": {},
    }
    for name, (script, output) in ANALYSES.items():
        manifest["analyses"][name] = {
            "script": script, "script_sha256": sha256(HERE / script), "output": output, "output_sha256": sha256(HERE / output),
        }
    target = HERE / "GATE_PART1_SUPPLEMENT_MANIFEST.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"manifest sha256: {sha256(target)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
