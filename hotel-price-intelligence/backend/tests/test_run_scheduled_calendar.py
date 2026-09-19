from __future__ import annotations

import hashlib
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
RUNNER = BACKEND / "scripts" / "run_scheduled_calendar.py"
CALENDAR = BACKEND.parents[1] / "outputs" / "aux-local-crawl-planner-20260901" / "aux_local_crawl_sampling_master.xlsx"
HOTELS = BACKEND.parents[1] / "link_hotel_data_expanded.xlsx"

spec = importlib.util.spec_from_file_location("scheduled_calendar", RUNNER)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def aux_args():
    return module.build_parser().parse_args([
        "--calendar", str(CALENDAR), "--hotel-file", str(HOTELS),
        "--plan-sheet", "AUX_CRAWL_PLAN", "--log-sheet", "CRAWL_LOG",
        "--checkin-headers", "AN1,AN4,AN5,AN6,AN7,AN8,AN9,AF1,AF2,AF3",
        "--environment", "local_aux", "--expected-hotels", "354", "--expected-items", "3540",
    ])


def test_defaults_preserve_local_primary():
    args = module.build_parser().parse_args(["--calendar", "a.xlsx", "--hotel-file", "b.xlsx"])
    assert args.plan_sheet == "DAILY_CRAWL_PLAN"
    assert args.log_sheet == "CRAWL_LOG"
    assert args.environment == "local_primary"
    assert args.checkin_headers == ",".join(module.DEFAULT_CHECKIN_HEADERS)


def test_aux_sheet_and_ten_checkins_are_readable():
    args = aux_args(); args.checkin_headers = args.checkin_headers.split(",")
    _, _, _, _, checkins = module.load_contract(CALENDAR, date(2026, 9, 3), args.plan_sheet, args.log_sheet, args.checkin_headers, args.environment)
    assert len(checkins) == len(set(checkins)) == 10


def test_expected_item_math():
    assert 354 * 10 == 3540


def test_validate_only_is_read_only(monkeypatch):
    args = aux_args(); args.calendar = CALENDAR; args.hotel_file = HOTELS; args.checkin_headers = args.checkin_headers.split(",")
    before = hashlib.sha256(CALENDAR.read_bytes()).hexdigest()
    monkeypatch.setattr(module, "_save", lambda *_: pytest.fail("validate-only attempted workbook write"))
    checkins, links = module.validate(args, date(2026, 9, 3))
    assert (len(checkins), len(links)) == (10, 354)
    assert hashlib.sha256(CALENDAR.read_bytes()).hexdigest() == before


def test_real_flow_logs_local_aux(monkeypatch):
    class Cell:
        value = None
    class Log:
        def __init__(self): self.cells = {}
        def cell(self, row, col): return self.cells.setdefault((row, col), Cell())
    log = Log(); headers = {"Environment": 1, "Status": 2}
    module._write(log, headers, 4, {"Environment": "local_aux", "Status": "Đang chạy"})
    assert log.cell(4, 1).value == "local_aux"


def test_ensure_worker_launches_a_run_scoped_worker(monkeypatch, tmp_path):
    calls = []

    class Queue:
        def __init__(self): self.health_calls = 0
        def worker_health(self, worker_id_prefix=None):
            self.health_calls += 1
            if self.health_calls == 1:
                return {"online": False}
            return {
                "online": True,
                "worker_id": f"{worker_id_prefix}abc123",
                "process_id": 4321,
            }
        def mark_worker_offline(self, worker_id):
            pytest.fail(f"unexpected offline mark for {worker_id}")

    class Process:
        pid = 1234
        def poll(self): return None

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return Process()

    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(module, "_pid_exists", lambda pid: pid == 4321)

    assert module._ensure_worker(Queue(), tmp_path, date(2026, 9, 15), 14) is True
    command, kwargs = calls[0]
    assert command[0] == sys.executable
    assert command[-4:] == ["--run-id", "14", "--worker-id", command[-1]]
    assert command[-1].startswith("scheduled-run-14-")
    assert kwargs["cwd"] == tmp_path


def test_crawl_worker_claim_is_scoped_to_run():
    from app.scraper.worker import CrawlWorker

    seen = {}

    class Queue:
        def claim_next_item(self, worker_id, run_id=None):
            seen.update(worker_id=worker_id, run_id=run_id)
            return None

    worker = CrawlWorker.__new__(CrawlWorker)
    worker.worker_id = "scheduled-run-14-test"
    worker.run_id = 14
    worker.queue = Queue()

    assert worker._claim_next_item() is None
    assert seen == {"worker_id": "scheduled-run-14-test", "run_id": 14}
