-- =====================================================
-- HOTEL PRICE INTELLIGENCE - DATABASE SCHEMA (v2, mới hoàn toàn)
-- =====================================================
-- DB name cố tình khác với DB cũ (`hotel_scraper`) để không đụng tới project cũ.
-- Chạy: mysql -u root -p < setup.sql
-- =====================================================

CREATE DATABASE IF NOT EXISTS hotel_price_intel
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE hotel_price_intel;

-- ---------------------------------------------------
-- hotels: thuộc tính khách sạn (gần như tĩnh, refresh định kỳ)
-- hotel_id = slug lấy từ URL Booking (vd: serenity-near-tan-son-nhat-airport)
-- Vì scope hiện tại chỉ 1 OTA (Booking.com), hotel_id == ota_hotel_id, không cần bảng mapping.
-- KHÔNG có cột star_rating riêng — dùng review_score (đã cào sẵn) để phân nhóm compset thay thế.
-- Giữ bảng gọn cho đúng feature set hiện tại: address/city/review/amenities là đủ cho Phase 3.
-- ---------------------------------------------------
CREATE TABLE hotels (
  hotel_id              VARCHAR(255) PRIMARY KEY,   -- slug từ URL Booking
  name                  VARCHAR(500) NOT NULL,
  name_normalized       VARCHAR(500),               -- bỏ dấu, lowercase (fuzzy match sau này nếu cần)
  hotel_link            TEXT NOT NULL,               -- URL gốc đã làm sạch (bỏ tracking params)
  address               TEXT,                        -- field vị trí chính
  city                  VARCHAR(100),                -- suy ra từ address hoặc sheet/market trong Excel
  review_score          DECIMAL(3,1),                -- thang 10, dùng để phân nhóm compset
  review_count          INT,
  amenities             JSON,                        -- top ~9 tiện nghi phổ biến (không đầy đủ)
  booking_status        ENUM('active','not_bookable','not_listed') NOT NULL DEFAULT 'active',
  booking_status_reason VARCHAR(500),
  booking_status_checked_at DATETIME NULL,
  attributes_updated_at TIMESTAMP NULL,
  created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_hotels_city (city),
  INDEX idx_hotels_booking_status (booking_status),
  INDEX idx_hotels_city_review (city, review_score)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------
-- crawl_runs: log + trạng thái hàng đợi cho mỗi lần chạy (thủ công hoặc cron)
-- ---------------------------------------------------
CREATE TABLE crawl_runs (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  status            ENUM('queued','running','completed','failed') NOT NULL DEFAULT 'queued',
  trigger_type      ENUM('scheduled','manual') NOT NULL DEFAULT 'manual',
  source_file       VARCHAR(500),               -- đường dẫn file Excel đã upload (NULL nếu scheduled dùng list mặc định)
  source_original_filename VARCHAR(500),
  source_file_sha256 CHAR(64),
  source_file_size BIGINT,
  save_artifacts    BOOLEAN NOT NULL DEFAULT FALSE,
  crawl_context     JSON,
  scraper_version   VARCHAR(50),
  selector_version  VARCHAR(50),
  git_commit        VARCHAR(64),
  storage_timezone  VARCHAR(64) NOT NULL DEFAULT 'UTC',
  retry_of_run_id   BIGINT,
  date_mode         ENUM('lead_time','explicit') NOT NULL DEFAULT 'lead_time',
  lead_time_buckets VARCHAR(100),               -- dùng khi date_mode='lead_time', vd "1,3,7,14,30,60"
  checkin_dates     JSON,                       -- dùng khi date_mode='explicit', vd ["2026-11-30","2027-01-15"]
  total             INT NOT NULL DEFAULT 0,
  processed         INT NOT NULL DEFAULT 0,
  success_count     INT NOT NULL DEFAULT 0,
  partial_count     INT NOT NULL DEFAULT 0,
  sold_out_count    INT NOT NULL DEFAULT 0,
  not_bookable_count INT NOT NULL DEFAULT 0,
  error_count       INT NOT NULL DEFAULT 0,
  started_at        TIMESTAMP NULL,
  finished_at       TIMESTAMP NULL,
  error_message     TEXT,
  created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_runs_status (status),
  INDEX idx_runs_created (created_at DESC),
  FOREIGN KEY (retry_of_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------
-- crawl_run_items: 1 dòng = 1 lần thử cào (1 khách sạn x 1 ngày checkin) trong 1 run, dùng để
-- hiển thị bảng "link nào thành công, link nào lỗi" ở trang chi tiết job trên frontend.
-- ---------------------------------------------------
CREATE TABLE crawl_run_items (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  crawl_run_id    BIGINT NOT NULL,
  source_hotel_link TEXT NOT NULL,
  requested_hotel_link TEXT NULL,             -- URL đã ép đúng checkin/checkout trước khi mở Selenium
  source_link_hash CHAR(64) NOT NULL,
  hotel_link      TEXT NOT NULL,             -- URL THỰC TẾ Selenium đã mở
  hotel_name_hint VARCHAR(500),              -- tên ở cột A file Excel (chưa chắc đúng tên thật)
  market_hint     VARCHAR(100),
  hotel_name      VARCHAR(500),              -- tên thật cào được (NULL nếu lỗi trước khi lấy được tên)
  hotel_id        VARCHAR(255),              -- điền vào nếu resolve thành công (slug thật)
  checkin_date    DATE NOT NULL,
  checkout_date   DATE NOT NULL,
  status          ENUM('queued','running','success','partial','sold_out','not_bookable','error') NOT NULL DEFAULT 'queued',
  attempt_count   INT NOT NULL DEFAULT 0,
  claimed_at      DATETIME NULL,
  heartbeat_at    DATETIME NULL,
  finished_at     DATETIME NULL,
  worker_id       VARCHAR(100),
  last_error_code VARCHAR(50),
  next_retry_at   DATETIME NULL,
  dom_room_row_count  INT NOT NULL DEFAULT 0,
  candidate_rate_count INT NOT NULL DEFAULT 0,
  parsed_options_count INT NOT NULL DEFAULT 0,
  rejected_options_count INT NOT NULL DEFAULT 0,
  duplicate_options_count INT NOT NULL DEFAULT 0,
  raw_options_count   INT NOT NULL DEFAULT 0,
  saved_options_count INT NOT NULL DEFAULT 0,
  parse_warning_count INT NOT NULL DEFAULT 0,
  rejected_options JSON,
  reference_match_status ENUM('calibrating','exact','alias','unavailable','ambiguous','not_applicable') NOT NULL DEFAULT 'calibrating',
  dead_link_confirmation JSON NULL,          -- evidence probe lan 2 (verdict, URL, error code...) khi status lien quan dead_link
  driver_start_ms INT,
  page_load_ms INT,
  availability_wait_ms INT,
  parse_ms INT,
  db_write_ms INT,
  item_total_ms INT,
  artifact_html_path VARCHAR(1000),
  screenshot_path VARCHAR(1000),
  error_message   TEXT,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
  INDEX idx_run_items_run (crawl_run_id),
  INDEX idx_run_items_claim (status, next_retry_at, created_at),
  INDEX idx_run_items_source (crawl_run_id, source_link_hash),
  UNIQUE KEY uq_run_item (crawl_run_id, source_link_hash, checkin_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE crawler_workers (
  worker_id       VARCHAR(100) PRIMARY KEY,
  status          ENUM('online','waiting_network','stopping','offline') NOT NULL DEFAULT 'online',
  started_at      DATETIME NOT NULL,
  heartbeat_at    DATETIME NOT NULL,
  current_item_id BIGINT,
  scraper_version VARCHAR(50),
  host_name       VARCHAR(255),
  process_id      INT,
  status_reason   VARCHAR(500),
  paused_at       DATETIME,
  next_probe_at   DATETIME,
  network_failure_count INT NOT NULL DEFAULT 0,
  INDEX idx_worker_heartbeat (heartbeat_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------
-- hotel_link_health: streak dead_link theo source_link_hash - KHÔNG theo hotel_id, vì một link có
-- thể chết ngay từ lần cào đầu tiên (chưa từng parse thành công -> chưa có row trong `hotels`).
-- Tách khỏi `hotels` để không phụ thuộc row đó tồn tại và không làm bẩn dimension table bằng các
-- link chưa từng crawl được. hotel_id ở đây chỉ là best-effort suy từ URL (extract_hotel_slug),
-- không FK sang hotels.
-- ---------------------------------------------------
CREATE TABLE hotel_link_health (
  source_link_hash            CHAR(64) PRIMARY KEY,
  hotel_id                    VARCHAR(255) NULL,
  source_hotel_link           TEXT NOT NULL,
  consecutive_dead_link_days  INT NOT NULL DEFAULT 0,
  dead_link_streak_started_on DATE NULL,
  dead_link_last_confirmed_on DATE NULL,
  dead_link_review_required   BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at                  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_link_health_review (dead_link_review_required),
  CONSTRAINT chk_link_health_streak_nonneg CHECK (consecutive_dead_link_days >= 0),
  CONSTRAINT chk_link_health_review_needs_streak CHECK (
    dead_link_review_required = FALSE OR consecutive_dead_link_days >= 3
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE hotel_room_candidates (
  hotel_id             VARCHAR(255) NOT NULL,
  checkin_date         DATE NOT NULL,
  room_identity_key    CHAR(64) NOT NULL,
  rate_plan_key        CHAR(64) NOT NULL,
  room_type_anchor_raw VARCHAR(500) NOT NULL,
  room_type_norm       VARCHAR(100),
  max_occupancy        INT,
  bed_config           VARCHAR(500),
  room_area            VARCHAR(50),
  breakfast_included   BOOLEAN,
  free_cancellation    BOOLEAN,
  observation_count    INT NOT NULL DEFAULT 0,
  distinct_run_count   INT NOT NULL DEFAULT 0,
  distinct_item_count  INT NOT NULL DEFAULT 0,
  eligible_item_count  INT NOT NULL DEFAULT 0,
  item_coverage        DECIMAL(5,4) NOT NULL DEFAULT 0,
  first_seen_at        DATETIME NOT NULL,
  last_seen_at         DATETIME NOT NULL,
  aliases              JSON,
  PRIMARY KEY (hotel_id, checkin_date, room_identity_key, rate_plan_key),
  FOREIGN KEY (hotel_id) REFERENCES hotels(hotel_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE hotel_reference_rooms (
  id                   BIGINT AUTO_INCREMENT PRIMARY KEY,
  hotel_id             VARCHAR(255) NOT NULL,
  checkin_date         DATE NOT NULL,
  room_identity_key    CHAR(64) NOT NULL,
  rate_plan_key        CHAR(64) NOT NULL,
  room_type_anchor_raw VARCHAR(500) NOT NULL,
  room_type_norm       VARCHAR(100),
  max_occupancy        INT,
  bed_config           VARCHAR(500),
  room_area            VARCHAR(50),
  breakfast_included   BOOLEAN,
  free_cancellation    BOOLEAN,
  selection_method     ENUM('auto','manual') NOT NULL DEFAULT 'auto',
  status               ENUM('proposed','approved','retired') NOT NULL DEFAULT 'proposed',
  coverage             DECIMAL(5,4) NOT NULL DEFAULT 0,
  confidence_score     DECIMAL(5,4) NOT NULL DEFAULT 0,
  observation_count    INT NOT NULL DEFAULT 0,
  distinct_run_count   INT NOT NULL DEFAULT 0,
  distinct_item_count  INT NOT NULL DEFAULT 0,
  eligible_item_count  INT NOT NULL DEFAULT 0,
  aliases              JSON,
  active_from          DATETIME NULL,
  active_to            DATETIME NULL,
  created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (hotel_id) REFERENCES hotels(hotel_id) ON DELETE CASCADE,
  INDEX idx_reference_hotel_checkin_status (hotel_id, checkin_date, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------
-- price_observations: bản ghi giá thô, 1 dòng = 1 room option tại 1 thời điểm cào
-- Merge info + price trong cùng 1 lần cào (không tách 2 loại như code cũ).
-- Luôn cào đúng 1 đêm (checkout = checkin + 1) => price_total = price_per_night luôn.
-- ---------------------------------------------------
CREATE TABLE price_observations (
  record_id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  hotel_id             VARCHAR(255) NOT NULL,
  crawl_run_id         BIGINT NOT NULL,
  crawl_run_item_id    BIGINT NOT NULL,
  crawl_trigger        ENUM('scheduled','manual') NOT NULL DEFAULT 'manual',

  observed_at          DATETIME NOT NULL,          -- UTC; đổi sang Asia/Ho_Chi_Minh khi hiển thị/feature lịch
  checkin_date         DATE NOT NULL,
  checkout_date        DATE NOT NULL,               -- luôn = checkin_date + 1
  lead_time            INT NOT NULL,                -- checkin_date - DATE(observed_at), tính sẵn lúc insert

  price_total          DECIMAL(15,2),               -- = price_per_night (luôn 1 đêm). NULL nếu sold_out
  price_per_night      DECIMAL(15,2),               -- NULL nếu sold_out
  original_price       DECIMAL(15,2),
  discount_percent     DECIMAL(5,2),
  taxes_fees           DECIMAL(15,2),               -- NULL nếu Booking không tách riêng số tiền
  price_includes_tax   BOOLEAN,                     -- parse từ "Đã bao gồm thuế và phí"

  room_type_raw        TEXT,
  room_type_norm       VARCHAR(100),                -- chuẩn hoá: hạng x sức chứa x breakfast
  room_option_index    INT NOT NULL,                -- thứ tự option trong bảng Booking của lần cào
  room_option_key      CHAR(64) NOT NULL,            -- SHA-256 fingerprint để audit option
  room_identity_key    CHAR(64),
  rate_plan_key        CHAR(64),
  is_reference_room    BOOLEAN NOT NULL DEFAULT FALSE,
  reference_definition_id BIGINT,
  reference_match_status ENUM('calibrating','exact','alias','unavailable','ambiguous','not_reference','not_applicable') NOT NULL DEFAULT 'calibrating',
  reference_match_score DECIMAL(5,4),
  max_occupancy        INT,
  bed_config           TEXT,
  room_area            VARCHAR(50),

  breakfast_included   BOOLEAN,
  free_cancellation    BOOLEAN,
  cancellation_policy  TEXT,
  rooms_left           INT,                         -- parse từ "Chúng tôi còn N căn"

  is_sold_out          BOOLEAN NOT NULL DEFAULT FALSE,
  availability_status  ENUM('available','sold_out','not_listed') NOT NULL DEFAULT 'available',
  is_anomaly           BOOLEAN NOT NULL DEFAULT FALSE,

  created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (hotel_id) REFERENCES hotels(hotel_id) ON DELETE CASCADE,
  FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
  FOREIGN KEY (crawl_run_item_id) REFERENCES crawl_run_items(id) ON DELETE CASCADE,
  FOREIGN KEY (reference_definition_id) REFERENCES hotel_reference_rooms(id) ON DELETE SET NULL,
  UNIQUE KEY uq_dedup (crawl_run_item_id, room_option_index),
  INDEX idx_po_hotel_checkin_observed (hotel_id, checkin_date, observed_at),
  INDEX idx_po_checkin_observed (checkin_date, observed_at),
  INDEX idx_po_reference_room (hotel_id, checkin_date, is_reference_room)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE file_cleanup_logs (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  cleanup_type   ENUM('upload','artifact') NOT NULL,
  file_path      VARCHAR(1000) NOT NULL,
  file_size      BIGINT NOT NULL DEFAULT 0,
  action         ENUM('dry_run','deleted','skipped','error') NOT NULL,
  reason         VARCHAR(500),
  created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_cleanup_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------
-- competitive_sets: định nghĩa nhóm đối thủ tự động (city + review_score/review_count), dùng cho hotelier view
-- Populate ở Phase 3 bằng script so khớp tự động, không nhập tay.
-- ---------------------------------------------------
CREATE TABLE competitive_sets (
  id                    BIGINT AUTO_INCREMENT PRIMARY KEY,
  hotel_id              VARCHAR(255) NOT NULL,       -- khách sạn "gốc"
  competitor_hotel_id   VARCHAR(255) NOT NULL,       -- 1 thành viên trong compset của hotel_id
  created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (hotel_id) REFERENCES hotels(hotel_id) ON DELETE CASCADE,
  FOREIGN KEY (competitor_hotel_id) REFERENCES hotels(hotel_id) ON DELETE CASCADE,
  UNIQUE KEY uq_compset (hotel_id, competitor_hotel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------
-- vn_holidays: lịch lễ/sự kiện Việt Nam (import từ data/vn_holidays.csv ở Phase 0)
-- ---------------------------------------------------
CREATE TABLE vn_holidays (
  holiday_date  DATE NOT NULL,
  event_code    VARCHAR(50) NOT NULL,        -- mã ổn định: tet, national_day, hung_kings, dalat_flower_festival...
  name          VARCHAR(255) NOT NULL,
  event_type    ENUM('public_holiday', 'festival', 'major_event') NOT NULL DEFAULT 'public_holiday',
  scope         ENUM('national', 'city') NOT NULL DEFAULT 'national',
  city          VARCHAR(100),                -- NULL nếu scope='national'; khớp đúng giá trị hotels.city
  is_tet        BOOLEAN NOT NULL DEFAULT FALSE,
  status        ENUM('confirmed', 'provisional') NOT NULL DEFAULT 'confirmed',
  source_url    VARCHAR(500),
  PRIMARY KEY (holiday_date, event_code),     -- 1 ngày có thể có nhiều sự kiện chồng nhau
  KEY idx_vn_holidays_city_date (city, holiday_date),
  KEY idx_vn_holidays_type_date (event_type, holiday_date),
  CONSTRAINT chk_vn_holidays_scope_city CHECK (
    (scope = 'national' AND city IS NULL) OR
    (scope = 'city' AND city IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------
-- weather_data: dự báo/lịch sử thời tiết theo city + date (Open-Meteo, Phase 3)
-- ---------------------------------------------------
CREATE TABLE weather_data (
  city                        VARCHAR(100) NOT NULL,
  weather_date                DATE NOT NULL,
  temperature                 DECIMAL(5,2),
  precipitation_probability   DECIMAL(5,2),
  humidity                    DECIMAL(5,2),
  PRIMARY KEY (city, weather_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------
-- tourism_stats: thống kê lượt khách theo city + tháng (VNAT, Phase 3)
-- ---------------------------------------------------
CREATE TABLE tourism_stats (
  city              VARCHAR(100) NOT NULL,
  stat_month        CHAR(7) NOT NULL,   -- vd '2026-08' (đổi tên khỏi 'year_month' vì đó là từ khoá dành riêng của MySQL)
  tourist_arrivals  INT,
  PRIMARY KEY (city, stat_month)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================================================
-- Anomaly v2 - candidate-signal detector + human-reviewed registry (thay is_anomaly v1 tu dong).
-- Thiet ke chot qua discuss/anomaly-v2-ground-truth/ (PASS FOR DESIGN file 17). price_observations.
-- is_anomaly da co san o tren (schema khong doi) nhung tu day tro di la PROJECTION duoc dong bo boi
-- sync_anomaly_registry.py/reconcile_anomaly_projection.py, khong phai gia tri rule ghi truc tiep.
-- Xem CLAUDE.md muc 4.5 de hieu day du mo hinh. Migration lich su:
-- 20260904_anomaly_v2_registry.sql (8 bang ban dau) + corrective migration sau do (composite FK
-- config_sha256+method_version - MIN1 discuss file 19) - setup.sql duoi day la SCHEMA CUOI CUNG DA
-- GOM CA 2, dung cho fresh install (vd VPS/local_aux lan dau).
-- =====================================================================================

CREATE TABLE anomaly_signal_configs (
  config_sha256   CHAR(64) PRIMARY KEY,
  method_version  VARCHAR(20) NOT NULL,
  config_json     JSON NOT NULL,
  created_at      DATETIME NOT NULL,
  UNIQUE KEY uq_configs_method_version (method_version),
  UNIQUE KEY uq_configs_hash_version (config_sha256, method_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE price_anomaly_signals (
  record_id             BIGINT NOT NULL,
  method_version        VARCHAR(20) NOT NULL,
  signal_code           VARCHAR(40) NOT NULL,
  severity              VARCHAR(20) NOT NULL,
  config_sha256         CHAR(64) NOT NULL,
  record_observed_at    DATETIME NOT NULL,
  evidence_available_at DATETIME NOT NULL,
  computed_at           DATETIME NOT NULL,
  metrics_json          JSON NOT NULL,
  PRIMARY KEY (record_id, method_version, signal_code),
  CONSTRAINT fk_signals_record FOREIGN KEY (record_id)
    REFERENCES price_observations(record_id) ON DELETE CASCADE,
  CONSTRAINT fk_signals_config_composite FOREIGN KEY (config_sha256, method_version)
    REFERENCES anomaly_signal_configs(config_sha256, method_version),
  INDEX idx_signals_signal_code (signal_code),
  INDEX idx_signals_evidence_available (evidence_available_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE anomaly_review_decisions (
  review_id                VARCHAR(64) PRIMARY KEY,
  decision                 ENUM('exclude_from_train','keep_as_valid','needs_review') NOT NULL,
  reason_code               VARCHAR(60) NOT NULL,
  rationale                  TEXT NOT NULL,
  evidence_json               JSON NOT NULL,
  reviewer                     VARCHAR(60) NOT NULL,
  decided_at                    DATETIME NOT NULL,
  state                          ENUM('active','superseded','retracted') NOT NULL DEFAULT 'active',
  member_count                    INT NULL,
  member_checksum                   CHAR(64) NULL,
  superseded_by_review_id             VARCHAR(64) NULL,
  created_at                            DATETIME NOT NULL,
  CONSTRAINT fk_decisions_superseded_by FOREIGN KEY (superseded_by_review_id)
    REFERENCES anomaly_review_decisions(review_id),
  INDEX idx_decisions_state (state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE anomaly_review_members (
  review_id             VARCHAR(64) NOT NULL,
  source_code           VARCHAR(20) NOT NULL,
  source_record_id      BIGINT NOT NULL,
  source_record_sha256  CHAR(64) NOT NULL,
  materialized_at       DATETIME NOT NULL,
  PRIMARY KEY (review_id, source_code, source_record_id),
  CONSTRAINT fk_members_decision FOREIGN KEY (review_id)
    REFERENCES anomaly_review_decisions(review_id) ON DELETE RESTRICT,
  CONSTRAINT fk_members_record FOREIGN KEY (source_record_id)
    REFERENCES price_observations(record_id) ON DELETE RESTRICT,
  CONSTRAINT chk_members_source_code_format CHECK (source_code REGEXP '^[a-z][a-z0-9_]{0,19}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE anomaly_review_resolutions (
  source_code       VARCHAR(20) NOT NULL,
  source_record_id  BIGINT NOT NULL,
  review_id         VARCHAR(64) NOT NULL,
  resolved_at       DATETIME NOT NULL,
  PRIMARY KEY (source_code, source_record_id),
  CONSTRAINT fk_resolutions_member FOREIGN KEY (review_id, source_code, source_record_id)
    REFERENCES anomaly_review_members(review_id, source_code, source_record_id) ON DELETE RESTRICT,
  INDEX idx_resolutions_review (review_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE anomaly_registry_events_applied (
  event_id              VARCHAR(64) PRIMARY KEY,
  sequence_no           INT NOT NULL,
  event_payload_sha256  CHAR(64) NOT NULL,
  action                ENUM('activate','supersede','retract') NOT NULL,
  review_id             VARCHAR(64) NOT NULL,
  member_count          INT NOT NULL DEFAULT 0,
  applied_at            DATETIME NOT NULL,
  UNIQUE KEY uq_events_sequence (sequence_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE anomaly_registry_sync_runs (
  sync_id                       BIGINT AUTO_INCREMENT PRIMARY KEY,
  source_code                   VARCHAR(20) NOT NULL,
  registry_file_sha256          CHAR(64) NOT NULL,
  started_at                    DATETIME NOT NULL,
  finished_at                   DATETIME NULL,
  status                        ENUM('running','success','failed') NOT NULL DEFAULT 'running',
  expected_event_count          INT NOT NULL,
  applied_through_sequence      INT NULL,
  active_resolution_checksum    CHAR(64) NULL,
  anomaly_projection_checksum   CHAR(64) NULL,
  error_message                 TEXT NULL,
  CONSTRAINT chk_sync_runs_source_code_format CHECK (source_code REGEXP '^[a-z][a-z0-9_]{0,19}$'),
  INDEX idx_sync_runs_source_status (source_code, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE anomaly_registry_source_identity (
  id             TINYINT NOT NULL PRIMARY KEY DEFAULT 1,
  source_code    VARCHAR(20) NOT NULL,
  configured_at  DATETIME NOT NULL,
  CONSTRAINT chk_identity_single_row CHECK (id = 1),
  CONSTRAINT chk_identity_source_code_format CHECK (source_code REGEXP '^[a-z][a-z0-9_]{0,19}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SELECT 'hotel_price_intel schema created successfully!' AS message;
