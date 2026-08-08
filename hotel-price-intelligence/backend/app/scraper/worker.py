"""Single durable worker; không tự tạo scheduled run."""
import os
import socket
import time
import uuid

from app.core.config import settings
from app.database.durable import DurableQueueRepository
from app.scraper.booking_scraper import scrape_booking_hotel
from app.scraper.data_contract import utc_now_naive
from app.scraper.driver import get_driver
from app.scraper.errors import ErrorCode, failure
from app.scraper.transform import build_hotel_upsert, build_price_observations


class CrawlWorker:
    def __init__(self, worker_id: str | None = None):
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.queue = DurableQueueRepository()
        self.driver = None
        self.driver_items = 0
        self.driver_start_ms = 0

    def _heartbeat(self, item_id=None):
        self.queue.heartbeat_item(self.worker_id, item_id)

    def _ensure_driver(self):
        if self.driver is None or self.driver_items >= settings.DRIVER_BATCH_SIZE:
            self._close_driver()
            started = time.perf_counter()
            self.driver = get_driver(is_headless=True)
            self.driver_start_ms = round((time.perf_counter() - started) * 1000)
            self.driver_items = 0
        else:
            self.driver_start_ms = 0
        return self.driver

    def _close_driver(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                try:
                    self.driver.service.process.kill()
                except Exception:
                    pass
        self.driver = None
        self.driver_items = 0

    def process_item(self, item):
        item_started = time.perf_counter()
        self._heartbeat(item["id"])
        try:
            driver = self._ensure_driver()
        except Exception as exc:
            self.queue.record_failure(item, failure(ErrorCode.DRIVER_INIT, str(exc)))
            return

        result, scrape_failure, meta = scrape_booking_hotel(
            item["source_hotel_link"],
            str(item["checkin_date"]),
            str(item["checkout_date"]),
            driver=driver,
            save_artifact=bool(item.get("save_artifacts")),
            artifact_root=settings.ARTIFACT_DIR,
            run_id=item["crawl_run_id"],
            item_id=item["id"],
            heartbeat=lambda: self._heartbeat(item["id"]),
        )
        self.driver_items += 1
        meta["driver_start_ms"] = self.driver_start_ms
        if scrape_failure:
            if scrape_failure.code in (ErrorCode.DRIVER_INIT, ErrorCode.NETWORK_TIMEOUT):
                self._close_driver()
            self.queue.record_failure(
                item,
                scrape_failure,
                meta=meta,
                item_total_ms=round((time.perf_counter() - item_started) * 1000),
            )
            return

        hotel = build_hotel_upsert(result, item["source_hotel_link"], item.get("market_hint"))
        if not hotel:
            self.queue.record_failure(
                item,
                failure(ErrorCode.PARSER_EMPTY, "Không xác định được hotel_id từ URL nguồn", False),
                meta=meta,
                item_total_ms=round((time.perf_counter() - item_started) * 1000),
            )
            return
        observed_at = utc_now_naive()
        records = build_price_observations(
            result, hotel["hotel_id"], item["crawl_run_id"], item["trigger_type"],
            observed_at, str(item["checkin_date"]), str(item["checkout_date"]), item["id"],
        )
        timings = {
            "driver_start_ms": meta.get("driver_start_ms", 0),
            "page_load_ms": meta.get("page_load_ms", 0),
            "availability_wait_ms": meta.get("availability_wait_ms", 0),
            "parse_ms": meta.get("parse_ms", 0),
            "db_write_ms": 0,
            "item_total_ms": 0,
        }
        diagnostics = result.get("diagnostics") or {}
        diagnostics["final_url"] = meta.get("final_url")
        artifacts = {
            "artifact_html_path": meta.get("artifact_html_path"),
            "screenshot_path": meta.get("screenshot_path"),
        }
        db_started = time.perf_counter()
        try:
            self.queue.persist_success(
                item=item, hotel=hotel, records=records, diagnostics=diagnostics,
                timings=timings, artifacts=artifacts, is_sold_out=bool(result.get("is_sold_out")),
            )
        except Exception as exc:
            self.queue.record_failure(
                item,
                failure(ErrorCode.DB_ERROR, str(exc)),
                meta=meta,
                item_total_ms=round((time.perf_counter() - item_started) * 1000),
            )
            return
        db_write_ms = round((time.perf_counter() - db_started) * 1000)
        item_total_ms = round((time.perf_counter() - item_started) * 1000)
        self.queue.update_item_timings(item["id"], db_write_ms, item_total_ms)

    def run_until_empty(self):
        self.queue.recover_stale_items()
        self._heartbeat(None)
        while True:
            item = self.queue.claim_next_item(self.worker_id)
            if not item:
                break
            self.process_item(item)
        self._close_driver()
        self._heartbeat(None)

    def run_forever(self):
        self.queue.recover_stale_items()
        try:
            while True:
                self._heartbeat(None)
                item = self.queue.claim_next_item(self.worker_id)
                if item:
                    self.process_item(item)
                    continue
                time.sleep(settings.WORKER_POLL_SECONDS)
        except KeyboardInterrupt:
            pass
        finally:
            self._close_driver()
            self.queue.mark_worker_offline(self.worker_id)
