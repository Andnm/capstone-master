"""Single durable worker; không tự tạo scheduled run."""
import os
import socket
import time
import uuid
from datetime import timedelta

from app.core.config import settings
from app.database.durable import DurableQueueRepository
from app.scraper.booking_scraper import scrape_booking_hotel
from app.scraper.data_contract import utc_now_naive
from app.scraper.driver import get_driver
from app.scraper.errors import ErrorCode, failure
from app.scraper.network import NetworkCircuitBreaker, booking_network_reachable
from app.scraper.transform import build_hotel_upsert, build_price_observations


class CrawlWorker:
    def __init__(self, worker_id: str | None = None):
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.queue = DurableQueueRepository()
        self.driver = None
        self.driver_items = 0
        self.driver_start_ms = 0
        self.network_breaker = NetworkCircuitBreaker(
            failure_threshold=settings.NETWORK_FAILURE_THRESHOLD,
            backoff_seconds=tuple(settings.network_probe_backoff_list),
            recovery_successes_required=settings.NETWORK_RECOVERY_SUCCESSES,
            recovery_confirm_seconds=settings.NETWORK_RECOVERY_CONFIRM_SECONDS,
        )

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

    def _network_wait_heartbeat(self, paused_at, next_probe_at, reason):
        self.queue.heartbeat_network_wait(
            self.worker_id,
            reason=reason,
            paused_at=paused_at,
            next_probe_at=next_probe_at,
            failure_count=self.network_breaker.consecutive_failures,
        )

    def _sleep_while_waiting_for_network(self, seconds, paused_at, next_probe_at, reason):
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(10, remaining))
            self._network_wait_heartbeat(paused_at, next_probe_at, reason)

    def _wait_until_network_recovers(self):
        self._close_driver()
        paused_at = utc_now_naive()
        while self.network_breaker.is_open:
            delay = self.network_breaker.next_probe_delay()
            next_probe_at = utc_now_naive() + timedelta(seconds=delay)
            if self.network_breaker.consecutive_probe_successes:
                reason = (
                    "Đã kết nối lại Booking; đang xác nhận lần thứ hai trước khi tiếp tục"
                )
            else:
                reason = f"Mất kết nối Booking; tự kiểm tra lại sau {delay} giây"
            self._network_wait_heartbeat(paused_at, next_probe_at, reason)
            self._sleep_while_waiting_for_network(
                delay, paused_at, next_probe_at, reason,
            )
            reachable = booking_network_reachable(settings.NETWORK_PROBE_TIMEOUT_SECONDS)
            if self.network_breaker.record_probe_result(reachable):
                self._heartbeat(None)
                return

    def _handle_item_outcome(self, outcome):
        if outcome == ErrorCode.NETWORK_TIMEOUT:
            if self.network_breaker.record_network_failure():
                self._wait_until_network_recovers()
            return
        self.network_breaker.record_non_network_result()

    def process_item(self, item):
        item_started = time.perf_counter()
        self._heartbeat(item["id"])
        try:
            driver = self._ensure_driver()
        except Exception as exc:
            self.queue.record_failure(item, failure(ErrorCode.DRIVER_INIT, str(exc)))
            return ErrorCode.DRIVER_INIT

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
            if scrape_failure.code == ErrorCode.NETWORK_TIMEOUT:
                self.queue.defer_network_failure(
                    item,
                    scrape_failure,
                    meta=meta,
                    item_total_ms=round((time.perf_counter() - item_started) * 1000),
                )
                return ErrorCode.NETWORK_TIMEOUT
            self.queue.record_failure(
                item,
                scrape_failure,
                meta=meta,
                item_total_ms=round((time.perf_counter() - item_started) * 1000),
            )
            return scrape_failure.code

        hotel = build_hotel_upsert(result, item["source_hotel_link"], item.get("market_hint"))
        if not hotel:
            self.queue.record_failure(
                item,
                failure(ErrorCode.PARSER_EMPTY, "Không xác định được hotel_id từ URL nguồn", False),
                meta=meta,
                item_total_ms=round((time.perf_counter() - item_started) * 1000),
            )
            return ErrorCode.PARSER_EMPTY
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
                is_not_bookable=bool(result.get("is_not_bookable")),
                booking_status_reason=result.get("booking_status_reason"),
            )
        except Exception as exc:
            self.queue.record_failure(
                item,
                failure(ErrorCode.DB_ERROR, str(exc)),
                meta=meta,
                item_total_ms=round((time.perf_counter() - item_started) * 1000),
            )
            return ErrorCode.DB_ERROR
        db_write_ms = round((time.perf_counter() - db_started) * 1000)
        item_total_ms = round((time.perf_counter() - item_started) * 1000)
        self.queue.update_item_timings(item["id"], db_write_ms, item_total_ms)
        return None

    def run_until_empty(self):
        self.queue.recover_stale_items()
        self._heartbeat(None)
        while True:
            item = self.queue.claim_next_item(self.worker_id)
            if not item:
                break
            self._handle_item_outcome(self.process_item(item))
        self._close_driver()
        self._heartbeat(None)

    def run_forever(self):
        self.queue.recover_stale_items()
        try:
            while True:
                self._heartbeat(None)
                item = self.queue.claim_next_item(self.worker_id)
                if item:
                    self._handle_item_outcome(self.process_item(item))
                    continue
                time.sleep(settings.WORKER_POLL_SECONDS)
        except KeyboardInterrupt:
            pass
        finally:
            self._close_driver()
            self.queue.mark_worker_offline(self.worker_id)
