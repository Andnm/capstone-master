import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  output: "standalone",
  // Nginx buffers proxied responses by default. Với response stream (loading.tsx +
  // Suspense) đủ lớn, buffering làm script "swap" nội dung thật vào chỗ fallback không
  // chạy đúng — trang kẹt mãi ở skeleton dù server đã trả đủ data. Header này báo Nginx
  // tắt buffering cho riêng response này (xem node_modules/next/dist/docs/.../streaming.md
  // mục "Reverse proxies").
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [{ key: "X-Accel-Buffering", value: "no" }],
      },
    ];
  },
};

export default nextConfig;
