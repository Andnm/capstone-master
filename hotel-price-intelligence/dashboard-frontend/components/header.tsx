"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Building2, ChartNoAxesCombined, CircleAlert, Compass, FlaskConical, Info } from "lucide-react";
import { daysSince, longDate } from "@/lib/format";
import { L, stalenessBanner } from "@/lib/labels";
import { useSnapshot } from "./snapshot-provider";

export function Header() {
  const pathname = usePathname();
  const snapshot = useSnapshot();
  const link = (href: string, label: string, icon: React.ReactNode) => <Link href={href} aria-current={pathname === href ? "page" : undefined}>{icon}{label}</Link>;
  const asOf = snapshot.status === "ready" ? snapshot.data.snapshot.dataAsOf : null;
  return <header className="site-header">
    <div className="header-inner">
      <Link href="/hotelier" className="brand" aria-label="RateScope — trang chủ">
        <span className="brand-icon"><ChartNoAxesCombined size={23} aria-hidden="true" /></span>
        <span>{L.brand}<span className="brand-sub">{L.brandSub}</span></span>
      </Link>
      <nav aria-label="Chọn góc nhìn" className="role-nav">
        {link("/hotelier", L.navHotelier, <Building2 size={16} aria-hidden="true" />)}
        {link("/consumer", L.navConsumer, <Compass size={16} aria-hidden="true" />)}
        {link("/model", L.navModel, <Info size={16} aria-hidden="true" />)}
      </nav>
      <span className="header-date">Dữ liệu tới <strong>{asOf ? longDate(asOf) : "—"}</strong></span>
    </div>
    <div className={`sample-banner ${snapshot.status === "error" ? "banner-error" : ""}`} role="status">
      {snapshot.status === "error" ? <><CircleAlert size={14} aria-hidden="true" /><span>{snapshot.error.message}</span><button type="button" className="link-button" onClick={snapshot.reload}>{L.retry}</button></>
        : asOf ? <><FlaskConical size={14} aria-hidden="true" /><span>{stalenessBanner(asOf, daysSince(asOf))}</span></>
        : <span>{L.loading}</span>}
    </div>
  </header>;
}
