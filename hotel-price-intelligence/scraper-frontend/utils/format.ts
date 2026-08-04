import dayjs from "dayjs";

/** Ngày hiển thị cho người dùng Việt: dd/mm/yyyy. Input là chuỗi ISO (yyyy-mm-dd) hoặc Date. */
export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return "—";
  return dayjs(value).format("DD/MM/YYYY");
}

/** Ngày giờ đầy đủ hiển thị cho người dùng: dd/mm/yyyy HH:mm. */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  return dayjs(value).format("DD/MM/YYYY HH:mm");
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
