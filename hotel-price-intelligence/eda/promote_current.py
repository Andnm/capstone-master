"""Promote mot artifact Wave A da duoc GPT `PASS WAVE A` thanh pointer hien hanh `eda/outputs/eda_current.json` (quy trinh file 20 cua thread
`eda-curated-implementation`, nay la script co test). FAIL-CLOSED: moi dieu kien kiem tra phai dung, sai mot la dung va liet ke tat ca loi.

    python eda/promote_current.py --analysis-dir eda/outputs/<analysis_id> --review-file discuss/<thread>/<file>.md \\
        [--gate "PASS WAVE A"] [--reviewed-by GPT] [--review-date YYYY-MM-DD] [--apply]

Mac dinh DRY-RUN (chi kiem tra, khong ghi). `--apply`: luu pointer cu vao `eda/outputs/eda_pointer_history/`, ghi pointer moi NGUYEN TU
(`artifacts.atomic_write_json`), doc lai tu dia, resolve va verify lai 100% manifest. Khong bao gio sua file trong artifact.
Plan: neu hash `EDA_CURATED_PLAN.md` KHONG doi so voi luc chay thi tot nhat; neu da doi (vd cap nhat dong trang thai truoc khi promote) thi chi duoc chap nhan khi MOI bullet cua
`EDA_COVERAGE_MATRIX.csv` van con nguyen van trong plan hien tai - pointer ghi `plan_changed_since_run=true` kem ca hai hash. Dieu kiem nay KHONG chung minh yeu cau
MOI them vao plan sau khi chay da duoc artifact dap ung: reviewer van phai xem semantic diff cua plan (GPT review vong 1, muc EDA).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path
from typing import Any

EDA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EDA_DIR / "src"))

import artifacts  # noqa: E402

REPO_ROOT = artifacts.REPO_ROOT
OUTPUTS_DIR = artifacts.OUTPUTS_DIR
POINTER_PATH = OUTPUTS_DIR / "eda_current.json"
HISTORY_DIR = OUTPUTS_DIR / "eda_pointer_history"
WAREHOUSE_POINTER_PATH = REPO_ROOT / "outputs" / "warehouse" / "warehouse_current.json"
PLAN_PATH = REPO_ROOT / "hotel-price-intelligence" / "EDA_CURATED_PLAN.md"
WAREHOUSE_KEYS = ("batch_id", "warehouse_database", "source_manifest_sha256", "cohort_manifest_sha256", "ownership_manifest_sha256",
                  "canonicalization_version")


class PromoteError(RuntimeError):
    pass


def verify_artifact(analysis_dir: Path) -> dict[str, Any]:
    """Kiem tra toan ven artifact (chi doc); tra ve {manifest, input_manifest, errors}. Khong raise - nguoi goi quyet dinh."""
    errors: list[str] = []
    analysis_dir = Path(analysis_dir)
    manifest = input_manifest = None
    if not analysis_dir.is_dir():
        return {"errors": [f"khong co thu muc {analysis_dir}"], "manifest": None, "input_manifest": None}
    if (analysis_dir / "FAILED.json").exists():
        errors.append("co FAILED.json")
    manifest_path = analysis_dir / "artifact_manifest.json"
    if not manifest_path.exists():
        errors.append("thieu artifact_manifest.json")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = manifest["files"]
        listed = {entry["path"] if isinstance(entry, dict) else entry for entry in (entries if isinstance(entries, list) else entries.keys())}
        if manifest.get("file_count") != len(listed):
            errors.append(f"file_count {manifest.get('file_count')} != so entry {len(listed)}")
        on_disk = {p.relative_to(analysis_dir).as_posix() for p in analysis_dir.rglob("*") if p.is_file()} - {"artifact_manifest.json"}
        if on_disk - listed:
            errors.append(f"file thua ngoai manifest: {sorted(on_disk - listed)[:5]}")
        if listed - on_disk:
            errors.append(f"file thieu so voi manifest: {sorted(listed - on_disk)[:5]}")
        records = entries if isinstance(entries, list) else [{"path": k, **v} for k, v in entries.items()]
        for entry in records:
            path = analysis_dir / entry["path"]
            if not path.exists():
                continue
            if "sha256" in entry and artifacts.sha256_file(path) != entry["sha256"]:
                errors.append(f"SHA-256 lech: {entry['path']}")
            if "size_bytes" not in entry or "sha256" not in entry:
                errors.append(f"entry manifest thieu sha256/size_bytes: {entry['path']}")
            elif path.stat().st_size != entry["size_bytes"]:
                errors.append(f"kich thuoc lech: {entry['path']}")
    input_path = analysis_dir / "input_manifest.json"
    if not input_path.exists():
        errors.append("thieu input_manifest.json")
    else:
        input_manifest = json.loads(input_path.read_text(encoding="utf-8"))
    return {"errors": errors, "manifest": manifest, "input_manifest": input_manifest}


def _bullets_missing_from_plan(analysis_dir: Path, plan_path: Path) -> list[str] | None:
    """Bullet trong `EDA_COVERAGE_MATRIX.csv` cua artifact khong con nguyen van (chuan hoa khoang trang) trong plan hien tai. None = khong doc duoc."""
    import csv

    matrix = Path(analysis_dir) / "EDA_COVERAGE_MATRIX.csv"
    if not matrix.exists():
        return None
    squash = lambda s: " ".join(str(s).split())  # noqa: E731
    plan_text = squash(Path(plan_path).read_text(encoding="utf-8"))
    with open(matrix, encoding="utf-8", newline="") as handle:
        bullets = [row["plan_bullet"] for row in csv.DictReader(handle) if row.get("plan_bullet")]
    if not bullets:
        return None
    # Dong 'para: <van ban>' la quy uoc cua coverage matrix cho doan van (khong phai bullet); van ban that nam sau tien to.
    texts = [b[len("para:"):].strip() if b.startswith("para:") else b for b in bullets]
    # Matrix co the cat bullet o cuoi cau (them dau cham) trong khi plan viet tiep ", vi du ..." => bo dau cau cuoi truoc khi tim.
    return [t for t in texts if squash(t).rstrip(".:;") not in plan_text]


def build_pointer(analysis_dir: Path, review_file: Path, *, gate: str, reviewed_by: str, review_date: str,
                  warehouse_pointer_path: Path = WAREHOUSE_POINTER_PATH, plan_path: Path = PLAN_PATH,
                  outputs_dir: Path = OUTPUTS_DIR, repo_root: Path = REPO_ROOT, now: dt.datetime | None = None) -> dict[str, Any]:
    analysis_dir, review_file = Path(analysis_dir).resolve(), Path(review_file).resolve()
    verified = verify_artifact(analysis_dir)
    errors: list[str] = list(verified["errors"])
    plan_state: dict[str, Any] = {}
    im = verified["input_manifest"] or {}
    provenance = im.get("code_provenance") or {}
    if im:
        if provenance.get("is_dirty") is not False or provenance.get("dirty_guarded_files"):
            errors.append(f"provenance khong sach: is_dirty={provenance.get('is_dirty')} dirty_guarded_files={provenance.get('dirty_guarded_files')}")
        if not provenance.get("git_head"):
            errors.append("thieu git_head trong provenance")
        if not Path(warehouse_pointer_path).exists():
            errors.append(f"khong co warehouse pointer {warehouse_pointer_path}")
        else:
            wh = json.loads(Path(warehouse_pointer_path).read_text(encoding="utf-8"))
            for key in WAREHOUSE_KEYS:
                if im.get(key) != wh.get(key):
                    errors.append(f"artifact khong khop warehouse pointer o khoa {key}: {im.get(key)!r} != {wh.get(key)!r}")
        if not Path(plan_path).exists():
            errors.append(f"khong co plan {plan_path}")
        else:
            plan_now = artifacts.sha256_file(Path(plan_path))
            plan_state["sha256_now"] = plan_now
            plan_state["changed_since_run"] = plan_now != provenance.get("plan_authority_sha256")
            if plan_state["changed_since_run"]:
                # Plan co the doi hop le sau khi artifact chay (vd GPT cap nhat dong trang thai truoc khi promote). Khong nuot im: moi bullet cua
                # coverage matrix luc chay phai con NGUYEN VAN trong plan hien tai (them bullet/trang thai khong lam artifact sai); thieu mot bullet = tu choi.
                missing_bullets = _bullets_missing_from_plan(analysis_dir, Path(plan_path))
                plan_state["bullets_verified"] = None if missing_bullets is None else (not missing_bullets)
                if missing_bullets is None:
                    errors.append("plan da doi so voi hash luc chay va khong doi chieu duoc EDA_COVERAGE_MATRIX.csv")
                elif missing_bullets:
                    errors.append(f"EDA_CURATED_PLAN.md da doi va {len(missing_bullets)} bullet cua coverage matrix khong con nguyen van trong plan: "
                                  f"{missing_bullets[:3]}")
    if not review_file.exists():
        errors.append(f"khong co file review {review_file}")
    else:
        text = review_file.read_text(encoding="utf-8")
        if analysis_dir.name not in text:
            errors.append(f"file review khong nhac analysis id {analysis_dir.name}")
        if gate not in text:
            errors.append(f"file review khong chua gate {gate!r}")
    if errors:
        raise PromoteError("KHONG promote - " + "; ".join(errors))
    now = now or dt.datetime.now(dt.timezone.utc)
    manifest_path = analysis_dir / "artifact_manifest.json"
    try:
        review_rel = review_file.relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        review_rel = review_file.as_posix()
    try:
        analysis_rel = analysis_dir.relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        analysis_rel = analysis_dir.as_posix()
    return {
        "analysis_dir": analysis_dir.name, "analysis_id": analysis_dir.name, "analysis_path_repo_relative": analysis_rel,
        "artifact_file_count": verified["manifest"]["file_count"], "artifact_manifest_sha256": artifacts.sha256_file(manifest_path),
        "batch_id": im["batch_id"], "code_content_sha256": provenance.get("eda_src_and_notebooks_and_plan_content_sha256"),
        "code_git_head": provenance["git_head"], "code_is_dirty": False,
        "input_manifest_sha256": artifacts.sha256_file(analysis_dir / "input_manifest.json"),
        "plan_authority_sha256_at_run": provenance["plan_authority_sha256"],
        "plan_authority_sha256_at_promotion": plan_state["sha256_now"], "plan_changed_since_run": plan_state["changed_since_run"],
        "plan_coverage_bullets_verified_present": plan_state.get("bullets_verified"),
        "promoted_at_utc": now.replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "review_date": review_date, "review_file": review_rel, "review_file_sha256": artifacts.sha256_file(review_file),
        "review_gate": gate, "reviewed_by": reviewed_by, "warehouse_database": im["warehouse_database"],
        "warehouse_source_manifest_sha256": im["source_manifest_sha256"], "wave": "A",
    }


def promote(analysis_dir: Path, review_file: Path, *, apply: bool = False, pointer_path: Path = POINTER_PATH,
            history_dir: Path = HISTORY_DIR, **kwargs: Any) -> dict[str, Any]:
    pointer = build_pointer(analysis_dir, review_file, outputs_dir=pointer_path.parent, **kwargs)
    result = {"pointer": pointer, "applied": False}
    if not apply:
        return result
    if Path(pointer_path).exists():
        old = json.loads(Path(pointer_path).read_text(encoding="utf-8"))
        history_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pointer_path, history_dir / f"eda_current__{old.get('analysis_id', 'unknown')}__promoted_{old.get('promoted_at_utc', 'na').replace(':', '')}.json")
    artifacts.atomic_write_json(Path(pointer_path), pointer)
    reread = json.loads(Path(pointer_path).read_text(encoding="utf-8"))
    resolved = Path(pointer_path).parent / reread["analysis_dir"]
    after = verify_artifact(resolved)
    if after["errors"] or artifacts.sha256_file(resolved / "artifact_manifest.json") != reread["artifact_manifest_sha256"]:
        raise PromoteError(f"doc lai pointer roi verify that bai: {after['errors']}")
    result.update(applied=True, pointer_sha256=artifacts.sha256_file(Path(pointer_path)), verified_after_write=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--analysis-dir", required=True, type=Path)
    parser.add_argument("--review-file", required=True, type=Path)
    parser.add_argument("--gate", default="PASS WAVE A")
    parser.add_argument("--reviewed-by", default="GPT")
    parser.add_argument("--review-date", default=dt.date.today().isoformat())
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = promote(args.analysis_dir, args.review_file, apply=args.apply, gate=args.gate, reviewed_by=args.reviewed_by,
                         review_date=args.review_date)
    except PromoteError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.apply:
        print("DRY-RUN: moi dieu kien dat; them --apply de ghi pointer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
