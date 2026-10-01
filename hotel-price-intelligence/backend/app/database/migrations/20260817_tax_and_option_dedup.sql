USE hotel_price_intel;

-- Scraper 2.2.2 keeps parser completeness auditable when exact RAW option
-- fingerprints are collapsed before persistence:
-- parsed_options_count = saved_options_count + duplicate_options_count.
ALTER TABLE crawl_run_items
  ADD COLUMN duplicate_options_count INT NOT NULL DEFAULT 0
  AFTER rejected_options_count;

-- Historical rows are intentionally preserved. Earlier runs did not perform
-- this dedup step, so their duplicate_options_count correctly remains zero.
