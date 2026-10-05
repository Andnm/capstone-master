"""Data dictionary cua `samples.parquet` (CLAUDE.md muc 6.4/mdc 4.11 - bao cao dataset): ten cot, nhom, kieu, mo ta, cong thuc,
ghi chu leakage. Sinh tu cung registry de khong the lech voi cot thuc te (test kiem tung cot Parquet co dong trong dictionary)."""
from __future__ import annotations

from .feature_spec import HORIZONS

_ID = "identifier"

_STATIC: dict[str, tuple[str, str, str, str]] = {
    # ten: (nhom, mo ta, cong thuc/nguon, ghi chu leakage)
    "dataset_version": (_ID, "Dinh danh build tai lap duoc", "dataset_build_manifests.dataset_version", "khong phai feature"),
    "hotel_id": (_ID, "Khach san (slug Booking)", "ml_reference_assignments.hotel_id", "dinh danh; khong dua vao model neu muon suy rong ngoai cohort"),
    "checkin_date": (_ID, "Ngay nhan phong cua chuoi", "assignment.checkin_date", "khoa chuoi"),
    "canonical_series_id": (_ID, "Frozen series (hotel, check-in, phong, rate plan)", "hash canonical (spec muc 9)", "khoa chuoi"),
    "vn_observation_date": (_ID, "Ngay quan sat theo gio Viet Nam", "DATE(observed_at + 7h)", "moc thoi gian cua mau"),
    "prediction_time": (_ID, "Thoi diem quan sat (UTC)", "price_observations.observed_at", "moc thoi gian cua mau"),
    "split": (_ID, "train/validation/test; rong = vung purge", "spec muc 15", "khong phai feature"),
    "hotel_seen_in_train": (_ID, "Khach san co mau o train", "any(train)", "dung loc primary val/test"),
    "warehouse_record_id": (_ID, "ID ky thuat cua observation trong warehouse", "price_observations.record_id", "KHONG dung lam feature; loai khoi checksum noi dung"),
    "current_price": ("current", "Gia/dem hien tai (VND)", "price_per_night cua snapshot", "co san luc du bao"),
    "day_of_week": ("calendar", "Thu trong tuan cua ngay check-in (Mon=0)", "checkin_date", "lich cong bo truoc"),
    "is_weekend": ("calendar", "Check-in thu 6 hoac thu 7", "weekday in (4,5)", "lich"),
    "month": ("calendar", "Thang check-in", "checkin_date", "lich"),
    "week_of_year": ("calendar", "Tuan ISO check-in", "isocalendar", "lich"),
    "quarter": ("calendar", "Quy check-in", "(month-1)//3+1", "lich"),
    "is_public_holiday": ("calendar", "Ngay le (national hoac thanh pho)", "vn_holidays.event_type=public_holiday", "lich (co the provisional)"),
    "is_holiday_eve": ("calendar", "Ngay truoc le", "ngay+1 la public_holiday", "lich"),
    "days_to_nearest_holiday": ("calendar", "Khoang cach ngay toi le gan nhat (hai phia)", "min |holiday-checkin|", "lich"),
    "is_tet_period": ("calendar", "Giai doan Tet Nguyen dan", "vn_holidays.is_tet", "lich"),
    "is_festival_period": ("calendar", "Le hoi/su kien lon tai thanh pho", "event_type festival|major_event", "lich (co the provisional)"),
    "lead_time": ("lead_time", "So ngay tu ngay quan sat VN toi check-in", "checkin_date - vn_observation_date", "tinh lai, khong dung cot luu"),
    "lead_time_bucket": ("lead_time", "Nhom lead time", "lt3,3-7,7-14,14-30,30-60,gt60", ""),
    "is_last_minute": ("lead_time", "lead_time <= 3", "", ""),
    "city": ("static", "Thanh pho cua khach san", "hotels.city", "snapshot cuoi ky nhung thanh pho khong doi"),
    "max_occupancy": ("static", "Suc chua toi da cua phong tham chieu", "assignment (dong bang)", "on dinh theo series"),
    "room_area_m2": ("static", "Dien tich phong (m2)", "parse assignment.room_area", "on dinh theo series"),
    "breakfast_included": ("static", "Gom bua sang (luu y N1: 5 hotel co co sai tu parser)", "assignment (dong bang)", "N1 chua sua o ban 1"),
    "free_cancellation": ("static", "Huy mien phi", "assignment (dong bang)", "on dinh theo series"),
    "price_lag_1": ("history", "Gia snapshot 1 ngay truoc (NULL neu khong co)", "mau chon cung series o d-1", "causal"),
    "price_lag_3": ("history", "Gia snapshot 3 ngay truoc", "d-3", "causal"),
    "price_lag_7": ("history", "Gia snapshot 7 ngay truoc", "d-7", "causal"),
    "price_lag_14": ("history", "Gia snapshot 14 ngay truoc", "d-14", "causal"),
    "price_rolling_mean_7": ("history", "TB gia 7 ngay truoc (mau co san)", "mean prices d-7..d-1", "causal"),
    "price_rolling_mean_14": ("history", "TB gia 14 ngay truoc", "d-14..d-1", "causal"),
    "price_rolling_std_7": ("history", "Do lech chuan mau 7 ngay truoc (>=2 diem)", "std ddof=1", "causal"),
    "price_velocity": ("history", "(gia - gia snapshot truoc)/gia snapshot truoc", "snapshot lien truoc", "causal"),
    "price_min_trailing_14": ("history", "Gia thap nhat 14 ngay truoc", "min d-14..d-1", "causal"),
    "price_max_trailing_14": ("history", "Gia cao nhat 14 ngay truoc", "max d-14..d-1", "causal"),
    "price_vs_own_mean_30": ("history", "Gia hien tai / TB 30 ngay truoc", "ratio", "causal"),
    "days_since_last_price_change": ("history", "So ngay tu lan doi gia gan nhat (censored = tu mau dau)", "", "causal"),
    "history_observation_count": ("history", "So snapshot da chon truoc do cua series", "", "causal"),
    "history_span_days": ("history", "So ngay tu snapshot dau cua series", "", "causal"),
    "rooms_left": ("listing_signals", "Con bao nhieu phong (neu hien thi)", "price_observations.rooms_left", "co san luc du bao; nhom ablation"),
    "discount_percent": ("listing_signals", "% giam gia niem yet", "price_observations.discount_percent", "co san luc du bao; nhom ablation"),
    "inference_mode": ("mode", "cold_start neu history_observation_count <= nguong, nguoc lai history_enriched", "feature_config.cold_start_max_history", ""),
}


def dictionary_rows(columns: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    labels = {}
    for k in HORIZONS:
        labels[f"has_label_h{k}"] = ("label", f"Co nhan h{k} (target cung series o t+{k})", f"ml_samples.has_label_h{k}", "nhan")
        labels[f"label_usable_h{k}"] = ("label", f"Nhan h{k} dung duoc: target cung split voi mau", "has_label & split khop", "dung loc luc train/eval")
        labels[f"y_price_h{k}"] = ("label", f"Gia muc tieu t+{k} (VND)", "price_per_night cua target", "muc tieu")
        labels[f"y_delta_h{k}"] = ("label", f"Chenh lech gia h{k}", "y_price - current_price", "muc tieu")
        labels[f"y_pct_change_h{k}"] = ("label", f"% doi gia h{k}", "y_delta/current_price", "muc tieu")
        labels[f"y_direction_h{k}"] = ("label", f"Huong (up/down/stable, nguong +-2%) h{k}", "y_pct_change", "muc tieu phu")
    for name in columns:
        group, description, formula, leakage = {**_STATIC, **labels}[name]
        rows.append({"column": name, "group": group, "description": description, "formula_or_source": formula, "leakage_note": leakage})
    return rows
