# E2 — paired-context 1 vs 2 người lớn (2026-09-24)

Mục tiêu: xem dòng "Chỉ dành cho 1 khách" (và các dòng ở hotel mẫu gọn) thay đổi thế nào khi ngữ cảnh tìm kiếm là **1** người lớn thay vì 2 — chỉ ở **trang kết quả công khai**, không đi vào phễu đặt phòng.

## Cách chạy (không sửa mã đã commit)
`paired_context_patch/sitecustomize.py` cập nhật tại chỗ dict `app.scraper.url_utils._SCRAPE_QUERY` (`group_adults`, `req_adults`, `room1`) khi biến môi trường `MINICRAWL_ADULTS=1` được đặt. Python tự import `sitecustomize` ở mọi process có thư mục này trên `PYTHONPATH`, kể cả worker con của supervisor (thừa hưởng `os.environ`), nên cả API (tạo item, `durable.py`) lẫn worker (mở trang, `booking_scraper.py:321`) dựng URL 1 người lớn. Không đặt biến ⇒ không có tác dụng (đã thử).

Cô lập hoàn toàn: DB riêng `hotel_price_intel_minicrawl_20260924_adult1` (cùng cấu trúc DB vận hành), thư mục `run/adult1/{uploaded_files,crawl_artifacts}` (đã gitignore), API cổng 8011.

```bash
cd hotel-price-intelligence/backend
export PYTHONIOENCODING=utf-8 DB_NAME=hotel_price_intel_minicrawl_20260924_adult1 \
  UPLOAD_DIR=D:/MSE/CAPSTONE/outputs/mini-crawl-discriminator-20260924/run/adult1/uploaded_files \
  ARTIFACT_DIR=D:/MSE/CAPSTONE/outputs/mini-crawl-discriminator-20260924/run/adult1/crawl_artifacts \
  MINICRAWL_ADULTS=1 MINICRAWL_BACKEND=D:/MSE/CAPSTONE/hotel-price-intelligence/backend \
  PYTHONPATH=D:/MSE/CAPSTONE/outputs/mini-crawl-discriminator-20260924/analysis/paired_context_patch
./venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8011      # API
./venv/Scripts/python.exe scripts/run_worker.py                                   # worker (terminal khác, cùng biến môi trường)
curl -X POST http://127.0.0.1:8011/api/scraper/upload -F "file=@mini_crawl_discriminator_20260924.xlsx" \
  -F "checkin_dates=2026-10-01,2026-10-11,2026-11-18" -F "save_artifacts=true" -F "trigger_type=manual"
```

## Lưu ý khi đọc dữ liệu
- `crawl_runs.crawl_context` của DB `_adult1` ghi `adults: 2` vì hằng số cứng ở `app/scraper/data_contract.py:16`; ngữ cảnh thật là 1 người lớn (kiểm bằng `requested_hotel_link` chứa `group_adults=1&req_adults=1&room1=A`). **Không đưa DB này vào warehouse/dataset.**
- Bên 2 người lớn dùng run #2 của DB mini (E1, ngay trước E2). Pha trộn thời gian ~vài chục phút giữa hai bên; giá có thể đổi tự nhiên — phân tích `analyze_e2_paired_context.py` đối chiếu theo `data-block-id`.
