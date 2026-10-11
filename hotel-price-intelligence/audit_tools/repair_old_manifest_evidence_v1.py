"""Bang chung THUAN (doc-chi, khong DB): tai tao `builder_code_sha256` CU da ghim cua dev1b/dev3b tu cay lam viec hien tai + hai file sua (lay ban cu tu `git show HEAD:`), va ghi manifest hien tai.

    python repair_old_manifest_evidence_v1.py --pinned 562d22af... --out <json>

Ket qua `reproduced=true` nghia: 38 file con lai trong manifest KHONG doi ke tu luc dung dataset cu va ban cu cua hai file sua la blob HEAD voi kieu xuong dong da neu; tap file doi so voi manifest ghim = DUNG hai file do."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("repair_manifest_v1", HERE / "repair_manifest_v1.py")
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)
CHANGED = ("hotel-price-intelligence/ml/dataset_builder/dictionary.py", "hotel-price-intelligence/ml/dataset_builder/features.py")


def git_show(rel: str, rev: str = "HEAD") -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:{rel}"], capture_output=True, check=True).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pinned", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    current = rm.compute_manifest(ROOT)
    candidates = {rel: git_show(rel) for rel in CHANGED}
    result = rm.reproduce_pinned(current, args.pinned, candidates)
    current_files = {rel: {"sha256": current["files"][rel], "bytes_crlf": (ROOT / rel).read_bytes().count(b"\r\n"), "bytes_lf_only": (ROOT / rel).read_bytes().replace(b"\r\n", b"").count(b"\n")} for rel in CHANGED}
    report = {"pinned_code_sha256": args.pinned, "current_code_sha256": current["code_sha256"], "files_in_manifest": len(current["files"]), "reproduction": result, "current_changed_files": current_files,
              "head_blob_sha256_lf": {rel: hashlib.sha256(rm.eol_variants(blob)["LF"]).hexdigest() for rel, blob in candidates.items()},
              "interpretation": "reproduced=true & matches=[{both: LF}] => cac file khac khong doi; ban cu hai file sua = blob HEAD o dang LF; cay hien tai co hai file do khac manifest ghim."}
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps({"reproduced": result["reproduced"], "matches": result["matches"], "current_code_sha256": current["code_sha256"]}))
    return 0 if result["reproduced"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
