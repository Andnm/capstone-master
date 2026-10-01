# Mini-crawl xác minh discriminator — 2026-09-24

**Mục đích:** xem trang Booking thật của các khách sạn có "nhóm trùng canonical key" để biết các option cùng tên/điều kiện khác nhau ở đâu
(định danh dòng, nhãn giá, điều kiện thanh toán, số khách…). Bằng chứng mức trang này phục vụ cuộc tranh luận A/B/C giữa Claude và GPT ở mốc
warehouse ba nguồn (~01/10). **Dữ liệu mini-crawl không đi vào warehouse/dataset** — lưu ở DB riêng.

## File

`mini_crawl_discriminator_20260924.xlsx` — 5 sheet thành phố (đúng định dạng scraper: cột A = tên, cột B = link; **cột C trở đi chỉ là ghi chú, scraper bỏ qua**).
18 khách sạn = **13 DUP** (có nhóm trùng bền trong warehouse) + **5 CONTROL** (mỗi thành phố 1, listing phong phú nhưng 0 nhóm trùng, để đối chiếu).
Đã kiểm bằng chính hàm preflight của backend: 18 link hợp lệ, không trùng/lỗi, cả 5 sheet trong scope.

| Vai trò | Khách sạn (`hotel_id`) |
|---|---|
| DUP – HCM | `hilton-saigon`, `the-myst-dong-khoi` |
| DUP – Hà Nội | `vinhomes-ocean-park-bunnys-homes-nature-room`, `dusit-le-palais-tu-hoa-hanoi`, `intercontinental-westlake` |
| DUP – Vũng Tàu | `the-imperial-vung-tau`, `mercure-vung-tau-vietnam`, `ibis-styles-vung-tau` |
| DUP – Đà Lạt | `starview-villa`, `pearl-wealth-da-lat` |
| DUP – Phú Quốc | `movenpick-residences-phu-quoc`, `b-amp-k-homestay-glocery-store`, `sol-phu-quoc` |
| CONTROL | `gardenplazasaigonparkroyal` (HCM), `22land-residence-2` (HN), `diamond-sea-vung-tau` (VT), `taladalat-thanh-pho-da-lat123` (ĐL), `roma` (PQ) |

## Ngày check-in nhập trên web

`2026-10-01,2026-10-11,2026-11-18` — gần / giữa / xa; đều là ngày anchor đã có lịch sử trong warehouse; đa số cặp DUP có nhóm trùng ở ≥ 70% lần cào (nhiều cặp 100%).
18 × 3 = **54 item**, ước 15–20 phút. **Tick "lưu screenshot/HTML.gz"** (bắt buộc — đó là bằng chứng chính).

## Cách chạy an toàn (nhánh + DB tách hẳn)

1. **Dùng `git worktree` thay vì đổi nhánh tại chỗ**, để không đụng file của tiến trình nhánh gốc và để `uploaded_files/` + `crawl_artifacts/` (đường dẫn tương đối theo `backend/`) tự tách riêng:
   ```bash
   git worktree add ../CAPSTONE-minicrawl -b mini-crawl-discriminator
   ```
2. **⚠ `.env` không nằm trong git** → worktree mới KHÔNG có `backend/.env`. Khi thiếu, backend rơi về mặc định `DB_NAME=hotel_price_intel` — **trùng tên DB crawl hằng ngày**.
   Phải tạo `backend/.env` trong worktree trỏ sang DB mới (tên khác hẳn), rồi tạo DB đó từ `backend/app/database/setup.sql`. (`venv/` và `node_modules/` cũng không có trong worktree: dùng lại venv của
   nhánh gốc bằng đường dẫn tuyệt đối và `npm install` cho frontend.) *Cách khác nếu không định sửa code:* giữ nguyên thư mục hiện tại và đặt biến môi trường `DB_NAME`, `UPLOAD_DIR`, `ARTIFACT_DIR`
   (khác hẳn giá trị gốc) trong đúng shell khởi chạy backend + worker của mini-crawl — biến môi trường được ưu tiên hơn `.env`.
3. **Kiểm tra trước khi upload** (chạy trong `backend/` của worktree, dùng venv của backend):
   ```bash
   python -c "from app.core.config import settings; print(settings.DB_NAME, settings.UPLOAD_DIR, settings.ARTIFACT_DIR)"
   ```
   Phải in ra tên DB mới, không phải `hotel_price_intel`.
4. Chạy backend + worker + scraper-frontend từ worktree; nếu tiến trình nhánh gốc còn chạy thì dùng **cổng khác** cho backend/frontend của worktree.
5. Upload workbook, nhập 3 ngày ở trên, tick artifact, bấm "Bắt đầu cào".
6. *(Tuỳ chọn)* chạy lại đúng workbook đó sau vài giờ để xem cấu trúc có ổn định theo thời gian không.

## Gửi lại cho Claude

- tên DB mới (hoặc file dump / Excel export của run);
- thư mục `crawl_artifacts/` của worktree;
- nếu chạy 2 lần: giờ bắt đầu của mỗi lần.

## Claude sẽ kiểm tra

1. Với mỗi nhóm trùng: các dòng option trên trang khác nhau ở điểm nào — `data-block-id` (phần định danh rate), nhãn/badge (Genius, ưu đãi, giá thành viên…), điều kiện thanh toán (trả trước/trả sau), số khách, "chỉ còn N phòng", chi tiết chính sách huỷ.
2. Số dòng trên trang so với `candidate / parsed / saved / duplicate` của item — loại trừ khả năng parser đếm nhầm.
3. Đối chiếu CONTROL: khách sạn không có nhóm trùng trông thế nào.
4. Cấu trúc có lặp lại giữa 3 ngày check-in và giữa hai lần cào (nếu có).
5. Kết luận (kèm phản biện) đưa vào cuộc tranh luận A/B/C với GPT.
