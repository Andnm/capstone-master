import type { Metadata } from "next";
import { Header } from "@/components/header";
import { getDashboardConfig } from "@/lib/api";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "RateScope · Giá phòng Đà Lạt", template: "%s · RateScope" },
  description: "Dashboard mẫu giám sát giá niêm yết và minh họa dự báo ngắn hạn. Không phải trang đặt phòng.",
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const config = await getDashboardConfig();
  return (
    <html lang="vi">
      <body>
        <a href="#main-content" className="skip-link">Chuyển đến nội dung</a>
        <Header dataLabel={config.dataLabel} mockToday={config.mockToday} />
        <main id="main-content" className="page-container">{children}</main>
        <footer className="site-footer">
          <div className="footer-inner">
            <span className="footer-brand">RateScope <span>· Dữ liệu hư cấu</span></span>
            <p>Thông tin tham khảo dựa trên giá niêm yết Booking.com đã quan sát. Đây không phải trang đặt phòng và không cam kết tiết kiệm.</p>
          </div>
        </footer>
      </body>
    </html>
  );
}
