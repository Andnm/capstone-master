import os
from pathlib import Path

from app.core.config import settings
from app.scraper.worker import CrawlWorker
from app.scraper.worker_supervisor import heartbeat_file_is_stale


class _FakeDriver:
    def __init__(self):
        self.quit_calls = 0

    def quit(self):
        self.quit_calls += 1


def test_driver_is_recycled_after_500_items(monkeypatch):
    old_driver = _FakeDriver()
    new_driver = _FakeDriver()
    worker = object.__new__(CrawlWorker)
    worker.driver = old_driver
    worker.driver_items = 499
    worker.driver_start_ms = 0

    monkeypatch.setattr(settings, "DRIVER_BATCH_SIZE", 500)
    monkeypatch.setattr("app.scraper.worker.get_driver", lambda **kwargs: new_driver)

    assert worker._ensure_driver() is old_driver
    assert old_driver.quit_calls == 0

    worker.driver_items = 500
    assert worker._ensure_driver() is new_driver
    assert old_driver.quit_calls == 1
    assert worker.driver_items == 0


def test_watchdog_heartbeat_file_is_touched(tmp_path, monkeypatch):
    heartbeat_path = tmp_path / "worker.heartbeat"
    monkeypatch.setenv("WORKER_WATCHDOG_HEARTBEAT_FILE", str(heartbeat_path))

    CrawlWorker._touch_watchdog_heartbeat()

    assert heartbeat_path.exists()


def test_missing_heartbeat_becomes_stale_after_timeout(tmp_path):
    heartbeat_path = tmp_path / "missing.heartbeat"

    assert heartbeat_file_is_stale(
        heartbeat_path,
        child_started_at=100.0,
        now=399.0,
        timeout_seconds=300,
    ) is False
    assert heartbeat_file_is_stale(
        heartbeat_path,
        child_started_at=100.0,
        now=401.0,
        timeout_seconds=300,
    ) is True


def test_fresh_heartbeat_extends_watchdog_deadline(tmp_path):
    heartbeat_path = tmp_path / "worker.heartbeat"
    heartbeat_path.touch()
    os.utime(heartbeat_path, (350.0, 350.0))

    assert heartbeat_file_is_stale(
        heartbeat_path,
        child_started_at=100.0,
        now=649.0,
        timeout_seconds=300,
    ) is False
    assert heartbeat_file_is_stale(
        heartbeat_path,
        child_started_at=100.0,
        now=651.0,
        timeout_seconds=300,
    ) is True
