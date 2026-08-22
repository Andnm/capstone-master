import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import timezone from "dayjs/plugin/timezone";

dayjs.extend(utc);
dayjs.extend(timezone);

// Backend lưu observed_at/started_at/finished_at... theo UTC (xem CLAUDE.md muc 4.3).
// Ép hiển thị theo giờ Việt Nam thay vì tin vào timezone hệ thống của trình duyệt đang
// xem trang - trước đây dayjs(value).format(...) dùng timezone máy client, nên nếu máy đó
// không đặt Asia/Ho_Chi_Minh (vd server, máy set UTC...) giờ hiển thị bị lệch ~7 tiếng.
const DISPLAY_TIMEZONE = "Asia/Ho_Chi_Minh";

/** Ngày hiển thị cho người dùng Việt: dd/mm/yyyy. Input là chuỗi ISO (yyyy-mm-dd) hoặc Date -
 * đây là ngày lịch thuần (checkin_date...), không phải mốc thời gian UTC nên không đổi timezone. */
export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return "—";
  return dayjs(value).format("DD/MM/YYYY");
}

/** Ngày giờ đầy đủ hiển thị cho người dùng: dd/mm/yyyy HH:mm, luôn theo giờ Việt Nam bất kể
 * timezone của thiết bị đang xem. Dùng cho các mốc UTC thật (started_at, created_at...). */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  return dayjs(value).tz(DISPLAY_TIMEZONE).format("DD/MM/YYYY HH:mm");
}

/** Date object -> chuỗi ISO yyyy-mm-dd, dùng khi gửi lên API/lưu dữ liệu (dễ đọc cho model/DB). */
export function toISODate(date: Date): string {
  return dayjs(date).format("YYYY-MM-DD");
}

/** Chuỗi ISO yyyy-mm-dd -> Date object (00:00 local), dùng để nạp vào DayPicker. */
export function fromISODate(value: string): Date {
  return dayjs(value, "YYYY-MM-DD").toDate();
}

export function isPastDate(value: string | Date): boolean {
  return dayjs(value).startOf("day").isBefore(dayjs().startOf("day"));
}
