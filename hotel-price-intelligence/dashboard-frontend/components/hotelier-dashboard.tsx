"use client";
import { useRef, useState } from "react";
import { ArrowDownWideNarrow, ArrowUpWideNarrow, CalendarDays, CircleAlert, Info, MapPin, Wallet, Users, ChartNoAxesColumnIncreasing, ListOrdered, LoaderCircle, Star } from "lucide-react";
import type { CompsetHotel, DashboardConfig, HotelierResponse } from "@/types";
import { getHotelierDashboard } from "@/lib/api";
import { fullDate, pct, shortDate, vnd } from "@/lib/format";
import { CheckInChart } from "./charts";
import { ConfidenceBadge, ForecastCards, ForecastCaution } from "./forecast-cards";

type Sort = { key: "price" | "score"; ascending: boolean };
export function HotelierDashboard({ config, initialData }: { config: DashboardConfig; initialData: HotelierResponse }) {
  const [data, setData] = useState(initialData);
  const [checkIn, setCheckIn] = useState(config.defaultCheckIn);
  const [sort, setSort] = useState<Sort>({ key: "price", ascending: true });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const latest = useRef(0);
  const ordered = [...data.compset].sort((a, b) => {
    const delta = sort.key === "price" ? a.currentPrice - b.currentPrice : a.reviewScore - b.reviewScore;
    return (sort.ascending ? delta : -delta) || a.name.localeCompare(b.name, "vi");
  });
  function changeSort(key: Sort["key"]) {
    setSort((old) => ({ key, ascending: old.key === key ? !old.ascending : key === "price" }));
  }
  async function changeDate(value: string) {
    const request = ++latest.current;
    setCheckIn(value); setError(""); setPending(true);
    try { const response = await getHotelierDashboard(value); if (request === latest.current) setData(response); }
    catch (caught) { if (request === latest.current) setError(caught instanceof Error ? caught.message : "Không tải được dữ liệu mẫu."); }
    finally { if (request === latest.current) setPending(false); }
  }
  const sortIcon = sort.ascending ? <ArrowDownWideNarrow size={14} aria-hidden="true" /> : <ArrowUpWideNarrow size={14} aria-hidden="true" />;
  return <div className="dashboard">
    <section className="page-heading">
      <div><div className="eyebrow"><span className="small-line" />GÓC NHÌN CHỦ KHÁCH SẠN</div><h1>Hiểu thị trường.<br className="mobile-break" /> Theo dõi vị thế giá.</h1><p>{data.myHotel.name} <span className="heading-separator">/</span> <MapPin size={14} aria-hidden="true" /> {data.myHotel.city} <span className="heading-separator">/</span> {data.groupSize - 1} đối thủ hư cấu</p></div>
      <label className="date-field"><span><CalendarDays size={15} aria-hidden="true" />Ngày check-in</span><input type="date" aria-describedby="hotelier-date-range" value={checkIn} min={config.hotelierMinDate} max={config.hotelierMaxDate} onChange={(e) => void changeDate(e.target.value)} /><small id="hotelier-date-range">14 ngày tới · {shortDate(config.hotelierMinDate)}–{shortDate(config.hotelierMaxDate)}</small></label>
    </section>
    <div className="context-line"><span>{fullDate(data.checkIn)}</span><span className="context-status" role="status" aria-live="polite">{pending ? <><LoaderCircle size={13} className="spin" aria-hidden="true" />Đang tải dữ liệu mẫu…</> : <>Quan sát mẫu: {shortDate(config.mockToday)} · 09:00</>}</span></div>
    {error && <p role="alert" className="error-message">{error} Số liệu bên dưới vẫn thuộc ngày {shortDate(data.checkIn)}.</p>}
    <div aria-busy={pending}>
      <section className="kpi-grid" aria-label="Chỉ số vị thế giá">
        <Kpi icon={<Wallet size={18} />} label="Giá của bạn" value={vnd(data.myHotel.currentPrice)} note="Giá niêm yết / phòng / đêm" primary />
        <Kpi icon={<Users size={18} />} label="Trung bình nhóm đối thủ" value={vnd(data.competitorAverage)} note="8 khách sạn · không gồm bạn" />
        <Kpi icon={<ChartNoAxesColumnIncreasing size={18} />} label="Chênh lệch với nhóm" value={pct(data.myHotel.pctVsGroup)} note={data.myHotel.pctVsGroup > 0 ? "Giá của bạn cao hơn trung bình" : data.myHotel.pctVsGroup < 0 ? "Giá của bạn thấp hơn trung bình" : "Giá của bạn bằng trung bình"} />
        <Kpi icon={<ListOrdered size={18} />} label="Thứ hạng giá" value={`${data.priceRank}/${data.groupSize}`} note="Xếp từ giá thấp đến cao" />
      </section>
      <div className="market-grid">
        <section className="panel chart-panel"><div className="section-title"><div><span className="section-kicker">MẶT BẰNG GIÁ</span><h2>Giá niêm yết hiện tại theo ngày check-in</h2><p>14 ngày tới · VND / phòng / đêm</p></div><span className="badge neutral-badge">Giá đã quan sát · mẫu</span></div><CheckInChart points={data.checkInPrices} /><p className="chart-note">So sánh ngày lưu trú, không phải dự báo. Điều kiện phòng/giá có thể khác nhau.</p></section>
        <section className="panel forecast-panel"><div className="section-title"><div><span className="section-kicker">XU HƯỚNG MINH HỌA</span><h2>Dự báo mặt bằng giá nhóm đối thủ</h2><p>Cho check-in {shortDate(data.checkIn)}/2026</p></div></div><div className="forecast-meta"><span>Confidence tier</span><ConfidenceBadge forecast={data.marketForecast} /></div><ForecastCards forecast={data.marketForecast} /><ForecastCaution warnings={data.marketForecast.warnings} /></section>
      </div>
      <div className="detail-grid">
        <section className="panel competitors-panel"><div className="section-title"><div><span className="section-kicker">NHÓM SO SÁNH</span><h2>Vị thế trong nhóm đối thủ <span className="count-badge">9</span></h2><p>Cùng thành phố · Check-in {shortDate(data.checkIn)}/2026</p></div></div>
          <div className="mobile-sort"><span>Sắp xếp:</span><button onClick={() => changeSort("price")} aria-pressed={sort.key === "price"}>Giá {sort.key === "price" && sortIcon}</button><button onClick={() => changeSort("score")} aria-pressed={sort.key === "score"}>Điểm {sort.key === "score" && sortIcon}</button><span className="sr-only">{sort.ascending ? "Tăng dần" : "Giảm dần"}</span></div>
          <table className="competitor-table"><caption className="sr-only">9 khách sạn hư cấu; trung bình nhóm chỉ gồm 8 đối thủ. Giá hết phòng là giá quan sát gần nhất.</caption><thead><tr><th scope="col">Khách sạn</th><th scope="col" aria-sort={sort.key === "score" ? sort.ascending ? "ascending" : "descending" : "none"}><button onClick={() => changeSort("score")}>Điểm {sort.key === "score" && sortIcon}</button></th><th scope="col" aria-sort={sort.key === "price" ? sort.ascending ? "ascending" : "descending" : "none"}><button onClick={() => changeSort("price")}>Giá hôm nay {sort.key === "price" && sortIcon}</button></th><th scope="col">So với nhóm</th><th scope="col">7 ngày</th><th scope="col">Tình trạng</th></tr></thead><tbody>{ordered.map((hotel) => <tr key={hotel.id} className={hotel.isMyHotel ? "my-hotel-row" : ""}><th scope="row"><HotelName hotel={hotel} /></th><td><span className="score-pill">{hotel.reviewScore.toFixed(1).replace(".", ",")}</span></td><td className="table-price">{vnd(hotel.currentPrice)}{hotel.soldOut && <small>Giá quan sát gần nhất</small>}</td><td><span className={hotel.pctVsGroup > 0 ? "positive-gap" : "negative-gap"}>{pct(hotel.pctVsGroup)}</span></td><td><span className="table-change">{hotel.change7DaysPct > 0 ? "↗" : "↘"} {pct(hotel.change7DaysPct)}</span></td><td><Availability soldOut={hotel.soldOut} /></td></tr>)}</tbody></table>
          <div className="competitor-cards">{ordered.map((hotel) => <article key={hotel.id} className={`competitor-card ${hotel.isMyHotel ? "my-hotel-row" : ""}`}><div className="competitor-card-top"><HotelName hotel={hotel} /><span className="score-pill"><Star size={12} aria-hidden="true" />{hotel.reviewScore.toFixed(1).replace(".", ",")}</span></div><div className="competitor-card-price"><strong>{vnd(hotel.currentPrice)}</strong><Availability soldOut={hotel.soldOut} /></div><div className="competitor-card-stats"><span>So với nhóm <strong>{pct(hotel.pctVsGroup)}</strong></span><span>7 ngày <strong>{hotel.change7DaysPct > 0 ? "↗" : "↘"} {pct(hotel.change7DaysPct)}</strong></span></div>{hotel.soldOut && <small className="muted">Giá quan sát gần nhất; hiện đã hết phòng.</small>}</article>)}</div>
          <p className="table-note"><Info size={14} aria-hidden="true" />Trung bình gồm 8 giá quan sát, kể cả giá gần nhất của đối thủ đã hết phòng. So sánh không điều chỉnh chênh lệch chất lượng.</p>
        </section>
        <aside className="panel alerts-panel"><div className="section-title"><div><span className="section-kicker">ĐÁNG CHÚ Ý</span><h2>Tín hiệu thị trường <span className="count-badge">3</span></h2><p>Diễn giải từ dữ liệu mẫu</p></div></div><ul className="alert-list">{data.alerts.map((alert) => <li key={alert.id}><span className={`alert-icon ${alert.severity}`}>{alert.severity === "attention" ? <CircleAlert size={18} aria-hidden="true" /> : <Info size={18} aria-hidden="true" />}</span><div><span className={`severity-label ${alert.severity}`}>{alert.severity === "attention" ? "Lưu ý" : "Thông tin"}</span><h3>{alert.title}</h3><p>{alert.description}</p></div></li>)}</ul><div className="aside-note">Các tín hiệu hỗ trợ quan sát, không đề xuất mức giá bán hay cam kết doanh thu.</div></aside>
      </div>
    </div>
  </div>;
}

function Kpi({ icon, label, value, note, primary = false }: { icon: React.ReactNode; label: string; value: string; note: string; primary?: boolean }) {
  return <article className={`kpi-card ${primary ? "kpi-primary" : ""}`}><div className="kpi-heading"><span>{label}</span><span className="kpi-icon" aria-hidden="true">{icon}</span></div><strong>{value}</strong><p>{note}</p></article>;
}
function HotelName({ hotel }: { hotel: CompsetHotel }) { return <span className="hotel-name">{hotel.name}{hotel.isMyHotel && <span className="your-hotel">Của bạn</span>}</span>; }
function Availability({ soldOut }: { soldOut: boolean }) { return <span className={`availability ${soldOut ? "sold-out" : "available"}`}><span aria-hidden="true">{soldOut ? "−" : "✓"}</span>{soldOut ? "Hết phòng" : "Còn phòng"}</span>; }
