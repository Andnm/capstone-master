"""Hop dong feature/label v1 dang DU LIEU THUAN (chua co logic tinh) - nam trong `build_config_json` nen
doi bat ky dong nao o day = `feature_config_sha256` moi = `dataset_version` moi.

Nguon: CLAUDE.md muc 5 (feature contract v1), spec muc 14 ("Gioi han feature v1"), muc 1 (cam tuyet doi).
Feature v1 chi dung: lich, city, lead time, thuoc tinh phong/rate plan, lich su gia CAUSAL cua chinh chuoi
(chi tu mau da chon, tu `approved_at` tro di), `vn_holidays`. Khong weather/tourism/review snapshot/compset.
"""
from __future__ import annotations

HORIZONS = (1, 3, 7, 14)

LABEL_VERSION = "labels-v1.0.0"
# 1.1.0 (GPT review vong 1 DB-M2/DB-M4): thay co chung `hotel_seen_in_train` bang `hotel_seen_in_train_h{k}` (CHI tinh tu mau train
# `label_usable_hk` cua dung horizon) va sua danh sach cot dinh danh cho khop Parquet that (nguon su that DUY NHAT cho `output_columns`).
# 1.2.0 (GPT file 48/52): them cot AUDIT `prediction_match_status` / `label_match_status_h{k}` (exact|alias cua sample / cua target; NULL khi khong co nhan) o nhom dinh danh
# - de bao cao strata exact-only sensitivity, KHONG BAO GIO la feature (trang thai khop cua target la thong tin tuong lai) - va ghim `category_domains` vao config.
FEATURE_VERSION = "features-v1.2.0"

# Co 'hotel co mau train dung duoc o horizon k' - dinh danh/audit, KHONG phai feature; training tu suy lai tu tap train cua horizon.
HOTEL_SEEN_COLUMNS = tuple(f"hotel_seen_in_train_h{k}" for k in HORIZONS)

# Cot audit strata nam TRONG Parquet nhung CAM lam feature (khac `FORBIDDEN_FEATURES`: nhom do KHONG duoc co mat trong Parquet).
AUDIT_MATCH_COLUMNS = ("prediction_match_status", *(f"label_match_status_h{k}" for k in HORIZONS))

# Cot dinh danh/metadata - KHONG phai feature (khong dua vao model, dung de join/chia/audit). Thu tu = thu tu cot dau cua Parquet;
# `features.output_columns()` doc DANH SACH NAY tu `config['feature_config']['identifier_columns']`, khong con ban hard-code thu hai.
IDENTIFIER_COLUMNS = (
    "dataset_version", "hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date",
    "prediction_time", "split", *HOTEL_SEEN_COLUMNS, *AUDIT_MATCH_COLUMNS, "warehouse_record_id",
)

# Nhom feature -> cot. Thu tu cot la thu tu xuat Parquet.
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "current": ("current_price",),
    "calendar": (
        "day_of_week", "is_weekend", "month", "week_of_year", "quarter",
        "is_public_holiday", "is_holiday_eve", "days_to_nearest_holiday", "is_tet_period", "is_festival_period",
    ),
    "lead_time": ("lead_time", "lead_time_bucket", "is_last_minute"),
    "static": ("city", "max_occupancy", "room_area_m2", "breakfast_included", "free_cancellation"),
    "history": (
        "price_lag_1", "price_lag_3", "price_lag_7", "price_lag_14",
        "price_rolling_mean_7", "price_rolling_mean_14", "price_rolling_std_7", "price_velocity",
        "price_min_trailing_14", "price_max_trailing_14", "price_vs_own_mean_30", "days_since_last_price_change",
        "history_observation_count", "history_span_days",
    ),
    "listing_signals": ("rooms_left", "discount_percent"),
    "mode": ("inference_mode",),
}

# Tin hieu tai thoi diem du bao, khong thuoc contract v1 viet o CLAUDE.md muc 5 -> tach nhom rieng de
# lam ablation; van la thong tin co san luc crawl (khong leak).
OPTIONAL_GROUPS = ("listing_signals",)

# Cam tuyet doi lam feature (spec muc 1).
FORBIDDEN_FEATURES = (
    "source_code", "record_id", "crawl_run_id", "crawl_run_item_id", "ml_reference_assignment_id", "worker_id",
    "host_name", "artifact_html_path", "screenshot_path", "driver_start_ms", "page_load_ms", "parse_ms",
    "db_write_ms", "item_total_ms", "hotel_link", "review_score", "review_count", "amenities",
)

LEAD_TIME_BUCKETS = ((0, 2, "lt3"), (3, 6, "3-7"), (7, 13, "7-14"), (14, 29, "14-30"), (30, 59, "30-60"), (60, 10**6, "gt60"))

# Domain danh muc CO DINH theo protocol (GPT file 52 muc 4): training ma hoa cay bang mapping nay, KHONG hoc tu du lieu (hoc tu train/fold = hoc covariate tuong lai cua CV).
# Nguon duy nhat: samples/sufficiency/training deu import tu day. Endpoint bucket: lt3=[0,3) 3-7=[3,7) 7-14=[7,14) 14-30=[14,30) 30-60=[30,60) gt60=[60,inf).
CITIES = ("Hồ Chí Minh", "Hà Nội", "Vũng Tàu", "Đà Lạt", "Phú Quốc")
INFERENCE_MODES = ("cold_start", "history_enriched")
CATEGORY_DOMAINS = {"city": CITIES, "lead_time_bucket": tuple(label for _, _, label in LEAD_TIME_BUCKETS), "inference_mode": INFERENCE_MODES}


def label_config() -> dict:
    return {
        "version": LABEL_VERSION,
        "horizons_days": list(HORIZONS),
        "target": "price_per_night cua daily snapshot da chon o vn_observation_date + k, cung frozen series",
        "calendar": "vn_observation_date theo Asia/Ho_Chi_Minh (UTC+07:00)",
        "direction_threshold": 0.02,
        "label_usable_rule": "target sample cung split voi source sample (khong xoa has_label, chi loc luc dung)",
        "daily_snapshot_tiebreak": [
            "ownership_status=owner_success", "qua tang 2", "observed_at som hon trong ngay VN",
            "source_code", "source_run_id", "source_item_id",
        ],
    }


def feature_config() -> dict:
    return {
        "version": FEATURE_VERSION,
        "groups": {name: list(columns) for name, columns in FEATURE_GROUPS.items()},
        "optional_groups": list(OPTIONAL_GROUPS),
        "identifier_columns": list(IDENTIFIER_COLUMNS),
        "forbidden_features": list(FORBIDDEN_FEATURES),
        "audit_only_columns": list(AUDIT_MATCH_COLUMNS),
        "category_domains": {name: list(values) for name, values in CATEGORY_DOMAINS.items()},
        "lags_days": [1, 3, 7, 14],
        "rolling_windows_days": [7, 14, 30],
        "lead_time_buckets": [[lo, hi, name] for lo, hi, name in LEAD_TIME_BUCKETS],
        "cold_start_max_history": 2,
        "history_source": "chi mau da chon (is_daily_snapshot_selected) cua cung frozen series, vn_observation_date < ngay du bao",
        "missing_history_policy": "NULL (khong dien)",
        "lag_before_approved_at": "khong dung (mau chi ton tai tu approved_at)",
    }
