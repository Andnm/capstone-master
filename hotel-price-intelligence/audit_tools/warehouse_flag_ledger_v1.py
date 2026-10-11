"""Producer DOC-CHI: ledger raw (`SQL_FLAG_LEDGER`) cho MOT dataset + (tuy chon) snapshot P4 cua `dataset_build_manifests` (`build_config_json`, `build_config_sha256`, `library_versions_json`) — GPT file 62/64.

    cwd = hotel-price-intelligence/backend (Settings doc `.env` theo cwd); duong dan tuyet doi cho script/duyet/bang chung/artifact.
    python warehouse_flag_ledger_v1.py --db <dsdev> --dataset <ds_YYYYMMDD_tag> --dataset-dir <outputs/datasets/ds_...> --expected-samples-sha256 <sha> \\
        [--snapshot-versions v1,v2 --expected-old-code-sha256 <sha>] --explain|--run --gpt-approval <file> --crawl-evidence <file> --budget-seconds N --max-queries N --max-batches N

Dung lai ha tang `warehouse_audit_v3` (controller nen pin cung `491d3f4a…`): guard DB `warehouse_dsdev_*`, duyet gan SHA cua CHINH script nay + bo SQL nay, cong crawler/gio kiem lai truoc MOI lenh (ke ca moi trang keyset), ngan sach/tran lenh,
EXPLAIN truoc SELECT, phien READ ONLY, MAX_EXECUTION_TIME 10 s. Moi process: MOT dataset, tuan tu, khong retry. Evidence KHONG tu gia han o day: van hanh vien chay `crawl_idle_evidence.py --renew-seconds` (bounded) RIENG; bang chung qua tuoi (>120 s) => dung, UNVERIFIED.

Ledger chi duoc ghi ra Parquet khi `evaluate_ledger` = PASS (146.431 khoa duy nhat == tap khoa Parquet cua dataset, 0 orphan, identity khop Parquet, domain hop le). Khong ghi de."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import types
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    """Nap module phu thuoc tu CHINH cac byte da bam (doc MOT lan, bam, compile/exec cung byte do): sha ghi/pin la sha cua ma THAT SU chay (khong co khe TOCTOU giua bam va nap)."""
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


wa = _load("warehouse_audit_v3", "warehouse_audit_v3.py")
rp = _load("repair_parity_v1_for_ledger", "repair_parity_v1.py")

SCRIPT_PATH = Path(__file__).resolve()
PINNED_BASE_CONTROLLER_SHA256 = "491d3f4a3e220da15ed2b57eac07762351cd98c2cf95ab2058f492676cfcbbc0"
PINNED_REPAIR_PARITY_SHA256 = "1022c5ee9f0227536413636d5bcbb46fc18a0d59849df54d53eb83e75cd83510"                                                       # dependency pin: evaluator/serializer/IO ledger la ma DUOC duyet cung producer (SQL set khong seal logic nay)
SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
DATASET_RE = re.compile(r"^ds_[0-9]{8}_[a-z0-9]{1,24}$")
SQL_STEP0_ONE = ("SELECT dataset_version, import_batch_id, status, last_completed_step, active_step, build_config_sha256, output_parquet_sha256_json, anomaly_registry_cutoff_at, "
                 "anomaly_registry_file_sha256 FROM dataset_build_manifests WHERE dataset_version = %s")
SQL_MANIFEST_SNAPSHOT = ("SELECT dataset_version, import_batch_id, build_config_json, build_config_sha256, library_versions_json FROM dataset_build_manifests "
                         "WHERE dataset_version IN (%s, %s) ORDER BY dataset_version")
EXPECTED_BATCH_ID = wa.BATCH_ID
OLD_CODE_FILES = 40                                                                              # manifest ma cu (file 63 §3): 40 file


def sql_statements() -> dict[str, str]:
    return {"SQL_STEP0_ONE": SQL_STEP0_ONE, "SQL_MANIFEST_SNAPSHOT": SQL_MANIFEST_SNAPSHOT, "SQL_FLAG_LEDGER": rp.SQL_FLAG_LEDGER}


def sql_set_sha256() -> str:
    return hashlib.sha256(json.dumps(sql_statements(), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def script_sha256() -> str:
    return hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- thuan
def load_parquet_identity(dataset_dir: Path, expected_sha256: str, expected_rows: int) -> dict[str, Any]:
    """Doc CHI 4 cot dinh danh tu samples.parquet (pyarrow, khong pandas), kiem sha256 file + so hang; tra {identity, sha, rows}. Loi => ValueError."""
    import pyarrow.parquet as pq
    path = Path(dataset_dir) / "samples.parquet"
    digest = wa.sha256_file(path)
    if digest != expected_sha256:
        raise ValueError(f"samples.parquet sha {digest} != expected {expected_sha256}")
    table = pq.read_table(path, columns=["warehouse_record_id", "hotel_id", "checkin_date", "canonical_series_id"])
    if table.num_rows != expected_rows:
        raise ValueError(f"samples.parquet rows {table.num_rows} != expected {expected_rows}")
    ids = table.column("warehouse_record_id").to_pylist()
    identity = {int(rid): (h, rp._iso_date(c), s) for rid, h, c, s in zip(ids, table.column("hotel_id").to_pylist(), table.column("checkin_date").to_pylist(), table.column("canonical_series_id").to_pylist())}
    if len(identity) != len(ids):
        raise ValueError("samples.parquet co warehouse_record_id trung")
    return {"identity": identity, "sha256": digest, "rows": table.num_rows}


def step0_single(rows: Sequence[Mapping[str, Any]], *, dataset: str, expected_sha256: str, expected_rows: int, expected_config_sha256: str | None = None) -> dict[str, Any]:
    """Dung 1 hang manifest: status=pass, last_completed_step=validation, active_step NULL, batch dung, `samples.parquet.file_sha256` == hash artifact, rows khop."""
    if len(rows) != 1 or rows[0].get("dataset_version") != dataset:
        return {"ok": False, "reason": f"expected exactly 1 manifest row for {dataset}, got {len(rows)}", "checks": {}}
    row = rows[0]
    entry = wa.parquet_entry(row.get("output_parquet_sha256_json"))
    checks = {"status_pass": row.get("status") == "pass", "last_completed_step_validation": row.get("last_completed_step") == "validation", "active_step_null": row.get("active_step") is None,
              "batch": row.get("import_batch_id") == EXPECTED_BATCH_ID, "parquet_file_sha256_exact": bool(entry is not None and entry.get("file_sha256") == expected_sha256),
              "parquet_rows": bool(entry is not None and entry.get("rows") == expected_rows)}
    if expected_config_sha256 is not None:                                                           # pin DOC LAP ngoai DB: self-hash dung chi chung minh nhat quan voi sha DB hien tai
        checks["build_config_sha256_pin"] = row.get("build_config_sha256") == expected_config_sha256
    identity = {"import_batch_id": row.get("import_batch_id"), "status": row.get("status"), "last_completed_step": row.get("last_completed_step"), "active_step": row.get("active_step"),
                "build_config_sha256": row.get("build_config_sha256"), "samples_file_sha256_in_db": None if entry is None else entry.get("file_sha256"),
                "samples_rows_in_db": None if entry is None else entry.get("rows"), "anomaly_registry_cutoff_at": str(row.get("anomaly_registry_cutoff_at")),
                "anomaly_registry_file_sha256": row.get("anomaly_registry_file_sha256")}
    return {"ok": all(checks.values()), "checks": checks, "identity": identity}


def _json_value(raw: Any) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def evaluate_snapshot(rows: Sequence[Mapping[str, Any]], versions: Sequence[str], *, expected_old_code_sha256: str | None, expected_config_sha256s: Mapping[str, str] | None = None) -> dict[str, Any]:
    """P4: dung 2 hang cho dung 2 version; `build_config_json` la object; canonical sha TAI LAP `build_config_sha256` trong DB; `builder_code` tu nhat quan (40 file, code_sha256 == cong thuc) va == pin neu co;
    `library_versions_json` la object. Vi pham => ok False (khong fallback log/subset)."""
    violations: list[str] = []
    out: dict[str, Any] = {"versions": {}}
    if len(rows) != 2 or sorted(r.get("dataset_version") for r in rows) != sorted(versions) or len(set(versions)) != 2:
        return {"ok": False, "violations": [f"expected exactly 2 rows for {sorted(versions)}, got {[r.get('dataset_version') for r in rows]}"], "versions": {}}
    for row in rows:
        version = row["dataset_version"]
        entry: dict[str, Any] = {"build_config_sha256_in_db": row.get("build_config_sha256"), "import_batch_id": row.get("import_batch_id")}
        if row.get("import_batch_id") != EXPECTED_BATCH_ID:
            violations.append(f"{version}: import_batch_id {row.get('import_batch_id')!r} != {EXPECTED_BATCH_ID!r}")
        if expected_config_sha256s is not None:
            pin = expected_config_sha256s.get(version)
            if pin is None:
                violations.append(f"{version}: thieu pin config doc lap")
            elif row.get("build_config_sha256") != pin:
                violations.append(f"{version}: build_config_sha256 in DB != pin doc lap")
        try:
            config = _json_value(row.get("build_config_json"))
            libraries = _json_value(row.get("library_versions_json"))
        except (ValueError, TypeError) as exc:
            violations.append(f"{version}: json_unparseable: {type(exc).__name__}")
            out["versions"][version] = entry
            continue
        if not isinstance(config, dict) or not isinstance(libraries, dict):
            violations.append(f"{version}: config/library_versions not JSON objects")
            out["versions"][version] = entry
            continue
        entry["canonical_config_sha256"] = rp.config_sha256(config)
        if entry["canonical_config_sha256"] != row.get("build_config_sha256"):
            violations.append(f"{version}: canonical config sha != build_config_sha256 in DB")
        if expected_config_sha256s is not None and expected_config_sha256s.get(version) is not None and entry["canonical_config_sha256"] != expected_config_sha256s[version]:
            violations.append(f"{version}: canonical config sha != pin doc lap")
        code = config.get("builder_code") if isinstance(config.get("builder_code"), dict) else None
        if code is None or not isinstance(code.get("files"), dict):
            violations.append(f"{version}: builder_code missing")
        else:
            entry["builder_code"] = {"code_sha256": code.get("code_sha256"), "files": len(code["files"])}
            if len(code["files"]) != OLD_CODE_FILES:
                violations.append(f"{version}: builder_code files {len(code['files'])} != {OLD_CODE_FILES}")
            if rp.code_sha256_of(code["files"], code.get("excluded_backend_prefixes", [])) != code.get("code_sha256"):
                violations.append(f"{version}: builder_code self hash mismatch")
            if expected_old_code_sha256 is not None and code.get("code_sha256") != expected_old_code_sha256:
                violations.append(f"{version}: builder_code sha != pinned old")
        entry["library_versions"] = libraries
        entry["config"] = config
        out["versions"][version] = entry
    out.update(ok=not violations, violations=violations)
    return out


def summarize(report: Mapping[str, Any]) -> dict[str, Any]:
    if report.get("refusals"):
        return {"overall": "REFUSED", "exit_code": wa.EXIT_CODES["REFUSED"], "reasons": list(report["refusals"])}
    step0 = report.get("step0")
    if not step0 or not step0.get("ok"):
        return {"overall": "REFUSED", "exit_code": wa.EXIT_CODES["REFUSED"], "reasons": [f"Buoc 0 khong khop: {(step0 or {}).get('checks') or (step0 or {}).get('reason')}"]}
    snapshot = report.get("snapshot")
    if snapshot is not None and snapshot.get("status") == "FAIL":
        return {"overall": "FAIL", "exit_code": wa.EXIT_CODES["FAIL"], "reasons": [f"P4 snapshot: {snapshot.get('violations')}"]}
    ledger = report.get("ledger") or {}
    status = ledger.get("status")
    if report.get("mode") == "explain":
        ok = status == "PLAN_OK"
        return {"overall": "EXPLAIN_OK" if ok else "EXPLAIN_REFUSED", "exit_code": wa.EXIT_CODES["EXPLAIN_OK" if ok else "EXPLAIN_REFUSED"], "reasons": [] if ok else [f"ledger: {status}"]}
    if status == "FAIL":
        return {"overall": "FAIL", "exit_code": wa.EXIT_CODES["FAIL"], "reasons": [f"ledger: {ledger.get('violations')}"]}
    if status != "PASS" or (snapshot is not None and snapshot.get("status") != "PASS"):
        return {"overall": "UNVERIFIED", "exit_code": wa.EXIT_CODES["UNVERIFIED"], "reasons": [f"ledger: {status} {ledger.get('reason', '')}".strip()]}
    return {"overall": "LEDGER_PASS", "exit_code": wa.EXIT_CODES["PASS_FULL"], "reasons": []}


# --------------------------------------------------------------------------- thuc thi
def run_ledger(ctx, dataset: str, identity: Mapping[int, Sequence[Any]], expected_rows: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    try:
        info = wa.iter_keyset(ctx, dataset, "ledger", rp.SQL_FLAG_LEDGER, [dataset], [0], rows.extend, lambda page: [page[-1]["record_id"]], driving="s")
    except wa.PlanRefused as exc:
        return {"status": "PLAN_REFUSED", "problems": exc.problems}, rows
    except wa.Incomplete as exc:
        return {"status": "UNVERIFIED", "reason": str(exc), "rows_so_far": len(rows)}, rows
    if info.get("explain_only"):
        return {"status": "PLAN_OK"}, rows
    if info.get("complete") is not True:
        return {"status": "UNVERIFIED", "reason": f"keyset chua het du lieu: {info}", "rows_so_far": len(rows)}, rows
    evaluation = rp.evaluate_ledger(rows, dataset_version=dataset, parquet_identity=identity, expected_rows=expected_rows)
    evaluation["keyset"] = info
    return evaluation, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--expected-samples-sha256", required=True)
    parser.add_argument("--expected-rows", type=int, default=rp.EXPECTED_ROWS)
    parser.add_argument("--snapshot-versions", default="", help="v1,v2: bat P4 (snapshot build_config_json/library_versions_json cua dung 2 version)")
    parser.add_argument("--expected-old-code-sha256", default=None)
    parser.add_argument("--expected-config-sha256", action="append", default=[], metavar="VERSION=SHA256", help="pin config doc lap; bat buoc cho dataset trong --run va cho CA HAI version khi co --snapshot-versions")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--explain", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--gpt-approval", required=True)
    parser.add_argument("--crawl-evidence", required=True)
    parser.add_argument("--budget-seconds", type=int, required=True)
    parser.add_argument("--max-queries", type=int, required=True)
    parser.add_argument("--max-batches", type=int, required=True)
    args = parser.parse_args(argv)
    mode_name = "run" if args.run else "explain"
    versions = [v for v in args.snapshot_versions.split(",") if v]
    ops = {"run:ledger"} if args.run else {"explain"}
    if versions and args.run:
        ops.add("run:p4")                                                                        # P4 la SELECT du lieu: chi trong --run, can quyen rieng `run:p4`
    pins: dict[str, str] = {}
    pin_errors: list[str] = []
    for item in args.expected_config_sha256:
        version, _, digest = item.partition("=")
        if not DATASET_RE.match(version) or not SHA64_RE.match(digest) or version in pins:
            pin_errors.append(f"--expected-config-sha256 khong hop le/trung: {item[:80]!r}")
        else:
            pins[version] = digest
    run_id = uuid.uuid4().hex[:12]
    report: dict[str, Any] = {"tool": "warehouse_flag_ledger_v1", "run_id": run_id, "mode": mode_name, "db": args.db, "dataset": args.dataset, "created_at_utc": wa._now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "script_sha256": script_sha256(), "base_controller_sha256": wa.controller_sha256(), "repair_parity_sha256": rp.__source_sha256__,
                              "sql_set_sha256": sql_set_sha256(), "budget_seconds": args.budget_seconds, "max_queries": args.max_queries, "max_batches": args.max_batches, "snapshot_versions": versions,
                              "expected_samples_sha256": args.expected_samples_sha256, "expected_rows": args.expected_rows, "expected_config_sha256s": pins,
                              "base_source_sha256": wa.__source_sha256__, "repair_parity_source_sha256": rp.__source_sha256__, "refusals": []}
    refusals: list[str] = report["refusals"]
    if wa.__source_sha256__ != PINNED_BASE_CONTROLLER_SHA256 or report["base_controller_sha256"] != PINNED_BASE_CONTROLLER_SHA256:
        refusals.append("controller nen (warehouse_audit_v3.py) khac ban da khoa")
    if rp.__source_sha256__ != PINNED_REPAIR_PARITY_SHA256:
        refusals.append("repair_parity_v1.py (evaluator/serializer ledger) khac ban da pin cung producer")
    refusals.extend(pin_errors)
    if args.run:
        if args.dataset not in pins:
            refusals.append("--run can --expected-config-sha256 cho dataset")
        if versions and not all(v in pins for v in versions):
            refusals.append("--snapshot-versions can --expected-config-sha256 cho CA HAI version")
    if not wa.database_allowed(args.db):
        refusals.append(f"ten DB khong hop le ({args.db!r}): chi `warehouse_dsdev_*`")
    if not DATASET_RE.match(args.dataset):
        refusals.append(f"ten dataset khong hop le: {args.dataset!r}")
    if versions and (len(versions) != 2 or len(set(versions)) != 2 or not all(DATASET_RE.match(v) for v in versions)):
        refusals.append("--snapshot-versions phai la DUNG 2 version khac nhau hop le")
    ok, why, approval = wa.check_approval(args.gpt_approval, ops, approvals_dir=wa.APPROVALS_DIR, controller_sha=report["script_sha256"], sql_sha=report["sql_set_sha256"])
    report["approval"] = approval
    if not ok:
        refusals.append(f"duyet cua GPT: {why}")
    gate = wa.EvidenceGate(args.crawl_evidence)
    try:
        gate.check(force=True)
    except wa.GateLost as exc:
        refusals.append(str(exc))
    parquet: dict[str, Any] = {}
    if not refusals:
        try:
            parquet = load_parquet_identity(args.dataset_dir, args.expected_samples_sha256, args.expected_rows)
            report["parquet"] = {"sha256": parquet["sha256"], "rows": parquet["rows"], "dataset_dir": str(args.dataset_dir)}
        except Exception as exc:                                                                 # noqa: BLE001
            refusals.append(f"artifact: {type(exc).__name__}: {exc}")
    rows: list[dict[str, Any]] = []
    if not refusals:
        started = wa._monotonic()
        try:
            conn = wa.connect(args.db)
        except Exception as exc:                                                                 # noqa: BLE001
            conn = None
            refusals.append(f"ket noi DB that bai: {type(exc).__name__}: {str(exc)[:200]}")
        if conn is not None:
            try:
                ctx = wa.Ctx(conn, explain_only=(mode_name == "explain"), budget_s=args.budget_seconds, max_batches=args.max_batches, max_queries=args.max_queries, gate=gate, run_id=run_id,
                             metadata_statements=wa.CONNECT_SET_STATEMENTS, started=started)
                step0_rows = None
                try:
                    ctx.before_statement()
                except wa.Incomplete as exc:
                    refusals.append(f"truoc Buoc 0: {exc}")
                else:
                    ctx.metadata_statements += 1
                    try:
                        step0_rows = wa.fetch(conn, SQL_STEP0_ONE, [args.dataset])
                    except Exception as exc:                                                     # noqa: BLE001
                        refusals.append(f"Buoc 0 loi: {type(exc).__name__}: {str(exc)[:200]}")
                if step0_rows is not None:
                    report["step0"] = step0_single(step0_rows, dataset=args.dataset, expected_sha256=args.expected_samples_sha256, expected_rows=args.expected_rows, expected_config_sha256=pins.get(args.dataset))
                    if report["step0"]["ok"]:
                        if versions and mode_name == "explain":
                            report["snapshot"] = {"status": "SKIPPED_IN_EXPLAIN"}
                        if versions and mode_name == "run":
                            try:
                                ctx.before_statement()
                                ctx.metadata_statements += 1
                                snap_rows = wa.fetch(conn, SQL_MANIFEST_SNAPSHOT, versions)
                                snap = evaluate_snapshot(snap_rows, versions, expected_old_code_sha256=args.expected_old_code_sha256, expected_config_sha256s=pins)
                                snap["status"] = "PASS" if snap["ok"] else "FAIL"
                                report["snapshot"] = snap
                                payload = {"run_id": run_id, "versions": versions, "trusted": bool(snap["ok"]), "rows": [{k: (v if isinstance(v, (str, int, type(None))) else str(v)) for k, v in r.items()} for r in snap_rows],
                                           "evaluation": snap}
                                path, sha = wa.write_unique_json("manifest_snapshot" if snap["ok"] else "manifest_snapshot_UNTRUSTED", payload, run_id)          # FAIL => UNTRUSTED, chi de dieu tra
                                report["snapshot"]["file"] = {"path": str(path), "sha256": sha, "trusted": bool(snap["ok"])}
                            except wa.Incomplete as exc:
                                report["snapshot"] = {"status": "UNVERIFIED", "reason": str(exc)}
                        if versions and mode_name == "run" and report.get("snapshot", {}).get("status") != "PASS":
                            report["ledger"] = {"status": "NOT_RUN", "reason": f"P4 snapshot {report.get('snapshot', {}).get('status')}: khong doc ledger"}          # P4 duoc yeu cau => chi PASS moi vao ledger
                        else:
                            ledger, rows = run_ledger(ctx, args.dataset, parquet["identity"], args.expected_rows)
                            report["ledger"] = ledger
                report["plans"], report["stopped_reason"] = ctx.plans, ctx.stopped
                report["statements_total"], report["metadata_statements"] = ctx.statements(), ctx.metadata_statements
                report["queries_executed"], report["explains_executed"] = ctx.queries_run, ctx.explains_run
            finally:
                conn.close()
            report["elapsed_s"] = round(wa._monotonic() - started, 1)
    ledger_result = report.get("ledger") or {}
    report["verdict"] = summarize(report)
    if mode_name == "run" and report["verdict"]["overall"] == "LEDGER_PASS" and ledger_result.get("status") == "PASS" and rows:       # chi xuat ledger dung lai khi OVERALL phase PASS
        out_path = wa.AUDIT_OUT_DIR / f"ledger_{args.dataset}_{run_id}.parquet"
        wa.AUDIT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            ledger_result["file"] = {"path": str(out_path), "sha256": rp.write_ledger(rows, out_path), "rows": len(rows)}
        except Exception as exc:                                                                      # noqa: BLE001
            ledger_result["status"] = "FAIL"
            ledger_result["write_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            report["verdict"] = {"overall": "FAIL", "exit_code": wa.EXIT_CODES["FAIL"], "reasons": [f"ghi ledger that bai: {ledger_result['write_error']}"]}
    report["crawl_evidence"] = {"path": str(args.crawl_evidence), "checks": gate.checks, "sha_chain": gate.sha_chain, "receipts": gate.receipts, "last_verdict": gate.last_verdict}
    path, sha = wa.write_unique_json(f"flag_ledger_{mode_name}", report, run_id)
    print(json.dumps({"overall": report["verdict"]["overall"], "exit_code": report["verdict"]["exit_code"], "reasons": report["verdict"]["reasons"][:10]}, ensure_ascii=False, indent=1))
    print("bao cao:", path)
    return int(report["verdict"]["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
