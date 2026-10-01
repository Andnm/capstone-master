export type RunStatus = "queued" | "running" | "completed" | "failed";
export type ItemStatus = "queued" | "running" | "success" | "partial" | "sold_out" | "not_bookable" | "error";

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  queued: "Đang chờ",
  running: "Đang chạy",
  completed: "Hoàn thành",
  failed: "Lỗi",
};

export const RUN_STATUS_BADGE_CLASS: Record<RunStatus, string> = {
  queued: "bg-amber-100 text-amber-800",
  running: "bg-blue-100 text-blue-800",
  completed: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
};

type RunStatusSummary = {
  status: string;
  partial_count: number;
  error_count: number;
};

export function getRunStatusLabel(run: RunStatusSummary): string {
  if (run.status === "completed" && run.error_count > 0) return "Hoàn thành có lỗi";
  if (run.status === "completed" && run.partial_count > 0) return "Hoàn thành có cảnh báo";
  return RUN_STATUS_LABEL[run.status as RunStatus] ?? run.status;
}

export function getRunStatusBadgeClass(run: RunStatusSummary): string {
  if (run.status === "completed" && (run.error_count > 0 || run.partial_count > 0)) {
    return "bg-amber-100 text-amber-800";
  }
  return RUN_STATUS_BADGE_CLASS[run.status as RunStatus] ?? "bg-slate-100 text-slate-700";
}

export const ITEM_STATUS_LABEL: Record<ItemStatus, string> = {
  queued: "Đang chờ",
  running: "Đang chạy",
  success: "Thành công",
  partial: "Thiếu dữ liệu",
  sold_out: "Hết phòng",
  not_bookable: "Không thể đặt",
  error: "Lỗi",
};

export const ITEM_STATUS_BADGE_CLASS: Record<ItemStatus, string> = {
  queued: "bg-amber-100 text-amber-800",
  running: "bg-blue-100 text-blue-800",
  success: "bg-emerald-100 text-emerald-800",
  partial: "bg-amber-100 text-amber-800",
  sold_out: "bg-slate-200 text-slate-700",
  not_bookable: "bg-violet-100 text-violet-800",
  error: "bg-red-100 text-red-800",
};

export const REFERENCE_STATUS_LABEL: Record<string, string> = {
  calibrating: "Đang hiệu chỉnh",
  exact: "Khớp chính xác",
  alias: "Khớp tên tương đương",
  unavailable: "Chưa tìm thấy",
  ambiguous: "Chưa phân giải",
  not_reference: "Không phải phòng tham chiếu",
  not_applicable: "Không áp dụng",
};
