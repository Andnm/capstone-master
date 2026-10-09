# RateScope — dashboard-frontend

Dashboard Next.js cho **Hotel Room Rate Monitoring and Short-Term Forecasting in Vietnam**.
Toàn bộ khách sạn, giá, tình trạng phòng, lịch sử, dự báo và confidence tier đều **mock hư cấu**.
Không nối backend/mô hình, không crawl, không gọi Booking.com/API thật, không giao dịch.

## Chạy

```powershell
cd D:\MSE\CAPSTONE\hotel-price-intelligence\dashboard-frontend
npm install --cache .npm-cache --no-audit --no-fund
npm run dev
```

Mở `http://localhost:3100`; `/` chuyển sang `/hotelier`. `/consumer` là góc nhìn phụ.
Production: `npm run build`, sau đó `npm run start` (cũng cổng **3100**).
Lint: `npm run lint`. Không dùng cổng 3000 hoặc sửa app scraper.
Server kiểm thử được dừng sau bàn giao; chạy `npm run dev` để mở lại.

## Cấu trúc và hợp đồng

- `app/`: root layout, metadata, header/footer dùng chung, các page App Router, design tokens/CSS.
- `components/`: hai dashboard, navigation, thẻ dự báo và biểu đồ Recharts.
- `types.ts`: hợp đồng API tương lai; VND dạng số, phần trăm dạng điểm phần trăm (2.7 nghĩa là +2,7%).
- `lib/mock-data.ts`: **toàn bộ fixture**, ngày cố định `MOCK_TODAY = 2026-11-18`.
- `lib/api.ts`: **nơi duy nhất page/component lấy dữ liệu**. Các hàm async trả Promise, độ trễ giả, không fetch.
  Khi có API thật, thay implementation module này, giữ `types.ts` và signatures. Không import fixture vào component.
- `lib/format.ts`: định dạng vi-VN, ngày, phân loại hướng và quy tắc gợi ý thuần.

Đã đọc docs cục bộ Next.js16.2.12 về App Router/project structure, layout/page,
server/client component và metadata **sau install, trước code ứng dụng**.
Phiên bản stack theo scraper-frontend; chỉ thêm một thư viện biểu đồ là Recharts.
Giữ các cấu hình cùng convention; `.gitignore`/ESLint thêm ignore `.npm-cache`/`.tmp` để
cache, nhật ký, scratch kiểm chứng nằm trong chính dashboard-frontend. Không dùng font mạng.

## Các quyết định và giả định mock

- **Người dùng đã xác nhận:** giữ check-in Hotelier mặc định28/11/2026, mở phạm vi **14ngày tới** (19/11–02/12), thay yêu cầu7ngày mâu thuẫn với mặc định.
- Hotelier28/11: của bạn1.120.000VND, trung bình8đối thủ950.764VND ⇒ chênh17,8% (làm tròn1số thập phân), hạng8/9, 2/8hếtphòng.
  Trung bình không gồm bạn, có giá quan sát gần nhất của khách sạn đãhếtphòng; UI ghi rõ.
- Market h1/h3/h7/h14 =+0,8/+1,5/+4,6/+6,4%; các ngày khác điều chỉnh giá bằng hệ số cố định weekday/weekend, không ngẫu nhiên.
- ConsumerA: Sương Mai+28/11;12snapshot từ07–18/11, span11ngày, approved, medium,
  từ1.050.000→1.120.000; h1+0,8%, h3+2,7%, h7+5,1%, **h14+6,4% được giả định** vì đề bài không chốt con số.
- ConsumerB: mọi lựa chọn khác trong1–30ngày tới (19/11–18/12), cold_start/proposed/low,
  1snapshot/span0ngày; h1+0,5%,h3−0,4%,h7+1,2%,h14+2,3% là giả định cố định.
- Phòng Superior/2người/1đêm, bữa sáng/hủy/thanh toán là điều kiện hư cấu.
- Hướng ổn định khi |%|≤2. Gợi ý xét mốc hỗ trợ≤7ngày: tăng≥2 ưu tiên “Nên đặt sớm”,
  nếu không có tăng nhưng giảm≤−2 thì “Có thể chờ”, còn lại ổn định; nếu có cả tăng/giảm,
  ưu tiên tăng theo thứ tự quy tắc trong yêu cầu. Cold_start luôn thêm “đánh giá sơ bộ”.
- Biểu đồ Consumer dùng ngày quan sát/dự báo, **không** ngày check-in; horizon kể từ18/11.
  Forecast là minh họa hợp đồng, không chứng minh tính khả dụng sau ngày lưu trú.
- Không dải tin cậy. Tier không phải xác suất và không chứng minh model vượt persistence.

Header mọi trang có “Dữ liệu mẫu - chưa nối mô hình thật”. Footer nhắc đây không phải trang đặt phòng
và không cam kết tiết kiệm. Nút kiểm tra giá chỉ mô phỏng5bước (~700ms/bước) và lấy fixture.
Ngày ngoài phạm vi bị từ chối cảUI vàmoduleapi, không tạo dự báo.

## Kiểm chứng

Đã kiểm ngày 10/10/2026 trên máy local, chỉ gọi loopback cổng 3100:

- `npm install --cache .npm-cache --no-audit --no-fund`: exit **0**, không xung đột peer dependency.
- `npm run lint`: exit **0**.
- `npm run build`: exit **0**, Next.js 16.2.12, các route được prerender thành công.
- `node .tmp/check-contracts.cjs`: exit **0**, **619 assertions**, 90 lựa chọn Consumer
  (3 khách sạn × 30 ngày), 14 ngày Hotelier. Dùng Node/TypeScript đã có, không thêm framework test.
- HTTP: `/` **307** với Location `/hotelier`; `/hotelier` và `/consumer` **200**.
- Trình duyệt Codex: đã kiểm trực quan hai trang ở **375px và 1440px**.
  Document scroll width lần lượt 360/1425px (trừ thanh cuộn), không tràn ngang.
  Đã kiểm bảng thành 9 thẻ mobile, sắp xếp giá/điểm, mô phỏng 5 bước, cả hai chế độ,
  cảnh báo ngoài khoảng và giới hạn lịch. Console warn/error được thu thập: **0**,
  không thấy hydration warning. Ảnh bằng chứng ở `.tmp/screenshots/`.

Chưa kiểm: API/mô hình thật (không có trong phạm vi), production runtime bằng `npm run start`,
Safari/Firefox/trình duyệt mobile thật, screen reader và kiểm accessibility chuyên dụng.
Các kiểm tra này không chứng minh độ chính xác dự báo; toàn bộ số liệu là fixture.

## Danh sách file tạo/cập nhật

File ứng dụng/cấu hình được tạo trong chính thư mục này:

```text
AGENTS.md, CLAUDE.md, .gitignore
package.json, package-lock.json, tsconfig.json
eslint.config.mjs, postcss.config.mjs, next.config.ts, types.ts
app/layout.tsx, app/page.tsx, app/globals.css, app/icon.svg
app/hotelier/page.tsx, app/consumer/page.tsx
components/header.tsx, components/forecast-cards.tsx, components/charts.tsx
components/hotelier-dashboard.tsx, components/consumer-dashboard.tsx
lib/api.ts, lib/mock-data.ts, lib/format.ts
```

`README.md` được cập nhật; Next tự sinh `next-env.d.ts`.
Dependency/build/cache (`node_modules`, `.next`, `.npm-cache`) và scratch kiểm chứng
(`.tmp/check-contracts.cjs`, `.tmp/contract-results.json`, `.tmp/screenshots`) cũng ở đây.
Không sửa backend/ML/crawler, không tạo `.env`, không chạy git hoặc publish/deploy.
