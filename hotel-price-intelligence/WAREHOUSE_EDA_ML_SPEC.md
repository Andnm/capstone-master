# Warehouse, EDA và ML Dataset — Đặc tả hợp nhất

## 1. Trạng thái và phạm vi

- **Trạng thái: APPROVED FOR IMPLEMENTATION** (acceptance review: 2026-08-24). Môi trường MySQL
  xác nhận là 8.0.45 — CHECK constraint enforced hợp lệ.
- **Local và VPS là database vận hành, READ-ONLY đối với mọi tiến trình ETL.**
- **2 lifecycle tách biệt**: warehouse build (mục 3a, 1 lần/batch) và dataset build (mục 3b, nhiều
  lần/warehouse, mỗi lần 1 `dataset_version`). Warehouse PASS không phụ thuộc `dataset_version` cụ
  thể nào.
- **Mục đích**: audit dữ liệu, EDA, curated dataset và huấn luyện model. Không phải hệ thống phục
  vụ sản phẩm.
- **Model lõi là regression** dự báo giá ngắn hạn (horizon 1/3/7/14 ngày), CLAUDE.md mục 3.
- **Cấm dùng làm feature model**: `source_code`, mọi ID tự tăng, `worker_id`, `host_name`, mọi
  field timing runtime, `artifact_html_path`, `screenshot_path`.

---

## 2. Kiến trúc dữ liệu

```
[Local MySQL]         [VPS MySQL]
     │                     │
     ▼                     ▼
 dump (không CREATE DATABASE/USE/DROP) + checksum
     │                     │
     └────────┬────────────┘
              ▼
══════════ WAREHOUSE BUILD (1 lần/batch, versioned) ══════════
      Unified Warehouse Core (4 bảng, payload ĐÓNG BĂNG)
      + etl_import_batches (canonicalization_version CỐ ĐỊNH)
      + etl_import_rejections (parent-chain)
      + etl_run_map / etl_item_map (ownership đúng grain)
              │
              ▼
      curated_observation_keys (canonical key, 1 version/batch)
              │
              ▼
      Full-history reference (hotel_room_candidates/hotel_reference_rooms)
              │
              ▼
         [WAREHOUSE PASS — promote_warehouse]
══════════ DATASET BUILD (nhiều lần/warehouse) ══════════
      dataset_build_manifests (status=running)
              │
              ▼
      Causal-freeze → ml_reference_assignments (mục 11)
              │
              ▼
      Item matching (alias-aware, đúng signature select_best_match) → ml_item_reference_matches (mục 12)
              │
              ▼
      Daily sample + label generation → ml_samples (mục 14)
              │
              ▼
      Split (có purge zone) + feature/label parquet (mục 15)
              │
              ▼
         [dataset_build_manifests status=pass]
```

**6 tầng dữ liệu**:

1. **Source snapshot/staging** — ephemeral, checksum, không `CREATE DATABASE`/`USE`/`DROP`.
2. **Unified Warehouse Core** — 4 bảng, import toàn bộ, không điều kiện. Field reference reset
   đúng 1 lần lúc import, không bao giờ ghi lại.
3. **`curated_observation_keys`** — canonical key mọi record_id, 1 `canonicalization_version` cố
   định/batch.
4. **Full-history derived reference** — warehouse-level, không phụ thuộc `dataset_version`.
5. **Causal-freeze + curated ML** — `ml_reference_assignments`/`ml_item_reference_matches`/
   `ml_samples`, scoped `dataset_version`, thuộc dataset build.
6. **Feature/label dataset** — parquet versioned theo `dataset_version`.

---

## 3a. Warehouse build lifecycle

```
 1. Snapshot local, snapshot VPS — chỉ định database + 4 bảng core, KHÔNG --databases/--all-databases
 2. Scan dump: statement top-level CREATE DATABASE/DROP DATABASE/USE → FAIL
 3. Checksum 2 dump
 4. Tạo warehouse database versioned, tên qua whitelist ^[A-Za-z0-9_]+$
 5. Sanitize + chạy setup.sql (parse theo statement)
 6. Verify SELECT DATABASE()
 7. Tạo toàn bộ 9 bảng ETL-only (mục 4)
 8. Tạo 2 staging tạm, restore dump, verify SELECT DATABASE() sau restore
 9. Import/remap 4 bảng core theo thứ tự FK; row lỗi ghi etl_import_rejections kèm
    rejection_scope + source_parent_pk_value; reject 1 crawl_runs kéo theo reject toàn bộ con
    (rejection_scope='parent_rejected')
10. Drop 2 staging (validate prefix + tên tuyệt đối trước khi drop)
11. Chạy integrity/row-count validation (mục 7 — công thức RIÊNG cho hotels vs run/item/observation)
12. Build curated_observation_keys — đúng MỘT canonicalization_version/git_commit/config_sha256
    đã ghi ở etl_import_batches, cho mọi record_id
13. Build full-history reference (mục 10)
14. Ghi batch status = PASS — chỉ khi rejection count = 0 HOẶC mọi rejection có waived=TRUE kèm
    waived_reason + waived_by
15. Atomic replace outputs/warehouse/warehouse_current.json
```

---

## 3b. Dataset build lifecycle

```
 1. init_dataset_build: tạo dataset_build_manifests status='running', dataset_version mới;
    ghi NGAY toàn bộ cấu hình bất biến gồm import_batch_id, reference_algorithm_version,
    label_config_sha256, feature_config_sha256, purge_gap_days, random_seed, build_config_json và
    build_config_sha256; last_completed_step='initialized', active_step=NULL; chỉ split_* và output
    checksum được để NULL
 2. build_causal_references (mục 11) → ml_reference_assignments
 3. build_item_matches (mục 12, đúng signature select_best_match) → ml_item_reference_matches
 4. build_daily_samples_labels (mục 14) → ml_samples (insert trước với label NULL, UPDATE label
    sau khi toàn bộ dòng của cửa sổ liên quan đã tồn tại — tránh vấn đề FK tự tham chiếu lúc insert)
 5. finalize_split (mục 15) — CHỈ sau khi biết coverage label thực tế; UPDATE split_train_end/
    split_validation_end (nullable tới bước này)
 6. build_features_labels → parquet
 7. validate_dataset (mục 17)
 8. UPDATE status='pass', finished_at=now — chỉ khi toàn bộ PASS gate mục 17 đạt; ngược lại
    status='fail' và ghi fail_reason
```

**Rerun 1 bước giữa chừng** — xem "Invalidation graph" ở mục 18, không tự ý DELETE/INSERT không
theo đúng thứ tự child-first.

---

## 4. DDL dự kiến

MySQL 8.0.16+ để CHECK constraint enforced (môi trường thật: 8.0.45). Mọi bảng ETL dùng
`DATETIME`, giá trị UTC-naive do Python cung cấp.

Các CHECK JSON dưới đây chỉ dùng deterministic built-in functions, phù hợp quy tắc CHECK của
[MySQL](https://dev.mysql.com/doc/refman/8.4/en/create-table-check-constraints.html); semantics của
`JSON_CONTAINS_PATH`/`JSON_EXTRACT` theo
[JSON search functions](https://dev.mysql.com/doc/refman/8.0/en/json-search-functions.html).

Lưu ý bảo trì: khi thêm một config projection bắt buộc, phải cập nhật đồng thời path list, type
check và phép so khớp cột trong `chk_dataset_config_projection`. Python phải serialize
`purge_gap_days`/`random_seed` thành JSON integer thật, không phải chuỗi.

### Warehouse-level

```sql
CREATE TABLE etl_import_batches (
  batch_id                       VARCHAR(40)  PRIMARY KEY,
  warehouse_database             VARCHAR(64)  NOT NULL,
  status                         ENUM('running','pass','fail') NOT NULL DEFAULT 'running',
  started_at                     DATETIME NOT NULL,
  finished_at                    DATETIME NULL,
  setup_sql_sha256               CHAR(64) NOT NULL,
  local_dump_path                 VARCHAR(1000),
  local_dump_sha256               CHAR(64),
  local_dump_taken_at             DATETIME,
  vps_dump_path                     VARCHAR(1000),
  vps_dump_sha256                   CHAR(64),
  vps_dump_taken_at                 DATETIME,
  cohort_manifest_sha256              CHAR(64),
  ownership_manifest_sha256           CHAR(64),
  etl_config_sha256                    CHAR(64),
  canonicalization_version              VARCHAR(50) NOT NULL,
  canonicalization_git_commit           VARCHAR(64) NOT NULL,
  canonicalization_config_sha256        CHAR(64) NOT NULL,
  source_versions_json                    JSON NOT NULL,
  fail_reason                               TEXT,
  notes                                       TEXT,
  INDEX idx_batches_status (status),
  INDEX idx_batches_started (started_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE etl_import_rejections (
  id                     BIGINT AUTO_INCREMENT PRIMARY KEY,
  import_batch_id         VARCHAR(40) NOT NULL,
  source_code              ENUM('local','vps') NOT NULL,
  source_table               VARCHAR(64) NOT NULL,
  source_pk_value              VARCHAR(255) NOT NULL,
  rejection_scope                ENUM('row_error','parent_rejected') NOT NULL,
  source_parent_pk_value           VARCHAR(255),
  rejection_reason                   TEXT NOT NULL,
  raw_row_json                         JSON,
  waived                                 BOOLEAN NOT NULL DEFAULT FALSE,
  waived_reason                            TEXT,
  waived_by                                  VARCHAR(100),
  created_at                                   DATETIME NOT NULL,
  CONSTRAINT fk_rejections_batch FOREIGN KEY (import_batch_id)
    REFERENCES etl_import_batches(batch_id),
  -- waived=TRUE bắt buộc có lý do + người duyệt, không được để trống
  CONSTRAINT chk_rejections_waived CHECK (
    waived = FALSE OR (waived_reason IS NOT NULL AND waived_by IS NOT NULL)
  ),
  INDEX idx_rejections_batch (import_batch_id),
  INDEX idx_rejections_parent (import_batch_id, source_table, source_parent_pk_value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE etl_run_map (
  import_batch_id       VARCHAR(40) NOT NULL,
  source_code           ENUM('local','vps') NOT NULL,
  source_run_id         BIGINT NOT NULL,
  warehouse_run_id      BIGINT NOT NULL,
  source_status          VARCHAR(20) NOT NULL,
  source_created_at      DATETIME NOT NULL,
  planned_crawl_date       DATE NULL,
  schedule_day_key           VARCHAR(100) NULL,      -- công thức mục 8, canonical JSON hash
  include_reference            BOOLEAN NOT NULL DEFAULT TRUE,
  include_eda_raw                BOOLEAN NOT NULL DEFAULT TRUE,
  include_eda_main                 BOOLEAN NOT NULL DEFAULT TRUE,
  include_training                   BOOLEAN NOT NULL DEFAULT TRUE,
  exclusion_reason                     VARCHAR(200),
  imported_at                            DATETIME NOT NULL,
  PRIMARY KEY (import_batch_id, source_code, source_run_id),
  UNIQUE KEY uq_run_map_warehouse (warehouse_run_id),
  CONSTRAINT fk_run_map_batch FOREIGN KEY (import_batch_id)
    REFERENCES etl_import_batches(batch_id),
  CONSTRAINT fk_run_map_warehouse_run FOREIGN KEY (warehouse_run_id) REFERENCES crawl_runs(id),
  CONSTRAINT chk_run_map_eda_invariant CHECK (
    (include_reference = FALSE AND include_training = FALSE) OR include_eda_main = TRUE
  ),
  -- main ⇒ raw
  CONSTRAINT chk_run_map_eda_hierarchy CHECK (
    include_eda_main = FALSE OR include_eda_raw = TRUE
  ),
  -- bất kỳ cờ có ý nghĩa nào TRUE ⇒ phải có đủ metadata lịch
  CONSTRAINT chk_run_map_schedule CHECK (
    (include_reference = FALSE AND include_training = FALSE AND include_eda_main = FALSE)
    OR (planned_crawl_date IS NOT NULL AND schedule_day_key IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE etl_item_map (
  import_batch_id       VARCHAR(40) NOT NULL,
  source_code           ENUM('local','vps') NOT NULL,
  source_item_id        BIGINT NOT NULL,
  warehouse_item_id     BIGINT NOT NULL,
  schedule_slot            VARCHAR(20) NULL,
  schedule_manifest_row_key  CHAR(64) NULL,           -- công thức mục 8, canonical JSON hash
  ownership_status              ENUM('owner_success','non_owner_duplicate','owner_failure',
                                       'protocol_deviation','unassigned') NOT NULL
                                       DEFAULT 'unassigned',
  include_reference                BOOLEAN NOT NULL DEFAULT TRUE,
  include_eda_raw                    BOOLEAN NOT NULL DEFAULT TRUE,
  include_eda_main                     BOOLEAN NOT NULL DEFAULT TRUE,
  include_training                       BOOLEAN NOT NULL DEFAULT TRUE,
  exclusion_reason                         VARCHAR(200),
  imported_at                                DATETIME NOT NULL,
  PRIMARY KEY (import_batch_id, source_code, source_item_id),
  UNIQUE KEY uq_item_map_warehouse (warehouse_item_id),
  CONSTRAINT fk_item_map_batch FOREIGN KEY (import_batch_id)
    REFERENCES etl_import_batches(batch_id),
  CONSTRAINT fk_item_map_warehouse_item FOREIGN KEY (warehouse_item_id)
    REFERENCES crawl_run_items(id),
  CONSTRAINT chk_item_map_eda_invariant CHECK (
    (include_reference = FALSE AND include_training = FALSE) OR include_eda_main = TRUE
  ),
  CONSTRAINT chk_item_map_eda_hierarchy CHECK (
    include_eda_main = FALSE OR include_eda_raw = TRUE
  ),
  -- 2 chiều — unassigned ⇒ tắt hết + có lý do; resolved ⇒ có đủ slot/key
  CONSTRAINT chk_item_map_ownership_unassigned CHECK (
    ownership_status <> 'unassigned'
    OR (include_reference = FALSE AND include_training = FALSE AND include_eda_main = FALSE
        AND exclusion_reason IS NOT NULL)
  ),
  CONSTRAINT chk_item_map_ownership_resolved CHECK (
    ownership_status = 'unassigned'
    OR (schedule_slot IS NOT NULL AND schedule_manifest_row_key IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE curated_observation_keys (
  record_id                 BIGINT PRIMARY KEY,
  hotel_id                  VARCHAR(255) NOT NULL,
  checkin_date                DATE NOT NULL,
  canonical_room_key           CHAR(64) NOT NULL,
  canonical_rate_key           CHAR(64) NOT NULL,
  canonical_series_id          CHAR(64) NOT NULL,
  created_at                     DATETIME NOT NULL,
  CONSTRAINT fk_cok_record FOREIGN KEY (record_id) REFERENCES price_observations(record_id),
  INDEX idx_cok_series (canonical_series_id),
  INDEX idx_cok_hotel_checkin (hotel_id, checkin_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### Dataset-level (scoped `dataset_version`)

```sql
CREATE TABLE dataset_build_manifests (
  dataset_version               VARCHAR(40) PRIMARY KEY,
  import_batch_id                VARCHAR(40) NOT NULL,
  status                           ENUM('running','pass','fail') NOT NULL DEFAULT 'running',
  started_at                        DATETIME NOT NULL,
  finished_at                        DATETIME NULL,
  fail_reason                          TEXT,
  reference_algorithm_version           VARCHAR(50) NOT NULL,
  label_config_sha256                     CHAR(64) NOT NULL,
  feature_config_sha256                     CHAR(64) NOT NULL,
  build_config_json                           JSON NOT NULL,
  build_config_sha256                           CHAR(64) NOT NULL,
  split_train_end                             DATE NULL,
  split_validation_end                          DATE NULL,
  purge_gap_days                                  INT NOT NULL DEFAULT 14,
  random_seed                                       INT NOT NULL,
  library_versions_json                               JSON,
  output_parquet_sha256_json                            JSON,
  last_completed_step                                    ENUM('initialized','causal_references',
                                                               'item_matches','samples_labels',
                                                               'split','features_labels','validation')
                                                               NOT NULL DEFAULT 'initialized',
  active_step                                             ENUM('causal_references','item_matches',
                                                               'samples_labels','split',
                                                               'features_labels','validation') NULL,
  active_step_attempt                                       INT NOT NULL DEFAULT 0,
  active_step_started_at                                      DATETIME NULL,
  active_step_heartbeat_at                                      DATETIME NULL,
  max_step_attempts                                               SMALLINT NOT NULL DEFAULT 3,
  retry_overrides_json                                             JSON NULL,
  last_step_finished_at                                         DATETIME NULL,
  created_at                                              DATETIME NOT NULL,
  UNIQUE KEY uq_dataset_batch (dataset_version, import_batch_id),
  CONSTRAINT fk_dataset_batch FOREIGN KEY (import_batch_id)
    REFERENCES etl_import_batches(batch_id),
  CONSTRAINT chk_dataset_active_step CHECK (
    (active_step IS NULL AND active_step_started_at IS NULL AND active_step_heartbeat_at IS NULL)
    OR (active_step IS NOT NULL AND active_step_started_at IS NOT NULL
        AND active_step_heartbeat_at IS NOT NULL)
  ),
  CONSTRAINT chk_dataset_attempts CHECK (
    active_step_attempt >= 0 AND max_step_attempts >= 1
  ),
  CONSTRAINT chk_dataset_retry_overrides CHECK (
    retry_overrides_json IS NULL OR JSON_TYPE(retry_overrides_json) = 'ARRAY'
  ),
  CONSTRAINT chk_dataset_config_projection CHECK (
    JSON_TYPE(build_config_json) = 'OBJECT'
    AND JSON_CONTAINS_PATH(
      build_config_json, 'all',
      '$.import_batch_id', '$.reference_algorithm_version',
      '$.label_config', '$.feature_config',
      '$.label_config_sha256', '$.feature_config_sha256',
      '$.reference_quality_gate', '$.split_selection_policy',
      '$.purge_gap_days', '$.random_seed'
    ) = 1
    AND JSON_TYPE(JSON_EXTRACT(build_config_json, '$.import_batch_id')) = 'STRING'
    AND JSON_TYPE(JSON_EXTRACT(build_config_json, '$.reference_algorithm_version')) = 'STRING'
    AND JSON_TYPE(JSON_EXTRACT(build_config_json, '$.label_config_sha256')) = 'STRING'
    AND JSON_TYPE(JSON_EXTRACT(build_config_json, '$.feature_config_sha256')) = 'STRING'
    AND JSON_TYPE(JSON_EXTRACT(build_config_json, '$.label_config')) = 'OBJECT'
    AND JSON_TYPE(JSON_EXTRACT(build_config_json, '$.feature_config')) = 'OBJECT'
    AND JSON_TYPE(JSON_EXTRACT(build_config_json, '$.reference_quality_gate')) = 'OBJECT'
    AND JSON_TYPE(JSON_EXTRACT(build_config_json, '$.split_selection_policy')) = 'OBJECT'
    AND JSON_TYPE(JSON_EXTRACT(build_config_json, '$.purge_gap_days')) = 'INTEGER'
    AND JSON_TYPE(JSON_EXTRACT(build_config_json, '$.random_seed')) = 'INTEGER'
    AND JSON_UNQUOTE(JSON_EXTRACT(build_config_json, '$.import_batch_id')) = import_batch_id
    AND JSON_UNQUOTE(JSON_EXTRACT(build_config_json, '$.reference_algorithm_version'))
          = reference_algorithm_version
    AND JSON_UNQUOTE(JSON_EXTRACT(build_config_json, '$.label_config_sha256'))
          = label_config_sha256
    AND JSON_UNQUOTE(JSON_EXTRACT(build_config_json, '$.feature_config_sha256'))
          = feature_config_sha256
    AND CAST(JSON_UNQUOTE(JSON_EXTRACT(build_config_json, '$.purge_gap_days')) AS SIGNED)
          = purge_gap_days
    AND CAST(JSON_UNQUOTE(JSON_EXTRACT(build_config_json, '$.random_seed')) AS SIGNED)
          = random_seed
  ),
  CONSTRAINT chk_dataset_pass_state CHECK (
    status <> 'pass'
    OR (last_completed_step = 'validation' AND active_step IS NULL
        AND finished_at IS NOT NULL
        AND split_train_end IS NOT NULL AND split_validation_end IS NOT NULL
        AND split_train_end < split_validation_end
        AND library_versions_json IS NOT NULL
        AND output_parquet_sha256_json IS NOT NULL)
  ),
  CONSTRAINT chk_dataset_fail_reason CHECK (
    status <> 'fail' OR fail_reason IS NOT NULL
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Composite FK tới dataset_build_manifests(dataset_version, import_batch_id) — chặn assignment
-- ghi batch khác batch thật của chính dataset_version.
-- Bổ sung tự phát hiện: thêm field mô tả phòng/rate cần cho alias scoring của select_best_match()
-- (room_type_anchor_raw/max_occupancy/room_area/breakfast_included/free_cancellation) — production
-- match_reference() dùng các field này để tính điểm alias, không chỉ 2 canonical key, mirror đúng
-- cột hotel_reference_rooms đã có cho mục đích tương tự ở full-history mode.
CREATE TABLE ml_reference_assignments (
  id                            BIGINT AUTO_INCREMENT PRIMARY KEY,
  import_batch_id               VARCHAR(40) NOT NULL,
  dataset_version                VARCHAR(40) NOT NULL,
  hotel_id                        VARCHAR(255) NOT NULL,
  checkin_date                      DATE NOT NULL,
  canonical_room_key                  CHAR(64) NOT NULL,
  canonical_rate_key                  CHAR(64) NOT NULL,
  canonical_series_id                   CHAR(64) NOT NULL,
  room_type_anchor_raw                    VARCHAR(500) NOT NULL,
  room_type_norm                            VARCHAR(100),
  max_occupancy                               INT,
  bed_config                                    VARCHAR(500),
  room_area                                       VARCHAR(50),
  breakfast_included                                BOOLEAN,
  free_cancellation                                   BOOLEAN,
  approved_at                                           DATETIME NOT NULL,
  approving_run_warehouse_id                              BIGINT NOT NULL,
  approving_item_warehouse_id                               BIGINT NOT NULL,
  evidence_run_count                                          INT NOT NULL,
  evidence_item_count                                           INT NOT NULL,
  eligible_item_count                                             INT NOT NULL,
  coverage                                                          DECIMAL(5,4) NOT NULL,
  confidence_score                                                    DECIMAL(5,4) NOT NULL,
  reference_algorithm_version                                           VARCHAR(50) NOT NULL,
  created_at                                                              DATETIME NOT NULL,
  UNIQUE KEY uq_ml_ref_series (dataset_version, hotel_id, checkin_date),
  UNIQUE KEY uq_ml_ref_id_dataset (id, dataset_version),
  INDEX idx_ml_ref_canonical_series (dataset_version, canonical_series_id),
  CONSTRAINT fk_ml_ref_dataset_batch FOREIGN KEY (dataset_version, import_batch_id)
    REFERENCES dataset_build_manifests(dataset_version, import_batch_id),
  CONSTRAINT fk_ml_ref_hotel FOREIGN KEY (hotel_id) REFERENCES hotels(hotel_id),
  CONSTRAINT fk_ml_ref_run FOREIGN KEY (approving_run_warehouse_id) REFERENCES crawl_runs(id),
  CONSTRAINT fk_ml_ref_item FOREIGN KEY (approving_item_warehouse_id)
    REFERENCES crawl_run_items(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE ml_item_reference_matches (
  id                              BIGINT AUTO_INCREMENT PRIMARY KEY,
  dataset_version                  VARCHAR(40) NOT NULL,
  crawl_run_item_id                 BIGINT NOT NULL,
  ml_reference_assignment_id          BIGINT NOT NULL,
  selected_record_id                    BIGINT NULL,
  match_status                            ENUM('exact','alias','unavailable','ambiguous') NOT NULL,
  match_score                               DECIMAL(5,4) NULL,
  created_at                                  DATETIME NOT NULL,
  UNIQUE KEY uq_item_match (dataset_version, crawl_run_item_id),
  CONSTRAINT fk_matches_item FOREIGN KEY (crawl_run_item_id) REFERENCES crawl_run_items(id),
  CONSTRAINT fk_matches_record FOREIGN KEY (selected_record_id)
    REFERENCES price_observations(record_id),
  CONSTRAINT fk_matches_assignment_dataset FOREIGN KEY (ml_reference_assignment_id, dataset_version)
    REFERENCES ml_reference_assignments(id, dataset_version),
  CONSTRAINT fk_matches_dataset FOREIGN KEY (dataset_version)
    REFERENCES dataset_build_manifests(dataset_version),
  CONSTRAINT chk_matches_selected CHECK (
    (match_status IN ('exact','alias') AND selected_record_id IS NOT NULL)
    OR (match_status IN ('unavailable','ambiguous') AND selected_record_id IS NULL)
  ),
  INDEX idx_matches_assignment (ml_reference_assignment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Label source FK là composite, trỏ tới CHÍNH ml_samples (không phải price_observations
-- trực tiếp) — đảm bảo label source luôn là 1 sample hợp lệ của ĐÚNG dataset_version, không phải
-- bất kỳ observation nào trong core. Thêm 4 CHECK bidirectional has_label <-> label_source.
CREATE TABLE ml_samples (
  id                          BIGINT AUTO_INCREMENT PRIMARY KEY,
  dataset_version               VARCHAR(40) NOT NULL,
  record_id                      BIGINT NOT NULL,
  ml_reference_assignment_id      BIGINT NOT NULL,
  prediction_time                  DATETIME NOT NULL,
  vn_observation_date               DATE NOT NULL,
  is_daily_snapshot_selected         BOOLEAN NOT NULL DEFAULT TRUE,
  daily_snapshot_reason                VARCHAR(200),
  split                                  ENUM('train','validation','test'),
  has_label_h1                             BOOLEAN NOT NULL DEFAULT FALSE,
  label_source_record_id_h1                  BIGINT,
  has_label_h3                                 BOOLEAN NOT NULL DEFAULT FALSE,
  label_source_record_id_h3                      BIGINT,
  has_label_h7                                     BOOLEAN NOT NULL DEFAULT FALSE,
  label_source_record_id_h7                          BIGINT,
  has_label_h14                                         BOOLEAN NOT NULL DEFAULT FALSE,
  label_source_record_id_h14                               BIGINT,
  created_at                                                 DATETIME NOT NULL,
  UNIQUE KEY uq_ml_samples (dataset_version, record_id),
  INDEX idx_samples_daily_key (dataset_version, ml_reference_assignment_id, vn_observation_date),
  CONSTRAINT fk_samples_record FOREIGN KEY (record_id) REFERENCES price_observations(record_id),
  CONSTRAINT fk_samples_assignment_dataset FOREIGN KEY (ml_reference_assignment_id, dataset_version)
    REFERENCES ml_reference_assignments(id, dataset_version),
  CONSTRAINT fk_samples_label_h1 FOREIGN KEY (dataset_version, label_source_record_id_h1)
    REFERENCES ml_samples(dataset_version, record_id),
  CONSTRAINT fk_samples_label_h3 FOREIGN KEY (dataset_version, label_source_record_id_h3)
    REFERENCES ml_samples(dataset_version, record_id),
  CONSTRAINT fk_samples_label_h7 FOREIGN KEY (dataset_version, label_source_record_id_h7)
    REFERENCES ml_samples(dataset_version, record_id),
  CONSTRAINT fk_samples_label_h14 FOREIGN KEY (dataset_version, label_source_record_id_h14)
    REFERENCES ml_samples(dataset_version, record_id),
  CONSTRAINT chk_samples_label_h1 CHECK (
    (has_label_h1 = TRUE AND label_source_record_id_h1 IS NOT NULL)
    OR (has_label_h1 = FALSE AND label_source_record_id_h1 IS NULL)
  ),
  CONSTRAINT chk_samples_label_h3 CHECK (
    (has_label_h3 = TRUE AND label_source_record_id_h3 IS NOT NULL)
    OR (has_label_h3 = FALSE AND label_source_record_id_h3 IS NULL)
  ),
  CONSTRAINT chk_samples_label_h7 CHECK (
    (has_label_h7 = TRUE AND label_source_record_id_h7 IS NOT NULL)
    OR (has_label_h7 = FALSE AND label_source_record_id_h7 IS NULL)
  ),
  CONSTRAINT chk_samples_label_h14 CHECK (
    (has_label_h14 = TRUE AND label_source_record_id_h14 IS NOT NULL)
    OR (has_label_h14 = FALSE AND label_source_record_id_h14 IS NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Populate order cho self-referential FK ở `ml_samples`**: INSERT toàn bộ dòng của cửa sổ liên
quan TRƯỚC (`has_label_h*=FALSE`, `label_source_record_id_h*=NULL`), rồi UPDATE label ở bước sau
(mục 14) — tránh việc FK tự tham chiếu đòi hỏi dòng đích tồn tại ngay lúc insert.

**Validation không thể diễn đạt bằng DDL** (mục 17, MySQL không CHECK xuyên bảng):

```sql
-- Sample phải là đúng observation đã được item matching chọn.
SELECT COUNT(*) FROM ml_samples s
JOIN price_observations po ON po.record_id = s.record_id
JOIN ml_item_reference_matches m
  ON m.dataset_version = s.dataset_version AND m.crawl_run_item_id = po.crawl_run_item_id
WHERE m.selected_record_id IS NULL OR m.selected_record_id <> s.record_id;   -- phải = 0

-- selected_record_id của match phải thật sự thuộc chính crawl_run_item_id của match.
SELECT COUNT(*) FROM ml_item_reference_matches m
JOIN price_observations po ON po.record_id = m.selected_record_id
WHERE m.selected_record_id IS NOT NULL
  AND po.crawl_run_item_id <> m.crawl_run_item_id;                           -- phải = 0

-- Item phê duyệt assignment phải thuộc đúng run, hotel và ngày check-in của assignment.
SELECT COUNT(*) FROM ml_reference_assignments a
JOIN crawl_run_items cri ON cri.id = a.approving_item_warehouse_id
WHERE cri.crawl_run_id <> a.approving_run_warehouse_id
   OR cri.hotel_id IS NULL
   OR cri.hotel_id <> a.hotel_id
   OR cri.checkin_date <> a.checkin_date;                                    -- phải = 0

-- Causal availability: không sample nào được xuất hiện trước khi assignment được phê duyệt.
SELECT COUNT(*) FROM ml_samples s
JOIN ml_reference_assignments a
  ON a.id = s.ml_reference_assignment_id AND a.dataset_version = s.dataset_version
JOIN price_observations po ON po.record_id = s.record_id
WHERE po.observed_at < a.approved_at;                                         -- phải = 0

-- Observation của sample phải cùng hotel/check-in với assignment của sample.
SELECT COUNT(*) FROM ml_samples s
JOIN price_observations po ON po.record_id = s.record_id
JOIN ml_reference_assignments a
  ON a.id = s.ml_reference_assignment_id AND a.dataset_version = s.dataset_version
WHERE po.hotel_id <> a.hotel_id OR po.checkin_date <> a.checkin_date;         -- phải = 0

-- Item được match phải cùng hotel/check-in với assignment mà nó sử dụng.
SELECT COUNT(*) FROM ml_item_reference_matches m
JOIN crawl_run_items cri ON cri.id = m.crawl_run_item_id
JOIN ml_reference_assignments a
  ON a.id = m.ml_reference_assignment_id AND a.dataset_version = m.dataset_version
WHERE cri.hotel_id IS NULL
   OR cri.hotel_id <> a.hotel_id
   OR cri.checkin_date <> a.checkin_date;                                     -- phải = 0

-- Version thuật toán ở mọi assignment phải khớp manifest của chính dataset.
SELECT COUNT(*) FROM ml_reference_assignments a
JOIN dataset_build_manifests d ON d.dataset_version = a.dataset_version
WHERE a.reference_algorithm_version <> d.reference_algorithm_version;        -- phải = 0

-- Label source của cả 4 horizon phải cùng frozen series, đúng target date;
-- source và target đều phải là daily snapshot đã được chọn.
SELECT COUNT(*)
FROM (
  SELECT dataset_version, ml_reference_assignment_id, vn_observation_date,
         is_daily_snapshot_selected AS source_daily_selected,
         label_source_record_id_h1 AS target_record_id, 1 AS horizon_days
  FROM ml_samples WHERE label_source_record_id_h1 IS NOT NULL
  UNION ALL
  SELECT dataset_version, ml_reference_assignment_id, vn_observation_date,
         is_daily_snapshot_selected, label_source_record_id_h3, 3
  FROM ml_samples WHERE label_source_record_id_h3 IS NOT NULL
  UNION ALL
  SELECT dataset_version, ml_reference_assignment_id, vn_observation_date,
         is_daily_snapshot_selected, label_source_record_id_h7, 7
  FROM ml_samples WHERE label_source_record_id_h7 IS NOT NULL
  UNION ALL
  SELECT dataset_version, ml_reference_assignment_id, vn_observation_date,
         is_daily_snapshot_selected, label_source_record_id_h14, 14
  FROM ml_samples WHERE label_source_record_id_h14 IS NOT NULL
) labels
JOIN ml_samples target
  ON target.dataset_version = labels.dataset_version
 AND target.record_id = labels.target_record_id
WHERE target.ml_reference_assignment_id <> labels.ml_reference_assignment_id
   OR target.vn_observation_date <>
        DATE_ADD(labels.vn_observation_date, INTERVAL labels.horizon_days DAY)
   OR labels.source_daily_selected <> TRUE
   OR target.is_daily_snapshot_selected <> TRUE;                              -- phải = 0
```

**Precedence**:

```
reference_run_eligible(run) = run.include_reference AND crawl_runs.status='completed'
reference_evidence(item) = reference_run_eligible(run) AND item.include_reference
                            AND crawl_run_items.status='success'
training_run_eligible(run) = run.include_training AND crawl_runs.status='completed'
training_protocol_eligible(item) = training_run_eligible(run) AND item.include_training
                            AND crawl_run_items.status='success'
```

Các predicate được tách rõ theo grain: cờ/status của run được kiểm tra ở run-grain; cờ/status của
item được kiểm tra ở item-grain. Không được dùng `reference_evidence(item)` như một thuộc tính của
run.

---

## 5. Snapshot và schema compatibility

- Dump chỉ định database + 4 bảng core, không `--databases`/`--all-databases`. Scan trước restore,
  FAIL nếu có `CREATE DATABASE`/`DROP DATABASE`/`USE`.
- Không dump khi còn run `queued`/`running`.
- So sánh schema nguồn với `setup.sql` trước import.
- Mọi connection ETL pin `time_zone='+00:00'`.
- Cột `TIMESTAMP` trong 4 bảng core: `hotels.attributes_updated_at/created_at`,
  `crawl_runs.started_at/finished_at/created_at/updated_at`, `crawl_run_items.created_at/
  updated_at`, `price_observations.created_at`. Cột khác là `DATETIME`/`DATE`.
- `observed_at` copy literal, không cộng/trừ giờ; chỉ đổi VN timezone khi tính feature lịch.

---

## 6. Merge policy từng bảng

### `hotels`

1. Scraped attributes: `attributes_updated_at` mới hơn thắng (NULL = cũ nhất).
2. Booking status: `booking_status_checked_at` mới hơn thắng.
3. Tie-break: ít NULL hơn thắng, sau đó `source_code='local'` trước `'vps'`.
4. `created_at` = MIN() giữa 2 nguồn.
5. `city`/cohort **luôn luôn** lấy từ cohort manifest, không bao giờ từ `hotels.city` nguồn nào.

### `crawl_runs` / `crawl_run_items` / `price_observations`

Insert `id` mới cho cả 3 bảng, remap FK theo đúng thứ tự (`hotels → crawl_runs → crawl_run_items →
price_observations`). `crawl_runs.retry_of_run_id` (self-FK) remap 2 bước: insert toàn bộ run với
giá trị NULL trước, UPDATE lại sau khi mapping đầy đủ. `source_file`/`artifact_html_path`/
`screenshot_path` giữ nguyên string để audit, không coi là đường dẫn hợp lệ ở warehouse. Field
reference trên `price_observations` (`is_reference_room`, `reference_definition_id`,
`reference_match_status`, `reference_match_score`) reset **đúng 1 lần lúc import, vĩnh viễn không
ghi lại** — toàn bộ logic reference thật nằm ở `curated_observation_keys`/`ml_reference_assignments`/
`ml_item_reference_matches`.

### Bảng skip/rebuild/import-canonical

| Bảng | Xử lý |
|---|---|
| `hotel_room_candidates`, `hotel_reference_rooms` | Rebuild ở warehouse (mục 10), DELETE sạch mỗi lần build lại cùng batch |
| `crawler_workers`, `file_cleanup_logs`, `competitive_sets` | Không merge |
| `vn_holidays` | `scripts/import_holidays.py --replace` chạy trên warehouse DB — **script phải verify connection đang trỏ đúng warehouse trước khi replace** |
| `weather_data`, `tourism_stats` | Ngoài phạm vi v1, không import — **mặc định EDA v1 không dùng** vì không có dữ liệu để dùng; chỉ dùng nếu được cung cấp qua 1 dataset versioned bên ngoài, ghi rõ nguồn trong report |

---

## 7. Expected row-count formulas

Hai công thức riêng; không dùng chung một công thức cho mọi bảng:

```
-- Bảng 1:1 (append thuần, không dedup theo business key)
crawl_runs/crawl_run_items/price_observations:
    source_rows(B) = imported_rows(B) + directly_rejected_rows(B, scope='row_error')
                      + parent_rejected_rows(B, scope='parent_rejected')

-- hotels: merge theo business key (hotel_id), 2 nguồn có thể cùng 1 hotel_id -> 1 dòng warehouse
hotels:
    source_hotel_rows          = tổng dòng hotels ở CẢ 2 nguồn (trước merge)
    accepted_hotel_source_rows = source_hotel_rows - rejected_hotel_source_rows
    rejected_hotel_source_rows = COUNT(*) từ etl_import_rejections WHERE source_table='hotels'
    duplicate_business_key_rows = accepted_hotel_source_rows - warehouse_distinct_hotels
    warehouse_distinct_hotels  = COUNT(DISTINCT hotel_id) trong warehouse.hotels
                                  (= COUNT(DISTINCT hotel_id) trên accepted_hotel_source_rows)

Batch chỉ PASS khi: rejection count = 0 HOẶC mọi rejection đã waived (mục 4).
```

Validation report phải in đủ cả 2 nhóm số — không dùng 1 công thức tổng quát cho tất cả bảng.

**Orphan/duplicate checks** (chạy trên toàn bộ core đã import, không lọc eligibility):

```sql
SELECT COUNT(*) FROM crawl_run_items i
LEFT JOIN crawl_runs r ON r.id = i.crawl_run_id WHERE r.id IS NULL;              -- phải = 0

SELECT COUNT(*) FROM price_observations po
LEFT JOIN crawl_runs r ON r.id = po.crawl_run_id
LEFT JOIN crawl_run_items i ON i.id = po.crawl_run_item_id
LEFT JOIN hotels h ON h.hotel_id = po.hotel_id
WHERE r.id IS NULL OR i.id IS NULL OR h.hotel_id IS NULL;                        -- phải = 0

SELECT crawl_run_item_id, room_option_index, COUNT(*) n
FROM price_observations GROUP BY crawl_run_item_id, room_option_index
HAVING COUNT(*) > 1;                                                              -- phải rỗng
```

---

## 8. Ownership manifest và collision handling

Khoá ownership `(local_crawl_date, hotel/cohort, checkin_date, schedule_slot)`, đúng grain:
run-level (`etl_run_map`) chỉ giữ ngày + định danh dòng workbook; item-level (`etl_item_map`) giữ
slot cụ thể — cả hai **nullable** cho run/item pilot/audit không có trong workbook chính thức.

**Công thức hash dùng canonical JSON, không nối chuỗi thô** (tránh 2 tổ hợp field
khác nhau vô tình serialize trùng nhau nếu chỉ nối bằng dấu phân cách đơn giản — cùng convention
`json.dumps(payload, sort_keys=True, separators=(",", ":"))` đã dùng trong
`app/scraper/reference.py::room_identity_key()`):

```
schedule_manifest_row_key = SHA256(canonical_json({
    "ownership_manifest_sha256": ..., "owner_source": ..., "crawl_date": "YYYY-MM-DD",
    "schedule_slot": ..., "checkin_date": "YYYY-MM-DD", "cohort_manifest_sha256": ...
}))

schedule_day_key = SHA256(canonical_json({
    "ownership_manifest_sha256": ..., "owner_source": ..., "crawl_date": "YYYY-MM-DD",
    "cohort_manifest_sha256": ...
}))
```

**Quy tắc NULL/CHECK** (đã cưỡng chế ở DDL mục 4, nhắc lại ý nghĩa):

- `ownership_status='unassigned'` ⇒ `include_reference=FALSE AND include_training=FALSE AND
  include_eda_main=FALSE AND exclusion_reason IS NOT NULL`.
- `ownership_status<>'unassigned'` ⇒ `schedule_slot IS NOT NULL AND schedule_manifest_row_key IS
  NOT NULL` (nếu tuyên bố resolved thì phải thực sự có dữ liệu slot/key).
- Run-level: bất kỳ `include_reference`/`include_training`/`include_eda_main`=TRUE ⇒
  `planned_crawl_date IS NOT NULL AND schedule_day_key IS NOT NULL`.
- `include_eda_main=TRUE ⇒ include_eda_raw=TRUE` (cả run và item; main là tập con chặt của
  raw).

**Ownership manifest**: bảng phẳng sinh từ 2 workbook, checksum trước khi dùng. **Định nghĩa
trạng thái** (map vào `ownership_status`): `owner_success` / `non_owner_duplicate` (giữ RAW, loại
reference+training) / `owner_failure` (dataset chính vẫn loại, không fallback) /
`protocol_deviation` (sensitivity dataset riêng) / `unassigned`. **Conflicting ownership**: FAIL
cứng lúc sinh manifest. **Không average giá, không fallback âm thầm.**

---

## 9. Canonicalization

Canonicalization cố định cấp warehouse batch — **1 batch chỉ có đúng 1
`canonicalization_version`** (mục 1/2/4 — lý do kỹ thuật: `curated_observation_keys` chỉ có 1
dòng/`record_id`, không thể giữ 2 phiên bản canonical key cho cùng 1 observation; muốn đổi thuật
toán canonicalization phải build warehouse mới, không phải dataset mới). Input lấy từ
`price_observations`, output ghi vào `curated_observation_keys`. RAW `room_identity_key`/
`rate_plan_key` trong `price_observations` không bị overwrite — chỉ dùng để audit parser.

```
canonical_room_key  = room_identity_key(payload)     -- reference.py
canonical_rate_key  = rate_plan_key(payload)
canonical_series_id = hash(hotel_id, checkin_date, canonical_room_key, canonical_rate_key)
```

---

## 10. Full-history reference

Bước cuối của warehouse build (mục 3a bước 13), **không phụ thuộc `dataset_version` nào**. Module
`backend/app/warehouse/reference_builder.py` mode `full_history` — đọc canonical key từ
`curated_observation_keys`, chỉ dùng run/item có `reference_evidence=TRUE` (mục 4 precedence, đã
gồm điều kiện `status='completed'`/`'success'`), ghi vào `hotel_room_candidates`/
`hotel_reference_rooms` trong warehouse — **DELETE sạch (không chỉ đánh dấu `retired`) trước mỗi
lần build lại cùng batch**. Không gọi `repair_not_bookable_item_urls()`, không import
`DurableQueueRepository`, tái dùng trực tiếp pure function từ `app/scraper/reference.py`.

Tie-break cuối cùng (dùng chung với mục 11):

```sql
ORDER BY
    (observation_count = distinct_item_count) DESC, item_coverage DESC, distinct_run_count DESC,
    (max_occupancy IS NOT NULL AND max_occupancy <= 2) DESC, observation_count DESC,
    canonical_room_key ASC, canonical_rate_key ASC
LIMIT 1
```

`confidence_score = item_coverage NẾU unique_per_item ELSE item_coverage * 0.60`.

**Parity test**: chỉ khẳng định khớp production trên fixture không có khác biệt chủ đích (canonical
key, eligibility manifest, 2 tie-break bổ sung, causal ordering theo run). Golden test riêng cho
canonicalization/eligibility/tie-break.

---

## 11. Causal first-approval/freeze

Thuộc **dataset build lifecycle** (mục 3b bước 2), chạy riêng cho mỗi `dataset_version`. Xử lý
`crawl_runs` theo thứ tự `finished_at` tăng dần, tích luỹ bằng chứng vào tập `processed_runs`,
đánh giá candidate đứng đầu (rank trước, kiểm tra ngưỡng sau) sau mỗi run hoàn tất — đúng thời
điểm và đúng cơ chế production thật sự ra quyết định (`_refresh_reference()`/
`is_reference_candidate_eligible()`), không đánh giá theo từng `observed_at` riêng lẻ.

Khi ghi `ml_reference_assignments`, **bắt buộc copy cả field mô tả phòng/rate**
(`room_type_anchor_raw`, `room_type_norm`, `max_occupancy`, `bed_config`, `room_area`,
`breakfast_included`, `free_cancellation` — lấy qua `MAX(...)` trên nhóm `best`, cùng cách
`_refresh_reference()` candidate query đang làm), không chỉ 2 canonical key — vì
`match_reference()` ở mục 12 cần các field này để tính alias score.

```
processed_runs = {}
runs_in_order = SELECT run có reference_run_eligible(run)=TRUE (mục 4 precedence)
    ORDER BY (finished_at, source_code, source_run_id) ASC

for R in runs_in_order:
    processed_runs.add(R)
    series_touched = DISTINCT (hotel_id, checkin_date) của item thuộc R có reference_evidence=TRUE

    for (hotel_id, checkin_date) in series_touched:
        if ml_reference_assignments đã có dòng (dataset_version, hotel_id, checkin_date): continue

        eligible_item_count = COUNT(DISTINCT item) trong processed_runs thỏa
            reference_evidence(item)=TRUE và có ≥1 quan sát is_sold_out=FALSE,
            price_per_night IS NOT NULL, có canonical_room_key
        if eligible_item_count == 0: continue

        candidates = GROUP BY (canonical_room_key, canonical_rate_key) trên processed_runs:
            observation_count, distinct_item_count, distinct_run_count,
            item_coverage = distinct_item_count/eligible_item_count (cap 1.0),
            room_type_anchor_raw = MAX(room_type_raw), room_type_norm = MAX(room_type_norm),
            max_occupancy = MAX(max_occupancy), bed_config = MAX(bed_config),
            room_area = MAX(room_area), breakfast_included = MAX(breakfast_included),
            free_cancellation = MAX(free_cancellation)
        if candidates rỗng: continue

        best = rank(candidates, ORDER BY mục 10) LIMIT 1
        unique_per_item = best.observation_count == best.distinct_item_count
        if best.distinct_run_count >= REFERENCE_MIN_RUNS
           and best.item_coverage >= REFERENCE_MIN_COVERAGE and unique_per_item:
            confidence_score = best.item_coverage
            ghi ml_reference_assignments (canonical keys + toàn bộ field mô tả ở trên,
                approved_at=R.finished_at, approving_run_warehouse_id=R.id, ...)
```

Điều kiện `price_per_night IS NOT NULL` là quality gate chủ đích của warehouse replay. Production
hiện lọc theo `is_sold_out=FALSE` và canonical key; fixture parity chỉ được yêu cầu trên fixture mà
hai cách lọc cho cùng tập observation. Nếu muốn kiểm tra parity tuyệt đối với production, cấu hình
replay phải cho phép tắt quality gate bổ sung này và ghi lựa chọn vào `build_config_json`/
`build_config_sha256`.

**Ghi chú khác biệt chủ đích**: causal replay trên warehouse đã hợp nhất 2 nguồn — có thể approve
sớm hơn so với chạy riêng lẻ từng database, vì bằng chứng cả 2 nguồn gộp vào cùng `processed_runs`.
Chủ đích, không phải bug.

---

## 12. Item matching (alias-aware) và curated eligibility

### Item matching — contract với `reference.py`

`match_reference()` (`reference.py` dòng 72-86) đọc field
`room_identity_key`/`rate_plan_key` (không phải `canonical_room_key`/`canonical_rate_key`). Vì vậy
adapter field-name là bắt buộc; truyền thẳng dict canonical có thể làm `None == None` và tạo
exact-match giả. `select_best_match()` (dòng 89-100) trả `(index_or_None, status, score)`.

```
for item (crawl_run_item_id) có training_protocol_eligible = TRUE:     -- mục 4 precedence
    hotel_id, checkin_date = của item
    assignment = ml_reference_assignments WHERE (dataset_version, hotel_id, checkin_date)
    if không có assignment: bỏ qua item này, không tạo dòng match

    records = SELECT po.*, cok.canonical_room_key, cok.canonical_rate_key
              FROM price_observations po
              JOIN curated_observation_keys cok ON cok.record_id=po.record_id
              WHERE po.crawl_run_item_id = item.crawl_run_item_id
              ORDER BY po.room_option_index ASC
              -- BẮT BUỘC deterministic: hàm trả INDEX, không trả record object

    adapted_records = [
        {**r, "room_identity_key": r.canonical_room_key,
              "rate_plan_key": r.canonical_rate_key}
        for r in records
    ]

    adapted_reference = {
        "room_identity_key": assignment.canonical_room_key,
        "rate_plan_key": assignment.canonical_rate_key,
        "room_type_anchor_raw": assignment.room_type_anchor_raw,
        "max_occupancy": assignment.max_occupancy,
        "room_area": assignment.room_area,
    }

    selected_index, status, score = select_best_match(adapted_records, adapted_reference)
        -- ĐÚNG thứ tự trả về: (index_or_None, status, score)

    selected_record_id = records[selected_index].record_id NẾU selected_index IS NOT NULL ELSE NULL

    ghi ml_item_reference_matches: match_status=status, selected_record_id=selected_record_id,
        match_score=score
```

`UNIQUE(dataset_version, crawl_run_item_id)` tự cưỡng chế 1 quyết định/item — không cần cờ riêng.

### 3 tầng eligibility

**Tầng 1** (`etl_run_map`/`etl_item_map`, precedence mục 4):

```
reference_run_eligible(run)      = run.include_reference AND run.status='completed'
reference_evidence(item)         = reference_run_eligible(run) AND item.include_reference
                                    AND item.status='success'
training_run_eligible(run)       = run.include_training AND run.status='completed'
training_protocol_eligible(item) = training_run_eligible(run) AND item.include_training
                                    AND item.status='success'
```

**Tầng 2** (`price_observations` core + `ml_item_reference_matches`): `is_sold_out=FALSE`,
`is_anomaly=FALSE`, `price_per_night>0`; `match_status IN ('exact','alias')` VÀ
`selected_record_id = record_id`.

**Tầng 3** (`ml_samples`): `has_label_h1/h3/h7/h14`, `is_daily_snapshot_selected`, `split`.

**Bất biến bắt buộc tại bước populate `ml_samples`**: **một dòng chỉ được insert vào `ml_samples`
nếu đã thoả ĐỦ tầng 1+2** — nghĩa là mọi
dòng ĐANG TỒN TẠI trong `ml_samples` mặc định đã qua hết `training_protocol_eligible` +
`is_sold_out=FALSE`/`is_anomaly=FALSE`/`price_per_night>0` + match `exact`/`alias` +
`observed_at >= approved_at`. Nhờ bất biến này, pseudocode label ở mục 14 chỉ cần kiểm tra
"tồn tại trong `ml_samples`" mà không phải re-check toàn bộ điều kiện tầng 1+2 cho target.

**Công thức đầy đủ "1 observation đủ điều kiện train cho horizon k"**:

```
eligible_item(k) =
    training_protocol_eligible(item)
    AND is_sold_out=FALSE AND price_per_night>0 AND is_anomaly=FALSE
    AND lead_time >= 0
    AND checkout_date = checkin_date + 1 ngày
    AND hotel_id thuộc cohort 355 đã khoá (cohort manifest)
    AND city thuộc 5 thành phố scope (Hồ Chí Minh, Hà Nội, Vũng Tàu, Đà Lạt, Phú Quốc)
    AND ml_item_reference_matches.match_status IN ('exact','alias')
    AND ml_item_reference_matches.selected_record_id = price_observations.record_id
    AND ml_samples.is_daily_snapshot_selected = TRUE
    AND ml_samples.has_label_hK = TRUE
    AND price_observations.observed_at >= ml_reference_assignments.approved_at
```

Nếu muốn thử nghiệm với item `status='partial'` (khác `'success'` mặc định), đó phải là 1
`dataset_version`/config RIÊNG, không trộn vào dataset chính.

---

## 13. EDA contract

**EDA warehouse full-history** (dùng `include_eda_raw`/`include_eda_main` tầng 1, reference mục
10):

- Volume observation theo crawl date, theo status (success/partial/sold_out/not_bookable/error).
- Số hotel hoạt động, số checkin theo dõi mỗi ngày; khoảng trống chuỗi theo
  `(hotel, checkin, canonical_series_id)` từ `curated_observation_keys`.
- Thời lượng run, throughput item/giờ, tỷ lệ hoàn tất trước lượt crawl ngày kế.
- Phân bố lead time toàn bộ và theo city; coverage weekday/weekend/holiday/tháng checkin.
- Tỷ lệ reference approved + thời gian trung bình tới approve.
- Histogram/log-price, boxplot theo city/hotel/lead-time bucket, giá theo weekday/weekend/holiday.
- Missingness theo field và theo `scraper_version`; missing giá do sold-out tách biệt khỏi lỗi
  parser.
- Sold-out theo hotel/checkin/lead-time; `not_bookable` là trạng thái property, không gộp
  sold-out; hotel đổi trạng thái active/not_bookable theo thời gian.
- **Collision audit local/VPS**: số `non_owner_duplicate` theo `exclusion_reason`.

**EDA curated ML** (dùng đủ 3 tầng eligibility mục 12):

- Số `canonical_series_id` đủ điều kiện train theo horizon.
- Phân bố `evidence_run_count`/thời gian tới `approved_at` theo city/review-score tier — **ghi rõ
  đây là post-hoc segmentation dùng snapshot cuối kỳ của `hotels.review_score`, không phải input
  đặc trưng cho model**.
- Coverage sample thực tế so với khả năng lý thuyết.

---

## 14. Feature/label contract

- **Grain 1 sample** = 1 dòng `ml_samples` (đã qua tầng 1+2 theo bất biến mục 12).
- **Prediction timestamp** = `prediction_time`. **Target timestamp** = `vn_observation_date + k`.

### Daily duplicate resolution — tie-break không dùng `record_id`

```
daily_snapshot_key = (hotel_id, checkin_date, canonical_series_id đã frozen, vn_observation_date)
```

Tie-break: `ownership_status='owner_success'` trước → vượt quality gate (tầng 2) → `observed_at`
sớm hơn TRONG cùng ngày lịch VN → `source_code`/`source_run_id`/`source_item_id` (chỉ audit, cuối
cùng). Không dùng `record_id` làm tie-break và không average giá.

### Pseudocode label generation

```
for S in ml_samples WHERE is_daily_snapshot_selected = TRUE:
    for k in [1, 3, 7, 14]:
        target_date = S.vn_observation_date + k ngày
        targets = SELECT * FROM ml_samples T
            WHERE T.dataset_version = S.dataset_version
              AND T.ml_reference_assignment_id = S.ml_reference_assignment_id
              AND T.vn_observation_date = target_date
              AND T.is_daily_snapshot_selected = TRUE
            -- KHÔNG cần re-check tầng 1+2 ở đây — bất biến populate (mục 12) đã đảm bảo mọi dòng
            -- trong ml_samples hợp lệ sẵn

        if COUNT(targets) = 1:
            has_label_hK(S) = TRUE; label_source_record_id_hK(S) = targets[0].record_id
        elif COUNT(targets) = 0:
            has_label_hK(S) = FALSE; label_source_record_id_hK(S) = NULL
        else:
            validation FAIL — daily-dedup đã hỏng, dừng build, không tự chọn 1 trong nhiều target
```

Giá trị nhãn tính lúc build parquet bằng JOIN qua `label_source_record_id_hK →
price_observations.price_per_night`, không lưu trùng trong `ml_samples`.

### Feature lịch sử giá trước `approved_at`

**Mặc định: KHÔNG dùng observation trước `approved_at` của 1 series làm lag/rolling feature**, kể
cả cho sample có `prediction_time >= approved_at`. Lý do chọn: giữ 1 mốc cắt duy nhất cho toàn bộ
series (đơn giản, không có 2 khái niệm "biết được" khác nhau cho 2 mục đích), tránh mô hình học từ
biến động giá của giai đoạn mà hệ thống còn chưa xác nhận đây là phòng/rate-plan ổn định đáng tin.
Đây là lựa chọn có chủ đích — nếu sau này thấy quá ít lịch sử để tính lag/rolling và cần nới lỏng
(cho phép observation trước `approved_at` làm feature vì nó vẫn là sự thật đã xảy ra), phải ghi
thành quyết định mới, không tự động nới trong code.

### Giới hạn feature v1

`hotels.review_score`/`review_count`/`amenities` — không dùng (chỉ có snapshot mới nhất, không
as-of). `weather_data`/`tourism_stats` — loại khỏi feature v1 hoàn toàn (không có publication
timestamp). Feature v1: lịch, `city` (từ cohort manifest), phòng/rate plan, lịch sử giá (từ
`approved_at` trở đi), `vn_holidays`.

**Cấm tuyệt đối**: field ở mục 1.

---

## 15. Split và leakage control

**Công thức đầy đủ với purge zone**:

```
validation_start = split_train_end + purge_gap_days + 1 ngày
test_start        = split_validation_end + purge_gap_days + 1 ngày

train:      vn_observation_date <= split_train_end
purge_1:    split_train_end < vn_observation_date <= split_train_end + purge_gap_days
            -> split = NULL (không thuộc split nào)
validation: validation_start <= vn_observation_date <= split_validation_end
purge_2:    split_validation_end < vn_observation_date <= split_validation_end + purge_gap_days
            -> split = NULL
test:       vn_observation_date >= test_start
```

`split_train_end`/`split_validation_end` là **ngày lịch VN** — khi so với `prediction_time` (UTC),
quy đổi biên bằng `00:00 ngày kế tiếp tại Asia/Ho_Chi_Minh` sang UTC trước khi so sánh.

**Target không cùng split với sample**: nếu `label_source_record_id_hK` của 1 sample rơi vào split
khác (hoặc vùng purge) so với chính sample đó, cặp `(sample, horizon k)` đó bị loại khỏi việc dùng
làm target ở bước train/eval — **không xoá `has_label_hK`/`label_source_record_id_hK` gốc**, chỉ
lọc ở bước sử dụng.

Không random split làm đánh giá chính. Hotel holdout theo city (nếu làm) chỉ là robustness test
phụ. Validate riêng theo từng horizon — 14 ngày cuối cửa sổ thu thập không thể có
`has_label_h14=TRUE`.

---

## 16. Reproducibility

**Cấp warehouse** (`etl_import_batches`): dump checksum, `setup_sql_sha256`,
`cohort_manifest_sha256`, `ownership_manifest_sha256`, `etl_config_sha256`,
`canonicalization_version`/`git_commit`/`config_sha256`, `source_versions_json`.

**Cấp dataset** (`dataset_build_manifests`): `reference_algorithm_version`,
`label_config_sha256`, `feature_config_sha256`, `build_config_json`/`build_config_sha256`,
`split_train_end`/`split_validation_end`, `purge_gap_days`, `random_seed`, `library_versions_json`,
`output_parquet_sha256_json`, pipeline state và `status`/`started_at`/`finished_at`/`fail_reason`.

`build_config_sha256 = SHA256(canonical_json(build_config_json))`. JSON này lưu **đầy đủ payload
cấu hình canonical**, không chỉ các hash. Envelope top-level bắt buộc có
`import_batch_id`, `reference_algorithm_version`, `label_config`, `label_config_sha256`,
`feature_config`, `feature_config_sha256`, `reference_quality_gate`, `split_selection_policy`,
`purge_gap_days`, `random_seed` và mọi tham số có thể làm thay đổi sample/feature/label.
`label_config_sha256`/`feature_config_sha256` lần lượt là hash của hai sub-object tương ứng trong
JSON. `chk_dataset_config_projection` cưỡng chế ở DB rằng sáu cột projection khớp sáu field
top-level tương ứng và đúng JSON type. Việc recompute cryptographic hash của toàn envelope và hai
sub-object vẫn do application validation thực hiện vì canonical JSON convention thuộc Python,
không dựa vào cách MySQL serialize JSON.

Các field cấu hình bất biến sau `init_dataset_build`; split dates, runtime state, heartbeat,
attempt/circuit-breaker, library versions và output checksum là kết quả hoặc metadata vận hành nên
không nằm trong build-config identity. Mọi command phải canonicalize lại JSON, xác nhận các checksum
khớp; DB CHECK chặn trường hợp chỉ UPDATE một cột projection mà quên cập nhật JSON tương ứng.

**Checksum reproducibility KHÔNG được tính trên technical ID** (`id` tự tăng của bất kỳ bảng
nào) — chỉ tính trên nội dung có ý nghĩa (`dataset_version`, `hotel_id`, `checkin_date`, canonical
key, `approved_at`, các count/coverage...) — vì `id` hợp lệ khác nhau giữa 2 lần build "giống hệt
nhau" (AUTO_INCREMENT không đảm bảo giá trị tuyệt đối lặp lại) mà không phản ánh khác biệt thật.

`reference_algorithm_version` xuất hiện cả ở `ml_reference_assignments` (per-row, audit) và
`dataset_build_manifests` (per-dataset). Tầng ứng dụng chỉ được ghi assignment với version khớp
manifest và query integrity ở mục 4 phải xác nhận lại sau khi ghi; không có ràng buộc DB nào tự
động đảm bảo hai giá trị này bằng nhau.

---

## 17. Validation và Definition of Done

```
schema validation       — so khớp bảng/cột/khoá giữa nguồn và setup.sql
merge integrity          — orphan FK=0, duplicate=0, reconcile đúng công thức RIÊNG cho hotels
                            vs run/item/observation (mục 7), batch PASS chỉ khi rejection=0 hoặc
                            waived đủ
ownership validation     — không conflicting ownership; 4 CHECK 2 chiều ở mục 4/8 đều pass
reference parity         — khớp fixture không-khác-biệt-chủ-đích + golden test riêng
match integrity           — CHECK exact/alias<->NOT NULL, unavailable/ambiguous<->NULL; query
                            xuyên bảng ở mục 4 xác nhận sample/match/assignment cùng item,
                            hotel/check-in và ml_samples.record_id đúng selected_record_id
label integrity           — query UNION ALL mục 4 xác nhận cả h1/h3/h7/h14 cùng frozen series,
                            đúng target date và source/target đều là daily snapshot được chọn
causality tests             — 3 test dưới, cộng: 2 run trùng finished_at cho kết quả xác định như
                            nhau qua nhiều lần chạy; observed_at >= approved_at cho mọi sample
dataset leakage tests         — không feature sau prediction_time; không dùng observation trước
                              approved_at làm lag feature (trừ khi đổi quyết định mục 14 tường
                              minh); target không cùng split bị loại khỏi dùng, không bị xoá
reproducibility test            — build lại cùng input/config ra cùng checksum, KHÔNG tính technical
                              ID (mục 16)
performance benchmark             — đo runtime thực tế, không hứa trước con số
```

**3 causality test**: (1) thêm observation SAU `approved_at` không đổi assignment đã freeze;
(2) xoá observation sau 1 cutoff, rebuild — assignment approve TRƯỚC cutoff giữ nguyên; (3) build
2 lần cùng input/config → cùng `ml_reference_assignments` và checksum dataset (loại trừ technical
ID).

**Rerun giữa chừng 1 bước** (mục 18 "Invalidation graph") cũng phải pass lại đủ causality test —
không coi rerun là ngoại lệ.

**DoD — warehouse merge**: 15 bước mục 3a hoàn tất; row-count reconcile đủ (cả 2 công thức mục 7);
rejection đã review/waived; timestamp/timezone đúng; reference không 2 approved/hotel-checkin;
source database không sửa/mất. Không phụ thuộc `dataset_version` nào.

**DoD — dataset build**: 8 bước mục 3b hoàn tất, `status='pass'`; match integrity + causality test
PASS; split validate đủ từng horizon; dataset sinh lại bằng `build_dataset` (mục 18); quality
report ghi rõ mọi hàng bị loại.

**PASS gate bắt buộc cho `dataset_build_manifests`** — chỉ được chuyển `status='pass'` khi:

- `split_train_end` và `split_validation_end` khác NULL, đúng thứ tự và tạo được train/validation/
  test không rỗng theo các split bắt buộc của cấu hình.
- `build_config_json` canonicalize lại đúng `build_config_sha256`; các field riêng lẻ khớp JSON;
  `feature_config_sha256`, `label_config_sha256`, `library_versions_json` và mọi config/version bắt
  buộc đã được ghi; query version ở mục 4 xác nhận manifest khớp mọi assignment.
- Toàn bộ Parquet output tồn tại, đọc được và `output_parquet_sha256_json` đầy đủ/khớp file thật.
- Tất cả orphan/duplicate/reconciliation, ownership, match-integrity, approving-item, causality,
  leakage và reproducibility validation ở mục này đều PASS; mọi query integrity ở mục 4 trả `0`.
- Coverage report xác nhận số sample có label cho từng horizon/split bắt buộc lớn hơn `0`, đồng thời
  ghi rõ các hàng bị loại và lý do.

Nếu bất kỳ gate nào fail: giữ/đổi `status='fail'`, ghi `fail_reason`, không publish Parquet như
dataset hiện hành.

**DoD — EDA**: đủ báo cáo mục 13 cho cả 2 phạm vi; biết số chuỗi đủ label từng horizon; có data
dictionary.

**DoD — training**: baseline; ≥1 regression model tune (`RandomizedSearchCV`→`GridSearchCV`,
time-aware CV); preprocessing chỉ fit train; test chronological + purge gap; báo cáo MAE/RMSE/
MAPE-sMAPE/R²/`Accuracy@20%` (≥80%), so baseline, phân tầng horizon/city/lead-time bucket/
weekday-weekend-holiday/review-score tier (có caveat post-hoc); truy vết qua mục 16.

---

## 18. Commands dự kiến

### Invalidation graph

```
ml_reference_assignments
    └── ml_item_reference_matches
    └── ml_samples
```

Không có `ON DELETE CASCADE` trên các FK này (cố ý — tránh xoá nhầm hàng loạt khi chỉ định sửa 1
phần) nên rerun PHẢI xoá con trước cha, theo đúng thứ tự, trong 1 transaction:

```
detach_sample_labels(X):
    -- BẮT BUỘC trước mọi DELETE ml_samples: bảng có 4 self-referential FK.
    -- Set đồng thời cả cờ và FK để mọi CHECK hai chiều vẫn đúng ở cuối statement.
    UPDATE ml_samples
       SET has_label_h1=FALSE,  label_source_record_id_h1=NULL,
           has_label_h3=FALSE,  label_source_record_id_h3=NULL,
           has_label_h7=FALSE,  label_source_record_id_h7=NULL,
           has_label_h14=FALSE, label_source_record_id_h14=NULL
     WHERE dataset_version=X

Rerun causal reference (build_causal_references):
    detach_sample_labels(X)
    DELETE ml_samples WHERE dataset_version=X
    DELETE ml_item_reference_matches WHERE dataset_version=X
    DELETE ml_reference_assignments WHERE dataset_version=X
    rebuild assignments
    -- upstream nhất -> clear cả finished_at/fail_reason/output checksum/split của manifest,
    -- vì coverage label có thể đổi hoàn toàn

Rerun item matching (build_item_matches):
    detach_sample_labels(X)
    DELETE ml_samples WHERE dataset_version=X
    DELETE ml_item_reference_matches WHERE dataset_version=X
    rebuild matches
    -- clear finished_at/fail_reason/output checksum/split (matching có thể đổi -> coverage đổi)

Rerun daily sample/label (build_daily_samples_labels):
    detach_sample_labels(X)
    DELETE ml_samples WHERE dataset_version=X
    rebuild samples/labels
    -- clear split (label coverage đổi -> mốc cắt cũ có thể không còn hợp lệ), giữ nguyên
    -- finished_at nếu manifest chưa từng pass, ngược lại set lại status='running'
```

Không thay bước detach bằng `ON DELETE SET NULL`: cơ chế đó chỉ NULL hóa FK label nhưng có thể để
`has_label_hK=TRUE`, làm vi phạm CHECK hai chiều. Transaction phải rollback toàn bộ nếu detach,
delete hoặc rebuild thất bại.

Các step sau cũng có cleanup xác định:

```
Rerun finalize_split:
    UPDATE ml_samples SET split=NULL WHERE dataset_version=X
    UPDATE dataset_build_manifests
       SET split_train_end=NULL, split_validation_end=NULL
     WHERE dataset_version=X

Rerun build_features_labels:
    xóa CHỈ temp output thuộc dataset_version=X sau khi validate resolved path nằm trong
    outputs/datasets/X/tmp; output cuối chỉ được publish bằng atomic replace
    SET output_parquet_sha256_json=NULL

Rerun validate_dataset:
    xóa/replace validation report của X; không sửa derived table upstream
```

Mọi rerun: `dataset_build_manifests.status` chuyển về `'running'`, `finished_at`/`fail_reason` xoá,
`output_parquet_sha256_json` xoá (không còn đúng nữa). **Checksum reproducibility loại trừ technical
ID** (mục 16).

### State machine, crash recovery và resume

Thứ tự step cố định:

```
initialized -> causal_references -> item_matches -> samples_labels
            -> split -> features_labels -> validation -> status=pass
```

`build_dataset --dataset-version X --apply` thực hiện như sau:

1. Lấy MySQL advisory lock theo tên xác định từ `dataset_version`; nếu lock đang bị giữ thì FAIL,
   không chạy song song hai builder cho cùng version.
2. `SELECT ... FOR UPDATE` manifest, verify warehouse batch đang PASS và recompute
   `build_config_sha256` từ canonical JSON. Sai hash/config thì FAIL và yêu cầu version mới.
3. Nếu `active_step IS NOT NULL`, lần chạy trước đã crash/fail giữa step đó. Chạy cleanup của
   Kiểm tra circuit-breaker trước; nếu còn retry budget thì chạy cleanup của chính step theo
   invalidation graph (cleanup luôn bao gồm mọi output downstream có thể stale), tăng
   `active_step_attempt`, cập nhật `active_step_started_at`/`active_step_heartbeat_at` và chạy lại
   step từ đầu; không tiếp tục append vào dữ liệu dở dang.
4. Nếu `active_step IS NULL`, chọn step ngay sau `last_completed_step`. Trước khi ghi output, trong
   một transaction đặt `active_step=<step>`, đặt `active_step_attempt=1`, ghi
   `active_step_started_at=active_step_heartbeat_at=UTC_NOW()` và `status='running'`.
5. Step ghi deterministic full replacement hoặc output tạm version-scoped. Khi hoàn tất, trong một
   transaction đặt `last_completed_step=<step>`, `active_step=NULL`,
   `active_step_started_at=active_step_heartbeat_at=NULL`, `last_step_finished_at=UTC_NOW()`. Chỉ
   sau đó chạy step kế.
6. Nếu exception được bắt, đặt `status='fail'`, giữ nguyên `active_step` để lần `--apply` kế tiếp
   biết chính xác step cần cleanup/restart. Nếu process chết trước khi ghi lỗi, `status` có thể còn
   `running` nhưng `active_step` vẫn là bằng chứng phục hồi.
7. Sau validation và toàn bộ PASS gate: đặt `last_completed_step='validation'`, `status='pass'`,
   `finished_at=UTC_NOW()`. Gọi lại `--apply` trên manifest PASS là no-op sau khi verify checksum.
8. Luôn giải phóng advisory lock trong khối `finally`; mất connection cũng phải làm MySQL tự giải
   phóng lock.

**Heartbeat/monitoring**: trong khi có `active_step`, worker cập nhật `active_step_heartbeat_at`
theo chu kỳ cấu hình vận hành (khuyến nghị 60 giây) bằng update có điều kiện đúng
`dataset_version` + `active_step`. Monitoring hiển thị `stale` nếu `status='running'`, có
`active_step` và heartbeat cũ hơn
`DATASET_BUILD_STALE_SECONDS` (khuyến nghị 5 phút). Trước khi kết luận process đã chết hoặc phục
hồi, kiểm tra thêm `IS_USED_LOCK('dataset_build:' + dataset_version)`: heartbeat stale nhưng lock
còn được giữ nghĩa là process có thể đang treo/chậm, không được chạy builder thứ hai; lock đã tự
giải phóng và heartbeat stale nghĩa là chờ `--apply` phục hồi. `stale` là trạng thái hiển thị suy ra,
không thêm giá trị mới vào enum `status`.

**Circuit-breaker**: một step được thực thi tối đa `max_step_attempts` lần trong một retry cycle.
Khi `active_step_attempt >= max_step_attempts`, `--apply` không cleanup hay chạy lại; giữ
`active_step`, đặt `status='fail'` và `fail_reason` nêu circuit đã mở. Sau khi sửa nguyên nhân, người
vận hành phải gọi `--retry-failed-step --reason ... --actor ...`. Command lấy advisory lock, append
vào `retry_overrides_json` một object gồm step, previous attempts, reason, actor, UTC timestamp và
code version, đặt attempt về `1`, cập nhật heartbeat/status, rồi cleanup idempotent và chạy lại
nguyên step. Override không thay đổi `build_config_json` vì đây là metadata vận hành, không làm đổi
dataset semantics.

Rebuild một step upstream đã hoàn tất **không xảy ra âm thầm trong `--apply`**. Người vận hành phải
dùng `--rebuild-from <step>`. Sau khi lấy cùng advisory lock, command phải **ghi recovery marker
trước khi cleanup**: trong một transaction đặt `status='running'`, `active_step=<step>`,
`active_step_attempt=1`, `active_step_started_at=active_step_heartbeat_at=UTC_NOW()`, hạ
`last_completed_step` về step liền trước và clear finished/checksum/split downstream tương ứng. Chỉ
sau khi commit marker mới chạy cleanup của step đó và toàn bộ downstream, rồi đi tiếp bằng state
machine trên. Nếu crash ngay giữa cleanup, lần `--apply` sau vẫn thấy `active_step` và lặp lại
cleanup idempotent trước khi build; partial rows không thể gây UNIQUE violation. Nếu manifest đã
có một `active_step` khác, `--rebuild-from` phải FAIL và yêu cầu phục hồi step đang dở trước.

### Command

| Command | Lifecycle | Input | Output | Idempotency |
|---|---|---|---|---|
| `init_warehouse_db` | warehouse | tên DB whitelist | DB rỗng + 9 bảng ETL | Tên đã tồn tại → lỗi |
| `build_warehouse` | warehouse | 2 dump đã checksum+scan an toàn, manifest | Core đã import, `etl_import_batches` PASS | Batch mới mỗi lần |
| `validate_warehouse` | warehouse | `batch_id` | report mục 17 | Cùng batch → cùng kết quả |
| `build_warehouse_references --mode full-history` | warehouse | `batch_id` | `hotel_room_candidates`/`hotel_reference_rooms`, DELETE sạch trước ghi | Ghi đè sạch trong batch |
| `promote_warehouse` | warehouse | `batch_id` PASS | atomic replace `warehouse_current.json` | No-op nếu đã promote |
| `init_dataset_build` | dataset | `batch_id`, config | `dataset_build_manifests` status=running | `dataset_version` mới |
| `build_causal_references` | dataset | `dataset_version` | `ml_reference_assignments` | DELETE child-first + rebuild (invalidation graph) |
| `build_item_matches` | dataset | `dataset_version` | `ml_item_reference_matches` | Tương tự |
| `build_daily_samples_labels` | dataset | `dataset_version` | `ml_samples` | Tương tự |
| `finalize_split` | dataset | `dataset_version`, coverage report | UPDATE `split_*` | 1 lần/dataset_version sau khi coverage đủ |
| `build_features_labels` | dataset | curated dataset, config | parquet | Idempotent theo version |
| `validate_dataset` | dataset | parquet output | report | Idempotent |
| **`build_dataset --dataset-version X --apply`** | dataset | `dataset_version` đã được `init_dataset_build` tạo | Chạy/resume step kế tiếp; crash giữa step thì cleanup và restart trọn step đó | Idempotent theo `build_config_sha256`; manifest PASS hợp lệ → no-op |
| **`build_dataset --dataset-version X --rebuild-from STEP`** | dataset | version tồn tại, STEP hợp lệ | Invalidate STEP + downstream, hạ state rồi build lại | Không đổi cấu hình; muốn đổi config phải tạo version mới |
| **`build_dataset --dataset-version X --retry-failed-step --reason ... --actor ...`** | dataset | active step đã chạm circuit-breaker | Audit override, reset retry cycle, cleanup và chạy lại đúng active step | Bắt buộc reason/actor; không đổi build config |

---

## 19. Quyết định triển khai đã chốt

Không còn quyết định kiến trúc treo:

- `ml_reference_assignments` lưu các field mô tả phòng/rate (`room_type_anchor_raw`/
  `max_occupancy`/`room_area`/...) cần cho `match_reference()` tính alias score.
- CHECK constraint được DB thực thi trên MySQL 8.0.45.
- 4 CHECK bidirectional `has_label_hK ⇔ label_source_record_id_hK IS NOT NULL` + composite FK label
  → `ml_samples` (không phải `price_observations` trực tiếp); mọi lần xóa sample phải detach label
  theo invalidation graph mục 18.
- Chính sách lag-feature trước `approved_at`: chọn **không dùng** làm mặc định (mục 14), nêu rõ lý
  do và điều kiện nếu muốn nới lỏng sau này.
- `init_dataset_build` là lệnh duy nhất tạo `dataset_version`; `build_dataset` chỉ build/resume một
  version đã tồn tại và không được âm thầm thay đổi `build_config_sha256`.
- Resume dùng `last_completed_step`/`active_step`, cleanup rồi restart trọn step dở dang; không
  append tiếp vào output một phần và không tự rebuild upstream nếu thiếu `--rebuild-from`.
- Heartbeat giúp phân biệt process đang chạy với state stale; circuit-breaker giới hạn retry và mọi
  override sau khi sửa lỗi được lưu trong `retry_overrides_json`.
