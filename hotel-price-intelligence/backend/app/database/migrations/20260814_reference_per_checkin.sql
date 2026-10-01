USE hotel_price_intel;

-- Price comparisons form a time series for one hotel and one future stay date.
-- Keep legacy definitions as retired audit history, then rebuild active metadata.
UPDATE hotel_reference_rooms
SET status='retired',active_to=COALESCE(active_to,UTC_TIMESTAMP())
WHERE status IN ('approved','proposed');

DELETE FROM hotel_room_candidates;

ALTER TABLE hotel_room_candidates
  DROP PRIMARY KEY,
  ADD COLUMN checkin_date DATE NOT NULL AFTER hotel_id,
  ADD PRIMARY KEY (hotel_id,checkin_date,room_identity_key,rate_plan_key);

-- Nullable only so retired, pre-migration definitions remain attributable history.
-- Every new proposed/approved definition is written with a check-in date.
ALTER TABLE hotel_reference_rooms
  ADD COLUMN checkin_date DATE NULL AFTER hotel_id,
  DROP INDEX idx_reference_hotel_status,
  ADD INDEX idx_reference_hotel_checkin_status (hotel_id,checkin_date,status);

UPDATE price_observations
SET is_reference_room=FALSE,reference_definition_id=NULL,
    reference_match_status=IF(is_sold_out=1,'not_applicable','calibrating'),
    reference_match_score=NULL;

UPDATE crawl_run_items
SET reference_match_status=CASE
  WHEN status IN ('sold_out','not_bookable') THEN 'not_applicable'
  ELSE 'calibrating' END;
