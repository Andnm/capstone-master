# BAN COPY NGUYEN VAN (khong sua tay) tu `ml/shared/dataset_builder/feature_spec.py`.
# SHA-256 (chuan hoa LF) cua nguon luc copy: 0605a93c940b52443bd44b512e231e88fb1800a813eb68596503e2f7e0f76872
# Sinh/kiem bang `cls_training.upstream_sync` (UPSTREAM_MANIFEST.json). Sua nguon => `verify()` bao drift; muon cap nhat phai sinh lai co chu y.
# Pham vi: HORIZONS, HOTEL_SEEN_COLUMNS, AUDIT_MATCH_COLUMNS, FORBIDDEN_FEATURES, LEAD_TIME_BUCKETS, CITIES, INFERENCE_MODES, CATEGORY_DOMAINS.
from __future__ import annotations


HORIZONS = (1, 3, 7, 14)


HOTEL_SEEN_COLUMNS = tuple(f"hotel_seen_in_train_h{k}" for k in HORIZONS)


AUDIT_MATCH_COLUMNS = ("prediction_match_status", *(f"label_match_status_h{k}" for k in HORIZONS))


FORBIDDEN_FEATURES = (
    "source_code", "record_id", "crawl_run_id", "crawl_run_item_id", "ml_reference_assignment_id", "worker_id",
    "host_name", "artifact_html_path", "screenshot_path", "driver_start_ms", "page_load_ms", "parse_ms",
    "db_write_ms", "item_total_ms", "hotel_link", "review_score", "review_count", "amenities",
)


LEAD_TIME_BUCKETS = ((0, 2, "lt3"), (3, 6, "3-7"), (7, 13, "7-14"), (14, 29, "14-30"), (30, 59, "30-60"), (60, 10**6, "gt60"))


CITIES = ("Hồ Chí Minh", "Hà Nội", "Vũng Tàu", "Đà Lạt", "Phú Quốc")


INFERENCE_MODES = ("cold_start", "history_enriched")


CATEGORY_DOMAINS = {"city": CITIES, "lead_time_bucket": tuple(label for _, _, label in LEAD_TIME_BUCKETS), "inference_mode": INFERENCE_MODES}
