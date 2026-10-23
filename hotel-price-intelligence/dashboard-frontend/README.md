# RateScope — dashboard-frontend

Dashboard Next.js (App Router, Next 16.2.12, React 19) cho **Hotel Room Rate Monitoring and Short-Term Forecasting in Vietnam**.
Nối vào **dịch vụ dashboard thật** (`backend/dashboard_main.py`, cổng 8100) qua cùng-origin `/api/dashboard/*`. **Không còn dữ liệu mock.**

Mọi số liệu là **bản chụp hồi cứu** (hiện tới 04/10/2026) từ **mô hình thử nghiệm (dev)**: không phải giá hiện tại, không phải khuyến nghị đặt phòng/định giá,
không phải trang đặt phòng. Mức diễn đạt và giới hạn do `lib/labels.ts` + backend `labels.py` quy định (chính sách nhãn trong thread
`discuss/model-serving-dashboard-20261023/artifacts/design/v3/LABEL_POLICY.md`).

## Chạy

```powershell
# 1) dịch vụ dữ liệu (loopback 127.0.0.1:8100; không đụng API cào ở cổng 8000)
cd D:\MSE\CAPSTONE\hotel-price-intelligence\backend
C:\Users\ADMIN\anaconda3\python.exe -B dashboard_main.py            # snapshot ở ..\data\serving\v1 (CURRENT.json)

# 2) giao diện
cd D:\MSE\CAPSTONE\hotel-price-intelligence\dashboard-frontend
npm run dev                                                          # http://localhost:3100  ( / -> /hotelier )
```

`npm run build` **không cần backend** (trang tĩnh; dữ liệu nạp ở trình duyệt). Lint: `npm run lint`. Kiểm nhãn hiển thị: `npm run check:labels`.
Cổng frontend 3100 (không dùng 3000). Đích proxy là biến server `DASHBOARD_API_ORIGIN` (mặc định `http://127.0.0.1:8100`; chỉ chấp nhận loopback, không cổng 8000).

## Cấu trúc

- `types.ts`: hợp đồng API (đồng bộ pydantic backend; test `backend/tests/test_dashboard_contract_ts.py` so khóa hai chiều). % là **điểm phần trăm**; `null` = thiếu dữ liệu thật.
- `lib/api.ts`: **điểm truy cập dữ liệu duy nhất** (fetch thật + `ApiError`, timeout, hủy request). Không fallback giả khi lỗi.
- `lib/use-api.ts`: hook nạp dữ liệu có hủy/bỏ phản hồi cũ khi đổi lựa chọn.
- `lib/labels.ts`: mọi chuỗi hiển thị do frontend định nghĩa; `lib/format.ts`: định dạng (ngày VN theo cắt chuỗi, không lệch múi giờ).
- `next.config.ts`: rewrites allowlist đúng 6 endpoint (`health, config, hotels, forecast, hotelier, model-info`) tới dịch vụ dashboard.
- `components/`: `snapshot-provider`, `header` (banner dữ liệu cũ), `consumer-dashboard`, `hotelier-dashboard`, `model-info-view`, `forecast-cards`, `charts`, `state-views`.
- `app/`: `/hotelier`, `/consumer`, `/model` (bằng chứng và giới hạn của mô hình), `error.tsx`.
- `scripts/check-ui-labels.mjs`: quét chuỗi/JSX text theo danh sách cụm cấm (trùng backend) + negative control + dấu vết UI cũ.

## Phạm vi được hỗ trợ / chưa hỗ trợ

- Có: dự báo cấp khách sạn cho mốc 1 và 3 ngày (theo từng cặp khách sạn–ngày check-in, chỉ khi `lead ≥ mốc`), lịch sử giá của chính chuỗi, nhóm so sánh (cùng thành phố, điểm đánh giá lệch ≤ 0,5, tối đa 15, cần ≥ 5; trung vị),
  tín hiệu luật (lệch vị thế ≥ 15%; thay đổi nhóm > ±2%), trang "Về mô hình".
- Chưa: mốc 7/14 ngày (chưa có mô hình), cào/featurize ngày mới, dữ liệu hết phòng, đăng nhập ("khách sạn của bạn" chỉ là bộ chọn demo), mô hình phân loại (chờ `xgboost`; UI ghi rõ chưa khả dụng).

## Lưu ý Next.js

Phiên bản này có thay đổi phá vỡ so với kiến thức cũ (`AGENTS.md`): đọc `node_modules/next/dist/docs/` trước khi viết mã mới. Đã dùng: fetching data, error handling,
backend-for-frontend, rewrites, route segment config, `error.tsx` (`unstable_retry`).
