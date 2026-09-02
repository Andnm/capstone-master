USE hotel_price_intel;

ALTER TABLE crawl_run_items
  ADD COLUMN dead_link_confirmation JSON NULL AFTER reference_match_status;

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
