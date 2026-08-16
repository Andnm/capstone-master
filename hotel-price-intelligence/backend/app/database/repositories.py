import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.database import get_db_connection


class HotelRepository:
    def upsert(self, hotel: Dict[str, Any]) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = """
                    INSERT INTO hotels (
                        hotel_id, name, name_normalized, hotel_link, address, city,
                        review_score, review_count, amenities, attributes_updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE
                        name = VALUES(name),
                        name_normalized = VALUES(name_normalized),
                        hotel_link = VALUES(hotel_link),
                        address = COALESCE(VALUES(address), address),
                        city = COALESCE(VALUES(city), city),
                        review_score = COALESCE(VALUES(review_score), review_score),
                        review_count = COALESCE(VALUES(review_count), review_count),
                        amenities = COALESCE(VALUES(amenities), amenities),
                        attributes_updated_at = NOW()
                """
                cursor.execute(query, (
                    hotel['hotel_id'], hotel['name'], hotel['name_normalized'], hotel['hotel_link'],
                    hotel.get('address'), hotel.get('city'),
                    hotel.get('review_score'), hotel.get('review_count'),
                    json.dumps(hotel.get('amenities') or [], ensure_ascii=False),
                ))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()


class PriceObservationRepository:
    def insert_many(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 0
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                query = """
                    INSERT INTO price_observations (
                        hotel_id, crawl_run_id, crawl_trigger, observed_at, checkin_date, checkout_date,
                        lead_time, price_total, price_per_night, original_price, discount_percent,
                        taxes_fees, price_includes_tax,
                        room_type_raw, room_type_norm, room_option_index, room_option_key,
                        is_reference_room, max_occupancy, bed_config,
                        room_area, breakfast_included, free_cancellation, cancellation_policy,
                        rooms_left, is_sold_out, availability_status, is_anomaly
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                values = [(
                    r['hotel_id'], r['crawl_run_id'], r['crawl_trigger'], r['observed_at'],
                    r['checkin_date'], r['checkout_date'], r['lead_time'],
                    r.get('price_total'), r.get('price_per_night'), r.get('original_price'),
                    r.get('discount_percent'), r.get('taxes_fees'), r.get('price_includes_tax'),
                    r.get('room_type_raw'), r.get('room_type_norm'),
                    r['room_option_index'], r['room_option_key'],
                    r.get('is_reference_room', False), r.get('max_occupancy'), r.get('bed_config'),
                    r.get('room_area'), r.get('breakfast_included'), r.get('free_cancellation'),
                    r.get('cancellation_policy'), r.get('rooms_left'), r.get('is_sold_out', False),
                    r.get('availability_status', 'available'), r.get('is_anomaly', False),
                ) for r in records]
                cursor.executemany(query, values)
                conn.commit()
                return cursor.rowcount
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def list_for_export(self, crawl_run_id: int) -> List[Dict[str, Any]]:
        """price_observations JOIN hotels cho 1 run — dùng để xuất Excel."""
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT
                        po.hotel_id, h.name AS hotel_name, h.city, h.address,
                        h.review_score, h.review_count,
                        cri.hotel_link AS crawl_url,
                        cri.dom_room_row_count, cri.candidate_rate_count,
                        cri.parsed_options_count, cri.rejected_options_count, cri.duplicate_options_count,
                        cri.raw_options_count, cri.saved_options_count, cri.reference_match_status AS item_reference_status,
                        po.observed_at, po.checkin_date, po.checkout_date, po.lead_time,
                        po.room_type_raw, po.room_type_norm, po.room_option_index,
                        po.room_option_key, po.room_identity_key, po.rate_plan_key,
                        po.is_reference_room, po.reference_definition_id,
                        po.reference_match_status, po.reference_match_score,
                        po.price_total, po.price_per_night, po.original_price, po.discount_percent,
                        po.taxes_fees, po.price_includes_tax,
                        po.max_occupancy, po.bed_config, po.room_area,
                        po.breakfast_included, po.free_cancellation, po.cancellation_policy,
                        po.rooms_left, po.is_sold_out, po.availability_status, po.is_anomaly
                    FROM price_observations po
                    JOIN hotels h ON h.hotel_id = po.hotel_id
                    LEFT JOIN crawl_run_items cri
                      ON cri.id = po.crawl_run_item_id
                    WHERE po.crawl_run_id = %s
                    ORDER BY po.hotel_id, po.checkin_date, po.is_reference_room DESC
                    """,
                    (crawl_run_id,),
                )
                return cursor.fetchall()
            finally:
                cursor.close()


class CrawlRunRepository:
    def create(self, trigger_type: str, source_file: Optional[str], total: int,
               date_mode: str = 'lead_time', lead_time_buckets: Optional[str] = None,
               checkin_dates: Optional[List[str]] = None) -> int:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO crawl_runs
                        (status, trigger_type, source_file, date_mode, lead_time_buckets, checkin_dates, total)
                    VALUES ('queued', %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        trigger_type, source_file, date_mode, lead_time_buckets,
                        json.dumps(checkin_dates, ensure_ascii=False) if checkin_dates else None,
                        total,
                    ),
                )
                conn.commit()
                return cursor.lastrowid
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def has_running(self) -> bool:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT COUNT(*) FROM crawl_runs WHERE status = 'running'")
                return cursor.fetchone()[0] > 0
            finally:
                cursor.close()

    def recover_stale_running(self) -> None:
        """Watchdog: nếu 1 run ở trạng thái 'running' nhưng không được cập nhật tiến độ
        trong quá lâu (worker process chết giữa chừng), tự động đánh dấu 'failed' để
        giải phóng lock cho run khác. Gọi ở đầu mỗi lần drain_queue().
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE crawl_runs
                    SET status = 'failed', finished_at = NOW(),
                        error_message = 'stale run - watchdog tự giải phóng lock'
                    WHERE status = 'running'
                      AND updated_at < (NOW() - INTERVAL %s MINUTE)
                    """,
                    (settings.STALE_RUN_MINUTES,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def has_queued(self) -> bool:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT COUNT(*) FROM crawl_runs WHERE status = 'queued'")
                return cursor.fetchone()[0] > 0
            finally:
                cursor.close()

    def try_claim_next_queued(self) -> Optional[int]:
        """Nếu không có run nào đang 'running', lấy run 'queued' cũ nhất và chuyển sang
        'running'. Trả về id nếu claim được, None nếu không (đã có run khác đang chạy,
        hoặc không có run nào trong hàng đợi). Dùng transaction + FOR UPDATE để tránh race.
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT id FROM crawl_runs WHERE status = 'running' LIMIT 1 FOR UPDATE")
                if cursor.fetchone():
                    conn.rollback()
                    return None

                cursor.execute(
                    "SELECT id FROM crawl_runs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1 FOR UPDATE"
                )
                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return None

                run_id = row[0]
                cursor.execute(
                    "UPDATE crawl_runs SET status = 'running', started_at = NOW() WHERE id = %s",
                    (run_id,),
                )
                conn.commit()
                return run_id
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def set_total(self, run_id: int, total: int) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("UPDATE crawl_runs SET total = %s WHERE id = %s", (total, run_id))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def update_progress(self, run_id: int, processed: int, success_count: int,
                        partial_count: int, error_count: int) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE crawl_runs
                    SET processed = %s, success_count = %s, partial_count = %s, error_count = %s
                    WHERE id = %s
                    """,
                    (processed, success_count, partial_count, error_count, run_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def mark_completed(self, run_id: int) -> None:
        self._finish(run_id, 'completed', None)

    def mark_failed(self, run_id: int, error_message: str) -> None:
        self._finish(run_id, 'failed', error_message)

    def _finish(self, run_id: int, status: str, error_message: Optional[str]) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE crawl_runs
                    SET status = %s, finished_at = NOW(), error_message = %s
                    WHERE id = %s
                    """,
                    (status, error_message, run_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    @staticmethod
    def _parse_checkin_dates(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if row and isinstance(row.get('checkin_dates'), str):
            try:
                row['checkin_dates'] = json.loads(row['checkin_dates'])
            except Exception:
                pass
        if row and isinstance(row.get('crawl_context'), str):
            try:
                row['crawl_context'] = json.loads(row['crawl_context'])
            except Exception:
                pass
        return row

    def get_by_id(self, run_id: int) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute("SELECT * FROM crawl_runs WHERE id = %s", (run_id,))
                return self._parse_checkin_dates(cursor.fetchone())
            finally:
                cursor.close()

    def list_runs(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT * FROM crawl_runs ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                return [self._parse_checkin_dates(r) for r in cursor.fetchall()]
            finally:
                cursor.close()


class CrawlRunItemRepository:
    """1 dòng = 1 lần thử cào (1 khách sạn x 1 ngày checkin) — phục vụ bảng chi tiết job ở FE."""

    def create(self, crawl_run_id: int, hotel_link: str, hotel_name_hint: Optional[str],
               hotel_name: Optional[str], hotel_id: Optional[str], checkin_date: str, status: str,
               error_message: Optional[str], raw_options_count: int = 0,
               saved_options_count: int = 0) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO crawl_run_items
                        (crawl_run_id, hotel_link, hotel_name_hint, hotel_name, hotel_id,
                         checkin_date, status, raw_options_count, saved_options_count, error_message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (crawl_run_id, hotel_link, hotel_name_hint, hotel_name, hotel_id,
                     checkin_date, status, raw_options_count, saved_options_count, error_message),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def list_by_run(self, crawl_run_id: int) -> List[Dict[str, Any]]:
        """Trả về từng item kèm `rooms`: toàn bộ price_observations cào được cho đúng
        (hotel_id, checkin_date) trong CÙNG run này — để FE hiển thị đầy đủ dữ liệu đã cào,
        không chỉ trạng thái thành công/lỗi.
        """
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT cri.*, h.city AS hotel_city, h.address AS hotel_address,
                           h.review_score AS hotel_review_score, h.review_count AS hotel_review_count
                    FROM crawl_run_items cri
                    LEFT JOIN hotels h ON h.hotel_id = cri.hotel_id
                    WHERE cri.crawl_run_id = %s
                    ORDER BY cri.checkin_date, cri.hotel_link
                    """,
                    (crawl_run_id,),
                )
                items = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT record_id, crawl_run_item_id, hotel_id, checkin_date, room_type_raw, room_type_norm,
                           room_option_index, room_option_key, room_identity_key, rate_plan_key,
                           is_reference_room, reference_definition_id, reference_match_status,
                           reference_match_score,
                           price_total, price_per_night, original_price, discount_percent,
                           taxes_fees, price_includes_tax, max_occupancy, bed_config, room_area,
                           breakfast_included, free_cancellation, cancellation_policy,
                           rooms_left, availability_status
                    FROM price_observations
                    WHERE crawl_run_id = %s
                    ORDER BY record_id ASC
                    """,
                    (crawl_run_id,),
                )
                rooms_by_item: Dict[int, List[Dict[str, Any]]] = {}
                for room in cursor.fetchall():
                    rooms_by_item.setdefault(room['crawl_run_item_id'], []).append(room)

                for item in items:
                    item['rooms'] = rooms_by_item.get(item['id'], [])

                return items
            finally:
                cursor.close()

    def get_by_id(self, item_id: int) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM crawl_run_items WHERE id=%s", (item_id,))
            row = cursor.fetchone()
            cursor.close()
            return row
