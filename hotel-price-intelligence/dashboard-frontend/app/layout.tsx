import type { Metadata } from "next";
import { Header } from "@/components/header";
import { SnapshotProvider } from "@/components/snapshot-provider";
import { L } from "@/lib/labels";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "RateScope · Giá phòng khách sạn", template: "%s · RateScope" },
  description: "Bảng theo dõi giá niêm yết và ước lượng thăm dò ngắn hạn từ mô hình thử nghiệm. Không phải trang đặt phòng.",
  robots: { index: false, follow: false },
};

/** Layout KHÔNG gọi API (không chặn render, build không cần backend): dữ liệu được nạp ở trình duyệt qua SnapshotProvider. */
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body>
        <SnapshotProvider>
          <a href="#main-content" className="skip-link">{L.skipLink}</a>
          <Header />
          <main id="main-content" className="page-container">{children}</main>
          <footer className="site-footer">
            <div className="footer-inner">
              <span className="footer-brand">{L.brand} <span>{L.footerBrandNote}</span></span>
              <p>{L.footerText}</p>
            </div>
          </footer>
        </SnapshotProvider>
      </body>
    </html>
  );
}
