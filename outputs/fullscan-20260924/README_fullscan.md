# Quét toàn cohort (2026-09-24 →) — quy trình và cách chạy lại

**Mục đích:** lấy số liệu TOÀN COHORT (354 hotel) cho cuộc thảo luận canonical key ở `discuss/canonical-key-duplicates/` — tỉ lệ hotel có dòng "Chỉ dành cho 1 khách", hotel mẫu gọn, câu phủ định bữa sáng (lỗi parser N1), nguyên nhân nhóm trùng, độ ổn định giữa hai lần quét. Không phải dữ liệu huấn luyện: DB cô lập, không vào warehouse/dataset.

**Ngữ cảnh tìm kiếm là mặc định của dự án: 2 người lớn · 0 trẻ em · 1 phòng · 1 đêm · VND** (`url_utils.py:6-15`); không có bản vá nào. Người dùng duyệt chạy hai lần: 24/09 và 25/09.

| | Giá trị |
|---|---|
| Danh sách | `link_hotel_data_expanded.xlsx` (354 hotel; SHA-256 `4a0aa5a39ad722bec361bae453c4da90596077f4c7336ed5e2f8691c04cb9fe4`) — chỉ đọc, API sao chép vào `run/uploaded_files` |
| Ngày check-in | **2026-10-11** cho cả hai lần (để so sánh cặp hotel×ngày; cũng trùng ngày trong mini-crawl) |
| Lưu artifact | có (`save_artifacts=true`, HTML.gz + ảnh) |
| DB cô lập | `hotel_price_intel_fullscan_20260924` (tạo bằng `scripts/init_db.py`, cấu trúc = DB vận hành) |
| Thư mục | `run/uploaded_files`, `run/crawl_artifacts` (đã gitignore) |
| Cổng API | 8012 |
| Lần 1 | run #1, bắt đầu 20:23:57 (VN) 24/09 |
| Lần 2 | **đã chạy:** canary = run #2 (25/09 21:10:37, BẬT ngay), cohort = **run #3, 25/09 21:11:25→22:35:16** trong cùng DB (xem mục "Lần quét 2" bên dưới) |

## Chạy (Git Bash, cwd `hotel-price-intelligence/backend`)
```bash
export PYTHONIOENCODING=utf-8 DB_NAME=hotel_price_intel_fullscan_20260924 \
  UPLOAD_DIR=D:/MSE/CAPSTONE/outputs/fullscan-20260924/run/uploaded_files \
  ARTIFACT_DIR=D:/MSE/CAPSTONE/outputs/fullscan-20260924/run/crawl_artifacts
./venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8012        # API
./venv/Scripts/python.exe scripts/run_worker.py                                    # worker (terminal khác, cùng biến môi trường)
curl -X POST http://127.0.0.1:8012/api/scraper/upload -F "file=@D:/MSE/CAPSTONE/link_hotel_data_expanded.xlsx" \
  -F "checkin_dates=2026-10-11" -F "save_artifacts=true" -F "trigger_type=manual"
python ../../outputs/fullscan-20260924/scripts/watch.py <RUN_ID> 180               # theo dõi
python ../../outputs/fullscan-20260924/scripts/status.py <RUN_ID>                  # tiến độ + ETA
```
Lần đầu: `python ../../outputs/fullscan-20260924/scripts/init_db.py hotel_price_intel_fullscan_20260924` (từ chối nếu DB đã có).

**Cách chạy ngắn gọn từ lần quét 2 (25/09)** — hai script PowerShell dựng/dừng đúng stack cô lập (API 8012 + worker), lưu PID vào `run/stack_pids.json`, dừng bằng `taskkill /T` sau khi đối chiếu thời điểm khởi động của PID (không thể đụng worker vận hành của người dùng):
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File outputs\fullscan-20260924\scripts\start_stack.ps1 -Tag a1     # worker MỚI = phiên Chrome mới
powershell -NoProfile -ExecutionPolicy Bypass -File outputs\fullscan-20260924\scripts\stop_stack.ps1             # dừng + kiểm cổng 8012, chromedriver
```
Canary: `python scripts/make_canary_workbook.py` (→ `run/canary_mercure.xlsx`, đúng dòng Mercure của workbook cohort), upload như trên với `checkin_dates=2026-10-11`, rồi `python scripts/canary_check.py <RUN_CANARY> <lần thử>` (thoát mã 0 = BẬT, 10 = TẮT, 20 = không rõ; ghi `run/canary_log.md`). BẬT ⇒ upload cohort NGAY (worker giữ Chrome khi rảnh, `run_forever` không đóng driver, nên cùng phiên). `python scripts/hot_state.py <RUN>` in trạng thái 10 hotel hay có dòng 1 khách theo từng mục (đọc HTML thật).

## Quy tắc an toàn
- **Không tắt worker vận hành của người dùng** (chuỗi python/chromedriver do PowerShell khởi động từ `start_project.ps1`). Kiểm bằng `Get-CimInstance Win32_Process` trước khi dừng bất kỳ tiến trình nào.
- Quét ~85 phút (354 mục × ~14 giây; ước ban đầu ~2 giờ) và ~0,4 GB mỗi lần; **xong trước 00:30**, giờ người dùng bấm cào hằng ngày. Không chạy song song với lượt cào chính.
- Backend/worker là tiến trình con của phiên Claude: đóng ứng dụng thì dừng. Hàng đợi bền trong DB: khởi động lại đúng các lệnh trên (không upload lại) thì worker tiếp tục các mục còn `queued`.
- Trước/sau: `scripts/ops_baseline.py hotel_price_intel_fullscan_20260924` — các dòng `OPS` phải giống hệt (DB vận hành không đổi).

## Phân tích
- `analysis/analyze_fullscan.py <RUN_ID>` — cấp cohort (trạng thái trang BẬT/TẮT/gọn, nhóm trùng và nguyên nhân, N1 kể cả hotel in cả hai kiểu, khoảng chênh theo hotel, đối chiếu mini-crawl); ghi `analysis/fullscan_hotel_summary_run<ID>.csv`.
- Thư viện tách dòng/ghép DOM↔DB dùng chung: `outputs/mini-crawl-discriminator-20260924/analysis/minicrawl_common.py`.

## Kết quả lần quét 1 (run #1, 24/09 20:23:57 → 21:49:02 theo `crawl_runs`, 85 phút) và điều phát hiện
- 354 mục: 324 success, 24 sold_out, 4 not_bookable, 2 error (`dead_link`: Happy Nest 140 TDH, Hưng Yên 301TDH140 — hai hotel đã biết); 0 lỗi/cảnh báo khác; 416 MB artifact; DB vận hành **không đổi** (đối chiếu `run/ops_baseline_before.txt` / `after.txt`).
- **Cả 324 hotel ở trạng thái TẮT suốt 85 phút** (0 dòng "Chỉ dành cho 1 khách", 0 cột số người, **và 0 dòng `bbasic`**, kể cả 10 hotel hay có dòng 1 khách). Lần quét này chỉ có 1 phiên Chrome. **Đính chính 25/09:** kết luận cũ ở đây ("BẬT/TẮT là thuộc tính của cả một phiên Chrome", 20/20 phiên nhất quán, phiên TẮT 6/155) quá tay — dữ liệu vận hành #52 có một ca đổi giữa phiên, độ dài phiên đã đổi theo thời gian (10 → ~90 → ~500 mục) và "TẮT" gồm ít nhất hai mức (có/không dòng `bbasic`); xem `discuss/canonical-key-duplicates/08-claude-consolidated-evidence-report.md` §4–§5 và `analysis/analyze_state_persistence.py`. Trước đó: tối 24/09 có 3/5 phiên TẮT.
- Vì vậy lần quét 1 là **đường cơ sở "sạch"** (chỉ hai khách): nhóm trùng 121/2.453 nhóm (4,9%) = 262/2.594 option (10,1%), 19/324 hotel; nguyên nhân: tiện ích/gói 62%, bữa ăn 28%, thanh toán+gói 6%. Câu phủ định bữa sáng (N1): 25 option ở 5/324 hotel (1,5%), 4 hotel chỉ phủ định, 1 hotel in cả hai kiểu. Nhưng nó **không đo được tỉ lệ hotel có dòng 1 khách** (cần trạng thái BẬT).

## Lần quét 2 (25/09) — ĐÃ CHẠY, canary + cohort trong cùng một phiên Chrome
Mục tiêu: có một lần quét BẬT ghép với lần quét 1 TẮT (cùng 354 hotel, cùng ngày check-in 2026-10-11) ⇒ mọi dòng có ở BẬT mà không có ở TẮT (theo `data-block-id`) là dòng phụ. Người dùng duyệt 25/09: "thử trước 1 trang theo như bạn muốn rồi sau đó chạy cohort ngày thứ 2 đi".
1. `start_stack.ps1 -Tag a1` (21:10; phiên Chrome mới ở mục đầu).
2. Canary = run #2, 1 mục (Mercure, 2026-10-11, workbook `run/canary_mercure.xlsx`), `canary_check.py 2 1`: **54 dòng, 27 dòng "1 khách", 54 ô số người ⇒ BẬT ngay lần thử đầu** (`driver_start_ms`=6.213; `run/canary_log.md`). Không cần khởi động lại.
3. Upload NGAY run toàn cohort trong cùng worker/phiên (workbook cùng SHA `4a0aa5a3…`): **run #3, 25/09 21:11:25 → 22:35:16, 83,8 phút**; 321 success, 23 sold_out, 8 not_bookable, 2 error (dead_link đã biết); 354 mục đều `driver_start_ms`=0 (không khởi động lại Chrome); 3.182 option. DB vận hành không đổi (`run/ops_baseline_before_run2.txt` = `..._after_run2.txt`); worker của người dùng không bị đụng; `stop_stack.ps1` dừng stack (cổng 8012 đóng, không còn chromedriver).
4. Phân tích: `bash analysis/run_scan2_analysis.sh 3` (analyze_fullscan, compare_scans 1 3, analyze_gap_transfer) và `analysis/analyze_row_families.py 1 3`; đầu ra lưu cạnh script (`fullscan_run3_output.txt`, `compare_scans_1_3_output.txt`, `gap_transfer_run3_output.txt`, `row_families_output.txt`).

**Kết quả chính** (chi tiết và diễn giải ở `discuss/canonical-key-duplicates/08-claude-consolidated-evidence-report.md`):
- BẬT suốt lần quét: 9/9 hotel hay có dòng 1 khách có thông tin (21:17→22:33) đều có dòng này (`scripts/hot_state.py 3`). 72/321 hotel (22,4%) có dòng 1 khách (338 dòng, 10,6% option); 249/321 (77,6%) mẫu gọn.
- **Họ dòng phụ thứ hai `bbasic_*`:** 0/2.594 option ở quét 1; 259/3.182 (8,1%) ở 109/321 hotel ở quét 2 (259/259 "• Thanh toán trước"; giá median 0,815× dòng thường cùng phòng). Hai họ không bật/tắt cùng nhau (mini run2: có bbasic, không dòng 1 khách).
- Nhóm trùng canonical key: quét 2 443/2.655 (16,7%) = 970/3.182 option (30,5%) ở 106/321 hotel; **bỏ cả hai họ ⇒ 120 nhóm (4,9%), 260 option (10,1%), 18 hotel = đường cơ sở quét 1 (121, 262, 19)**. 66,4% nhóm trùng chứa dòng 1 khách, 15,1% chứa bbasic, 18,7% không chứa họ nào.
- Trang TẮT là tập con của trang BẬT (99,1% block-id); dòng dôi ra 608 = 334 dòng 1 khách + 258 bbasic + 16 còn lại.
- N1: cùng 5 hotel (32 option ở quét 2). T3 (khoảng chênh cố định, học mini run1 → thử quét 2): 111/117 (94,9%; 6 hotel tốt 110/110; Pearl/Starview/Vinhomes hỏng); gate 05 không đạt.
- Phát hiện phụ: 5 hotel sold_out (quét 1) → not_bookable (quét 2) — HTML quét 2 có `non-bookable-container`.
- **Ý tưởng (tôi từng cân nhắc, chưa đề xuất) "collector kiểm canary đầu phiên rồi khởi động lại Chrome cho tới khi TẮT" đã BỎ** (chỉ ~6% lượt hotel TẮT, "TẮT" có nhiều mức, và nó lọc quần thể theo biến thể trang): xem 08-claude-consolidated-evidence-report.md §1 và §5.

## E6 (25→26/09) — 20 cặp phiên Chrome đồng thời: trạng thái trang là của PHIÊN hay của THỜI ĐIỂM? (ĐÃ CHẠY)
Người dùng duyệt: "ok, chạy 20 cặp phiên Chrome đi". Bộ chạy `scripts/e6_pairs.py` (mỗi phiên = một tiến trình Python riêng + Chrome headless MỚI = hồ sơ tạm/cookie mới, dùng đúng `get_driver` và `scrape_booking_hotel`; không DB, không hàng đợi; hai phiên trong cặp khởi động cách nhau ~1 giây; dừng ngay nếu CAPTCHA/chặn; chỉ dừng tiến trình do chính nó tạo, theo PID).
```bash
cd hotel-price-intelligence/backend && export PYTHONIOENCODING=utf-8
./venv/Scripts/python.exe ../../outputs/fullscan-20260924/scripts/e6_pairs.py --pairs 20 --start 1 --deadline 00:20   # ~45 giây/cặp; không mở cặp mới nếu còn <80 giây tới hạn (giờ máy)
./venv/Scripts/python.exe ../../outputs/fullscan-20260924/analysis/analyze_e6_pairs.py                                # -> analysis/e6_output.txt
```
Đầu ra: `run/e6/pairNN_{A,B}.json`, `e6_log.jsonl`; HTML/ảnh ở `run/crawl_artifacts/<1000+cặp>/<mục>/` (gitignore). Trang mặc định: Mercure Vũng Tàu (dò dòng 1 khách) rồi Roma (dò dòng `bbasic`), check-in 2026-10-11, 2 người lớn mặc định. **Không chạy khi lượt cào chính hằng ngày đang chạy** (00:30 → ~16:00) trừ khi người dùng đồng ý.
**Kết quả (23:49→00:04, 20/20 cặp ok, 0 CAPTCHA/chặn):** dòng 1 khách: 30 phiên có/10 không, 10/20 cặp lệch, 0 cặp cùng không; `bbasic`: 24 có/16 không, 10/20 cặp lệch; kỳ vọng độc lập 7,5 và 9,6 (thời điểm/hệ thống: 0) ⇒ **biến thể trang liên quan đến phiên (không phải trạng thái toàn cục theo thời điểm)**; chưa thấy liên hệ giữa hai họ (Fisher p=0,263, n=40; không chứng minh độc lập); thời điểm gán chưa xác định; bốn biến thể của 40 phiên: có 1 khách+bbasic 40%, có 1 khách 35%, chỉ bbasic 20%, không họ nào 5%; TẮT 25% (Wilson 14–40%) so với 6,0% lượt hotel vận hành lịch sử. Chi tiết: `discuss/canonical-key-duplicates/08-claude-consolidated-evidence-report.md`, `analysis/e6_output.txt`.
Mốc phiên: tham số `chal_t` trong URL cuối (`crawl_run_items.hotel_link`) = lần tải đầu của một phiên Booking mới (E6 40/40; vận hành khớp `driver_start_ms>0`; `analyze_state_persistence.py [RUN_MIN] [driver|chal|either]`).

