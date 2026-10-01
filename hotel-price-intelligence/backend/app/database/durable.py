"""Durable MySQL queue và persistence theo crawl_run_item.

API chỉ tạo run/items. Một worker độc lập claim từng item, heartbeat, ghi toàn bộ dữ liệu
trong transaction và có thể reclaim item stale mà không tạo observation trùng.
"""
import hashlib
import json
import os
import socket
from datetime import timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.database import get_db_connection
from app.scraper.data_contract import current_git_commit, utc_now_naive
from app.scraper.errors import ErrorCode, ScrapeFailure
from app.scraper.reference import is_reference_candidate_eligible, select_best_match
from app.scraper.url_utils import build_scrape_url, clean_hotel_link, extract_hotel_slug

_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _vn_date(naive_utc_dt):
    """utc_now_naive() tra ve naive datetime nhung LA UTC (xem app.core.database time_zone='+00:00')
    - phai gan tzinfo=UTC truoc khi doi sang gio VN, khong duoc doi thang tu naive datetime."""
    return naive_utc_dt.replace(tzinfo=timezone.utc).astimezone(_VN_TZ).date()


TERMINAL_ITEM_STATUSES = ("success", "partial", "sold_out", "not_bookable", "error")


def _source_hash(link: str) -> str:
    return hashlib.sha256(clean_hotel_link(link).lower().encode("utf-8")).hexdigest()


class DurableQueueRepository:
    def create_run_with_items(
        self,
        *,
        trigger_type: str,
        source_file: Optional[str],
        source_original_filename: Optional[str],
        source_file_sha256: Optional[str],
        source_file_size: Optional[int],
        date_mode: str,
        checkin_dates: List[str],
        hotel_links: Iterable[tuple],
        crawl_context: Dict[str, Any],
        save_artifacts: bool,
        scraper_version: str,
        selector_version: str,
        git_commit: Optional[str],
        retry_of_run_id: Optional[int] = None,
    ) -> int:
        links = list(hotel_links)
        total = len(links) * len(checkin_dates)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO crawl_runs (
                      status, trigger_type, source_file, source_original_filename,
                      source_file_sha256, source_file_size, save_artifacts, crawl_context,
                      scraper_version, selector_version, git_commit, storage_timezone,
                      retry_of_run_id, date_mode, checkin_dates, total
                    ) VALUES ('queued', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              'UTC', %s, %s, %s, %s)
                    """,
                    (
                        trigger_type, source_file, source_original_filename, source_file_sha256,
                        source_file_size, save_artifacts,
                        json.dumps(crawl_context, ensure_ascii=False), scraper_version,
                        selector_version, git_commit, retry_of_run_id, date_mode,
                        json.dumps(checkin_dates), total,
                    ),
                )
                run_id = cursor.lastrowid
                rows = []
                for source_link, name_hint, market_hint in links:
                    source_hash = _source_hash(source_link)
                    for checkin in checkin_dates:
                        checkout = (
                            __import__("datetime").datetime.strptime(checkin, "%Y-%m-%d").date()
                            + timedelta(days=1)
                        ).isoformat()
                        rows.append((
                            run_id, source_link, source_hash,
                            build_scrape_url(source_link, checkin, checkout),
                            build_scrape_url(source_link, checkin, checkout),
                            name_hint, market_hint, checkin, checkout,
                        ))
                cursor.executemany(
                    """
                    INSERT INTO crawl_run_items (
                      crawl_run_id, source_hotel_link, source_link_hash, requested_hotel_link, hotel_link,
                      hotel_name_hint, market_hint, checkin_date, checkout_date, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued')
                    """,
                    rows,
                )
                conn.commit()
                return run_id
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def create_retry_run(self, source_run_id: int) -> Optional[int]:
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute("SELECT * FROM crawl_runs WHERE id = %s", (source_run_id,))
                run = cursor.fetchone()
                if not run:
                    return None
                cursor.execute(
                    """
                    SELECT source_hotel_link,source_link_hash,requested_hotel_link,hotel_link,hotel_name_hint,
                           market_hint,checkin_date,checkout_date
                    FROM crawl_run_items
                    WHERE crawl_run_id = %s AND status IN ('error','partial')
                    ORDER BY id
                    """,
                    (source_run_id,),
                )
                failed = cursor.fetchall()
                if not failed:
                    return 0
                checkin_dates = sorted({str(item["checkin_date"]) for item in failed})
                cursor.execute(
                    """
                    INSERT INTO crawl_runs (
                      status,trigger_type,source_file,source_original_filename,source_file_sha256,
                      source_file_size,save_artifacts,crawl_context,scraper_version,selector_version,
                      git_commit,storage_timezone,retry_of_run_id,date_mode,checkin_dates,total
                    ) VALUES ('queued','manual',%s,%s,%s,%s,%s,%s,%s,%s,%s,'UTC',%s,
                              'explicit',%s,%s)
                    """,
                    (
                        run.get("source_file"), run.get("source_original_filename"),
                        run.get("source_file_sha256"), run.get("source_file_size"),
                        run.get("save_artifacts"), run.get("crawl_context"), settings.SCRAPER_VERSION,
                        settings.SELECTOR_VERSION, current_git_commit(), source_run_id,
                        json.dumps(checkin_dates), len(failed),
                    ),
                )
                retry_run_id = cursor.lastrowid
                cursor.executemany(
                    """
                    INSERT INTO crawl_run_items (
                      crawl_run_id,source_hotel_link,source_link_hash,requested_hotel_link,hotel_link,hotel_name_hint,
                      market_hint,checkin_date,checkout_date,status
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued')
                    """,
                    [(
                        retry_run_id, item["source_hotel_link"], item["source_link_hash"],
                        build_scrape_url(
                            item["source_hotel_link"],
                            str(item["checkin_date"]),
                            str(item["checkout_date"]),
                        ),
                        build_scrape_url(
                            item["source_hotel_link"],
                            str(item["checkin_date"]),
                            str(item["checkout_date"]),
                        ),
                        item["hotel_name_hint"], item["market_hint"],
                        item["checkin_date"], item["checkout_date"],
                    ) for item in failed],
                )
                conn.commit()
                return retry_run_id
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def recover_stale_items(self) -> int:
        now = utc_now_naive()
        cutoff = now - timedelta(seconds=settings.WORKER_LEASE_SECONDS)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT DISTINCT crawl_run_id FROM crawl_run_items WHERE status='running' AND heartbeat_at < %s",
                    (cutoff,),
                )
                affected_run_ids = [row[0] for row in cursor.fetchall()]
                cursor.execute(
                    """
                    UPDATE crawl_run_items
                    SET status = CASE WHEN attempt_count >= %s THEN 'error' ELSE 'queued' END,
                        last_error_code = CASE WHEN attempt_count >= %s THEN 'worker_lease_expired' ELSE last_error_code END,
                        error_message = CASE WHEN attempt_count >= %s THEN 'Worker dừng quá lease và đã hết số lần thử' ELSE error_message END,
                        finished_at = CASE WHEN attempt_count >= %s THEN %s ELSE NULL END,
                        worker_id = NULL, claimed_at = NULL, heartbeat_at = NULL,
                        next_retry_at = CASE WHEN attempt_count >= %s THEN NULL ELSE %s END
                    WHERE status = 'running' AND heartbeat_at < %s
                    """,
                    (
                        settings.WORKER_MAX_ATTEMPTS, settings.WORKER_MAX_ATTEMPTS,
                        settings.WORKER_MAX_ATTEMPTS, settings.WORKER_MAX_ATTEMPTS, now,
                        settings.WORKER_MAX_ATTEMPTS, now, cutoff,
                    ),
                )
                count = cursor.rowcount
                conn.commit()
                for run_id in affected_run_ids:
                    self.recompute_run(run_id)
                return count
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def claim_next_item(self, worker_id: str) -> Optional[Dict[str, Any]]:
        now = utc_now_naive()
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute("SELECT id FROM crawl_run_items WHERE status = 'running' LIMIT 1 FOR UPDATE")
                if cursor.fetchone():
                    conn.rollback()
                    return None
                cursor.execute(
                    """
                    SELECT cri.id
                    FROM crawl_run_items cri
                    JOIN crawl_runs cr ON cr.id = cri.crawl_run_id
                    WHERE cri.status = 'queued'
                      AND (cri.next_retry_at IS NULL OR cri.next_retry_at <= %s)
                      AND cr.status IN ('queued','running')
                    ORDER BY cr.created_at, cri.id
                    LIMIT 1 FOR UPDATE
                    """,
                    (now,),
                )
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return None
                item_id = row["id"]
                cursor.execute(
                    """
                    UPDATE crawl_run_items
                    SET status='running', attempt_count=attempt_count+1, claimed_at=%s,
                        heartbeat_at=%s, worker_id=%s, next_retry_at=NULL
                    WHERE id=%s
                    """,
                    (now, now, worker_id, item_id),
                )
                cursor.execute(
                    """
                    UPDATE crawl_runs cr
                    JOIN crawl_run_items cri ON cri.crawl_run_id = cr.id
                    SET cr.status='running', cr.started_at=COALESCE(cr.started_at, %s)
                    WHERE cri.id=%s
                    """,
                    (now, item_id),
                )
                conn.commit()
                cursor.execute(
                    """
                    SELECT cri.*, cr.trigger_type, cr.save_artifacts, cr.crawl_context,
                           cr.scraper_version, cr.selector_version
                    FROM crawl_run_items cri JOIN crawl_runs cr ON cr.id=cri.crawl_run_id
                    WHERE cri.id=%s
                    """,
                    (item_id,),
                )
                item = cursor.fetchone()
                if isinstance(item.get("crawl_context"), str):
                    item["crawl_context"] = json.loads(item["crawl_context"])
                return item
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def heartbeat_item(self, worker_id: str, item_id: Optional[int]) -> None:
        now = utc_now_naive()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO crawler_workers
                      (worker_id,status,started_at,heartbeat_at,current_item_id,scraper_version,host_name,process_id)
                    VALUES (%s,'online',%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE status='online',heartbeat_at=VALUES(heartbeat_at),
                      current_item_id=VALUES(current_item_id),scraper_version=VALUES(scraper_version),
                      process_id=VALUES(process_id),status_reason=NULL,paused_at=NULL,
                      next_probe_at=NULL,network_failure_count=0
                    """,
                    (worker_id, now, now, item_id, settings.SCRAPER_VERSION, socket.gethostname(), os.getpid()),
                )
                if item_id:
                    cursor.execute(
                        "UPDATE crawl_run_items SET heartbeat_at=%s WHERE id=%s AND worker_id=%s",
                        (now, item_id, worker_id),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def heartbeat_network_wait(
        self,
        worker_id: str,
        *,
        reason: str,
        paused_at,
        next_probe_at,
        failure_count: int,
    ) -> None:
        now = utc_now_naive()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO crawler_workers (
                      worker_id,status,started_at,heartbeat_at,current_item_id,scraper_version,
                      host_name,process_id,status_reason,paused_at,next_probe_at,network_failure_count
                    ) VALUES (%s,'waiting_network',%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE status='waiting_network',heartbeat_at=VALUES(heartbeat_at),
                      current_item_id=NULL,scraper_version=VALUES(scraper_version),
                      process_id=VALUES(process_id),status_reason=VALUES(status_reason),
                      paused_at=VALUES(paused_at),next_probe_at=VALUES(next_probe_at),
                      network_failure_count=VALUES(network_failure_count)
                    """,
                    (
                        worker_id, now, now, settings.SCRAPER_VERSION, socket.gethostname(),
                        os.getpid(), reason[:500], paused_at, next_probe_at, failure_count,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def worker_health(self) -> Dict[str, Any]:
        now = utc_now_naive()
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute("SELECT * FROM crawler_workers ORDER BY heartbeat_at DESC LIMIT 1")
                row = cursor.fetchone()
                if not row:
                    return {"online": False, "message": "Chưa thấy worker nào khởi động"}
                age = max(0, int((now - row["heartbeat_at"]).total_seconds()))
                row["online"] = (
                    row.get("status") in ("online", "waiting_network")
                    and age <= max(15, settings.WORKER_POLL_SECONDS * 4)
                )
                row["waiting_for_network"] = row["online"] and row.get("status") == "waiting_network"
                row["heartbeat_age_seconds"] = age
                return row
            finally:
                cursor.close()

    def mark_worker_offline(self, worker_id: str) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE crawler_workers
                   SET status='offline',heartbeat_at=%s,current_item_id=NULL,status_reason=NULL,
                       paused_at=NULL,next_probe_at=NULL,network_failure_count=0
                   WHERE worker_id=%s""",
                (utc_now_naive(), worker_id),
            )
            conn.commit()
            cursor.close()

    def record_failure(
        self,
        item: Dict[str, Any],
        scrape_failure: ScrapeFailure,
        *,
        meta: Optional[Dict[str, Any]] = None,
        item_total_ms: Optional[int] = None,
        dead_link_confirmation: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Ghi 1 item lỗi. KHÔNG còn tự cascade sibling theo raw ErrorCode.DEAD_LINK — cascade chỉ
        chạy sau khi probe lần 2 xác nhận thật qua record_confirmed_dead_link(). Một DEAD_LINK ở
        đây chỉ còn nghĩa "hết attempt trong lúc chờ xác nhận" (không nên xảy ra ở luồng bình
        thường vì worker luôn probe trước khi gọi hàm này cho DEAD_LINK), không kéo theo sibling.
        """
        now = utc_now_naive()
        meta = meta or {}
        should_retry = scrape_failure.retryable and item["attempt_count"] < settings.WORKER_MAX_ATTEMPTS
        status = "queued" if should_retry else "error"
        retry_at = now + timedelta(seconds=10 * item["attempt_count"]) if should_retry else None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE crawl_run_items
                    SET status=%s,last_error_code=%s,error_message=%s,next_retry_at=%s,
                        finished_at=%s,worker_id=NULL,heartbeat_at=NULL,
                        hotel_link=COALESCE(%s,hotel_link),driver_start_ms=%s,page_load_ms=%s,
                        availability_wait_ms=%s,parse_ms=%s,item_total_ms=%s,
                        artifact_html_path=COALESCE(%s,artifact_html_path),
                        screenshot_path=COALESCE(%s,screenshot_path),
                        dead_link_confirmation=COALESCE(%s,dead_link_confirmation)
                    WHERE id=%s
                    """,
                    (
                        status, scrape_failure.code.value, scrape_failure.message, retry_at,
                        None if should_retry else now, meta.get("final_url"), meta.get("driver_start_ms"),
                        meta.get("page_load_ms"), meta.get("availability_wait_ms"), meta.get("parse_ms"),
                        item_total_ms, meta.get("artifact_html_path"), meta.get("screenshot_path"),
                        json.dumps(dead_link_confirmation, ensure_ascii=False) if dead_link_confirmation else None,
                        item["id"],
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()
        self.recompute_run(item["crawl_run_id"])
        return status

    def compute_dead_link_streak(
        self,
        prev_streak: int,
        prev_last_confirmed_on,
        new_confirmed_on,
    ) -> tuple:
        """Pure logic - không đụng DB, dễ test độc lập.

        Trả (new_streak, streak_started_on_should_reset, review_required).
        streak_started_on_should_reset=True nghĩa là caller phải set
        dead_link_streak_started_on=new_confirmed_on (streak mới bắt đầu hoặc bị đứt quãng).
        """
        if prev_last_confirmed_on == new_confirmed_on:
            return prev_streak, False, prev_streak >= 3
        if prev_last_confirmed_on and (new_confirmed_on - prev_last_confirmed_on).days == 1:
            new_streak = prev_streak + 1
            return new_streak, False, new_streak >= 3
        return 1, True, 1 >= 3

    def record_confirmed_dead_link(
        self,
        item: Dict[str, Any],
        evidence: Dict[str, Any],
        *,
        item_total_ms: Optional[int] = None,
    ) -> str:
        """Chỉ gọi SAU KHI probe lần 2 (canonical URL, driver mới) đã xác nhận dead_link thật.
        Cascade sibling + streak/review update nằm trong cùng 1 transaction để tránh lost update
        nếu có worker khác chạm cùng source_link_hash.
        """
        now = utc_now_naive()
        confirmed_on = _vn_date(now)
        evidence = dict(evidence)
        evidence["confirmed_at_utc"] = now.replace(tzinfo=timezone.utc).isoformat()
        hotel_id = extract_hotel_slug(item["source_hotel_link"])
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT * FROM hotel_link_health WHERE source_link_hash=%s FOR UPDATE",
                    (item["source_link_hash"],),
                )
                health = cursor.fetchone()
                prev_streak = health["consecutive_dead_link_days"] if health else 0
                prev_last_confirmed = health["dead_link_last_confirmed_on"] if health else None
                new_streak, reset_start, review_required = self.compute_dead_link_streak(
                    prev_streak, prev_last_confirmed, confirmed_on
                )
                started_on = confirmed_on if reset_start else (health["dead_link_streak_started_on"] if health else confirmed_on)
                cursor.execute(
                    """
                    INSERT INTO hotel_link_health (
                      source_link_hash, hotel_id, source_hotel_link, consecutive_dead_link_days,
                      dead_link_streak_started_on, dead_link_last_confirmed_on, dead_link_review_required
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                      hotel_id=VALUES(hotel_id), source_hotel_link=VALUES(source_hotel_link),
                      consecutive_dead_link_days=VALUES(consecutive_dead_link_days),
                      dead_link_streak_started_on=VALUES(dead_link_streak_started_on),
                      dead_link_last_confirmed_on=VALUES(dead_link_last_confirmed_on),
                      dead_link_review_required=VALUES(dead_link_review_required)
                    """,
                    (
                        item["source_link_hash"], hotel_id, item["source_hotel_link"], new_streak,
                        started_on, confirmed_on, review_required,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE crawl_run_items
                    SET status='error',last_error_code=%s,error_message=%s,finished_at=%s,
                        worker_id=NULL,heartbeat_at=NULL,item_total_ms=%s,
                        dead_link_confirmation=%s
                    WHERE id=%s
                    """,
                    (
                        ErrorCode.DEAD_LINK.value,
                        "Xac nhan link chet qua probe lan 2 (canonical URL, driver moi)",
                        now, item_total_ms, json.dumps(evidence, ensure_ascii=False), item["id"],
                    ),
                )
                cursor.execute(
                    """
                    UPDATE crawl_run_items
                    SET status='error',last_error_code='dead_link_skipped',
                        error_message='Bỏ qua vì cùng link đã được xác nhận là link chết (2 lần probe)',
                        finished_at=%s
                    WHERE crawl_run_id=%s AND source_link_hash=%s AND status='queued'
                    """,
                    (now, item["crawl_run_id"], item["source_link_hash"]),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()
        self.recompute_run(item["crawl_run_id"])
        return "error"

    @staticmethod
    def _reset_dead_link_health_sql(cursor, source_link_hash: str) -> None:
        """Chạy UPDATE reset bằng cursor CỦA CALLER - không tự mở connection/commit. Dùng để nhét
        vào transaction chính (vd persist_success()) thay vì làm transaction riêng sau khi
        transaction đó đã commit (rủi ro: exception ở đây từng có thể biến 1 item success thật
        thành error/DB_ERROR dù observation đã lưu xong)."""
        cursor.execute(
            """
            UPDATE hotel_link_health
            SET consecutive_dead_link_days=0, dead_link_streak_started_on=NULL,
                dead_link_last_confirmed_on=NULL, dead_link_review_required=FALSE
            WHERE source_link_hash=%s
            """,
            (source_link_hash,),
        )

    def reset_dead_link_health(self, source_link_hash: str) -> None:
        """Wrapper transaction riêng - chỉ dùng khi gọi ĐỘC LẬP (vd script/test), KHÔNG dùng trong
        persist_success() nữa (xem _reset_dead_link_health_sql)."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                self._reset_dead_link_health_sql(cursor, source_link_hash)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def defer_network_failure(
        self,
        item: Dict[str, Any],
        scrape_failure: ScrapeFailure,
        *,
        meta: Optional[Dict[str, Any]] = None,
        item_total_ms: Optional[int] = None,
        dead_link_confirmation: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Return an outage-affected item to the queue without consuming its attempt."""
        now = utc_now_naive()
        retry_at = now + timedelta(seconds=settings.NETWORK_FAILURE_REQUEUE_SECONDS)
        meta = meta or {}
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE crawl_run_items
                    SET status='queued',attempt_count=GREATEST(attempt_count-1,0),
                        last_error_code=%s,error_message=%s,next_retry_at=%s,finished_at=NULL,
                        worker_id=NULL,claimed_at=NULL,heartbeat_at=NULL,
                        hotel_link=COALESCE(%s,hotel_link),driver_start_ms=%s,page_load_ms=%s,
                        availability_wait_ms=%s,parse_ms=%s,item_total_ms=%s,
                        artifact_html_path=COALESCE(%s,artifact_html_path),
                        screenshot_path=COALESCE(%s,screenshot_path),
                        dead_link_confirmation=COALESCE(%s,dead_link_confirmation)
                    WHERE id=%s
                    """,
                    (
                        scrape_failure.code.value,
                        "Tạm hoãn vì mất kết nối; worker sẽ tự chạy lại khi mạng phục hồi",
                        retry_at, meta.get("final_url"), meta.get("driver_start_ms"),
                        meta.get("page_load_ms"), meta.get("availability_wait_ms"),
                        meta.get("parse_ms"), item_total_ms, meta.get("artifact_html_path"),
                        meta.get("screenshot_path"),
                        json.dumps(dead_link_confirmation, ensure_ascii=False) if dead_link_confirmation else None,
                        item["id"],
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()
        self.recompute_run(item["crawl_run_id"])

    def persist_success(
        self,
        *,
        item: Dict[str, Any],
        hotel: Dict[str, Any],
        records: List[Dict[str, Any]],
        diagnostics: Dict[str, Any],
        timings: Dict[str, int],
        artifacts: Dict[str, Optional[str]],
        is_sold_out: bool,
        is_not_bookable: bool = False,
        booking_status_reason: Optional[str] = None,
        dead_link_confirmation: Optional[Dict[str, Any]] = None,
    ) -> str:
        now = utc_now_naive()
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    INSERT INTO hotels (hotel_id,name,name_normalized,hotel_link,address,city,
                      review_score,review_count,amenities,booking_status,booking_status_reason,
                      booking_status_checked_at,attributes_updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                      name=COALESCE(NULLIF(VALUES(name),''),name),
                      name_normalized=COALESCE(NULLIF(VALUES(name_normalized),''),name_normalized),
                      hotel_link=VALUES(hotel_link),address=COALESCE(VALUES(address),address),
                      city=COALESCE(VALUES(city),city),review_score=COALESCE(VALUES(review_score),review_score),
                      review_count=COALESCE(VALUES(review_count),review_count),
                      amenities=COALESCE(VALUES(amenities),amenities),booking_status=VALUES(booking_status),
                      booking_status_reason=VALUES(booking_status_reason),
                      booking_status_checked_at=VALUES(booking_status_checked_at),
                      attributes_updated_at=VALUES(attributes_updated_at)
                    """,
                    (
                        hotel["hotel_id"], hotel["name"], hotel["name_normalized"], hotel["hotel_link"],
                        hotel.get("address"), hotel.get("city"), hotel.get("review_score"),
                        hotel.get("review_count"), json.dumps(hotel.get("amenities") or [], ensure_ascii=False),
                        "not_bookable" if is_not_bookable else "active",
                        booking_status_reason if is_not_bookable else None, now, now,
                    ),
                )
                cursor.execute(
                    """
                    SELECT * FROM hotel_reference_rooms
                    WHERE hotel_id=%s AND checkin_date=%s AND status='approved'
                    LIMIT 1
                    """,
                    (hotel["hotel_id"], item["checkin_date"]),
                )
                reference = cursor.fetchone()
                reference_status = "not_applicable" if (is_sold_out or is_not_bookable) else "calibrating"
                if reference and not is_sold_out and not is_not_bookable:
                    match_index, reference_status, score = select_best_match(records, reference)
                    for index, record in enumerate(records):
                        record["reference_definition_id"] = reference["id"] if index == match_index else None
                        record["reference_match_status"] = reference_status if index == match_index else "not_reference"
                        record["reference_match_score"] = score if index == match_index else None
                        record["is_reference_room"] = index == match_index
                elif is_sold_out or is_not_bookable:
                    for record in records:
                        record["reference_match_status"] = "not_applicable"

                cursor.execute("DELETE FROM price_observations WHERE crawl_run_item_id=%s", (item["id"],))
                if records:
                    query = """
                    INSERT INTO price_observations (
                      hotel_id,crawl_run_id,crawl_run_item_id,crawl_trigger,observed_at,checkin_date,checkout_date,
                      lead_time,price_total,price_per_night,original_price,discount_percent,taxes_fees,
                      price_includes_tax,room_type_raw,room_type_norm,room_option_index,room_option_key,
                      room_identity_key,rate_plan_key,is_reference_room,reference_definition_id,
                      reference_match_status,reference_match_score,max_occupancy,bed_config,room_area,
                      breakfast_included,free_cancellation,cancellation_policy,rooms_left,is_sold_out,
                      availability_status,is_anomaly
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """
                    values = [(
                        r["hotel_id"], r["crawl_run_id"], item["id"], r["crawl_trigger"], r["observed_at"],
                        r["checkin_date"], r["checkout_date"], r["lead_time"], r.get("price_total"),
                        r.get("price_per_night"), r.get("original_price"), r.get("discount_percent"),
                        r.get("taxes_fees"), r.get("price_includes_tax"), r.get("room_type_raw"),
                        r.get("room_type_norm"), r["room_option_index"], r["room_option_key"],
                        r.get("room_identity_key"), r.get("rate_plan_key"), r.get("is_reference_room", False),
                        r.get("reference_definition_id"), r.get("reference_match_status", "calibrating"),
                        r.get("reference_match_score"), r.get("max_occupancy"), r.get("bed_config"),
                        r.get("room_area"), r.get("breakfast_included"), r.get("free_cancellation"),
                        r.get("cancellation_policy"), r.get("rooms_left"), r.get("is_sold_out", False),
                        r.get("availability_status", "available"), r.get("is_anomaly", False),
                    ) for r in records]
                    cursor.executemany(query, values)
                saved_count = len(records)

                rejected_count = int(diagnostics.get("rejected_options_count", 0))
                duplicate_count = int(diagnostics.get("duplicate_options_count", 0))
                parsed_count = int(diagnostics.get("parsed_options_count", len(records)))
                expected_saved_count = max(0, parsed_count - duplicate_count)
                if is_not_bookable:
                    item_status = "not_bookable"
                    reference_status = "not_applicable"
                elif is_sold_out:
                    item_status = "sold_out"
                    reference_status = "not_applicable"
                elif rejected_count or saved_count != expected_saved_count:
                    item_status = "partial"
                else:
                    item_status = "success"
                error_code = None
                error_message = None
                if rejected_count:
                    error_code = ErrorCode.PARSER_PARTIAL.value
                    error_message = f"Parser loại {rejected_count}/{diagnostics.get('candidate_rate_count', 0)} candidate"
                elif is_not_bookable:
                    error_code = ErrorCode.PROPERTY_NOT_BOOKABLE.value
                    error_message = booking_status_reason or "Booking xác nhận chỗ nghỉ hiện không nhận đặt phòng"

                cursor.execute(
                    """
                    UPDATE crawl_run_items SET hotel_link=%s,hotel_name=%s,hotel_id=%s,status=%s,
                      dom_room_row_count=%s,candidate_rate_count=%s,parsed_options_count=%s,
                      rejected_options_count=%s,duplicate_options_count=%s,
                      raw_options_count=%s,saved_options_count=%s,
                      parse_warning_count=%s,rejected_options=%s,reference_match_status=%s,
                      driver_start_ms=%s,page_load_ms=%s,availability_wait_ms=%s,parse_ms=%s,
                      db_write_ms=%s,item_total_ms=%s,artifact_html_path=%s,screenshot_path=%s,
                      last_error_code=%s,error_message=%s,finished_at=%s,worker_id=NULL,heartbeat_at=NULL,
                      dead_link_confirmation=COALESCE(%s,dead_link_confirmation)
                    WHERE id=%s
                    """,
                    (
                        diagnostics.get("final_url") or item["hotel_link"], hotel.get("name"), hotel["hotel_id"],
                        item_status, diagnostics.get("dom_room_row_count", 0),
                        diagnostics.get("candidate_rate_count", 0), parsed_count, rejected_count,
                        duplicate_count, parsed_count, saved_count,
                        diagnostics.get("parse_warning_count", rejected_count),
                        json.dumps(diagnostics.get("rejected_options", []), ensure_ascii=False), reference_status,
                        timings.get("driver_start_ms"), timings.get("page_load_ms"),
                        timings.get("availability_wait_ms"), timings.get("parse_ms"), timings.get("db_write_ms"),
                        timings.get("item_total_ms"), artifacts.get("artifact_html_path"),
                        artifacts.get("screenshot_path"), error_code, error_message, now,
                        json.dumps(dead_link_confirmation, ensure_ascii=False) if dead_link_confirmation else None,
                        item["id"],
                    ),
                )
                if is_not_bookable:
                    cursor.execute(
                        """
                        UPDATE crawl_run_items
                        SET hotel_name=%s,hotel_id=%s,status='not_bookable',
                            reference_match_status='not_applicable',last_error_code=%s,
                            error_message=%s,next_retry_at=NULL,finished_at=%s
                        WHERE crawl_run_id=%s AND source_link_hash=%s AND status='queued'
                        """,
                        (
                            hotel.get("name"), hotel["hotel_id"], ErrorCode.PROPERTY_NOT_BOOKABLE.value,
                            "Bỏ qua vì cùng chỗ nghỉ đã được Booking xác nhận là không thể đặt",
                            now, item["crawl_run_id"], item["source_link_hash"],
                        ),
                    )
                # Bat ky ket qua property-level hop le nao (success/partial/sold_out/not_bookable)
                # deu chung minh link con song - clear dead-link streak dang co, neu co. Chay trong
                # CUNG transaction/cursor nay truoc commit - KHONG mo transaction rieng sau commit,
                # vi neu buoc do fail rieng thi worker.py se doi 1 item da luu du lieu that thanh
                # error/DB_ERROR (xem GPT review file 05 MAJOR 1).
                self._reset_dead_link_health_sql(cursor, item["source_link_hash"])
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()
        self.recompute_run(item["crawl_run_id"])
        return item_status

    def _refresh_reference(self, cursor, hotel_id: str, checkin_date, now) -> None:
        """Build one reference for a hotel/check-in series from complete items only."""
        cursor.execute(
            """
            SELECT id FROM hotel_reference_rooms
            WHERE hotel_id=%s AND checkin_date=%s AND status='approved' LIMIT 1
            """,
            (hotel_id, checkin_date),
        )
        if cursor.fetchone():
            return
        cursor.execute(
            """
            SELECT COUNT(DISTINCT po.crawl_run_item_id) eligible_item_count
            FROM price_observations po
            JOIN crawl_runs cr ON cr.id=po.crawl_run_id AND cr.status='completed'
            JOIN crawl_run_items cri ON cri.id=po.crawl_run_item_id AND cri.status='success'
            WHERE po.hotel_id=%s AND po.checkin_date=%s
              AND po.is_sold_out=0 AND po.room_identity_key IS NOT NULL
            """,
            (hotel_id, checkin_date),
        )
        eligible_item_count = int(cursor.fetchone()["eligible_item_count"] or 0)
        if eligible_item_count == 0:
            return

        cursor.execute(
            "DELETE FROM hotel_room_candidates WHERE hotel_id=%s AND checkin_date=%s",
            (hotel_id, checkin_date),
        )
        cursor.execute(
            """
            SELECT po.room_identity_key,po.rate_plan_key,MAX(po.room_type_raw) room_type_anchor_raw,
              MAX(po.room_type_norm) room_type_norm,MAX(po.max_occupancy) max_occupancy,
              MAX(po.bed_config) bed_config,MAX(po.room_area) room_area,
              MAX(po.breakfast_included) breakfast_included,MAX(po.free_cancellation) free_cancellation,
              COUNT(*) observation_count,COUNT(DISTINCT po.crawl_run_id) distinct_run_count,
              COUNT(DISTINCT po.crawl_run_item_id) distinct_item_count,
              MIN(po.observed_at) first_seen_at,MAX(po.observed_at) last_seen_at
            FROM price_observations po
            JOIN crawl_runs cr ON cr.id=po.crawl_run_id AND cr.status='completed'
            JOIN crawl_run_items cri ON cri.id=po.crawl_run_item_id AND cri.status='success'
            WHERE po.hotel_id=%s AND po.checkin_date=%s
              AND po.is_sold_out=0 AND po.room_identity_key IS NOT NULL
            GROUP BY po.room_identity_key,po.rate_plan_key
            """,
            (hotel_id, checkin_date),
        )
        candidates = cursor.fetchall()
        for candidate in candidates:
            coverage = min(1.0, candidate["distinct_item_count"] / eligible_item_count)
            cursor.execute(
                """
                INSERT INTO hotel_room_candidates (
                  hotel_id,checkin_date,room_identity_key,rate_plan_key,room_type_anchor_raw,room_type_norm,
                  max_occupancy,bed_config,room_area,breakfast_included,free_cancellation,
                  observation_count,distinct_run_count,distinct_item_count,eligible_item_count,
                  item_coverage,first_seen_at,last_seen_at,aliases
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    hotel_id, checkin_date, candidate["room_identity_key"], candidate["rate_plan_key"],
                    candidate["room_type_anchor_raw"] or "", candidate["room_type_norm"],
                    candidate["max_occupancy"], candidate["bed_config"], candidate["room_area"],
                    candidate["breakfast_included"], candidate["free_cancellation"],
                    candidate["observation_count"], candidate["distinct_run_count"],
                    candidate["distinct_item_count"], eligible_item_count, coverage,
                    candidate["first_seen_at"], candidate["last_seen_at"],
                    json.dumps([candidate["room_type_anchor_raw"]], ensure_ascii=False),
                ),
            )
        cursor.execute(
            """
            SELECT * FROM hotel_room_candidates
            WHERE hotel_id=%s AND checkin_date=%s
            ORDER BY (observation_count=distinct_item_count) DESC,
              item_coverage DESC,distinct_run_count DESC,
              (max_occupancy IS NOT NULL AND max_occupancy<=2) DESC,observation_count DESC
            LIMIT 1
            """,
            (hotel_id, checkin_date),
        )
        best = cursor.fetchone()
        if not best:
            return
        coverage = float(best["item_coverage"])
        unique_per_item = best["observation_count"] == best["distinct_item_count"]
        confidence = coverage if unique_per_item else coverage * 0.60
        status = "approved" if is_reference_candidate_eligible(
            best,
            min_runs=settings.REFERENCE_MIN_RUNS,
            min_coverage=settings.REFERENCE_MIN_COVERAGE,
        ) else "proposed"
        cursor.execute(
            """
            SELECT id FROM hotel_reference_rooms
            WHERE hotel_id=%s AND checkin_date=%s AND status='proposed' LIMIT 1
            """,
            (hotel_id, checkin_date),
        )
        proposed = cursor.fetchone()
        params = (
            best["room_identity_key"], best["rate_plan_key"], best["room_type_anchor_raw"],
            best["room_type_norm"], best["max_occupancy"], best["bed_config"], best["room_area"],
            best["breakfast_included"], best["free_cancellation"], status, coverage, confidence,
            best["observation_count"], best["distinct_run_count"], best["distinct_item_count"],
            best["eligible_item_count"],
            json.dumps([best["room_type_anchor_raw"]], ensure_ascii=False),
            now if status == "approved" else None,
        )
        if proposed:
            cursor.execute(
                """
                UPDATE hotel_reference_rooms SET room_identity_key=%s,rate_plan_key=%s,
                  room_type_anchor_raw=%s,room_type_norm=%s,max_occupancy=%s,bed_config=%s,room_area=%s,
                  breakfast_included=%s,free_cancellation=%s,status=%s,coverage=%s,confidence_score=%s,
                  observation_count=%s,distinct_run_count=%s,distinct_item_count=%s,
                  eligible_item_count=%s,aliases=%s,active_from=%s
                WHERE id=%s
                """,
                params + (proposed["id"],),
            )
            reference_id = proposed["id"]
        else:
            cursor.execute(
                """
                INSERT INTO hotel_reference_rooms (
                  hotel_id,checkin_date,room_identity_key,rate_plan_key,room_type_anchor_raw,room_type_norm,
                  max_occupancy,bed_config,room_area,breakfast_included,free_cancellation,
                  selection_method,status,coverage,confidence_score,observation_count,distinct_run_count,
                  distinct_item_count,eligible_item_count,aliases,active_from
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'auto',%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (hotel_id, checkin_date) + params,
            )
            reference_id = cursor.lastrowid
        if status == "approved":
            cursor.execute(
                """
                UPDATE price_observations SET is_reference_room=FALSE,reference_definition_id=NULL,
                  reference_match_status='not_reference',reference_match_score=NULL
                WHERE hotel_id=%s AND checkin_date=%s AND is_sold_out=0
                """,
                (hotel_id, checkin_date),
            )
            cursor.execute(
                """
                UPDATE price_observations SET is_reference_room=TRUE,reference_definition_id=%s,
                  reference_match_status='exact',reference_match_score=1.0
                WHERE hotel_id=%s AND checkin_date=%s
                  AND room_identity_key=%s AND rate_plan_key=%s
                """,
                (
                    reference_id, hotel_id, checkin_date,
                    best["room_identity_key"], best["rate_plan_key"],
                ),
            )
            cursor.execute(
                """
                UPDATE crawl_run_items cri
                SET reference_match_status=CASE
                  WHEN EXISTS (
                    SELECT 1 FROM price_observations po
                    WHERE po.crawl_run_item_id=cri.id AND po.reference_definition_id=%s
                  ) THEN 'exact' ELSE 'unavailable' END
                WHERE cri.hotel_id=%s AND cri.checkin_date=%s
                  AND cri.status IN ('success','partial')
                """,
                (reference_id, hotel_id, checkin_date),
            )

    def repair_not_bookable_item_urls(self) -> int:
        """Restore each skipped item's own dates after the property circuit breaker."""
        repaired = 0
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT id,source_hotel_link,hotel_link,checkin_date,checkout_date
                    FROM crawl_run_items WHERE status='not_bookable'
                    """
                )
                for item in cursor.fetchall():
                    expected_url = build_scrape_url(
                        item["source_hotel_link"],
                        str(item["checkin_date"]),
                        str(item["checkout_date"]),
                    )
                    if item["hotel_link"] != expected_url:
                        cursor.execute(
                            "UPDATE crawl_run_items SET hotel_link=%s WHERE id=%s",
                            (expected_url, item["id"]),
                        )
                        repaired += 1
                conn.commit()
                return repaired
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def recalibrate_all_references(self) -> Dict[str, int]:
        """Retire legacy definitions and rebuild without deleting observations."""
        now = utc_now_naive()
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    DELETE reference FROM hotel_reference_rooms reference
                    LEFT JOIN hotels hotel ON hotel.hotel_id=reference.hotel_id
                    WHERE hotel.hotel_id IS NULL
                    """
                )
                orphaned_count = cursor.rowcount
                # Old FK-disabled setup scripts could leave invalid metadata.
                # Commit this repair before InnoDB revalidates rows on UPDATE.
                conn.commit()
                cursor.execute(
                    "UPDATE hotel_reference_rooms SET status='retired',active_to=%s WHERE status IN ('approved','proposed')",
                    (now,),
                )
                retired_count = cursor.rowcount
                cursor.execute("DELETE FROM hotel_room_candidates")
                cursor.execute(
                    """
                    UPDATE price_observations
                    SET is_reference_room=FALSE,reference_definition_id=NULL,
                        reference_match_status=IF(is_sold_out=1,'not_applicable','calibrating'),
                        reference_match_score=NULL
                    """
                )
                cursor.execute(
                    """
                    UPDATE crawl_run_items
                    SET reference_match_status=CASE
                      WHEN status IN ('sold_out','not_bookable') THEN 'not_applicable'
                      ELSE 'calibrating' END
                    """
                )
                cursor.execute(
                    """
                    SELECT DISTINCT hotel_id,checkin_date FROM price_observations
                    WHERE hotel_id IS NOT NULL ORDER BY hotel_id,checkin_date
                    """
                )
                series = [(row["hotel_id"], row["checkin_date"]) for row in cursor.fetchall()]
                for hotel_id, checkin_date in series:
                    self._refresh_reference(cursor, hotel_id, checkin_date, now)
                cursor.execute("SELECT COUNT(*) n FROM hotel_reference_rooms WHERE status='approved'")
                approved_count = int(cursor.fetchone()["n"])
                cursor.execute("SELECT COUNT(*) n FROM hotel_reference_rooms WHERE status='proposed'")
                proposed_count = int(cursor.fetchone()["n"])
                conn.commit()
                return {
                    "series": len(series), "retired": retired_count,
                    "approved": approved_count, "proposed": proposed_count,
                    "orphaned_removed": orphaned_count,
                }
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def refresh_proposed_references(self) -> int:
        """Refresh candidate metrics without retiring or replacing audit history."""
        now = utc_now_naive()
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT DISTINCT hotel_id,checkin_date FROM hotel_reference_rooms
                    WHERE status='proposed' AND checkin_date IS NOT NULL
                    """
                )
                series = [(row["hotel_id"], row["checkin_date"]) for row in cursor.fetchall()]
                for hotel_id, checkin_date in series:
                    self._refresh_reference(cursor, hotel_id, checkin_date, now)
                conn.commit()
                return len(series)
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def recompute_run(self, run_id: int) -> None:
        now = utc_now_naive()
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT COUNT(*) total,
                      SUM(status='success') success_count,
                      SUM(status='partial') partial_count,
                      SUM(status='sold_out') sold_out_count,
                      SUM(status='not_bookable') not_bookable_count,
                      SUM(status='error') error_count,
                      SUM(status IN ('success','partial','sold_out','not_bookable','error')) processed
                    FROM crawl_run_items WHERE crawl_run_id=%s
                    """,
                    (run_id,),
                )
                counts = cursor.fetchone()
                completed = counts["processed"] == counts["total"] and counts["total"] > 0
                cursor.execute(
                    """
                    UPDATE crawl_runs SET total=%s,processed=%s,success_count=%s,partial_count=%s,
                      sold_out_count=%s,not_bookable_count=%s,error_count=%s,status=%s,
                      finished_at=%s WHERE id=%s
                    """,
                    (
                        counts["total"], counts["processed"], counts["success_count"],
                        counts["partial_count"], counts["sold_out_count"],
                        counts["not_bookable_count"], counts["error_count"],
                        "completed" if completed else "running", now if completed else None, run_id,
                    ),
                )
                if completed:
                    cursor.execute(
                        """
                        SELECT DISTINCT hotel_id,checkin_date FROM crawl_run_items
                        WHERE crawl_run_id=%s AND hotel_id IS NOT NULL
                        """,
                        (run_id,),
                    )
                    for row in cursor.fetchall():
                        self._refresh_reference(cursor, row["hotel_id"], row["checkin_date"], now)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def update_item_timings(self, item_id: int, db_write_ms: int, item_total_ms: int) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE crawl_run_items SET db_write_ms=%s,item_total_ms=%s WHERE id=%s",
                (db_write_ms, item_total_ms, item_id),
            )
            conn.commit()
            cursor.close()
