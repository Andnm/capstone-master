"""Watchdog dinh ky: kiem tra heartbeat cua run_worker.py, tu khoi dong lai neu da
chet han va van con item cho cao. Chay boi Task Scheduler moi 15 phut - tach biet voi
task "At log on" (chi ban 1 lan luc dang nhap/reboot).

Nguong STALE_THRESHOLD_SECONDS co chu dich lon hon nhieu so voi vong tu phuc hoi noi bo
cua supervisor (300 giay giua supervisor va child, xem CLAUDE.md muc 4.6) - tranh bao
dong gia va tu y khoi dong 1 supervisor thu 2 trong luc cai dau tien dang tu xu ly binh
thuong.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import get_db_connection  # noqa: E402
from app.database.durable import DurableQueueRepository  # noqa: E402

STALE_THRESHOLD_SECONDS = 900
BACKEND_ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = BACKEND_ROOT / "crawl_artifacts" / "watchdog_worker.log"


def _log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def _has_pending_work() -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) FROM crawl_run_items cri
            JOIN crawl_runs cr ON cr.id = cri.crawl_run_id
            WHERE cri.status = 'queued' AND cr.status IN ('queued','running')
            """
        )
        count = int(cursor.fetchone()[0] or 0)
        cursor.close()
        return count > 0


def main() -> int:
    try:
        queue_repo = DurableQueueRepository()
        health = queue_repo.worker_health()
        age = health.get("heartbeat_age_seconds")

        if age is None or age > STALE_THRESHOLD_SECONDS:
            if not _has_pending_work():
                _log(f"Heartbeat cu ({age}s) nhung khong con item queued - khong lam gi.")
                return 0
            _log(f"Heartbeat cu ({age}s), van con item queued - tu khoi dong lai run_worker.py.")
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            subprocess.Popen(
                [
                    str(BACKEND_ROOT / "venv" / "Scripts" / "python.exe"),
                    str(BACKEND_ROOT / "scripts" / "run_worker.py"),
                ],
                cwd=str(BACKEND_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        else:
            _log(f"Heartbeat binh thuong ({age}s) - khong can lam gi.")
        return 0
    except Exception as exc:  # noqa: BLE001
        _log(f"Watchdog gap loi: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
