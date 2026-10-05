"""Preflight + snapshot DB van hanh cho moi lan build warehouse (rehearsal/official). CHI DOC.

    venv/Scripts/python.exe ../../outputs/warehouse-rebuild-20261004/scripts/preflight.py --tag aux_rehearsal \\
        --target-db warehouse_rh20261004_aux --batch-id b20261004_auxrh [--allow-running-crawl]
    ... preflight.py --compare outputs/warehouse-rebuild-20261004/snapshots/<file>.json     # sau build: so voi snapshot truoc

Kiem tra (FAIL cung): o C con >= 60 GB; khong co DB target, KHONG co BAT KY `wh_staging_%`, khong user whr_% ton tai; guard `GUARDED_PATHS` sach
va HEAD; DB van hanh 0 run/item queued|running. Mode MAC DINH la STRICT/no-overlap (GPT 07b MAJOR 3): sau build count/max_id ba bang van hanh
va latest run phai BANG TUYET DOI va van zero-active. Chi `--allow-running-crawl` (can gate rieng, KHONG dung o batch nay) moi cho quy tac don dieu
after >= before. Snapshot ghi mode.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "hotel-price-intelligence" / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
import mysql.connector  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.warehouse.provenance import GUARDED_PATHS, dirty_guarded_files  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_lib import (  # noqa: E402
    check_code_state, compare_input_snapshots, compare_snapshots, evaluate_preflight_doc, verify_profile,
)

SNAP_DIR = REPO / "outputs" / "warehouse-rebuild-20261004" / "snapshots"
MIN_FREE_GB = 60


def _connect(database=None):
    kwargs = dict(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD,
                  time_zone="+00:00", connection_timeout=15)
    if database:
        kwargs["database"] = database
    return mysql.connector.connect(**kwargs)


def ops_snapshot() -> dict:
    cn = _connect(settings.DB_NAME)
    cur = cn.cursor(dictionary=True)
    snap: dict = {"taken_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "operational_database": settings.DB_NAME}
    for table, pk in (("crawl_runs", "id"), ("crawl_run_items", "id"), ("price_observations", "record_id")):
        cur.execute(f"SELECT COUNT(*) n, MAX({pk}) mx FROM {table}")
        row = cur.fetchone()
        snap[table] = {"count": int(row["n"]), "max_id": int(row["mx"] or 0)}
    cur.execute("SELECT id, status, started_at, finished_at, processed, total FROM crawl_runs ORDER BY id DESC LIMIT 1")
    snap["latest_run"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in (cur.fetchone() or {}).items()}
    cur.execute("SELECT COUNT(*) n FROM crawl_runs WHERE status IN ('queued','running')")
    snap["runs_queued_or_running"] = int(cur.fetchone()["n"])
    cur.execute("SELECT COUNT(*) n FROM crawl_run_items WHERE status IN ('queued','running')")
    snap["items_queued_or_running"] = int(cur.fetchone()["n"])
    cn.close()
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Select-Object -ExpandProperty ProcessId"],
                         capture_output=True, text=True, timeout=60)
    snap["python_pids_observed_not_touched"] = sorted(int(x) for x in out.stdout.split() if x.strip().isdigit())
    return snap


def _repo_rel(path: Path) -> str:
    return Path(path).resolve().relative_to(REPO.resolve()).as_posix()


def compute_inputs(paths: dict[str, Path | None]) -> dict[str, dict[str, str]]:
    """Identity THAT cua dung cac file se truyen cho build_warehouse (tinh lai qua loader that, khong tin runbook)."""
    import hashlib
    from app.warehouse.cohort_manifest import load_cohort
    from app.warehouse.ownership_manifest import load_ownership_manifest
    from app.warehouse.source_manifest import load_source_manifest
    out: dict[str, dict[str, str]] = {}
    for kind, path in paths.items():
        if path is None:
            continue
        if kind == "source_manifest":
            sha = load_source_manifest(path, base_dir=REPO).manifest_sha256
        elif kind == "ownership_manifest":
            sha = load_ownership_manifest(path).manifest_sha256
        elif kind == "cohort_manifest":
            sha = load_cohort(path, base_dir=REPO).manifest_sha256
        else:                                               # cutoff_file: hash byte tho
            sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        out[kind] = {"path": _repo_rel(path), "sha256": sha}
    return out


def _input_paths(args) -> dict[str, Path | None]:
    return {"source_manifest": args.source_manifest, "ownership_manifest": args.ownership_manifest,
            "cohort_manifest": args.cohort_manifest, "cutoff_file": args.cutoff_file}


def _provenance_check(args) -> tuple[tuple[str, bool, str], dict]:
    """input_provenance.json.profiles[<profile>] phai ghim dung identity + dung duong dan cua manifest SE DUNG (khong 'preflight file A, build file B')."""
    inputs: dict = {}
    try:
        doc = json.loads((REPO / "outputs" / "warehouse-rebuild-20261004" / "input_provenance.json").read_text(encoding="utf-8"))
        inputs = compute_inputs(_input_paths(args))
        problems = verify_profile(doc, args.profile, inputs)
    except Exception as exc:  # noqa: BLE001 - thieu file cung la FAIL co thong bao
        problems = [f"{type(exc).__name__}: {exc}"]
    return (f"input_provenance profile={args.profile} khop manifest se dung", not problems, "; ".join(problems) or "OK"), inputs


def run_preflight(args) -> int:
    results: list[tuple[str, bool, str]] = []
    free_gb = shutil.disk_usage("C:\\").free / 1024 ** 3
    results.append(("C: free >= %d GB" % MIN_FREE_GB, free_gb >= MIN_FREE_GB, f"{free_gb:.1f} GB"))
    cn = _connect()
    cur = cn.cursor()
    cur.execute("SHOW DATABASES")
    databases = {row[0] for row in cur.fetchall()}
    results.append((f"target DB {args.target_db} chua ton tai", args.target_db not in databases, "" if args.target_db not in databases else "DA TON TAI"))
    stale = sorted(d for d in databases if d.startswith("wh_staging_"))
    results.append(("khong con BAT KY DB wh_staging_%", not stale, str(stale)))
    cur.execute("SELECT user FROM mysql.user WHERE user LIKE 'whr_%'")
    users = [row[0] for row in cur.fetchall()]
    results.append(("khong co MySQL user whr_% sot", not users, str(users)))
    cn.close()
    dirty = dirty_guarded_files(repo_root=REPO)
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    results.append(("GUARDED_PATHS sach", not dirty, f"HEAD {head[:12]}; dirty={dirty[:5]}"))
    prov_result, inputs = _provenance_check(args)
    results.append(prov_result)
    snap = ops_snapshot()
    snap.update({"tag": args.tag, "head": head, "free_gb_C": round(free_gb, 1), "guarded_paths_dirty": dirty,
                 "mode": "allow_running_crawl" if args.allow_running_crawl else "strict_no_overlap",
                 "profile": args.profile, "inputs": inputs})
    busy = snap["runs_queued_or_running"] or snap["items_queued_or_running"]
    ok_busy = (not busy) or args.allow_running_crawl
    results.append(("DB van hanh: 0 run/item queued|running" + (" (cho phep chay song song)" if args.allow_running_crawl else ""), ok_busy,
                    f"runs={snap['runs_queued_or_running']} items={snap['items_queued_or_running']}"))
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAP_DIR / f"{args.tag}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps({"snapshot": snap, "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results]},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    for name, ok, detail in results:
        print(f"[{'OK ' if ok else 'FAIL'}] {name}: {detail}")
    print(f"snapshot -> {path}")
    return 0 if all(ok for _n, ok, _d in results) else 1


def run_compare(path: Path) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    before = doc["snapshot"]
    after = ops_snapshot()
    strict = before.get("mode", "strict_no_overlap") == "strict_no_overlap"
    problems = evaluate_preflight_doc(doc) + compare_snapshots(before, after, strict=strict)
    now_head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    now_dirty = dirty_guarded_files(repo_root=REPO)
    problems += check_code_state(before.get("head"), now_head, now_dirty)
    print(f"HEAD truoc={before.get('head')} sau={now_head}; GUARDED_PATHS dirty sau build={now_dirty}")
    # input paths + hash phai bat bien suot stage: tinh lai tu DUNG path da ghi trong snapshot truoc build
    after_inputs: dict = {}
    try:
        recorded = before.get("inputs") or {}
        after_inputs = compute_inputs({kind: REPO / entry["path"] for kind, entry in recorded.items()})
    except Exception as exc:  # noqa: BLE001
        problems.append(f"khong tinh lai duoc input sau build: {type(exc).__name__}: {exc}")
    problems += compare_input_snapshots(before.get("inputs"), after_inputs)
    print("inputs (path -> sha256):", {k: (v['path'], v['sha256'][:12]) for k, v in after_inputs.items()})
    for table in ("crawl_runs", "crawl_run_items", "price_observations"):
        print(f"{table:<20} count {before[table]['count']} -> {after[table]['count']}  max_id {before[table]['max_id']} -> {after[table]['max_id']}")
    print(f"mode={'strict_no_overlap' if strict else 'allow_running_crawl'}; latest_run truoc={before.get('latest_run')} sau={after.get('latest_run')}")
    print("python pids truoc:", before["python_pids_observed_not_touched"], "sau:", after["python_pids_observed_not_touched"])
    with _connect() as cn:
        cur = cn.cursor()
        cur.execute("SHOW DATABASES")
        leftovers = sorted(r[0] for r in cur.fetchall() if r[0].startswith("wh_staging_"))
        cur.execute("SELECT user FROM mysql.user WHERE user LIKE 'whr_%'")
        users = [r[0] for r in cur.fetchall()]
    if leftovers or users:
        problems.append(f"con staging/user tam: {leftovers} {users}")
    for p in problems:
        print("[FAIL]", p)
    print("[OK] DB van hanh dung gate (" + ("bang tuyet doi" if strict else "don dieu") + ") va sach staging/user" if not problems else "[FAIL] co bat thuong")
    return 0 if not problems else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    parser.add_argument("--target-db")
    parser.add_argument("--batch-id")
    parser.add_argument("--allow-running-crawl", action="store_true")
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--profile", choices=("aux", "full"), help="aux = aux-only rehearsal; full = 3 nguon (rehearsal/official)")
    parser.add_argument("--source-manifest", type=Path, help="CHINH file se truyen cho build_warehouse --source-manifest")
    parser.add_argument("--ownership-manifest", type=Path, help="CHINH file se truyen cho build_warehouse --ownership-manifest")
    parser.add_argument("--cohort-manifest", type=Path, help="CHINH file se truyen cho build_warehouse --cohort-manifest")
    parser.add_argument("--cutoff-file", type=Path, help="bat buoc o profile full (gate_inputs.py dung chinh file nay)")
    args = parser.parse_args()
    if args.compare:
        return run_compare(args.compare)
    if not (args.tag and args.target_db and args.batch_id and args.profile and args.source_manifest and args.ownership_manifest and args.cohort_manifest):
        parser.error("can --tag --target-db --batch-id --profile --source-manifest --ownership-manifest --cohort-manifest")
    if args.profile == "full" and args.cutoff_file is None:
        parser.error("profile full can --cutoff-file")
    return run_preflight(args)


if __name__ == "__main__":
    raise SystemExit(main())
