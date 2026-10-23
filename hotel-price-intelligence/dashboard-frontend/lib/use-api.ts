"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";
import { L } from "./labels";

export type ApiState<T> =
  | { status: "idle"; data: null; error: null }
  | { status: "loading"; data: null; error: null }
  | { status: "ready"; data: T; error: null }
  | { status: "error"; data: null; error: ApiError };

/**
 * Nạp dữ liệu theo `key` (null = chưa chọn). Mỗi lần đổi key/gọi reload: HỦY request cũ (AbortController) và bỏ phản hồi cũ — không bao giờ để phản hồi muộn ghi đè lựa chọn mới.
 * Không đặt state đồng bộ trong effect: trạng thái "loading" suy ra từ việc kết quả đã lưu chưa khớp key hiện tại.
 */
export function useApi<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>): ApiState<T> & { reload: () => void } {
  const [nonce, setNonce] = useState(0);
  const [result, setResult] = useState<{ key: string; data: T | null; error: ApiError | null } | null>(null);
  const fullKey = key === null ? null : `${key}#${nonce}`;

  useEffect(() => {
    if (fullKey === null) return;
    const controller = new AbortController();
    load(controller.signal).then(
      (data) => { if (!controller.signal.aborted) setResult({ key: fullKey, data, error: null }); },
      (caught: unknown) => {
        if (controller.signal.aborted) return;
        setResult({ key: fullKey, data: null, error: caught instanceof ApiError ? caught : new ApiError(0, "unknown", L.networkError) });
      },
    );
    return () => controller.abort();
    // `load` được tạo mới mỗi render; khóa nạp lại chỉ theo `fullKey` (đã mã hóa mọi tham số).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fullKey]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  if (fullKey === null) return { status: "idle", data: null, error: null, reload };
  if (result === null || result.key !== fullKey) return { status: "loading", data: null, error: null, reload };
  if (result.error) return { status: "error", data: null, error: result.error, reload };
  return { status: "ready", data: result.data as T, error: null, reload };
}
