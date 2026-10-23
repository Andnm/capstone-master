"use client";
import { createContext, useContext } from "react";
import { getDashboardConfig } from "@/lib/api";
import { useApi, type ApiState } from "@/lib/use-api";
import type { DashboardConfig } from "@/types";

type SnapshotState = ApiState<DashboardConfig> & { reload: () => void };
const SnapshotContext = createContext<SnapshotState | null>(null);

/** Nạp cấu hình/snapshot MỘT lần ở trình duyệt; layout KHÔNG chặn vào API (build không cần backend, API tắt chỉ hiện trạng thái lỗi). */
export function SnapshotProvider({ children }: { children: React.ReactNode }) {
  const state = useApi<DashboardConfig>("config", (signal) => getDashboardConfig(signal));
  return <SnapshotContext.Provider value={state}>{children}</SnapshotContext.Provider>;
}

export function useSnapshot(): SnapshotState {
  const value = useContext(SnapshotContext);
  if (!value) throw new Error("useSnapshot phải nằm trong SnapshotProvider");
  return value;
}
