USE hotel_price_intel;

ALTER TABLE crawl_runs
  ADD COLUMN source_original_filename VARCHAR(500) NULL AFTER source_file,
  ADD COLUMN source_file_sha256 CHAR(64) NULL AFTER source_original_filename,
  ADD COLUMN source_file_size BIGINT NULL AFTER source_file_sha256,
  ADD COLUMN save_artifacts BOOLEAN NOT NULL DEFAULT FALSE AFTER source_file_size,
  ADD COLUMN crawl_context JSON NULL AFTER save_artifacts,
  ADD COLUMN scraper_version VARCHAR(50) NULL AFTER crawl_context,
  ADD COLUMN selector_version VARCHAR(50) NULL AFTER scraper_version,
  ADD COLUMN git_commit VARCHAR(64) NULL AFTER selector_version,
  ADD COLUMN storage_timezone VARCHAR(64) NOT NULL DEFAULT 'UTC' AFTER git_commit,
  ADD COLUMN retry_of_run_id BIGINT NULL AFTER storage_timezone,
  ADD CONSTRAINT fk_runs_retry_of FOREIGN KEY (retry_of_run_id) REFERENCES crawl_runs(id) ON DELETE SET NULL;

ALTER TABLE crawl_run_items
  MODIFY COLUMN status ENUM('queued','running','success','partial','sold_out','error') NOT NULL DEFAULT 'queued',
  ADD COLUMN source_hotel_link TEXT NULL AFTER crawl_run_id,
  ADD COLUMN source_link_hash CHAR(64) NULL AFTER source_hotel_link,
  ADD COLUMN market_hint VARCHAR(100) NULL AFTER hotel_name_hint,
  ADD COLUMN checkout_date DATE NULL AFTER checkin_date,
  ADD COLUMN attempt_count INT NOT NULL DEFAULT 0 AFTER status,
  ADD COLUMN claimed_at DATETIME NULL AFTER attempt_count,
  ADD COLUMN heartbeat_at DATETIME NULL AFTER claimed_at,
  ADD COLUMN finished_at DATETIME NULL AFTER heartbeat_at,
  ADD COLUMN worker_id VARCHAR(100) NULL AFTER finished_at,
  ADD COLUMN last_error_code VARCHAR(50) NULL AFTER worker_id,
  ADD COLUMN next_retry_at DATETIME NULL AFTER last_error_code,
  ADD COLUMN dom_room_row_count INT NOT NULL DEFAULT 0 AFTER next_retry_at,
  ADD COLUMN candidate_rate_count INT NOT NULL DEFAULT 0 AFTER dom_room_row_count,
  ADD COLUMN parsed_options_count INT NOT NULL DEFAULT 0 AFTER candidate_rate_count,
  ADD COLUMN rejected_options_count INT NOT NULL DEFAULT 0 AFTER parsed_options_count,
  ADD COLUMN parse_warning_count INT NOT NULL DEFAULT 0 AFTER saved_options_count,
  ADD COLUMN rejected_options JSON NULL AFTER parse_warning_count,
  ADD COLUMN reference_match_status ENUM('calibrating','exact','alias','unavailable','ambiguous','not_applicable') NOT NULL DEFAULT 'calibrating' AFTER rejected_options,
  ADD COLUMN driver_start_ms INT NULL AFTER reference_match_status,
  ADD COLUMN page_load_ms INT NULL AFTER driver_start_ms,
  ADD COLUMN availability_wait_ms INT NULL AFTER page_load_ms,
  ADD COLUMN parse_ms INT NULL AFTER availability_wait_ms,
  ADD COLUMN db_write_ms INT NULL AFTER parse_ms,
  ADD COLUMN item_total_ms INT NULL AFTER db_write_ms,
  ADD COLUMN artifact_html_path VARCHAR(1000) NULL AFTER item_total_ms,
  ADD COLUMN screenshot_path VARCHAR(1000) NULL AFTER artifact_html_path,
  ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER created_at;

UPDATE crawl_run_items
SET source_hotel_link = hotel_link,
    source_link_hash = SHA2(SUBSTRING_INDEX(hotel_link, '?', 1), 256),
    checkout_date = DATE_ADD(checkin_date, INTERVAL 1 DAY),
    parsed_options_count = raw_options_count,
    candidate_rate_count = raw_options_count,
    finished_at = created_at;

ALTER TABLE crawl_run_items
  MODIFY COLUMN source_hotel_link TEXT NOT NULL,
  MODIFY COLUMN source_link_hash CHAR(64) NOT NULL,
  MODIFY COLUMN checkout_date DATE NOT NULL,
  ADD INDEX idx_run_items_claim (status, next_retry_at, created_at),
  ADD INDEX idx_run_items_source (crawl_run_id, source_link_hash),
  ADD UNIQUE KEY uq_run_item (crawl_run_id, source_link_hash, checkin_date);

CREATE TABLE crawler_workers (
  worker_id VARCHAR(100) PRIMARY KEY,
  status ENUM('online','stopping','offline') NOT NULL DEFAULT 'online',
  started_at DATETIME NOT NULL,
  heartbeat_at DATETIME NOT NULL,
  current_item_id BIGINT,
  scraper_version VARCHAR(50),
  host_name VARCHAR(255),
  process_id INT,
  INDEX idx_worker_heartbeat (heartbeat_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE hotel_room_candidates (
  hotel_id VARCHAR(255) NOT NULL,
  room_identity_key CHAR(64) NOT NULL,
  rate_plan_key CHAR(64) NOT NULL,
  room_type_anchor_raw VARCHAR(500) NOT NULL,
  room_type_norm VARCHAR(100),
  max_occupancy INT,
  bed_config VARCHAR(500),
  room_area VARCHAR(50),
  breakfast_included BOOLEAN,
  free_cancellation BOOLEAN,
  observation_count INT NOT NULL DEFAULT 0,
  distinct_run_count INT NOT NULL DEFAULT 0,
  first_seen_at DATETIME NOT NULL,
  last_seen_at DATETIME NOT NULL,
  aliases JSON,
  PRIMARY KEY (hotel_id, room_identity_key, rate_plan_key),
  FOREIGN KEY (hotel_id) REFERENCES hotels(hotel_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE hotel_reference_rooms (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  hotel_id VARCHAR(255) NOT NULL,
  room_identity_key CHAR(64) NOT NULL,
  rate_plan_key CHAR(64) NOT NULL,
  room_type_anchor_raw VARCHAR(500) NOT NULL,
  room_type_norm VARCHAR(100),
  max_occupancy INT,
  bed_config VARCHAR(500),
  room_area VARCHAR(50),
  breakfast_included BOOLEAN,
  free_cancellation BOOLEAN,
  selection_method ENUM('auto','manual') NOT NULL DEFAULT 'auto',
  status ENUM('proposed','approved','retired') NOT NULL DEFAULT 'proposed',
  coverage DECIMAL(5,4) NOT NULL DEFAULT 0,
  confidence_score DECIMAL(5,4) NOT NULL DEFAULT 0,
  observation_count INT NOT NULL DEFAULT 0,
  distinct_run_count INT NOT NULL DEFAULT 0,
  aliases JSON,
  active_from DATETIME NULL,
  active_to DATETIME NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (hotel_id) REFERENCES hotels(hotel_id) ON DELETE CASCADE,
  INDEX idx_reference_hotel_status (hotel_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE price_observations ADD INDEX idx_po_run_fk (crawl_run_id);

ALTER TABLE price_observations
  DROP INDEX uq_dedup,
  ADD COLUMN crawl_run_item_id BIGINT NULL AFTER crawl_run_id,
  ADD COLUMN room_identity_key CHAR(64) NULL AFTER room_option_key,
  ADD COLUMN rate_plan_key CHAR(64) NULL AFTER room_identity_key,
  ADD COLUMN reference_definition_id BIGINT NULL AFTER is_reference_room,
  ADD COLUMN reference_match_status ENUM('calibrating','exact','alias','unavailable','ambiguous','not_reference','not_applicable') NOT NULL DEFAULT 'calibrating' AFTER reference_definition_id,
  ADD COLUMN reference_match_score DECIMAL(5,4) NULL AFTER reference_match_status;

UPDATE price_observations po
JOIN crawl_run_items cri
  ON cri.crawl_run_id = po.crawl_run_id
 AND cri.hotel_id = po.hotel_id
 AND cri.checkin_date = po.checkin_date
SET po.crawl_run_item_id = cri.id;

DELETE FROM price_observations WHERE crawl_run_item_id IS NULL;
UPDATE price_observations SET observed_at = DATE_SUB(observed_at, INTERVAL 7 HOUR);

ALTER TABLE price_observations
  MODIFY COLUMN crawl_run_item_id BIGINT NOT NULL,
  ADD CONSTRAINT fk_po_item FOREIGN KEY (crawl_run_item_id) REFERENCES crawl_run_items(id) ON DELETE CASCADE,
  ADD CONSTRAINT fk_po_reference FOREIGN KEY (reference_definition_id) REFERENCES hotel_reference_rooms(id) ON DELETE SET NULL,
  ADD UNIQUE KEY uq_dedup (crawl_run_item_id, room_option_index);

CREATE TABLE file_cleanup_logs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  cleanup_type ENUM('upload','artifact') NOT NULL,
  file_path VARCHAR(1000) NOT NULL,
  file_size BIGINT NOT NULL DEFAULT 0,
  action ENUM('dry_run','deleted','skipped','error') NOT NULL,
  reason VARCHAR(500),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_cleanup_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
