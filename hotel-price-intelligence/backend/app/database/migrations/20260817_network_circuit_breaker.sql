USE hotel_price_intel;

ALTER TABLE crawler_workers
  MODIFY COLUMN status ENUM('online','waiting_network','stopping','offline')
    NOT NULL DEFAULT 'online',
  ADD COLUMN status_reason VARCHAR(500) NULL AFTER process_id,
  ADD COLUMN paused_at DATETIME NULL AFTER status_reason,
  ADD COLUMN next_probe_at DATETIME NULL AFTER paused_at,
  ADD COLUMN network_failure_count INT NOT NULL DEFAULT 0 AFTER next_probe_at;
