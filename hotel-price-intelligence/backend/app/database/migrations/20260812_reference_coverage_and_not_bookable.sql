USE hotel_price_intel;

-- Property lifecycle is independent from date-specific room availability.
ALTER TABLE hotels
  ADD COLUMN booking_status ENUM('active','not_bookable','not_listed') NOT NULL DEFAULT 'active' AFTER amenities,
  ADD COLUMN booking_status_reason VARCHAR(500) NULL AFTER booking_status,
  ADD COLUMN booking_status_checked_at DATETIME NULL AFTER booking_status_reason,
  ADD INDEX idx_hotels_booking_status (booking_status);

-- Keep operational outcomes separate. A sold-out date and a property that cannot
-- be booked are neither successful price observations nor parser failures.
ALTER TABLE crawl_runs
  ADD COLUMN sold_out_count INT NOT NULL DEFAULT 0 AFTER partial_count,
  ADD COLUMN not_bookable_count INT NOT NULL DEFAULT 0 AFTER sold_out_count;

ALTER TABLE crawl_run_items
  MODIFY COLUMN status ENUM(
    'queued','running','success','partial','sold_out','not_bookable','error'
  ) NOT NULL DEFAULT 'queued';

-- Reference coverage is measured over hotel/check-in crawl items, not only runs.
ALTER TABLE hotel_room_candidates
  ADD COLUMN distinct_item_count INT NOT NULL DEFAULT 0 AFTER distinct_run_count,
  ADD COLUMN eligible_item_count INT NOT NULL DEFAULT 0 AFTER distinct_item_count,
  ADD COLUMN item_coverage DECIMAL(5,4) NOT NULL DEFAULT 0 AFTER eligible_item_count;

ALTER TABLE hotel_reference_rooms
  ADD COLUMN distinct_item_count INT NOT NULL DEFAULT 0 AFTER distinct_run_count,
  ADD COLUMN eligible_item_count INT NOT NULL DEFAULT 0 AFTER distinct_item_count;

-- A complete parser result remains crawl-success even when an approved reference
-- room is absent. Reference availability is retained in its own audit column.
UPDATE crawl_run_items
SET status = 'success', last_error_code = NULL, error_message = NULL
WHERE status = 'partial'
  AND last_error_code IN ('reference_unavailable','reference_ambiguous')
  AND rejected_options_count = 0
  AND parsed_options_count = saved_options_count;

-- Sen Hotel was verified on 2026-08-12 as property-level non-bookable on its
-- canonical Booking page. Reclassify only the exact source property and prior
-- parser-empty outcome; do not fabricate a price observation.
UPDATE crawl_run_items
SET status = 'not_bookable',
    last_error_code = 'property_not_bookable',
    error_message = 'Booking xác nhận hiện tại không thể đặt phòng tại khách sạn này',
    next_retry_at = NULL,
    finished_at = COALESCE(finished_at, UTC_TIMESTAMP())
WHERE source_hotel_link LIKE '%booking.com/hotel/vn/sen.%'
  AND last_error_code = 'parser_empty';

INSERT INTO hotels (
  hotel_id,name,name_normalized,hotel_link,city,booking_status,
  booking_status_reason,booking_status_checked_at,attributes_updated_at
) VALUES (
  'sen','Sen Hotel - Managed by Sen Hotel Group','sen hotel - managed by sen hotel group',
  'https://www.booking.com/hotel/vn/sen.vi.html','Hà Nội','not_bookable',
  'Booking xác nhận hiện tại không thể đặt phòng tại khách sạn này',UTC_TIMESTAMP(),UTC_TIMESTAMP()
)
ON DUPLICATE KEY UPDATE
  booking_status='not_bookable',
  booking_status_reason=VALUES(booking_status_reason),
  booking_status_checked_at=VALUES(booking_status_checked_at);

UPDATE crawl_run_items
SET hotel_id='sen',hotel_name='Sen Hotel - Managed by Sen Hotel Group'
WHERE source_hotel_link LIKE '%booking.com/hotel/vn/sen.%'
  AND status='not_bookable';

UPDATE crawl_runs cr
JOIN (
  SELECT crawl_run_id,
    COUNT(*) AS total,
    SUM(status IN ('success','partial','sold_out','not_bookable','error')) AS processed,
    SUM(status = 'success') AS success_count,
    SUM(status = 'partial') AS partial_count,
    SUM(status = 'sold_out') AS sold_out_count,
    SUM(status = 'not_bookable') AS not_bookable_count,
    SUM(status = 'error') AS error_count
  FROM crawl_run_items
  GROUP BY crawl_run_id
) item_counts ON item_counts.crawl_run_id = cr.id
SET cr.total = item_counts.total,
    cr.processed = item_counts.processed,
    cr.success_count = item_counts.success_count,
    cr.partial_count = item_counts.partial_count,
    cr.sold_out_count = item_counts.sold_out_count,
    cr.not_bookable_count = item_counts.not_bookable_count,
    cr.error_count = item_counts.error_count;
