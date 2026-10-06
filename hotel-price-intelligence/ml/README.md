# ml/ — Dataset builder (Phase 3B)

Biến một **warehouse đã PASS** thành bảng huấn luyện (Parquet) theo lifecycle riêng của dataset (không phải lifecycle warehouse).
Nguồn thiết kế: spec warehouse mục 3b/11/12/14/15/17/18 (bản 24/08 trích ở `discuss/dataset-builder-3b/ref/`; **DDL thật** ở
`backend/app/warehouse/etl_ddl.py`), CLAUDE.md mục 5–6.

## Cấu trúc
- `dataset_builder/` — thư viện (config, manifest + state machine, causal references, matching, samples/labels, split, features, export, validation).
- `scripts/` — `init_dataset_build.py`, `build_dataset.py`, `clone_warehouse_for_dev.py` (chỉ dev).
- `tests/` — 242 test: 210 thuần (không MySQL) + 32 MySQL tích hợp trên fixture warehouse dựng bằng `build_warehouse()` thật (chỉ chạy khi `ML_SMOKE=1` và operational DB không còn run queued/running).
- Chạy bằng **`eda/.venv`** (có pandas + pyarrow). Chưa cần scikit-learn/xgboost ở đây (Phase 4 dùng môi trường ML riêng).

## Quy tắc an toàn
1. **Không** nằm trong và **không** sửa `GUARDED_PATHS` của `app.warehouse.provenance` (rebuild chính thức đòi các đường đó sạch).
2. Warehouse đã promote **bất biến**: chỉ ghi `dataset_build_manifests` và `ml_*` của đúng một `dataset_version`. Tập dượt chạy trên bản sao
   `warehouse_dsdev_*` (`clone_warehouse_for_dev.py`); dataset trên warehouse hai nguồn mang `purpose=rehearsal`, không dùng làm kết quả luận văn.
3. Không đụng DB vận hành (`hotel_price_intel`), `.env`, worker. DB đích luôn có prefix `warehouse_` (guard của backend).

## Lệnh (từ `hotel-price-intelligence/ml`)
```bash
# 1) tạo dataset_version (ghi bất biến toàn bộ cấu hình)
../eda/.venv/Scripts/python.exe scripts/init_dataset_build.py --database warehouse_dsdev_20260916_2src \
    --dataset-version ds_20261005_rh1 --purpose rehearsal --anomaly-cutoff 2026-10-05T00:00:00Z
# 2) chạy / resume (6 step); crash giữa step -> --apply lần sau cleanup + chạy lại trọn step
../eda/.venv/Scripts/python.exe scripts/build_dataset.py --database warehouse_dsdev_20260916_2src --dataset-version ds_20261005_rh1 --apply
# 3) trạng thái, rebuild từ một step, retry sau circuit-breaker
../eda/.venv/Scripts/python.exe scripts/build_dataset.py --database ... --dataset-version ... --status
../eda/.venv/Scripts/python.exe scripts/build_dataset.py --database ... --dataset-version ... --rebuild-from samples_labels
../eda/.venv/Scripts/python.exe scripts/build_dataset.py --database ... --dataset-version ... --retry-failed-step --reason "..." --actor claude
```
Test: `ML_SMOKE=1 ../eda/.venv/Scripts/python.exe -m pytest tests -q` (không đặt `ML_SMOKE` thì bỏ qua test cần MySQL).

## Step và đầu ra
`causal_references` → `item_matches` → `samples_labels` → `split` → `features_labels` → `validation` (→ `status=pass`).
Đầu ra `outputs/datasets/<dataset_version>/`: `samples.parquet`, `data_dictionary.csv`, `coverage_report.json`, `sufficiency_report.json`,
`output_checksums.json` (`file_sha256` + `content_sha256` loại technical ID), `calendar_input.json`, `reports/` (báo cáo từng step).
`status=pass` = toàn vẹn + gate; **sufficiency** (bảng gate đã đăng ký: ngày eligible, mẫu có nhãn, hotel/city) báo riêng `primary_eligible`/`exploratory` cho từng horizon.

**Danh tính mã (R2-M1).** `init_dataset_build` ghim vào `build_config_json` mục `builder_code`: SHA-256 của mọi file `ml/dataset_builder/*.py`, hai CLI, `setup.sql`, `etl_ddl.py`
và **bao đóng import tĩnh** (AST) sang `backend/app/**` (canonicalize/hashing/etl_config/reference/anomaly registry…; `app.core.*` loại có chủ đích, ghi trong manifest).
`runner._session` kiểm manifest hiện tại khớp bản đã ghim **trước mọi step/cleanup/ghi**, và kiểm lại ngay trước `complete_step`/`mark_pass`; lệch ⇒ `CodeIdentityError` (liệt kê file đổi).
Đổi mã ⇒ tạo `dataset_version` mới, không rebuild cùng version bằng mã khác. `official` còn đòi các file này sạch (git) và có HEAD, ngay từ `init`. `--apply` trên dataset đã PASS chỉ kiểm hash output.
**Horizon được đánh giá (builder 1.4.0, GPT file 50).** `build_config.evaluation_horizons` (không rỗng, không trùng, ⊂ {1,3,7,14}; mặc định cả bốn = build shared audit) và
`purge_gap_days >= max(evaluation_horizons)` (mặc định = max) được kiểm **trước mọi ghi** (`init_dataset_build`, `verify_manifest`). Build riêng cho một horizon K:
`init_dataset_build.py --evaluation-horizons K` (purge = K) — một cặp boundary/dataset, ứng viên chọn biên chỉ là K (không khả thi ⇒ `fallback_ratio` exploratory, KHÔNG chọn H khác rồi gọi K sẵn sàng).
Số ngày cần (purge = K): h1 47 · h3 57 · h7 77 · h14 98 (là cận dưới lịch, không tự PASS). `sufficiency_report.json` chỉ đánh giá horizon được phép, còn lại `not_evaluated`; nhãn bốn horizon
vẫn được tính để audit (`computed_label_horizons`). `purpose=official` ⇒ validation FAIL nếu bất kỳ evaluation horizon nào không `primary_eligible`. Artifact `dataset_contract.json` (trong checksum DB +
`output_checksums.json`; 8 file bắt buộc cho Colab) ghi horizon/purge/biên split/trạng thái sufficiency/hash mã-config-lịch; `train_models.py --official` từ chối horizon ngoài whitelist trước khi tạo thư mục run
(exit 2), chạy không `--official` ngoài whitelist chỉ được khi báo cáo gắn `outside_evaluation_whitelist=true`; không chỉ định `--horizons` thì chỉ chạy horizon thuộc whitelist.
**Input lịch (R3-M1).** `data/vn_holidays.csv` là *input dữ liệu* làm đổi feature lịch nên nằm trong `build_config_json["calendar_input"]` (`name`, `sha256`, `bytes`), không nằm trong `builder_code`. `features_labels` kiểm hash
trước khi đọc DB/ghi output (lệch ⇒ `CalendarInputError`, phải tạo `dataset_version` mới), parse **chính bytes đã kiểm**, copy nguyên bytes vào `inputs/vn_holidays.csv`; `inputs/vn_holidays.csv` và
`calendar_input.json` nằm trong `output_checksums.json` + `output_parquet_sha256_json`, và validation/`--apply` kiểm snapshot == hash đã ghim == khai báo.
**Gói report tự chứa (R2-m3).** Cuối step `validation` (trước `mark_pass`), `reports/` được thay bằng đủ 6 report + `REPORTS_MANIFEST.json` (sha từng report, `dataset_version`, `build_config_sha256`,
`builder_code_sha256`); các mục `reports/*` được ghi vào `output_parquet_sha256_json` nên `verify_pass_outputs`/`--apply` kiểm cùng cơ chế với Parquet. `cleanup validation` chỉ gỡ sản phẩm của validation
(Parquet + hash của `features_labels` giữ nguyên để retry validation chạy được).

## Giới hạn đã biết
- Dữ liệu còn mỏng (cần ≥ 70 + 3k ngày chuỗi mẫu để một horizon k qua gate đã đăng ký) ⇒ dataset sớm chỉ "exploratory".
- Chỉ mẫu thuộc chuỗi đã được duyệt reference (causal freeze) ⇒ thiên lệch sống sót ở lead time dài; luôn báo coverage theo lead time.
- Lỗi parser N1 (bữa sáng) và các dòng phụ (single-guest/Basic) nằm sẵn trong dữ liệu; primary giữ "legacy exact-unique", không sửa.

## Huấn luyện / đánh giá (Phase 4) — `training/`, `scripts/train_models.py`, `configs/train_v1.yaml`
Đọc `samples.parquet` của một dataset đã xuất (không đọc DB), chạy **baseline** (persistence, trung vị tỉ lệ theo city×lead-time), **Ridge**, **Random Forest**,
**XGBoost** (tùy chọn). Tune RF/XGB bằng `RandomizedSearchCV` rồi `GridSearchCV` quanh ứng viên, cross-validation **theo thời gian có purge** (train: `d + h < val_start`)
chỉ trên tập train. Chọn mô hình theo **validation** (Accuracy@20% rồi MAE); **test chỉ tính một lần** cho mô hình đã chọn + baseline. Metric trên thang giá:
Accuracy@20%, MAE, RMSE, MAPE/sMAPE/median APE, R², bias, độ chính xác hướng (±2%); báo cáo thêm theo `inference_mode`, city, lead-time bucket.
```bash
# Python cần scikit-learn (anaconda base có; eda/.venv chưa có). XGBoost/SHAP chưa cài ⇒ xgb tự bỏ qua kèm lý do.
python scripts/train_models.py --dataset-dir ../../outputs/datasets/<dataset_version> --horizons 7 --models ridge,rf,xgb
# Test (13 test, dataset giả lập đúng schema Parquet, không cần MySQL):
python -m pytest tests/test_training.py -q
```
Mỗi horizon ghi `h{k}_report.json` (cấu hình + sha256, feature, dataset sha, `evaluation_status` lấy từ `sufficiency_report.json`; không `primary_eligible` ⇒ cảnh báo
"exploratory", không dùng làm kết quả cuối), `h{k}_model_<tên>.joblib`, `h{k}_predictions_<tên>.parquet` và `h{k}_test_metrics_by_*.csv`.
Chưa làm: SHAP, ablation theo nhóm feature (cấu hình `exclude_groups` đã sẵn), mô hình chuỗi (stretch).

**Run là giao dịch (R2-M3).** `train_models.py` xác minh dataset + provenance **trước khi tạo bất kỳ thư mục nào**, rồi xây trong thư mục tạm `.<run_id>.tmp-<uuid>` (`run_manifest.json` state `running`,
cập nhật sau từng horizon). Lỗi/Ctrl+C ⇒ state `fail` + lý do, đổi tên `<run_id>.failed-<ts>-<id>` (giữ làm bằng chứng, không bao giờ là run hợp lệ). Chỉ khi mọi horizon xong mới ghi checksum từng file
+ state `pass` rồi đổi tên nguyên tử thành `<run_id>`; tên đã tồn tại ⇒ từ chối. `training.run_transaction.verify_run_dir(path)` kiểm run PASS (manifest + checksum + đủ horizon). Run đi kèm
`environment_resolved.txt`, bản sao `CODE_MANIFEST.json` và `COLAB_MANIFEST.json` (nếu có). `CODE_MANIFEST.json` được tính lại aggregate `code_sha256` (không tin giá trị khai báo); `--official` trên gói Colab
bắt buộc có `--colab-manifest` **và manifest đó phải nối mật mã với code đang chạy (R3-M2)**: `schema_version=2`, `code_manifest_sha256` == SHA-256 bytes `CODE_MANIFEST.json` thật,
`code_sha256` == aggregate đã tính lại, cùng `created_at`/tên archive code, có archive dataset đúng tên; sai/rỗng/của gói khác ⇒ exit 3 trước khi tạo thư mục. `verify_run_dir` còn đòi tên thư mục == `run_id` hợp lệ (R3-m1:
thư mục tạm `.tmp-` đã ghi `pass` vẫn không hợp lệ). Tham số CLI sai (`--models ridg`, `--horizons ,,`, horizon ngoài cấu hình, `--run-id` lạ) bị từ chối với mã thoát 2.
Mã thoát: 0 ok · 1 lỗi giữa chừng (run đánh dấu fail) · 2 tham số/thư mục đã tồn tại · 3 dataset/provenance không qua xác minh.

## Chạy huấn luyện trên Google Colab Pro (GPU) — quyết định của người dùng 06/10/2026
Huấn luyện/tuning chạy trên Colab (không chạy nặng trên máy chính, nơi còn crawler + MySQL). Pipeline chỉ đọc Parquet nên không cần MySQL/backend.
1. Máy chính: `python scripts/package_for_colab.py --dataset-dir ../../outputs/datasets/<dataset_version>` → `outputs/colab/{ml_train_pkg_*.zip, dataset_<version>.zip, COLAB_MANIFEST.json}` (kèm SHA-256). Dataset zip gồm **7 file bắt buộc** (Parquet, dictionary, 2 report, `output_checksums.json`
   + bằng chứng lịch `calendar_input.json`, `inputs/vn_holidays.csv`); thiếu bất kỳ file nào ⇒ packager dừng **trước khi tạo zip**; `verify_dataset` trên Colab hash lại cả hai file lịch
   và kiểm `calendar_input.json` khai báo đúng SHA-256 của snapshot (`calendar_sha256` đi vào `run_manifest.json` và báo cáo).
2. Tải 3 file lên cùng một thư mục Google Drive; mở `notebooks/train_colab.ipynb` trên Colab (Runtime → GPU), sửa `DRIVE_DIR`/`DATASET_VERSION`, chạy lần lượt các ô
   (kiểm SHA-256 → giải nén → `pip install -r requirements-train.txt` → `train_models.py --device cuda` → bảng tóm tắt). Kết quả ghi về Drive.
3. Chỉ **XGBoost** dùng GPU (`--device cuda`, XGBoost ≥ 2.0); Random Forest/Ridge của scikit-learn luôn chạy CPU. LSTM/Transformer (stretch) sẽ dùng GPU nhưng chưa có mã.
4. Tái lập: mỗi `h{k}_report.json` ghi `config_sha256` (đã gồm `device`), phiên bản `python/sklearn/pandas/numpy/xgboost`, `xgb_device`, hash nội dung dataset. Seed cố định theo `configs/train_v1.yaml`.
5. Lưu ý Phase 6 (serving): model XGBoost huấn luyện trên GPU vẫn nạp được trên CPU, nhưng môi trường serving cần cài cùng phiên bản `xgboost`/`scikit-learn`; thêm vào requirements của backend khi triển khai API.
Dữ liệu chỉ dùng cho mục đích nghiên cứu học thuật (CLAUDE.md 4.2e) — tải lên Drive của chính tài khoản người dùng, không chia sẻ công khai.
