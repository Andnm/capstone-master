import dayjs from "dayjs";
import "dayjs/locale/vi";

const currency = new Intl.NumberFormat("vi-VN", { style: "currency", currency: "VND", maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const VN_TIME = "Asia/Ho_Chi_Minh";

export const vnd = (value: number) => currency.format(value);
/** Hiển thị điểm phần trăm (API đã là điểm %, không phải phân số). Chỉ để hiển thị: hướng do API quyết định, KHÔNG suy lại từ số làm tròn. */
export const pct = (value: number, signed = true) => {
  const text = percent.format(Math.abs(value) < 0.05 ? 0 : value);
  return `${signed && value >= 0.05 ? "+" : ""}${text}%`;
};
/** "YYYY-MM-DD" -> "DD/MM" bằng cắt chuỗi (không qua Date => không lệch múi giờ). */
export const shortDate = (value: string) => `${value.slice(8, 10)}/${value.slice(5, 7)}`;
export const longDate = (value: string) => `${value.slice(8, 10)}/${value.slice(5, 7)}/${value.slice(0, 4)}`;
export const fullDate = (value: string) => dayjs(`${value}T12:00:00`).locale("vi").format("dddd, DD/MM/YYYY");
export const addDays = (value: string, count: number) => dayjs(`${value}T12:00:00`).add(count, "day").format("YYYY-MM-DD");
export const calendarDate = (value: string) => new Date(`${value}T12:00:00`);
export const isoDay = (value: Date) => dayjs(value).format("YYYY-MM-DD");

/** Ngày VN (YYYY-MM-DD) của một thời điểm UTC; không dùng `.slice(0,10)` trên chuỗi UTC. */
export function vnDateOf(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: VN_TIME, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(iso));
}
export function vnDateTime(iso: string): string {
  return new Intl.DateTimeFormat("vi-VN", { timeZone: VN_TIME, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
}
/** Số ngày lịch (giờ VN) từ `dataAsOf` đến hôm nay. */
export function daysSince(dataAsOf: string, now: Date = new Date()): number {
  const today = vnDateOf(now.toISOString());
  const a = Date.UTC(Number(dataAsOf.slice(0, 4)), Number(dataAsOf.slice(5, 7)) - 1, Number(dataAsOf.slice(8, 10)));
  const b = Date.UTC(Number(today.slice(0, 4)), Number(today.slice(5, 7)) - 1, Number(today.slice(8, 10)));
  return Math.round((b - a) / 86_400_000);
}
export const reviewText = (score: number | null) => (score === null ? "—" : score.toFixed(1).replace(".", ","));
export const gapText = (gap: number | null) => (gap === null ? "—" : gap.toFixed(1).replace(".", ","));
export const spanText = (days: number | null) => (days === null ? null : `${new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 1 }).format(days)} ngày`);
