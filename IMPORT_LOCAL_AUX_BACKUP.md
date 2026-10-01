# Hướng dẫn import dữ liệu `local_aux_backup`

Tài liệu này dùng chung cho mọi gói backup crawl nằm trong:

`hotel-price-intelligence/data/local_aux_backup/<CRAWL_DATE>/`

Máy nhận phải sử dụng MySQL và database local của chính nó. Không kết nối tới MySQL của máy đã
crawl, không dùng chung `datadir`, và không sửa database nguồn trên máy đã crawl.

## 1. Chọn gói dữ liệu

Mỗi thư mục ngày phải có đủ bốn file:

- `local_aux_backup_core_<CRAWL_DATE>.sql`
- `local_aux_backup_core_<CRAWL_DATE>.sql.sha256`
- `manifest.md`
- `SHA256SUMS.txt`

Các gói hiện có:

| Crawl date | Phạm vi snapshot | Ghi chú |
|---|---:|---|
| `2026-09-29` | 1 run, 3.540 item, 29.923 observation | Snapshot đầu tiên |
| `2026-10-01` | 2 run, 7.080 item, 59.280 observation | Snapshot tích lũy, đã chứa run ngày 29/09 |

Luôn đọc `manifest.md` trong đúng thư mục ngày trước khi import. Không tự cộng hai snapshot tích lũy
như hai nguồn độc lập: gói `2026-10-01` đã chứa dữ liệu của gói `2026-09-29`. Nếu run cũ đã được nhập,
quy trình nhận phải deduplicate theo provenance/run mapping.

## 2. Quy tắc bắt buộc

1. Mọi lệnh MySQL phải chạy bằng MySQL local của máy nhận, thường là `127.0.0.1`.
2. Không chạy file dump trực tiếp vào database vận hành hiện có như `hotel_price_intel`. Dump chứa
   `DROP TABLE`/`CREATE TABLE` cho bốn bảng lõi và có thể xóa dữ liệu đang có.
3. Nguồn đúng là `local_aux_backup`; không tự đổi thành `local_aux` hoặc nguồn khác.
4. Không dùng `run_id`, `item_id` hoặc `record_id` nguồn như ID toàn cục. Quy trình merge/warehouse
   phải remap ID.
5. Trước khi xử lý database vận hành trên máy nhận, xác nhận không còn run `queued` hoặc `running`.
6. Chỉ promote sau khi restore-test, build và validation đều PASS.

## 3. Kiểm tra checksum

Chọn ngày cần nhận, ví dụ gói mới nhất:

```powershell
$crawlDate = "2026-10-01"
$handoffDir = Join-Path "<REPOSITORY_ROOT>\hotel-price-intelligence\data\local_aux_backup" $crawlDate
$dumpName = "local_aux_backup_core_${crawlDate}.sql"
$dumpPath = Join-Path $handoffDir $dumpName
$sidecarPath = "$dumpPath.sha256"

if (-not (Test-Path -LiteralPath $dumpPath -PathType Leaf)) {
  throw "Không tìm thấy dump: $dumpPath"
}
if (-not (Test-Path -LiteralPath $sidecarPath -PathType Leaf)) {
  throw "Không tìm thấy checksum sidecar: $sidecarPath"
}

$expectedHash = ((Get-Content -LiteralPath $sidecarPath -Raw).Trim() -split '\s+')[0].ToUpperInvariant()
$actualHash = (Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256).Hash.ToUpperInvariant()
if ($actualHash -ne $expectedHash) {
  throw "SHA-256 không khớp; không được import file này."
}

Write-Host "Checksum hợp lệ: $actualHash"
Get-Content -LiteralPath (Join-Path $handoffDir "manifest.md")
```

Đối chiếu thêm `SHA256SUMS.txt`. Nếu manifest, sidecar hoặc dump có hash khác, dừng quy trình.

## 4. Restore kiểm tra vào staging

Staging chỉ dùng để kiểm tra dump, không phải database vận hành. Ví dụ dưới đây dùng login path
`localaux`; thay bằng login path quản trị local của máy nhận nếu tên khác.

```powershell
$mysql = "C:\mysql-aux\bin\mysql.exe"
$dateToken = $crawlDate -replace '-', ''
$stagingDb = "wh_staging_auxbackup_$dateToken"

$existing = & $mysql --login-path=localaux -N -e `
  "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='$stagingDb';"
if ($existing -ne "0") {
  throw "Staging $stagingDb đã tồn tại; kiểm tra thủ công, không tự xóa."
}

& $mysql --login-path=localaux -e `
  "CREATE DATABASE $stagingDb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

$sourceCommand = "source " + ($dumpPath -replace '\\','/')
& $mysql --login-path=localaux $stagingDb -e $sourceCommand

& $mysql --login-path=localaux -N -e @"
SELECT 'hotels', COUNT(*) FROM $stagingDb.hotels
UNION ALL SELECT 'runs', COUNT(*) FROM $stagingDb.crawl_runs
UNION ALL SELECT 'items', COUNT(*) FROM $stagingDb.crawl_run_items
UNION ALL SELECT 'observations', COUNT(*) FROM $stagingDb.price_observations;
"@
```

So sánh số dòng với mục `Database snapshot counts` trong `manifest.md` của chính gói đã chọn. Không
dùng số liệu của một ngày khác. Nếu bất kỳ bảng nào lệch, dừng import.

## 5. Hợp nhất vào dữ liệu của máy nhận

Nếu máy nhận đã có dữ liệu, dùng warehouse builder để tạo staging riêng, merge hotel theo business
key và remap toàn bộ ID. Không append trực tiếp core dump vào database vận hành.

Source manifest phải dùng thông tin lấy từ gói đã chọn:

```json
{
  "source_code": "local_aux_backup",
  "source_priority": 1,
  "dump_path": "<ABSOLUTE_PATH_TO_SELECTED_SQL_DUMP>",
  "dump_sha256": "<SHA256_FROM_SELECTED_SIDECAR>",
  "dump_taken_at": "<VALUE_RECORDED_FOR_THIS_HANDOFF>",
  "schema_sha256": "<SCHEMA_HASH_EXPECTED_BY_THE_RECEIVING_BATCH>",
  "source_version_json": {
    "scraper_version_range": ["2.3.0", "2.3.0"],
    "selector_version": "booking-2026-08-17",
    "mysql_version": "8.0.45"
  }
}
```

`source_priority` chỉ là ví dụ; phải chọn cùng với các nguồn khác để không trùng. Cohort manifest và
ownership manifest cũng phải khai báo nguồn `local_aux_backup`.

Chạy từ `hotel-price-intelligence/backend`:

```powershell
$python = ".\venv\Scripts\python.exe"
$warehouseDb = "warehouse_${dateToken}_with_auxbackup"

& $python scripts\init_warehouse_db.py --database $warehouseDb --dry-run
& $python scripts\init_warehouse_db.py --database $warehouseDb

& $python scripts\build_warehouse.py `
  --database $warehouseDb `
  --source-manifest <SOURCE_MANIFEST_JSON> `
  --cohort-manifest <COHORT_HISTORY_JSON> `
  --ownership-manifest <OWNERSHIP_MANIFEST_JSON> `
  --base-dir <REPOSITORY_ROOT>

& $python scripts\validate_warehouse.py `
  --database $warehouseDb `
  --batch-id <BATCH_ID_FROM_BUILD>
```

Không chạy `promote_warehouse.py` cho đến khi người vận hành review báo cáo và xác nhận số lượng từng
nguồn. Không dùng `--allow-dirty-provenance` cho bản chính thức.

## 6. Kiểm tra cuối

- Database vận hành cũ trên máy nhận vẫn còn nguyên.
- Dump và manifest thuộc cùng một `CRAWL_DATE`.
- Checksum của dump khớp sidecar.
- Số dòng staging khớp manifest của gói đã chọn.
- Nguồn được ghi nhận là `local_aux_backup`.
- Snapshot tích lũy không tạo run trùng với snapshot đã nhập trước đó.
- Không có ID nguồn nào được dùng như ID toàn cục nếu chưa qua bảng map.
- Không promote nếu validation chưa PASS.

## Prompt ngắn cho Codex trên máy nhận

```text
Đọc toàn bộ IMPORT_LOCAL_AUX_BACKUP.md rồi làm theo.

CRAWL_DATE = <YYYY-MM-DD>

Máy này phải dùng MySQL/database local của chính nó; tuyệt đối không kết nối tới database trên máy đã
crawl. Chọn đúng gói tại hotel-price-intelligence/data/local_aux_backup/CRAWL_DATE, kiểm tra sidecar
SHA-256 và manifest, rồi restore vào một staging database mới. Không import trực tiếp vào
hotel_price_intel đang có dữ liệu. Kiểm tra xem snapshot đã chứa những run nào để tránh nhập trùng.
Nếu cần hợp nhất lâu dài, dùng warehouse builder để remap ID và giữ source_code=local_aux_backup.
Trước mọi bước promote hoặc xóa staging, báo kết quả validation và hỏi tôi.
```
