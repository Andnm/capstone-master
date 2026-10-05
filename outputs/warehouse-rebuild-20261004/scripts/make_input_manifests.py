"""Sinh/kiểm input manifest cho rebuild warehouse (dump 04/10/2026) theo HAI PROFILE: `aux` (aux-only rehearsal) và `full` (3 nguồn).
Chỉ ĐỌC dump/workbook/manifest nguồn; ghi file mới trong `hotel-price-intelligence/data/warehouse/` (gitignored) +
`outputs/warehouse-rebuild-20261004/input_provenance.json` (tái ghi mỗi lần chạy: đây là bằng chứng freeze, không phải identity bất biến).

Chạy (từ backend/): venv/Scripts/python.exe ../../outputs/warehouse-rebuild-20261004/scripts/make_input_manifests.py [--write]
Mặc định DRY-RUN. Tính lại `schema_sha256` từ CHÍNH dump bằng `compute_schema_sha256_from_dump` (manifest aux ghi null) và đối chiếu manifest nguồn nếu có.
File đã tồn tại KHÔNG bị ghi đè: chỉ chấp nhận khi hash định danh (source identity / ownership content hash) trùng hệt bản vừa sinh.
`input_provenance.json.profiles.{aux,full}` ghim identity của ĐÚNG source/ownership/cohort manifest (và cutoff ở `full`) — preflight verify theo profile
bằng chính đường dẫn sẽ truyền cho `build_warehouse` (GPT 13 MAJOR 3: không "preflight file A, build file B").
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "hotel-price-intelligence" / "backend"
sys.path.insert(0, str(BACKEND))

from app.warehouse.cohort_manifest import load_cohort  # noqa: E402
from app.warehouse.hashing import file_sha256, iso_utc  # noqa: E402
from app.warehouse.ownership_manifest import (  # noqa: E402
    build_ownership_rows, compute_ownership_manifest_sha256, load_ownership_manifest, write_ownership_manifest,
)
from app.warehouse.source_manifest import compute_schema_sha256_from_dump, load_source_manifest  # noqa: E402

DATA = REPO / "hotel-price-intelligence" / "data"
WH = DATA / "warehouse"
BASE = REPO / "outputs" / "warehouse-rebuild-20261004"
EXPORT = DATA / "20261004"
SOURCES = (
    ("local_primary", 0, EXPORT / "local_crawl", "local_primary_20261004_141119"),
    ("vps", 1, EXPORT / "vps_crawl", "vps_20261004_141548"),
    ("local_aux", 2, EXPORT / "aux_crawl", "local_aux_20261004_142222"),
)
WORKBOOKS = {
    "local_primary": REPO / "outputs" / "crawl-data-planner-20260816" / "crawl_sampling_master.xlsx",
    "vps": REPO / "outputs" / "vps-crawl-planner-20260823" / "vps_crawl_sampling_master.xlsx",
    "local_aux": REPO / "outputs" / "aux-local-crawl-planner-20260901" / "aux_local_crawl_sampling_master.xlsx",
    "cohort_root": REPO / "link_hotel_data_expanded.xlsx",
}
COHORT = WH / "cohort_history_20260916.json"
CUTOFF = BASE / "cutoff_20261004.json"
OLD_MANIFEST = WH / "source_manifest_20260916.json"
PROFILES = {
    "full": {"label": "warehouse_20261004_3src", "sources": ("local_primary", "vps", "local_aux"), "source_manifest": WH / "source_manifest_20261004.json",
             "ownership_manifest": WH / "ownership_manifest_20261004.json", "cutoff": CUTOFF},
    "aux": {"label": "warehouse_rh20261004_aux", "sources": ("local_aux",), "source_manifest": WH / "source_manifest_20261004_auxonly.json",
            "ownership_manifest": WH / "ownership_manifest_20261004_auxonly.json", "cutoff": None},
}


def rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def ensure_source_manifest(target: Path, manifest: dict, *, write: bool) -> str:
    """Trả identity hash; file có sẵn chỉ được chấp nhận khi trùng identity."""
    candidate = target.with_suffix(".candidate.json")
    candidate.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        fresh = load_source_manifest(candidate, base_dir=REPO).manifest_sha256
        if target.exists():
            if load_source_manifest(target, base_dir=REPO).manifest_sha256 != fresh:
                raise SystemExit(f"FAIL: {target.name} đã tồn tại và KHÁC nội dung vừa sinh - không ghi đè manifest đã pin.")
            print(f"source manifest đã tồn tại và trùng identity: {target.name} ({fresh[:12]}…)")
        elif write:
            candidate.replace(target)
            print(f"Đã ghi {target.name} ({fresh[:12]}…)")
        else:
            print(f"[dry-run] sẽ ghi {target.name} ({fresh[:12]}…)")
    finally:
        candidate.unlink(missing_ok=True)
    return fresh


def ensure_ownership(target: Path, workbooks: dict[str, str], *, write: bool) -> tuple[str, int]:
    rows = build_ownership_rows(workbooks)
    expected = compute_ownership_manifest_sha256(rows)
    if target.exists():
        loaded = load_ownership_manifest(target)
        if loaded.manifest_sha256 != expected:
            raise SystemExit(f"FAIL: {target.name} có sẵn {loaded.manifest_sha256} != vừa tính {expected}")
        print(f"ownership manifest đã tồn tại và khớp: {target.name} {expected[:12]}… ({len(rows)} dòng)")
    elif write:
        write_ownership_manifest(rows, target)
        print(f"Đã ghi {target.name} ({len(rows)} dòng, {expected[:12]}…)")
    else:
        print(f"[dry-run] sẽ ghi {target.name} ({len(rows)} dòng, {expected[:12]}…)")
    return expected, len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="ghi file (mặc định dry-run)")
    args = parser.parse_args()
    entries: dict[str, dict] = {}
    provenance: dict = {"sources": {}, "workbooks": {}, "profiles": {}}
    old = json.loads(OLD_MANIFEST.read_text(encoding="utf-8"))
    for code, priority, folder, stem in SOURCES:
        dump = folder / f"{stem}.sql"
        info = json.loads((folder / f"{stem}.manifest.json").read_text(encoding="utf-8"))
        sha = file_sha256(dump)
        schema_sha, _extra = compute_schema_sha256_from_dump(dump)
        recorded_schema = info.get("schema_sha256")
        print(f"{code:<14} dump_sha256={sha} (manifest nguồn {'KHỚP' if sha == info['dump_sha256'] else 'LỆCH'}); schema_sha256={schema_sha} (manifest nguồn: {recorded_schema!r})")
        if sha != info["dump_sha256"]:
            raise SystemExit(f"FAIL: dump {code} lệch checksum manifest nguồn")
        if recorded_schema is not None and recorded_schema != schema_sha:
            raise SystemExit(f"FAIL: schema {code} lệch manifest nguồn")
        entries[code] = {
            "source_code": code, "source_priority": priority, "dump_path": rel(dump), "dump_sha256": sha,
            "dump_taken_at": iso_utc(info["dump_taken_at"]),          # cùng quy tắc chuẩn hoá với loader/hash
            "schema_sha256": schema_sha, "source_version_json": info["source_version_json"],
        }
        provenance["sources"][code] = {
            "dump": rel(dump), "dump_sha256": sha, "schema_sha256": schema_sha,
            "source_manifest_file_sha256": file_sha256(folder / f"{stem}.manifest.json"),
            "row_counts_at_dump_time": info["row_counts_at_dump_time"], "export_status": info.get("export_status"),
            "schema_status": info.get("schema_status"), "schema_extract_sha256": file_sha256(folder / f"{stem}.schema.sql"),
        }
    for name, path in WORKBOOKS.items():
        provenance["workbooks"][name] = {"path": rel(path), "sha256": file_sha256(path)}
    provenance["workbooks_note"] = ("sha256 CẢ FILE workbook chỉ là generation-time evidence (automation ghi CRAWL_LOG hằng ngày làm đổi hash); "
                                   "identity thật của lịch = ownership_manifest content hash.")
    provenance["generated_at_utc"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    provenance["export_report"] = {"path": rel(EXPORT / "EXPORT_REPORT.json"), "sha256": file_sha256(EXPORT / "EXPORT_REPORT.json")}
    cohort = load_cohort(COHORT, base_dir=REPO)
    for profile, spec in PROFILES.items():
        manifest = {
            "manifest_version": 1, "batch_label": spec["label"],
            "note": (("AUX-ONLY compatibility rehearsal (diagnostic, không thay thế full 3-source rebuild) - " if profile == "aux" else "")
                     + "EXTERNAL IMMUTABLE source manifest - input cố định của batch (dump 2026-10-04). schema_sha256 tính lại từ chính dump bằng "
                       "compute_schema_sha256_from_dump; local_aux có raw schema thiếu crawl_run_items.dead_link_confirmation và được xử lý bằng adapter "
                       "staging hẹp đã đăng ký theo (source_code, schema_sha256) - xem backend/app/warehouse/schema_adapters.py. Không append vào warehouse "
                       "đã PASS/promote; mỗi lần đổi tập nguồn = warehouse/batch mới."),
            "source_priority_rationale": old["source_priority_rationale"].replace("vps=1.", "vps=1; local_aux=2 (nguồn thứ ba, máy phụ)."),
            "sources": [entries[code] for code in spec["sources"]],
        }
        source_identity = ensure_source_manifest(spec["source_manifest"], manifest, write=args.write)
        own_workbooks = {code: str(WORKBOOKS[code]) for code in spec["sources"]}
        ownership_sha, rows = ensure_ownership(spec["ownership_manifest"], own_workbooks, write=args.write)
        entry = {
            "sources": list(spec["sources"]),
            "source_manifest": {"path": rel(spec["source_manifest"]), "identity_sha256": source_identity},
            "ownership_manifest": {"path": rel(spec["ownership_manifest"]), "sha256": ownership_sha, "rows": rows},
            "cohort_manifest": {"path": rel(COHORT), "sha256": cohort.manifest_sha256},
        }
        if spec["cutoff"] is not None:
            entry["cutoff_file"] = {"path": rel(spec["cutoff"]), "sha256": file_sha256(spec["cutoff"])}
        provenance["profiles"][profile] = entry
    if args.write:
        out = BASE / "input_provenance.json"
        out.write_text(json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Đã (tái) ghi {out}")
    else:
        print("[dry-run] không ghi input_provenance.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
