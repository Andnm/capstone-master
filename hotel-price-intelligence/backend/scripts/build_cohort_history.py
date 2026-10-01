"""Sinh cohort history JSON tu cac commit git cua workbook cohort (CLAUDE.md muc 2: v1=355 -> v2=354).

Moi version = 1 commit git cua `link_hotel_data_expanded.xlsx` + crawl_date VN dau tien version do co hieu
luc. Workbook cua tung commit duoc trich bang `git show` vao data/warehouse/cohort/ (gitignored, tai tao
duoc bang chinh lenh nay); JSON ghi `members_sha256` (hash NOI DUNG) nen build se FAIL neu file bi sua sau.
Version cuoi PHAI trung workbook dang dung o goc repo - neu khong, history da cu (co attrition moi chua ghi).

Ngay hieu luc lay theo TAP HOTEL THUC TE tung run da crawl, KHONG theo ngay commit: run tao luc 00:30 dung
workbook truoc khi sua trong ngay (batch 20260916: commit d85759b ngay 18/08 nhung run 18/08 van crawl
683c2ea; commit 5ad54fb ngay 02/09 nhung run 02/09 cua ca 2 nguon van crawl 355).

Chay (tu backend/):
    python scripts/build_cohort_history.py --version v1.0:2026-08-18:683c2ea \\
        --version v1.1:2026-08-19:d85759b --version v2:2026-09-03:5ad54fb \\
        --reason "v1.0=..." --reason "v1.1=..." --reason "v2=..." \\
        --out ../data/warehouse/cohort_history_20260916.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.warehouse.atomic import atomic_write_json
from app.warehouse.cohort_manifest import COHORT_HISTORY_VERSION, load_cohort_history, load_cohort_manifest
from app.warehouse.errors import WarehouseError

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKBOOK = "link_hotel_data_expanded.xlsx"
SNAPSHOT_DIR = REPO_ROOT / "hotel-price-intelligence" / "data" / "warehouse" / "cohort"


def _git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], check=True, capture_output=True).stdout


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Sinh cohort history JSON tu git.")
    parser.add_argument("--version", action="append", required=True, help="label:YYYY-MM-DD:commit (tang dan)")
    parser.add_argument("--reason", action="append", default=[], help="label=ly do")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        reasons = dict(item.split("=", 1) for item in args.reason)
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        entries = []
        for spec in args.version:
            label, effective, commit = spec.split(":")
            dt.date.fromisoformat(effective)
            full = _git("rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
            snapshot = SNAPSHOT_DIR / f"{label}_{full[:12]}_{WORKBOOK}"
            snapshot.write_bytes(_git("show", f"{full}:{WORKBOOK}"))
            manifest = load_cohort_manifest(snapshot)
            entries.append({"cohort_version": label, "effective_from_crawl_date": effective,
                            "workbook_path": snapshot.relative_to(REPO_ROOT).as_posix(), "git_commit": full,
                            "members_sha256": manifest.manifest_sha256, "size": manifest.size,
                            "reason": reasons.get(label)})
        current = load_cohort_manifest(REPO_ROOT / WORKBOOK)
        if current.manifest_sha256 != entries[-1]["members_sha256"]:
            raise WarehouseError(
                f"workbook dang dung ({WORKBOOK}) KHAC version cuoi {entries[-1]['cohort_version']} - history da cu "
                f"hoac thieu 1 version (attrition moi chua ghi). Them version moi roi chay lai."
            )
        atomic_write_json(args.out, {"cohort_history_version": COHORT_HISTORY_VERSION, "versions": entries})
        history = load_cohort_history(args.out, base_dir=REPO_ROOT)  # doc lai = kiem tra dung duong build
    except (WarehouseError, subprocess.CalledProcessError, ValueError) as exc:
        detail = exc.stderr.decode(errors="replace") if isinstance(exc, subprocess.CalledProcessError) else exc
        print(f"FAIL: {detail}", file=sys.stderr)
        return 1
    for version in history.versions:
        print(f"{version.label}: tu {version.effective_from} | {version.manifest.size} hotel | "
              f"members_sha256={version.manifest.manifest_sha256[:16]} | commit {version.git_commit[:12]}")
    for earlier, later in zip(history.versions, history.versions[1:]):
        before, after = set(earlier.manifest.hotel_city), set(later.manifest.hotel_city)
        print(f"{earlier.label} -> {later.label}: bo {sorted(before - after)}, them {sorted(after - before)}")
    print(f"cohort_manifest_sha256 = {history.manifest_sha256}")
    print(f"da ghi {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
