"use client";
import { useState } from "react";
import Link from "next/link";
import { ArrowDownWideNarrow, ArrowUpWideNarrow, CalendarDays, ChartNoAxesColumnIncreasing, CircleAlert, Info, ListOrdered, MapPin, Users, Wallet } from "lucide-react";
import { getHotelier, getHotels } from "@/lib/api";
import { fullDate, gapText, longDate, pct, reviewText, shortDate, vnd } from "@/lib/format";
import { alertsPanel } from "@/lib/hotelier-view";
import { horizonLabel, L } from "@/lib/labels";
import { useApi } from "@/lib/use-api";
import type { CompsetHotel, HotelierResponse, HotelsResponse, HotelWithCheckIns } from "@/types";
import { CheckInChart } from "./charts";
import { ForecastCards, ModeBadge, WarningList } from "./forecast-cards";
import { useSnapshot } from "./snapshot-provider";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "./state-views";

type Sort = { key: "price" | "score"; ascending: boolean };
const dash = L.unavailable;
const pctOrDash = (value: number | null) => (value === null ? dash : pct(value));

export function HotelierDashboard() {
  const snapshot = useSnapshot();
  const hotelsState = useApi<HotelsResponse>("hotels", (signal) => getHotels(signal));
  const [city, setCity] = useState("");
  const [pickedHotel, setPickedHotel] = useState("");
  const [pickedDate, setPickedDate] = useState("");
  const [sort, setSort] = useState<Sort>({ key: "price", ascending: true });

  const hotels: HotelWithCheckIns[] = hotelsState.status === "ready" ? hotelsState.data.hotels : [];
  const cities = [...new Set(hotels.map((h) => h.city))].sort((a, b) => a.localeCompare(b, "vi"));
  const filtered = hotels.filter((h) => !city || h.city === city).sort((a, b) => a.name.localeCompare(b.name, "vi"));
  const hotel = filtered.find((h) => h.id === pickedHotel) ?? filtered[0];
  const dates = hotel?.checkIns.map((c) => c.date) ?? [];
  const checkIn = dates.includes(pickedDate) ? pickedDate : (dates[0] ?? "");
  const data = useApi<HotelierResponse>(hotel && checkIn ? `hotelier|${hotel.id}|${checkIn}` : null, (signal) => getHotelier({ hotelId: hotel!.id, checkIn }, signal));

  return <div className="dashboard">
    <section className="page-heading">
      <div><div className="eyebrow"><span className="small-line" />GÓC NHÌN CHỦ KHÁCH SẠN</div><h1>{L.hotelierHeading}<br className="mobile-break" /> {L.hotelierHeading2}</h1>
        <p>{snapshot.status === "ready" ? snapshot.data.snapshot.label : snapshot.status === "loading" ? L.loading : L.unavailable}</p></div>
    </section>
    {hotelsState.status === "loading" && <LoadingBlock />}
    {hotelsState.status === "error" && <ErrorBlock error={hotelsState.error} onRetry={hotelsState.reload} />}
    {hotelsState.status === "ready" && hotels.length === 0 && <EmptyBlock text={L.emptyCatalog} />}
    {hotelsState.status === "ready" && hotels.length > 0 && <>
      <form className="picker-row" onSubmit={(e) => e.preventDefault()} aria-label={L.demoAnchorNote}>
        <label className="form-field"><span>{L.pickCity}</span><select value={city} onChange={(e) => { setCity(e.target.value); setPickedHotel(""); setPickedDate(""); }}><option value="">{L.allCities}</option>{cities.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
        <label className="form-field"><span>{L.demoAnchorNote}</span><select value={hotel?.id ?? ""} onChange={(e) => { setPickedHotel(e.target.value); setPickedDate(""); }}>{filtered.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}</select></label>
        <label className="form-field"><span><CalendarDays size={15} aria-hidden="true" />{L.pickCheckIn}</span><select value={checkIn} onChange={(e) => setPickedDate(e.target.value)} disabled={dates.length === 0}>{dates.map((d) => <option key={d} value={d}>{longDate(d)}</option>)}</select></label>
      </form>
      {dates.length === 0 && <p className="error-message">{L.noSupportedDates}</p>}
      <div aria-busy={data.status === "loading"}>
        {data.status === "loading" && <LoadingBlock />}
        {data.status === "error" && <ErrorBlock error={data.error} onRetry={data.reload} />}
        {data.status === "ready" && <HotelierResult data={data.data} sort={sort} onSort={(key) => setSort((old) => ({ key, ascending: old.key === key ? !old.ascending : key === "price" }))} />}
      </div>
    </>}
  </div>;
}

function HotelierResult({ data, sort, onSort }: { data: HotelierResponse; sort: Sort; onSort: (key: Sort["key"]) => void }) {
  const g = data.groupSummary;
  const rows: CompsetHotel[] = [data.anchor, ...data.compset].sort((a, b) => {
    const delta = sort.key === "price" ? a.currentPrice - b.currentPrice : (a.reviewScore ?? -1) - (b.reviewScore ?? -1);
    return (sort.ascending ? delta : -delta) || a.name.localeCompare(b.name, "vi");
  });
  const sortIcon = sort.ascending ? <ArrowDownWideNarrow size={14} aria-hidden="true" /> : <ArrowUpWideNarrow size={14} aria-hidden="true" />;
  const vs = data.anchor.pctVsGroupMedian;
  const alerts = alertsPanel(data);
  return <>
    <div className="context-line"><span>{fullDate(data.checkIn)}</span><span className="context-status">{data.anchor.name} · <MapPin size={13} aria-hidden="true" /> {data.anchor.city} · Dữ liệu tới {longDate(data.dataAsOf)}</span></div>
    {g.insufficient && <p className="error-message" role="status">{g.reason === "anchor_review_missing" ? L.anchorReviewMissing : L.groupInsufficient} (n = {g.n})</p>}
    <section className="kpi-grid" aria-label="Chỉ số vị thế giá">
      <Kpi icon={<Wallet size={18} />} label={L.kpiYourPrice} value={vnd(data.anchor.currentPrice)} note="Giá niêm yết đã quan sát / phòng / đêm" primary />
      <Kpi icon={<Users size={18} />} label={L.kpiGroupMedian} value={g.currentMedianPrice === null ? dash : vnd(g.currentMedianPrice)} note={g.insufficient ? "Chưa đủ khách sạn tương đồng" : `${g.n} khách sạn tương đồng · không gồm bạn`} />
      <Kpi icon={<ChartNoAxesColumnIncreasing size={18} />} label={L.kpiVsGroup} value={pctOrDash(vs)} note={vs === null ? "Không có trung vị nhóm để so sánh" : vs > 0 ? "Giá của bạn cao hơn trung vị" : vs < 0 ? "Giá của bạn thấp hơn trung vị" : "Giá của bạn bằng trung vị"} />
      <Kpi icon={<ListOrdered size={18} />} label={L.kpiRank} value={g.priceRank ? `${g.priceRank.rank}/${g.priceRank.of}` : dash} note={L.rankNote} />
    </section>
    <div className="market-grid">
      <section className="panel chart-panel"><div className="section-title"><div><span className="section-kicker">MẶT BẰNG GIÁ</span><h2>Giá niêm yết đã quan sát theo ngày check-in</h2><p>Các ngày được hỗ trợ của khách sạn · VND / phòng / đêm</p></div></div>
        <CheckInChart points={data.checkInPrices} /><p className="chart-note">So sánh ngày lưu trú tại ngày dữ liệu, không phải ước lượng. Điều kiện phòng/giá có thể khác nhau.</p></section>
      <section className="panel forecast-panel"><div className="section-title"><div><span className="section-kicker">ƯỚC LƯỢNG THĂM DÒ</span><h2>Thay đổi ước lượng của nhóm so sánh</h2><p>Trung vị theo từng khách sạn, cùng ngày check-in {shortDate(data.checkIn)}</p></div></div>
        {g.perHorizon.length === 0 ? <p className="muted">{g.insufficient ? L.groupInsufficient : L.noData}</p>
          : <ul className="group-forecast">{g.perHorizon.map((h) => <li key={h.horizon}><strong>{horizonLabel(h.horizon)}</strong><span>n = {h.nMatched} (mô hình {h.nModel} · giữ giá {h.nPersistence})</span>
            <span>Trung vị giá hiện tại {vnd(h.currentMedianPrice)} → trung vị giá ước lượng {vnd(h.predictedMedianPrice)}</span><span>Trung vị % thay đổi: <b>{pct(h.medianPctChange)}</b></span></li>)}</ul>}
        {data.anchorForecast && <div className="anchor-forecast"><div className="forecast-meta"><span>Khách sạn của bạn</span><ModeBadge mode={data.anchorForecast.inferenceMode} /></div><ForecastCards forecast={data.anchorForecast} /></div>}
        <WarningList warnings={data.warnings} extra={data.anchorForecast?.warnings.map((w) => w.message)} />
      </section>
    </div>
    <div className="detail-grid">
      <section className="panel competitors-panel"><div className="section-title"><div><span className="section-kicker">NHÓM SO SÁNH</span><h2>Vị thế trong nhóm tương đồng <span className="count-badge">{data.compset.length}</span></h2><p>Cùng thành phố · check-in {shortDate(data.checkIn)} · điểm đánh giá lệch ≤ 0,5</p></div></div>
        <div className="mobile-sort"><span>Sắp xếp:</span><button type="button" onClick={() => onSort("price")} aria-pressed={sort.key === "price"}>Giá {sort.key === "price" && sortIcon}</button><button type="button" onClick={() => onSort("score")} aria-pressed={sort.key === "score"}>Điểm {sort.key === "score" && sortIcon}</button></div>
        <table className="competitor-table"><caption className="sr-only">Nhóm so sánh gồm khách sạn của bạn và {data.compset.length} khách sạn tương đồng. Trung vị nhóm không gồm khách sạn của bạn.</caption>
          <thead><tr><th scope="col">{L.tableHotel}</th><th scope="col" aria-sort={sort.key === "score" ? (sort.ascending ? "ascending" : "descending") : "none"}><button type="button" onClick={() => onSort("score")}>{L.tableScore} {sort.key === "score" && sortIcon}</button></th>
            <th scope="col" aria-sort={sort.key === "price" ? (sort.ascending ? "ascending" : "descending") : "none"}><button type="button" onClick={() => onSort("price")}>{L.tablePrice} {sort.key === "price" && sortIcon}</button></th><th scope="col">{L.tableVsGroup}</th><th scope="col">{L.table7days}</th><th scope="col">{L.tableGap}</th></tr></thead>
          <tbody>{rows.map((h) => <tr key={h.id} className={h.isAnchor ? "my-hotel-row" : ""}><th scope="row"><HotelName hotel={h} /></th><td><span className="score-pill">{reviewText(h.reviewScore)}</span></td><td className="table-price">{vnd(h.currentPrice)}</td>
            <td><span className={(h.pctVsGroupMedian ?? 0) > 0 ? "positive-gap" : "negative-gap"}>{pctOrDash(h.pctVsGroupMedian)}</span></td><td><span className="table-change">{pctOrDash(h.change7DaysPct)}</span></td><td>{gapText(h.reviewGap)}</td></tr>)}</tbody></table>
        <div className="competitor-cards">{rows.map((h) => <article key={h.id} className={`competitor-card ${h.isAnchor ? "my-hotel-row" : ""}`}><div className="competitor-card-top"><HotelName hotel={h} /><span className="score-pill">{reviewText(h.reviewScore)}</span></div>
          <div className="competitor-card-price"><strong>{vnd(h.currentPrice)}</strong></div><div className="competitor-card-stats"><span>So với trung vị <strong>{pctOrDash(h.pctVsGroupMedian)}</strong></span><span>7 ngày <strong>{pctOrDash(h.change7DaysPct)}</strong></span></div></article>)}</div>
        <p className="table-note"><Info size={14} aria-hidden="true" />{L.groupNote} {L.noSoldOut}</p>
      </section>
      <aside className="panel alerts-panel"><div className="section-title"><div><span className="section-kicker">ĐÁNG CHÚ Ý</span><h2>Tín hiệu <span className="count-badge">{alerts.badge}</span></h2><p>Luật so sánh trên giá đã quan sát và ước lượng thăm dò</p></div></div>
        {alerts.message !== null ? <p className="muted">{alerts.message}</p> : <ul className="alert-list">{data.alerts.map((a) => <li key={a.id}><span className={`alert-icon ${a.severity}`}>{a.severity === "attention" ? <CircleAlert size={18} aria-hidden="true" /> : <Info size={18} aria-hidden="true" />}</span>
          <div><span className={`severity-label ${a.severity}`}>{a.severity === "attention" ? "Lưu ý" : "Thông tin"}</span><h3>{a.title}</h3><p>{a.description}</p></div></li>)}</ul>}
        <div className="aside-note">{L.alertsNote} <Link href="/model" className="inline-link">{L.aboutModelCta}</Link>.</div>
      </aside>
    </div>
  </>;
}

function Kpi({ icon, label, value, note, primary = false }: { icon: React.ReactNode; label: string; value: string; note: string; primary?: boolean }) {
  return <article className={`kpi-card ${primary ? "kpi-primary" : ""}`}><div className="kpi-heading"><span>{label}</span><span className="kpi-icon" aria-hidden="true">{icon}</span></div><strong>{value}</strong><p>{note}</p></article>;
}
function HotelName({ hotel }: { hotel: CompsetHotel }) { return <span className="hotel-name">{hotel.name}{hotel.isAnchor && <span className="your-hotel">{L.you}</span>}</span>; }
