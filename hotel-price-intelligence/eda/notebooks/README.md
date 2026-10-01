# EDA notebooks

Nguồn thẩm quyền: `../../EDA_CURATED_PLAN.md` và `../../../discuss/eda-curated-implementation/`.

## Cài đặt (1 lần)

Từ `eda/`:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-eda.txt
.venv/Scripts/python.exe -m ipykernel install --user --name eda_venv --display-name "Python (eda)"
```

## Chạy (GPT review 12 B1/M1/M2: PHẢI qua runner, không mở notebook trực tiếp)

```bash
cd hotel-price-intelligence/eda
python run_wave_a.py
```

`run_wave_a.py`:

1. Suy `repo_root`/`src_dir` từ chính `__file__` của nó (không đoán CWD trong notebook — đây là nguồn
   lỗi B1 cũ).
2. Tạo `outputs/<analysis_id>/` **fail-if-exists** (không bao giờ ghi đè phân tích cũ).
3. Đặt biến môi trường `EDA_SRC_DIR`/`EDA_ANALYSIS_DIR` **trước** khi mở kernel — notebook đọc 2 biến
   này ở cell đầu, fail rõ ràng nếu thiếu (không tự đoán).
4. Chạy **bản copy** của notebook qua `nbclient`, CWD pin cứng vào `eda/notebooks/`.
5. Ghi notebook đã chạy vào `outputs/<analysis_id>/executed_notebooks/`.
6. Chỉ khi mọi bước trên xong mới ghi `artifact_manifest.json` (SHA-256 + size mọi file khác) — đây là
   bước **cuối cùng**. Lỗi bất kỳ → `outputs/<analysis_id>/FAILED.json`, không để lại artifact "trông
   như PASS".

Mở trực tiếp trong Jupyter Lab (`jupyter lab notebooks/`) chỉ dùng để **soạn/sửa** notebook — chạy thủ
công sẽ báo lỗi thiếu `EDA_SRC_DIR`/`EDA_ANALYSIS_DIR` ngay ở cell đầu, đúng chủ đích.

## `01_warehouse_full_history_eda.ipynb`

Wave A — mô tả toàn bộ lịch sử đã hợp nhất trong warehouse (đọc qua
`outputs/warehouse/warehouse_current.json`), không phải causal training dataset. Toàn bộ logic thật
nằm ở `src/wave_a.py` (orchestration + provenance/manifest + quality findings), `src/queries.py` (query
catalog — mọi bảng giá/availability/reference/turnover được aggregate trong SQL; turnover population
trả một dòng scalar/canonical series, không tải room-option payload),
`src/sql_builders.py` (sinh SQL quantile/histogram), `src/metrics.py` (tính tỷ lệ/cờ trên bảng đã
aggregate), `src/plots.py` (vẽ hình từ bảng aggregate), `src/report.py` + `src/dictionary.py`
(EDA_REPORT.md / DATA_DICTIONARY.md) — notebook chỉ gọi vào và hiển thị/vẽ hình.

Registry duy nhất cho artifact: `src/publication.py` (bảng + hình, metric ID, scope, grain, denominator).
`src/coverage_matrix.py` ánh xạ **từng bullet** của plan 7.1–7.12 → metric ID / artifact / test ID;
`src/tests/test_coverage_matrix.py` parse thẳng `EDA_CURATED_PLAN.md` và **fail** nếu bullet thiếu
mapping/artifact/test. Artifact mỗi lần chạy (ngoài các bảng `tables/*.csv` và hình `figures/*.png`):
`input_manifest.json`, `EDA_REPORT.md`, `DATA_DICTIONARY.md`, `EDA_COVERAGE_MATRIX.md/.csv`,
`TABLE_METADATA.csv` (scope/grain/denominator từng bảng), `eda_summary.json` (kèm thời gian từng metric
và peak memory), `quality_findings.csv`, `dataset_readiness_by_horizon.csv`, `artifact_manifest.json`.

## `02_curated_ml_eda.ipynb`

Wave B — **scaffold, chưa chạy được với ý nghĩa đầy đủ**. Chỉ preflight qua
`queries.wave_b_dataset_version_readiness`; raise rõ ràng nếu chưa có `dataset_version` nào vừa
`status='pass'` vừa đủ dữ liệu ở cả 3 bảng `ml_*`. Không fallback sang full-history reference của
Notebook 01.

## Output

Notebook **source** trong thư mục này luôn output-free (`execution_count=null`, `outputs=[]`) — ép
bằng `src/tests/test_notebooks.py`. `../outputs/` bị gitignore — không commit kết quả chạy.

## Test

```bash
# Test thuần (không cần MySQL)
.venv/Scripts/python.exe -m pytest src/tests -q

# Kèm test tích hợp MySQL (disposable DB/warehouse fixture tự tạo/tự xoá, KHÔNG đụng warehouse current)
EDA_SMOKE=1 .venv/Scripts/python.exe -m pytest src/tests -q
```

Fixture integration dựng bằng chính `app.warehouse.batch.build_warehouse()` (`src/tests/warehouse_fixture.py`,
số liệu biết trước ở `fixture_specs.py`): `price_wh` (1 nguồn) và `collision_wh` (2 nguồn có collision).
Test quality check **tiêm vi phạm** vào bản sao rồi hoàn tác (`warehouse_fixture.mutated`) để chứng minh
check thực sự phát hiện được, không chỉ trả 0 trên dữ liệu sạch.
