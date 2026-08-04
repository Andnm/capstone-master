export type RunStatus = "queued" | "running" | "completed" | "failed";
export type ItemStatus = "success" | "sold_out" | "error";

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

export const ITEM_STATUS_LABEL: Record<ItemStatus, string> = {
  success: "Thành công",
  sold_out: "Hết phòng",
  error: "Lỗi",
};

export const ITEM_STATUS_BADGE_CLASS: Record<ItemStatus, string> = {
  success: "bg-emerald-100 text-emerald-800",
  sold_out: "bg-slate-200 text-slate-700",
  error: "bg-red-100 text-red-800",
};
