"""Prevalence/coverage DOC-CHI cua NULL o ba cot static tren bang `ml_reference_assignments` cho cac sample DA CHON cua dev1b/dev3b (GPT file 46 muc 3b).

    python warehouse_prevalence_v1.py --explain  --gpt-approval <file> --crawl-evidence <file>        # Step0 + EXPLAIN, khong chay aggregate
    python warehouse_prevalence_v1.py --run      --gpt-approval <file> --crawl-evidence <file>        # + 1 aggregate / dataset

Chay voi cwd=hotel-price-intelligence/backend (Settings doc `.env` theo cwd) va duong dan tuyet doi cho script/--gpt-approval/--crawl-evidence.

CHI DEM (coverage/prevalence), KHONG do anh huong len mo hinh, KHONG dung marginal de "chung minh" fidelity tung dong (phan vi du 42 van ap dung). Mot SELECT/dataset: LEFT JOIN giu orphan, GROUP BY
(`vn_observation_date`, `split`) de thay phan bo theo ngay/split; tong cac bucket + orphan phai CONG DUNG mau so (so sample da chon, ky vong 146.431). Dung lai ha tang cua `warehouse_audit_v3`
(guard DB `warehouse_dsdev_*`, duyet gan SHA cua CHINH script nay + bo SQL nay, cong crawler/gio kiem lai truoc MOI lenh, ngan sach/tran lenh, Buoc 0, phien READ ONLY, EXPLAIN truoc SELECT, tran so dong)."""
from __future__ import annotations

import argparse
import datetime as dt
import decimal
import hashlib
import importlib.util
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("warehouse_audit_v3", HERE / "warehouse_audit_v3.py")
wa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wa)

SCRIPT_PATH = Path(__file__).resolve()
PINNED_BASE_CONTROLLER_SHA256 = "491d3f4a3e220da15ed2b57eac07762351cd98c2cf95ab2058f492676cfcbbc0"      # warehouse_audit_v3.py da khoa (gate/Ctx/guarded/EvidenceGate cua script nay nam o do); doi => tu choi
Q4_PREFIX_LOWER_BOUNDS = {"fc_null": 25, "occ_null": 28}              # Q4 xac nhan (file 44a/46): trong 3.000 id nho nhat (PREFIX41), CA HAI dataset co 25 hang free_cancellation NULL va 28 hang max_occupancy NULL; prefix la tap con => tong toan dataset phai >= cac so nay (doi chieu cheo aggregate voi bang chung muc hang)
GROUP_CAP = 2000                                     # so nhom (ngay x split) toi da; tra ve GROUP_CAP + 1 dong = tran bi vuot => UNVERIFIED
FLAGS = ("fc", "bf")
COLUMNS = {"fc": "free_cancellation", "bf": "breakfast_included"}


def _count_if(condition: str, alias: str) -> str:
    """`SUM(CASE ... THEN 1 ELSE 0 END)`, KHONG `SUM(<bieu thuc boolean>)`: `a.id IS NOT NULL AND a.col = 0` la NULL (khong phai 0) khi col NULL, nen mot nhom toan cot NULL se cho SUM = NULL."""
    return f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END) AS {alias}"


def _flag_sums(alias: str, column: str) -> str:
    return ", ".join([_count_if(f"a.id IS NOT NULL AND a.{column} IS NULL", f"{alias}_null"), _count_if(f"a.id IS NOT NULL AND a.{column} = 0", f"{alias}_zero"),
                      _count_if(f"a.id IS NOT NULL AND a.{column} = 1", f"{alias}_one"), _count_if(f"a.id IS NOT NULL AND a.{column} IS NOT NULL AND a.{column} NOT IN (0, 1)", f"{alias}_invalid")])


SQL_PREVALENCE = ("SELECT s.vn_observation_date AS vn_date, s.split AS split, COUNT(*) AS selected_rows, " + _count_if("a.id IS NULL", "orphan_assignment") + ", "
                  + _flag_sums("fc", "free_cancellation") + ", " + _flag_sums("bf", "breakfast_included") + ", "
                  + _count_if("a.id IS NOT NULL AND a.max_occupancy IS NULL", "occ_null") + ", " + _count_if("a.id IS NOT NULL AND a.max_occupancy IS NOT NULL", "occ_known") + " "
                  "FROM ml_samples s LEFT JOIN ml_reference_assignments a ON a.id = s.ml_reference_assignment_id AND a.dataset_version = s.dataset_version "
                  "WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = 1 GROUP BY s.vn_observation_date, s.split ORDER BY s.vn_observation_date, s.split LIMIT " + str(GROUP_CAP + 1))


CWD_HINT = (" | goi y: `app.core.config.Settings` doc `.env` theo cwd; chay producer voi cwd=hotel-price-intelligence/backend (duong dan script/duyet/bang chung tuyet doi). "
            "Loi nay xay ra TRUOC khi mo ket noi (0 lenh SQL).")


def _looks_like_settings_error(exc: BaseException) -> bool:
    """Loi nap cau hinh (pydantic ValidationError cho `Settings`/thieu bien) — chi nhan dien kieu/thong diep, KHONG doc/in gia tri secret."""
    text = f"{type(exc).__name__} {exc}"
    return type(exc).__name__ == "ValidationError" or "Settings" in text or "Field required" in text


def _settings_fields(exc: BaseException) -> list[str]:
    """Ten truong (dong chi gom mot ten bien) trong thong diep ValidationError cua pydantic; KHONG lay `input_value`/gia tri nao."""
    return [name[:64] for name in re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)[ \t]*$", str(exc), re.MULTILINE)][:10]


def connect_refusal(exc: BaseException) -> str:
    """Thong diep tu choi khi ket noi that bai. Loi nap cau hinh (Settings) KHONG in nguyen van thong diep (pydantic co the kem `input_value`): chi in kieu loi + ten truong + goi y cwd."""
    if _looks_like_settings_error(exc):
        fields = ", ".join(_settings_fields(exc)) or "khong xac dinh"
        return f"ket noi DB that bai: {type(exc).__name__}: cau hinh Settings khong nap duoc (truong: {fields}; gia tri duoc an)" + CWD_HINT
    return f"ket noi DB that bai: {type(exc).__name__}: {exc}"


def script_sha256() -> str:
    return hashlib.sha256(SCRIPT_PATH.read_bytes()).hexdigest()


def sql_set_sha256() -> str:
    return hashlib.sha256(json.dumps({"SQL_PREVALENCE": SQL_PREVALENCE, "SQL_STEP0": wa.SQL_STEP0}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- danh gia thuan
NUMERIC = ("selected_rows", "orphan_assignment", "fc_null", "fc_zero", "fc_one", "fc_invalid", "bf_null", "bf_zero", "bf_one", "bf_invalid", "occ_null", "occ_known")


MAX_COUNTER = 10 ** 12                                # tran hop ly cho mot bo dem (so hang 1 dataset ~1.5e5); lon hon => khong tin


def _num(value: Any) -> int:
    """Bo dem hop le CHI la `int` (khong phai `bool`) hoac `decimal.Decimal` HUU HAN, NGUYEN, 0 <= n <= MAX_COUNTER (kieu ma `COUNT(*)`/`SUM(CASE...)` cua mysql-connector tra ve). Moi thu khac — NULL, bool, float,
    chuoi, numpy, Decimal NaN/inf/phan so/am/khong lo — la ValueError ro rang (khong cat phan thap phan, khong ep bool thanh 1, khong de OverflowError/InvalidOperation lam mat bao cao)."""
    if isinstance(value, bool) or not isinstance(value, (int, decimal.Decimal)):
        raise ValueError(f"kieu so dem khong duoc ho tro ({type(value).__name__}): {repr(value)[:80]}")
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            raise ValueError(f"so dem Decimal khong huu han: {value}")
        if value.copy_abs() > MAX_COUNTER:                # copy_abs() khong qua ngu canh Decimal: abs(Decimal('1E+999999999')) nem decimal.Overflow
            raise ValueError(f"so dem vuot tran {MAX_COUNTER}: {str(value)[:40]}")
        if value != value.to_integral_value():
            raise ValueError(f"so dem khong nguyen: {value}")
    number = int(value)
    if number < 0:
        raise ValueError(f"so dem am: {number}")
    if number > MAX_COUNTER:
        raise ValueError(f"so dem vuot tran {MAX_COUNTER}: {number}")
    return number


_MISSING = object()


def _driver_types(rows: Any) -> dict[str, dict[str, int]]:
    """Dem TEN KIEU (int / Decimal / ...) cua gia tri tho theo cot trong `NUMERIC`; 'missing' khi hang khong co cot (hoac khong phai Mapping). Chi de BAO CAO — JSON chi con 'number', nen kieu goc cua driver
    phai duoc ghi lai o day. Khong chuan hoa, khong nem loi."""
    out: dict[str, dict[str, int]] = {}
    rows = rows if isinstance(rows, (list, tuple)) else []
    for key in NUMERIC:
        counts: dict[str, int] = {}
        for row in rows:
            value = row.get(key, _MISSING) if isinstance(row, Mapping) else _MISSING
            name = "missing" if value is _MISSING else type(value).__name__
            counts[name] = counts.get(name, 0) + 1
        out[key] = counts
    return out


def evaluate_prevalence(rows: Sequence[Mapping[str, Any]], expected_selected: int, *, truncated: bool = False, lower_bounds: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Tong hop theo nhom (ngay, split). `integrity` = mau so dung + moi bucket cong dung (flag: orphan + null + 0 + 1 + invalid; occupancy: orphan + null + known) + 0 orphan + 0 invalid.
    `lower_bounds` (tuy chon): tong cac cot nay phai >= can duoi (Q4 prefix) — doi chieu cheo aggregate voi bang chung muc hang da co.
    Status: `MEASURED` (integrity day du), `FAIL` (vi pham integrity/so dem khong hop le), `UNVERIFIED` (bi cat tran nhom). Day KHONG la ket luan ve anh huong mo hinh."""
    driver_types = _driver_types(rows)                          # kieu THO cua driver theo cot, tinh TRUOC moi chuan hoa (ca khi truncated/FAIL); khong bao gio nem loi
    rows_received = len(rows) if isinstance(rows, (list, tuple)) else 0           # so hang THUC nhan tu truy van (ke ca hang sentinel); khop tong moi histogram trong driver_types
    if truncated:
        return {"status": "UNVERIFIED", "reason": f"so nhom vuot tran {GROUP_CAP}: khong cong du, khong ket luan", "integrity": {"truncated": True}, "driver_types": driver_types,
                "rows_received": rows_received, "groups_complete": False}
    try:
        groups = [{"vn_date": str(r["vn_date"]), "split": r["split"], **{k: _num(r[k]) for k in NUMERIC}} for r in rows]
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        return {"status": "FAIL", "reason": f"hang tong hop khong hop le: {type(exc).__name__}: {exc}", "integrity": {"valid_rows": False}, "driver_types": driver_types,
                "rows_received": rows_received, "groups_complete": False}
    total = {k: sum(g[k] for g in groups) for k in NUMERIC}
    per_group_ok = all(g["selected_rows"] == g["orphan_assignment"] + g["fc_null"] + g["fc_zero"] + g["fc_one"] + g["fc_invalid"]
                       and g["selected_rows"] == g["orphan_assignment"] + g["bf_null"] + g["bf_zero"] + g["bf_one"] + g["bf_invalid"]
                       and g["selected_rows"] == g["orphan_assignment"] + g["occ_null"] + g["occ_known"] for g in groups)
    integrity = {"selected_rows": total["selected_rows"], "expected_selected": expected_selected, "selected_equals_expected": total["selected_rows"] == expected_selected,
                 "fc_buckets_reconcile": total["selected_rows"] == total["orphan_assignment"] + total["fc_null"] + total["fc_zero"] + total["fc_one"] + total["fc_invalid"],
                 "bf_buckets_reconcile": total["selected_rows"] == total["orphan_assignment"] + total["bf_null"] + total["bf_zero"] + total["bf_one"] + total["bf_invalid"],
                 "occ_buckets_reconcile": total["selected_rows"] == total["orphan_assignment"] + total["occ_null"] + total["occ_known"], "every_group_reconciles": per_group_ok,
                 "zero_orphans": total["orphan_assignment"] == 0, "zero_invalid_flags": total["fc_invalid"] == 0 and total["bf_invalid"] == 0}
    bounds = dict(lower_bounds or {})
    integrity["prefix_lower_bounds"] = {key: {"required_at_least": bound, "observed": total[key], "ok": total[key] >= bound} for key, bound in bounds.items()}
    integrity["prefix_lower_bounds_ok"] = all(item["ok"] for item in integrity["prefix_lower_bounds"].values())
    integrity["ok"] = all(integrity[k] for k in ("selected_equals_expected", "fc_buckets_reconcile", "bf_buckets_reconcile", "occ_buckets_reconcile", "every_group_reconciles", "zero_orphans", "zero_invalid_flags",
                                                  "prefix_lower_bounds_ok"))
    known = total["selected_rows"] - total["orphan_assignment"]
    by_split: dict[str, dict[str, int]] = {}
    for g in groups:
        slot = by_split.setdefault(str(g["split"]), {k: 0 for k in NUMERIC})
        for k in NUMERIC:
            slot[k] += g[k]
    null_dates = {col: sorted({g["vn_date"] for g in groups if g[col] > 0}) for col in ("fc_null", "bf_null", "occ_null")}
    summary = {"groups": len(groups), "totals": total, "by_split": by_split,
               "null_fraction_of_non_orphan": {col: (total[col] / known if known else None) for col in ("fc_null", "bf_null", "occ_null")},
               "dates_with_nulls": {col: {"count": len(dates), "first": dates[0] if dates else None, "last": dates[-1] if dates else None} for col, dates in null_dates.items()}}
    return {"status": "MEASURED" if integrity["ok"] else "FAIL", "integrity": integrity, "summary": summary, "driver_types": driver_types, "groups": groups, "rows_received": rows_received, "groups_complete": True,
            "ledger_note": "groups = so DA chuan hoa thanh int (so sanh/cong lai duoc); kieu goc cua driver CHI nam o driver_types (JSON number khong chung minh kieu driver)",
            "note": "dem/coverage; khong do anh huong mo hinh; moi group CHI la dem, khong khang dinh fidelity tung dong"}


def summarize(report: Mapping[str, Any]) -> dict[str, Any]:
    if report.get("refusals"):
        return {"overall": "REFUSED", "exit_code": wa.EXIT_CODES["REFUSED"], "reasons": list(report["refusals"])}
    step0 = report.get("step0")
    if not step0 or not step0.get("all_ok"):
        return {"overall": "REFUSED", "exit_code": wa.EXIT_CODES["REFUSED"], "reasons": ["Buoc 0: manifest DB khong khop artifact"]}
    datasets = report.get("datasets") or {}
    expected = set(wa.DATASETS)
    problems: list[str] = []
    if set(datasets) != expected:
        problems.append(f"tap dataset sai: {sorted(datasets)}")
    statuses = {name: (datasets.get(name) or {}).get("status") for name in expected}
    if report.get("mode") == "explain":
        bad = [f"{n}: {s}" for n, s in statuses.items() if s != "PLAN_OK"]
        return {"overall": "EXPLAIN_OK" if not bad and not problems else "EXPLAIN_REFUSED", "exit_code": wa.EXIT_CODES["EXPLAIN_OK" if not bad and not problems else "EXPLAIN_REFUSED"], "reasons": bad + problems}
    if any(s == "FAIL" for s in statuses.values()):
        return {"overall": "FAIL", "exit_code": wa.EXIT_CODES["FAIL"], "reasons": [f"{n}: {s}" for n, s in statuses.items() if s != "MEASURED"] + problems}
    if problems or any(s != "MEASURED" for s in statuses.values()):
        return {"overall": "UNVERIFIED", "exit_code": wa.EXIT_CODES["UNVERIFIED"], "reasons": [f"{n}: {s}" for n, s in statuses.items() if s != "MEASURED"] + problems}
    return {"overall": "MEASURED", "exit_code": wa.EXIT_CODES["PASS_FULL"], "reasons": []}


# --------------------------------------------------------------------------- thuc thi
def run_dataset(ctx, name: str) -> dict[str, Any]:
    rows = wa.guarded(ctx, name, "prevalence", SQL_PREVALENCE, [name], driving="s", cap=GROUP_CAP + 1)
    if rows is None:
        return {"status": "PLAN_OK"}
    return evaluate_prevalence(rows, wa.EXPECTED_SELECTED, truncated=len(rows) > GROUP_CAP, lower_bounds=Q4_PREFIX_LOWER_BOUNDS)         # TOAN BO hang nhan duoc (khong cat): truncated => khong ledger, nhung histogram + rows_received day du


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="warehouse_dsdev_20261004_3src")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--explain", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--gpt-approval", required=True)
    parser.add_argument("--crawl-evidence", required=True)
    parser.add_argument("--budget-seconds", type=int, default=wa.DEFAULT_PREFLIGHT_BUDGET_S)
    parser.add_argument("--max-queries", type=int, default=wa.DEFAULT_PREFLIGHT_MAX_QUERIES)
    args = parser.parse_args(argv)
    mode_name = "run" if args.run else "explain"
    ops = {"run:prevalence"} if args.run else {"explain"}
    now = wa._now_utc()
    run_id = uuid.uuid4().hex[:12]
    report: dict[str, Any] = {"tool": "warehouse_prevalence_v1", "run_id": run_id, "mode": mode_name, "db": args.db, "created_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "script_sha256": script_sha256(),
                              "base_controller_sha256": wa.controller_sha256(), "sql_set_sha256": sql_set_sha256(), "budget_seconds": args.budget_seconds, "max_queries": args.max_queries, "refusals": []}
    refusals: list[str] = report["refusals"]
    if report["base_controller_sha256"] != PINNED_BASE_CONTROLLER_SHA256:
        refusals.append("controller nen (warehouse_audit_v3.py) khac ban da khoa: khong chay bang ha tang chua duoc duyet")
    if not wa.database_allowed(args.db):
        refusals.append(f"ten DB khong hop le ({args.db!r}): chi `warehouse_dsdev_*`")
    ok, why, approval = wa.check_approval(args.gpt_approval, ops, approvals_dir=wa.APPROVALS_DIR, controller_sha=report["script_sha256"], sql_sha=report["sql_set_sha256"])
    report["approval"] = approval
    if not ok:
        refusals.append(f"duyet cua GPT: {why}")
    gate = wa.EvidenceGate(args.crawl_evidence)
    try:
        gate.check(force=True)
    except wa.GateLost as exc:
        refusals.append(str(exc))
    artifacts: dict[str, Any] = {}
    if not refusals:
        try:
            artifacts = wa.load_artifacts(need_frames=False)
            report["artifacts"] = {"samples_sha256": artifacts["samples_sha"], "samples_rows": artifacts["samples_rows"]}
        except Exception as exc:  # noqa: BLE001
            refusals.append(f"artifact: {type(exc).__name__}: {exc}")
    if not refusals:
        started = wa._monotonic()
        try:
            conn = wa.connect(args.db)
        except Exception as exc:  # noqa: BLE001
            conn = None
            refusals.append(connect_refusal(exc))
        if conn is not None:
            try:
                ctx = wa.Ctx(conn, explain_only=(mode_name == "explain"), budget_s=args.budget_seconds, max_batches=1, max_queries=args.max_queries, gate=gate, run_id=run_id,
                             metadata_statements=wa.CONNECT_SET_STATEMENTS, started=started)
                rows = None
                try:
                    ctx.before_statement()
                except wa.Incomplete as exc:
                    refusals.append(f"truoc Buoc 0: {exc}")
                else:
                    ctx.metadata_statements += 1
                    try:
                        rows = wa.fetch(conn, wa.SQL_STEP0, list(wa.DATASETS))
                    except Exception as exc:  # noqa: BLE001
                        refusals.append(f"Buoc 0 loi: {type(exc).__name__}: {exc}")
                if rows is not None:
                    report["step0"] = wa.step0_verdict(rows, artifacts)
                    if report["step0"]["all_ok"]:
                        report["datasets"] = {}
                        for name in wa.DATASETS:
                            try:
                                report["datasets"][name] = run_dataset(ctx, name)
                            except wa.PlanRefused as exc:
                                report["datasets"][name] = {"status": "PLAN_REFUSED", "problems": exc.problems}
                            except wa.Incomplete as exc:
                                report["datasets"][name] = {"status": "UNVERIFIED", "reason": str(exc)}
                        report["plans"], report["stopped_reason"] = ctx.plans, ctx.stopped
                report["statements_total"], report["metadata_statements"] = ctx.statements(), ctx.metadata_statements
                report["queries_executed"], report["explains_executed"] = ctx.queries_run, ctx.explains_run
            finally:
                conn.close()
            report["elapsed_s"] = round(wa._monotonic() - started, 1)
    report["crawl_evidence"] = {"path": str(args.crawl_evidence), "checks": gate.checks, "sha_chain": gate.sha_chain, "receipts": gate.receipts, "last_verdict": gate.last_verdict}
    report["verdict"] = summarize(report)
    path, sha = wa.write_unique_json(f"prevalence_{mode_name}", report, run_id)
    print(json.dumps({"overall": report["verdict"]["overall"], "exit_code": report["verdict"]["exit_code"], "reasons": report["verdict"]["reasons"][:10]}, ensure_ascii=False, indent=1))
    print("bao cao:", path)
    return int(report["verdict"]["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
