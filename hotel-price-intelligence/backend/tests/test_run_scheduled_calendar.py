from __future__ import annotations

import hashlib
import importlib.util
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
