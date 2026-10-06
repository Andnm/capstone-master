"""Sinh `ml/policies/n1/` (policy loai hotel bi anh huong boi bug phu dinh bua sang + bang chung bat bien) tu thu muc bang chung N1.

    python ml/scripts/make_n1_policy.py --evidence-dir outputs/s2-patch-20261006 [--out ml/policies/n1] [--version n1-policy-1.0.0]

Copy NGUYEN byte cac file bang chung vao thu muc policy, tinh SHA-256, doc danh sach hotel tu `n1_hotels_from_scan.json`, ghi `n1_policy_v1.json`. Dataset builder chi doc
thu muc nay (khong doc `outputs/`), ghim hash vao `build_config` va copy bytes vao artifact. Doi bat ky byte nao => dataset_version moi. Chay lai voi cung input cho cung ket qua.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset_builder.n1_policy import POLICY_DIR, POLICY_FILE, POLICY_SCHEMA_VERSION  # noqa: E402

EVIDENCE = (
    ("n1_hotels_from_scan.json", "hotel co it nhat mot option bi parser cu doi breakfast_included boi cau phu dinh, gom theo hotel tu artifact quet 24-25/09 (check-in 2026-10-11)"),
    ("n1_affected_items.json", "item/option bi anh huong theo tung trang artifact (so option, so dong phu dinh, so dong doi)"),
    ("n1_matcher_replay.json", "replay MATCHER THAT (select_best_match) tren toan bo offer cua tung item bi anh huong truoc/sau patch, voi assignment causal cua dataset rehearsal; "
                                "tach association (rate key trung) khoi matcher evidence; item khong can chinh duoc HTML/khong co assignment la unmapped/no_assignment (khong gan unaffected)"),
    ("parity_on_artifacts.out", "parity parser goc vs parser da va tren moi dong HTML con lai: chi breakfast_included doi, chi o dong co cau phu dinh"),
)
SCAN_IDENTITY = {
    "scan_database": "hotel_price_intel_fullscan_20260924",
    "scope": "MOT ngay check-in (2026-10-11), toan cohort 354 item/run; exposure o ngay/rate khac KHONG biet",
    "runs": [
        {"run_id": 1, "started_at_utc": "2026-09-24 20:23:57", "finished_at_utc": "2026-09-24 21:49:02", "scraper_version": "2.3.0", "selector_version": "booking-2026-08-17",
         "git_commit": "3eb1988ab26f83fc1261ce40fddbbf6ac87e3fd5"},
        {"run_id": 3, "started_at_utc": "2026-09-25 21:11:25", "finished_at_utc": "2026-09-25 22:35:16", "scraper_version": "2.3.0", "selector_version": "booking-2026-08-17",
         "git_commit": "0799d348e2df15b65f70c95b4edc5c54a5294d66"}],
    "parser_original": "backend/app/scraper/parser.py tai commit 503db7d (parse_room_conditions coi 'Khong bao gom bua sang' la breakfast_included=True)",
    "pages_total": 789, "option_rows_total": 8694, "rows_changed_by_patch": 59, "pages_with_change": 10,
}
REASON = ("v1.1 loai cac hotel co exposure da biet (cat duoi: chi tu mot snapshot quet) qua MOI regime; hotel ngoai danh sach co exposure CHUA BIET, khong duoc coi la khong bi anh huong; "
          "khong bridge raw bool cu, khong tai approve reference da dong bang.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=POLICY_DIR)
    parser.add_argument("--version", default="n1-policy-1.1.0")
    args = parser.parse_args()
    hotels = sorted({row["hotel_id"] for row in json.loads((args.evidence_dir / "n1_hotels_from_scan.json").read_text(encoding="utf-8"))})
    args.out.mkdir(parents=True, exist_ok=True)
    evidence = []
    for name, scope in EVIDENCE:
        raw = (args.evidence_dir / name).read_bytes()
        shutil.copyfile(args.evidence_dir / name, args.out / name)
        evidence.append({"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "scope": scope})
    policy = {"schema_version": POLICY_SCHEMA_VERSION, "policy_version": args.version, "reason": REASON, "excluded_hotels": hotels, "scan_identity": SCAN_IDENTITY, "evidence": evidence}
    (args.out / POLICY_FILE).write_text(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"policy {args.version}: {len(hotels)} hotel, {len(evidence)} evidence -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
