"use client";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import dayjs from "dayjs";
import type { ForecastResponse, HotelierResponse } from "@/types";
import { addDays, shortDate, vnd } from "@/lib/format";

const yTick = (value: number) => `${new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 }).format(value / 1000)}k`;

export function CheckInChart({ points }: { points: HotelierResponse["checkInPrices"] }) {
  return <>
    <div className="chart-legend"><span><i className="legend-line my-line" />Khách sạn của bạn</span><span><i className="legend-line group-line" />Trung bình 8 đối thủ</span></div>
    <div className="chart-frame" role="img" aria-label="Giá niêm yết hiện tại theo 14 ngày check-in; đường tím là khách sạn của bạn, đường xanh là trung bình đối thủ. Bảng dữ liệu nằm dưới biểu đồ.">
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 600, height: 270 }}>
        <LineChart data={points} margin={{ top: 12, right: 12, bottom: 2, left: -16 }} accessibilityLayer>
          <CartesianGrid stroke="var(--border)" vertical={false} strokeDasharray="3 4" />
          <XAxis dataKey="checkIn" tickFormatter={shortDate} axisLine={false} tickLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} minTickGap={26} dy={8} />
          <YAxis tickFormatter={yTick} domain={[600000, 1400000]} axisLine={false} tickLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} width={64} />
          <Tooltip formatter={(value) => vnd(Number(value))} labelFormatter={(label) => `Check-in ${shortDate(String(label))}`} contentStyle={{ borderRadius: 12, border: "1px solid var(--border)", fontSize: 12, background: "var(--surface)", color: "var(--foreground)" }} />
          <Line type="linear" dataKey="myPrice" name="Khách sạn của bạn" stroke="var(--accent)" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
          <Line type="linear" dataKey="competitorAverage" name="Trung bình nhóm" stroke="#0d9488" strokeWidth={2.2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
    <details className="chart-data"><summary>Xem số liệu biểu đồ</summary><div className="data-grid">{points.map((p) => <div key={p.checkIn}><strong>{shortDate(p.checkIn)}</strong><span>Của bạn: {vnd(p.myPrice)}</span><span>Nhóm: {vnd(p.competitorAverage)}</span></div>)}</div></details>
  </>;
}

export function HistoryChart({ forecast }: { forecast: ForecastResponse }) {
  const today = forecast.currentObservedAt.slice(0, 10);
  const firstOffset = Math.min(0, ...forecast.history.map((p) => dayjs(p.date).diff(dayjs(today), "day")));
  const historyTicks = firstOffset < 0 ? [firstOffset, Math.round(firstOffset / 2), 0, 7, 14] : [0, 1, 3, 7, 14];
  const points = [
    ...forecast.history.map((p) => ({ offset: dayjs(p.date).diff(dayjs(today), "day"), observed: p.price, predicted: p.date === today ? p.price : undefined })),
    ...forecast.predictions.filter((p) => forecast.supportedHorizons.includes(p.horizon)).map((p) => ({ offset: p.horizon, observed: undefined, predicted: p.predictedPrice })),
  ];
  return <>
    <div className="chart-legend"><span><i className="legend-line my-line" />Giá đã quan sát (mẫu)</span><span><i className="legend-line dashed-line" />Dự báo minh họa</span></div>
    <div className="chart-frame" role="img" aria-label="Lịch sử giá nét liền và các mốc dự báo nét đứt. Không có dải tin cậy. Các giá trị dự báo được liệt kê trong thẻ bên dưới.">
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 600, height: 270 }}>
        <LineChart data={points} margin={{ top: 15, right: 16, bottom: 2, left: -16 }} accessibilityLayer>
          <CartesianGrid stroke="var(--border)" vertical={false} strokeDasharray="3 4" />
          <XAxis type="number" dataKey="offset" domain={[firstOffset, 14]} ticks={historyTicks} tickFormatter={(n) => shortDate(addDays(today, Number(n)))} axisLine={false} tickLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} dy={8} />
          <YAxis domain={[600000, 1600000]} tickFormatter={yTick} axisLine={false} tickLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} width={64} />
          <Tooltip formatter={(value) => vnd(Number(value))} labelFormatter={(n) => shortDate(addDays(today, Number(n)))} contentStyle={{ borderRadius: 12, border: "1px solid var(--border)", fontSize: 12, background: "var(--surface)", color: "var(--foreground)" }} />
          <Line type="linear" dataKey="observed" name="Giá đã quan sát" stroke="var(--accent)" strokeWidth={2.5} dot={{ r: 3, fill: "var(--accent)", strokeWidth: 0 }} isAnimationActive={false} />
          <Line type="linear" dataKey="predicted" name="Dự báo minh họa" stroke="#0d9488" strokeWidth={2.3} strokeDasharray="5 5" dot={{ r: 4, fill: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
    <p className="chart-note">Ngày trên trục là ngày quan sát/dự báo, không phải ngày check-in. Không thể hiện dải tin cậy.</p>
    <details className="chart-data"><summary>Xem lịch sử giá</summary><div className="data-grid">{forecast.history.map((p) => <div key={p.date}><strong>{shortDate(p.date)}</strong><span>{vnd(p.price)}</span></div>)}</div></details>
  </>;
}
