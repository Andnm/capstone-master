-- Anomaly v2: candidate-signal detector + human-reviewed registry, thay cho is_anomaly v1
-- (rule tự động confirm). Thiết kế chốt qua discuss/anomaly-v2-ground-truth/ (17 file,
-- PASS FOR DESIGN file 17). price_observations.is_anomaly GIỮ NGUYÊN schema (đã có sẵn) nhưng đổi
-- nghĩa: từ giờ là PROJECTION được reconcile từ anomaly_review_resolutions, không phải giá trị rule
-- tự ghi trực tiếp. Xem CLAUDE.md mục 4.5 để hiểu đầy đủ mô hình.

USE hotel_price_intel;

-- 1) Config cho detector - mỗi method_version chỉ được gắn ĐÚNG 1 config (đổi threshold phải bump
--    method_version, không ghi đè config cũ - tránh signal cũ/mới lẫn lộn dưới cùng version string).
CREATE TABLE anomaly_signal_configs (
  config_sha256   CHAR(64) PRIMARY KEY,
  method_version  VARCHAR(20) NOT NULL,
  config_json     JSON NOT NULL,
  created_at      DATETIME NOT NULL,
  UNIQUE KEY uq_configs_method_version (method_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2) Tín hiệu detector - CHỈ LÀ CANDIDATE, không tự loại record nào khỏi train. 4 signal_code:
--    low_price_outlier, context_level_high, temporal_level_shift, hotel_wide_level_shift.
--    evidence_available_at khác record_observed_at: là MAX(finished_at) của mọi crawl_runs đóng góp
--    bằng chứng cho signal này (không phải observed_at của chính record) - causal consumer luôn lọc
--    theo evidence_available_at.
CREATE TABLE price_anomaly_signals (
  record_id             BIGINT NOT NULL,
  method_version        VARCHAR(20) NOT NULL,
  signal_code           VARCHAR(40) NOT NULL,
  severity              VARCHAR(20) NOT NULL,
  config_sha256         CHAR(64) NOT NULL,
  record_observed_at    DATETIME NOT NULL,
  evidence_available_at DATETIME NOT NULL,
  computed_at           DATETIME NOT NULL,
  metrics_json          JSON NOT NULL,
  PRIMARY KEY (record_id, method_version, signal_code),
  CONSTRAINT fk_signals_record FOREIGN KEY (record_id)
    REFERENCES price_observations(record_id) ON DELETE CASCADE,
  CONSTRAINT fk_signals_config FOREIGN KEY (config_sha256)
    REFERENCES anomaly_signal_configs(config_sha256),
  INDEX idx_signals_signal_code (signal_code),
  INDEX idx_signals_evidence_available (evidence_available_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3) Quyết định review (con người/GPT/Claude thay mặt, luôn ghi rõ lý do+bằng chứng). state chỉ có
--    3 giá trị - KHÔNG có draft/materialized trung gian trong DB: draft/preview xảy ra NGOÀI DB
--    (script preview ra file tạm), chỉ khi publish vào anomaly_registry.json và sync mới ghi thẳng
--    active (hoặc superseded/retracted khi event tương ứng chạy).
CREATE TABLE anomaly_review_decisions (
  review_id                VARCHAR(64) PRIMARY KEY,
  decision                 ENUM('exclude_from_train','keep_as_valid','needs_review') NOT NULL,
  reason_code               VARCHAR(60) NOT NULL,
  rationale                  TEXT NOT NULL,
  evidence_json               JSON NOT NULL,
  reviewer                     VARCHAR(60) NOT NULL,
  decided_at                    DATETIME NOT NULL,
  state                          ENUM('active','superseded','retracted') NOT NULL DEFAULT 'active',
  member_count                    INT NULL,
  member_checksum                   CHAR(64) NULL,
  superseded_by_review_id             VARCHAR(64) NULL,
  created_at                            DATETIME NOT NULL,
  CONSTRAINT fk_decisions_superseded_by FOREIGN KEY (superseded_by_review_id)
    REFERENCES anomaly_review_decisions(review_id),
  INDEX idx_decisions_state (state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4) Lịch sử membership - append-only, KHÔNG BAO GIỜ update/delete (kể cả khi decision bị
--    superseded/retracted - lịch sử vẫn giữ nguyên, chỉ resolutions ở bảng 5 đổi). Có
--    source_record_sha256 để chặn tái sử dụng record_id sau khi DB bị reset/reseed (đã từng xảy ra
--    ở giai đoạn pilot trước 2026-08-17) - sync phải verify hash khớp mới materialize.
CREATE TABLE anomaly_review_members (
  review_id             VARCHAR(64) NOT NULL,
  source_code           VARCHAR(20) NOT NULL,
  source_record_id      BIGINT NOT NULL,
  source_record_sha256  CHAR(64) NOT NULL,
  materialized_at       DATETIME NOT NULL,
  PRIMARY KEY (review_id, source_code, source_record_id),
  CONSTRAINT fk_members_decision FOREIGN KEY (review_id)
    REFERENCES anomaly_review_decisions(review_id) ON DELETE RESTRICT,
  CONSTRAINT fk_members_record FOREIGN KEY (source_record_id)
    REFERENCES price_observations(record_id) ON DELETE RESTRICT,
  CONSTRAINT chk_members_source_code_format CHECK (source_code REGEXP '^[a-z][a-z0-9_]{0,19}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5) Trạng thái HIỆN HÀNH - PK ép đúng 1 review đang active cho mỗi record, KHÔNG suy luận
--    "latest decided_at wins". is_anomaly (price_observations) là projection của EXISTS(resolution
--    -> decision active exclude_from_train) trên đúng bảng này.
CREATE TABLE anomaly_review_resolutions (
  source_code       VARCHAR(20) NOT NULL,
  source_record_id  BIGINT NOT NULL,
  review_id         VARCHAR(64) NOT NULL,
  resolved_at       DATETIME NOT NULL,
  PRIMARY KEY (source_code, source_record_id),
  CONSTRAINT fk_resolutions_member FOREIGN KEY (review_id, source_code, source_record_id)
    REFERENCES anomaly_review_members(review_id, source_code, source_record_id) ON DELETE RESTRICT,
  INDEX idx_resolutions_review (review_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 6) Log áp dụng TỪNG event của anomaly_registry.json (grain=event, không phải grain=file). Hash
--    tính trên CHÍNH payload của event đó (không gồm phần còn lại của file) - append thêm event mới
--    vào file KHÔNG làm mất khả năng idempotency-check của event cũ.
CREATE TABLE anomaly_registry_events_applied (
  event_id              VARCHAR(64) PRIMARY KEY,
  sequence_no           INT NOT NULL,
  event_payload_sha256  CHAR(64) NOT NULL,
  action                ENUM('activate','supersede','retract') NOT NULL,
  review_id             VARCHAR(64) NOT NULL,
  member_count          INT NOT NULL DEFAULT 0,
  applied_at            DATETIME NOT NULL,
  UNIQUE KEY uq_events_sequence (sequence_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 7) Log MỘT LẦN CHẠY sync_anomaly_registry.py --apply (grain=file-sync-attempt, khác grain bảng 6).
--    status chỉ 'success' sau khi replay HẾT mọi event + projection integrity = 0. Consumer
--    (monitor/export/warehouse) chỉ coi registry của 1 DB là current/đáng tin khi có dòng success
--    khớp đúng registry_file_sha256 hiện tại.
CREATE TABLE anomaly_registry_sync_runs (
  sync_id                       BIGINT AUTO_INCREMENT PRIMARY KEY,
  source_code                   VARCHAR(20) NOT NULL,
  registry_file_sha256          CHAR(64) NOT NULL,
  started_at                    DATETIME NOT NULL,
  finished_at                   DATETIME NULL,
  status                        ENUM('running','success','failed') NOT NULL DEFAULT 'running',
  expected_event_count          INT NOT NULL,
  applied_through_sequence      INT NULL,
  active_resolution_checksum    CHAR(64) NULL,
  anomaly_projection_checksum   CHAR(64) NULL,
  error_message                 TEXT NULL,
  CONSTRAINT chk_sync_runs_source_code_format CHECK (source_code REGEXP '^[a-z][a-z0-9_]{0,19}$'),
  INDEX idx_sync_runs_source_status (source_code, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 8) Danh tính CỐ ĐỊNH của chính DB này - đúng 1 dòng, seed bằng script provision (không phải file
--    .sql tĩnh, vì cần đọc application settings). sync_anomaly_registry.py bắt buộc đọc bảng này
--    trước tiên và FAIL nếu --source-code truyền vào không khớp - chặn "chạy nhầm lệnh trên nhầm
--    máy" (khác hẳn việc file registry chứa member của nguồn khác, việc đó luôn hợp lệ và được bỏ
--    qua có chủ đích, không phải lỗi).
CREATE TABLE anomaly_registry_source_identity (
  id             TINYINT NOT NULL PRIMARY KEY DEFAULT 1,
  source_code    VARCHAR(20) NOT NULL,
  configured_at  DATETIME NOT NULL,
  CONSTRAINT chk_identity_single_row CHECK (id = 1),
  CONSTRAINT chk_identity_source_code_format CHECK (source_code REGEXP '^[a-z][a-z0-9_]{0,19}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
