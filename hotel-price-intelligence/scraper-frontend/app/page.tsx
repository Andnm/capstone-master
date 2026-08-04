"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { DayPicker } from "react-day-picker";
import { uploadHotelList } from "@/lib/api";
import { formatDate, toISODate } from "@/utils/format";

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [selectedDates, setSelectedDates] = useState<Date[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checkinDates = selectedDates.map(toISODate).sort();
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || checkinDates.length === 0) return;
    setIsUploading(true);
    setError(null);
    try {
      const res = await uploadHotelList(file, checkinDates);
      router.push(`/jobs/${res.run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Có lỗi xảy ra");
      setIsUploading(false);
    }
  }

  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <h1 className="text-2xl font-semibold">Cào giá khách sạn</h1>
      <p className="mt-1.5 text-sm text-muted">
        Upload file Excel chứa link Booking.com (cột A = tên, cột B = link), chọn ngày checkin
        muốn cào (checkout tự động = checkin + 1 đêm). Job chạy nền — có thể đóng trình duyệt,
        quay lại sau xem tiến độ.
      </p>

      <form onSubmit={handleSubmit} className="mt-8 grid gap-6 md:grid-cols-2">
        <section className="space-y-6">
          <div className="rounded-xl border border-border bg-surface p-5">
            <label className="text-sm font-medium">File Excel</label>
            <input
              type="file"
              accept=".xlsx,.xls"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="mt-3 block w-full text-sm text-muted file:mr-4 file:rounded-lg file:border-0 file:bg-accent file:px-4 file:py-2 file:text-sm file:font-medium file:text-accent-foreground hover:file:opacity-90"
            />
            {file && (
              <p className="mt-2 text-xs text-muted">
                Đã chọn: <span className="text-foreground">{file.name}</span>
              </p>
            )}
          </div>

          <div className="rounded-xl border border-border bg-surface p-5">
            <label className="text-sm font-medium">Ngày checkin đã chọn</label>
            <p className="mt-1 text-xs text-muted">
              Bấm vào 1 ngày trên lịch để thêm, bấm lại để bỏ chọn. Không giới hạn bởi lịch cào tự
              động (1/3/7/14/30/60 ngày tới do scheduler chạy riêng) — chọn ngày bất kỳ trong
              tương lai, kể cả năm sau.
            </p>

            {checkinDates.length === 0 ? (
              <p className="mt-4 text-sm text-muted">Chưa chọn ngày nào.</p>
            ) : (
              <ul className="mt-4 flex flex-wrap gap-2">
                {checkinDates.map((iso) => (
                  <li
                    key={iso}
                    className="flex items-center gap-2 rounded-full bg-accent/10 px-3 py-1 text-sm text-accent"
                  >
                    {formatDate(iso)}
                    <button
                      type="button"
                      onClick={() =>
                        setSelectedDates(selectedDates.filter((d) => toISODate(d) !== iso))
                      }
                      className="text-accent/70 hover:text-accent"
                      aria-label={`Bỏ chọn ${formatDate(iso)}`}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {error && (
            <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
          )}

          <button
            type="submit"
            disabled={!file || checkinDates.length === 0 || isUploading}
            className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-accent-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {isUploading ? "Đang gửi..." : "Bắt đầu cào"}
          </button>

          
        </section>

        <section className="rounded-xl border border-border bg-surface p-5">
          <DayPicker
            mode="multiple"
            selected={selectedDates}
            onSelect={(dates) => setSelectedDates(dates ?? [])}
            disabled={{ before: today }}
            showOutsideDays
            className="mx-auto"
          />
        </section>
      </form>
    </main>
  );
}
