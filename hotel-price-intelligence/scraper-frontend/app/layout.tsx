import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Hotel Scraper — Internal",
  description: "Công cụ nội bộ cào giá khách sạn Booking.com",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="vi"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <header className="border-b border-border bg-surface">
          <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-4 sm:px-6">
            <Link href="/" className="min-w-0 text-sm font-semibold tracking-tight">
              🏨 Hotel Scraper <span className="font-normal text-muted">/ nội bộ</span>
            </Link>
            <nav className="flex shrink-0 gap-3 text-sm sm:gap-5">
              <Link href="/" className="text-muted transition hover:text-foreground">
                Cào mới
              </Link>
              <Link href="/jobs" className="text-muted transition hover:text-foreground">
                Lịch sử job
              </Link>
            </nav>
          </div>
        </header>
        <div className="flex-1">{children}</div>
      </body>
    </html>
  );
}
