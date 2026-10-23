import type { NextConfig } from "next";

/**
 * Trình duyệt chỉ gọi cùng-origin `/api/dashboard/<endpoint>`; Next chuyển tiếp (allowlist đúng 6 endpoint, chỉ GET dữ liệu đọc) tới dịch vụ dashboard RIÊNG
 * (mặc định http://127.0.0.1:8100/api/dashboard/v1/...). KHÔNG có `NEXT_PUBLIC_*` cho URL backend, KHÔNG gọi qua server component, KHÔNG trỏ vào API cào (cổng 8000).
 * Đích là biến server `DASHBOARD_API_ORIGIN` (chỉ loopback, không cổng 8000), đọc lúc khởi động/build — không lộ ra bundle trình duyệt.
 */
const origin = process.env.DASHBOARD_API_ORIGIN ?? "http://127.0.0.1:8100";
const parsed = new URL(origin);
if (!["127.0.0.1", "localhost"].includes(parsed.hostname) || parsed.port === "8000" || parsed.pathname !== "/") {
  throw new Error(`DASHBOARD_API_ORIGIN không hợp lệ (chỉ loopback, không cổng 8000, không path): ${origin}`);
}
const endpoints = ["health", "config", "hotels", "forecast", "hotelier", "model\\-info"];

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  async rewrites() {
    return endpoints.map((endpoint) => ({ source: `/api/dashboard/${endpoint}`, destination: `${parsed.origin}/api/dashboard/v1/${endpoint.replace("\\", "")}` }));
  },
};
export default nextConfig;
