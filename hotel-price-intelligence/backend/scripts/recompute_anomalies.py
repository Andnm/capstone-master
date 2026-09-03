"""Recompute price_observations.is_anomaly cho DB vận hành (operational, KHÔNG phải warehouse).

Thiết kế chốt qua discuss/anomaly-detection-recompute/ (13 file, PASS FOR DESIGN file 12):
mỗi giá được so 2 chiều - (a) so với các loại phòng KHÁC cùng khách sạn/checkin/lần cào (context),
(b) so với lịch sử TRƯỚC ĐÓ của chính loại phòng đó (temporal, causal - không dùng dữ liệu tương
lai). Vì sao cần 2 chiều: 1 phòng bị "khoá mềm" (giá cực cao) NGAY TỪ LẦN CÀO ĐẦU TIÊN sẽ có lịch sử
riêng cũng luôn cao -> so temporal cho ratio ~1, không bao giờ bắt được - phải so với các phòng khác
CÙNG khách sạn mới thấy lệch (case thật: Lumina Premium Đà Lạt, "Suite 1 Phòng Ngủ" 76,5-90 triệu
trong khi phòng khác cùng khách sạn chỉ 2-5 triệu). Đồng thời không được báo nhầm surge giá thật (vd
Tết Dương lịch) là bất thường - khi CẢ khách sạn cùng tăng giá, context median cũng tăng theo nên
ratio giữ nguyên ~1, không bị flag (case Roma Hotel Phu Quoc 01/01/2027, verify qua Booking.com thật
là giá surge thật, không phải bug).

Ra quyết định cho TỪNG record_id riêng lẻ (không phải cả room hay cả item) - 1 rate-option lệch giá
không được kéo theo các rate-option khác cùng phòng, và các phòng khác cùng item tuyệt đối không bị
đụng tới.

5 mức (không phải đúng/sai):
  not_applicable        - sold-out, giá NULL, hoặc không xác định được room_identity_key
  insufficient_evidence - < 2 loại phòng khác cùng khách sạn để so sánh
  normal                - giá trong ngưỡng bình thường
  suspected              - giá cao bất thường so với ngữ cảnh - HOẶC chưa đủ bằng chứng lặp lại,
                            HOẶC đã lặp lại nhiều lần/nhiều ngày check-in (reason
                            'persistent_contextual_high') nhưng CHƯA đủ tin cậy để tự động loại
                            khỏi train - xem CẢNH BÁO dưới
  confirmed              - CHỈ 2 trường hợp: giá quá thấp phi lý (implausible_low), HOẶC tăng vọt
                            so với chính lịch sử nó (high_price_outlier) - ĐÂY là "rule-confirmed"
                            theo ngưỡng cấu hình, KHÔNG phải bằng chứng đã xác minh ý đồ khách sạn.

CẢNH BÁO (GPT review file 15 MAJOR 2, 2026-09-03): bản đầu tiên coi persistence-only (giá cao lặp
lại nhiều lần/nhiều ngày, không có spike so với lịch sử riêng) là confirmed/'suspected_soft_lock'.
Audit dữ liệu thật cho thấy nhánh này bắt NHẦM nhiều villa/suite cao cấp hợp lệ (Pullman Vung Tau
Presidential suite, Movenpick Phu Quoc villa...) vì chúng có CÙNG dấu hiệu thống kê với soft-lock
thật (giá cao ổn định, lặp lại nhiều ngày) - thống kê đơn thuần không phân biệt được 2 hiện tượng
này. Đã hạ persistence-only xuống suspected/'persistent_contextual_high', KHÔNG tự động loại khỏi
train nữa, chờ rule v2 dùng thêm tín hiệu cấu trúc (max_occupancy/room_area/tên phòng) để phân biệt
tier sản phẩm thật với khoá mềm.

Chỉ `confirmed` mới nên bị loại khỏi tập train (ở tầng warehouse/curated - script này chỉ ghi tín
hiệu SỚM cho vận hành, KHÔNG phải authority cho train; xem CLAUDE.md mục 4.5).

Run (dry-run mặc định, chỉ in báo cáo - từ backend/):
    python scripts/recompute_anomalies.py
    python scripts/recompute_anomalies.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from statistics import median
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_db_connection

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

CONFIG = {
    "method_version": "v1",
    "high_context_ratio": Decimal("5"),
    "high_temporal_ratio": Decimal("5"),
    "absolute_low_floor_vnd": Decimal("50000"),
    "context_min_other_room_keys": 2,
    "temporal_min_distinct_items": 5,
    "temporal_min_distinct_dates": 3,
    "persistence_min_distinct_items": 3,
    "persistence_min_distinct_checkins": 2,
}

STATUSES = ("not_applicable", "insufficient_evidence", "normal", "suspected", "confirmed")


def _vn_date(observed_at_utc_naive: datetime) -> date:
    return observed_at_utc_naive.replace(tzinfo=timezone.utc).astimezone(VN_TZ).date()


@dataclass(frozen=True)
class HistoryEntry:
    item_id: int
    observed_at: datetime
    vn_observation_date: date
    checkin_date: date
    representative_price: Decimal
    context_median: Optional[Decimal]
    representative_context_ratio: Optional[Decimal]


def apply_rule(
    price: Decimal,
    context_median: Optional[Decimal],
    context_room_count: int,
    temporal_median: Optional[Decimal],
    temporal_sufficient: bool,
    persistence_ok: bool,
) -> tuple[str, Optional[str]]:
    """State machine chốt qua discuss (file 06/09/11, PASS file 12) - KHÔNG đổi logic khi sửa code,
    chỉ sửa qua đúng quy trình discuss lại nếu cần."""
    if price < CONFIG["absolute_low_floor_vnd"]:
        return "confirmed", "implausible_low"
    if context_room_count < CONFIG["context_min_other_room_keys"]:
        return "insufficient_evidence", None
    if context_median is None or price < context_median * CONFIG["high_context_ratio"]:
        return "normal", None
    if temporal_sufficient and temporal_median is not None and price >= temporal_median * CONFIG["high_temporal_ratio"]:
        return "confirmed", "high_price_outlier"
    if persistence_ok:
        # KHÔNG tự động confirmed - audit dữ liệu thật (GPT review file 15 MAJOR 2) cho thấy nhánh
        # nay tinh chinh chua du de phan biet "phong khoa mem" voi "villa/suite cao cap hop le luon
        # dat hon phong thuong" (case that: Pullman Vung Tau Presidential suite, Movenpick Phu Quoc
        # villa... deu bi bat nham vi ca 2 hien tuong co CUNG dau hieu thong ke: gia cao on dinh, lap
        # lai nhieu ngay). Giu o suspected voi reason rieng cho toi khi co rule v2 phan biet duoc
        # tier san pham that (vd dung max_occupancy/room_area/ten phong). Lumina van dung la nghi van
        # hop ly, chi la chua du chac de tu dong loai khoi train.
        return "suspected", "persistent_contextual_high"
    return "suspected", "contextual_high"


def _load_scope(cursor) -> list[dict]:
    """Toàn bộ price_observations thuộc run ĐÃ HOÀN TẤT (cr.status='completed') VÀ item thành công
    (cri.status='success') - đúng contract đã PASS ở file 12 mục eligibility. KHÔNG được chỉ lọc
    theo item status - 1 run đang 'running' vẫn có thể có item đã 'success' (worker xử lý tuần tự),
    nhưng dữ liệu của run đó chưa chốt, không được dùng làm context/temporal evidence hay bị ghi cờ
    (GPT review file 15 MAJOR 1 - lỗi thật, đã ghi is_anomaly cho 121 row của 1 run chưa xong).

    Gồm cả sold-out/giá NULL/room-key NULL (sẽ thành not_applicable), không chỉ tập đã lọc hợp lệ -
    để --apply reset đúng is_anomaly cho MỌI record trong phạm vi, không bỏ sót record nào (đúng
    clarification #3, file 12)."""
    cursor.execute(
        """
        SELECT po.record_id, po.hotel_id, po.room_identity_key, po.crawl_run_item_id,
               po.observed_at, po.checkin_date, po.price_per_night, po.is_sold_out
        FROM price_observations po
        JOIN crawl_run_items cri ON cri.id = po.crawl_run_item_id
        JOIN crawl_runs cr ON cr.id = cri.crawl_run_id
        WHERE cr.status = 'completed' AND cri.status = 'success'
        """
    )
    return cursor.fetchall()


def compute_decisions(rows: list[dict]) -> tuple[dict[int, tuple[str, Optional[str]]], dict[str, int]]:
    """Trả (record_id -> (status, reason), status -> count). Pure - không đụng DB, dễ test."""
    decisions: dict[int, tuple[str, Optional[str]]] = {}
    counts: dict[str, int] = defaultdict(int)

    evaluable_rows = []
    for r in rows:
        if r["is_sold_out"] or r["price_per_night"] is None or r["room_identity_key"] is None:
            decisions[r["record_id"]] = ("not_applicable", None)
            counts["not_applicable"] += 1
        else:
            evaluable_rows.append(r)

    # item -> room_key -> [(record_id, price)]
    item_room_records: dict[int, dict[str, list[tuple[int, Decimal]]]] = defaultdict(lambda: defaultdict(list))
    item_meta: dict[int, tuple[str, date, datetime]] = {}
    for r in evaluable_rows:
        iid = r["crawl_run_item_id"]
        price = Decimal(str(r["price_per_night"]))
        item_room_records[iid][r["room_identity_key"]].append((r["record_id"], price))
        item_meta[iid] = (r["hotel_id"], r["checkin_date"], r["observed_at"])

    representative: dict[tuple[int, str], Decimal] = {}
    for iid, room_map in item_room_records.items():
        for room_key, entries in room_map.items():
            representative[(iid, room_key)] = median(p for _, p in entries)

    hotel_items: dict[str, list[tuple[int, date, datetime]]] = defaultdict(list)
    for iid, (hotel_id, checkin_date, observed_at) in item_meta.items():
        hotel_items[hotel_id].append((iid, checkin_date, observed_at))
    for hotel_id in hotel_items:
        hotel_items[hotel_id].sort(key=lambda t: (t[2], t[0]))  # observed_at asc, item_id tie-break

    # 2 lich su TACH BIET, dung muc dich khac nhau (GPT review file 17 MAJOR - loi thuc, 62/99
    # high_price_outlier truoc do sai vi tron checkin_date):
    #  - temporal_history: CHI cung (hotel_id, room_key, checkin_date) - dung cho nhanh "spike so
    #    voi chinh no", PHAI giu nguyen checkin_date, chi dich observed_at, dung nguyen tac
    #    CLAUDE.md muc 5.3 (lag/rolling khong duoc tron cac ngay check-in khac nhau).
    #  - persistence_history: (hotel_id, room_key) XUYEN moi checkin_date - dung cho nhanh "lap lai
    #    o nhieu ngay luu tru khac nhau", day la dinh nghia cua chinh no nen tron checkin_date la
    #    CHU DICH, khong phai bug.
    temporal_history: dict[tuple[str, str, date], list[HistoryEntry]] = defaultdict(list)
    persistence_history: dict[tuple[str, str], list[HistoryEntry]] = defaultdict(list)

    for hotel_id, items in hotel_items.items():
        for iid, checkin_date, observed_at in items:
            room_keys_in_item = list(item_room_records[iid].keys())
            vn_obs_date = _vn_date(observed_at)

            for room_key in room_keys_in_item:
                other_reps = [representative[(iid, rk)] for rk in room_keys_in_item if rk != room_key]
                context_room_count = len(other_reps)
                context_median = median(other_reps) if context_room_count >= CONFIG["context_min_other_room_keys"] else None

                rep_price = representative[(iid, room_key)]
                rep_ratio = (rep_price / context_median) if context_median else None

                temporal_key = (hotel_id, room_key, checkin_date)
                prior_temporal = [h for h in temporal_history[temporal_key] if h.observed_at < observed_at]
                distinct_items_prior = len({h.item_id for h in prior_temporal})
                distinct_dates_prior = len({h.vn_observation_date for h in prior_temporal})
                temporal_sufficient = (
                    distinct_items_prior >= CONFIG["temporal_min_distinct_items"]
                    and distinct_dates_prior >= CONFIG["temporal_min_distinct_dates"]
                )
                temporal_median = median(h.representative_price for h in prior_temporal) if temporal_sufficient else None

                persistence_key = (hotel_id, room_key)
                prior_persistence = [h for h in persistence_history[persistence_key] if h.observed_at < observed_at]
                persistence_pool = [h for h in prior_persistence if h.representative_context_ratio is not None
                                     and h.representative_context_ratio >= CONFIG["high_context_ratio"]]
                if rep_ratio is not None and rep_ratio >= CONFIG["high_context_ratio"]:
                    persistence_items = {h.item_id for h in persistence_pool} | {iid}
                    persistence_checkins = {h.checkin_date for h in persistence_pool} | {checkin_date}
                else:
                    persistence_items = {h.item_id for h in persistence_pool}
                    persistence_checkins = {h.checkin_date for h in persistence_pool}
                persistence_ok = (
                    len(persistence_items) >= CONFIG["persistence_min_distinct_items"]
                    and len(persistence_checkins) >= CONFIG["persistence_min_distinct_checkins"]
                )

                for record_id, price in item_room_records[iid][room_key]:
                    status, reason = apply_rule(
                        price, context_median, context_room_count,
                        temporal_median, temporal_sufficient, persistence_ok,
                    )
                    decisions[record_id] = (status, reason)
                    counts[status] += 1

                entry = HistoryEntry(
                    item_id=iid, observed_at=observed_at, vn_observation_date=vn_obs_date,
                    checkin_date=checkin_date, representative_price=rep_price,
                    context_median=context_median, representative_context_ratio=rep_ratio,
                )
                temporal_history[temporal_key].append(entry)
                persistence_history[persistence_key].append(entry)

    return decisions, dict(counts)


_INSERT_CHUNK_SIZE = 5000


def _apply(cursor, decisions: dict[int, tuple[str, Optional[str]]]) -> int:
    """UPDATE...JOIN qua temporary table - tránh NOT IN(<list dai>), verify count truoc commit
    (clarification #3, file 12). Insert theo chunk co dinh, khong dua toan bo vao 1 executemany
    (GPT review file 15 MINOR 2 - tranh cham max_allowed_packet khi du lieu tang len vai trieu row)."""
    cursor.execute(
        "CREATE TEMPORARY TABLE anomaly_decisions (record_id BIGINT PRIMARY KEY, confirmed BOOLEAN NOT NULL)"
    )
    try:
        rows = [(rid, status == "confirmed") for rid, (status, _reason) in decisions.items()]
        for start in range(0, len(rows), _INSERT_CHUNK_SIZE):
            chunk = rows[start:start + _INSERT_CHUNK_SIZE]
            cursor.executemany(
                "INSERT INTO anomaly_decisions (record_id, confirmed) VALUES (%s,%s)", chunk
            )
        cursor.execute("SELECT COUNT(*) AS n FROM anomaly_decisions")
        populated = cursor.fetchone()
        populated_n = populated["n"] if isinstance(populated, dict) else populated[0]
        if populated_n != len(rows):
            raise RuntimeError(
                f"Temp table populated {populated_n} row nhung decisions co {len(rows)} - dung, khong apply."
            )
        cursor.execute(
            """
            UPDATE price_observations po
            JOIN anomaly_decisions d ON d.record_id = po.record_id
            SET po.is_anomaly = d.confirmed
            WHERE po.is_anomaly <> d.confirmed
            """
        )
        updated = cursor.rowcount
    finally:
        cursor.execute("DROP TEMPORARY TABLE IF EXISTS anomaly_decisions")
    return updated


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Ghi is_anomaly thật; mặc định chỉ dry-run in báo cáo.")
    args = parser.parse_args()

    with get_db_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        rows = _load_scope(cursor)
        decisions, counts = compute_decisions(rows)

        print(f"Method version: {CONFIG['method_version']}")
        print(f"Tổng record trong phạm vi (run completed + item success): {len(rows)}")
        for status in STATUSES:
            print(f"  {status}: {counts.get(status, 0)}")

        if len(decisions) != len(rows):
            print(f"CẢNH BÁO: decisions ({len(decisions)}) != rows quét được ({len(rows)}) - dừng, không apply.")
            cursor.close()
            raise SystemExit(1)

        if not args.apply:
            print("\nDry run - không ghi gì. Chạy lại với --apply để cập nhật is_anomaly thật.")
            cursor.close()
            return

        try:
            updated = _apply(cursor, decisions)
        except Exception:
            conn.rollback()
            cursor.close()
            raise
        conn.commit()
        cursor.close()
        print(f"\nĐã UPDATE is_anomaly cho {updated} record (chỉ những record đổi giá trị).")


if __name__ == "__main__":
    main()
