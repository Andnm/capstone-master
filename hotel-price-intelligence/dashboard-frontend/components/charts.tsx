"use client";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import dayjs from "dayjs";
import type { ForecastResponse, HotelierResponse } from "@/types";
import { addDays, shortDate, vnd } from "@/lib/format";
import { L } from "@/lib/labels";

const yTick = (value: number) => (value >= 1_000_000 ? `${(value / 1_000_000).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}tr` : `${Math.round(value / 1000)}k`);
const padded = ([min, max]: readonly [number, number]): [number, number] => [Math.max(0, Math.floor(min * 0.9)), Math.ceil(max * 1.1)];
const tooltipStyle = { borderRadius: 12, border: "1px solid var(--border)", fontSize: 12, background: "var(--surface)", color: "var(--foreground)" };

/** So sánh giá theo các ngày check-in được hỗ trợ của khách sạn; trục Y TỰ ĐỘNG theo dữ liệu (giá thật có thể tới hàng chục triệu); khoảng trống giữ nguyên khoảng trống (null không thành 0). */
export function CheckInChart({ points }: { points: HotelierResponse["checkInPrices"] }) {
  return <>
    <div className="chart-legend"><span><i className="legend-line my-line" />{L.chartAnchor}</span><span><i className="legend-line group-line" />{L.chartGroup}</span></div>
    <div className="chart-frame" role="img" aria-label="Giá niêm yết đã quan sát theo ngày check-in: đường tím là khách sạn của bạn, đường xanh là trung vị nhóm so sánh. Bảng số liệu nằm dưới biểu đồ.">
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 600, height: 270 }}>
        <LineChart data={points} margin={{ top: 12, right: 12, bottom: 2, left: 0 }} accessibilityLayer>
          <CartesianGrid stroke="var(--border)" vertical={false} strokeDasharray="3 4" />
          <XAxis dataKey="checkIn" tickFormatter={shortDate} axisLine={false} tickLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} minTickGap={22} dy={8} />
          <YAxis tickFormatter={yTick} domain={padded} axisLine={false} tickLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} width={58} />
          <Tooltip formatter={(value) => (value === null || value === undefined ? L.unavailable : vnd(Number(value)))} labelFormatter={(label) => `Check-in ${shortDate(String(label))}`} contentStyle={tooltipStyle} />
          <Line type="linear" dataKey="anchorPrice" name={L.chartAnchor} stroke="var(--accent)" strokeWidth={2.5} dot={{ r: 3 }} activeDot={{ r: 4 }} isAnimationActive={false} />
          <Line type="linear" dataKey="groupMedianPrice" name={L.chartGroup} stroke="#0d9488" strokeWidth={2.2} dot={{ r: 3 }} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
    <details className="chart-data"><summary>Xem số liệu biểu đồ</summary><div className="data-grid">{points.map((p) => <div key={p.checkIn}><strong>{shortDate(p.checkIn)}</strong><span>{L.chartAnchor}: {vnd(p.anchorPrice)}</span><span>{L.chartGroup}: {p.groupMedianPrice === null ? L.unavailable : vnd(p.groupMedianPrice)} (n={p.n})</span></div>)}</div></details>
  </>;
}

interface Row { offset: number; observed?: number; predicted?: number }

/** Lịch sử giá đã quan sát của CHÍNH chuỗi (≤ ngày dữ liệu) + các mốc ước lượng. Trục X là NGÀY QUAN SÁT (khoảng cách theo thời gian thật), không phải ngày check-in. */
export function HistoryChart({ forecast }: { forecast: ForecastResponse }) {
  const base = forecast.dataAsOf;
  const offsetOf = (date: string) => dayjs(`${date}T12:00:00`).diff(dayjs(`${base}T12:00:00`), "day");
  const rows = new Map<number, Row>();
  for (const p of forecast.history) rows.set(offsetOf(p.date), { offset: offsetOf(p.date), observed: p.price });
  rows.set(0, { ...(rows.get(0) ?? { offset: 0, observed: forecast.currentPrice }), predicted: forecast.currentPrice });
  for (const p of forecast.predictions) rows.set(p.horizon, { ...(rows.get(p.horizon) ?? { offset: p.horizon }), predicted: p.predictedPrice });
  const data = [...rows.values()].sort((a, b) => a.offset - b.offset);
  const first = data[0].offset;
  const last = data[data.length - 1].offset;
  const ticks = [...new Set([first, Math.round(first / 2), 0, last])].sort((a, b) => a - b);
  return <>
    <div className="chart-legend"><span><i className="legend-line my-line" />{L.chartObserved}</span><span><i className="legend-line dashed-line" />{L.chartPredicted}</span></div>
    <div className="chart-frame" role="img" aria-label="Lịch sử giá đã quan sát nét liền và các mốc ước lượng thăm dò nét đứt. Không có dải tin cậy. Giá trị ước lượng được liệt kê trong thẻ bên dưới.">
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 600, height: 270 }}>
        <LineChart data={data} margin={{ top: 15, right: 16, bottom: 2, left: 0 }} accessibilityLayer>
          <CartesianGrid stroke="var(--border)" vertical={false} strokeDasharray="3 4" />
          <XAxis type="number" dataKey="offset" domain={[first, last]} ticks={ticks} tickFormatter={(n) => shortDate(addDays(base, Number(n)))} axisLine={false} tickLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} dy={8} />
          <YAxis domain={padded} tickFormatter={yTick} axisLine={false} tickLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} width={58} />
          <Tooltip formatter={(value) => vnd(Number(value))} labelFormatter={(n) => shortDate(addDays(base, Number(n)))} contentStyle={tooltipStyle} />
          <Line type="linear" dataKey="observed" name={L.chartObserved} stroke="var(--accent)" strokeWidth={2.5} dot={{ r: 3, fill: "var(--accent)", strokeWidth: 0 }} isAnimationActive={false} />
          <Line type="linear" dataKey="predicted" name={L.chartPredicted} stroke="#0d9488" strokeWidth={2.3} strokeDasharray="5 5" dot={{ r: 4, fill: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
    <p className="chart-note">Trục ngang là ngày quan sát (không phải ngày check-in). Không thể hiện dải tin cậy.</p>
    <details className="chart-data"><summary>Xem lịch sử giá</summary><div className="data-grid">{forecast.history.map((p) => <div key={p.date}><strong>{shortDate(p.date)}</strong><span>{vnd(p.price)}</span></div>)}</div></details>
  </>;
}
