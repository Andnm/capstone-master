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
| Lần 2 | dự kiến 25/09 sau lượt cào chính (~17:30), tốt nhất khoảng 20:00–20:30 để cùng khung giờ; sẽ là run #2 (hoặc #3 nếu có canary) trong cùng DB |

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

## Quy tắc an toàn
- **Không tắt worker vận hành của người dùng** (chuỗi python/chromedriver do PowerShell khởi động từ `start_project.ps1`). Kiểm bằng `Get-CimInstance Win32_Process` trước khi dừng bất kỳ tiến trình nào.
- Quét ~2 giờ (354 mục × ~20 giây) và ~0,5 GB; **xong trước 00:30**, giờ người dùng bấm cào hằng ngày. Không chạy song song với lượt cào chính.
- Backend/worker là tiến trình con của phiên Claude: đóng ứng dụng thì dừng. Hàng đợi bền trong DB: khởi động lại đúng các lệnh trên (không upload lại) thì worker tiếp tục các mục còn `queued`.
- Trước/sau: `scripts/ops_baseline.py hotel_price_intel_fullscan_20260924` — các dòng `OPS` phải giống hệt (DB vận hành không đổi).

## Phân tích
- `analysis/analyze_fullscan.py <RUN_ID>` — cấp cohort (trạng thái trang BẬT/TẮT/gọn, nhóm trùng và nguyên nhân, N1 kể cả hotel in cả hai kiểu, khoảng chênh theo hotel, đối chiếu mini-crawl); ghi `analysis/fullscan_hotel_summary_run<ID>.csv`.
- Thư viện tách dòng/ghép DOM↔DB dùng chung: `outputs/mini-crawl-discriminator-20260924/analysis/minicrawl_common.py`.

## Kết quả lần quét 1 (run #1, 24/09 20:23:57 → 21:51, 88 phút) và điều phát hiện
- 354 mục: 324 success, 24 sold_out, 4 not_bookable, 2 error (`dead_link`: Happy Nest 140 TDH, Hưng Yên 301TDH140 — hai hotel đã biết); 0 lỗi/cảnh báo khác; 416 MB artifact; DB vận hành **không đổi** (đối chiếu `run/ops_baseline_before.txt` / `after.txt`).
- **Cả 324 hotel ở trạng thái TẮT suốt 88 phút** (0 dòng "Chỉ dành cho 1 khách", 0 cột số người, kể cả 10 hotel hay có dòng này). Lý do: trạng thái BẬT/TẮT là thuộc tính của **cả một phiên Chrome** (worker dùng một phiên tối đa 500 mục, lần quét này chỉ có 1 phiên) — `analysis/analyze_session_state.py`: 20/20 phiên vận hành có ≥2 hotel dòng-1-khách cùng trạng thái; phiên TẮT 6/155 (3,9%) trước đây, nhưng tối 24/09 có 3/5 phiên TẮT.
- Vì vậy lần quét 1 là **đường cơ sở "sạch"** (chỉ hai khách): nhóm trùng 121/2.453 nhóm (4,9%) = 262/2.594 option (10,1%), 19/324 hotel; nguyên nhân: tiện ích/gói 62%, bữa ăn 28%, thanh toán+gói 6%. Câu phủ định bữa sáng (N1): 25 option ở 5/324 hotel (1,5%), 4 hotel chỉ phủ định, 1 hotel in cả hai kiểu. Nhưng nó **không đo được tỉ lệ hotel có dòng 1 khách** (cần trạng thái BẬT).

## Lần quét 2 — đề xuất "canary phiên" để chắc chắn ở trạng thái BẬT (chờ người dùng đồng ý)
Mục tiêu: có một lần quét BẬT ghép với lần quét 1 TẮT (cùng 354 hotel, cùng ngày check-in) ⇒ mọi dòng có ở BẬT mà không có ở TẮT (theo `data-block-id`) là dòng 1 khách, kể cả ở hotel mẫu gọn không có cảnh báo.
1. Khởi động API + worker như trên (phiên Chrome mới ở mục đầu).
2. Upload một run 1 mục (Mercure, 2026-10-11; workbook `outputs/mini-crawl-discriminator-20260924/analysis/probe_workbook_3hotels.xlsx` lọc còn Mercure) và xem HTML: có dòng cảnh báo "Chỉ dành cho 1 khách" ⇒ phiên BẬT.
3. BẬT ⇒ upload NGAY run toàn cohort (cùng worker/phiên; 354 mục < 500 nên không khởi động lại Chrome). TẮT ⇒ dừng worker (đóng Chrome), khởi động lại (phiên mới), lặp lại canary; tối đa 8 lần. Ghi từng lần thử vào `run/canary_log.md` (giờ, BẬT/TẮT): đó cũng là ước lượng tần suất TẮT hiện tại.
Không có gì thay đổi ở collector hay dữ liệu vận hành; ngữ cảnh vẫn 2 người lớn.
