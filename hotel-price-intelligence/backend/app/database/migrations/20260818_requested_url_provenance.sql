USE hotel_price_intel;

-- Keep the exact date-forced URL separately from Booking's final canonical URL.
-- Booking may redirect a valid hotel page and remove checkin/checkout from the
-- final URL, while the page still contains the requested dates in its DOM.
ALTER TABLE crawl_run_items
  ADD COLUMN requested_hotel_link TEXT NULL
  AFTER source_hotel_link;

-- Existing rows predate this provenance field. Leave them NULL rather than
-- pretending that their canonical final URL was the exact forced request.
