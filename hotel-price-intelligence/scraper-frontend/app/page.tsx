"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { DayPicker } from "react-day-picker";
import {
  getWorkerHealth,
  preflightHotelList,
  uploadHotelList,
  type PreflightResult,
  type WorkerHealth,
} from "@/lib/api";
import { formatDate, formatDateTime, toISODate } from "@/utils/format";
import Skeleton from "@/components/Skeleton";

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [selectedDates, setSelectedDates] = useState<Date[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isChecking, setIsChecking] = useState(false);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveArtifacts, setSaveArtifacts] = useState(false);
  const [worker, setWorker] = useState<WorkerHealth | null>(null);

  const checkinDates = selectedDates.map(toISODate).sort();
  const outOfScopeSheets =
    preflight?.sheets.filter(
      (sheet) => sheet.total_rows > 0 && !sheet.in_scope,
    ) ?? [];
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  useEffect(() => {
    let cancelled = false;
    async function refreshWorker() {
      try {
        const health = await getWorkerHealth();
        if (!cancelled) setWorker(health);
      } catch {
        if (!cancelled)
          setWorker({
            online: false,
            waiting_for_network: false,
            message: "Không kết nối được worker health",
            worker_id: null,
            status: null,
            heartbeat_at: null,
            heartbeat_age_seconds: null,
            current_item_id: null,
            scraper_version: null,
            status_reason: null,
            paused_at: null,
            next_probe_at: null,
            network_failure_count: 0,
          });
      }
    }
    void refreshWorker();
    const timer = setInterval(refreshWorker, 10_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  async function handleFileChange(nextFile: File | null) {
    setFile(nextFile);
    setPreflight(null);
    setError(null);
    if (!nextFile) return;
    setIsChecking(true);
    try {
      setPreflight(await preflightHotelList(nextFile));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Không kiểm tra được file Excel",
      );
    } finally {
      setIsChecking(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || checkinDates.length === 0) return;
    setIsUploading(true);
    setError(null);
    try {
      const res = await uploadHotelList(file, checkinDates, saveArtifacts);
      router.push(`/jobs/${res.run_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Có lỗi xảy ra");
      setIsUploading(false);
    }
  }

  return (
    <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-10">
      <h1 className="text-2xl font-semibold">Cào giá khách sạn</h1>
      <p className="mt-1.5 text-sm text-muted">
        Upload file Excel chứa link Booking.com (cột A = tên, cột B = link),
        chọn ngày checkin muốn cào (checkout tự động = checkin + 1 đêm). Job
        chạy nền — có thể đóng trình duyệt, quay lại sau xem tiến độ.
      </p>
      <div
        className={`mt-4 flex items-center rounded-lg px-4 py-3 text-sm ${worker === null ? "bg-background" : worker.waiting_for_network ? "bg-amber-50 text-amber-900" : worker.online ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-800"}`}
      >
        {worker === null ? (
          <Skeleton className="h-4 w-72" />
        ) : (
          <span>
            Worker: {worker.waiting_for_network
              ? "đang chờ mạng và sẽ tự tiếp tục"
              : worker.online
                ? "đang online"
                : "đang offline"}
            {worker.current_item_id
              ? ` · đang xử lý item #${worker.current_item_id}`
              : ""}
            {worker.waiting_for_network && worker.status_reason
              ? ` · ${worker.status_reason}`
              : ""}
            {worker.waiting_for_network && worker.next_probe_at
              ? ` · lần kiểm tra kế tiếp: ${formatDateTime(worker.next_probe_at)}`
              : ""}
            {!worker.online &&
              " · Job vẫn có thể xếp hàng nhưng chỉ chạy sau khi scripts/run_worker.py được bật."}
          </span>
        )}
      </div>

      <form onSubmit={handleSubmit} className="mt-8 grid gap-6 md:grid-cols-2">
        <section className="space-y-6">
          <div className="rounded-xl border border-border bg-surface p-5">
            <label className="text-sm font-medium">File Excel</label>
            <input
              type="file"
              accept=".xlsx"
              onChange={(e) =>
                void handleFileChange(e.target.files?.[0] ?? null)
              }
              className="mt-3 block w-full text-sm text-muted file:mr-4 file:rounded-lg file:border-0 file:bg-accent file:px-4 file:py-2 file:text-sm file:font-medium file:text-accent-foreground hover:file:opacity-90"
            />
            {file && (
              <p className="mt-2 text-xs text-muted">
                Đã chọn: <span className="text-foreground">{file.name}</span>
              </p>
            )}
            {isChecking && (
              <p className="mt-2 text-xs text-muted">
                Đang kiểm tra cấu trúc file…
              </p>
            )}
            {preflight && (
              <div className="mt-3 space-y-2 rounded-lg bg-background p-3 text-xs">
                <p className="font-medium text-foreground">
                  {preflight.valid_links}/{preflight.total_rows} link hợp lệ
                </p>
                <p className="text-muted">
                  Ngữ cảnh cào: {preflight.search_context}
                </p>
                {preflight.sheets.map((sheet) => (
                  <p
                    key={sheet.name}
                    className={sheet.in_scope ? "text-muted" : "text-amber-700"}
                  >
                    Sheet {sheet.name}: {sheet.valid_links} link
                    {sheet.in_scope
                      ? ` · ${sheet.city}`
                      : " · ngoài 5 thành phố trong scope"}
                  </p>
                ))}
                {outOfScopeSheets.length > 0 && (
                  <p className="font-medium text-red-700">
                    Không thể bắt đầu: hãy đổi tên hoặc xóa dữ liệu ở sheet
                    ngoài scope (
                    {outOfScopeSheets.map((sheet) => sheet.name).join(", ")}).
                  </p>
                )}
                {(preflight.invalid_rows.length > 0 ||
                  preflight.duplicate_rows.length > 0) && (
                  <p className="text-amber-700">
                    Bỏ qua {preflight.invalid_rows.length} link lỗi và{" "}
                    {preflight.duplicate_rows.length} link trùng.
                  </p>
                )}
              </div>
            )}
          </div>

          <div className="rounded-xl border border-border bg-surface p-5">
            <label className="text-sm font-medium">Ngày checkin đã chọn</label>
            <p className="mt-1 text-xs text-muted">
              Bấm vào 1 ngày trên lịch để thêm, bấm lại để bỏ chọn. Không giới
              hạn bởi lịch cào tự động — chọn ngày bất kỳ trong tương lai, kể cả
              năm sau. Hệ thống không tự tạo lịch crawl.
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
                        setSelectedDates(
                          selectedDates.filter((d) => toISODate(d) !== iso),
                        )
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

          <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-border bg-surface p-4">
            <input
              type="checkbox"
              checked={saveArtifacts}
              onChange={(event) => setSaveArtifacts(event.target.checked)}
              className="mt-0.5 h-4 w-4"
            />
            <span>
              <span className="block text-sm font-medium">
                Lưu bằng chứng trang
              </span>
              <span className="mt-1 block text-xs text-muted">
                Khi bật, lưu HTML nén và ảnh chụp cho từng khách sạn/ngày để
                kiểm tra đúng khoảnh khắc cào; mặc định tắt và artifact được giữ
                30 ngày.
              </span>
            </span>
          </label>

          {error && (
            <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={
              !file ||
              !preflight ||
              preflight.valid_links === 0 ||
              checkinDates.length === 0 ||
              outOfScopeSheets.length > 0 ||
              isUploading ||
              isChecking
            }
            className="w-full cursor-pointer rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-accent-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {isUploading
              ? "Đang gửi..."
              : isChecking
                ? "Đang kiểm tra file..."
                : "Bắt đầu cào"}
          </button>
        </section>

        <section className="self-start rounded-xl border border-border bg-surface p-5">
          <DayPicker
            mode="multiple"
            selected={selectedDates}
            onSelect={(dates) => setSelectedDates(dates ?? [])}
            disabled={{ before: today }}
            showOutsideDays
            className="mx-auto w-fit"
          />
        </section>
      </form>
    </main>
  );
}
