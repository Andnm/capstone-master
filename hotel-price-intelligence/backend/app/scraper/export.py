"""Xuất dữ liệu 1 crawl_run ra Excel — phục vụ gộp nhiều lần cào thành dataset huấn luyện sau này."""
import io
from typing import Any, Dict, List

import openpyxl
from openpyxl.utils import get_column_letter

_COLUMNS = [
    ("hotel_id", "Hotel ID (slug)"),
    ("hotel_name", "Tên khách sạn"),
    ("city", "Thành phố"),
    ("address", "Địa chỉ"),
    ("review_score", "Điểm review"),
    ("review_count", "Số review"),
    ("observed_at", "Thời điểm cào"),
    ("checkin_date", "Checkin"),
    ("checkout_date", "Checkout"),
    ("lead_time", "Lead time (ngày)"),
    ("room_type_raw", "Loại phòng (gốc)"),
    ("room_type_norm", "Loại phòng (chuẩn hoá)"),
    ("is_reference_room", "Phòng tham chiếu"),
    ("price_total", "Giá tổng (VND)"),
    ("price_per_night", "Giá/đêm (VND)"),
    ("original_price", "Giá gốc (VND)"),
    ("discount_percent", "% giảm"),
    ("max_occupancy", "Số khách tối đa"),
    ("bed_config", "Giường"),
    ("room_area", "Diện tích"),
    ("breakfast_included", "Bao gồm bữa sáng"),
    ("free_cancellation", "Huỷ miễn phí"),
    ("cancellation_policy", "Chính sách huỷ"),
    ("rooms_left", "Số phòng còn lại"),
    ("is_sold_out", "Hết phòng"),
    ("availability_status", "Trạng thái"),
    ("is_anomaly", "Bất thường"),
]


def build_run_export_xlsx(run_id: int, rows: List[Dict[str, Any]]) -> bytes:
    """rows: list dict đã JOIN price_observations + hotels cho 1 crawl_run_id (xem repository)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"crawl_run_{run_id}"

    for col_idx, (_, header) in enumerate(_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = openpyxl.styles.Font(bold=True)

    for row_idx, row in enumerate(rows, start=2):
        for col_idx, (key, _) in enumerate(_COLUMNS, start=1):
            value = row.get(key)
            if value is not None and key in ('observed_at', 'checkin_date', 'checkout_date'):
                value = str(value)
            ws.cell(row=row_idx, column=col_idx, value=value)

    for col_idx, (key, header) in enumerate(_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = max(len(header) + 2, 14)

    ws.freeze_panes = "A2"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
