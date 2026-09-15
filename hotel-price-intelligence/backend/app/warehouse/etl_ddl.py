"""DDL 11 bang ETL/dataset cua warehouse - chep DUNG theo WAREHOUSE_EDA_ML_SPEC.md muc 4.

Quy uoc: khong "cai tien" DDL o day. Neu thay DDL sai thi sua SPEC truoc roi moi sua file nay, vi
spec la tai lieu da duoc duyet (APPROVED FOR IMPLEMENTATION) va moi rang buoc CHECK trong do deu co
ly do ghi kem trong spec.

SAI LECH CO Y DUY NHAT so voi spec muc 4 (phat hien 2026-09-16 khi chay smoke test tren MySQL
8.0.45 that, KHONG phai do doc tai lieu): spec ghi `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4` khong
kem COLLATE. Tren MySQL 8, thieu COLLATE => bang lay collation mac dinh cua charset la
`utf8mb4_0900_ai_ci`, trong khi 4 bang core tu `setup.sql` dung `utf8mb4_unicode_ci`. FK giua 2 cot
VARCHAR khac collation bi MySQL tu choi:

    ERROR 3780 (HY000): Referencing column 'hotel_id' and referenced column 'hotel_id' in
    foreign key constraint 'fk_ml_ref_hotel' are incompatible.

Tuc DDL nguyen van cua spec KHONG CHAY DUOC. Da them `COLLATE=utf8mb4_unicode_ci` cho ca 11 bang
(khong chi bang co FK VARCHAR) - neu chi sua 1 bang thi moi JOIN sau nay giua cot VARCHAR cua ETL
va core se nem "Illegal mix of collations" luc query, mot loi kho truy hon nhieu. Da bao GPT va
cap nhat lai spec muc 4.

Thu tu trong `ETL_TABLES` la thu tu tao bang - phai ton trong FK (bang cha truoc bang con).
`ml_samples` co 4 self-FK; MySQL cho phep khai bao ngay trong CREATE TABLE.

4 bang cuoi (dataset-scoped) duoc TAO nhung KHONG populate o warehouse build - dataset build la
lifecycle rieng (muc 3b), se lam o thread sau.
"""
from __future__ import annotations

ETL_IMPORT_BATCHES = """
CREATE TABLE etl_import_batches (
  batch_id                       VARCHAR(40)  PRIMARY KEY,
  warehouse_database             VARCHAR(64)  NOT NULL,
  status                         ENUM('running','pass','fail') NOT NULL DEFAULT 'running',
  started_at                     DATETIME NOT NULL,
  finished_at                    DATETIME NULL,
  setup_sql_sha256               CHAR(64) NOT NULL,
  source_manifest_sha256          CHAR(64) NOT NULL,
  cohort_manifest_sha256              CHAR(64),
  ownership_manifest_sha256           CHAR(64),
  etl_config_sha256                    CHAR(64),
  canonicalization_version              VARCHAR(50) NOT NULL,
  canonicalization_git_commit           VARCHAR(64) NOT NULL,
  canonicalization_config_sha256        CHAR(64) NOT NULL,
  fail_reason                               TEXT,
  notes                                       TEXT,
  INDEX idx_batches_status (status),
  INDEX idx_batches_started (started_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

ETL_IMPORT_SOURCES = """
CREATE TABLE etl_import_sources (
  import_batch_id       VARCHAR(40) NOT NULL,
  source_code           VARCHAR(20) NOT NULL,
  source_priority       SMALLINT NOT NULL,
  dump_path             VARCHAR(1000) NOT NULL,
  dump_sha256           CHAR(64) NOT NULL,
  dump_taken_at         DATETIME NOT NULL,
  schema_sha256         CHAR(64) NOT NULL,
  source_version_json   JSON NOT NULL,
  PRIMARY KEY (import_batch_id, source_code),
  CONSTRAINT fk_sources_batch FOREIGN KEY (import_batch_id)
    REFERENCES etl_import_batches(batch_id),
  CONSTRAINT chk_sources_code_format CHECK (source_code REGEXP '^[a-z][a-z0-9_]{0,19}$'),
  CONSTRAINT chk_sources_priority CHECK (source_priority >= 0),
  UNIQUE KEY uq_sources_priority (import_batch_id, source_priority)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

ETL_IMPORT_REJECTIONS = """
CREATE TABLE etl_import_rejections (
  id                     BIGINT AUTO_INCREMENT PRIMARY KEY,
  import_batch_id         VARCHAR(40) NOT NULL,
  source_code              VARCHAR(20) NOT NULL,
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
  CONSTRAINT fk_rejections_source FOREIGN KEY (import_batch_id, source_code)
    REFERENCES etl_import_sources(import_batch_id, source_code),
  CONSTRAINT chk_rejections_waived CHECK (
    waived = FALSE OR (waived_reason IS NOT NULL AND waived_by IS NOT NULL)
  ),
  INDEX idx_rejections_batch (import_batch_id),
  INDEX idx_rejections_source (import_batch_id, source_code),
  INDEX idx_rejections_parent (import_batch_id, source_table, source_parent_pk_value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

ETL_RUN_MAP = """
CREATE TABLE etl_run_map (
  import_batch_id       VARCHAR(40) NOT NULL,
  source_code           VARCHAR(20) NOT NULL,
  source_run_id         BIGINT NOT NULL,
  warehouse_run_id      BIGINT NOT NULL,
  source_status          VARCHAR(20) NOT NULL,
  source_created_at      DATETIME NOT NULL,
  planned_crawl_date       DATE NULL,
  schedule_day_key           VARCHAR(100) NULL,
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
  CONSTRAINT fk_run_map_source FOREIGN KEY (import_batch_id, source_code)
    REFERENCES etl_import_sources(import_batch_id, source_code),
  CONSTRAINT fk_run_map_warehouse_run FOREIGN KEY (warehouse_run_id) REFERENCES crawl_runs(id),
  CONSTRAINT chk_run_map_eda_invariant CHECK (
    (include_reference = FALSE AND include_training = FALSE) OR include_eda_main = TRUE
  ),
  CONSTRAINT chk_run_map_eda_hierarchy CHECK (
    include_eda_main = FALSE OR include_eda_raw = TRUE
  ),
  CONSTRAINT chk_run_map_schedule CHECK (
    (include_reference = FALSE AND include_training = FALSE AND include_eda_main = FALSE)
    OR (planned_crawl_date IS NOT NULL AND schedule_day_key IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

ETL_ITEM_MAP = """
CREATE TABLE etl_item_map (
  import_batch_id       VARCHAR(40) NOT NULL,
  source_code           VARCHAR(20) NOT NULL,
  source_item_id        BIGINT NOT NULL,
  warehouse_item_id     BIGINT NOT NULL,
  schedule_slot            VARCHAR(20) NULL,
  schedule_manifest_row_key  CHAR(64) NULL,
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
  CONSTRAINT fk_item_map_source FOREIGN KEY (import_batch_id, source_code)
    REFERENCES etl_import_sources(import_batch_id, source_code),
  CONSTRAINT fk_item_map_warehouse_item FOREIGN KEY (warehouse_item_id)
    REFERENCES crawl_run_items(id),
  CONSTRAINT chk_item_map_eda_invariant CHECK (
    (include_reference = FALSE AND include_training = FALSE) OR include_eda_main = TRUE
  ),
  CONSTRAINT chk_item_map_eda_hierarchy CHECK (
    include_eda_main = FALSE OR include_eda_raw = TRUE
  ),
  CONSTRAINT chk_item_map_ownership_unassigned CHECK (
    ownership_status <> 'unassigned'
    OR (include_reference = FALSE AND include_training = FALSE AND include_eda_main = FALSE
        AND exclusion_reason IS NOT NULL)
  ),
  CONSTRAINT chk_item_map_ownership_resolved CHECK (
    ownership_status = 'unassigned'
    OR (schedule_slot IS NOT NULL AND schedule_manifest_row_key IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

ETL_OBSERVATION_MAP = """
CREATE TABLE etl_observation_map (
  import_batch_id       VARCHAR(40) NOT NULL,
  source_code           VARCHAR(20) NOT NULL,
  source_record_id      BIGINT NOT NULL,
  source_record_sha256   CHAR(64) NOT NULL,
  warehouse_record_id   BIGINT NOT NULL,
  imported_at            DATETIME NOT NULL,
  PRIMARY KEY (import_batch_id, source_code, source_record_id),
  UNIQUE KEY uq_observation_map_warehouse (warehouse_record_id),
  CONSTRAINT fk_observation_map_batch FOREIGN KEY (import_batch_id)
    REFERENCES etl_import_batches(batch_id),
  CONSTRAINT fk_observation_map_source FOREIGN KEY (import_batch_id, source_code)
    REFERENCES etl_import_sources(import_batch_id, source_code),
  CONSTRAINT fk_observation_map_warehouse_record FOREIGN KEY (warehouse_record_id)
    REFERENCES price_observations(record_id),
  INDEX idx_observation_map_batch (import_batch_id),
  INDEX idx_observation_map_source (import_batch_id, source_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CURATED_OBSERVATION_KEYS = """
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

DATASET_BUILD_MANIFESTS = """
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
  anomaly_registry_file_sha256    CHAR(64) NULL,
  anomaly_registry_mode           ENUM('evaluation_asof','retrospective_full') NULL,
  anomaly_registry_cutoff_at      DATETIME NULL,
  anomaly_source_member_checksums JSON NULL,
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
        AND output_parquet_sha256_json IS NOT NULL
        AND anomaly_registry_file_sha256 IS NOT NULL
        AND anomaly_registry_mode IS NOT NULL
        AND anomaly_source_member_checksums IS NOT NULL)
  ),
  CONSTRAINT chk_dataset_anomaly_mode CHECK (
    anomaly_registry_mode IS NULL
    OR anomaly_registry_mode <> 'evaluation_asof'
    OR anomaly_registry_cutoff_at IS NOT NULL
  ),
  CONSTRAINT chk_dataset_fail_reason CHECK (
    status <> 'fail' OR fail_reason IS NOT NULL
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

ML_REFERENCE_ASSIGNMENTS = """
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

ML_ITEM_REFERENCE_MATCHES = """
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

ML_SAMPLES = """
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# Thu tu = thu tu tao bang (cha truoc con). KHONG sap xep lai neu khong kiem tra lai FK.
ETL_TABLES: tuple[tuple[str, str], ...] = (
    ("etl_import_batches", ETL_IMPORT_BATCHES),
    ("etl_import_sources", ETL_IMPORT_SOURCES),
    ("etl_import_rejections", ETL_IMPORT_REJECTIONS),
    ("etl_run_map", ETL_RUN_MAP),
    ("etl_item_map", ETL_ITEM_MAP),
    ("etl_observation_map", ETL_OBSERVATION_MAP),
    ("curated_observation_keys", CURATED_OBSERVATION_KEYS),
    ("dataset_build_manifests", DATASET_BUILD_MANIFESTS),
    ("ml_reference_assignments", ML_REFERENCE_ASSIGNMENTS),
    ("ml_item_reference_matches", ML_ITEM_REFERENCE_MATCHES),
    ("ml_samples", ML_SAMPLES),
)

ETL_TABLE_NAMES: tuple[str, ...] = tuple(name for name, _ in ETL_TABLES)

# 4 bang core tai dung tu setup.sql - warehouse import vao day, khong tao lai DDL o module nay.
CORE_TABLES: tuple[str, ...] = ("hotels", "crawl_runs", "crawl_run_items", "price_observations")

# Bang derived duoc rebuild o buoc 15 (muc 10) - DELETE sach truoc moi lan build lai.
DERIVED_REFERENCE_TABLES: tuple[str, ...] = ("hotel_room_candidates", "hotel_reference_rooms")
