USE hotel_price_intel;

-- Booking currently uses this property-level message for LIA Homestay:
-- "Hiện tại không thể đặt phòng tại chỗ nghỉ này trên trang web chúng tôi".
-- Reclassify only this exact Booking property and its previous parser-empty
-- outcomes. These items have no observations, so no price data is deleted.
INSERT INTO hotels (
  hotel_id,name,name_normalized,hotel_link,city,booking_status,
  booking_status_reason,booking_status_checked_at,attributes_updated_at
) VALUES (
  'sunny-venice-grand-world-phu-quoc',
  'LIA Homestay Grand World Phu Quoc - Sunny Venice Apartment',
  'lia homestay grand world phu quoc - sunny venice apartment',
  'https://www.booking.com/hotel/vn/sunny-venice-grand-world-phu-quoc.vi.html',
  'Phú Quốc','not_bookable',
  'Booking xác nhận hiện tại không thể đặt phòng tại chỗ nghỉ này trên trang web chúng tôi',
  UTC_TIMESTAMP(),UTC_TIMESTAMP()
)
ON DUPLICATE KEY UPDATE
  booking_status='not_bookable',
  booking_status_reason=VALUES(booking_status_reason),
  booking_status_checked_at=VALUES(booking_status_checked_at);

UPDATE crawl_run_items
SET hotel_id='sunny-venice-grand-world-phu-quoc',
    hotel_name='LIA Homestay Grand World Phu Quoc - Sunny Venice Apartment',
    status='not_bookable',
    reference_match_status='not_applicable',
    last_error_code='property_not_bookable',
    error_message='Booking xác nhận hiện tại không thể đặt phòng tại chỗ nghỉ này trên trang web chúng tôi',
    next_retry_at=NULL,
    finished_at=COALESCE(finished_at, UTC_TIMESTAMP())
WHERE source_hotel_link LIKE '%booking.com/hotel/vn/sunny-venice-grand-world-phu-quoc.%'
  AND status='error'
  AND last_error_code='parser_empty';

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
