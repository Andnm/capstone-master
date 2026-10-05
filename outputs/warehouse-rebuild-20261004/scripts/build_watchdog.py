"""Watchdog fail-closed cho build dai (GPT file 22). CHI DOC; KHONG kill build, chi canh bao + thoat.

    venv/Scripts/python.exe ../../outputs/warehouse-rebuild-20261004/scripts/build_watchdog.py --log <build log> --alert <file> [--interval 30] [--stall-minutes 20]

Moi chu ky: (1) canary bang that `hotel_price_intel.crawl_runs` (SELECT id ... LIMIT 1) - tai wedge 05/10, `SELECT 1` van chay nhung truy van
cham bang thi treo, nen KHONG dung `SELECT 1` lam tin hieu song; (2) `SHOW GLOBAL STATUS` cac bo dem InnoDB. Moi truy van chay trong thread, qua 20 s coi
la that bai. Canh bao khi:
  - 3 chu ky lien tiep that bai (MySQL wedge), hoac
  - tong delta Innodb_rows_read + rows_inserted + rows_updated + data_written ~ 0 lien tuc `--stall-minutes` phut (khong con hoat dong phia MySQL;
    KHONG dung CPU cua process python vi cau SQL dai - vd references 425 s - khien CPU python ~ 0 ca khi binh thuong).
Thoat binh thuong khi log build co dong `exit=`.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "hotel-price-intelligence" / "backend"
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
import mysql.connector  # noqa: E402
from app.core.config import settings  # noqa: E402

COUNTERS = ("Innodb_rows_read", "Innodb_rows_inserted", "Innodb_rows_updated", "Innodb_data_written")
t0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')} +{time.time() - t0:7.0f}s] {msg}", flush=True)


def probe(result: dict) -> None:
    try:
        cn = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
                                     password=settings.DB_PASSWORD, connection_timeout=8)
        cur = cn.cursor()
        cur.execute("SELECT id FROM hotel_price_intel.crawl_runs ORDER BY id DESC LIMIT 1")
        cur.fetchall()
        cur.execute("SHOW GLOBAL STATUS WHERE Variable_name IN (%s)" % ",".join(f"'{c}'" for c in COUNTERS))
        result["counters"] = {name: int(value) for name, value in cur.fetchall()}
        cur.close()
        cn.close()
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001
        result["err"] = f"{type(exc).__name__}: {str(exc)[:120]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--alert", required=True, type=Path)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--stall-minutes", type=float, default=20.0)
    args = parser.parse_args()
    args.alert.unlink(missing_ok=True)
    failures = 0
    prev = None
    still_since = None
    beat = 0
    while True:
        if args.log.exists() and "exit=" in args.log.read_text(encoding="utf-8", errors="ignore"):
            log("build log co `exit=` - watchdog ket thuc binh thuong.")
            return 0
        result: dict = {}
        thread = threading.Thread(target=probe, args=(result,), daemon=True)
        thread.start()
        thread.join(20)
        if not result.get("ok"):
            failures += 1
            log(f"probe THAT BAI lan {failures}/3: {result.get('err', 'qua 20 s khong tra loi (treo)')}")
            if failures >= 3:
                args.alert.write_text(f"WEDGE {time.strftime('%Y-%m-%d %H:%M:%S')}: 3 chu ky lien tiep canary bang that/SHOW STATUS that bai.\n",
                                      encoding="utf-8")
                log("CANH BAO WEDGE - ghi alert va thoat (khong kill build).")
                return 3
        else:
            failures = 0
            counters = result["counters"]
            if prev is not None:
                delta = sum(counters[c] - prev[c] for c in COUNTERS)
                if delta <= 0:
                    still_since = still_since or time.time()
                else:
                    still_since = None
                if still_since and (time.time() - still_since) / 60 >= args.stall_minutes:
                    args.alert.write_text(f"STALL {time.strftime('%Y-%m-%d %H:%M:%S')}: counters InnoDB khong doi {args.stall_minutes} phut.\n",
                                          encoding="utf-8")
                    log("CANH BAO STALL - ghi alert va thoat (khong kill build).")
                    return 4
            prev = counters
            beat += 1
            if beat % 4 == 1:  # ~2 phut/lan
                log(f"song; rows_read={counters['Innodb_rows_read']:,} rows_ins={counters['Innodb_rows_inserted']:,} "
                    f"rows_upd={counters['Innodb_rows_updated']:,} data_written={counters['Innodb_data_written'] / 1e9:.2f} GB")
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
