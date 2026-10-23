"use client";
import { useState } from "react";
import Link from "next/link";
import { DayPicker } from "react-day-picker";
import { vi } from "react-day-picker/locale";
import { CalendarDays, Clock3, MapPin, ShieldCheck, Sparkles } from "lucide-react";
import { getForecast, getHotels } from "@/lib/api";
import { calendarDate, fullDate, isoDay, longDate, reviewText, spanText, vnDateTime, vnd } from "@/lib/format";
import { forecastMeaning, L, signalSentences } from "@/lib/labels";
import { useApi } from "@/lib/use-api";
import type { ForecastResponse, HotelsResponse, HotelWithCheckIns } from "@/types";
import { HistoryChart } from "./charts";
import { ClassifierPanel, ForecastCards, ModeBadge, WarningList } from "./forecast-cards";
import { useSnapshot } from "./snapshot-provider";
import { EmptyBlock, ErrorBlock, LoadingBlock } from "./state-views";

const yesNo = (value: boolean | null, yes: string, no: string, unknown: string) => (value === null ? unknown : value ? yes : no);

export function ConsumerDashboard() {
  const snapshot = useSnapshot();
  const hotelsState = useApi<HotelsResponse>("hotels", (signal) => getHotels(signal));
  const [city, setCity] = useState("");
  const [pickedHotel, setPickedHotel] = useState("");
  const [pickedDate, setPickedDate] = useState("");
  const [monthOverride, setMonthOverride] = useState<Date | null>(null);

  const hotels: HotelWithCheckIns[] = hotelsState.status === "ready" ? hotelsState.data.hotels : [];
  const cities = [...new Set(hotels.map((h) => h.city))].sort((a, b) => a.localeCompare(b, "vi"));
  const filtered = hotels.filter((h) => !city || h.city === city).sort((a, b) => a.name.localeCompare(b.name, "vi"));
  const hotel = filtered.find((h) => h.id === pickedHotel) ?? filtered[0];
  const supported = new Set(hotel?.checkIns.map((c) => c.date) ?? []);
  const dates = hotel?.checkIns.map((c) => c.date) ?? [];
  const checkIn = supported.has(pickedDate) ? pickedDate : (dates[0] ?? "");
  const forecast = useApi<ForecastResponse>(hotel && checkIn ? `forecast|${hotel.id}|${checkIn}` : null, (signal) => getForecast({ hotelId: hotel!.id, checkIn }, signal));
  const month = monthOverride ?? (checkIn ? calendarDate(checkIn) : new Date());

  return <div className="dashboard consumer-dashboard">
    <section className="page-heading"><div><div className="eyebrow"><span className="small-line" />GÓC NHÌN KHÁCH ĐẶT PHÒNG</div><h1>{L.consumerHeading}<br className="mobile-break" /> {L.consumerHeading2}</h1><p>{L.consumerSub}</p></div>
      <span className="badge neutral-badge"><ShieldCheck size={15} aria-hidden="true" />{L.evidenceLine.split(".")[0]}</span></section>
    <div className="consumer-grid">
      <section className="panel search-panel"><div className="section-title"><div><span className="section-kicker">TRA CỨU</span><h2>Chọn kỳ lưu trú</h2><p>{snapshot.status === "ready" ? snapshot.data.snapshot.label : snapshot.status === "loading" ? L.loading : L.unavailable}</p></div></div>
        {hotelsState.status === "loading" && <LoadingBlock />}
        {hotelsState.status === "error" && <ErrorBlock error={hotelsState.error} onRetry={hotelsState.reload} />}
        {hotelsState.status === "ready" && hotels.length === 0 && <EmptyBlock text={L.emptyCatalog} />}
        {hotelsState.status === "ready" && hotels.length > 0 && <form onSubmit={(e) => e.preventDefault()}>
          <label className="form-field"><span>{L.pickCity}</span><select value={city} onChange={(e) => { setCity(e.target.value); setPickedHotel(""); setPickedDate(""); setMonthOverride(null); }}><option value="">{L.allCities}</option>{cities.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
          <label className="form-field"><span>{L.pickHotel}</span><select value={hotel?.id ?? ""} onChange={(e) => { setPickedHotel(e.target.value); setPickedDate(""); setMonthOverride(null); }}>{filtered.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}</select></label>
          {hotel && <p className="selected-hotel"><MapPin size={13} aria-hidden="true" />{hotel.city} <span>·</span> Điểm đánh giá {reviewText(hotel.reviewScore)}/10</p>}
          <label className="form-field"><span><CalendarDays size={15} aria-hidden="true" />{L.pickCheckIn}</span>
            <select value={checkIn} onChange={(e) => { setPickedDate(e.target.value); setMonthOverride(null); }} disabled={dates.length === 0}>{dates.map((d) => <option key={d} value={d}>{longDate(d)}</option>)}</select></label>
          {dates.length === 0 ? <p className="error-message">{L.noSupportedDates}</p> : <p className="field-hint">{dates.length} ngày được hỗ trợ cho khách sạn này; ngày không có dữ liệu bị vô hiệu trên lịch.</p>}
          {dates.length > 0 && <div className="calendar-wrap"><DayPicker mode="single" required month={month} onMonthChange={setMonthOverride} selected={checkIn ? calendarDate(checkIn) : undefined} locale={vi} weekStartsOn={1}
            startMonth={calendarDate(dates[0])} endMonth={calendarDate(dates[dates.length - 1])} disabled={(date: Date) => !supported.has(isoDay(date))} onSelect={(date) => { if (date) setPickedDate(isoDay(date)); }} showOutsideDays /></div>}
        </form>}
      </section>
      <div className="consumer-result" aria-busy={forecast.status === "loading"}>
        {forecast.status === "idle" && hotelsState.status !== "error" && <section className="panel empty-result"><h2>{L.consumerHeading}</h2><p>Chọn khách sạn và ngày check-in để xem lịch sử giá và ước lượng thăm dò.</p></section>}
        {forecast.status === "loading" && <section className="panel"><LoadingBlock /></section>}
        {forecast.status === "error" && <ErrorBlock error={forecast.error} onRetry={forecast.reload} />}
        {forecast.status === "ready" && <ForecastResult forecast={forecast.data} />}
      </div>
    </div>
  </div>;
}

function ForecastResult({ forecast }: { forecast: ForecastResponse }) {
  const room = forecast.roomSummary;
  const span = spanText(forecast.historySpanDays);
  const meaning = forecast.predictions.length > 0 ? forecastMeaning(forecast.checkIn, forecast.predictions.map((p) => p.horizon), forecast.dataAsOf) : "";
  return <>
    <section className="panel result-summary"><div className="section-title"><div><span className="section-kicker">GIÁ ĐÃ QUAN SÁT</span><h2>{forecast.hotel.name}</h2><p>Check-in {fullDate(forecast.checkIn)} · còn {forecast.leadDays} ngày kể từ ngày dữ liệu</p></div></div>
      <div className="result-badges"><ModeBadge mode={forecast.inferenceMode} /></div>
      <div className="room-grid"><div><span className="meta-label">Giá niêm yết đã quan sát</span><strong className="current-price">{vnd(forecast.currentPrice)}</strong><small>Quan sát lúc {vnDateTime(forecast.currentObservedAt)} (giờ Việt Nam)</small></div>
        <div className="room-reference"><strong>Thông tin phòng đã quan sát</strong>
          <p>{room.maxOccupancy === null ? "Sức chứa chưa rõ" : `Tối đa ${room.maxOccupancy} khách`} · {room.areaM2 === null ? "diện tích chưa rõ" : `${room.areaM2.toLocaleString("vi-VN")} m²`}</p>
          <p>{yesNo(room.breakfastIncluded, "Có bữa sáng", "Không gồm bữa sáng", "Bữa sáng chưa rõ")} · {yesNo(room.freeCancellation, "Hủy miễn phí", "Không hủy miễn phí", "Hủy phòng chưa rõ")}</p></div></div>
      <div className="history-stats"><span><Clock3 size={13} aria-hidden="true" />{forecast.historyObservationCount} lần quan sát</span><span>{span ?? L.historyMissing}</span></div>
    </section>
    <section className="panel"><div className="section-title"><div><span className="section-kicker">LỊCH SỬ & ƯỚC LƯỢNG THĂM DÒ</span><h2>Giá đã quan sát và ước lượng</h2><p>VND / phòng / đêm · {meaning || "Chưa có mốc ước lượng cho ngày này."}</p></div></div>
      <HistoryChart forecast={forecast} /><ForecastCards forecast={forecast} /></section>
    {forecast.predictions.length > 0 && <section className="panel hint-panel"><div className="hint-icon"><Sparkles size={20} aria-hidden="true" /></div><div><span className="section-kicker">{L.signalTitle.toUpperCase()}</span>
      <ul className="signal-list">{signalSentences(forecast.predictions).map((s) => <li key={s}>{s}</li>)}</ul><p>{L.signalDisclaimer}</p></div></section>}
    <WarningList warnings={forecast.warnings} />
    <ClassifierPanel forecast={forecast} />
    <p className="field-hint">{L.evidenceLine} <Link href="/model" className="inline-link">{L.aboutModelCta}</Link>. {L.notBookingSite}</p>
  </>;
}
