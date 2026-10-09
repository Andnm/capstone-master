import dayjs from "dayjs";
import "dayjs/locale/vi";
import type { Direction, ForecastResponse, HorizonPrediction } from "@/types";

const currency = new Intl.NumberFormat("vi-VN", { style: "currency", currency: "VND", maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
export const vnd = (value: number) => currency.format(value);
export const pct = (value: number, signed = true) => `${signed && value > 0 ? "+" : ""}${percent.format(value)}%`;
export const shortDate = (value: string) => dayjs(value).format("DD/MM");
export const fullDate = (value: string) => dayjs(value).locale("vi").format("dddd, DD/MM/YYYY");
export const observedTime = (value: string) => new Intl.DateTimeFormat("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
export const addDays = (value: string, count: number) => dayjs(value).add(count, "day").format("YYYY-MM-DD");
export const directionFor = (change: number): Direction => Math.abs(change) <= 2 ? "stable" : change > 2 ? "up" : "down";
export const calendarDate = (value: string) => new Date(`${value}T12:00:00`);
export const isoDay = (value: Date) => dayjs(value).format("YYYY-MM-DD");

export function bookingHint(forecast: ForecastResponse): { title: string; reason: string } {
  const shortTerm = forecast.predictions.filter((p) => p.horizon <= 7 && forecast.supportedHorizons.includes(p.horizon));
  const rising = shortTerm.find((p) => p.expectedPctChange >= 2);
  const falling = shortTerm.find((p) => p.expectedPctChange <= -2);
  const evidence: HorizonPrediction | undefined = rising ?? falling;
  const suffix = forecast.inferenceMode === "cold_start" ? " (đánh giá sơ bộ)" : "";
  if (evidence) return {
    title: `${rising ? "Nên đặt sớm" : "Có thể chờ"}${suffix}`,
    reason: `Minh họa h${evidence.horizon}: ${pct(evidence.expectedPctChange)} (${vnd(evidence.expectedDelta)}) so với ${vnd(forecast.currentPrice)}. Đây là gợi ý tham khảo, không phải khuyến nghị giao dịch.`,
  };
  return {
    title: `Giá dự kiến ổn định, chưa có cơ sở để chờ${suffix}`,
    reason: `Các mốc trong 7 ngày: ${shortTerm.map((p) => `h${p.horizon} ${pct(p.expectedPctChange)}`).join("; ") || "chưa có mốc được hỗ trợ"}. Chưa có thay đổi đạt ±2%.`,
  };
}
