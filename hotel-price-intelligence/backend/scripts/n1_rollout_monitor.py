"""G4 monitor cho regime parser N1 (bữa sáng phủ định) — CHỈ ĐỌC. Đặc tả: discuss/parser-n1-fix-20261013/09a-claude-g4-monitor-spec.md; ngưỡng đăng ký trước:
outputs/s2-rollout-package-20261006/monitor_preregistration.md (không đổi ở đây).

So sánh THEO TỪNG HOTEL, trên cùng một DB nguồn: cửa sổ TRƯỚC (7 ngày crawl cuối trên code cũ) vs SAU (mọi run completed trên code mới).
  1) share_breakfast_true (option có giá, không sold-out, breakfast không NULL): cờ nếu |delta| >= 0,20 và mỗi cửa sổ >= 30 option;
  2) unavailable_rate (item success đã có reference khớp: exact/alias/unavailable/ambiguous): cờ nếu tăng >= 0,15 và mỗi cửa sổ >= 20 item.
Thiếu mẫu => INSUFFICIENT_SAMPLE (KHÔNG phải bằng chứng "không ảnh hưởng"); chưa đủ >= 3 ngày/3 run code mới => NOT_READY.
Lệnh: python scripts/n1_rollout_monitor.py --source local_primary [--out]
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

THRESHOLDS = {"breakfast_delta": 0.20, "min_options": 30, "unavailable_delta": 0.15, "min_items": 20,
              "before_days": 7, "min_after_days": 3, "min_after_runs": 3}
ELIGIBLE_REFERENCE_STATUSES = ("exact", "alias", "unavailable", "ambiguous")
FIXED_NOTE = "INSUFFICIENT_SAMPLE hotels are not evidence of zero impact"
MAX_MS_LIMIT = 600000
SQL_RUNS = "SELECT id, status, started_at, scraper_version FROM crawl_runs ORDER BY id"
SQL_BREAKFAST = ("SELECT hotel_id, SUM(breakfast_included=1), COUNT(*) FROM price_observations WHERE crawl_run_id IN ({ph}) AND price_per_night IS NOT NULL "
                 "AND is_sold_out=0 AND availability_status='available' AND breakfast_included IS NOT NULL GROUP BY hotel_id")
SQL_UNAVAILABLE = ("SELECT hotel_id, SUM(reference_match_status='unavailable'), COUNT(*) FROM crawl_run_items WHERE crawl_run_id IN ({ph}) AND status='success' "
                   "AND reference_match_status IN ('exact','alias','unavailable','ambiguous') AND hotel_id IS NOT NULL GROUP BY hotel_id")
_IN_LIST = re.compile(r"IN \((?:%s,)*%s\)")
_SET_TIMEOUT = re.compile(r"^SET SESSION MAX_EXECUTION_TIME=[1-9][0-9]{0,5}$")
# deny-list là lớp thứ hai (phòng khi một template bị sửa nhầm): file-I/O, khóa, hàm có tác dụng phụ, chú thích, nhiều câu lệnh
SQL_DENY = re.compile(r"\b(INTO|OUTFILE|DUMPFILE|FOR\s+UPDATE|LOCK|SLEEP|BENCHMARK|LOAD_FILE|GET_LOCK|RELEASE_LOCK)\b|;|--|#|/\*", re.IGNORECASE)


def normalize_sql(sql: str) -> str:
    return _IN_LIST.sub("IN (%s...)", " ".join(sql.split()))


ALLOWED_TEMPLATES = {normalize_sql(SQL_RUNS), normalize_sql(SQL_BREAKFAST.format(ph="%s")), normalize_sql(SQL_UNAVAILABLE.format(ph="%s"))}
ALLOWED_SESSION = {"START TRANSACTION READ ONLY", "COMMIT"}


def sql_is_allowed(sql: str) -> bool:
    """Chỉ đúng 3 template SELECT sinh sẵn của monitor (+ phiên chỉ-đọc); không SELECT tùy ý."""
    flat = " ".join(sql.split())
    if SQL_DENY.search(flat):
        return False
    return flat in ALLOWED_SESSION or bool(_SET_TIMEOUT.match(flat)) or normalize_sql(flat) in ALLOWED_TEMPLATES
DEFAULT_POLICY = Path(__file__).resolve().parents[2] / "ml" / "shared" / "policies" / "n1" / "n1_policy_v1.json"


def version_tuple(value: Optional[str]) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", value or ""))


def vn_day(started_at_utc: datetime) -> date:
    """`crawl_runs.started_at` lưu UTC naive; ngày crawl tính theo giờ VN (UTC+7)."""
    return (started_at_utc + timedelta(hours=7)).date()


def select_windows(runs: Iterable[Dict[str, Any]], boundary_version: str, before_days: int, min_after_days: int, min_after_runs: int) -> Dict[str, Any]:
    completed = sorted((r for r in runs if r.get("status") == "completed"), key=lambda r: (r["started_at"], r["id"]))
    boundary = version_tuple(boundary_version)
    new_runs = [r for r in completed if version_tuple(r["scraper_version"]) >= boundary]
    out: Dict[str, Any] = {"status": "READY", "reasons": [], "boundary_version": boundary_version, "boundary_run_id": None,
                           "before_run_ids": [], "after_run_ids": [], "before_days": [], "after_days": [], "post_boundary_old_version_runs": []}
    if not new_runs:
        out.update(status="NOT_READY", reasons=["no_new_code_run"])
        return out
    first = new_runs[0]
    out["boundary_run_id"] = first["id"]
    old_before = [r for r in completed if version_tuple(r["scraper_version"]) < boundary and (r["started_at"], r["id"]) < (first["started_at"], first["id"])]
    out["post_boundary_old_version_runs"] = [r["id"] for r in completed if version_tuple(r["scraper_version"]) < boundary and (r["started_at"], r["id"]) > (first["started_at"], first["id"])]
    days = sorted({vn_day(r["started_at"]) for r in old_before})[-before_days:]
    out["before_days"] = [d.isoformat() for d in days]
    out["before_run_ids"] = [r["id"] for r in old_before if vn_day(r["started_at"]) in days]
    out["after_run_ids"] = [r["id"] for r in new_runs]
    out["after_days"] = sorted({vn_day(r["started_at"]).isoformat() for r in new_runs})
    if not out["before_run_ids"]:
        out["reasons"].append("no_before_window_run")
    if len(out["after_days"]) < min_after_days:
        out["reasons"].append(f"after_days<{min_after_days}")
    if len(out["after_run_ids"]) < min_after_runs:
        out["reasons"].append(f"after_runs<{min_after_runs}")
    if out["reasons"]:
        out["status"] = "NOT_READY"
    return out


def rate(numerator: int, denominator: int) -> Optional[float]:
    return None if not denominator else numerator / denominator


def evaluate_hotel(bf_before, bf_after, un_before, un_after, th: Dict[str, Any] = THRESHOLDS) -> Dict[str, Any]:
    """Mỗi tham số là (đếm_tử, đếm_mẫu) hoặc None (hotel không có dòng nào trong cửa sổ đó)."""
    b0, b1 = bf_before or (0, 0), bf_after or (0, 0)
    u0, u1 = un_before or (0, 0), un_after or (0, 0)
    res: Dict[str, Any] = {"breakfast": {"before": [b0[0], b0[1]], "after": [b1[0], b1[1]], "share_before": rate(*b0), "share_after": rate(*b1), "delta": None},
                           "unavailable": {"before": [u0[0], u0[1]], "after": [u1[0], u1[1]], "rate_before": rate(*u0), "rate_after": rate(*u1), "delta": None}}
    bf_ok = b0[1] >= th["min_options"] and b1[1] >= th["min_options"]
    un_ok = u0[1] >= th["min_items"] and u1[1] >= th["min_items"]
    flags = []
    if bf_ok:
        res["breakfast"]["delta"] = rate(*b1) - rate(*b0)
        if abs(res["breakfast"]["delta"]) >= th["breakfast_delta"] - 1e-12:
            flags.append("FLAG_BREAKFAST")
    if un_ok:
        res["unavailable"]["delta"] = rate(*u1) - rate(*u0)
        if res["unavailable"]["delta"] >= th["unavailable_delta"] - 1e-12:
            flags.append("FLAG_UNAVAILABLE")
    if len(flags) == 2:
        res["status"] = "FLAG_BOTH"
    elif flags:
        res["status"] = flags[0]
    elif bf_ok and un_ok:
        res["status"] = "OK"
    else:
        res["status"] = "INSUFFICIENT_SAMPLE"
    res["evaluable_breakfast"], res["evaluable_unavailable"] = bf_ok, un_ok
    return res


def overall_status(windows: Dict[str, Any], hotel_results: Dict[str, Dict[str, Any]], policy_hotels: Sequence[str],
                   policy_load: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    policy = set(policy_hotels)
    policy_warning = None if (policy_load or {"status": "ok"}).get("status") == "ok" else \
        "policy hotels not loaded: known N1 exposure is NOT separated and may appear as new flags"
    if windows["status"] != "READY":
        out = {"status": "NOT_READY", "reasons": windows["reasons"], "note": FIXED_NOTE}
        if policy_warning:
            out["policy_warning"] = policy_warning
        return out
    flagged = {h: r["status"] for h, r in hotel_results.items() if r["status"].startswith("FLAG") and h not in policy}
    policy_flagged = {h: r["status"] for h, r in hotel_results.items() if h in policy}
    evaluable = [h for h, r in hotel_results.items() if r["status"] in ("OK",) or r["status"].startswith("FLAG")]
    return {"status": "READY_FLAGS_FOUND" if flagged else "READY_NO_NEW_FLAGS", "new_flags": flagged, "policy_hotels_reported_separately": policy_flagged,
            "hotels_total": len(hotel_results), "hotels_evaluable": len(evaluable),
            "hotels_insufficient_sample": sorted(h for h, r in hotel_results.items() if r["status"] == "INSUFFICIENT_SAMPLE"), "note": FIXED_NOTE,
            **({"policy_warning": policy_warning} if policy_warning else {})}


class ReadOnlySession:
    """Mọi câu SQL phải khớp whitelist (SELECT / SET SESSION MAX_EXECUTION_TIME / START TRANSACTION READ ONLY / COMMIT); ghi lại để kiểm."""

    def __init__(self, cursor, max_ms: int):
        if isinstance(max_ms, bool) or not isinstance(max_ms, int) or not (1 <= max_ms <= MAX_MS_LIMIT):
            raise ValueError(f"max_ms phải là số nguyên trong [1, {MAX_MS_LIMIT}], nhận {max_ms!r}")   # từ chối TRƯỚC khi gọi cursor.execute
        self.cursor, self.issued = cursor, []
        self.execute(f"SET SESSION MAX_EXECUTION_TIME={int(max_ms)}")
        self.execute("START TRANSACTION READ ONLY")

    def execute(self, sql: str, params: Sequence[Any] = ()):
        if not sql_is_allowed(sql) or any(isinstance(p, bool) or not isinstance(p, int) for p in params):
            raise RuntimeError(f"câu SQL/tham số ngoài whitelist chỉ-đọc: {sql[:80]!r}")
        if sql.count("%s") != len(params):
            raise RuntimeError("số placeholder khác số tham số")
        self.issued.append(sql)
        self.cursor.execute(sql, tuple(params))

    def fetchall(self):
        return self.cursor.fetchall()

    def close(self):
        self.execute("COMMIT")


def _placeholders(n: int) -> str:
    return ",".join(["%s"] * n)


def collect(session: ReadOnlySession, run_ids: List[int], kind: str) -> Dict[str, tuple]:
    if not run_ids:
        return {}
    ph = _placeholders(len(run_ids))
    if kind == "breakfast":
        session.execute(SQL_BREAKFAST.format(ph=ph), run_ids)
    else:
        session.execute(SQL_UNAVAILABLE.format(ph=ph), run_ids)
    return {row[0]: (int(row[1] or 0), int(row[2])) for row in session.fetchall()}


def load_policy_hotels(path: Optional[Path]):
    """Trả (danh sách hotel, trạng thái nạp). KHÔNG nuốt lỗi thành [] im lặng: trạng thái được đưa vào báo cáo."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        return [], {"status": "missing_or_unreadable", "path": str(path), "error": type(exc).__name__}
    except (ValueError, TypeError) as exc:
        return [], {"status": "invalid_json", "path": str(path), "error": type(exc).__name__}
    hotels = data.get("excluded_hotels") if isinstance(data, dict) else None
    if not isinstance(hotels, list) or not all(isinstance(h, str) for h in hotels):
        return [], {"status": "no_excluded_hotels_list", "path": str(path), "error": None}
    return list(hotels), {"status": "ok", "path": str(path), "count": len(hotels)}


def run_monitor(session: ReadOnlySession, *, boundary_version: str, policy_hotels: Sequence[str], policy_load: Optional[Dict[str, Any]] = None,
                th: Dict[str, Any] = THRESHOLDS) -> Dict[str, Any]:
    session.execute(SQL_RUNS)
    runs = [{"id": r[0], "status": r[1], "started_at": r[2], "scraper_version": r[3]} for r in session.fetchall()]
    windows = select_windows(runs, boundary_version, th["before_days"], th["min_after_days"], th["min_after_runs"])
    hotels: Dict[str, Dict[str, Any]] = {}
    if windows["status"] == "READY":
        bf0, bf1 = collect(session, windows["before_run_ids"], "breakfast"), collect(session, windows["after_run_ids"], "breakfast")
        un0, un1 = collect(session, windows["before_run_ids"], "unavailable"), collect(session, windows["after_run_ids"], "unavailable")
        for h in sorted(set(bf0) | set(bf1) | set(un0) | set(un1)):
            hotels[h] = evaluate_hotel(bf0.get(h), bf1.get(h), un0.get(h), un1.get(h), th)
    return {"thresholds": th, "windows": windows, "overall": overall_status(windows, hotels, policy_hotels, policy_load), "hotels": hotels,
            "policy_hotels": list(policy_hotels), "policy_load": policy_load or {"status": "not_provided"}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default="local_primary", help="nhãn nguồn (chỉ để ghi nhãn; script đọc DB mà nó kết nối)")
    parser.add_argument("--boundary-version", default="2.3.1")
    parser.add_argument("--policy-file", default=str(DEFAULT_POLICY))
    parser.add_argument("--max-ms", type=int, default=60000, help=f"1..{MAX_MS_LIMIT}")
    parser.add_argument("--out", action="store_true", help="ghi thêm data/quality_monitor/n1_rollout_<source>_<ngày UTC>.json")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.core.database import get_db_connection  # import muộn: module thuần không chạm DB khi được test

    with get_db_connection() as conn:
        session = ReadOnlySession(conn.cursor(), args.max_ms)
        try:
            policy_hotels, policy_load = load_policy_hotels(Path(args.policy_file))
            result = run_monitor(session, boundary_version=args.boundary_version, policy_hotels=policy_hotels, policy_load=policy_load)
        finally:
            session.close()
    result["source"] = args.source
    result["generated_at_utc"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.out:
        out_dir = Path(__file__).resolve().parents[2] / "data" / "quality_monitor"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"n1_rollout_{args.source}_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d')}.json").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
