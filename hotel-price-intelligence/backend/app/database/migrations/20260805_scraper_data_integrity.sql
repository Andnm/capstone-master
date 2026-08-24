USE hotel_price_intel;

-- Gọn hoá thuộc tính hotel theo feature set đã chốt.
ALTER TABLE hotels
  DROP COLUMN district,
  DROP COLUMN latitude,
  DROP COLUMN longitude,
  DROP COLUMN amenity_count,
  DROP COLUMN is_chain,
  DROP COLUMN chain_name,
  DROP COLUMN distance_to_center,
  DROP COLUMN distance_to_beach;

ALTER TABLE crawl_runs
  ADD COLUMN partial_count INT NOT NULL DEFAULT 0 AFTER success_count;

ALTER TABLE crawl_run_items
  MODIFY COLUMN status ENUM('success','partial','sold_out','error') NOT NULL,
  ADD COLUMN raw_options_count INT NOT NULL DEFAULT 0 AFTER status,
  ADD COLUMN saved_options_count INT NOT NULL DEFAULT 0 AFTER raw_options_count;

ALTER TABLE price_observations
  DROP INDEX uq_dedup,
  ADD COLUMN taxes_fees DECIMAL(15,2) NULL AFTER discount_percent,
  ADD COLUMN price_includes_tax BOOLEAN NULL AFTER taxes_fees,
  ADD COLUMN room_option_index INT NULL AFTER room_type_norm,
  ADD COLUMN room_option_key CHAR(64) NULL AFTER room_option_index;

-- Dữ liệu trước migration chỉ là dữ liệu test; dùng record_id để backfill khóa duy nhất, không
-- giả vờ khôi phục các option đã từng bị INSERT IGNORE làm mất.
UPDATE price_observations
SET room_option_index = record_id,
    room_option_key = SHA2(CONCAT_WS('|', hotel_id, checkin_date, observed_at, record_id), 256)
WHERE room_option_index IS NULL OR room_option_key IS NULL;

ALTER TABLE price_observations
  MODIFY COLUMN room_option_index INT NOT NULL,
  MODIFY COLUMN room_option_key CHAR(64) NOT NULL,
  ADD UNIQUE KEY uq_dedup (crawl_run_id, hotel_id, checkin_date, room_option_index);
