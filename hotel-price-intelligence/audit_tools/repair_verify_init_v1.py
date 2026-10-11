"""Verify-config sau `init_dataset_build`, TRUOC `build_dataset` (GPT 76 §5.5): MOT lenh SELECT co gioi han doc `dataset_build_manifests` cua DUNG mot `dataset_version` va so voi gia tri DU KIEN doc lap.

Gia tri du kien KHONG suy tu chinh hang DB: no den tu bien lai du doan `config_prediction_receipt` (do `repair_parity_v1.predict_new_config` tao tu config CU da ghim + manifest ma moi) — tep bien lai duoc ghim byte
trong spec cua runner. Kiem: status=running, last_completed_step=initialized, hash cot `build_config_sha256` == du kien, hash tinh lai tu `build_config_json` (canonical) == du kien, `builder_code.code_sha256` ==
bien lai.new_code_sha256. Khong sua config de khop sau khi do. PASS => exit 0; moi truong hop khac => exit 1 (khong build). Phien ket noi chi-doc, `use_pure=True`, MAX_EXECUTION_TIME 5000 ms.

TRANG THAI: THIET KE + MA + TEST tren ket noi GIA; KHONG quyen thuc thi.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

HERE = Path(__file__).resolve().parent
SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
IDENT_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
VERSION_RE = re.compile(r"^ds_[0-9]{8}_[a-z0-9_]{1,24}$")
REQUIRED_CHECKS = ("exactly_one_row", "status_running", "step_initialized", "json_parsed", "stored_sha_equals_predicted", "recomputed_sha_equals_predicted", "stored_sha_equals_recomputed",
                   "code_sha_equals_receipt")                                                                  # TAP CHINH XAC cac kiem tra cua mot bao cao PASS (consumer phai doi chieu)
SQL_SET_READ_ONLY = "SET SESSION TRANSACTION READ ONLY"
SQL_SANITY = "SELECT /*+ MAX_EXECUTION_TIME(5000) */ @@session.transaction_read_only AS ro, @@session.time_zone AS tz"
SQL_MANIFEST = ("SELECT /*+ MAX_EXECUTION_TIME(5000) */ dataset_version, status, last_completed_step, build_config_sha256, CAST(build_config_json AS CHAR) AS build_config_json "
                "FROM dataset_build_manifests WHERE dataset_version = %s")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)                   # cung quy uoc `repair_parity_v1` / builder `config.canonical_json`


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sql_set_sha256() -> str:
    return sha256_hex("\n".join([SQL_SET_READ_ONLY, SQL_SANITY, SQL_MANIFEST]))


def load_prediction_receipt(path: Path) -> dict[str, Any]:
    data = Path(path).read_bytes()
    doc = json.loads(data.decode("utf-8"))
    problems = []
    if not isinstance(doc, dict) or doc.get("kind") != "config_prediction_receipt":
        problems.append("receipt_kind")
    else:
        if doc.get("ok") is not True or doc.get("violations"):
            problems.append("receipt_not_ok")
        for key in ("predicted_config_sha256", "new_code_sha256", "old_config_sha256"):
            if not (isinstance(doc.get(key), str) and SHA64_RE.match(doc[key])):
                problems.append(f"receipt_{key}")
    if problems:
        raise ValueError(f"prediction_receipt_invalid: {problems}")
    return {**doc, "file_sha256": hashlib.sha256(data).hexdigest()}


def verify(conn: Any, *, dataset_version: str, receipt: Mapping[str, Any], database: str | None = None) -> dict[str, Any]:
    """Doc DUNG mot hang (1 SELECT) va tra bao cao co `verdict`. `conn` da o che do chi-doc."""
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(SQL_MANIFEST, (dataset_version,))
        rows = cursor.fetchall()
    finally:
        cursor.close()
    report: dict[str, Any] = {"kind": "init_config_verification", "dataset_version": dataset_version, "database": database, "sql_set_sha256": sql_set_sha256(), "rows": len(rows), "predicted_config_sha256": receipt["predicted_config_sha256"],
                              "prediction_receipt_sha256": receipt["file_sha256"], "expected_new_code_sha256": receipt["new_code_sha256"]}
    checks: dict[str, bool] = {"exactly_one_row": len(rows) == 1}
    if len(rows) == 1:
        row = rows[0]
        raw = row["build_config_json"]
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        try:
            config = json.loads(text)
        except (TypeError, ValueError):
            config = None
        recomputed = sha256_hex(canonical_json(config)) if config is not None else None
        stored = row["build_config_sha256"]
        report.update(status=row["status"], last_completed_step=row["last_completed_step"], stored_config_sha256=stored, recomputed_config_sha256=recomputed)
        code = config.get("builder_code") if isinstance(config, dict) else None
        report["config_builder_code_sha256"] = code.get("code_sha256") if isinstance(code, dict) else None
        checks.update(status_running=row["status"] == "running", step_initialized=row["last_completed_step"] == "initialized", json_parsed=config is not None,
                      stored_sha_equals_predicted=stored == receipt["predicted_config_sha256"], recomputed_sha_equals_predicted=recomputed == receipt["predicted_config_sha256"],
                      stored_sha_equals_recomputed=stored == recomputed, code_sha_equals_receipt=report["config_builder_code_sha256"] == receipt["new_code_sha256"])
    report["checks"] = checks
    report["verdict"] = "PASS" if checks and all(checks.values()) else "FAIL"
    return report


def readonly_session(conn: Any) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(SQL_SET_READ_ONLY)
    finally:
        cursor.close()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(SQL_SANITY)
        row = cursor.fetchall()[0]
    finally:
        cursor.close()
    if str(row.get("ro")) not in ("1", "True") or row.get("tz") != "+00:00":
        raise RuntimeError(f"session_not_read_only_or_wrong_timezone: ro={row.get('ro')!r} tz={row.get('tz')!r}")


def default_connect(database: str) -> Any:
    backend = HERE.parent / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    import mysql.connector                                                                                # noqa: PLC0415
    from app.warehouse.connection import _connect_kwargs                                                  # noqa: PLC0415
    return mysql.connector.connect(**{**_connect_kwargs(database), "use_pure": True})


def main(argv: list[str] | None = None, *, connect: Callable[[str], Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--prediction-receipt", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="bao cao (xb: khong ghi de)")
    args = parser.parse_args(argv)
    if not IDENT_RE.match(args.db) or not VERSION_RE.match(args.dataset_version):
        print(json.dumps({"ok": False, "error": "bad_identifier"}))
        return 2
    if args.out.exists():
        print(json.dumps({"ok": False, "error": "out_exists"}))
        return 2
    try:
        receipt = load_prediction_receipt(args.prediction_receipt)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"prediction_receipt_rejected: {type(exc).__name__}: {str(exc)[:120]}"}))
        return 2
    conn = (connect or default_connect)(args.db)
    try:
        readonly_session(conn)
        report = verify(conn, dataset_version=args.dataset_version, receipt=receipt, database=args.db)
        conn.rollback()
    finally:
        conn.close()
    with open(args.out, "xb") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    print(json.dumps({"ok": report["verdict"] == "PASS", "verdict": report["verdict"], "failed_checks": sorted(k for k, v in report["checks"].items() if not v)}, sort_keys=True))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
