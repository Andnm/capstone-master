# Hướng dẫn cài máy backup và chạy crawl `local_aux`

Tài liệu này dành cho một máy Windows mới chỉ có Codex. Mục tiêu trước mắt là dùng nhánh
`sub-crawl-backup` để thay máy `local_aux` trong ngày **29/09/2026**, chạy đúng lịch trong
`aux_local_crawl_sampling_master.xlsx`, dùng đúng danh sách `link_hotel_data_expanded.xlsx`, rồi
lưu dữ liệu bàn giao dưới `hotel-price-intelligence/data`.

Tài liệu cũng có prompt tái sử dụng cho các ngày crawl sau.

## 1. Cấu hình đã khóa cho ngày 29/09/2026

| Hạng mục | Giá trị phải dùng |
|---|---|
| Nhánh Git | `sub-crawl-backup` |
| Commit hiện tại khi soạn tài liệu | `9bbce81` |
| Crawl date | `2026-09-29` |
| Giờ dự kiến | `00:30`, múi giờ `Asia/Ho_Chi_Minh` |
| File lịch | `outputs/aux-local-crawl-planner-20260901/aux_local_crawl_sampling_master.xlsx` |
| Sheet/dòng | `AUX_CRAWL_PLAN`, dòng có `Crawl date = 2026-09-29` |
| File khách sạn | `link_hotel_data_expanded.xlsx` |
| Số khách sạn hợp lệ hiện tại | 354 |
| Số ngày check-in | 10, không thêm/bớt/thay ngày |
| Số item dự kiến | `354 × 10 = 3.540` |
| Lưu HTML/ảnh | Tắt |
| Nơi lưu bàn giao | `hotel-price-intelligence/data/local_aux_backup/2026-09-29/` |

Mười ngày check-in của dòng 29/09/2026, theo đúng slot trong workbook:

| Slot | Check-in |
|---|---|
| AN1 | `2026-10-02` |
| AN4 | `2026-10-23` |
| AN5 | `2026-10-05` |
| AN6 | `2026-10-19` |
| AN7 | `2026-10-06` |
| AN8 | `2026-10-13` |
| AN9 | `2026-10-18` |
| AF1 | `2026-10-26` |
| AF2 | `2026-11-24` |
| AF3 | `2027-02-04` |

Hash để kiểm tra đúng hai file tại commit nói trên:

```text
link_hotel_data_expanded.xlsx
SHA-256 4a0aa5a39ad722bec361bae453c4da90596077f4c7336ed5e2f8691c04cb9fe4

aux_local_crawl_sampling_master.xlsx
SHA-256 a122bf2970471ae454ad448ee9ef222a13f1a05dd751c0bcb1d40bb7e3ce84dd
```

Các hash này chỉ khóa lượt 29/09. Những lần sau phải đọc file đang có trên nhánh, không ép dùng lại
hash cũ nếu cohort hoặc lịch đã được cập nhật có chủ đích.

## 2. Phân biệt hai loại dump database

### 2.1. Bộ file trong ảnh không dùng để khởi tạo máy backup

Các file:

```text
local_20260915_155533.manifest.json
local_20260915_155533.schema.sql
local_20260915_155533.sql
local_dump.stderr.log
```

là snapshot của `local_primary`. Manifest ghi rõ `source_code = local_primary`. File SQL chỉ chứa bốn
bảng lõi `hotels`, `crawl_runs`, `crawl_run_items`, `price_observations`, phục vụ import staging/
warehouse. Nó không chứa đầy đủ durable queue, reference candidates, link health và các bảng hỗ trợ
để tiếp tục vận hành crawler.

Không restore bộ này lên máy backup rồi gọi dữ liệu mới là `local_aux`. Làm vậy sẽ trộn lịch sử máy
chính vào nguồn máy phụ và làm sai provenance.

### 2.2. Dump cần có trước khi thay máy `local_aux`

Nếu máy mới thay đúng vai trò `local_aux` trong một ngày, cần lấy **full operational dump của chính
database `local_aux` gần nhất**, gồm tất cả bảng. Việc này giữ liên tục `run_id`, `item_id`,
`record_id`, queue và trạng thái crawler.

Trên máy `local_aux` cũ, chỉ tạo dump khi không còn run `queued` hoặc `running`. Ví dụ:

```powershell
$mysqlBin = "C:\mysql-aux\bin"
$backupDir = "D:\transfer\local_aux_before_20260929"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

& "$mysqlBin\mysqldump.exe" `
  --login-path=localaux `
  --single-transaction `
  --routines `
  --triggers `
  --events `
  --no-tablespaces `
  --set-charset `
  --hex-blob `
  --result-file="$backupDir\local_aux_operational_before_20260929.sql" `
  hotel_price_intel

Get-FileHash -Algorithm SHA256 `
  "$backupDir\local_aux_operational_before_20260929.sql" |
  Format-List
```

Copy file SQL và hash sang máy mới bằng USB hoặc kênh truyền file tin cậy. Không commit dump vào Git.

Nếu không lấy được operational dump của `local_aux` cũ, vẫn có thể crawl vào database mới, nhưng đó
phải được coi là một nguồn vật lý mới. Không được tự gắn nhãn `local_aux` rồi nhập chung, vì technical
ID sẽ bắt đầu lại từ 1 và có thể va chạm với nguồn cũ. Trường hợp này cần thiết kế thêm source code và
ownership trước khi nhập warehouse; không tự xử lý trong lượt crawl khẩn cấp.

Trong ngày thay máy, không cho máy `local_aux` cũ tạo run song song. Sau lượt crawl, phải restore full
operational dump mới từ máy backup về máy cũ trước khi máy cũ chạy tiếp.

## 3. Chuẩn bị máy mới

### 3.1. Kiểm tra dung lượng trước

Không bắt đầu tải/cài/restore trước khi biết dung lượng trống trên ổ sẽ chứa repository, MySQL data
và output. Chạy:

```powershell
Get-PSDrive -PSProvider FileSystem |
  Select-Object Name,
    @{Name="UsedGiB";Expression={[math]::Round($_.Used / 1GB, 1)}},
    @{Name="FreeGiB";Expression={[math]::Round($_.Free / 1GB, 1)}} |
  Format-Table -AutoSize
```

Ước lượng thực tế từ máy chính tại thời điểm soạn tài liệu:

| Thành phần | Dung lượng tham khảo |
|---|---:|
| Python virtual environment | khoảng 0,21 GB |
| Frontend `node_modules` | khoảng 0,44 GB |
| Core SQL dump hiện có | khoảng 0,71 GB |
| Toàn bộ `hotel-price-intelligence` trên máy chính, gồm cả data hiện có | khoảng 2,68 GB |

Máy backup còn **50 GB trống là đủ** cho lượt crawl một ngày nếu không lưu HTML/ảnh. Tuy nhiên phải
tính cả các bản cùng tồn tại trong lúc bàn giao: dump nhận vào, database MySQL đã restore, full dump
sau-run và core dump sau-run.

Dùng công thức bảo thủ sau sau khi đã có full operational dump:

```text
Dung lượng cần tối thiểu = 15 GB + 4 × kích thước operational dump
```

Ví dụ dump 1 GB cần tối thiểu khoảng 19 GB; dump 5 GB cần khoảng 35 GB. Với 50 GB, chỉ nên tiếp tục
khi công thức trên còn chừa ít nhất 10 GB sau dự tính. Nếu operational dump lớn hơn khoảng 6 GB, cần
đo lại thay vì mặc định 50 GB là đủ.

Có thể kiểm tra tự động:

```powershell
$dumpPath = "D:\transfer\local_aux_before_20260929\local_aux_operational_before_20260929.sql"
$targetDrive = "D"  # đổi thành C nếu mọi thứ được đặt trên ổ C
$dumpGiB = (Get-Item -LiteralPath $dumpPath).Length / 1GB
$freeGiB = (Get-PSDrive -Name $targetDrive).Free / 1GB
$requiredGiB = [math]::Ceiling(15 + 4 * $dumpGiB)

[pscustomobject]@{
  DumpGiB = [math]::Round($dumpGiB, 2)
  FreeGiB = [math]::Round($freeGiB, 2)
  RequiredGiB = $requiredGiB
  Pass = $freeGiB -ge $requiredGiB
} | Format-List

if ($freeGiB -lt $requiredGiB) {
  throw "Không đủ dung lượng an toàn để cài, restore và tạo dump bàn giao."
}
```

Nếu MySQL data ở ổ C nhưng repository/output ở ổ D, phải kiểm tra riêng cả hai ổ. Trước khi tạo run,
ổ chứa MySQL data nên còn tối thiểu 15 GB và tổng dung lượng theo công thức phải PASS. Không xóa dump
đầu vào hoặc output để giải phóng chỗ cho tới khi đã kiểm tra hash và copy bản bàn giao sang nơi khác.

### 3.2. Kiểm tra phần mềm đã có

Chạy inventory trước. Việc một lệnh không tồn tại chỉ có nghĩa là thành phần đó cần cài; không phải
lỗi bắt buộc dừng toàn bộ:

```powershell
$commands = "git", "py", "python", "node", "npm", "winget"
foreach ($name in $commands) {
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  [pscustomobject]@{
    Command = $name
    Installed = $null -ne $cmd
    Path = if ($cmd) { $cmd.Source } else { $null }
  }
}

git --version
py -0p
node --version
npm --version

$chromeCandidates = @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chromeCandidates | Where-Object { Test-Path -LiteralPath $_ }

Get-Service -Name "MySQL*" -ErrorAction SilentlyContinue
Get-Command mysql, mysqld, mysqldump -ErrorAction SilentlyContinue
```

Yêu cầu đạt:

- Git chạy được.
- Có Python 3.12; Python khác đã cài không thay thế yêu cầu này nếu không tạo được venv 3.12.
- Node.js từ 20.9 trở lên và có npm.
- Có Chrome.
- Có MySQL Server/client 8.0 phù hợp hoặc chuẩn bị dùng bản portable ở mục 3.4.

Nếu thành phần đã có và đạt phiên bản thì dùng lại, không reinstall hoặc upgrade chỉ vì tài liệu có
lệnh cài. Nếu đã có MySQL đang chạy, phải kiểm tra port, version, datadir và database hiện hữu trước;
không initialize đè lên data directory cũ.

### 3.3. Chỉ cài công cụ nền còn thiếu

Mở PowerShell bằng quyền Administrator và chỉ chạy dòng tương ứng với thành phần thiếu/không đạt:

```powershell
winget install --id Git.Git -e --source winget
winget install --id Python.Python.3.12 -e --source winget
winget install --id OpenJS.NodeJS.LTS -e --source winget
winget install --id Google.Chrome -e --source winget
```

Sau khi có cài mới, đóng PowerShell và mở lại, rồi kiểm tra lại toàn bộ inventory:

```powershell
git --version
py -3.12 --version
node --version
npm --version
```

Frontend hiện dùng Next.js 16 nên Node phải từ 20.9 trở lên. Python 3.12 là baseline khuyến nghị cho
virtual environment của backend. Chrome cần thiết cho Selenium; `webdriver-manager` sẽ lấy driver
phù hợp ở lần chạy đầu, vì vậy máy phải có Internet.

### 3.4. Chỉ cài MySQL 8 portable nếu máy chưa có MySQL phù hợp

Nếu máy đã có MySQL 8.0, client tools và một datadir riêng có thể dùng an toàn thì không tải/cài thêm.
Ghi lại kết quả `SELECT VERSION()`, port và datadir rồi chuyển sang phần restore.

Nếu chưa có, máy không cần MySQL Installer nhưng vẫn phải tải MySQL Server ZIP. Nên dùng cùng nhánh
MySQL 8.0 với máy cũ, tốt nhất cùng patch version. Project đã được kiểm tra trên MySQL 8.0.45.

Tải `mysql-8.0.x-winx64.zip` từ trang chính thức, giải nén thành `C:\mysql-aux`. MySQL ZIP trên
Windows cần Microsoft Visual C++ Redistributable. Hướng dẫn chính thức:

- <https://dev.mysql.com/doc/refman/8.0/en/windows-install-archive.html>
- <https://dev.mysql.com/doc/refman/8.0/en/windows-installation.html>

Tạo `C:\mysql-aux\my.ini` với nội dung sau:

```ini
[client]
port=3306
default-character-set=utf8mb4

[mysqld]
basedir=C:/mysql-aux
datadir=C:/mysql-aux-data
port=3306
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
default-time-zone=+00:00
```

Khởi tạo và cài service chỉ khi `C:\mysql-aux-data` là datadir mới, rỗng và chưa thuộc MySQL instance
nào. Chỉ dùng `--initialize-insecure` trong trường hợp đó, sau đó đặt mật khẩu root ngay:

```powershell
New-Item -ItemType Directory -Force -Path C:\mysql-aux-data | Out-Null

& C:\mysql-aux\bin\mysqld.exe `
  --defaults-file=C:\mysql-aux\my.ini `
  --initialize-insecure `
  --console

& C:\mysql-aux\bin\mysqld.exe `
  --defaults-file=C:\mysql-aux\my.ini `
  --install MySQLAux

Start-Service MySQLAux
& C:\mysql-aux\bin\mysql.exe -u root --skip-password
```

Trong MySQL shell, đặt mật khẩu và thoát:

```sql
ALTER USER 'root'@'localhost' IDENTIFIED BY 'MAT_KHAU_ROOT_MANH_TU_TAO';
EXIT;
```

Lưu credential mã hóa cho command-line client; lệnh này sẽ hỏi mật khẩu và không ghi mật khẩu thẳng
trong script:

```powershell
& C:\mysql-aux\bin\mysql_config_editor.exe set `
  --login-path=localaux `
  --host=127.0.0.1 `
  --user=root `
  --password

& C:\mysql-aux\bin\mysql.exe --login-path=localaux -e "SELECT VERSION();"
```

## 4. Clone đúng nhánh

Ví dụ clone vào `D:\MSE\CAPSTONE`:

```powershell
New-Item -ItemType Directory -Force -Path D:\MSE | Out-Null
Set-Location D:\MSE

git clone `
  --branch sub-crawl-backup `
  --single-branch `
  https://github.com/Andnm/capstone-master.git `
  CAPSTONE

Set-Location D:\MSE\CAPSTONE
git status --short --branch
git rev-parse --short HEAD
```

Lượt 29/09 phải thấy nhánh `sub-crawl-backup`. Hai file Excel đầu vào đã được Git track trên nhánh.
Dữ liệu trong `hotel-price-intelligence/data` bị `.gitignore` loại ra, nên clone sẽ không mang theo
dump hoặc output cũ.

Kiểm tra file:

```powershell
Get-FileHash -Algorithm SHA256 .\link_hotel_data_expanded.xlsx
Get-FileHash -Algorithm SHA256 `
  .\outputs\aux-local-crawl-planner-20260901\aux_local_crawl_sampling_master.xlsx
```

## 5. Restore database vận hành `local_aux`

Giả sử operational dump đã copy tới:

```text
D:\transfer\local_aux_before_20260929\local_aux_operational_before_20260929.sql
```

Tạo database rỗng và restore:

```powershell
& C:\mysql-aux\bin\mysql.exe --login-path=localaux -e `
  "CREATE DATABASE IF NOT EXISTS hotel_price_intel CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

& C:\mysql-aux\bin\mysql.exe `
  --login-path=localaux `
  --default-character-set=utf8mb4 `
  hotel_price_intel `
  -e "source D:/transfer/local_aux_before_20260929/local_aux_operational_before_20260929.sql"
```

Kiểm tra các bảng và các run đang mở:

```powershell
& C:\mysql-aux\bin\mysql.exe --login-path=localaux hotel_price_intel -e `
  "SHOW TABLES; SELECT status, COUNT(*) AS n FROM crawl_runs GROUP BY status;"
```

Không tạo run mới nếu còn run `queued` hoặc `running` từ snapshot. Trước khi chạy code của nhánh,
kiểm tra migration chống dead-link đã có:

```powershell
& C:\mysql-aux\bin\mysql.exe --login-path=localaux hotel_price_intel -e `
  "SELECT COUNT(*) AS dead_link_column FROM information_schema.columns WHERE table_schema='hotel_price_intel' AND table_name='crawl_run_items' AND column_name='dead_link_confirmation'; SHOW TABLES LIKE 'hotel_link_health';"
```

Nếu thiếu cả column và table, chạy đúng migration sau một lần:

```powershell
& C:\mysql-aux\bin\mysql.exe `
  --login-path=localaux `
  --default-character-set=utf8mb4 `
  hotel_price_intel `
  -e "source D:/MSE/CAPSTONE/hotel-price-intelligence/backend/app/database/migrations/20260903_dead_link_confirmation.sql"
```

Không chạy mù toàn bộ migration. Nếu chỉ thiếu một phần hoặc lệnh báo duplicate, dừng và đối chiếu
schema trước khi sửa.

## 6. Cấu hình backend và frontend

### 6.1. Cài dependency

```powershell
Set-Location D:\MSE\CAPSTONE\hotel-price-intelligence\backend
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt

Set-Location D:\MSE\CAPSTONE\hotel-price-intelligence\scraper-frontend
npm ci
```

### 6.2. Tạo `backend/.env`

Tạo user ứng dụng riêng trong MySQL, cấp quyền trên đúng database, rồi lưu mật khẩu đó trong `.env`.
Không dùng mật khẩu minh họa và không commit `.env`.

```powershell
& C:\mysql-aux\bin\mysql.exe --login-path=localaux
```

```sql
CREATE USER IF NOT EXISTS 'hotel_app'@'127.0.0.1'
  IDENTIFIED BY 'MAT_KHAU_APP_MANH_TU_TAO';
GRANT ALL PRIVILEGES ON hotel_price_intel.* TO 'hotel_app'@'127.0.0.1';
FLUSH PRIVILEGES;
EXIT;
```

```env
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=hotel_price_intel
DB_USER=hotel_app
DB_PASSWORD=MAT_KHAU_APP_MANH_TU_TAO
DB_POOL_SIZE=5
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
DISPLAY_TIMEZONE=Asia/Ho_Chi_Minh
```

### 6.3. Tạo `scraper-frontend/.env.local`

```env
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

### 6.4. Gắn identity nếu operational database đã có anomaly registry

Trước tiên kiểm tra bảng identity có tồn tại không:

```powershell
& C:\mysql-aux\bin\mysql.exe --login-path=localaux hotel_price_intel -e `
  "SHOW TABLES LIKE 'anomaly_registry_source_identity';"
```

Nếu bảng tồn tại, sau khi `.env` hoạt động, lệnh sau phải tạo `local_aux` hoặc báo đã đúng từ trước.
Nếu báo database đã gắn source khác, dừng ngay:

```powershell
Set-Location D:\MSE\CAPSTONE\hotel-price-intelligence\backend
.\venv\Scripts\python.exe scripts\provision_anomaly_source_identity.py --source-code local_aux
```

Nếu bảng không tồn tại trên operational dump cũ, không chạy migration anomaly chỉ để phục vụ máy
collector và không chạy lệnh provision. Crawl/dump raw data vẫn là nhiệm vụ chính; Excel export ở mục
9.1 có thể không dùng được, còn hai dump ở mục 9.2–9.3 vẫn phải tạo.

Máy collector không chạy warehouse, EDA, recompute reference hoặc anomaly canonical. Các bước đó chỉ
chạy trên `local_primary` sau khi nhận dump.

## 7. Khởi động và kiểm tra

```powershell
Set-Location D:\MSE\CAPSTONE\hotel-price-intelligence
Set-ExecutionPolicy -Scope Process Bypass
.\start_project.ps1
```

Script bật ba tiến trình: FastAPI, durable worker và frontend. Kiểm tra:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/scraper/worker/health
```

Mở <http://127.0.0.1:3000>. Chỉ tiếp tục khi backend trả `status = ok` và UI báo worker online.

Ở lần đầu, Selenium có thể mất thêm thời gian để tải ChromeDriver. Không tạo crawl run nếu worker vẫn
offline.

## 8. Chạy lượt ngày 29/09/2026

1. Mở workbook lịch, sheet `AUX_CRAWL_PLAN`, tìm `Crawl date = 29/09/2026`.
2. Đối chiếu đủ đúng 10 slot ở mục 1. Không chọn AN2/AN3 vì hai slot này đã ngừng từ 03/09.
3. Upload `D:\MSE\CAPSTONE\link_hotel_data_expanded.xlsx`.
4. Preflight phải cho 354 link hợp lệ thuộc đúng 5 thành phố. Nếu số khác 354, dừng trước khi tạo run.
5. Chọn đúng 10 ngày check-in. Giao diện có thể tự sắp xếp ngày tăng dần; điều này không làm đổi tập
   ngày.
6. Không tích **Lưu bằng chứng trang**.
7. Bấm **Bắt đầu cào** một lần. Ghi lại `run_id`.
8. Tổng item của run phải là 3.540. Nếu sai, dừng worker và điều tra, không tạo run bù ngay.

Theo dõi tại trang chi tiết job. Trạng thái terminal là `completed` hoặc `failed`; trong run hoàn tất,
từng item có thể là `success`, `partial`, `sold_out`, `not_bookable` hoặc `error`. Không xem
`sold_out/not_bookable` là lỗi parser một cách tự động.

Không restart MySQL, backend hoặc worker khi run đang chạy nếu hệ thống vẫn có heartbeat. Durable
queue có thể tiếp tục sau crash, nhưng restart không cần thiết sẽ làm tăng thời gian và rủi ro retry.

## 9. Bàn giao sau khi crawl xong

Tạo thư mục output:

```powershell
$crawlDate = "2026-09-29"
$outDir = "D:\MSE\CAPSTONE\hotel-price-intelligence\data\local_aux_backup\$crawlDate"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
```

### 9.1. Excel của đúng run, nếu API cho phép export

Thay `<RUN_ID>` bằng run vừa hoàn tất:

```powershell
$runId = <RUN_ID>
Invoke-WebRequest `
  -Uri "http://127.0.0.1:8000/api/scraper/runs/$runId/export" `
  -OutFile "$outDir\crawl_run_${runId}_${crawlDate}.xlsx"
```

API có thể trả HTTP 409 nếu anomaly registry của collector chưa được sync. Đây là fail-closed có chủ
đích vì file Excel chứa cột `is_anomaly`. Không chạy anomaly pipeline trên máy phụ chỉ để ép export.
Trong trường hợp đó, dùng dump SQL ở hai mục sau làm bản bàn giao canonical và ghi rõ `Excel export =
blocked_by_registry` trong manifest vận hành.

### 9.2. Full operational dump để trả máy `local_aux` cũ

Dump này gồm mọi bảng và dùng để máy cũ tiếp tục chạy mà không reset ID/trạng thái:

```powershell
& C:\mysql-aux\bin\mysqldump.exe `
  --login-path=localaux `
  --single-transaction `
  --routines `
  --triggers `
  --events `
  --no-tablespaces `
  --set-charset `
  --hex-blob `
  --result-file="$outDir\local_aux_operational_after_${crawlDate}.sql" `
  hotel_price_intel
```

### 9.3. Core dump để đưa về `local_primary`/warehouse

```powershell
& C:\mysql-aux\bin\mysqldump.exe `
  --login-path=localaux `
  --single-transaction `
  --no-tablespaces `
  --set-charset `
  --complete-insert `
  --result-file="$outDir\local_aux_core_${crawlDate}.sql" `
  hotel_price_intel `
  hotels crawl_runs crawl_run_items price_observations
```

Tạo hash cho mọi file bàn giao:

```powershell
Get-ChildItem -LiteralPath $outDir -File |
  Get-FileHash -Algorithm SHA256 |
  Format-Table Path, Hash -AutoSize
```

Tối thiểu phải ghi kèm trong một manifest vận hành:

- `source_code = local_aux`;
- hostname của máy backup;
- branch và commit;
- crawl date, run ID, start/finish time;
- 10 check-in date;
- SHA-256 của input hotel list, calendar và mọi output;
- tổng item và các count `success/partial/sold_out/not_bookable/error`;
- MySQL, Python, Node, Chrome và scraper/selector version;
- việc Excel export thành công hay bị HTTP 409.

Không `git add -f` thư mục `data`. Chuyển output bằng USB/kênh file riêng. Trước lượt crawl kế tiếp,
restore full operational dump sau-run về máy `local_aux` cũ và xác nhận `MAX(crawl_runs.id)` cùng run
29/09 đã hiện diện.

## 10. Prompt dùng lại trong Codex

Mỗi lần dùng, chỉ thay `CRAWL_DATE`. Prompt bắt Codex đọc ngày từ workbook, không tự chọn ngày khác.

```text
Bạn đang thao tác trên máy Windows dùng để thay thế tạm thời collector local_aux của dự án Hotel
Price Intelligence.

CRAWL_DATE = 2026-09-29
BRANCH = sub-crawl-backup
REPO = D:\MSE\CAPSTONE
CALENDAR = D:\MSE\CAPSTONE\outputs\aux-local-crawl-planner-20260901\aux_local_crawl_sampling_master.xlsx
HOTEL_FILE = D:\MSE\CAPSTONE\link_hotel_data_expanded.xlsx
OUTPUT_ROOT = D:\MSE\CAPSTONE\hotel-price-intelligence\data\local_aux_backup

Trước tiên đọc toàn bộ D:\MSE\CAPSTONE\AUX_BACKUP_CRAWL_SETUP.md và tuân thủ nó. Luôn kiểm kê phần
mềm và dung lượng trước. Chỉ cài thành phần còn thiếu hoặc không đạt phiên bản; không reinstall hay
upgrade thành phần đã đạt. Nếu môi trường đã sẵn sàng thì đi thẳng sang kiểm tra database/project.

Mục tiêu là chạy đúng một crawl run cho CRAWL_DATE bằng giao diện web scraper hiện có. Đây là run do
người vận hành chủ động tạo qua web, không tạo cron/scheduled automation.

Các invariant bắt buộc:
1. Trước mọi thao tác cài đặt, báo inventory của Git, Python, Node/npm, Chrome, MySQL server/client,
   port 3306 và dung lượng trống trên mọi ổ sẽ dùng. Chỉ cài phần thiếu. Không initialize đè MySQL
   datadir đã tồn tại.
2. Tính dung lượng tối thiểu theo `15 GB + 4 × kích thước operational dump`. Chỉ tiếp tục khi ổ chứa
   MySQL và output đáp ứng kế hoạch, đồng thời còn ít nhất 10 GB dự phòng sau ước tính. Máy có 50 GB
   trống thường đủ, nhưng kết luận phải dựa trên kích thước dump thật.
3. Đúng nhánh BRANCH; báo commit đang chạy. Không tự pull/merge sang nhánh khác khi chưa được yêu cầu.
4. Database phải là bản tiếp tục hợp lệ của chính local_aux. Không restore dump local_primary và
   không dùng database rỗng rồi giả danh local_aux. Nếu thiếu full operational dump local_aux, dừng
   trước khi tạo run và báo đúng blocker.
5. Xác nhận không có run queued/running cũ và máy local_aux cũ không chạy song song trong ngày này.
6. Đọc sheet AUX_CRAWL_PLAN, tìm duy nhất dòng Crawl date = CRAWL_DATE. Lấy tất cả check-in cell
   không trống trong các slot active của dòng đó. Không dùng ngày từ trí nhớ, không tự thay ngày.
7. Đối chiếu cột Số ngày hợp lệ và các cột kiểm tra. Với ngày từ 03/09/2026 trở đi phải có đúng 10
   ngày active: AN1, AN4-AN9, AF1-AF3; AN2/AN3 phải trống. Mọi check-in phải >= CRAWL_DATE và không
   trùng nhau. Nếu workbook không có dòng ngày này hoặc invariant fail, dừng trước khi tạo run.
8. Dùng đúng HOTEL_FILE. Chạy preflight qua web; hiện tại cohort v2 chuẩn là 354 link hợp lệ thuộc 5
   thành phố. Nếu file trên nhánh đã thay đổi có chủ đích ở tương lai, báo số mới và hash trước khi
   chạy. Không tự sửa/xóa khách sạn để ép preflight pass.
9. Không bật lưu HTML/screenshot. Chỉ bấm Bắt đầu cào một lần. Sau khi tạo, ghi run_id và xác minh
   total item = số link hợp lệ × số check-in.
10. Theo dõi đến terminal. Không tạo run thứ hai để xử lý lỗi; trước tiên dùng retry item lỗi của cùng
   run khi đúng luồng hiện có. Phân biệt success, partial, sold_out, not_bookable và error.
11. Khi hoàn tất, lưu output vào OUTPUT_ROOT\CRAWL_DATE. Ưu tiên tạo:
   - Excel đúng run nếu API export không bị registry gate chặn;
   - full operational dump mọi bảng để trả về máy local_aux cũ;
   - core dump bốn bảng hotels/crawl_runs/crawl_run_items/price_observations để bàn giao về máy chính;
   - manifest vận hành và SHA-256 của input/output.
   Không commit raw data hoặc dump vào Git.
12. Cập nhật đúng một dòng CRAWL_LOG tương ứng CRAWL_DATE sau khi có số liệu terminal; không sửa
    LOCAL_REFERENCE, VPS_REFERENCE hoặc công thức lịch.

Riêng khi CRAWL_DATE = 2026-09-29, trước khi tạo run phải in lại và xác nhận đúng tập ngày:
2026-10-02, 2026-10-23, 2026-10-05, 2026-10-19, 2026-10-06, 2026-10-13,
2026-10-18, 2026-10-26, 2026-11-24, 2027-02-04. Tập ngày là điều cần khớp; UI được phép hiển thị
theo thứ tự tăng dần.

Không hỏi lại các thao tác thông thường đã nằm trong phạm vi trên. Chỉ dừng trước khi tạo run nếu có
nguy cơ sai nguồn database, sai nhánh, sai file, sai lịch, trùng run, worker offline, preflight không
khớp hoặc cần thay đổi ngoài phạm vi. Trong lúc run dài đang chạy, báo tiến độ ngắn gọn theo mốc và
không để máy sleep.
```

Những ngày sau chỉ cần thay dòng `CRAWL_DATE`, giữ nguyên cơ chế đọc workbook. Nếu ngày nằm ngoài phạm
vi hiện có của lịch (sau 30/11/2026), prompt phải dừng và yêu cầu một workbook lịch mới; không tự ngoại
suy slot.

## 11. Checklist nghiệm thu

- [ ] Đã kiểm kê phần mềm; chỉ cài thành phần còn thiếu hoặc không đạt phiên bản.
- [ ] Dung lượng đã PASS công thức `15 GB + 4 × operational dump` và còn ít nhất 10 GB dự phòng.
- [ ] Git đang ở `sub-crawl-backup`, working tree không có thay đổi bất ngờ.
- [ ] MySQL, backend, worker và frontend đều chạy; worker online.
- [ ] Database là operational continuation của `local_aux`, không phải dump `local_primary`.
- [ ] Không có run cũ `queued/running`; máy `local_aux` cũ không chạy song song.
- [ ] Preflight đúng file và đủ 354 link hợp lệ cho lượt 29/09.
- [ ] Chọn đúng 10 check-in, không lưu HTML/ảnh.
- [ ] Run có đúng 3.540 item và chỉ được tạo một lần.
- [ ] Run đã terminal; counts cộng lại bằng processed/total theo trạng thái hệ thống.
- [ ] Output nằm trong `hotel-price-intelligence/data/local_aux_backup/2026-09-29/`.
- [ ] Có full operational dump sau-run, core dump, hash và manifest vận hành.
- [ ] Full operational dump đã được chuyển về máy `local_aux` cũ trước khi máy đó chạy tiếp.
- [ ] Raw data không bị commit/push lên Git.

