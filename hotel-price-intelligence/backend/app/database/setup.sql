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
-- KHÔNG bắt buộc lat/long — address là field vị trí chính (cào cùng lúc lúc cào giá).
-- ---------------------------------------------------
CREATE TABLE hotels (
  hotel_id              VARCHAR(255) PRIMARY KEY,   -- slug từ URL Booking
  name                  VARCHAR(500) NOT NULL,
  name_normalized       VARCHAR(500),               -- bỏ dấu, lowercase (fuzzy match sau này nếu cần)
  hotel_link            TEXT NOT NULL,               -- URL gốc đã làm sạch (bỏ tracking params)
  address               TEXT,                        -- field vị trí chính
  city                  VARCHAR(100),                -- suy ra từ address hoặc sheet/market trong Excel
  district              VARCHAR(100),                -- suy ra từ address (best-effort)
  latitude              DECIMAL(10,7),               -- optional, best-effort qua JSON-LD
  longitude             DECIMAL(10,7),               -- optional, best-effort qua JSON-LD
  review_score          DECIMAL(3,1),                -- thang 10, dùng để phân nhóm compset
  review_count          INT,
  amenities             JSON,                        -- top ~9 tiện nghi phổ biến (không đầy đủ)
  amenity_count         INT,                          -- parse từ "Xem tất cả N tiện nghi"
  is_chain              BOOLEAN DEFAULT FALSE,
  chain_name            VARCHAR(255),
  distance_to_center    DECIMAL(6,2),                -- NULL nếu không có toạ độ
  distance_to_beach     DECIMAL(6,2),                -- NULL nếu không có toạ độ
  attributes_updated_at TIMESTAMP NULL,
  created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_hotels_city (city),
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
  date_mode         ENUM('lead_time','explicit') NOT NULL DEFAULT 'lead_time',
  lead_time_buckets VARCHAR(100),               -- dùng khi date_mode='lead_time', vd "1,3,7,14,30,60"
  checkin_dates     JSON,                       -- dùng khi date_mode='explicit', vd ["2026-11-30","2027-01-15"]
  total             INT NOT NULL DEFAULT 0,
  processed         INT NOT NULL DEFAULT 0,
  success_count     INT NOT NULL DEFAULT 0,
  error_count       INT NOT NULL DEFAULT 0,
  started_at        TIMESTAMP NULL,
  finished_at       TIMESTAMP NULL,
  error_message     TEXT,
  created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_runs_status (status),
  INDEX idx_runs_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------
-- crawl_run_items: 1 dòng = 1 lần thử cào (1 khách sạn x 1 ngày checkin) trong 1 run, dùng để
-- hiển thị bảng "link nào thành công, link nào lỗi" ở trang chi tiết job trên frontend.
-- ---------------------------------------------------
CREATE TABLE crawl_run_items (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  crawl_run_id    BIGINT NOT NULL,
  hotel_link      TEXT NOT NULL,             -- URL THỰC TẾ Selenium đã mở (đã thay đúng checkin/checkout), không phải link gốc trong Excel
  hotel_name_hint VARCHAR(500),              -- tên ở cột A file Excel (chưa chắc đúng tên thật)
  hotel_name      VARCHAR(500),              -- tên thật cào được (NULL nếu lỗi trước khi lấy được tên)
  hotel_id        VARCHAR(255),              -- điền vào nếu resolve thành công (slug thật)
  checkin_date    DATE NOT NULL,
  status          ENUM('success','sold_out','error') NOT NULL,
  error_message   TEXT,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs(id) ON DELETE CASCADE,
  INDEX idx_run_items_run (crawl_run_id)
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
  crawl_trigger        ENUM('scheduled','manual') NOT NULL DEFAULT 'manual',

  observed_at          DATETIME NOT NULL,          -- thời điểm cào record này (per-record)
  checkin_date         DATE NOT NULL,
  checkout_date        DATE NOT NULL,               -- luôn = checkin_date + 1
  lead_time            INT NOT NULL,                -- checkin_date - DATE(observed_at), tính sẵn lúc insert

  price_total          DECIMAL(15,2),               -- = price_per_night (luôn 1 đêm). NULL nếu sold_out
  price_per_night      DECIMAL(15,2),               -- NULL nếu sold_out
  original_price       DECIMAL(15,2),
  discount_percent     DECIMAL(5,2),

  room_type_raw        TEXT,
  room_type_norm       VARCHAR(100),                -- chuẩn hoá: hạng x sức chứa x breakfast
  is_reference_room    BOOLEAN NOT NULL DEFAULT FALSE,
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
  UNIQUE KEY uq_dedup (hotel_id, checkin_date, observed_at, room_type_norm),
  INDEX idx_po_hotel_checkin_observed (hotel_id, checkin_date, observed_at),
  INDEX idx_po_checkin_observed (checkin_date, observed_at),
  INDEX idx_po_reference_room (hotel_id, checkin_date, is_reference_room)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------
-- competitive_sets: định nghĩa nhóm đối thủ tự động (city+district+review_score), dùng cho hotelier view
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
  event_code    VARCHAR(50) NOT NULL,        -- mã ổn định: tet, national_day, hung_kings, danang_fireworks...
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
    (scope = 'city' AND city IN ('Hồ Chí Minh', 'Hà Nội', 'Đà Nẵng', 'Nha Trang', 'Phú Quốc'))
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

SELECT 'hotel_price_intel schema created successfully!' AS message;
