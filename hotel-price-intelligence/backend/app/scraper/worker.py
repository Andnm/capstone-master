"""Single durable worker; không tự tạo scheduled run."""
import os
import socket
import time
import uuid
from datetime import timedelta
from pathlib import Path

from app.core.config import settings
from app.database.durable import DurableQueueRepository
from app.scraper.booking_scraper import DeadLinkConfirmation, confirm_dead_link, scrape_booking_hotel
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
        self._touch_watchdog_heartbeat()

    @staticmethod
    def _touch_watchdog_heartbeat():
        """Signal the external supervisor without coupling it to DB process IDs.

        Windows venv launchers may have a different PID from the Python process
        recorded in ``crawler_workers``. A per-child heartbeat file lets the
        supervisor reliably detect both a dead process and a Selenium call that
        has stopped returning.
        """
        heartbeat_path = os.environ.get("WORKER_WATCHDOG_HEARTBEAT_FILE")
        if not heartbeat_path:
            return
        try:
            Path(heartbeat_path).touch()
        except OSError:
            # DB heartbeat remains the source of truth shown to operators. A
            # temporary filesystem error must not crash the crawler.
            pass

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
        self._touch_watchdog_heartbeat()

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

    def _handle_dead_link_confirmation(self, item, confirmation, item_started):
        """Chỉ được gọi sau khi lần cào chính (driver batch, có checkin/checkout) gặp DEAD_LINK.
        Không bao giờ cascade sibling ở đây - chỉ record_confirmed_dead_link() (verdict="confirmed")
        mới được phép cascade, đúng behavior contract đã thống nhất với GPT."""
        item_total_ms = round((time.perf_counter() - item_started) * 1000)

        if confirmation.verdict == "confirmed":
            self.queue.record_confirmed_dead_link(item, confirmation.evidence, item_total_ms=item_total_ms)
            return ErrorCode.DEAD_LINK

        if confirmation.verdict == "not_bookable":
            hotel = build_hotel_upsert(
                {"hotel_name": item.get("hotel_name_hint") or ""},
                item["source_hotel_link"],
                item.get("market_hint"),
            )
            if hotel:
                self.queue.persist_success(
                    item=item, hotel=hotel, records=[],
                    diagnostics={"final_url": confirmation.evidence.get("probe_final_url")},
                    timings={}, artifacts={}, is_sold_out=False, is_not_bookable=True,
                    booking_status_reason=confirmation.not_bookable_message,
                    dead_link_confirmation=confirmation.evidence,
                )
                return None
            # Slug khong suy duoc tu URL nguon (rat hiem, URL sai dinh dang) - khong the upsert
            # hotels, roi ve nhanh inconclusive de tu retry thay vi mat item.
            confirmation = DeadLinkConfirmation("inconclusive", confirmation.evidence)

        if confirmation.verdict == "not_confirmed":
            self.queue.record_failure(
                item,
                failure(
                    ErrorCode.PROPERTY_REDIRECT_UNCONFIRMED,
                    "Probe lan 2 tai duoc trang property that - lan redirect dau la fluke, thu lai",
                    True,
                ),
                meta={"final_url": confirmation.evidence.get("first_final_url")},
                item_total_ms=item_total_ms,
                dead_link_confirmation=confirmation.evidence,
            )
            return ErrorCode.PROPERTY_REDIRECT_UNCONFIRMED

        # inconclusive: giu nguyen taxonomy that cua probe (NETWORK_TIMEOUT/CAPTCHA/BLOCKED/
        # DRIVER_INIT) neu co, de khong pha vo network circuit-breaker/retry semantics hien co.
        probe_failure = confirmation.scrape_failure
        if probe_failure and probe_failure.code == ErrorCode.NETWORK_TIMEOUT:
            self._close_driver()
            self.queue.defer_network_failure(
                item, probe_failure,
                meta={"final_url": confirmation.evidence.get("first_final_url")},
                item_total_ms=item_total_ms,
                dead_link_confirmation=confirmation.evidence,
            )
            return ErrorCode.NETWORK_TIMEOUT
        final_failure = probe_failure or failure(
            ErrorCode.DEAD_LINK_INCONCLUSIVE,
            "Probe lan 2 khong du tin hieu ket luan - se thu lai, khong cascade",
            True,
        )
        self.queue.record_failure(
            item, final_failure,
            meta={"final_url": confirmation.evidence.get("first_final_url")},
            item_total_ms=item_total_ms,
            dead_link_confirmation=confirmation.evidence,
        )
        return final_failure.code

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
            if scrape_failure.code == ErrorCode.DEAD_LINK:
                # Nghi van lan 1 - KHONG duoc coi la du de cascade sibling. Probe lan 2 bang driver
                # rieng, canonical URL, khong checkin/checkout truoc khi ket luan bat cu dieu gi.
                confirmation = confirm_dead_link(
                    item["source_hotel_link"],
                    item.get("requested_hotel_link"),
                    meta.get("final_url"),
                    heartbeat=lambda: self._heartbeat(item["id"]),
                )
                return self._handle_dead_link_confirmation(item, confirmation, item_started)
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
                # claim_next_item() không claim gì được nếu có 1 item khác đang kẹt ở
                # 'running' quá lease (vd. worker cũ chết/restart giữa chừng). recover_stale_items()
                # lúc đầu run_forever() chỉ chạy 1 lần nên không bắt được item kẹt SAU thời điểm đó -
                # gọi lại mỗi khi rảnh (claim_next_item trả None) để tự gỡ, không cần restart worker.
                self.queue.recover_stale_items()
                time.sleep(settings.WORKER_POLL_SECONDS)
        except KeyboardInterrupt:
            pass
        finally:
            self._close_driver()
            self.queue.mark_worker_offline(self.worker_id)
