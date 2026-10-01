USE hotel_price_intel;

-- Keep national events city-neutral and require a city for city-scoped events.
-- The previous live constraint was created from a mis-decoded SQL script.
ALTER TABLE vn_holidays
  DROP CHECK chk_vn_holidays_scope_city;

ALTER TABLE vn_holidays
  ADD CONSTRAINT chk_vn_holidays_scope_city CHECK (
    (scope = 'national' AND city IS NULL)
    OR
    (scope = 'city' AND city IS NOT NULL)
  );
