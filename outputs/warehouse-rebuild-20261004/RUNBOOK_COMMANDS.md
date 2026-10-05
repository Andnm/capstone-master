# Lệnh rebuild warehouse 3 nguồn (04/10/2026) — chạy SAU khi lượt cào 05/10 kết thúc (0 run/item queued|running)

> **ĐÃ HOÀN TẤT 06/10/2026 00:03** (`warehouse_20261004_3src`, GPT `PASS FINAL REBUILD`). Giữ file này làm mẫu cho lần rebuild sau; khác biệt so với lần này: (1) bước −1 tăng InnoDB là BẮT BUỘC và phải sửa cỡ/baseline trong `scripts/apply_innodb_temp.py`; (2) build và smoke chạy bằng `scripts/run_build_detached.ps1` + `scripts/build_watchdog.py` + `scripts/run_smokes_detached.ps1` / `run_official_checks_detached.ps1` (tách khỏi harness, không giới hạn thời gian); (3) HEAD, hash 3 manifest, tên DB/batch trong các lệnh dưới là của lần 04/10 — đổi theo dump mới.

Môi trường: **PowerShell**. Mỗi lệnh dưới đây là **một dòng hoàn chỉnh** (không có ký tự nối dòng) để copy/paste nguyên văn. Chạy từ thư mục backend:
```powershell
Set-Location D:\MSE\CAPSTONE\hotel-price-intelligence\backend
$env:PYTHONIOENCODING = 'utf-8'; $py = 'venv\Scripts\python.exe'; $o = '..\..\outputs\warehouse-rebuild-20261004'; $w = '..\data\warehouse'
```
Code ở commit `5f07c6146424821fb6685dbaae5baced566e7463` (`code_provenance()` strict phải sạch). **Mỗi giai đoạn chỉ chạy khi GPT đã ghi gate cho phép giai đoạn đó.** Không promote trước `PASS FOR PROMOTE`.

## −1. Tài nguyên MySQL (BẮT BUỘC trước mọi build lớn; lặp lại sau mỗi lần restart MySQL)
Lần 05/10 build 3 nguồn (3,15 triệu obs) kẹt vì MySQL mặc định `innodb_buffer_pool_size`=128 MB / `innodb_redo_log_capacity`=100 MB (xem thread file 21–22, CLAUDE.md §7.2). Trước preflight phải tăng **tạm thời** (`SET GLOBAL`, không persist, cần 0 run/item active và RAM trống):
```powershell
& $py "$o\scripts\apply_innodb_temp.py"
& $py "$o\scripts\apply_innodb_temp.py" --apply
```
(Dòng 1 là dry-run: in giá trị hiện tại và kiểm baseline vận hành; dòng 2 mới đổi.) Mặc định script đặt **8 GiB pool + 4 GiB redo** — mức của lần 05/10. **Với dữ liệu lớn hơn phải sửa hằng `TARGET_POOL`/`TARGET_REDO` trong script trước khi chạy:** pool ≥ data+index của warehouse sẽ build (~1,6 GiB/triệu obs; ~6,9 triệu obs cuối tháng 11 ≈ 11 GB ⇒ dự kiến 12 GiB, trần ~16 GiB trên máy 31,4 GB RAM), redo ≥ 4 GiB (8 GiB nếu >6 triệu obs). Script cũng đang so baseline đếm DB vận hành cố định của ngày 05/10 (`OPS_BASELINE`) — **phải cập nhật baseline theo snapshot preflight mới nhất** trước khi dùng cho lần sau, nếu không nó dừng với "!= baseline". Xác nhận bằng cách đọc lại `@@innodb_buffer_pool_size` và `@@innodb_redo_log_capacity`; ghi giá trị thật vào evidence của báo cáo build.
Build dài: **không đặt giới hạn thời gian**; chạy tách khỏi harness (`Start-Process`, log ra file), theo dõi bằng process còn sống + `SELECT 1` ngắn, không dùng `information_schema`/`SHOW PROCESSLIST` khi MySQL đang có dấu hiệu đứng.

## 0. Input-freeze + preflight (ngay trước mỗi giai đoạn)
Preflight verify `input_provenance.json.profiles.<profile>` với CHÍNH các đường dẫn truyền vào (cả hash lẫn path), ghi path+hash vào snapshot; `--compare` tính lại và FAIL nếu path/hash đổi trong stage. **Đường dẫn manifest ở preflight phải giống hệt `build_warehouse` ngay sau đó.**
Preflight FAIL nếu còn run/item queued|running, còn BẤT KỲ `wh_staging_%`/user `whr_%`, guard dirty, hoặc C: < 60 GB. Sau build, `--compare` đòi count/max_id ba bảng vận hành + latest run **bằng tuyệt đối**, HEAD không đổi, GUARDED_PATHS sạch.
```powershell
& $py "$o\scripts\make_input_manifests.py" --write
& $py "$o\scripts\preflight.py" --tag aux_rehearsal --profile aux --target-db warehouse_rh20261004_aux --batch-id b20261004_auxrh --source-manifest "$w\source_manifest_20261004_auxonly.json" --ownership-manifest "$w\ownership_manifest_20261004_auxonly.json" --cohort-manifest "$w\cohort_history_20260916.json"
& $py "$o\scripts\preflight.py" --tag full_rehearsal --profile full --target-db warehouse_rh20261004_3src --batch-id b20261004_3srcrh --source-manifest "$w\source_manifest_20261004.json" --ownership-manifest "$w\ownership_manifest_20261004.json" --cohort-manifest "$w\cohort_history_20260916.json" --cutoff-file "$o\cutoff_20261004.json"
```
(Dòng 2 cho giai đoạn 1; dòng 3 cho giai đoạn 2 và — đổi `--tag/--target-db/--batch-id` — giai đoạn 3 official.) Hash kỳ vọng: source `5c7207853a985a1331e7782ebd4fc0c098ae71626c8262a71368a29cba6c04fd`, cohort `63ad92b51d185435563c3667626967efa7ff5057d9acec7b6a84423b9eae4581`, ownership `ab31fcff2ae931dd84b241f4229ab28d173efb758a933161317e92d5fe88444c` (aux-only: source `19fc7a48…`, ownership `ead58dab…`), cutoff `e7f1f4d5c78c1aa7d563f7a4f62b8bc9b1f48f4069028a9895cbc6624368df5a`.

## 1. Aux-only rehearsal (chẩn đoán tương thích, không thay thế full rebuild)
```powershell
& $py scripts\init_warehouse_db.py --database warehouse_rh20261004_aux
& $py scripts\build_warehouse.py --database warehouse_rh20261004_aux --batch-id b20261004_auxrh --base-dir ..\.. --source-manifest "$w\source_manifest_20261004_auxonly.json" --cohort-manifest "$w\cohort_history_20260916.json" --ownership-manifest "$w\ownership_manifest_20261004_auxonly.json"
& $py scripts\validate_warehouse.py --database warehouse_rh20261004_aux --batch-id b20261004_auxrh
& $py "$o\scripts\preflight.py" --compare "$o\snapshots\<file>.json"
```
## 2. Full 3 nguồn rehearsal
Như trên với `warehouse_rh20261004_3src`, batch `b20261004_3srcrh`, manifest 3 nguồn (`source_manifest_20261004.json`, `ownership_manifest_20261004.json`).
## 3. Official
`warehouse_20261004_3src`, batch `b20261004_3src`, cùng manifest/code commit. Rồi:
```powershell
& $py scripts\validate_warehouse.py --database warehouse_20261004_3src --batch-id b20261004_3src
& $py "$o\scripts\compare_checksums.py" --a warehouse_rh20261004_3src:b20261004_3srcrh --b warehouse_20261004_3src:b20261004_3src
& $py "$o\scripts\gate_inputs.py" --database warehouse_20261004_3src --batch-id b20261004_3src --ownership-manifest "$w\ownership_manifest_20261004.json" --cutoff-file "$o\cutoff_20261004.json"
& $py "$o\scripts\compare_wave_a.py" --old warehouse_20260916_2src:b20260916_2src --old-sources local_primary,vps --new warehouse_20261004_3src:b20261004_3src --new-sources local_primary,vps,local_aux
```
**Chỉ sau GPT `PASS FOR PROMOTE`:**
```powershell
& $py scripts\promote_warehouse.py --database warehouse_20261004_3src --batch-id b20261004_3src
```
Static test của thư viện hỗ trợ: `& $py -m pytest "$o\tests" -q` (15 passed).
Dừng ngay (không promote) nếu: rejection ≠ 0; `protocol_deviation_by_hotel` ≠ []; timestamp audit lệch; checksum semantic/exact lệch; preflight FAIL, hoặc compare báo BẤT KỲ thay đổi ở DB vận hành (count/max_id/latest run), HEAD đổi, GUARDED_PATHS dirty, input path/hash đổi, còn staging/user tạm.
Sau xong: `DB wh_staging_% = []`, `user whr_% = []`, DB rehearsal giữ đến `PASS FINAL REBUILD` rồi mới xóa.
