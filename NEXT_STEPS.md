# NEXT STEPS — Hotel Price Intelligence

> Cập nhật: 14/08/2026.
> Trạng thái: pilot 6 ngày đã pass hard gate; đủ điều kiện chuyển sang batch 50 khách sạn.

## 1. Những remediation đã hoàn tất

| # | Hướng xử lý | Kết quả |
|---|---|---|
| 1 | Reference room tự động | Candidate/reference được sinh tự động, không tạo tay theo từng khách sạn |
| 2 | Scope reference đúng chuỗi giá | Đã đổi sang `(hotel_id, checkin_date)`; không dùng một phòng chung cho mọi ngày lưu trú |
| 3 | Điều kiện approve | ≥3 run hoàn tất, item coverage ≥80%, unique-per-item |
| 4 | Tách raw completeness khỏi reference | Parse/lưu đủ vẫn là `success`; `unavailable/ambiguous` là KPI reference riêng |
| 5 | Recompute không mất raw data | Migration và recompute giữ nguyên 2.312 observations |
| 6 | Property `not_bookable` | Tách khỏi sold-out/error; circuit-break sibling nhưng vẫn giữ URL riêng theo từng ngày |
| 7 | Quality gate | Sáu ngày pilot đã pass; cadence/version khác nhau chỉ là cảnh báo provenance |
| 8 | Mở rộng theo nấc | Đã đóng pilot 10; bước tiếp theo là 50, sau đó mới tới 272 |

## 2. Trạng thái dữ liệu đã kiểm chứng

- Run `#1–#6`: 10 khách sạn × 5 check-in × 6 ngày crawl = 300 item.
- 250 success, 20 sold-out, 30 `not_bookable`, 0 partial, 0 technical error.
- 2.312 `price_observations`; không duplicate `(crawl_run_item_id, room_option_index)`.
- 0 CAPTCHA/block; candidate = parsed = saved ở mọi item có giá.
- 42 chuỗi `(hotel_id, checkin_date)` có inventory: 38 reference `approved`, 4 `proposed`;
  readiness = 90,48% và mọi approved reference đều hợp lệ.
- 0 observation nối nhầm reference của ngày check-in khác.
- 8 URL sibling `not_bookable` lịch sử từng bị ghi đè ngày đã được repair; hiện mọi URL khớp ngày
  của chính item.
- Cảnh báo không chặn scale: run #2/#4/#5 lệch 22:00 ±30 phút; pilot dùng scraper 2.1.0 và 2.2.0.

Lệnh audit pilot đã pass:

```powershell
cd hotel-price-intelligence/backend
python scripts/audit_sampling_quality.py --run-ids 1 2 3 4 5 6
```

## 3. Protocol v2

`sampling_protocol_v2.xlsx` vẫn có 5 khu vực × 2 khách sạn, 10/10 link hợp lệ, không trùng và
không có sheet ngoài scope. So với v1 chỉ có một thay đổi:

- bỏ `sen` (`not_bookable`);
- thêm `hai-muoi-amp-apartment` — Hai Mươi Hotel & Apartment, Hà Nội.

Kiểm tra Booking trực tiếp với check-in 20/08/2026, checkout 21/08/2026 xác nhận đúng property,
có bảng phòng/rate đang đặt được và không có banner `not_bookable`. Chính Selenium/parser backend
cũng đọc đủ 5 candidate → 5 parsed room; regression này không tạo run hoặc ghi thêm observation.

Protocol v2 là regression cohort 10 khách sạn; không phải file batch 50. Khi tạo file 50, phải bảo
đảm Sen không còn trong cohort và giữ Hai Mươi hoặc một property Hà Nội active tương đương.

## 4. Các bước chuyển lên 50 khách sạn

### Bước 1 — Khoá workbook batch 50

- Chọn đúng 50 khách sạn, phân bố trên đủ 5 khu vực; nên bắt đầu khoảng 10 khách sạn/khu vực.
- Chạy preflight trong UI: phải đạt 50/50 link hợp lệ, không duplicate, không sheet ngoài scope.
- Đặt tên/version riêng và lưu SHA của file. Sau khi bắt đầu time series, không thay link âm thầm;
  thay property phải tạo protocol version mới và ghi lý do.
- Tạo backup MySQL trước batch đầu; không commit dump vào Git.

### Bước 2 — Chạy batch đầu

- Dùng cùng 5 check-in đã khoá: 20/08, 29/08, 02/09, 09/09, 26/09/2026; checkout +1 ngày.
- Ngữ cảnh cố định: 2 người lớn, 0 trẻ em, 1 phòng, 1 đêm, VND, anonymous.
- Không bật artifact đại trà. Chỉ bật cho regression nhỏ hoặc khi cần bằng chứng lỗi parser/selector.
- Một run sẽ có 50 × 5 = 250 item. Không chạy song song nhiều worker thật trên cùng IP.

### Bước 3 — Audit ngay sau batch đầu

Kiểm tra:

- item đã processed đủ 250;
- `candidate_rate_count = parsed_options_count = saved_options_count` cho item success;
- 0 duplicate, CAPTCHA/block và persistence mismatch;
- phân loại riêng `sold_out`, `not_bookable`, `dead_link`, parser error;
- missingness của occupancy/cancellation/tax flags không tăng bất thường theo khách sạn/khu vực;
- tên, slug, final URL và ngày check-in của một mẫu ở cả 5 khu vực khớp Booking.

Nếu có parser error, retry riêng item lỗi và bật artifact; không chạy lại toàn bộ batch chỉ để che lỗi.

### Bước 4 — Tích luỹ ít nhất ba ngày crawl cho cohort 50

- Mỗi ngày lịch thực dùng đúng workbook và 5 check-in trên.
- Hai run đầu reference mới có thể ở `calibrating/proposed`; đây là trạng thái bình thường.
- Sau run thứ ba, chạy quality gate trên đúng các run batch 50. Không trộn run pilot v1 vì SHA/cohort
  khác nhau.
- Chỉ mở rộng 272 khi batch 50 không có lỗi hệ thống và reference readiness/quality report hợp lý.

## 5. Việc làm song song trong lúc thu thập batch 50

1. Viết pipeline EDA/data-quality tái lập được: price distribution, missingness, sold-out,
   not_bookable, anomaly, option drift và reference coverage theo hotel/check-in.
2. Viết feature ngày lễ theo `city + checkin_date`, rà các holiday `provisional`.
3. Tích hợp weather theo city/date và lưu nguồn/thời điểm cập nhật.
4. Tạo competitive set theo city, review score và review count.
5. Thiết kế feature pipeline không leakage: lag/rolling theo `observed_at` trong từng
   `(hotel_id, checkin_date)`; scaler chỉ fit trên train.
6. Chuẩn bị baseline naïve/seasonal và time-based split; chưa train bốn model chính thức.

## 6. Lệnh vận hành reference

Migration `20260814_reference_per_checkin.sql` đã được áp dụng. Script sau chỉ dùng khi thật sự cần
rebuild toàn bộ metadata reference; nó giữ raw observations nhưng retire definition hiện hành để
giữ audit trail:

```powershell
cd hotel-price-intelligence/backend
python scripts/recompute_references.py --apply
```

Bình thường không chạy script sau mỗi batch: worker tự refresh candidate/reference khi run hoàn tất.

## 7. Chưa làm ở giai đoạn này

- Không scheduler/cron/Windows Task Scheduler; người dùng chủ động tạo run.
- Không lưu artifact mặc định cho mọi item.
- Không dùng `not_bookable` như sold-out hoặc giá 0.
- Không approve candidate thủ công hàng loạt và không dùng candidate chưa approved để train.
- Không nhảy thẳng từ 10 lên 272 khách sạn.
- Không train model chính thức chỉ từ vài run; cần tối thiểu 8–12 tuần dữ liệu liên tục và quality
  report ổn định.
- **Không đưa dữ liệu pilot 10 hoặc batch 50 vào dataset train/val/test.** Hai giai đoạn này chỉ để
  kiểm tra pipeline (parser, reference, error taxonomy) trước khi khoá cohort cuối cùng. Ngưỡng
  "8–12 tuần dữ liệu liên tục" ở trên tính **từ ngày cohort cuối cùng được khoá**, không tính từ
  lúc pilot bắt đầu — xem quyết định 2026-08-14 trong ROADMAP.md Phase 2 và CLAUDE.md mục 6.3 (bẫy
  #7). Lý do: train/val/test bắt buộc chia theo thời gian, nên nếu gộp cả pilot vào, đoạn train sẽ
  toàn khách sạn cũ còn đoạn test toàn khách sạn mới — hai tập không còn cùng một quần thể.
