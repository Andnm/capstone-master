"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChartNoAxesCombined, FlaskConical, Building2, Compass } from "lucide-react";
import { shortDate } from "@/lib/format";

export function Header({ dataLabel, mockToday }: { dataLabel: string; mockToday: string }) {
  const pathname = usePathname();
  return <header className="site-header">
    <div className="header-inner">
      <Link href="/hotelier" className="brand" aria-label="RateScope — trang chủ">
        <span className="brand-icon"><ChartNoAxesCombined size={23} aria-hidden="true" /></span>
        <span>RateScope<span className="brand-sub">Hotel rate intelligence</span></span>
      </Link>
      <nav aria-label="Chọn góc nhìn" className="role-nav">
        <Link href="/hotelier" aria-current={pathname === "/hotelier" ? "page" : undefined}><Building2 size={16} aria-hidden="true" />Chủ khách sạn</Link>
        <Link href="/consumer" aria-current={pathname === "/consumer" ? "page" : undefined}><Compass size={16} aria-hidden="true" />Khách đặt phòng</Link>
      </nav>
      <span className="header-date">Ngày mẫu <strong>{shortDate(mockToday)}/2026</strong></span>
    </div>
    <div className="sample-banner"><FlaskConical size={14} aria-hidden="true" /><span>{dataLabel}</span><span className="sample-detail">Không gọi Booking.com hoặc API thật</span></div>
  </header>;
}
