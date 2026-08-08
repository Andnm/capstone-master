# NEXT STEPS — Hotel Price Intelligence

> Cập nhật: 08/08/2026
> Điểm xuất phát: durable worker và luồng cào thủ công đã chạy E2E thành công; run #4 có 4/4 item
> thành công, 51/51 dòng DOM được lưu vào DB và 0 mismatch trong 473 field đã đối chiếu.

## 1. Cách đọc `Phát hiện → Parse → DB`

Ba số trên trang chi tiết job là ba checkpoint của cùng một hotel/check-in:

1. **Phát hiện (candidate):** số rate-option crawler nhìn thấy trong DOM của bảng phòng Booking.
2. **Parse:** số option parser đọc và chuyển thành record hợp lệ.
3. **DB:** số record được lưu thành công vào `price_observations`.

Ví dụ `18 → 18 → 18` nghĩa là crawler phát hiện 18 option, parser đọc đủ 18 và DB lưu đủ 18.
Đây là kết quả toàn vẹn.

| Hiển thị | Ý nghĩa | Mức độ |
|---|---|---|
| `18 → 18 → 18` | Không mất dòng qua pipeline | Tốt |
| `18 → 17 → 17` | Một candidate bị parser loại; xem `rejected_options` và artifact | Cần kiểm tra |
| `18 → 18 → 17` | Parser có 18 nhưng DB chỉ lưu 17 | Lỗi nghiêm trọng |
| `18 → 16 → 15` | Vừa có dòng bị parser loại, vừa có dòng không lưu được | Lỗi nghiêm trọng |

`Candidate` ở cột này là **candidate rate-option trong DOM**, không phải
`hotel_room_candidates` dùng để hiệu chỉnh reference room. Nên đổi nhãn UI thành
**`Phát hiện → Parse → DB`** để tránh nhầm hai khái niệm.

---

## 2. Việc cần làm ngay trước pilot

### Task 1 — Khoá sampling protocol

- [ ] Chốt 5 thành phố: Hồ Chí Minh, Hà Nội, Vũng Tàu, Đà Lạt, Phú Quốc.
- [ ] Giữ cố định 2 người lớn, 0 trẻ em, 1 phòng, 1 đêm và VND.
- [ ] Chốt các mốc lead-time/check-in sẽ dùng xuyên suốt dự án.
- [ ] Chốt lịch người dùng chủ động bấm chạy; không dùng scheduler/cron.
- [ ] Ghi version protocol vào mỗi đợt thu thập nếu sau này phải thay đổi.

**Definition of done:** có một bảng ngày/mốc lead-time rõ ràng để hai lần chạy không chọn ngày tuỳ hứng.

### Task 2 — Kiểm thử backup và restore MySQL

- [ ] Tạo một bản `mysqldump` thủ công.
- [ ] Restore thử vào database test khác, không ghi đè database đang dùng.
- [ ] Đối chiếu số dòng ở `crawl_runs`, `crawl_run_items`, `hotels`, `price_observations`,
      `hotel_room_candidates` và `hotel_reference_rooms`.
- [ ] Viết lại lệnh backup/restore và nơi lưu file trong tài liệu vận hành.

**Definition of done:** restore thành công và số dòng/khóa ngoại khớp database nguồn.

### Task 3 — Chỉnh nhãn UX của parser completeness

- [ ] Đổi `Candidate → DB` thành `Phát hiện → Parse → DB`.
- [ ] Thêm tooltip giải thích ba checkpoint.
- [ ] Hiển thị `rejected_options_count` rõ ràng khi lớn hơn 0.
- [ ] Giữ cảnh báo `partial` nếu chênh lệch chưa được giải thích.

**Definition of done:** người không đọc code vẫn hiểu được ba con số trên trang job.

---

## 3. Pilot 1–2 tuần

### Task 4 — Chuẩn bị tập pilot

- [ ] Chọn khoảng 10–20 khách sạn, có đại diện cả 5 thành phố.
- [ ] Kiểm tra link trùng, link chết, sai thành phố và redirect về search results.
- [ ] Không cần cố cân bằng tuyệt đối, nhưng không để thành phố nào không có mẫu.
- [ ] Lưu file Excel pilot riêng, không sửa trực tiếp file regression nhỏ.

### Task 5 — Chạy đủ dữ liệu hiệu chỉnh reference

- [ ] Mỗi khách sạn có ít nhất 3 successful run khác nhau.
- [ ] Chỉ bật lưu artifact cho lần baseline, mẫu kiểm tra ngẫu nhiên hoặc item có lỗi.
- [ ] Theo dõi candidate reference chuyển từ `proposed/calibrating` sang `approved`.
- [ ] Kiểm tra coverage ≥80% và candidate chỉ xuất hiện tối đa một lần trong mỗi item.
- [ ] Không đưa reference chưa `approved` vào dataset ML.

### Task 6 — Báo cáo chất lượng sau mỗi batch

Theo dõi tối thiểu:

- [ ] Tỉ lệ `success`, `partial`, `sold_out`, `error`.
- [ ] Tổng candidate/parsed/rejected/saved và mọi chênh lệch.
- [ ] Tỉ lệ thiếu của giá, tên phòng, occupancy, breakfast, cancellation, tax inclusion.
- [ ] Số observation trùng `crawl_run_item_id + room_option_index`.
- [ ] Tỉ lệ hotel có reference `approved` và reference coverage.
- [ ] Lỗi theo taxonomy: navigation, redirect, bot challenge, parser, validation, persistence.
- [ ] Parser/selector version và git commit của batch.

**Gate để mở rộng:** success ≥95%, không có chênh lệch Parse→DB chưa giải thích, không có duplicate
key và các lỗi parser quan trọng đã có artifact/test regression.

### Task 7 — Tạo regression corpus từ artifact

- [ ] Giữ một số HTML.gz/ảnh đại diện cho mỗi kiểu bảng phòng quan trọng.
- [ ] Thêm test cho rate thường, partner rate, sold-out, tax included, tax tách riêng và redirect.
- [ ] Khi Booking đổi DOM, tái hiện lỗi bằng artifact trước khi sửa selector.
- [ ] Sau khi sửa parser, chạy lại unit test và một E2E nhỏ trước batch tiếp theo.

---

## 4. Mở rộng thu thập dữ liệu

### Task 8 — Hoàn thiện danh sách 200–300 khách sạn

- [ ] Phân bổ theo 5 thành phố và các mức review score/review count.
- [ ] Chuẩn hoá tên sheet/city đúng bộ tên canonical.
- [ ] Loại link trùng theo Booking hotel slug.
- [ ] Preflight toàn bộ file trước khi chạy thật.
- [ ] Chia thành batch vừa phải để dễ retry/audit, không gom tất cả vào một run quá lớn.

### Task 9 — Vận hành thu thập thủ công

- [ ] Chạy theo sampling protocol đã khoá.
- [ ] Sau mỗi run, kiểm tra worker health, item lỗi/partial và parser completeness.
- [ ] Retry chỉ các item lỗi sau khi đã hiểu nguyên nhân.
- [ ] Export Excel khi cần audit; dữ liệu train lấy từ DB, không lấy từ file export.
- [ ] Chạy cleanup thủ công khi cần; upload giữ 90 ngày, artifact giữ 30 ngày.

> Không triển khai lịch tự động hàng tuần/tháng. Worker chỉ xử lý job do người dùng tạo từ UI/API.

---

## 5. Chuẩn bị dữ liệu cho ML

### Task 10 — Chốt dữ liệu lịch và ngoại cảnh

- [ ] Rà lại các dòng `vn_holidays.status = provisional` trước khi dùng.
- [ ] Viết feature ngày lễ/sự kiện theo city và check-in date.
- [ ] Tích hợp weather theo city/date và lưu rõ nguồn/thời điểm cập nhật.
- [ ] Định nghĩa competitive set theo city, review score và review count.

### Task 11 — EDA và data-quality pipeline

- [ ] Phân phối giá và missingness theo city/hotel/reference/rate plan.
- [ ] Kiểm tra anomaly, sold-out, partner-rate drift và độ liên tục theo thời gian.
- [ ] Chỉ lấy observation khớp reference đã `approved` cho chuỗi giá chính.
- [ ] Xuất báo cáo chất lượng tái lập được bằng script, không chỉnh tay trong Excel.

### Task 12 — Feature engineering và labeling

- [ ] Calendar/holiday/weather/lead-time features.
- [ ] Lag và rolling chỉ dịch `observed_at`, giữ nguyên `checkin_date`.
- [ ] Competitive-set features cùng check-in date và thời điểm quan sát.
- [ ] Label horizon 1/3/7/14 ngày và cờ `has_label`.
- [ ] Chia train/validation/test theo thời gian, có gap theo horizon lớn nhất.

**Gate bắt đầu model chính thức:** tối thiểu 8–12 tuần dữ liệu đủ liên tục, phần lớn khách sạn mục
tiêu có reference approved, quality report đạt và không có data leakage.

---

## 6. Modeling và sản phẩm sau khi dữ liệu đạt gate

- [ ] Baseline naïve/seasonal trước khi train model phức tạp.
- [ ] Random Forest, XGBoost, LSTM và Transformer cho các horizon đã chốt.
- [ ] Optuna tuning chỉ trên train/validation; test được giữ kín tới cuối.
- [ ] Đánh giá MAPE/RMSE/R²/direction accuracy và lớp price-drop.
- [ ] SHAP, ablation study và phân tích sai số theo city/lead-time.
- [ ] Sau khi model đạt, mới làm serving API, dashboard và booking/pricing simulation.

---

## 7. Những việc chủ động không làm lúc này

- Không scheduler/cron/Windows Task Scheduler cho crawler.
- Không lưu artifact mặc định cho mọi run.
- Không tạo reference room/rate plan bằng tay cho từng khách sạn.
- Không dùng candidate reference chưa approved để train.
- Không bắt đầu model chính thức chỉ từ vài run regression.
