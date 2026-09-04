-- Corrective migration cho 20260904_anomaly_v2_registry.sql - sua MIN1 (discuss
-- discuss/anomaly-v2-ground-truth/19-gpt-review-implementation.md): price_anomaly_signals chi FK
-- toi anomaly_signal_configs(config_sha256), khong rang buoc method_version phai khop dung config -
-- ve ly thuyet 1 signal co the khai method_version='v2' nhung config_sha256 lai tro toi config cua
-- 'v2.1'. Doi FK sang composite (config_sha256, method_version).
--
-- KHONG sua truc tiep file migration goc (da apply len local truoc luc review - discuss file 19
-- "Chỉ sau PASS mới apply corrective migration"). File nay CHUA duoc apply - cho PASS FOR
-- IMPLEMENTATION xong moi chay. setup.sql da co san schema CUOI CUNG (gom ca fix nay) cho fresh
-- install, nen file nay chi can cho DB DA TON TAI tu migration goc.

USE hotel_price_intel;

ALTER TABLE anomaly_signal_configs
  ADD UNIQUE KEY uq_configs_hash_version (config_sha256, method_version);

-- Ten constraint moi KHAC ten cu (fk_signals_config) - MySQL tu choi DROP+ADD cung ten trong 1
-- cau ALTER (loi 1826 "Duplicate foreign key constraint name" da gap khi tu test tren scratch DB).
ALTER TABLE price_anomaly_signals
  DROP FOREIGN KEY fk_signals_config,
  ADD CONSTRAINT fk_signals_config_composite FOREIGN KEY (config_sha256, method_version)
    REFERENCES anomaly_signal_configs(config_sha256, method_version);
