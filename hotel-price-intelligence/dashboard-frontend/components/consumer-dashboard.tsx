"use client";
import { useEffect, useRef, useState } from "react";
import { DayPicker } from "react-day-picker";
import { vi } from "react-day-picker/locale";
import { ArrowRight, CalendarDays, Check, CheckCheck, Circle, FlaskConical, Search, ShieldCheck, Sparkles, LoaderCircle, MapPin, Clock3 } from "lucide-react";
import type { DashboardConfig, ForecastResponse, Hotel } from "@/types";
import { getForecast } from "@/lib/api";
import { bookingHint, calendarDate, fullDate, isoDay, observedTime, shortDate, vnd } from "@/lib/format";
import { ConfidenceBadge, ForecastCards, ForecastCaution, ModeBadge } from "./forecast-cards";
import { HistoryChart } from "./charts";

const STEPS = ["Mở Booking.com", "Đọc bảng phòng", "Lưu snapshot", "Dựng đặc trưng", "Dự báo"];
function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) { reject(new Error("aborted")); return; }
    const abort = () => { clearTimeout(timer); reject(new Error("aborted")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}
export function ConsumerDashboard({ config, hotels }: { config: DashboardConfig; hotels: Hotel[] }) {
  const [hotelId, setHotelId] = useState(hotels[0].id);
  const [checkIn, setCheckIn] = useState(config.defaultCheckIn);
  const [month, setMonth] = useState(calendarDate(config.defaultCheckIn));
  const [result, setResult] = useState<ForecastResponse | null>(null);
  const [step, setStep] = useState(-1);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const operation = useRef<AbortController | null>(null);
  useEffect(() => () => operation.current?.abort(), []);
  const supported = /^\d{4}-\d{2}-\d{2}$/.test(checkIn) && checkIn >= config.consumerMinDate && checkIn <= config.consumerMaxDate;
  const hotel = hotels.find((h) => h.id === hotelId)!;
  const hint = result ? bookingHint(result) : null;
  function selectDate(value: string) { setCheckIn(value); setResult(null); setStep(-1); setError(""); if (value >= config.consumerMinDate && value <= config.consumerMaxDate) setMonth(calendarDate(value)); }
  async function checkPrice(e: React.FormEvent) {
    e.preventDefault();
    if (!supported || running) return;
    const controller = new AbortController(); operation.current = controller;
    setRunning(true); setResult(null); setError("");
    try {
      for (let i = 0; i < STEPS.length; i++) { setStep(i); await pause(700, controller.signal); }
      const response = await getForecast({ hotelId, checkIn });
      if (controller.signal.aborted) return;
      setResult(response); setStep(STEPS.length);
    } catch (caught) { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Không thể chạy mô phỏng."); }
    finally { if (!controller.signal.aborted) setRunning(false); }
  }
  return <div className="dashboard consumer-dashboard">
    <section className="page-heading"><div><div className="eyebrow"><span className="small-line" />GÓC NHÌN KHÁCH ĐẶT PHÒNG</div><h1>Quan sát giá.<br className="mobile-break" /> Lên kế hoạch lưu trú.</h1><p>Tham khảo xu hướng giá của một phòng cụ thể, không đặt phòng tại đây.</p></div><span className="badge neutral-badge"><ShieldCheck size={15} aria-hidden="true" />Cùng engine · hai góc nhìn</span></section>
    <div className="consumer-grid">
      <section className="panel search-panel"><div className="section-title"><div><span className="section-kicker">TRA CỨU MINH HỌA</span><h2>Chọn kỳ lưu trú</h2><p>3 khách sạn hư cấu tại Đà Lạt</p></div><Search size={20} className="muted" aria-hidden="true" /></div>
        <form onSubmit={(e) => void checkPrice(e)}>
          <label className="form-field"><span>Khách sạn</span><select value={hotelId} disabled={running} onChange={(e) => { setHotelId(e.target.value); setResult(null); setStep(-1); setError(""); }}>{hotels.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}</select></label>
          <p className="selected-hotel"><MapPin size={13} aria-hidden="true" />{hotel.city} <span>·</span> Điểm mẫu {hotel.reviewScore.toFixed(1).replace(".", ",")}/10</p>
          <label className="form-field"><span><CalendarDays size={15} aria-hidden="true" />Ngày check-in</span><input type="date" value={checkIn} disabled={running} required aria-describedby="consumer-range" onChange={(e) => selectDate(e.target.value)} /></label>
          <p id="consumer-range" className={supported ? "field-hint" : "error-message"}>{supported ? `Hỗ trợ ${shortDate(config.consumerMinDate)}–${shortDate(config.consumerMaxDate)}/2026 (1–30 ngày tới)` : "Ngoài phạm vi hỗ trợ (1-30 ngày tới)"}</p>
          <div className={`calendar-wrap ${running ? "calendar-disabled" : ""}`}><DayPicker mode="single" required month={month} onMonthChange={setMonth} selected={supported ? calendarDate(checkIn) : undefined} today={calendarDate(config.mockToday)} locale={vi} weekStartsOn={1} startMonth={calendarDate(config.consumerMinDate)} endMonth={calendarDate(config.consumerMaxDate)} disabled={running ? true : [{ before: calendarDate(config.consumerMinDate) }, { after: calendarDate(config.consumerMaxDate) }]} onSelect={(date) => { if (date) selectDate(isoDay(date)); }} showOutsideDays /></div>
          <button className="primary-button" type="submit" disabled={!supported || running}>{running ? <><LoaderCircle size={17} className="spin" aria-hidden="true" />Đang mô phỏng…</> : <><Search size={17} aria-hidden="true" />Kiểm tra giá <ArrowRight size={17} aria-hidden="true" /></>}</button>
          <p className="button-note"><FlaskConical size={13} aria-hidden="true" />Mô phỏng · không mở Booking.com thật</p>
        </form>
        <div className="simulation"><div className="simulation-heading"><span>Quy trình kiểm tra giá</span><span className="badge neutral-badge">Mô phỏng</span></div><ol>{STEPS.map((label, i) => <li key={label} className={step > i ? "step-done" : step === i && running ? "step-active" : ""}><span className="step-number" aria-hidden="true">{step > i ? <Check size={13} /> : i + 1}</span><span>{label}</span>{step === i && running && <LoaderCircle size={13} className="spin" aria-hidden="true" />}</li>)}</ol><p className="simulation-live" role="status" aria-live="polite" aria-atomic="true">{running ? `Mô phỏng bước ${step + 1}/5: ${STEPS[step]}` : result ? "Mô phỏng hoàn tất — đã tạo kết quả mẫu." : "Sẵn sàng mô phỏng 5 bước, khoảng 3,5 giây."}</p></div>
      </section>
      <div className="consumer-result" aria-busy={running}>
        {error && <p className="error-message" role="alert">{error}</p>}
        {!result ? <section className="panel empty-result"><div className="empty-visual" aria-hidden="true"><span className="empty-orbit" /><span className="empty-orbit second" /><span className="empty-icon">{running ? <LoaderCircle size={32} className="spin" /> : <ChartIcon />}</span></div><span className="section-kicker">{running ? "ĐANG CHẠY MÔ PHỎNG" : "MỘT GÓC NHÌN VỀ GIÁ"}</span><h2>{running ? STEPS[step] : "Giá phòng kể một câu chuyện."}</h2><p>{running ? "Đây là quy trình giả lập. Không có dữ liệu thật được thu thập." : "Chọn khách sạn và ngày check-in, rồi kiểm tra giá để xem lịch sử và các mốc dự báo minh họa."}</p><div className="empty-features"><span><Clock3 size={16} aria-hidden="true" />1 · 3 · 7 · 14 ngày</span><span><ShieldCheck size={16} aria-hidden="true" />Không đặt phòng</span></div><div className="scenario-note"><strong>Thử kịch bản có lịch sử</strong><span>{hotels[0].name} · {shortDate(config.defaultCheckIn)}/{config.defaultCheckIn.slice(0, 4)}</span><small>Các lựa chọn khác minh họa chế độ đánh giá sơ bộ.</small></div></section> : <>
          <section className="panel result-summary"><div className="section-title"><div><span className="section-kicker">KẾT QUẢ MẪU</span><h2>{hotel.name}</h2><p>Check-in {fullDate(checkIn)}</p></div><span className="result-complete"><CheckCheck size={15} aria-hidden="true" />Mô phỏng hoàn tất</span></div><div className="result-badges"><ModeBadge forecast={result} /><ConfidenceBadge forecast={result} /></div><div className="room-grid"><div><span className="meta-label">Giá niêm yết đã quan sát</span><strong className="current-price">{vnd(result.currentPrice)}</strong><small>Ngày mẫu {shortDate(result.currentObservedAt.slice(0, 10))}/2026 · {observedTime(result.currentObservedAt)}</small></div><div className="room-reference"><strong>{result.roomType}</strong><p>{result.rateConditions.breakfastIncluded === null ? "Bữa sáng chưa rõ" : result.rateConditions.breakfastIncluded ? "Có bữa sáng" : "Không gồm bữa sáng"} · {result.rateConditions.freeCancellation === null ? "Hủy phòng chưa rõ" : result.rateConditions.freeCancellation ? "Hủy miễn phí" : "Không hủy miễn phí"}</p><p>{result.rateConditions.payment}</p><span className={`badge ${result.referenceStatus === "approved" ? "badge-history" : "badge-cold"}`}>reference_status: {result.referenceStatus} · {result.referenceStatus === "approved" ? "Đã duyệt" : "Đề xuất"}</span></div></div><div className="history-stats"><span><HistoryLabel />{result.historyObservationCount} snapshot</span><span>{result.historySpanDays} ngày lịch sử</span><span>{result.historySpanDays ? `${shortDate(result.history[0].date)}–${shortDate(result.history[result.history.length - 1].date)}/${result.history[result.history.length - 1].date.slice(0, 4)}` : "Chỉ một lần quan sát"}</span></div></section>
          <section className="panel"><div className="section-title"><div><span className="section-kicker">LỊCH SỬ & XU HƯỚNG</span><h2>Giá đã quan sát và dự báo</h2><p>VND / phòng / đêm · mốc h tính từ {shortDate(config.mockToday)}</p></div></div><HistoryChart forecast={result} /><ForecastCards forecast={result} /></section>
          <section className="panel hint-panel"><div className="hint-icon"><Sparkles size={20} aria-hidden="true" /></div><div><span className="section-kicker">GỢI Ý THỜI ĐIỂM · THAM KHẢO</span><h2>{hint?.title}</h2><p>{hint?.reason}</p></div></section>
          <ForecastCaution warnings={result.warnings} />
        </>}
      </div>
    </div>
  </div>;
}
function ChartIcon() { return <span className="tiny-chart"><span /><span /><span /><span /></span>; }
function HistoryLabel() { return <Circle size={9} fill="currentColor" aria-hidden="true" />; }
